"""Agent Service — CRUD operations for adh_agents.

Migrated from backend/api/admin.py (agents section).
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


def list_agents(workspace_id: int = 0) -> list:
    """List agents, optionally filtered by workspace."""
    if workspace_id:
        rows = execute_query(
            "SELECT id, name, display_name, description, agent_type, "
            "system_prompt, mcp_server_ids, datasource_ids, tools, config, "
            "route_patterns, is_active, is_default, created_at, updated_at "
            "FROM adh_agents WHERE workspace_id = %s ORDER BY id DESC",
            (workspace_id,),
        )
    else:
        rows = execute_query(
            "SELECT id, name, display_name, description, agent_type, "
            "system_prompt, mcp_server_ids, datasource_ids, tools, config, "
            "route_patterns, is_active, is_default, created_at, updated_at "
            "FROM adh_agents ORDER BY id DESC"
        )
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


def get_agent(agent_id: int) -> Optional[dict]:
    """Get agent by ID."""
    row = execute_query(
        "SELECT * FROM adh_agents WHERE id = %s",
        (agent_id,),
        fetchone=True,
    )
    if not row:
        return None
    if isinstance(row.get("config"), str):
        try:
            row["config"] = json.loads(row["config"])
        except (json.JSONDecodeError, TypeError):
            pass
    for k in ("created_at", "updated_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()
    return row


def create_agent(data: dict) -> int:
    """Create a new agent. Returns the new agent ID."""
    now = _now()
    row_id = _generate_id()
    workspace_id = data.get("workspace_id", 0)
    execute_insert(
        "INSERT INTO adh_agents "
        "(id, name, display_name, description, agent_type, system_prompt, "
        "mcp_server_ids, datasource_ids, tools, config, route_patterns, "
        "is_active, is_default, workspace_id, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row_id, data.get("name", ""), data.get("display_name", ""),
         data.get("description", ""), data.get("agent_type", "custom"),
         data.get("system_prompt", ""), data.get("mcp_server_ids", ""),
         data.get("datasource_ids", ""),
         data.get("tools", ""), json.dumps(data.get("config", {}), ensure_ascii=False),
         data.get("route_patterns", ""),
         1 if data.get("is_active", True) else 0,
         1 if data.get("is_default", False) else 0,
         workspace_id, now, now),
    )
    return row_id


def update_agent(agent_id: int, data: dict) -> bool:
    """Update agent fields. Returns True if updated."""
    fields = []
    params = []
    for key in ("name", "display_name", "description", "agent_type",
                "system_prompt", "mcp_server_ids", "datasource_ids", "tools",
                "route_patterns", "is_active", "is_default", "workspace_id"):
        if key in data:
            fields.append(f"{key} = %s")
            params.append(data[key])
    if "config" in data:
        fields.append("config = %s")
        params.append(json.dumps(data["config"], ensure_ascii=False))

    if not fields:
        return False

    fields.append("updated_at = %s")
    params.append(_now())
    params.append(agent_id)
    execute_write(f"UPDATE adh_agents SET {', '.join(fields)} WHERE id = %s", params)
    return True


def delete_agent(agent_id: int) -> bool:
    """Delete agent by ID."""
    execute_write("DELETE FROM adh_agents WHERE id = %s", (agent_id,))
    return True


def reload_route_patterns():
    """Reload route patterns cache after agent changes."""
    try:
        from services.datamind.agent.router import reload_route_patterns as _reload
        _reload()
    except Exception:
        pass
