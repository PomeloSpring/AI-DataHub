"""MCP Server for DataCatalog - Tools for external AI integration.

Exposes data catalog tools via MCP protocol for Claude Desktop, Cursor, etc.
"""

import json
import logging
from typing import Optional

from ..shared.common.mcp_base import create_mcp_server, create_mcp_starlette_app
from .services import catalog_service, metrics_service, tags_service

logger = logging.getLogger(__name__)

mcp = create_mcp_server("datacatalog", "Data Catalog MCP Server")


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


def create_mcp_app():
    """Create the MCP Starlette app for serving."""
    return create_mcp_starlette_app(mcp, sse_path="/sse", message_path="/messages")
