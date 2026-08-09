"""Authentication module — JWT validation + bcrypt password verification against adh_users.

Includes:
- JWT token validation (decode_token, get_current_user, require_admin, get_workspace_id)
- User management (CRUD, password policy, login lockout, audit logging)
Sensitive fields (email, phone) are encrypted with AES-256-GCM.
"""
from __future__ import annotations

import logging
import re
import time as _time
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
import jwt
import pymysql
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
    ADH_DEFAULT_ADMIN_PASSWORD, ADH_SECRET_KEY,
)
from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.crypto import encrypt_password, decrypt_password, is_encrypted

logger = logging.getLogger(__name__)

# ── JWT Configuration ──────────────────────────────────────────────────

security = HTTPBearer()

ALGORITHM = "HS256"


# ── JWT Token Validation ──────────────────────────────────────────────

def decode_token(token: str) -> dict:
    """Decode and validate a JWT token.

    Returns:
        dict with user info: {user_id, username, role, workspace_id}
    Raises:
        jwt.InvalidTokenError on invalid token
    """
    payload = jwt.decode(token, ADH_SECRET_KEY, algorithms=[ALGORITHM])

    # Check expiration
    if payload.get("exp", 0) < _time.time():
        raise jwt.ExpiredSignatureError("Token has expired")

    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """FastAPI dependency that extracts and validates the current user from JWT.

    Returns:
        dict with {user_id, username, role}
    """
    try:
        payload = decode_token(credentials.credentials)
        return {
            "user_id": payload.get("user_id"),
            "username": payload.get("username"),
            "role": payload.get("role", "viewer"),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_admin(
    user: dict = Depends(get_current_user),
) -> dict:
    """FastAPI dependency that requires admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_workspace_id(request: Request) -> int:
    """Extract workspace_id from query params or headers."""
    ws_id = request.query_params.get("workspace_id")
    if not ws_id:
        ws_id = request.headers.get("X-Workspace-Id")
    if not ws_id:
        return 0
    try:
        return int(ws_id)
    except (ValueError, TypeError):
        return 0


# ── Ranger Data-Level Authorization ─────────────────────────────────────

def require_datasource_access(
    datasource_id: int = 0,
    database: str = "",
    table: str = "",
    columns: list[str] = [],
    action: str = "select",
):
    """Factory for a FastAPI dependency that checks data-level access via Ranger.

    Usage:
        @router.post("/query")
        async def run_query(
            req: QueryRequest,
            user: dict = Depends(require_datasource_access(
                database="mydb", table="orders", action="select"
            )),
        ):
            ...

    When Ranger is disabled, this is a no-op (always allows).
    """
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        from services.shared.common.config import RANGER_ENABLED
        if not RANGER_ENABLED:
            return user

        try:
            from services.shared.services.ranger_client import ranger_client

            # Get user's LDAP groups for Ranger policy matching
            groups = await _get_user_ranger_groups(user["user_id"])

            result = await ranger_client.check_access(
                user=user["username"],
                groups=groups,
                resource_type="table",
                resource={"database": database, "table": table},
                action=action,
            )

            if not result.allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"无权访问 {database}.{table}: {result.reason}",
                )

            # Attach Ranger context to user dict for downstream use
            user["ranger_result"] = result
            return user
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Ranger check failed, allowing access: %s", e)
            return user

    return _check


async def _get_user_ranger_groups(user_id: int) -> list[str]:
    """Get a user's LDAP group DNs for Ranger policy matching."""
    try:
        from services.authservice.services.ldap_backend import ldap_backend
        return ldap_backend.get_user_groups(user_id)
    except Exception:
        return []


# ── Lightweight Permission Check (no Ranger/LDAP/Kerberos) ────────────

def require_permission(
    datasource_id: int = 0,
    table: str = "",
    workspace_id: int = 0,
):
    """Factory for a FastAPI dependency that checks data-level access via role_service + rls_service.

    Usage:
        @router.post("/query")
        async def run_query(
            req: QueryRequest,
            user: dict = Depends(require_permission(datasource_id=1, table="orders")),
        ):
            ...

    This uses the lightweight permission system (role_service + rls_service).
    No external dependencies (Ranger/LDAP/Kerberos).
    """
    async def _check(
        user: dict = Depends(get_current_user),
        ws_id: int = Depends(get_workspace_id),
    ) -> dict:
        effective_ws = workspace_id or ws_id

        try:
            from services.datamind.permission.enforcer import permission_enforcer

            result = permission_enforcer.check_access(
                user_id=user["user_id"],
                workspace_id=effective_ws,
                datasource_id=datasource_id,
                table_name=table,
            )

            if not result.allowed:
                raise HTTPException(
                    status_code=403,
                    detail=result.reason,
                )

            # Attach permission context for downstream use
            user["permission_result"] = result
            user["workspace_id"] = effective_ws
            return user
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Permission check failed, allowing access: %s", e)
            return user

    return _check


# ── Security constants ──────────────────────────────────────────────────
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 8


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


# ── Sensitive field encryption helpers ──────────────────────────────────

def _encrypt_field(value: str) -> str:
    """Encrypt a sensitive field (email, phone). Returns empty string for empty input."""
    if not value:
        return ""
    try:
        return encrypt_password(value)
    except Exception as e:
        logger.warning("Failed to encrypt field: %s", e)
        return value  # Fallback to plaintext on error


def _decrypt_field(value: str) -> str:
    """Decrypt a sensitive field. Returns empty string for empty input."""
    if not value:
        return ""
    try:
        if is_encrypted(value):
            return decrypt_password(value)
        return value  # Not encrypted (legacy data), return as-is
    except Exception as e:
        logger.warning("Failed to decrypt field: %s", e)
        return "***"  # Return masked value on error


def _decrypt_user_row(row: dict) -> dict:
    """Decrypt sensitive fields in a user row."""
    if row:
        row["email"] = _decrypt_field(row.get("email", ""))
        row["phone"] = _decrypt_field(row.get("phone", ""))
    return row


def _decrypt_user_rows(rows: list) -> list:
    """Decrypt sensitive fields in a list of user rows."""
    return [_decrypt_user_row(row) for row in rows]


# ── Password policy ─────────────────────────────────────────────────────

def validate_password_strength(password: str) -> tuple[bool, str]:
    """Check password meets minimum requirements. Returns (valid, message)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"密码长度不能少于{MIN_PASSWORD_LENGTH}个字符"
    if not re.search(r"[a-zA-Z]", password):
        return False, "密码必须包含字母"
    if not re.search(r"\d", password):
        return False, "密码必须包含数字"
    return True, ""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, pw_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))


# ── Audit logging ───────────────────────────────────────────────────────

def log_audit(user_id: int, username: str, action: str,
              target_type: str = "", target_id: int = 0,
              detail: str = "", ip_address: str = "", module: str = ""):
    """Write an audit log entry."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_audit_logs "
                    "(id, user_id, username, action, target_type, target_id, detail, ip_address, module, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (_ts_id(), user_id, username, action, target_type, target_id,
                     detail, ip_address, module, _now_str()),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to write audit log: %s", e)


# ── Ensure admin user ───────────────────────────────────────────────────

def _ensure_admin_user():
    """Create default admin user if no users exist."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_users")
                row = cur.fetchone()
                if row and row["cnt"] > 0:
                    return

                pw_hash = hash_password(ADH_DEFAULT_ADMIN_PASSWORD)
                now = _now_str()
                cur.execute(
                    "INSERT INTO adh_users "
                    "(id, username, password_hash, email, phone, avatar, user_role, status, "
                    "login_attempts, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (_ts_id(), "admin", pw_hash, "", "", "", "admin", "active", 0, now, now),
                )
            conn.commit()
            logger.info("Default admin user created.")
    except Exception as e:
        logger.warning("Failed to ensure admin user: %s", e)


# ── Authentication ──────────────────────────────────────────────────────

def authenticate(username: str, password: str, ip_address: str = "") -> dict | None:
    """Verify credentials and return user dict or None.

    Handles login-attempt counting and account lockout.
    """
    _ensure_admin_user()
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, password_hash, user_role, status, "
                    "login_attempts, locked_until "
                    "FROM adh_users WHERE username = %s",
                    (username,),
                )
                user = cur.fetchone()

        if not user:
            return None

        # Check account status
        if user["status"] == "disabled":
            return {"error": "disabled", "message": "账号已被禁用，请联系管理员"}

        # Check lockout
        if user.get("locked_until"):
            locked_until = user["locked_until"]
            if isinstance(locked_until, str):
                locked_until = datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
            if datetime.now() < locked_until:
                remaining = int((locked_until - datetime.now()).total_seconds() / 60) + 1
                return {"error": "locked", "message": f"账号已锁定，请{remaining}分钟后重试"}
            else:
                # Lockout expired, reset attempts
                _reset_login_attempts(user["id"])

        # Check password
        if not check_password(password, user["password_hash"]):
            _increment_login_attempts(user["id"], username)
            return None

        # Success — reset attempts and update last_login
        _reset_login_attempts(user["id"])
        _update_last_login(user["id"])

        log_audit(user["id"], username, "login", detail="登录成功", ip_address=ip_address)

        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["user_role"],
            "status": user["status"],
        }
    except Exception as e:
        logger.error("Authentication error: %s", e)
        return None


def _increment_login_attempts(user_id: int, username: str):
    """Increment login attempts and lock account if threshold reached."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_users SET login_attempts = login_attempts + 1, updated_at = %s WHERE id = %s",
                    (_now_str(), user_id),
                )
                cur.execute("SELECT login_attempts FROM adh_users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if row and row["login_attempts"] >= MAX_LOGIN_ATTEMPTS:
                    locked_until = (datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                    cur.execute(
                        "UPDATE adh_users SET locked_until = %s, updated_at = %s WHERE id = %s",
                        (locked_until, _now_str(), user_id),
                    )
                    log_audit(user_id, username, "account_locked",
                              detail=f"连续{MAX_LOGIN_ATTEMPTS}次登录失败，锁定{LOCKOUT_MINUTES}分钟")
            conn.commit()
    except Exception as e:
        logger.warning("Failed to increment login attempts: %s", e)


def _reset_login_attempts(user_id: int):
    """Reset login attempts and clear lockout."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_users SET login_attempts = 0, locked_until = NULL, updated_at = %s WHERE id = %s",
                    (_now_str(), user_id),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to reset login attempts: %s", e)


def _update_last_login(user_id: int):
    """Update last login timestamp."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_users SET last_login = %s, updated_at = %s WHERE id = %s",
                    (_now_str(), _now_str(), user_id),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to update last login: %s", e)


# ── User CRUD ───────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> dict | None:
    """Get user by ID (without password hash). Sensitive fields are decrypted."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, phone, avatar, user_role, status, "
                    "last_login, login_attempts, locked_until, created_at, updated_at "
                    "FROM adh_users WHERE id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                return _decrypt_user_row(row)
    except Exception as e:
        logger.error("Failed to get user: %s", e)
        return None


def list_users(page: int = 1, size: int = 50, search: str = "", role: str = "") -> tuple[list[dict], int]:
    """List users with pagination and filtering. Returns (items, total).

    Note: Search on encrypted fields (email, phone) won't work with encrypted storage.
    Only username search is supported for encrypted fields.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []
                if search:
                    # Can only search username directly; email/phone are encrypted
                    where_clauses.append("username LIKE %s")
                    like = f"%{search}%"
                    params.append(like)
                if role:
                    where_clauses.append("user_role = %s")
                    params.append(role)

                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

                # Count
                cur.execute(f"SELECT COUNT(*) AS cnt FROM adh_users{where_sql}", params)
                total = cur.fetchone()["cnt"]

                # Fetch page
                offset = (page - 1) * size
                cur.execute(
                    f"SELECT id, username, email, phone, avatar, user_role, status, "
                    f"last_login, login_attempts, locked_until, created_at, updated_at "
                    f"FROM adh_users{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                items = cur.fetchall()
                return _decrypt_user_rows(items), total
    except Exception as e:
        logger.error("Failed to list users: %s", e)
        return [], 0


def create_user(username: str, password: str, role: str = "viewer",
                email: str = "", phone: str = "") -> tuple[bool, str, int]:
    """Create a new user. Returns (success, message, user_id).

    Email and phone are encrypted before storage.
    """
    valid, msg = validate_password_strength(password)
    if not valid:
        return False, msg, 0

    try:
        pw_hash = hash_password(password)
        user_id = _ts_id()
        now = _now_str()
        enc_email = _encrypt_field(email)
        enc_phone = _encrypt_field(phone)

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_users WHERE username = %s", (username,))
                if cur.fetchone():
                    return False, "用户名已存在", 0
                if enc_email:
                    # Check uniqueness by comparing encrypted values
                    cur.execute("SELECT id FROM adh_users WHERE email = %s AND email != ''", (enc_email,))
                    if cur.fetchone():
                        return False, "邮箱已被使用", 0
                cur.execute(
                    "INSERT INTO adh_users "
                    "(id, username, password_hash, email, phone, avatar, user_role, status, "
                    "login_attempts, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (user_id, username, pw_hash, enc_email, enc_phone, "", role, "active", 0, now, now),
                )
            conn.commit()
        return True, "创建成功", user_id
    except Exception as e:
        logger.error("Failed to create user: %s", e)
        return False, "创建失败", 0


def update_user(user_id: int, username: str = None, email: str = None,
                phone: str = None, role: str = None) -> tuple[bool, str]:
    """Update user info. Returns (success, message).

    Email and phone are encrypted before storage.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                # Check user exists
                cur.execute("SELECT id, username FROM adh_users WHERE id = %s", (user_id,))
                existing = cur.fetchone()
                if not existing:
                    return False, "用户不存在"

                updates = []
                params = []
                if username is not None and username != existing["username"]:
                    cur.execute("SELECT id FROM adh_users WHERE username = %s AND id != %s", (username, user_id))
                    if cur.fetchone():
                        return False, "用户名已存在"
                    updates.append("username = %s")
                    params.append(username)
                if email is not None:
                    enc_email = _encrypt_field(email)
                    if enc_email:
                        cur.execute("SELECT id FROM adh_users WHERE email = %s AND id != %s AND email != ''",
                                    (enc_email, user_id))
                        if cur.fetchone():
                            return False, "邮箱已被使用"
                    updates.append("email = %s")
                    params.append(enc_email)
                if phone is not None:
                    enc_phone = _encrypt_field(phone)
                    updates.append("phone = %s")
                    params.append(enc_phone)
                if role is not None:
                    updates.append("user_role = %s")
                    params.append(role)

                if not updates:
                    return True, "无需更新"

                updates.append("updated_at = %s")
                params.append(_now_str())
                params.append(user_id)

                cur.execute(
                    f"UPDATE adh_users SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
        return True, "更新成功"
    except Exception as e:
        logger.error("Failed to update user: %s", e)
        return False, "更新失败"


def update_user_status(user_id: int, status: str) -> tuple[bool, str]:
    """Enable or disable a user. Returns (success, message)."""
    if status not in ("active", "disabled"):
        return False, "无效的状态值"
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_users WHERE id = %s", (user_id,))
                if not cur.fetchone():
                    return False, "用户不存在"
                cur.execute(
                    "UPDATE adh_users SET status = %s, login_attempts = 0, locked_until = NULL, updated_at = %s WHERE id = %s",
                    (status, _now_str(), user_id),
                )
            conn.commit()
        return True, "状态已更新"
    except Exception as e:
        logger.error("Failed to update user status: %s", e)
        return False, "更新失败"


def change_password(user_id: int, old_password: str, new_password: str) -> tuple[bool, str]:
    """Change user's own password. Returns (success, message)."""
    valid, msg = validate_password_strength(new_password)
    if not valid:
        return False, msg

    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password_hash FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False, "用户不存在"
                if not check_password(old_password, user["password_hash"]):
                    return False, "旧密码不正确"
                new_hash = hash_password(new_password)
                cur.execute(
                    "UPDATE adh_users SET password_hash = %s, updated_at = %s WHERE id = %s",
                    (new_hash, _now_str(), user_id),
                )
            conn.commit()
        return True, "密码修改成功"
    except Exception as e:
        logger.error("Failed to change password: %s", e)
        return False, "密码修改失败"


def reset_password(user_id: int, new_password: str) -> tuple[bool, str]:
    """Admin reset user password. Returns (success, message)."""
    valid, msg = validate_password_strength(new_password)
    if not valid:
        return False, msg

    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_users WHERE id = %s", (user_id,))
                if not cur.fetchone():
                    return False, "用户不存在"
                new_hash = hash_password(new_password)
                cur.execute(
                    "UPDATE adh_users SET password_hash = %s, login_attempts = 0, "
                    "locked_until = NULL, updated_at = %s WHERE id = %s",
                    (new_hash, _now_str(), user_id),
                )
            conn.commit()
        return True, "密码重置成功"
    except Exception as e:
        logger.error("Failed to reset password: %s", e)
        return False, "密码重置失败"


def delete_user(user_id: int) -> tuple[bool, str]:
    """Delete a user by ID. Returns (success, message)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False, "用户不存在"
                cur.execute("DELETE FROM adh_users WHERE id = %s", (user_id,))
            conn.commit()
        return True, "删除成功"
    except Exception as e:
        logger.error("Failed to delete user: %s", e)
        return False, "删除失败"


# ── Audit logs ──────────────────────────────────────────────────────────

def list_audit_logs(page: int = 1, size: int = 50, user_id: int = 0,
                    action: str = "", module: str = "") -> tuple[list[dict], int]:
    """List audit logs with pagination. Returns (items, total)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []
                if user_id:
                    where_clauses.append("user_id = %s")
                    params.append(user_id)
                if action:
                    where_clauses.append("action = %s")
                    params.append(action)
                if module:
                    where_clauses.append("module = %s")
                    params.append(module)

                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

                cur.execute(f"SELECT COUNT(*) AS cnt FROM adh_audit_logs{where_sql}", params)
                total = cur.fetchone()["cnt"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT id, user_id, username, action, target_type, target_id, "
                    f"detail, ip_address, module, created_at "
                    f"FROM adh_audit_logs{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                return cur.fetchall(), total
    except Exception as e:
        logger.error("Failed to list audit logs: %s", e)
        return [], 0
