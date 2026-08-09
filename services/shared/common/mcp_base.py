"""MCP Server base class for all microservices.

Each service creates an MCP server that exposes tools and resources
for external AI tool integration (Claude Desktop, Cursor, etc.).

Usage in each service's main.py:
    from services.shared.common.mcp_base import create_mcp_server

    mcp = create_mcp_server("datacatalog", "Data Catalog MCP Server")

    @mcp.tool()
    async def search_metadata(query: str) -> str:
        ...
"""

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
import json


def create_mcp_server(name: str, description: str) -> Server:
    """Create an MCP server instance for a microservice.

    Args:
        name: Service name (e.g., "datacatalog")
        description: Human-readable description

    Returns:
        Configured MCP Server instance
    """
    server = Server(name)
    return server


def create_mcp_starlette_app(server: Server, sse_path: str = "/sse", message_path: str = "/messages") -> Starlette:
    """Create a Starlette app that serves the MCP server over SSE.

    Args:
        server: MCP Server instance
        sse_path: URL path for SSE endpoint
        message_path: URL path for message endpoint

    Returns:
        Starlette ASGI application
    """
    sse_transport = SseServerTransport(message_path)

    async def handle_sse(request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    async def handle_messages(request):
        await sse_transport.handle_post_message(request.scope, request.receive, request._send)

    routes = [
        Route(sse_path, endpoint=handle_sse),
        Route(message_path, endpoint=handle_messages, methods=["POST"]),
    ]

    return Starlette(routes=routes)
