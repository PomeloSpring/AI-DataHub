"""Embed API — Third-party embed integration.

Migrated from backend/api/embed.py
Tables: adh_applications, adh_embed_logs
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


class ApplicationCreate(BaseModel):
    name: str
    description: str = ""
    enable_chat: bool = True
    allowed_dashboards: str = ""
    allowed_tables: str = ""
    rate_limit: int = 100


class ApplicationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enable_chat: Optional[bool] = None
    allowed_dashboards: Optional[str] = None
    allowed_tables: Optional[str] = None
    rate_limit: Optional[int] = None
    status: Optional[str] = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.get("/applications")
def list_applications():
    """List embed applications."""
    try:
        rows = execute_query(
            "SELECT id, name, description, status, enable_chat, rate_limit, created_at "
            "FROM adh_applications ORDER BY created_at DESC"
        )
        for r in rows:
            if hasattr(r.get("created_at"), "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
        return rows
    except Exception as e:
        logger.error("List applications failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/applications")
def create_application(req: ApplicationCreate):
    """Create embed application."""
    try:
        import hashlib
        import secrets
        now = _now()
        app_key = secrets.token_hex(32)
        app_key_hash = hashlib.sha256(app_key.encode()).hexdigest()

        app_id = execute_insert(
            """INSERT INTO adh_applications
               (name, description, app_key_hash, status, enable_chat,
                allowed_dashboards, allowed_tables, rate_limit, created_at)
               VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s)""",
            (req.name, req.description, app_key_hash, 1 if req.enable_chat else 0,
             req.allowed_dashboards, req.allowed_tables, req.rate_limit, now),
        )
        return {"id": app_id, "app_key": app_key, "success": True}
    except Exception as e:
        logger.error("Create application failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/applications/{app_id}")
def update_application(app_id: int, req: ApplicationUpdate):
    """Update embed application."""
    try:
        updates = []
        params = []

        if req.name is not None:
            updates.append("name = %s")
            params.append(req.name)
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if req.enable_chat is not None:
            updates.append("enable_chat = %s")
            params.append(1 if req.enable_chat else 0)
        if req.allowed_dashboards is not None:
            updates.append("allowed_dashboards = %s")
            params.append(req.allowed_dashboards)
        if req.allowed_tables is not None:
            updates.append("allowed_tables = %s")
            params.append(req.allowed_tables)
        if req.rate_limit is not None:
            updates.append("rate_limit = %s")
            params.append(req.rate_limit)
        if req.status is not None:
            updates.append("status = %s")
            params.append(req.status)

        if not updates:
            return {"success": True}

        params.append(app_id)
        execute_write(f"UPDATE adh_applications SET {', '.join(updates)} WHERE id = %s", params)
        return {"success": True}
    except Exception as e:
        logger.error("Update application failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/applications/{app_id}")
def delete_application(app_id: int):
    """Delete embed application."""
    try:
        execute_write("DELETE FROM adh_applications WHERE id = %s", (app_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete application failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
def list_embed_logs(
    app_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List embed API call logs."""
    try:
        conditions = []
        params = []
        if app_id:
            conditions.append("app_id = %s")
            params.append(app_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) as total FROM adh_embed_logs {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * page_size
                cur.execute(
                    f"SELECT * FROM adh_embed_logs {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [page_size, offset],
                )
                rows = cur.fetchall()
                for r in rows:
                    if hasattr(r.get("created_at"), "isoformat"):
                        r["created_at"] = r["created_at"].isoformat()

        return {"total": total, "page": page, "page_size": page_size, "items": rows}
    except Exception as e:
        logger.error("List embed logs failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
