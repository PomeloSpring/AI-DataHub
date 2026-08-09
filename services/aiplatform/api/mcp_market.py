"""MCP Market API — MCP server marketplace.

Migrated from backend/api/mcp_market.py
Table: adh_mcp_registry
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def list_mcp_market(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """List MCP marketplace entries."""
    try:
        conditions = []
        params = []

        if category:
            conditions.append("category = %s")
            params.append(category)
        if search:
            conditions.append("(name LIKE %s OR description LIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = execute_query(
            f"SELECT * FROM adh_mcp_registry {where} ORDER BY is_popular DESC, sort_order, name",
            params,
        )
        for r in rows:
            for field in ("tags", "required_env"):
                if isinstance(r.get(field), str):
                    import json
                    try:
                        r[field] = json.loads(r[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
        return rows
    except Exception as e:
        logger.error("List MCP market failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{registry_id}")
def get_mcp_market_entry(registry_id: int):
    """Get MCP marketplace entry by ID."""
    try:
        row = execute_query(
            "SELECT * FROM adh_mcp_registry WHERE id = %s",
            (registry_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get MCP market entry failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{registry_id}/install")
def install_from_market(registry_id: int, workspace_id: int = Query(0)):
    """Install MCP server from marketplace."""
    try:
        entry = execute_query(
            "SELECT * FROM adh_mcp_registry WHERE id = %s",
            (registry_id,),
            fetchone=True,
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")

        import json
        import time
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # Create MCP server from registry entry
        server_id = execute_insert(
            """INSERT INTO adh_mcp_servers
               (name, transport, command, args, env, description, is_active, created_at, updated_at)
               VALUES (%s, 'stdio', %s, %s, %s, %s, 1, %s, %s)""",
            (entry["name"], entry.get("install_cmd", ""),
             json.dumps(entry.get("default_args", [])),
             json.dumps(entry.get("required_env", {})),
             entry.get("description", ""), now, now),
        )

        return {"server_id": server_id, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Install from market failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
