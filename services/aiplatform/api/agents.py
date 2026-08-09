"""Agents API — Manage agent configurations.

Migrated from backend/api/admin.py (agents section)
Table: adh_agents
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


class AgentCreate(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    agent_type: str = "custom"
    system_prompt: str = ""
    mcp_server_ids: str = ""
    datasource_ids: str = ""
    tools: str = ""
    config: dict = {}
    route_patterns: str = ""
    is_active: bool = True
    is_default: bool = False


class AgentUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    mcp_server_ids: Optional[str] = None
    datasource_ids: Optional[str] = None
    tools: Optional[str] = None
    config: Optional[dict] = None
    route_patterns: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.get("/")
def list_agents(workspace_id: int = Query(0)):
    """List agents."""
    try:
        if workspace_id:
            rows = execute_query(
                """SELECT a.* FROM adh_agents a
                   JOIN adh_workspace_agents wa ON wa.agent_name = a.name
                   WHERE wa.workspace_id = %s
                   ORDER BY a.name""",
                (workspace_id,),
            )
        else:
            rows = execute_query("SELECT * FROM adh_agents ORDER BY name")

        for r in rows:
            if isinstance(r.get("config"), str):
                try:
                    r["config"] = json.loads(r["config"])
                except (json.JSONDecodeError, TypeError):
                    pass
            for k in ("created_at", "updated_at"):
                if hasattr(r.get(k), "isoformat"):
                    r[k] = r[k].isoformat()
        return rows
    except Exception as e:
        logger.error("List agents failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
def get_agent(agent_id: int):
    """Get agent by ID."""
    try:
        row = execute_query(
            "SELECT * FROM adh_agents WHERE id = %s",
            (agent_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")

        if isinstance(row.get("config"), str):
            try:
                row["config"] = json.loads(row["config"])
            except (json.JSONDecodeError, TypeError):
                pass
        for k in ("created_at", "updated_at"):
            if hasattr(row.get(k), "isoformat"):
                row[k] = row[k].isoformat()
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get agent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_agent(req: AgentCreate):
    """Create a new agent."""
    try:
        now = _now()
        agent_id = execute_insert(
            """INSERT INTO adh_agents
               (name, display_name, description, agent_type, system_prompt,
                mcp_server_ids, datasource_ids, tools, config, route_patterns,
                is_active, is_default, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (req.name, req.display_name, req.description, req.agent_type, req.system_prompt,
             req.mcp_server_ids, req.datasource_ids, req.tools, json.dumps(req.config),
             req.route_patterns, 1 if req.is_active else 0, 1 if req.is_default else 0,
             now, now),
        )
        return {"id": agent_id, "success": True}
    except Exception as e:
        logger.error("Create agent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{agent_id}")
def update_agent(agent_id: int, req: AgentUpdate):
    """Update agent."""
    try:
        updates = []
        params = []

        if req.display_name is not None:
            updates.append("display_name = %s")
            params.append(req.display_name)
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if req.system_prompt is not None:
            updates.append("system_prompt = %s")
            params.append(req.system_prompt)
        if req.mcp_server_ids is not None:
            updates.append("mcp_server_ids = %s")
            params.append(req.mcp_server_ids)
        if req.datasource_ids is not None:
            updates.append("datasource_ids = %s")
            params.append(req.datasource_ids)
        if req.tools is not None:
            updates.append("tools = %s")
            params.append(req.tools)
        if req.config is not None:
            updates.append("config = %s")
            params.append(json.dumps(req.config))
        if req.route_patterns is not None:
            updates.append("route_patterns = %s")
            params.append(req.route_patterns)
        if req.is_active is not None:
            updates.append("is_active = %s")
            params.append(1 if req.is_active else 0)
        if req.is_default is not None:
            updates.append("is_default = %s")
            params.append(1 if req.is_default else 0)

        if not updates:
            return {"success": True, "message": "No changes"}

        updates.append("updated_at = %s")
        params.append(_now())
        params.append(agent_id)

        execute_write(f"UPDATE adh_agents SET {', '.join(updates)} WHERE id = %s", params)
        return {"success": True}
    except Exception as e:
        logger.error("Update agent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{agent_id}")
def delete_agent(agent_id: int):
    """Delete agent."""
    try:
        execute_write("DELETE FROM adh_agents WHERE id = %s", (agent_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete agent failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
