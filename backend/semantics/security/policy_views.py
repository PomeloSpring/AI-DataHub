"""policy 裁决产物 → 过滤视图（RLS/mask 视图化强制，Phase 6.3）。

enforcer 裁决产物（`{table: row_filter}` + `{table: {hidden, masked}}`）按会话指纹
注册**过滤视图**：行过滤进 WHERE、hidden 列剔除、masked 列换 mask 表达式。

- **plan 期强制、天然覆盖 JOIN 两侧**：视图即强制面——查询改写只需把物理表引用替换为
  视图名（`rewrite_to_views`），不再逐引用做子查询包裹，从根上消除别名风险
  （护栏 §4 行为「别名保留、无非法双重别名」迁至本层验证）；
- 内嵌/联邦模式：视图注册进 SessionContext（session 池按指纹分桶，逐出后复放）；
- 远程下推模式：视图体即子查询包裹语义（现役 `rls.apply_rls`/`inject_filters` 路径，
  Phase 6.4 消费方切换后由本层视图体统一供给）。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from backend.semantics.execution.session import get_session_pool, policy_fingerprint
from backend.semantics.security.masking import mask_expr

VIEW_PREFIX = "sec_"


def view_name(table: str) -> str:
    """过滤视图名（同名遮蔽会递归，视图名必须区别于物理表名）。"""
    return f"{VIEW_PREFIX}{table}"


def filtered_view_sql(
    table: str,
    *,
    row_filter: Optional[str] = None,
    column_restriction: Optional[dict[str, Any]] = None,
    columns: Optional[list[str]] = None,
    dialect: str = "mysql",
) -> Optional[str]:
    """生成过滤视图体 SQL；无需任何过滤/脱敏时返回 None（调用方直查物理表）。

    - hidden 列直接剔除、masked 列换 mask 表达式（列名保留为别名列）；
    - hidden/masked 非空时必须给 `columns` 列清单（否则无法保证剔除/脱敏，fail-loud）。
    """
    hidden = list((column_restriction or {}).get("hidden") or [])
    masked = dict((column_restriction or {}).get("masked") or {})
    row_filter = (row_filter or "").strip()
    if not hidden and not masked and not row_filter:
        return None

    cols = list(columns or [])
    if not cols and (hidden or masked):
        raise ValueError(
            f"表 {table} 存在 hidden/masked 列限制但缺列清单，无法生成安全过滤视图（fail-loud）"
        )

    if cols:
        projections = []
        for c in cols:
            if c in hidden:
                continue  # hidden：不可见，直接剔除
            if c in masked:
                projections.append(f"{mask_expr(c, masked[c], dialect)} AS {c}")
            else:
                projections.append(c)
        select_list = ", ".join(projections) or "NULL"
    else:
        select_list = "*"  # 仅行过滤、无列限制

    where = f" WHERE {row_filter}" if row_filter else ""
    return f"SELECT {select_list} FROM {table}{where}"


def rewrite_to_views(
    sql: str,
    tables: Iterable[str],
    dialect: str = "mysql",
) -> tuple[str, list[str]]:
    """把物理表引用替换为过滤视图名 —— plan 期强制，JOIN 两侧天然覆盖。

    只换表名节点，**别名原样保留**（`FROM t t1` → `FROM sec_t t1`），不产生
    非法双重别名（护栏 §4）；解析失败 fail-loud（不静默放行未强制 SQL）。
    """
    targets = {t for t in (tables or []) if t}
    if not sql or not targets:
        return sql, []

    from sqlglot import exp

    from backend.semantics.sql_guard import parse_query

    tree = parse_query(sql, dialect)
    applied: list[str] = []
    for node in tree.find_all(exp.Table):
        name = node.name
        if name in targets:
            node.set("this", exp.to_identifier(view_name(name)))
            if name not in applied:
                applied.append(name)
    return tree.sql(dialect=dialect), applied


class PolicyViewManager:
    """按会话指纹注册过滤视图的管理面（内嵌/联邦模式的强制入口）。"""

    def __init__(self, pool=None):
        self._pool = pool if pool is not None else get_session_pool()

    def apply(
        self,
        policy: Optional[dict[str, Any]],
        table_specs: dict[str, dict[str, Any]],
        *,
        columns: Optional[dict[str, list[str]]] = None,
        dialect: str = "mysql",
    ) -> list[str]:
        """为裁决产物中的每张表注册过滤视图，返回已注册视图名。

        table_specs: {table: {"row_filter": str, "column_restriction": {hidden, masked}}}
        columns: {table: [col, ...]}（hidden/masked 非空时必填）

        约定：datafusion 视图注册为**急切解析**，底层表须先注册（调用方先表后视图）；
        apply 幂等，可随表就绪重复调用。
        """
        fingerprint = policy_fingerprint(policy)
        registered: list[str] = []
        for table, spec in (table_specs or {}).items():
            view_sql = filtered_view_sql(
                table,
                row_filter=spec.get("row_filter"),
                column_restriction=spec.get("column_restriction"),
                columns=(columns or {}).get(table),
                dialect=dialect,
            )
            if view_sql:
                self._pool.register_view(fingerprint, view_name(table), view_sql)
                registered.append(view_name(table))
        return registered

    def view_sql_of(self, policy: Optional[dict[str, Any]], table: str) -> Optional[str]:
        """回读已登记的视图体（诊断/对拍用）。"""
        fingerprint = policy_fingerprint(policy)
        return (self._pool._views.get(fingerprint) or {}).get(view_name(table))
