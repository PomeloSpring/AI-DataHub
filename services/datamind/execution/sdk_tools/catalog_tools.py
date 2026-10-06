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


# §7 数据源黑盒：物理元数据回给 LLM 前剥除内部 id（datasource_id/行 id），
# 仅保留写 SQL 所需的建模信息（表名/列名/类型/注释/business_desc 等）。
_PHYSICAL_ID_KEYS = {"datasource_id", "id"}


def _ds_name_map() -> dict:
    """数据源 id→业务名映射(元数据查询): 剥物理 id 时以业务名补位, 供 LLM 按名选源
    (waker-datasource-domain §2: 只出业务名, 不出内部 id)。"""
    from services.shared.common.db import execute_query
    try:
        rows = execute_query("SELECT id, name FROM adh_datasources") or []
        return {int(r["id"]): (r.get("name") or "") for r in rows if r.get("id")}
    except Exception as e:  # noqa: BLE001 — 映射失败不阻断检索, 缺名处标未知
        logger.debug("ds name map unavailable: %s", e)
        return {}


def _strip_physical_ids(obj, ds_names: dict | None = None):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _PHYSICAL_ID_KEYS:
                if k == "datasource_id":
                    # 剥内部 id, 但以业务名补位(否则 LLM 无从知道表在哪个源, 会猜错源)
                    name = ""
                    try:
                        name = (ds_names or {}).get(int(v), "") if v else ""
                    except (TypeError, ValueError):
                        name = ""
                    out["datasource"] = name or "未知数据源"
                continue
            out[k] = _strip_physical_ids(v, ds_names)
        return out
    if isinstance(obj, list):
        return [_strip_physical_ids(x, ds_names) for x in obj]
    return obj


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
        return _text(_strip_physical_ids(result, _ds_name_map()))
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
        return _text(_strip_physical_ids(result, _ds_name_map()))
    except Exception as e:
        logger.error("get_table_schema error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def list_datasources(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.datacatalog.services.datasource_service import datasource_service
        rows = await datasource_service.list_datasources(
            workspace_id=ctx.workspace_id, user_id=ctx.user_id)
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
            "List the datasources you are authorized to use in the current workspace, each with its business name and db_type. "
            "Use the returned name as the 'datasource' argument of check_sql/execute_sql to target a specific source in multi-source "
            "scenarios; omit it to use the session-selected source. Internal ids are never exposed."
        ),
        "schema": {},
        "handler": list_datasources,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_catalog_server(backend: str = "qoder", tool_names=None):
    """构建 catalog 进程内 MCP server(qoder / claude).

    tool_names 给定时只注册被选中的工具(AS-BOT 粒度的逐个工具权限控制);
    为空则注册本组全部工具。
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
    return make_server(backend, "datahub_catalog", tools)
