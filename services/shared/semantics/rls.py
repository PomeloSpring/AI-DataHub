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
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

try:  # sqlglot 是 Phase4 硬依赖; 缺失时降级为"不改写"(secured=base)并由 gate 记录
    import sqlglot
    from sqlglot import exp

    _HAS_SQLGLOT = True
except Exception:  # pragma: no cover
    _HAS_SQLGLOT = False


def resolve_table_filters(
    user_id: int,
    workspace_id: int,
    datasource_id: int,
    tables: Iterable[str],
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """用 permission_enforcer 解析每张表的 RLS 谓词与列限制。

    Returns:
        ({table: row_filter}, {table: {"hidden":[...], "masked":{...}}}, policies_applied)
    """
    tables = [t for t in tables if t]
    filters: dict[str, str] = {}
    column_restriction: dict[str, dict[str, object]] = {}
    applied: list[str] = []
    if not tables:
        return filters, column_restriction, applied

    from services.datamind.permission.enforcer import permission_enforcer

    for table in tables:
        res = permission_enforcer.check_access(
            user_id=user_id,
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            table_name=table,
        )
        if res.row_filter:
            filters[table] = res.row_filter
        column_restriction[table] = {
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
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:
        logger.warning("[rls] parse failed, refusing unfiltered execution: %s", e)
        return "", [], f"RLS rewrite parse error: {e}"

    applied: list[str] = []
    for table, predicate in table_filters.items():
        replaced = False
        for node in list(tree.find_all(exp.Table)):
            if (node.name or "").lower() != table.lower():
                continue
            # 已经是本表过滤子查询的, 跳过(幂等)
            parent = node.find_ancestor(exp.Subquery)
            if parent is not None and predicate in (parent.sql(dialect=dialect) or ""):
                continue
            # 保留原限定引用(catalog.db.table), 避免包裹后丢层级
            qualified = node.sql(dialect=dialect) or table
            try:
                inner = sqlglot.parse_one(
                    f"SELECT * FROM {qualified} WHERE {predicate}", dialect=dialect,
                )
            except Exception as e:
                logger.warning("[rls] predicate parse failed for %s: %s", table, e)
                return "", [], f"RLS predicate invalid for {table}: {e}"
            sub = exp.Subquery(
                this=inner,
                alias=exp.TableAlias(this=exp.to_identifier(table)),
            )
            node.replace(sub)
            replaced = True
        if replaced:
            applied.append(table)

    return tree.sql(dialect=dialect), applied, None


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


# ── 列级限制的 SQL 可见改写(Playground "所见即所执行" 展示用) ──────────────
# 语义与 permission_enforcer 结果侧处理对齐: hidden=drop 列, masked=null/hash/partial。
# 主路(语义查询)由 MDL 显式列清单 + 结果侧掩码兑现; 裸 SQL(含 *)只能在
# SQL 层等价展开/替换, 才能把屏蔽效果呈现进 securedSql。

def _mask_sql_expr(col_sql: str, mask_type: str) -> str:
    """单列掩码的 SQL 表达式(mysql/doris 函数系), 与 _mask_value 行为一致。"""
    if mask_type == "null":
        return f"CAST(NULL AS CHAR)"
    if mask_type == "hash":
        return f"LEFT(SHA2({col_sql}, 256), 16)"
    # partial: 保首尾各 2 字符, 中间打星; 长度<=4 全打星(同 _partial_mask)
    return (
        f"CASE WHEN CHAR_LENGTH({col_sql}) <= 4 THEN REPEAT('*', CHAR_LENGTH({col_sql})) "
        f"ELSE CONCAT(LEFT({col_sql}, 2), REPEAT('*', GREATEST(CHAR_LENGTH({col_sql}) - 4, 0)), "
        f"RIGHT({col_sql}, 2)) END"
    )


def apply_column_restriction(
    sql: str,
    column_restriction: dict[str, dict[str, object]],
    get_columns,
    dialect: str = "mysql",
) -> tuple[str, list[str], Optional[str]]:
    """把列级权限(hidden/masked)改写进 SQL: 展开 * / 剔除隐藏列 / 掩码列换表达式。

    Args:
        column_restriction: resolve_table_filters 的 {table: {"hidden":[], "masked":{col:type}}}
        get_columns: table -> 有序列名列表 | None(拿不到列清单时 * 无法展开 -> error)
    Returns:
        (secured_sql, restricted_tables, error) — error 非空时调用方必须拒绝执行,
        不得静默回退未屏蔽 SQL(隐藏列会泄露)。
    """
    if not sql or not column_restriction:
        return sql, [], None
    if not _HAS_SQLGLOT:  # pragma: no cover
        return "", [], "sqlglot unavailable; refusing to run column-restricted query unfiltered"
    restr: dict[str, dict[str, object]] = {}
    for t, spec in column_restriction.items():
        hidden = {str(c).lower() for c in (spec or {}).get("hidden") or []}
        masked = {str(k).lower(): str(v) for k, v in ((spec or {}).get("masked") or {}).items()}
        if hidden or masked:
            restr[t.lower()] = {"hidden": hidden, "masked": masked}
    if not restr:
        return sql, [], None

    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:  # noqa: BLE001
        return "", [], f"column restriction parse error: {e}"

    applied: list[str] = []
    marked: set[str] = set()

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
                    continue
                ref = exp.column(c, table=disp)
                if c.lower() in masked:
                    expr = sqlglot.parse_one(
                        f"SELECT {_mask_sql_expr(ref.sql(dialect=dialect), masked[c.lower()])} AS {c}",
                        dialect=dialect,
                    ).expressions[0]
                    _mark(real)
                    items.append(expr)
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
                    return "", [], f"无法展开 *(表 {need} 列清单不可用: 请选择可连的数据源或先做元数据同步)"
                new_items.extend(expanded)
                changed = True
                continue
            # 显式列引用: hidden 剔除 / masked 替换
            if isinstance(item, exp.Column) and item.name:
                col_l = item.name.lower()
                cand = [disp2real.get(item.table.lower(), item.table.lower())] if item.table else list(reals)
                hit_hidden = any(t in restr and col_l in restr[t]["hidden"] for t in cand if t)
                hit_mask = next(((t, restr[t]["masked"][col_l]) for t in cand
                                 if t in restr and col_l in restr[t]["masked"]), None)
                if hit_hidden:
                    for t in cand:
                        if t in restr and col_l in restr[t]["hidden"]:
                            _mark(t)
                    changed = True
                    continue
                if hit_mask:
                    t_src, m_type = hit_mask
                    _mark(t_src)
                    expr = sqlglot.parse_one(
                        f"SELECT {_mask_sql_expr(item.sql(dialect=dialect), m_type)} AS {item.name}",
                        dialect=dialect,
                    ).expressions[0]
                    new_items.append(expr)
                    changed = True
                    continue
            new_items.append(item)
        if changed:
            if not new_items:
                return "", [], "所有选择列均被权限屏蔽, 查询无可用列"
            select.set("expressions", new_items)

    if not marked:
        return sql, [], None
    return tree.sql(dialect=dialect), applied, None
