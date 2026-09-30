"""MCP Server for DataCatalog - Tools for external AI integration.

Exposes data catalog tools via MCP protocol for Claude Desktop, Cursor, etc.

统一用 mcp SDK 低级 Server(list_tools/call_tool 装饰器)：mcp_base.create_mcp_server
返回的是 mcp.server.Server，FastMCP 风格的 @mcp.tool() 在其上不存在（会 AttributeError）。
"""

import json
import logging
from typing import Optional

from ..shared.common.mcp_base import create_mcp_server, create_mcp_starlette_app
from .services import catalog_service, metrics_service, tags_service

logger = logging.getLogger(__name__)

server = create_mcp_server("datacatalog", "Data Catalog MCP Server")

TOOLS_SCHEMA = [
    {
        "name": "search_metadata",
        "description": "Search metadata across tables, columns, metrics, and terms.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
                "type": {"type": "string",
                          "description": 'Optional filter - "table", "column", "metric", "term"'},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_table_schema",
        "description": "Get table structure including columns and metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table name to look up"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_metrics",
        "description": "Query metrics by name or tags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric_name": {"type": "string", "description": "Optional metric name filter"},
                "tags": {"type": "string", "description": "Optional comma-separated tags filter"},
            },
        },
    },
    {
        "name": "query_tags",
        "description": "Query entities by tag conditions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag_conditions": {"type": "string",
                                    "description": 'JSON string, e.g. {"conditions": [{"tag_id": 1}], "operator": "AND"}'},
            },
            "required": ["tag_conditions"],
        },
    },
]


async def search_metadata(query: str, type: Optional[str] = None) -> str:
    """Search metadata across tables, columns, metrics, and terms.

    Args:
        query: Search keyword
        type: Optional filter - "table", "column", "metric", "term"

    Returns:
        JSON string with search results
    """
    try:
        results = catalog_service.global_search(
            keyword=query,
            search_type=type,
            limit=10,
        )
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"search_metadata error: {e}")
        return json.dumps({"error": str(e)})


async def get_table_schema(table_name: str) -> str:
    """Get table structure including columns and metadata.

    Args:
        table_name: Table name to look up

    Returns:
        JSON string with table schema
    """
    try:
        result = catalog_service.get_table_detail(table_name=table_name)
        if not result:
            return json.dumps({"error": f"Table '{table_name}' not found"})
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"get_table_schema error: {e}")
        return json.dumps({"error": str(e)})


async def get_metrics(metric_name: Optional[str] = None, tags: Optional[str] = None) -> str:
    """Query metrics by name or tags.

    Args:
        metric_name: Optional metric name filter
        tags: Optional comma-separated tags filter

    Returns:
        JSON string with metrics list
    """
    try:
        result = metrics_service.list_metrics(
            search=metric_name or "",
            tags=tags,
            size=20,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"get_metrics error: {e}")
        return json.dumps({"error": str(e)})


async def query_tags(tag_conditions: str) -> str:
    """Query entities by tag conditions.

    Args:
        tag_conditions: JSON string with conditions, e.g.
            '{"conditions": [{"tag_id": 1}, {"tag_id": 2}], "operator": "AND"}'

    Returns:
        JSON string with matching entities
    """
    try:
        params = json.loads(tag_conditions)
        conditions = params.get("conditions", [])
        operator = params.get("operator", "AND")

        if not conditions:
            return json.dumps({"error": "At least one tag condition required"})

        result = tags_service.query_entities_by_tags(
            conditions=conditions,
            operator=operator,
        )
        return json.dumps({"items": result, "total": len(result)}, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON in tag_conditions"})
    except Exception as e:
        logger.error(f"query_tags error: {e}")
        return json.dumps({"error": str(e)})


@server.list_tools()
async def _list_tools() -> list:
    """Expose the catalog tool catalog with hand-written JSON schemas."""
    from mcp.types import Tool
    return [Tool(**spec) for spec in TOOLS_SCHEMA]


@server.call_tool()
async def _call_tool(name: str, arguments: dict) -> list:
    from mcp.types import TextContent

    args = arguments or {}
    try:
        if name == "search_metadata":
            text = await search_metadata(args.get("query", ""), args.get("type"))
        elif name == "get_table_schema":
            text = await get_table_schema(args.get("table_name", ""))
        elif name == "get_metrics":
            text = await get_metrics(args.get("metric_name"), args.get("tags"))
        elif name == "query_tags":
            text = await query_tags(args.get("tag_conditions", ""))
        else:
            text = json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    except Exception:
        # 各工具内部已捕获业务错误；此处兜结构性异常，不回显原始报错。
        logger.exception("MCP tool %s failed", name)
        text = json.dumps({"error": "工具执行失败，请联系管理员查看服务端日志"}, ensure_ascii=False)
    return [TextContent(type="text", text=text)]


def create_mcp_app():
    """Create the MCP Starlette app for serving (挂载于 datacatalog/main.py: /mcp)。"""
    return create_mcp_starlette_app(server, sse_path="/sse", message_path="/messages")
