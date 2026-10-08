"""Embed authentication — API Key verification and embed token management."""
from __future__ import annotations

import json
import logging
import secrets
import time as _time
from datetime import datetime, timedelta
from contextlib import contextmanager

import bcrypt
import pymysql
from jose import jwt

from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD,
    METADATA_DB_DATABASE, ADH_SECRET_KEY,
)
from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

EMBED_TOKEN_EXPIRE_MINUTES = 30
EMBED_TOKEN_REFRESH_THRESHOLD_MINUTES = 5
ALGORITHM = "HS256"


@contextmanager
def _get_connection():
    conn = get_metadata_conn()
    try:
        yield conn
    finally:
        conn.close()


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id():
    return int(_time.time() * 1000)


# API Key functions
def generate_api_key() -> str:
    """Generate a new API key with ck_ prefix."""
    return "ck_" + secrets.token_hex(16)

def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage."""
    return bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_api_key(api_key: str, key_hash: str) -> bool:
    """Verify an API key against its hash."""
    return bcrypt.checkpw(api_key.encode("utf-8"), key_hash.encode("utf-8"))


# Embed Token functions
def create_embed_token(user_id: str, app_id: int) -> tuple[str, datetime]:
    """Create an embed JWT token. Returns (token, expires_at)."""
    expires_at = datetime.utcnow() + timedelta(minutes=EMBED_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "app_id": app_id,
        "type": "embed",
        "exp": expires_at,
    }
    token = jwt.encode(payload, ADH_SECRET_KEY, algorithm=ALGORITHM)
    return token, expires_at

def decode_embed_token(token: str) -> dict | None:
    """Decode and validate an embed token. Returns payload or None."""
    try:
        payload = jwt.decode(token, ADH_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "embed":
            return None
        return payload
    except Exception:
        return None

def should_refresh_token(token: str) -> bool:
    """Check if token should be refreshed (within threshold of expiry)."""
    try:
        payload = jwt.decode(token, ADH_SECRET_KEY, algorithms=[ALGORITHM])
        exp = datetime.utcfromtimestamp(payload["exp"])
        remaining = (exp - datetime.utcnow()).total_seconds() / 60
        return remaining < EMBED_TOKEN_REFRESH_THRESHOLD_MINUTES
    except Exception:
        return True


# Application CRUD
def get_app_by_id(app_id: int) -> dict | None:
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_applications WHERE id = %s", (app_id,))
                return cur.fetchone()
    except Exception as e:
        logger.error("Failed to get app: %s", e)
        return None

def find_app_by_api_key(api_key: str) -> dict | None:
    """Find an active application by verifying the API key against all active apps."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, app_key_hash, status, enable_chat, allowed_dashboards, "
                    "allowed_tables, rate_limit, description "
                    "FROM adh_applications WHERE status = 'active'"
                )
                apps = cur.fetchall()
        for app in apps:
            if verify_api_key(api_key, app["app_key_hash"]):
                return app
        return None
    except Exception as e:
        logger.error("Failed to find app by key: %s", e)
        return None

def create_application(
    name: str, description: str, enable_chat: bool,
    allowed_dashboards: list | None, allowed_tables: list | None,
    rate_limit: int, created_by: int,
) -> tuple[bool, str, int, str]:
    """Create a new application. Returns (success, message, app_id, api_key)."""
    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)
    app_id = _ts_id()
    now = _now_str()
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_applications "
                    "(id, name, app_key_hash, status, enable_chat, allowed_dashboards, allowed_tables, "
                    "rate_limit, description, created_by, created_at, updated_at) "
                    "VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        app_id, name, key_hash, 1 if enable_chat else 0,
                        json.dumps(allowed_dashboards) if allowed_dashboards else None,
                        json.dumps(allowed_tables) if allowed_tables else None,
                        rate_limit, description, created_by, now, now,
                    ),
                )
            conn.commit()
        return True, "创建成功", app_id, api_key
    except Exception as e:
        logger.error("Failed to create application: %s", e)
        return False, "创建失败", 0, ""

def update_application(
    app_id: int, name: str = None, description: str = None,
    enable_chat: bool = None, allowed_dashboards: list = None,
    allowed_tables: list = None, rate_limit: int = None, status: str = None,
) -> tuple[bool, str]:
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_applications WHERE id = %s", (app_id,))
                if not cur.fetchone():
                    return False, "应用不存在"
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
                    params.append(json.dumps(allowed_dashboards) if allowed_dashboards else None)
                if allowed_tables is not None:
                    updates.append("allowed_tables = %s")
                    params.append(json.dumps(allowed_tables) if allowed_tables else None)
                if rate_limit is not None:
                    updates.append("rate_limit = %s")
                    params.append(rate_limit)
                if status is not None:
                    updates.append("status = %s")
                    params.append(status)
                if not updates:
                    return True, "无需更新"
                updates.append("updated_at = %s")
                params.append(_now_str())
                params.append(app_id)
                cur.execute(
                    f"UPDATE adh_applications SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
        return True, "更新成功"
    except Exception as e:
        logger.error("Failed to update application: %s", e)
        return False, "更新失败"

def delete_application(app_id: int) -> tuple[bool, str]:
    """Delete an application by ID."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_applications WHERE id = %s", (app_id,))
                if not cur.fetchone():
                    return False, "应用不存在"
                cur.execute("DELETE FROM adh_applications WHERE id = %s", (app_id,))
            conn.commit()
        return True, "删除成功"
    except Exception as e:
        logger.error("Failed to delete application: %s", e)
        return False, "删除失败"

def rotate_api_key(app_id: int) -> tuple[bool, str, str]:
    new_key = generate_api_key()
    new_hash = hash_api_key(new_key)
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_applications SET app_key_hash = %s, updated_at = %s WHERE id = %s",
                    (new_hash, _now_str(), app_id),
                )
            conn.commit()
        return True, "Key已轮换", new_key
    except Exception as e:
        logger.error("Failed to rotate key: %s", e)
        return False, "轮换失败", ""

def list_applications(page: int = 1, size: int = 50, search: str = "") -> tuple[list[dict], int]:
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []
                if search:
                    where_clauses.append("name LIKE %s")
                    params.append(f"%{search}%")
                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                cur.execute(f"SELECT COUNT(*) AS cnt FROM adh_applications{where_sql}", params)
                total = cur.fetchone()["cnt"]
                offset = (page - 1) * size
                cur.execute(
                    f"SELECT id, name, status, enable_chat, allowed_dashboards, allowed_tables, "
                    f"rate_limit, description, last_used_at, created_by, created_at, updated_at "
                    f"FROM adh_applications{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                items = cur.fetchall()
                return items, total
    except Exception as e:
        logger.error("Failed to list applications: %s", e)
        return [], 0

def update_last_used(app_id: int):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_applications SET last_used_at = %s WHERE id = %s",
                    (_now_str(), app_id),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to update last_used: %s", e)


# Embed Logs
def log_embed_action(
    app_id: int, user_id: str, user_name: str, action: str,
    detail: str = "", ip_address: str = "", status: str = "success",
    error_message: str = "",
):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_embed_logs "
                    "(id, app_id, user_id, user_name, action, detail, ip_address, "
                    "status, error_message, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        _ts_id(), app_id, user_id, user_name, action,
                        detail, ip_address, status, error_message, _now_str(),
                    ),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to write embed log: %s", e)

def list_embed_logs(
    page: int = 1, size: int = 50, app_id: int = 0,
    user_id: str = "", status: str = "",
) -> tuple[list[dict], int]:
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []
                if app_id:
                    where_clauses.append("app_id = %s")
                    params.append(app_id)
                if user_id:
                    where_clauses.append("user_id = %s")
                    params.append(user_id)
                if status:
                    where_clauses.append("status = %s")
                    params.append(status)
                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                cur.execute(f"SELECT COUNT(*) AS cnt FROM adh_embed_logs{where_sql}", params)
                total = cur.fetchone()["cnt"]
                offset = (page - 1) * size
                cur.execute(
                    f"SELECT id, app_id, user_id, user_name, action, detail, "
                    f"ip_address, status, error_message, created_at "
                    f"FROM adh_embed_logs{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                return cur.fetchall(), total
    except Exception as e:
        logger.error("Failed to list embed logs: %s", e)
        return [], 0
