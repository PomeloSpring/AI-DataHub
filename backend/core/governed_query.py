"""DataViz 治理取数 — raw_sql 存图/看板/组件的取数一律走统一护城河。

原则(数据护城河 · 见 data-moat 计划): 本项目不是数据库连接工具, 任何返回数据行的
取数都必须经过统一取数执行口(`semantics.execute.execute_sql`, 内部经
`execute_query_with_permission`: permission_enforcer 敏感 block 剔除/mask 脱敏 +
RLS 行级 + RBAC + 审计), 且身份一律由服务端(JWT / 嵌入 AK / 报表创建者)解析后传入。
无可信身份 -> fail-closed 拒绝, 绝不回退到裸连数据源执行(I3/I5)。
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

    Phase 6.4：执行口统一为 `semantics.execute.execute_sql`（护栏 §1），
    异常语义保持——权限拒绝仍是 PermissionError 族，执行失败上抛。
    """
    uid = int(user_id or 0)
    if not uid:
        # I5: 无可信身份一律拒绝; 不回退到无过滤的裸执行
        raise NoIdentityError("缺少可信用户身份, 拒绝直接取数(数据合规护城河)")

    from backend.semantics.contract import ErrorCode, SemanticError
    from backend.semantics.execute import PolicyContext, execute_sql

    try:
        result = execute_sql(sql, PolicyContext(
            user_id=uid,
            username=username or "",
            workspace_id=int(workspace_id or 0),
            datasource_id=int(datasource_id or 0),
        ))
    except SemanticError as e:
        # 异常语义还原：权限拒绝保持 PermissionError 族，其余按执行失败上抛
        if e.code is ErrorCode.FORBIDDEN:
            raise PermissionError(e.message) from e
        raise RuntimeError(e.message) from e

    names = [c.name for c in result.columns]
    return {
        "columns": names,
        "rows": [dict(zip(names, row)) for row in result.rows],
        "row_count": int(result.row_count),
        "elapsed_ms": result.elapsed_ms,
    }
