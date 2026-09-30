"""MCP Server base class for all microservices.

Each service creates an MCP server that exposes tools and resources
for external AI tool integration (Claude Desktop, Cursor, etc.).

统一用 mcp SDK 低级 Server(list_tools/call_tool 装饰器)——FastMCP 风格的
`@server.tool()` 在低级 Server 上不存在，使用会 AttributeError。

Usage in each service's mcp_server.py:
    from services.shared.common.mcp_base import create_mcp_server, create_mcp_starlette_app

    server = create_mcp_server("datacatalog", "Data Catalog MCP Server")

    @server.list_tools()
    async def _list_tools(): ...
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
        sse_path: URL path for SSE endpoint (子 app 内相对路径)
        message_path: URL path for message endpoint (子 app 内相对路径)

    挂载前缀由 Starlette Mount 的 root_path 自动处理：mcp SDK 的 SseServerTransport
    在 SSE 连接建立时按 root_path + message_path 回传 endpoint event，
    因此此处只传相对路径（手动拼接前缀会造成 /mcp/mcp/messages 双重前缀）。

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
