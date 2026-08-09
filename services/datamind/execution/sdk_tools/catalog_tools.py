"""catalog 工具组 — 元数据检索(search_metadata / get_table_schema / list_datasources).

handler 为 SDK 无关的纯函数(读 ContextVar 上下文、调 service 层),
由 build_catalog_server(backend) 用指定 SDK(qoder/claude)的 @tool 包装。
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

logger = logging.getLogger(__name__)


def _text(data, is_error: bool = False) -> dict:
    """统一 CallToolResult 构造(content 块 + MCP 标准 isError 键)."""
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], **({"isError": True} if is_error else {})}


def _global_search_with_global(keyword: str, search_type, workspace_id: int, limit: int) -> dict:
    """目录搜索:本工作空间 + 全局(workspace_id=0)元数据合并去重."""
    from services.datacatalog.services import catalog_service

    result = catalog_service.global_search(keyword, search_type, workspace_id, limit)
    if workspace_id:
        global_result = catalog_service.global_search(keyword, search_type, 0, limit)
        for key in ("tables", "columns", "metrics", "terms"):
            seen = {item.get("id") for item in result.get(key, [])}
            merged = list(result.get(key, []))
            for item in global_result.get(key, []):
                if item.get("id") not in seen:
                    merged.append(item)
            result[key] = merged[:limit]
    return result


def _get_table_detail_with_global(table_name: str, workspace_id: int):
    """表详情:先查本工作空间,未命中回退全局元数据."""
    from services.datacatalog.services import catalog_service

    result = catalog_service.get_table_detail(table_name, workspace_id)
    if not result and workspace_id:
        result = catalog_service.get_table_detail(table_name, 0)
    return result


# ── 工具 handler(SDK 无关) ─────────────────────────────────────

async def search_metadata(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        result = await asyncio.to_thread(
            _global_search_with_global,
            args.get("query", ""),
            args.get("type"),
            ctx.workspace_id,
            10,
        )
        return _text(result)
    except Exception as e:
        logger.error("search_metadata error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def get_table_schema(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        result = await asyncio.to_thread(
            _get_table_detail_with_global,
            args["table_name"],
            ctx.workspace_id,
        )
        if not result:
            return _text({"error": f"Table '{args['table_name']}' not found"}, is_error=True)
        return _text(result)
    except Exception as e:
        logger.error("get_table_schema error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def list_datasources(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.datacatalog.services.datasource_service import datasource_service
        rows = await datasource_service.list_datasources(workspace_id=ctx.workspace_id)
        return _text({"datasources": rows, "total": len(rows)})
    except Exception as e:
        logger.error("list_datasources error: %s", e)
        return _text({"error": str(e)}, is_error=True)


# 工具元信息(name / description / schema / annotations)
TOOL_SPECS = [
    {
        "name": "search_metadata",
        "description": (
            "Search the data catalog for tables, columns, metrics and business terms by keyword. "
            "Use this FIRST to discover which tables exist before writing any SQL. "
            "Prefer specific keywords (e.g. 'orders', '用户'); pass empty string to browse all."
        ),
        "schema": {
            "query": Annotated[str, "Search keyword, e.g. '用户' or 'sales'; empty means list all"],
            "type": Annotated[Optional[str], "Optional filter: table | column | metric | term"],
        },
        "handler": search_metadata,
    },
    {
        "name": "get_table_schema",
        "description": (
            "Get the full schema of one table: columns, types, comments and sensitivity flags. "
            "Call this for each table you plan to use in SQL."
        ),
        "schema": {"table_name": Annotated[str, "Exact table name, e.g. 'adh_users'"]},
        "handler": get_table_schema,
    },
    {
        "name": "list_datasources",
        "description": (
            "List available datasources (MySQL/Doris/ES) in the current workspace with their ids and db_type. "
            "Needed to pick datasource_id before execute_sql."
        ),
        "schema": {},
        "handler": list_datasources,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_catalog_server(backend: str = "qoder"):
    """构建 catalog 进程内 MCP server(qoder / claude)."""
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in TOOL_SPECS
    ]
    return make_server(backend, "datahub_catalog", tools)
