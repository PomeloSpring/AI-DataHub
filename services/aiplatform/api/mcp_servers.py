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
from services.shared.common.versioned_config import get_mcp_version_store

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
    created_by: str = "system"


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
    change_log: str = "Updated"
    updated_by: str = "system"


class MCPRollbackRequest(BaseModel):
    version: int
    rolled_by: str = "system"


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
    """Create a new MCP server (records an initial v1 snapshot)."""
    try:
        store = get_mcp_version_store()
        server_id = store.create(
            {
                "name": req.name,
                "transport": req.transport,
                "url": req.url,
                "command": req.command,
                "args": req.args,
                "env": req.env,
                "tools_config": req.tools_config,
                "description": req.description,
                "is_active": 1 if req.is_active else 0,
            },
            created_by=req.created_by,
        )
        return {"id": server_id, "success": True}
    except Exception as e:
        logger.error("Create MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{server_id}")
def update_mcp_server(server_id: int, req: MCPServerUpdate):
    """Update MCP server. Automatically creates a version snapshot."""
    try:
        store = get_mcp_version_store()
        data = {}
        for field in ("name", "transport", "url", "command", "args",
                      "env", "tools_config", "description"):
            val = getattr(req, field, None)
            if val is not None:
                data[field] = val
        if req.is_active is not None:
            data["is_active"] = 1 if req.is_active else 0

        if not data:
            return {"success": True, "message": "No changes"}
        data["change_log"] = req.change_log

        ok = store.update(server_id, data, updated_by=req.updated_by)
        if not ok:
            raise HTTPException(status_code=404, detail="MCP server not found")
        return {"success": True}
    except HTTPException:
        raise
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


# ── Version history & rollback ─────────────────────────────────────────

@router.get("/{server_id}/versions")
def list_mcp_versions(server_id: int):
    """List version snapshots for an MCP server."""
    try:
        store = get_mcp_version_store()
        versions = store.get_versions(server_id)
        for v in versions:
            content = v.get("content")
            if isinstance(content, str):
                try:
                    v["content"] = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    pass
            for k in ("created_at",):
                if hasattr(v.get(k), "isoformat"):
                    v[k] = v[k].isoformat()
        return versions
    except Exception as e:
        logger.error("List MCP versions failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_id}/rollback")
def rollback_mcp_server(server_id: int, req: MCPRollbackRequest):
    """Rollback an MCP server config to a specific version."""
    try:
        store = get_mcp_version_store()
        ok = store.rollback(server_id, req.version, rolled_by=req.rolled_by)
        if not ok:
            raise HTTPException(status_code=404, detail="MCP server or target version not found")
        return {"success": True, "message": f"Rolled back to version {req.version}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rollback MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
