"""Workspace Service — manages workspaces and their resources.

A workspace groups datasources, MCP servers, and agents together.
In Agent mode, the workspace provides the available tools instead of
requiring the user to select a datasource manually.
"""

import json
import logging
from typing import Optional

from backend.common.db import get_metadata_conn

logger = logging.getLogger(__name__)


class WorkspaceService:
    """Service for managing workspaces."""

    async def get_workspace(self, workspace_id: int) -> Optional[dict]:
        """Get workspace by ID with all associated resources."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Get workspace
                cur.execute("SELECT * FROM adh_workspaces WHERE id = %s", (workspace_id,))
                workspace = cur.fetchone()
                if not workspace:
                    return None

                # 工作空间不再绑定数据源(adh_workspace_datasources 退役为冻结表):
                # 数据源可用集由用户角色授权唯一裁决, 此处不再读取空间绑定。

                # Get MCP servers (workspace_id field on adh_mcp_servers)
                cur.execute(
                    "SELECT * FROM adh_mcp_servers WHERE workspace_id = %s AND is_active = 1",
                    (workspace_id,)
                )
                workspace['mcp_servers'] = cur.fetchall()

                # Get agents (workspace_id field on adh_agents)
                cur.execute(
                    "SELECT * FROM adh_agents WHERE workspace_id = %s AND is_active = 1",
                    (workspace_id,)
                )
                workspace['agents'] = cur.fetchall()

                # Parse config JSON
                if isinstance(workspace.get('config'), str):
                    workspace['config'] = json.loads(workspace['config'])

                return workspace
        finally:
            conn.close()

    async def get_workspace_tools(self, workspace_id: int) -> Optional[dict]:
        """Get MCP tools available for this workspace.

        Uses discovered_tools as the base list, filtered by tools_config
        (whitelist) if set. This avoids duplicate tools.
        """
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            return None

        mcp_tools = []
        for server in workspace.get('mcp_servers', []):
            # Parse discovered_tools (full tool list from MCP server)
            discovered = server.get('discovered_tools', '')
            all_tools = []
            if isinstance(discovered, str) and discovered:
                try:
                    parsed = json.loads(discovered)
                    if isinstance(parsed, list):
                        all_tools = parsed
                except json.JSONDecodeError:
                    pass

            # Parse tools_config as whitelist filter
            tools_config = server.get('tools_config', '')
            whitelist = None
            if isinstance(tools_config, str) and tools_config:
                try:
                    parsed = json.loads(tools_config)
                    if isinstance(parsed, list) and parsed:
                        whitelist = {t.get('name') for t in parsed if isinstance(t, dict)}
                except json.JSONDecodeError:
                    pass

            # Filter: if whitelist is set, only include whitelisted tools
            if whitelist:
                filtered = [t for t in all_tools if t.get('name') in whitelist]
            else:
                filtered = all_tools

            for t in filtered:
                t['server_id'] = server['id']
                t['server_name'] = server.get('name', '')
            mcp_tools.extend(filtered)

        return {'mcp_tools': mcp_tools}

    async def get_user_workspaces(self, user_id: int) -> list[dict]:
        """Get all workspaces for a user."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT w.*, wu.role, wu.is_default as user_default,
                              u.username as owner_name
                       FROM adh_workspace_users wu
                       JOIN adh_workspaces w ON w.id = wu.workspace_id
                       LEFT JOIN adh_users u ON u.id = w.owner_id
                       WHERE wu.user_id = %s
                       ORDER BY wu.is_default DESC, w.name""",
                    (user_id,)
                )
                workspaces = cur.fetchall()

                # Parse config JSON for each workspace
                for ws in workspaces:
                    if isinstance(ws.get('config'), str):
                        ws['config'] = json.loads(ws['config'])

                return workspaces
        finally:
            conn.close()


_service = WorkspaceService()


def get_workspace_service() -> WorkspaceService:
    return _service
