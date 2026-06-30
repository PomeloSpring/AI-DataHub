"""MCP Registry — manages MCP Server configurations and connections.

Loads server configs from adh_mcp_servers table, manages connection lifecycle,
and provides a unified interface to access all connected servers.

NOTE: MCP SDK context managers use anyio cancel scopes that are task-bound —
they MUST be entered and exited in the same asyncio task.  We therefore do NOT
cache connections across tasks. Each call creates a fresh connection that is
cleaned up within the same task.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pymysql

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP Server."""
    id: int
    name: str
    description: str = ""
    transport: str = "sse"  # sse, streamable_http, or stdio
    url: str = ""  # for SSE/HTTP transport
    command: str = ""  # for stdio transport
    args: list[str] = field(default_factory=list)  # for stdio transport
    env: dict[str, str] = field(default_factory=dict)  # environment variables for stdio
    tools_config: list[dict] = field(default_factory=list)  # filtered tools (whitelist applied)
    tools_whitelist: list[dict] = field(default_factory=list)  # raw whitelist from tools_config
    is_active: bool = True
    datasource_id: int = 0


class MCPRegistry:
    """Registry of MCP Servers. Manages configs and active connections."""

    def __init__(self):
        self._configs: dict[int, MCPServerConfig] = {}
        self._last_load: float = 0
        self._load_ttl: float = 60  # reload configs every 60s

    def load_configs(self, force: bool = False) -> list[MCPServerConfig]:
        """Load MCP server configs from database."""
        now = time.time()
        if not force and self._configs and (now - self._last_load) < self._load_ttl:
            return list(self._configs.values())

        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, name, description, transport, url, command, "
                        "args, `env`, tools_config, discovered_tools, is_active, datasource_id "
                        "FROM adh_mcp_servers WHERE is_active = 1"
                    )
                    rows = cur.fetchall()

                self._configs.clear()
                for row in rows:
                    args = row.get("args", "")
                    if isinstance(args, str):
                        args = [a.strip() for a in args.split(",") if a.strip()]

                    # Parse env (JSON string → dict)
                    env_raw = row.get("env", "")
                    env = {}
                    if isinstance(env_raw, str) and env_raw.strip():
                        try:
                            env = json.loads(env_raw)
                        except json.JSONDecodeError:
                            # Try KEY=VALUE format
                            for line in env_raw.strip().split("\n"):
                                if "=" in line:
                                    k, v = line.split("=", 1)
                                    env[k.strip()] = v.strip()

                    # Load discovered_tools (full list from MCP server)
                    discovered = row.get("discovered_tools", "")
                    if isinstance(discovered, str):
                        try:
                            discovered = json.loads(discovered) if discovered else []
                        except json.JSONDecodeError:
                            discovered = []

                    # Load tools_config (whitelist of allowed tools)
                    whitelist = row.get("tools_config", "")
                    if isinstance(whitelist, str):
                        try:
                            whitelist = json.loads(whitelist) if whitelist else []
                        except json.JSONDecodeError:
                            whitelist = []

                    # If whitelist is set, filter discovered tools to only include whitelisted
                    if whitelist:
                        whitelist_names = {t.get("name") for t in whitelist if isinstance(t, dict)}
                        tools_cfg = [t for t in discovered if t.get("name") in whitelist_names]
                    else:
                        # No whitelist = allow all discovered tools
                        tools_cfg = discovered

                    # Store whitelist separately for Chat API
                    tools_whitelist = whitelist

                    config = MCPServerConfig(
                        id=row["id"],
                        name=row["name"],
                        description=row.get("description", ""),
                        transport=row.get("transport", "sse"),
                        url=row.get("url", ""),
                        command=row.get("command", ""),
                        args=args,
                        env=env,
                        tools_config=tools_cfg,
                        tools_whitelist=tools_whitelist,
                        is_active=bool(row.get("is_active", 1)),
                        datasource_id=row.get("datasource_id", 0),
                    )
                    self._configs[config.id] = config

                self._last_load = now
                logger.info("[MCP Registry] Loaded %d server configs", len(self._configs))
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MCP Registry] Failed to load configs: %s", e)

        return list(self._configs.values())

    def get_config(self, server_id: int) -> Optional[MCPServerConfig]:
        """Get a server config by ID."""
        if not self._configs:
            self.load_configs()
        return self._configs.get(server_id)

    def get_all_configs(self) -> list[MCPServerConfig]:
        """Get all server configs."""
        if not self._configs:
            self.load_configs()
        return list(self._configs.values())

    def get_configs_for_datasource(self, datasource_id: int) -> list[MCPServerConfig]:
        """Get MCP server configs associated with a datasource."""
        if not self._configs:
            self.load_configs()
        return [
            c for c in self._configs.values()
            if c.datasource_id == datasource_id or c.datasource_id == 0
        ]

    async def get_client(self, server_id: int) -> Optional["MCPClient"]:
        """Create a fresh connected MCP client for the given server.

        IMPORTANT: The caller MUST call ``client.disconnect()`` when done,
        ideally in the same async task via a ``try/finally`` block.
        Do NOT rely on garbage collection — the MCP SDK's anyio cancel scopes
        require the context manager to be exited in the same task it was entered.
        """
        from backend.mcp_client.client import MCPClient

        config = self.get_config(server_id)
        if not config:
            logger.error("[MCP Registry] Server %d not found", server_id)
            return None

        if not config.is_active:
            logger.warning("[MCP Registry] Server %s (%d) is disabled, skipping", config.name, server_id)
            return None

        client = MCPClient(
            server_id=config.id,
            name=config.name,
            transport=config.transport,
            url=config.url,
            command=config.command,
            args=config.args,
            env=config.env,
        )

        if await client.connect():
            return client
        else:
            logger.error("[MCP Registry] Failed to connect to server %s", config.name)
            return None

    async def get_all_clients(self) -> dict[int, "MCPClient"]:
        """Create fresh connected clients for all active servers.

        Returns a dict of server_id -> MCPClient. The caller is responsible
        for disconnecting all clients when done.
        """
        configs = self.load_configs()
        clients = {}
        for config in configs:
            client = await self.get_client(config.id)
            if client:
                clients[config.id] = client
        return clients

    async def disconnect_all(self):
        """No-op — connections are no longer cached across tasks."""
        pass

    def get_all_tools_for_llm(self) -> list[dict]:
        """Get all tools from server configs (no live connection needed).

        Returns tools from the database's discovered_tools field.
        """
        configs = self.load_configs()
        tools = []
        for cfg in configs:
            if not cfg.is_active:
                continue
            server_tools = cfg.tools_config or []
            for t in server_tools:
                tool_name = t.get("name", "")
                if cfg.name:
                    tool_name = f"{cfg.name}__{tool_name}"
                tools.append({
                    "name": tool_name,
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
                })
        return tools

    async def call_tool(self, qualified_name: str, arguments: dict) -> any:
        """Call a tool by its qualified name (server__tool).

        Creates a fresh connection for the call, executes the tool, then
        disconnects — all within the same async task to avoid anyio cancel
        scope errors.
        """
        import json as _json

        parts = qualified_name.split("__", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid tool name format: {qualified_name}. Expected 'server__tool'")

        server_name, tool_name = parts

        # Find the config for this server
        configs = self.load_configs()
        config = None
        for cfg in configs:
            if cfg.name == server_name:
                config = cfg
                break

        if not config:
            raise RuntimeError(f"No MCP server config found for: {server_name}")

        # Create fresh connection, call tool, disconnect — all in same task
        client = await self.get_client(config.id)
        if not client:
            raise RuntimeError(f"Failed to connect to MCP server: {server_name}")

        # Auto-parse JSON strings before sending to MCP server
        # (LLM sometimes passes dicts as JSON strings)
        from backend.mcp_client.client import MCPClient
        arguments = MCPClient._deserialize_json_strings(arguments)

        try:
            return await client.call_tool(tool_name, arguments)
        finally:
            await client.disconnect()


# ── Singleton ──────────────────────────────────────────────────────

_registry: Optional[MCPRegistry] = None


def get_mcp_registry() -> MCPRegistry:
    """Get the global MCP registry singleton."""
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry
