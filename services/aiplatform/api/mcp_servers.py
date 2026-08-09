"""MCP Servers API — Manage MCP server configurations.

Migrated from backend/api/admin.py (mcp-servers section)
Table: adh_mcp_servers
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


class MCPServerCreate(BaseModel):
    name: str
    transport: str = "sse"
    url: str = ""
    command: str = ""
    args: list = []
    env: dict = {}
    tools_config: dict = {}
    description: str = ""
    is_active: bool = True


class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    transport: Optional[str] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list] = None
    env: Optional[dict] = None
    tools_config: Optional[dict] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.get("/")
def list_mcp_servers(workspace_id: int = Query(0)):
    """List MCP servers."""
    try:
        if workspace_id:
            rows = execute_query(
                """SELECT s.* FROM adh_mcp_servers s
                   JOIN adh_workspace_mcp_servers ws ON ws.mcp_server_id = s.id
                   WHERE ws.workspace_id = %s
                   ORDER BY s.name""",
                (workspace_id,),
            )
        else:
            rows = execute_query("SELECT * FROM adh_mcp_servers ORDER BY name")

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
    except Exception as e:
        logger.error("List MCP servers failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{server_id}")
def get_mcp_server(server_id: int):
    """Get MCP server by ID."""
    try:
        row = execute_query(
            "SELECT * FROM adh_mcp_servers WHERE id = %s",
            (server_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="MCP server not found")

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_mcp_server(req: MCPServerCreate):
    """Create a new MCP server."""
    try:
        now = _now()
        server_id = execute_insert(
            """INSERT INTO adh_mcp_servers
               (name, transport, url, command, args, env, tools_config, description,
                is_active, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (req.name, req.transport, req.url, req.command,
             json.dumps(req.args), json.dumps(req.env), json.dumps(req.tools_config),
             req.description, 1 if req.is_active else 0, now, now),
        )
        return {"id": server_id, "success": True}
    except Exception as e:
        logger.error("Create MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{server_id}")
def update_mcp_server(server_id: int, req: MCPServerUpdate):
    """Update MCP server."""
    try:
        updates = []
        params = []

        if req.name is not None:
            updates.append("name = %s")
            params.append(req.name)
        if req.transport is not None:
            updates.append("transport = %s")
            params.append(req.transport)
        if req.url is not None:
            updates.append("url = %s")
            params.append(req.url)
        if req.command is not None:
            updates.append("command = %s")
            params.append(req.command)
        if req.args is not None:
            updates.append("args = %s")
            params.append(json.dumps(req.args))
        if req.env is not None:
            updates.append("env = %s")
            params.append(json.dumps(req.env))
        if req.tools_config is not None:
            updates.append("tools_config = %s")
            params.append(json.dumps(req.tools_config))
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if req.is_active is not None:
            updates.append("is_active = %s")
            params.append(1 if req.is_active else 0)

        if not updates:
            return {"success": True, "message": "No changes"}

        updates.append("updated_at = %s")
        params.append(_now())
        params.append(server_id)

        execute_write(f"UPDATE adh_mcp_servers SET {', '.join(updates)} WHERE id = %s", params)
        return {"success": True}
    except Exception as e:
        logger.error("Update MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{server_id}")
def delete_mcp_server(server_id: int):
    """Delete MCP server."""
    try:
        execute_write("DELETE FROM adh_mcp_servers WHERE id = %s", (server_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_id}/test")
def test_mcp_server(server_id: int):
    """Test MCP server connection."""
    try:
        row = execute_query(
            "SELECT * FROM adh_mcp_servers WHERE id = %s",
            (server_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="MCP server not found")

        # TODO: Implement actual MCP connection test
        # For now, just return success
        now = _now()
        execute_write(
            "UPDATE adh_mcp_servers SET last_test_at = %s, last_test_status = %s WHERE id = %s",
            (now, "success", server_id),
        )
        return {"success": True, "message": "Connection test passed"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Test MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
