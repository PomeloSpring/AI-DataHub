"""MCP Server Service — CRUD operations for adh_mcp_servers.

Migrated from backend/api/admin.py (mcp-servers section).
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


def list_mcp_servers(workspace_id: int = 0) -> list:
    """List MCP servers, optionally filtered by workspace."""
    if workspace_id:
        rows = execute_query(
            "SELECT id, name, description, transport, url, command, args, "
            "`env`, tools_config, discovered_tools, is_active, datasource_id, workspace_id, "
            "last_test_at, last_test_status, last_test_message, created_at, updated_at "
            "FROM adh_mcp_servers WHERE workspace_id = %s ORDER BY id DESC",
            (workspace_id,),
        )
    else:
        rows = execute_query(
            "SELECT id, name, description, transport, url, command, args, "
            "`env`, tools_config, discovered_tools, is_active, datasource_id, workspace_id, "
            "last_test_at, last_test_status, last_test_message, created_at, updated_at "
            "FROM adh_mcp_servers ORDER BY id DESC"
        )
    for r in rows:
        for field in ("args", "env", "tools_config", "discovered_tools"):
            if isinstance(r.get(field), str):
                try:
                    r[field] = json.loads(r[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        for k in ("created_at", "updated_at", "last_test_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
    return rows


def get_mcp_server(server_id: int) -> Optional[dict]:
    """Get MCP server by ID."""
    row = execute_query(
        "SELECT * FROM adh_mcp_servers WHERE id = %s",
        (server_id,),
        fetchone=True,
    )
    if not row:
        return None
    for field in ("args", "env", "tools_config", "discovered_tools"):
        if isinstance(row.get(field), str):
            try:
                row[field] = json.loads(row[field])
            except (json.JSONDecodeError, TypeError):
                pass
    for k in ("created_at", "updated_at", "last_test_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()
    return row


def create_mcp_server(data: dict) -> int:
    """Create a new MCP server. Returns the new server ID."""
    now = _now()
    row_id = _generate_id()
    execute_insert(
        "INSERT INTO adh_mcp_servers "
        "(id, name, description, transport, url, command, args, `env`, tools_config, "
        "is_active, datasource_id, workspace_id, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row_id, data.get("name", ""), data.get("description", ""),
         data.get("transport", "sse"), data.get("url", ""),
         data.get("command", ""),
         json.dumps(data.get("args", []), ensure_ascii=False) if isinstance(data.get("args"), list) else data.get("args", ""),
         json.dumps(data.get("env", {}), ensure_ascii=False) if isinstance(data.get("env"), dict) else data.get("env", ""),
         json.dumps(data.get("tools_config", {}), ensure_ascii=False) if isinstance(data.get("tools_config"), dict) else data.get("tools_config", ""),
         1 if data.get("is_active", True) else 0,
         data.get("datasource_id", 0),
         data.get("workspace_id", 0), now, now),
    )
    return row_id


def update_mcp_server(server_id: int, data: dict) -> bool:
    """Update MCP server fields. Returns True if updated."""
    fields = []
    params = []
    for key in ("name", "description", "transport", "url", "command",
                "is_active", "datasource_id", "workspace_id"):
        if key in data:
            fields.append(f"{key} = %s")
            params.append(data[key])
    # JSON fields
    for key in ("args", "env", "tools_config"):
        if key in data:
            fields.append(f"{key} = %s")
            val = data[key]
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            params.append(val)

    if not fields:
        return False

    fields.append("updated_at = %s")
    params.append(_now())
    params.append(server_id)
    execute_write(f"UPDATE adh_mcp_servers SET {', '.join(fields)} WHERE id = %s", params)
    return True


def delete_mcp_server(server_id: int) -> bool:
    """Delete MCP server by ID."""
    execute_write("DELETE FROM adh_mcp_servers WHERE id = %s", (server_id,))
    return True


def get_server_tools(server_id: int) -> list:
    """Get discovered tools for an MCP server."""
    row = execute_query(
        "SELECT discovered_tools, tools_config FROM adh_mcp_servers WHERE id = %s",
        (server_id,),
        fetchone=True,
    )
    if not row:
        return []

    tools = []
    if row.get("discovered_tools"):
        try:
            tools = json.loads(row["discovered_tools"]) if isinstance(row["discovered_tools"], str) else row["discovered_tools"]
        except (json.JSONDecodeError, TypeError):
            pass

    if not tools and row.get("tools_config"):
        try:
            tools = json.loads(row["tools_config"]) if isinstance(row["tools_config"], str) else row["tools_config"]
        except (json.JSONDecodeError, TypeError):
            pass

    return tools


def update_test_result(server_id: int, success: bool, message: str, tools: list = None):
    """Update test results for an MCP server."""
    now = _now()
    execute_write(
        "UPDATE adh_mcp_servers SET "
        "last_test_at = %s, last_test_status = %s, last_test_message = %s, "
        "discovered_tools = %s, updated_at = %s WHERE id = %s",
        (now, "success" if success else "failed", message,
         json.dumps(tools, ensure_ascii=False) if tools else "", now, server_id),
    )
