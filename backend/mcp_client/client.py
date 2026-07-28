"""MCP Client — connects to MCP Servers via SSE, Streamable HTTP, or stdio transport.

Wraps the mcp Python SDK to provide a simple interface for:
- Listing available tools on a server
- Calling a tool with arguments
- Managing connection lifecycle
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for a single MCP Server.

    Supports two transports:
    - SSE: HTTP Server-Sent Events (remote services)
    - stdio: subprocess with stdin/stdout (local tools)
    """

    def __init__(self, server_id: int, name: str, transport: str,
                 url: str = None, command: str = None, args: list[str] = None,
                 env: dict[str, str] = None, docker_image: str = None):
        self.server_id = server_id
        self.name = name
        self.transport = transport
        self.url = url
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.docker_image = docker_image or ""
        self._session = None
        self._transport_ctx = None
        self._tools: list[dict] = []
        self._connected = False

    async def connect(self) -> bool:
        """Connect to the MCP Server and list available tools."""
        try:
            if self.transport == "sse":
                return await self._connect_sse()
            elif self.transport == "streamable_http":
                return await self._connect_streamable_http()
            elif self.transport == "stdio":
                return await self._connect_stdio()
            else:
                logger.error("[MCP] Unknown transport: %s", self.transport)
                return False
        except Exception as e:
            logger.error("[MCP] Failed to connect to %s: %s", self.name, e)
            return False

    async def _connect_sse(self) -> bool:
        """Connect via SSE transport."""
        try:
            from mcp.client.sse import sse_client
            from mcp import ClientSession

            self._transport_ctx = sse_client(self.url)
            read, write = await self._transport_ctx.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()

            # List available tools
            result = await self._session.list_tools()
            self._tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema if hasattr(t, 'inputSchema') else {},
                }
                for t in result.tools
            ]
            self._connected = True
            logger.info("[MCP] Connected to %s via SSE, %d tools available",
                        self.name, len(self._tools))
            return True
        except ImportError:
            logger.error("[MCP] mcp package not installed. Run: pip install mcp")
            return False

    async def _connect_streamable_http(self) -> bool:
        """Connect via Streamable HTTP transport."""
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from mcp import ClientSession

            self._transport_ctx = streamablehttp_client(self.url)
            read, write, _ = await self._transport_ctx.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()

            # List available tools
            result = await self._session.list_tools()
            self._tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema if hasattr(t, 'inputSchema') else {},
                }
                for t in result.tools
            ]
            self._connected = True
            logger.info("[MCP] Connected to %s via streamable_http, %d tools available",
                        self.name, len(self._tools))
            return True
        except ImportError:
            logger.error("[MCP] mcp package not installed. Run: pip install mcp")
            return False

    async def _connect_stdio(self) -> bool:
        """Connect via stdio transport (subprocess or Docker container)."""
        try:
            from mcp.client.stdio import stdio_client
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters

            # ── Docker 模式 ──
            if self.docker_image:
                # 构建 docker run 命令
                docker_cmd_parts = ["docker", "run", "-i", "--rm"]
                for k, v in self.env.items():
                    docker_cmd_parts.extend(["-e", f"{k}={v}"])
                docker_cmd_parts.append(self.docker_image)
                if self.command:
                    docker_cmd_parts.extend(self.command.split())

                # 检查是否需要 SSH（后端在容器中，Docker 在宿主机上）
                ssh_config = self._get_ssh_config()
                if ssh_config and ssh_config.get("host"):
                    # SSH 模式：通过 SSH 在宿主机上运行 Docker
                    ssh_cmd = self._build_ssh_cmd(ssh_config)
                    remote_docker = " ".join(f"'{a}'" if " " in a else a for a in docker_cmd_parts)
                    ssh_cmd.append(remote_docker)

                    logger.info("[MCP] Connecting to %s via SSH Docker: ssh %s@%s docker run ...",
                                self.name, ssh_config.get("user"), ssh_config.get("host"))

                    params = StdioServerParameters(
                        command=ssh_cmd[0],
                        args=ssh_cmd[1:],
                        env=None,
                    )
                else:
                    # 本地 Docker
                    logger.info("[MCP] Connecting to %s via local Docker: %s",
                                self.name, " ".join(docker_cmd_parts[:6]))

                    params = StdioServerParameters(
                        command="docker",
                        args=docker_cmd_parts,
                        env=None,
                    )
            else:
                # ── 本地 stdio 模式 ──
                logger.info("[MCP] Connecting to %s via stdio: command=%s, args=%s",
                            self.name, self.command, self.args)

                params = StdioServerParameters(
                    command=self.command,
                    args=self.args,
                    env=self.env if self.env else None,
                )
            self._transport_ctx = stdio_client(params)
            read, write = await self._transport_ctx.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()

            result = await self._session.list_tools()
            self._tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema if hasattr(t, 'inputSchema') else {},
                }
                for t in result.tools
            ]
            self._connected = True
            logger.info("[MCP] Connected to %s via stdio, %d tools available",
                        self.name, len(self._tools))
            return True
        except ImportError:
            logger.error("[MCP] mcp package not installed. Run: pip install mcp")
            return False
        except Exception as e:
            logger.error("[MCP] stdio connection failed for %s: %s", self.name, e, exc_info=True)
            return False

    @staticmethod
    def _deserialize_json_strings(obj: Any, _path: str = "") -> Any:
        """Recursively parse string values that look like JSON objects or arrays.

        LLM sometimes serializes nested JSON as strings (e.g., query_body as a string).
        This fixes that before sending to the MCP server.
        """
        import json
        if isinstance(obj, dict):
            return {k: MCPClient._deserialize_json_strings(v, f"{_path}.{k}") for k, v in obj.items()}
        if isinstance(obj, list):
            return [MCPClient._deserialize_json_strings(item, f"{_path}[]") for item in obj]
        if isinstance(obj, str):
            stripped = obj.strip()
            if (stripped.startswith('{') and stripped.endswith('}')) or \
               (stripped.startswith('[') and stripped.endswith(']')):
                try:
                    parsed = json.loads(stripped)
                    logger.info("[MCP] Auto-parsed JSON string at '%s' (len=%d)", _path, len(stripped))
                    return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
        return obj

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the connected MCP Server.

        Returns:
            Tool result content (text or structured data).
        """
        if not self._connected or not self._session:
            raise RuntimeError(f"MCP client '{self.name}' is not connected")

        # Fix: LLM sometimes passes JSON objects as strings — auto-parse them
        arguments = self._deserialize_json_strings(arguments)

        try:
            result = await self._session.call_tool(tool_name, arguments)

            # Extract text content from result
            if result.content:
                texts = []
                for item in result.content:
                    if hasattr(item, 'text'):
                        texts.append(item.text)
                    elif hasattr(item, 'data'):
                        texts.append(item.data)
                return "\n".join(texts) if texts else str(result.content)
            return None
        except Exception as e:
            logger.error("[MCP] Tool call failed: %s.%s — %s", self.name, tool_name, e)
            raise

    def get_tools(self) -> list[dict]:
        """Get list of available tools with their schemas."""
        return self._tools.copy()

    def get_tools_for_llm(self) -> list[dict]:
        """Get tools formatted for LLM tool_use API."""
        return [
            {
                "name": f"{self.name}__{t['name']}",
                "description": t["description"],
                "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
            }
            for t in self._tools
        ]

    async def test_connection(self) -> dict:
        """Test connection and discover tools.

        Returns:
            dict with keys: success (bool), message (str), tools (list)
        """
        try:
            connected = await self.connect()
            if not connected:
                mode = f"Docker({self.docker_image})" if self.docker_image else f"{self.transport}"
                return {
                    "success": False,
                    "message": f"无法连接到 {mode} 服务 (command: {self.command} {' '.join(self.args[:3]) if self.args else ''})",
                    "tools": [],
                }

            tools = self.get_tools()
            return {
                "success": True,
                "message": f"连接成功，发现 {len(tools)} 个工具",
                "tools": tools,
            }
        except Exception as e:
            logger.error("[MCP] test_connection failed: %s", e, exc_info=True)
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "tools": [],
            }
        finally:
            await self.disconnect()

    @staticmethod
    def _get_ssh_config() -> dict:
        """从默认沙箱配置获取 SSH 设置。"""
        try:
            from backend.services.docker_executor import _load_default_ssh_config
            return _load_default_ssh_config()
        except Exception:
            return {}

    @staticmethod
    def _build_ssh_cmd(ssh_config: dict) -> list[str]:
        """构建 SSH 命令前缀。"""
        import os
        host = ssh_config.get("host", "")
        port = ssh_config.get("port", 22)
        user = ssh_config.get("user", "root")
        auth_type = ssh_config.get("auth_type", "key")
        key_file = ssh_config.get("key_file", "")

        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30",
            "-p", str(port),
        ]
        if auth_type == "key" and key_file:
            cmd.extend(["-i", os.path.expanduser(key_file)])
        cmd.append(f"{user}@{host}")
        return cmd

    async def disconnect(self):
        """Disconnect from the MCP Server.

        All cleanup is wrapped in a broad try/except because the MCP SDK's
        anyio cancel scopes can raise RuntimeError if the context manager
        is exited from a different task than it was entered in.
        """
        try:
            if self._session:
                try:
                    await self._session.__aexit__(None, None, None)
                except Exception:
                    pass
                self._session = None
            if self._transport_ctx:
                try:
                    await self._transport_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                self._transport_ctx = None
        except Exception as e:
            logger.debug("[MCP] disconnect() cleanup error for %s: %s", self.name, e)
        finally:
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def __repr__(self):
        return f"MCPClient(name={self.name}, transport={self.transport}, connected={self._connected})"
