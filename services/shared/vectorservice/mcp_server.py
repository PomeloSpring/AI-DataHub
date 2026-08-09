"""MCP Server for VectorService.

Exposes vector search and upsert operations as MCP tools via SSE transport.
Runs on port 31010.

Tools:
    - search_vector: Search similar vectors in a Doris table
    - upsert_vector: Insert or update a record with embedding
"""

import json
import logging
import sys
from pathlib import Path

# Add shared modules to path
_shared_dir = Path(__file__).resolve().parent.parent
if str(_shared_dir.parent) not in sys.path:
    sys.path.insert(0, str(_shared_dir.parent))

from mcp import types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from vector_db import get_vector_connection

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── MCP Server ────────────────────────────────────────────────────────

server = Server("vectorservice")


def _embedding_to_sql_literal(embedding: list[float]) -> str:
    """Convert embedding list to SQL array literal for Doris HNSW."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


# ── Tool Definitions ──────────────────────────────────────────────────

TOOLS = [
    types.Tool(
        name="search_vector",
        description=(
            "Search for similar vectors in a Doris table using HNSW index. "
            "Performs L2 distance-based nearest neighbor search. "
            "The table must have an 'embedding' column with HNSW index."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query text. Will be embedded into a vector before searching.",
                },
                "table": {
                    "type": "string",
                    "description": "The Doris table name (e.g., 'adh_table_info').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 20).",
                    "default": 20,
                },
                "filters": {
                    "type": "object",
                    "description": "Optional column filters as key-value pairs. Example: {\"is_active\": 1}",
                    "additionalProperties": True,
                },
            },
            "required": ["query", "table"],
        },
    ),
    types.Tool(
        name="upsert_vector",
        description=(
            "Insert or update a record with vector embedding in a Doris table. "
            "Uses DELETE + INSERT pattern for Doris DUPLICATE KEY table compatibility. "
            "The data dict must include an 'embedding' key with the vector values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "The Doris table name.",
                },
                "id_column": {
                    "type": "string",
                    "description": "The primary key column name (e.g., 'id').",
                },
                "id_value": {
                    "type": "string",
                    "description": "The primary key value to upsert.",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Column values as key-value pairs. Must include 'embedding' "
                        "with a list of float values. Example: "
                        '{"id": 1, "name": "test", "embedding": [0.1, 0.2, ...]}'
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["table", "id_column", "id_value", "data"],
        },
    ),
]


# ── Handlers ──────────────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return the list of available tools."""
    return TOOLS


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    """Handle tool calls from MCP clients."""
    if name == "search_vector":
        return await _handle_search_vector(arguments)
    elif name == "upsert_vector":
        return await _handle_upsert_vector(arguments)
    else:
        return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def _handle_search_vector(arguments: dict) -> list[types.TextContent]:
    """Handle search_vector tool call."""
    try:
        query = arguments["query"]
        table = arguments["table"]
        limit = arguments.get("limit", 20)
        filters = arguments.get("filters")

        # Generate embedding for the query text
        try:
            from shared.common.embedding import generate_embedding
        except ImportError:
            try:
                sys.path.insert(0, str(_shared_dir.parent.parent / "backend"))
                from common.llm.embedding import generate_embedding
            except ImportError:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"error": "Embedding module not available."}),
                )]

        query_embedding = generate_embedding(query)
        vec_literal = _embedding_to_sql_literal(query_embedding)

        # Build WHERE clause
        where_parts = []
        params = []
        if filters:
            for key, value in filters.items():
                if key == "_raw":
                    where_parts.append(value)
                elif isinstance(value, (list, tuple)):
                    placeholders = ", ".join(["%s"] * len(value))
                    where_parts.append(f"`{key}` IN ({placeholders})")
                    params.extend(value)
                else:
                    where_parts.append(f"`{key}` = %s")
                    params.append(value)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        sql = f"""
            SELECT *,
                   l2_distance_approximate(embedding, {vec_literal}) AS distance
            FROM {table}
            WHERE {where_clause}
            ORDER BY distance ASC
            LIMIT %s
        """
        params.append(limit)

        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                results = cur.fetchall()

        # Sanitize results for JSON serialization
        sanitized = []
        for row in results:
            clean = {}
            for k, v in row.items():
                if isinstance(v, list):
                    # Skip embedding columns in output to reduce size
                    if k == "embedding":
                        continue
                    clean[k] = v
                elif hasattr(v, "isoformat"):
                    clean[k] = v.isoformat()
                elif hasattr(v, "__float__"):
                    clean[k] = float(v)
                else:
                    clean[k] = v
            sanitized.append(clean)

        return [types.TextContent(
            type="text",
            text=json.dumps({
                "results": sanitized,
                "count": len(sanitized),
                "table": table,
            }, ensure_ascii=False),
        )]
    except Exception as e:
        logger.error("MCP search_vector failed: %s", e)
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": f"Search failed: {e}"}),
        )]


async def _handle_upsert_vector(arguments: dict) -> list[types.TextContent]:
    """Handle upsert_vector tool call."""
    try:
        table = arguments["table"]
        id_column = arguments["id_column"]
        id_value = arguments["id_value"]
        data = arguments["data"]

        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                # Delete existing record
                cur.execute(
                    f"DELETE FROM {table} WHERE `{id_column}` = %s",
                    (id_value,),
                )

                # Insert new record
                cols = ", ".join(f"`{k}`" for k in data.keys())
                placeholders = ", ".join(["%s"] * len(data))
                cur.execute(
                    f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                    list(data.values()),
                )

        return [types.TextContent(
            type="text",
            text=json.dumps({
                "success": True,
                "table": table,
                "id_column": id_column,
                "id_value": id_value,
            }, ensure_ascii=False),
        )]
    except Exception as e:
        logger.error("MCP upsert_vector failed: %s", e)
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": f"Upsert failed: {e}"}),
        )]


# ── SSE Transport ─────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages/")


async def handle_sse(request):
    """Handle SSE connection from MCP client."""
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


# ── Starlette App ─────────────────────────────────────────────────────

mcp_app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ],
)


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 31010
    logger.info("Starting MCP SSE server on port %d...", port)
    uvicorn.run(mcp_app, host="0.0.0.0", port=port, log_level="info")
