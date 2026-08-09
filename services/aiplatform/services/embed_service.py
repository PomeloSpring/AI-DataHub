"""Embed Service — Third-party embed integration.

Migrated from backend/api/embed.py.
Tables: adh_applications, adh_embed_logs
"""

import hashlib
import json
import logging
import secrets
import time as _time
from datetime import datetime
from typing import Optional, Tuple

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Application Management ──────────────────────────────────────────

def list_applications(page: int = 1, size: int = 50, search: str = "") -> Tuple[list, int]:
    """List embed applications with pagination."""
    conditions = []
    params = []
    if search:
        conditions.append("(name LIKE %s OR description LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_applications {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, name, description, status, enable_chat, "
                f"allowed_dashboards, allowed_tables, rate_limit, created_at "
                f"FROM adh_applications {where} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()

    return rows, total


def get_app_by_id(app_id: int) -> Optional[dict]:
    """Get application by ID."""
    return execute_query(
        "SELECT * FROM adh_applications WHERE id = %s", (app_id,), fetchone=True
    )


def create_application(
    name: str, description: str = "", enable_chat: bool = True,
    allowed_dashboards: str = "", allowed_tables: str = "", rate_limit: int = 100,
    created_by: int = 0,
) -> Tuple[bool, str, int, str]:
    """Create a new embed application. Returns (ok, msg, app_id, api_key)."""
    now = _now()
    app_key = secrets.token_hex(32)
    app_key_hash = hashlib.sha256(app_key.encode()).hexdigest()

    app_id = execute_insert(
        "INSERT INTO adh_applications "
        "(name, description, app_key_hash, status, enable_chat, "
        "allowed_dashboards, allowed_tables, rate_limit, created_at) "
        "VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s)",
        (name, description, app_key_hash, 1 if enable_chat else 0,
         allowed_dashboards, allowed_tables, rate_limit, now),
    )
    return True, "Application created", app_id, app_key


def update_application(
    app_id: int, name: str = None, description: str = None,
    enable_chat: bool = None, allowed_dashboards: str = None,
    allowed_tables: str = None, rate_limit: int = None, status: str = None,
) -> Tuple[bool, str]:
    """Update an embed application."""
    updates = []
    params = []

    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if description is not None:
        updates.append("description = %s")
        params.append(description)
    if enable_chat is not None:
        updates.append("enable_chat = %s")
        params.append(1 if enable_chat else 0)
    if allowed_dashboards is not None:
        updates.append("allowed_dashboards = %s")
        params.append(allowed_dashboards)
    if allowed_tables is not None:
        updates.append("allowed_tables = %s")
        params.append(allowed_tables)
    if rate_limit is not None:
        updates.append("rate_limit = %s")
        params.append(rate_limit)
    if status is not None:
        updates.append("status = %s")
        params.append(status)

    if not updates:
        return True, "No changes"

    params.append(app_id)
    execute_write(f"UPDATE adh_applications SET {', '.join(updates)} WHERE id = %s", params)
    return True, "Updated"


def delete_application(app_id: int) -> Tuple[bool, str]:
    """Delete an embed application."""
    execute_write("DELETE FROM adh_applications WHERE id = %s", (app_id,))
    return True, "Deleted"


def rotate_api_key(app_id: int) -> Tuple[bool, str, str]:
    """Rotate API key for an application."""
    new_key = secrets.token_hex(32)
    new_hash = hashlib.sha256(new_key.encode()).hexdigest()
    execute_write(
        "UPDATE adh_applications SET app_key_hash = %s WHERE id = %s",
        (new_hash, app_id),
    )
    return True, "Key rotated", new_key


def find_app_by_api_key(api_key: str) -> Optional[dict]:
    """Find application by API key."""
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    return execute_query(
        "SELECT * FROM adh_applications WHERE app_key_hash = %s AND status = 'active'",
        (key_hash,), fetchone=True,
    )


def update_last_used(app_id: int):
    """Update last_used_at timestamp."""
    execute_write(
        "UPDATE adh_applications SET last_used_at = %s WHERE id = %s",
        (_now(), app_id),
    )


# ── Embed Logs ──────────────────────────────────────────────────────

def log_embed_action(
    app_id: int, user_id: str, user_name: str, action: str,
    detail: str = "", ip_address: str = "",
):
    """Log an embed API action."""
    execute_insert(
        "INSERT INTO adh_embed_logs "
        "(app_id, user_id, user_name, action, detail, ip_address, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (app_id, user_id, user_name, action, detail, ip_address, _now()),
    )


def list_embed_logs(
    page: int = 1, size: int = 50, app_id: int = 0,
    user_id: str = "", status: str = "",
) -> Tuple[list, int]:
    """List embed logs with filtering."""
    conditions = []
    params = []
    if app_id:
        conditions.append("app_id = %s")
        params.append(app_id)
    if user_id:
        conditions.append("user_id = %s")
        params.append(user_id)
    if status:
        conditions.append("status = %s")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_embed_logs {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT * FROM adh_embed_logs {where} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()

    return rows, total
