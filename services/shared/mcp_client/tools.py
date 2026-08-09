"""MCP Tool Caller — unified interface for calling MCP tools from agents.

Provides a simple way for agents to discover and call MCP tools
without managing connections directly.
"""

import logging
from typing import Any

from services.shared.mcp_client.registry import get_mcp_registry

logger = logging.getLogger(__name__)


class MCPToolCaller:
    """Unified interface for calling MCP tools.

    Usage:
        caller = MCPToolCaller()
        result = await caller.call("elasticsearch__get_document", {"index": "logs", "id": "123"})

    Each ``call()`` creates a fresh MCP connection, executes the tool,
    and disconnects — all within the same async task.
    """

    def __init__(self, server_ids: list[int] = None):
        """Initialize tool caller.

        Args:
            server_ids: Specific MCP server IDs to use. None = all active servers.
        """
        self._server_ids = server_ids
        self._registry = get_mcp_registry()

    async def initialize(self):
        """No-op — connections are created per-call to avoid anyio cancel scope issues."""
        pass

    async def list_tools(self) -> list[dict]:
        """List available tools from server configs (no live connection needed)."""
        configs = self._registry.load_configs()
        tools = []
        from services.shared.mcp_client.tools import convert_tools_for_anthropic
        for cfg in configs:
            if not cfg.is_active:
                continue
            if self._server_ids and cfg.id not in self._server_ids:
                continue
            server_tools = cfg.tools_config or []
            tools.extend(convert_tools_for_anthropic(server_tools, cfg.name))
        return tools

    async def list_tools_for_prompt(self) -> str:
        """Get a formatted string of available tools for inclusion in prompts."""
        tools = await self.list_tools()
        if not tools:
            return "（无可用工具）"

        parts = ["## 可用工具"]
        for t in tools:
            parts.append(f"- **{t['name']}**: {t['description']}")
            schema = t.get("input_schema", {})
            props = schema.get("properties", {})
            if props:
                params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
                parts.append(f"  参数: {params}")
        return "\n".join(parts)

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool by qualified name (server__tool).

        Creates a fresh connection, calls the tool, and disconnects
        within the same async task.

        Args:
            tool_name: Qualified tool name, e.g. "elasticsearch__query_sql"
            arguments: Tool arguments dict

        Returns:
            Tool result (usually text content).
        """
        # Auto-parse string arguments that should be dicts (LLM common mistake)
        import json as _json
        parsed_keys = []
        for key, val in list(arguments.items()):
            if isinstance(val, str):
                stripped = val.strip()
                if (stripped.startswith('{') and stripped.endswith('}')) or \
                   (stripped.startswith('[') and stripped.endswith(']')):
                    try:
                        arguments[key] = _json.loads(stripped)
                        parsed_keys.append(key)
                    except (ValueError, TypeError):
                        pass
        if parsed_keys:
            logger.info("[MCP Tool] Auto-parsed JSON strings for keys: %s", parsed_keys)

        logger.info("[MCP Tool] Calling %s with args types: %s", tool_name,
                    {k: type(v).__name__ for k, v in arguments.items()})
        try:
            result = await self._registry.call_tool(tool_name, arguments)
            logger.info("[MCP Tool] %s returned %d chars", tool_name, len(str(result)) if result else 0)
            return result
        except Exception as e:
            logger.error("[MCP Tool] Call failed: %s — %s", tool_name, e)
            raise

    async def close(self):
        """No-op — connections are already cleaned up per-call."""
        pass

    async def call_by_server(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool by server name and tool name separately."""
        return await self.call(f"{server_name}__{tool_name}", arguments)


def convert_tools_for_anthropic(tools: list[dict], server_name: str = "") -> list[dict]:
    """Convert MCP tool schemas to Anthropic tool_use format.

    Args:
        tools: List of MCP tool dicts with 'name', 'description', 'input_schema'
        server_name: MCP server name to use as prefix

    Returns:
        List of tool dicts in Anthropic tool_use format
    """
    result = []
    for t in tools:
        tool_name = t["name"]
        if server_name:
            tool_name = f"{server_name}__{tool_name}"

        result.append({
            "name": tool_name,
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
        })
    return result


def parse_tool_name(qualified_name: str) -> tuple[str, str]:
    """Parse a qualified tool name into server_name and tool_name.

    Args:
        qualified_name: Tool name in format "server__tool"

    Returns:
        Tuple of (server_name, tool_name)
    """
    parts = qualified_name.split("__", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid tool name format: {qualified_name}. Expected 'server__tool'")
    return parts[0], parts[1]
