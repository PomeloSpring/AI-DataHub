"""query 工具组 — execute_sql(校验 + 权限 + 审计).

handler 为 SDK 无关的纯函数,由 build_query_server(backend) 包装。
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

from services.datamind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

# 结果行数上限,避免超大结果撑爆上下文
MAX_ROWS = 200


async def execute_sql(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context
    from services.datamind.nl2sql.sql.query_executor import (
        execute_query_with_permission,
        validate_sql,
    )

    ctx = get_execution_context()
    sql = (args.get("sql") or "").strip()
    if not sql:
        return _text({"error": "sql is required"}, is_error=True)

    ok, msg = validate_sql(sql, require_limit=False)
    if not ok:
        return _text({"error": f"SQL 校验失败: {msg}"}, is_error=True)

    user_context = {"user_id": ctx.user_id, "username": ctx.username}
    try:
        df, exec_ms, row_count = await asyncio.to_thread(
            execute_query_with_permission,
            sql,
            args.get("datasource_id"),
            "sql",
            user_context,
            ctx.workspace_id,
        )
    except Exception as e:
        logger.error("execute_sql error: %s", e)
        return _text({"error": str(e)}, is_error=True)

    truncated = row_count > MAX_ROWS
    records = json.loads(df.head(MAX_ROWS).to_json(orient="records", force_ascii=False))
    return _text({
        "row_count": row_count,
        "execution_ms": exec_ms,
        "truncated": truncated,
        "columns": list(df.columns),
        "rows": records,
    })


TOOL_SPECS = [
    {
        "name": "execute_sql",
        "description": (
            "Execute a read-only SQL query against a datasource with permission checks and audit logging. "
            "Only SELECT statements are allowed; the query is validated and auto-limited. "
            "Use list_datasources to find datasource_id and get_table_schema before writing SQL."
        ),
        "schema": {
            "sql": Annotated[str, "The SELECT SQL statement to execute"],
            "datasource_id": Annotated[Optional[int], "Datasource id from list_datasources; omit to use the default"],
        },
        "handler": execute_sql,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_query_server(backend: str = "qoder"):
    """构建 query 进程内 MCP server(qoder / claude)."""
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in TOOL_SPECS
    ]
    return make_server(backend, "datahub_query", tools)
