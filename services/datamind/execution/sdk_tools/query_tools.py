"""query 工具组 — execute_sql(校验 + 权限 + 审计).

handler 为 SDK 无关的纯函数,由 build_query_server(backend) 包装。
"""

import asyncio
import json
import logging
import re
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
        _extract_table_names,
    )

    ctx = get_execution_context()
    sql = (args.get("sql") or "").strip()
    if not sql:
        return _text({"error": "sql is required"}, is_error=True)

    # Phase 4 护栏: execute_sql 已降级为"受控探索旁路"(agent 主路走 run_semantic_query)。
    #   1) 消除 require_limit=False 旁路: 缺 LIMIT 的扫描自动补默认 LIMIT 后再校验。
    #   2) huge + raw_source 且无谓词时直接拒绝(与 planner 同口径, 不静默)。
    ok, msg = validate_sql(sql, require_limit=False)  # 先做安全性(DDL/DML/多语句)校验, 不含 LIMIT
    if not ok:
        return _text({"error": f"SQL 校验失败: {msg}"}, is_error=True)

    try:
        from services.datamind.nl2sql.sql.sql_validator import add_limit
        sql = add_limit(sql)
    except Exception:
        pass
    ok, msg = validate_sql(sql, require_limit=True)
    if not ok:
        return _text({"error": f"护栏: {msg}"}, is_error=True)

    ds_id = args.get("datasource_id") or (ctx.datasource_id if ctx else None)
    guardrail_err = _huge_scan_guard(sql, ds_id)
    if guardrail_err:
        return _text({"error": guardrail_err, "hint": "为 large/huge 表加过滤谓词, 或改用 run_semantic_query。"}, is_error=True)

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


def _huge_scan_guard(sql: str, datasource_id) -> str | None:
    """raw_source/huge 表若无 WHERE 则拒绝(消除无界全表扫旁路)。"""
    try:
        from services.shared.common.db.metadata_db import get_metadata_conn
        tables = _extract_table_names(sql)
        if not tables:
            return None
        has_where = bool(re.search(r"\bWHERE\b", sql, re.IGNORECASE))
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fmt = ",".join(["%s"] * len(tables))
                cur.execute(
                    f"SELECT table_name, size_class, allow_full_scan FROM adh_table_info "
                    f"WHERE table_name IN ({fmt}) AND (%s OR datasource_id = %s) AND is_active = 1",
                    (*tables, datasource_id or 0, datasource_id or 0),
                )
                for r in cur.fetchall():
                    if r.get("size_class") == "huge" and not r.get("allow_full_scan") and not has_where:
                        return f"huge 表 {r['table_name']} 无过滤谓词的全表扫描被护栏拒绝"
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[execute_sql] huge-scan guard skipped: %s", e)
    return None


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


def build_query_server(backend: str = "qoder", tool_names=None):
    """构建 query 进程内 MCP server(qoder / claude).

    tool_names 给定时只注册被选中的工具(waker 粒度控制 execute_sql 开关)。
    """
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_query", tools)
