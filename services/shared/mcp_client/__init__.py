"""MCP (Model Context Protocol) Client Layer.

Provides standardized access to external services via MCP protocol.
Each datasource/service is exposed as an MCP Server with typed tools.
"""

from services.shared.mcp_client.registry import MCPRegistry, MCPServerConfig
from services.shared.mcp_client.client import MCPClient
from services.shared.mcp_client.tools import MCPToolCaller

__all__ = ["MCPRegistry", "MCPServerConfig", "MCPClient", "MCPToolCaller"]
