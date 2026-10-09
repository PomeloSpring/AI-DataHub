"""语义执行引擎编排 —— DataFusion 规划/校验 + 远程下推（Phase 6.2）。

remote_exec.rs 思路的 Python 重做：远程表下推为**远程 SQL 在源库执行、拉回 Arrow**，
DataFusion 只做规划/校验/跨源联邦；**不引本地 Rust 编译链**（连接器质量不足再评估
PyO3 扩展，届时必须锁同一 DataFusion 版本 ABI，见 adapter.py）。

护栏 §5 执行前强制（不满足即拒绝，fail-loud）：
- `force_limit` / `size_class=huge` / `query_mode=raw_source` → `bounded_query`
  自动补/收紧外层 LIMIT **后再校验**（缺 LIMIT 的扫描不得直接放行）；
- 仅 SELECT/WITH，禁 DDL/DML/多语句（`validate_sql`）；
- `timeout_sec` → 连接读超时强制；`max_rows` → 拉回行数上限（与 LIMIT 取严）。
"""

from __future__ import annotations

from typing import Any, Optional, Union

from backend.semantics.execution.connectors import get_connector
from backend.semantics.execution.session import SessionPool, get_session_pool, policy_fingerprint
from backend.semantics.models import Guardrail
from backend.semantics.sql_guard import bounded_query

_DEFAULT_LIMIT = 1000

GuardrailLike = Union[Guardrail, dict[str, Any], None]


def _guardrail(guardrail: GuardrailLike) -> Guardrail:
    if guardrail is None:
        return Guardrail()
    if isinstance(guardrail, Guardrail):
        return guardrail
    return Guardrail(**dict(guardrail))


def _need_limit(gr: Guardrail) -> bool:
    """护栏 §5：强制限流触发条件。"""
    return bool(gr.force_limit) or gr.size_class == "huge" or gr.query_mode == "raw_source"


class SemanticEngine:
    """执行编排：校验 → 限流强制 → 远程下推拉回 Arrow。"""

    def __init__(self, pool: Optional[SessionPool] = None):
        self._pool = pool if pool is not None else get_session_pool()

    # ── 会话（Phase 6.3 RLS/mask 视图化强制挂点） ──────────────

    def session(self, policy: Optional[dict[str, Any]] = None, *, extra: str = "") -> Any:
        """按 policy 指纹取分桶 SessionContext。"""
        return self._pool.get(policy_fingerprint(policy, extra=extra))

    # ── 护栏 §5 执行前强制 ────────────────────────────────────

    def bounded_sql(
        self,
        sql: str,
        guardrail: GuardrailLike = None,
        *,
        dialect: str = "mysql",
        max_rows: Optional[int] = None,
    ) -> tuple[str, bool]:
        """需要限流时自动补/收紧外层 LIMIT。返回 (sql, 是否强制限流)。"""
        gr = _guardrail(guardrail)
        need = _need_limit(gr)
        limit = int(max_rows or gr.max_rows or _DEFAULT_LIMIT)
        out = bounded_query(sql, limit=limit, dialect=dialect) if need else sql
        return out, need

    def validate(self, sql: str, *, require_limit: bool = False) -> None:
        """SQL 安全校验（只读/禁 DDL-DML/多语句/LIMIT）；不通过即拒绝（fail-loud）。"""
        from backend.core.query_executor import validate_sql

        ok, err = validate_sql(sql, require_limit=require_limit)
        if not ok:
            raise PermissionError(err or "SQL 未通过安全校验")

    # ── 远程下推执行 ──────────────────────────────────────────

    def execute_pushdown(
        self,
        sql: str,
        *,
        datasource_id: int = 0,
        db_type: str = "mysql",
        guardrail: GuardrailLike = None,
        dialect: str = "mysql",
        max_rows: Optional[int] = None,
        use_cache: bool = False,
        config: Optional[dict[str, Any]] = None,
    ) -> Any:
        """校验 + 限流强制后远程下推执行，拉回 Arrow 表。

        方言口径：`sql` 为 planner 产出的目标源方言；远程下推原样执行
        （内嵌 DataFusion 执行经 `connectors.base.to_pushdown_sql` 转换）。
        `use_cache` 结果缓存为性能旁路（重复列名/异常不缓存）；`config` 直传
        连接配置（跨源联邦随请求下发的辅源）。
        """
        gr = _guardrail(guardrail)
        sql_out, need = self.bounded_sql(sql, gr, dialect=dialect, max_rows=max_rows)
        self.validate(sql_out, require_limit=need)

        row_cap = max_rows or gr.max_rows
        key = ""
        if use_cache:
            from backend.semantics.execution.cache import cache_key, from_ipc, result_cache

            key = cache_key(datasource_id, sql_out, row_cap)
            try:
                cached = result_cache.get(key)
                if cached:
                    return from_ipc(cached)
            except Exception:  # noqa: BLE001 — 缓存是性能旁路，异常透明降级
                pass

        connector = get_connector(db_type, datasource_id=datasource_id, config=config)
        table = connector.execute_pushdown(
            sql_out,
            timeout_sec=gr.timeout_sec,
            max_rows=int(row_cap) if row_cap else None,
        )

        if use_cache and key:
            try:
                from backend.semantics.execution.cache import result_cache, to_ipc

                # 重复列名场景不入缓存（IPC 往返无损优先）
                if len(set(table.column_names)) == len(table.column_names):
                    result_cache.set(key, to_ipc(table))
            except Exception:  # noqa: BLE001 — 缓存写入失败不影响结果返回
                pass
        return table

    # ── 跨源联邦 ──────────────────────────────────────────────

    def execute_federated(
        self,
        sql: str,
        sources: list[dict[str, Any]],
        *,
        guardrail: GuardrailLike = None,
        dialect: str = "mysql",
        max_rows: Optional[int] = None,
        policy: Optional[dict[str, Any]] = None,
    ) -> Any:
        """跨源联邦执行：各源表下推拉回 Arrow，DataFusion 本地联邦规划执行。

        sources: [{"name": str, "datasource_id": int, "db_type": str,
                   "config": dict|None, "tables": [table, ...]}]
        表名约定：SQL 引用限定名 `<name>.<table>`（或 `<name>.<schema>.<table>`），
        拉回后注册为 `<name>__<table>` 并改写 SQL 引用（别名保留，护栏 §4 同口径）；
        未限定的裸表按 sources 的 tables 归属同法改写。每张辅源表拉回受
        bounded 强制（护栏 §5，防维表全表无界拉内存）。
        """
        from backend.semantics.execution.connectors.base import dedup_columns
        from backend.semantics.sql_guard import bounded_query

        # 1. 限定名/裸表 → 扁平注册名改写（source 缺 tables 时从 SQL 限定引用推导）
        sources = _with_derived_tables(sql, sources, dialect)
        flat_of, owner_of = _federated_table_map(sources)
        rewritten, _applied = _rewrite_federated_refs(sql, flat_of, owner_of, dialect)

        # 2. 各源表下推拉回（bounded 强制）注册进会话
        ctx = self.session(policy)
        for src in sources or []:
            src_name = str(src.get("name") or "")
            for table in src.get("tables") or []:
                flat = f"{src_name}__{table}" if src_name else str(table)
                pull_sql = bounded_query(
                    f"SELECT * FROM {table}",
                    limit=int(max_rows or _DEFAULT_LIMIT),
                    dialect=("postgres" if str(src.get("db_type") or "").startswith("pg")
                             or src.get("db_type") in ("postgres", "postgresql") else "mysql"),
                )
                connector = get_connector(
                    src.get("db_type") or "mysql",
                    datasource_id=int(src.get("datasource_id") or 0),
                    config=src.get("config"),
                )
                table_data = dedup_columns(connector.execute_pushdown(
                    pull_sql, max_rows=int(max_rows or _DEFAULT_LIMIT)))
                _register_table(ctx, flat, table_data)

        # 3. 联邦 SQL 在会话内执行（plan 期联邦规划）
        gr = _guardrail(guardrail)
        out_sql, need = self.bounded_sql(rewritten, gr, dialect=dialect, max_rows=max_rows)
        self.validate(out_sql, require_limit=need)
        batches = ctx.sql(out_sql).collect()
        import pyarrow as pa

        table = pa.Table.from_batches(batches) if batches else pa.table({})
        return dedup_columns(table)


def _with_derived_tables(
    sql: str,
    sources: list[dict[str, Any]],
    dialect: str,
) -> list[dict[str, Any]]:
    """source 缺 tables 时，从 SQL 的限定引用 `<name>.<table>` 推导源表清单。"""
    if not sources or all((s.get("tables") or []) for s in sources):
        return list(sources or [])
    from sqlglot import exp

    from backend.semantics.sql_guard import parse_query

    out = [dict(s) for s in sources]
    index = {str(s.get("name") or ""): s for s in out if str(s.get("name") or "")}
    tree = parse_query(sql, dialect)
    for node in tree.find_all(exp.Table):
        db = node.args.get("db")
        if db is not None and db.name in index:
            tables = index[db.name].setdefault("tables", [])
            if node.name not in tables:
                tables.append(node.name)
    return out


def _federated_table_map(sources: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """(限定引用 → 扁平注册名, 裸表名 → 所属源名)。"""
    flat_of: dict[str, str] = {}
    owner_of: dict[str, str] = {}
    for src in sources or []:
        name = str(src.get("name") or "")
        for table in src.get("tables") or []:
            table = str(table)
            if name:
                flat_of[f"{name}.{table}"] = f"{name}__{table}"
                flat_of[f"{name}.public.{table}"] = f"{name}__{table}"
                flat_of[f"{name}.dbo.{table}"] = f"{name}__{table}"
            owner_of.setdefault(table, name)
    return flat_of, owner_of


def _rewrite_federated_refs(
    sql: str,
    flat_of: dict[str, str],
    owner_of: dict[str, str],
    dialect: str,
) -> tuple[str, list[str]]:
    """SQL 中联邦表引用 → 扁平注册名（别名保留；解析失败 fail-loud）。"""
    from sqlglot import exp

    from backend.semantics.sql_guard import parse_query

    tree = parse_query(sql, dialect)
    applied: list[str] = []
    for node in tree.find_all(exp.Table):
        name = node.name
        qualified = ".".join(p for p in (
            node.args.get("catalog").name if node.args.get("catalog") else "",
            node.args.get("db").name if node.args.get("db") else "",
            name,
        ) if p)
        flat = flat_of.get(qualified) or flat_of.get(f"{node.args['db'].name}.{name}" if node.args.get("db") else "", "")
        if not flat and name in owner_of:
            owner = owner_of[name]
            flat = f"{owner}__{name}" if owner else name
        if flat and flat != qualified:
            node.set("this", exp.to_identifier(flat))
            node.set("db", None)
            node.set("catalog", None)
            if qualified not in applied:
                applied.append(qualified)
    return tree.sql(dialect=dialect), applied


def _register_table(ctx: Any, name: str, table: Any) -> None:
    """会话注册（同名覆盖：先撤旧）。"""
    try:
        ctx.deregister_table(name)
    except Exception:  # noqa: BLE001 — 不存在即忽略
        pass
    ctx.register_record_batches(name, [table.to_batches()])
