"""DataViz 治理取数 — raw_sql 存图/看板/组件的取数一律走统一护城河。

原则(数据护城河 · 见 data-moat 计划): 本项目不是数据库连接工具, 任何返回数据行的
取数都必须经过 `execute_query_with_permission`(permission_enforcer: 敏感 block 剔除
/mask 脱敏 + RLS 行级 + RBAC + 审计), 且身份一律由服务端(JWT / 嵌入 AK / 报表创建者)
解析后传入。无可信身份 -> fail-closed 拒绝, 绝不回退到裸连数据源执行(I3/I5)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class NoIdentityError(PermissionError):
    """无可信用户身份 -> 拒绝取数(护城河 fail-closed)。"""


def governed_execute(
    sql: str,
    datasource_id: int,
    user_id: int,
    workspace_id: int = 0,
    username: str = "",
) -> dict:
    """经统一护城河执行 raw_sql 取数。

    返回 {columns, rows, row_count, elapsed_ms}, 与原 `_execute_on_datasource` 契约一致,
    但结果已按当前用户身份施加敏感列屏蔽(block 剔除/mask 脱敏) + RLS 行级过滤 + 审计。
    """
    from services.datamind.nl2sql.sql.query_executor import execute_query_with_permission

    uid = int(user_id or 0)
    if not uid:
        # I5: 无可信身份一律拒绝; 不回退到无过滤的裸执行
        raise NoIdentityError("缺少可信用户身份, 拒绝直接取数(数据合规护城河)")

    df, elapsed_ms, row_count = execute_query_with_permission(
        sql,
        datasource_id=datasource_id,
        query_type="sql",
        user_context={"user_id": uid, "username": username or ""},
        workspace_id=int(workspace_id or 0),
    )
    return _df_to_result(df, row_count, elapsed_ms)


def _df_to_result(df, row_count, elapsed_ms) -> dict:
    """DataFrame -> {columns, rows(list[dict]), row_count, elapsed_ms}(JSON 安全)。

    关联查询(t1.*, t2.*)产生的重复列名由共享 df_to_columns_rows 无损消歧,
    与 Playground 取数保持同一返回契约。
    """
    from services.shared.common.df_serialize import df_to_columns_rows

    columns, rows = df_to_columns_rows(df)
    rc = row_count if row_count is not None else len(rows)
    return {"columns": columns, "rows": rows, "row_count": int(rc), "elapsed_ms": elapsed_ms}
