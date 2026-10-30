"""RLS rewriter (Phase 4 · 详设 F) — 语义层内 baseSql -> securedSql 的唯一改写点。

设计:
- 行级权限(RLS)的**可见改写**只发生在这里:输入 planner 编译出的 baseSql +
  每张物理表的 row_filter 谓词, 用 sqlglot 做 AST 级"表 -> 过滤子查询"包裹,
  产出 securedSql(所见即所执行)。
- 谓词来源复用既有策略事实源(`permission_enforcer.check_access` ->
  `rls_service.get_effective_policies`), 不重造策略, 避免与执行层 DataFusion
  的 `rls_policy` 定义漂移。
- DataFusion `SecureTableProvider` 仍会在 plan 期二次并一次 rls_filter(纵深防御);
  两处共用同一策略, 若二次校验结果与 securedSql 不一致由上层告警(Phase 6 Playground)。

只处理 SELECT/WITH;解析失败时**保守返回原 SQL + 标记**, 由调用方(gates)决定拒绝。
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

try:  # sqlglot 是 Phase4 硬依赖; 缺失时降级为"不改写"(secured=base)并由 gate 记录
    import sqlglot
    from sqlglot import exp

    _HAS_SQLGLOT = True
except Exception:  # pragma: no cover
    _HAS_SQLGLOT = False


def _bare_table(ref: str) -> str:
    """限定表引用 -> 裸表名（策略匹配用，与 enforcer._resolve_table_ref 同口径）。

    `db.t` / `catalog.db.t` 的限定段只用于"表属于哪个源"的判定，行策略/敏感基线
    在元库里按裸表名登记；拿限定名去查策略会**静默查不到**（看起来已施加实则未过滤）。
    """
    return str(ref or "").strip().rsplit(".", 1)[-1]


def resolve_table_filters(
    user_id: int,
    workspace_id: int,
    datasource_id: int,
    tables: Iterable[str],
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """用 permission_enforcer 解析每张表的 RLS 谓词与列限制。

    Returns:
        ({table: row_filter}, {table: {"hidden":[...], "masked":{...}}}, policies_applied)

    入参可以是限定名（模板正文里写 `db.t`）：策略按**裸表名**加载，但返回的谓词映射
    同时以裸表名和原始限定名为键，使 `inject_filters` 两种引用形式都能命中（护栏 §10）。
    """
    tables = [t for t in tables if t]
    filters: dict[str, str] = {}
    column_restriction: dict[str, dict[str, object]] = {}
    applied: list[str] = []
    if not tables:
        return filters, column_restriction, applied

    # 同一裸表可能以多种形式出现（t / db.t）：按裸表名去重加载策略，避免重复查与重计
    variants: dict[str, list[str]] = {}
    for ref in tables:
        variants.setdefault(_bare_table(ref), []).append(ref)

    from backend.core.enforcer import permission_enforcer

    for bare, refs in variants.items():
        if not bare:
            continue
        res = permission_enforcer.check_access(
            user_id=user_id,
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            table_name=bare,
        )
        if res.row_filter:
            for key in {bare, *refs}:     # 限定名与裸名都映射到同一谓词
                if key:
                    filters[key] = res.row_filter
        column_restriction[bare] = {
            "hidden": list(res.hidden_columns or []),
            "masked": dict(res.masked_columns or {}),
        }
        applied.extend(res.policies_applied or [])

    return filters, column_restriction, applied


def apply_rls(
    sql: str,
    table_filters: dict[str, str],
    dialect: str = "mysql",
) -> tuple[str, list[str], Optional[str]]:
    """把 baseSql 按 {table: predicate} 改写为 securedSql。

    Returns:
        (secured_sql, applied_tables, error) — error 非空表示改写失败(调用方应拒绝执行,
        不得静默回退到未过滤的 baseSql)。
    """
    if not sql:
        return sql, [], None
    table_filters = {k: v for k, v in (table_filters or {}).items() if v}
    if not table_filters:
        return sql, [], None
    if not _HAS_SQLGLOT:  # pragma: no cover
        return "", [], "sqlglot unavailable; refusing to run RLS-guarded query unfiltered"

    try:
        from backend.semantics.sql_guard import inject_filters
        secured, applied = inject_filters(sql, table_filters, dialect)
        return secured, applied, None
    except Exception:
        logger.exception("[rls] 无法安全应用行策略")
        return "", [], "行级策略无法安全应用，已拒绝执行"


def masked_columns(column_restriction: dict[str, dict[str, object]]) -> list[str]:
    """汇总所有表的受掩码/隐藏列, 供 SemanticResult 展示。"""
    out: list[str] = []
    for spec in (column_restriction or {}).values():
        for col in spec.get("hidden", []) or []:
            if col not in out:
                out.append(col)
        for col in (spec.get("masked", {}) or {}).keys():
            if col not in out:
                out.append(col)
    return out


def column_policies(
    *, user_id: int, workspace_id: int, datasource_id: int, table_names: Iterable[str],
) -> dict[str, str]:
    """列级脱敏策略（敏感基线 ∪ RLS 列策略）-> {物理列名(小写): mask 类型}。

    供 planner 把策略**编译进下推 SQL**（护栏 §7：业务敏感列能否下发由数据权限裁决，
    不在出站层 hardcode 内容形态规则）。为什么必须在编译期：语义投影别名是业务名/SLS
    ASCII 槽位（`o0/o1…`），执行后按 DataFrame 列名匹配的 `apply_post_processing`
    根对不上名 → 静默不脱敏（历史缺陷）。

    多表（主对象 + 关联对象）按**列名**合并：表达式文本只能按列名匹配，同名列取
    遇到的策略 —— 宁可多脱敏不可漏脱敏。表级无权不在此报错（由执行层裁决），
    但策略加载失败（敏感基线读不到）一律上抛 PermissionError，不得当“无策略”放行。
    """
    from backend.core.enforcer import permission_enforcer

    out: dict[str, str] = {}
    for bare in {_bare_table(t) for t in (table_names or []) if t}:
        res = permission_enforcer.check_access(
            user_id=user_id or 0, workspace_id=workspace_id or 0,
            datasource_id=datasource_id or 0, table_name=bare,
        )
        for col, mask_type in (getattr(res, "masked_columns", None) or {}).items():
            key = str(col or "").strip().lower()
            if key and mask_type and key not in out:
                out[key] = str(mask_type)
    return out


# ── 列级限制的 SQL 可见改写（语义主路编译期 + Playground 展示共用）──────────────
# 语义与 permission_enforcer 结果侧处理对齐: hidden=drop 列, masked=null/hash/partial。
# 为什么不能只靠结果侧按列名脱敏：语义投影别名是业务名/SLS ASCII 槽位（`o0/o1…`），
# DataFrame 列名与物理列名对不上 → 静默不脱敏（护栏 §7）。表达式统一由
# `security/masking.mask_expr` 生成（按方言族 + 引擎分支，不在本层另写一份）。

def _mask_sql_expr(col_sql: str, mask_type: str, dialect: str = "mysql",
                   engine: str | None = None) -> str:
    """单列掩码的 SQL 表达式 —— 实现在 `security/masking.py`（唯一口径，fail-loud）。"""
    from backend.semantics.security.masking import mask_expr

    return mask_expr(col_sql, mask_type, dialect, engine)


def _as_masked(inner_sql: str, mask_type: str, out_name: str,
               dialect: str, engine: str | None):
    """脱敏表达式 + **输出列名**（带引号标识符）。

    输出名必须显式包引号：带点列（SLS 的 `net.ip`）拼成 `AS net.ip` 会直接语法错。
    """
    expr = sqlglot.parse_one(
        f"SELECT {_mask_sql_expr(inner_sql, mask_type, dialect, engine)}", dialect=dialect,
    ).expressions[0]
    return exp.Alias(this=expr, alias=exp.to_identifier(str(out_name), quoted=True))


def renderer_faithful(sql: str, dialect: str = "mysql") -> bool:
    """sqlglot 将 SQL 原样重渲染后是否仍等价（只允许空白/大小写差异）。

    AST 改写必然整句重渲染，而渲染器会把同义写法改成目标引擎不认的形式
    （实测 SLS：`to_unixtime(now() - INTERVAL '7 days')` 被渲染成
    `TO_UNIXTIME(CURRENT_TIMESTAMP - INTERVAL '7 DAYS')` 后报 UndefinedFunction）。
    不保真就不能进 AST 改写（执行口与 Playground 展示同一口径），而不是赌解析器。
    """
    if not sql or not _HAS_SQLGLOT:
        return False
    norm = lambda s: re.sub(r"\s+", " ", str(s or "")).strip().lower()  # noqa: E731
    try:
        return norm(sqlglot.parse_one(sql, dialect=dialect).sql(dialect=dialect)) == norm(sql)
    except Exception:  # noqa: BLE001 — 解不动即不保真
        return False


def apply_column_restriction(
    sql: str,
    column_restriction: dict[str, dict[str, object]],
    get_columns,
    dialect: str = "mysql",
    engine: str | None = None,
) -> tuple[str, list[str], Optional[str], set[str]]:
    """把列级权限(hidden/masked)改写进 SQL: 展开 * / 剔除隐藏列 / 掩码列换表达式。

    Args:
        column_restriction: resolve_table_filters 的 {table: {"hidden":[], "masked":{col:type}}}
        get_columns: table -> 有序列名列表 | None(拿不到列清单时 * 无法展开 -> error)
        dialect/engine: 目方言族与引擎（sls 的函数系与 mysql/pg 不同，mask 表达式据此生成）
    Returns:
        (secured_sql, restricted_tables, error, handled_output_columns) —
        error 非空时调用方必须拒绝执行或回退结果侧脱敏，不得静默放行未屏蔽 SQL；
        `handled_output_columns` = 已在 SQL 层处理完的**输出列名**（小写），调用方据此跳过
        结果侧同列处理（否则已脱敏值会被再脱敏一次，静默改数）。
    """
    if not sql or not column_restriction:
        return sql, [], None, set()
    if not _HAS_SQLGLOT:  # pragma: no cover
        return "", [], "sqlglot unavailable; refusing to run column-restricted query unfiltered", set()
    restr: dict[str, dict[str, object]] = {}
    for t, spec in column_restriction.items():
        hidden = {str(c).lower() for c in (spec or {}).get("hidden") or []}
        masked = {str(k).lower(): str(v) for k, v in ((spec or {}).get("masked") or {}).items()}
        if hidden or masked:
            restr[t.lower()] = {"hidden": hidden, "masked": masked}
    if not restr:
        return sql, [], None, set()

    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:  # noqa: BLE001
        return "", [], f"column restriction parse error: {e}", set()

    applied: list[str] = []
    marked: set[str] = set()
    handled: set[str] = set()      # 已在 SQL 层处理完的输出列名（结果侧据此跳过，免二次脱敏）

    def _mark(tl: str):
        if tl not in marked:
            marked.add(tl)
            applied.append(tl)

    def _scope(select) -> list[tuple[str, str]]:
        """作用域表: [(真实表名, 引用名(别名或表名))]。限制按真名匹配, 改写用引用名。"""
        out: list[tuple[str, str]] = []
        # sqlglot 30.x 把 Select 的 from 键改为 from_, 两版兼容
        frm = select.args.get("from_") or select.args.get("from")
        joins = select.args.get("joins") or []
        nodes = ([frm] if frm else []) + list(joins)
        for node in nodes:
            for tb in node.find_all(exp.Table):
                if tb.name:
                    pair = (tb.name.lower(), (tb.alias or tb.name).lower())
                    if pair not in out:
                        out.append(pair)
        return out

    def _expand_star(qual_real: Optional[str], scope: list[tuple[str, str]]) -> Optional[list]:
        """* / t.* -> 显式列表达式(剔除 hidden, masked 替换); 缺列清单返回 None。"""
        targets = [(r, d) for (r, d) in scope if not qual_real or r == qual_real]
        items: list = []
        for real, disp in targets:
            hidden = restr.get(real, {}).get("hidden", set())
            masked = restr.get(real, {}).get("masked", {})
            cols = get_columns(real)
            if cols is None:
                return None
            for c in cols:
                if c.lower() in hidden:
                    if real in restr:
                        _mark(real)
                    handled.add(str(c).lower())
                    continue
                ref = exp.column(c, table=disp)
                if c.lower() in masked:
                    _mark(real)
                    handled.add(str(c).lower())
                    items.append(_as_masked(ref.sql(dialect=dialect), masked[c.lower()],
                                            c, dialect, engine))
                else:
                    items.append(ref)
        return items

    selects = [tree] if isinstance(tree, exp.Select) else []
    selects += list(tree.find_all(exp.Select))
    for select in selects:
        scope = _scope(select)
        if not scope:
            continue
        reals = [r for r, _ in scope]
        disp2real = {d: r for r, d in scope}
        new_items: list = []
        changed = False
        for item in select.expressions:
            # 裸 * 或 t.*/u.*: 仅当作用域内有受限制表才展开(其余保持原样)
            is_bare_star = isinstance(item, exp.Star)
            is_tbl_star = isinstance(item, exp.Column) and item.is_star
            if (is_bare_star or is_tbl_star) and any(t in restr for t in reals):
                qual_real = None
                if is_tbl_star:
                    q = item.table.lower()
                    qual_real = disp2real.get(q, q)
                expanded = _expand_star(qual_real, scope)
                if expanded is None:
                    need = qual_real or ",".join(t for t in reals if t in restr) or ",".join(reals)
                    return "", [], f"无法展开 *(表 {need} 列清单不可用: 请选择可连的数据源或先做元数据同步)", set()
                new_items.extend(expanded)
                changed = True
                continue
            # 显式列引用（含 `col AS alias` 形式）: hidden 剔除 / masked 替换
            # 别名必须原样保留：结果集列名不变，只是值从源头就是脱敏的
            carrier = item.this if isinstance(item, exp.Alias) else item
            if isinstance(carrier, exp.Column) and carrier.name:
                col_l = carrier.name.lower()
                cand = ([disp2real.get(carrier.table.lower(), carrier.table.lower())]
                        if carrier.table else list(reals))
                hit_hidden = any(t in restr and col_l in restr[t]["hidden"] for t in cand if t)
                hit_mask = next(((t, restr[t]["masked"][col_l]) for t in cand
                                 if t in restr and col_l in restr[t]["masked"]), None)
                out_name = item.alias_or_name or carrier.name
                if hit_hidden:
                    for t in cand:
                        if t in restr and col_l in restr[t]["hidden"]:
                            _mark(t)
                    handled.add(str(out_name).lower())
                    changed = True
                    continue
                if hit_mask:
                    t_src, m_type = hit_mask
                    _mark(t_src)
                    handled.add(str(out_name).lower())
                    new_items.append(_as_masked(carrier.sql(dialect=dialect), m_type,
                                                out_name, dialect, engine))
                    changed = True
                    continue
            new_items.append(item)
        if changed:
            if not new_items:
                return "", [], "所有选择列均被权限屏蔽, 查询无可用列", set()
            select.set("expressions", new_items)
        _restrict_group_by(select, restr, reals, disp2real, dialect, engine, _mark)

    if not marked:
        return sql, [], None, set()
    return tree.sql(dialect=dialect), applied, None, handled


def _restrict_group_by(select, restr, reals, disp2real, dialect, engine, mark) -> None:
    """GROUP BY 里被脱敏的列同步换成脱敏表达式（与语义主路 planner 同口径）。

    只把展示值打星、仍按原值分组，会把 distinct 个数泄露出去（行数与原值一一对应）；
    换成同一份脱敏表达式才是“按脱敏值分组”。
    """
    group = select.args.get("group")
    if group is None:
        return
    new_keys: list = []
    changed = False
    for key in group.expressions:
        col = key if isinstance(key, exp.Column) else (
            key.this if isinstance(key, exp.Alias) and isinstance(key.this, exp.Column) else None)
        hit = None
        if col is not None and col.name:
            col_l = col.name.lower()
            cand = ([disp2real.get(col.table.lower(), col.table.lower())]
                    if col.table else list(reals))
            hit = next(((t, restr[t]["masked"][col_l]) for t in cand
                        if t in restr and col_l in restr[t]["masked"]), None)
        if hit is None:
            new_keys.append(key)
            continue
        mark(hit[0])
        new_keys.append(sqlglot.parse_one(
            f"SELECT {_mask_sql_expr(col.sql(dialect=dialect), hit[1], dialect, engine)}",
            dialect=dialect,
        ).expressions[0])
        changed = True
    if changed:
        group.set("expressions", new_keys)
