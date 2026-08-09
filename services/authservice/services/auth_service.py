"""Auth Service — business logic for authentication, user management, and audit.

Uses bcrypt for password hashing, JWT for tokens, and the shared DB pool.
Sensitive fields (email, phone) are encrypted with AES-256-GCM.
"""
from __future__ import annotations

import logging
import time as _time
import re
from datetime import datetime, timedelta

import bcrypt
import jwt

from services.shared.common.config import ADH_SECRET_KEY, LDAP_ENABLED, RANGER_ENABLED
from services.shared.common.db import DBConnection, execute_query, execute_write
from services.shared.common.crypto import encrypt_password, decrypt_password, is_encrypted

logger = logging.getLogger(__name__)

# ── Security constants ──────────────────────────────────────────────────
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
REFRESH_TOKEN_EXPIRE_DAYS = 7
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 8


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


# ── Password utilities ──────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, pw_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Check password meets minimum requirements. Returns (valid, message)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"密码长度不能少于{MIN_PASSWORD_LENGTH}个字符"
    if not re.search(r"[a-zA-Z]", password):
        return False, "密码必须包含字母"
    if not re.search(r"\d", password):
        return False, "密码必须包含数字"
    return True, ""


# ── JWT token utilities ─────────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    """Create a JWT access token (24h expiry)."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, ADH_SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token (7d expiry)."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, ADH_SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    return jwt.decode(token, ADH_SECRET_KEY, algorithms=[ALGORITHM])


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    return int(_time.time() * 1000)


# ── Authentication ──────────────────────────────────────────────────────

def login(username: str, password: str, ip_address: str = "") -> dict | None:
    """Authenticate user and return token pair, or None on failure.

    Authentication flow:
    1. If LDAP is enabled, try LDAP authentication first
    2. On LDAP success, sync user to local DB (lazy provisioning)
    3. On LDAP failure or if LDAP is disabled, fall back to local auth

    Returns dict with access_token, refresh_token, user info — or None.
    """
    _ensure_admin_user()

    # ── LDAP authentication (if enabled) ──
    if LDAP_ENABLED:
        ldap_result = _try_ldap_login(username, password, ip_address)
        if ldap_result is not None:
            return ldap_result
        # LDAP returned None → fall through to local auth

    # ── Local authentication ──
    return _local_login(username, password, ip_address)


def _try_ldap_login(username: str, password: str, ip_address: str) -> dict | None:
    """Attempt LDAP authentication. Returns token dict or None on failure."""
    try:
        from services.authservice.services.ldap_backend import ldap_backend

        ldap_user = ldap_backend.authenticate(username, password)
        if ldap_user is None:
            return None  # LDAP auth failed, fall through to local

        # LDAP auth succeeded — sync user to local DB
        local_user = ldap_backend.sync_user_to_local(ldap_user)

        log_audit(
            local_user["id"], username, "login",
            detail="LDAP登录成功", ip_address=ip_address, module="ldap",
        )

        token_data = {
            "sub": str(local_user["id"]),
            "user_id": local_user["id"],
            "username": local_user["username"],
            "role": local_user["role"],
        }
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "id": local_user["id"],
                "username": local_user["username"],
                "role": local_user["role"],
            },
            "auth_source": "ldap",
        }
    except ImportError:
        logger.warning("ldap3 not installed, skipping LDAP authentication")
        return None
    except Exception as e:
        logger.error("LDAP authentication error for %s: %s", username, e)
        return None


def _local_login(username: str, password: str, ip_address: str) -> dict | None:
    """Local (bcrypt) authentication against adh_users table."""
    try:
        with DBConnection() as conn:
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
                _reset_login_attempts(user["id"])

        # Check password
        if not verify_password(password, user["password_hash"]):
            _increment_login_attempts(user["id"], username)
            return None

        # Success — reset attempts and update last_login
        _reset_login_attempts(user["id"])
        _update_last_login(user["id"])

        log_audit(user["id"], username, "login", detail="登录成功", ip_address=ip_address)

        token_data = {
            "sub": str(user["id"]),
            "user_id": user["id"],
            "username": user["username"],
            "role": user["user_role"],
        }
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["user_role"],
            },
        }
    except Exception as e:
        logger.error("Local authentication error: %s", e)
        return None


def refresh_access_token(refresh_token: str) -> dict | None:
    """Exchange a refresh token for a new token pair.

    Returns dict with access_token and refresh_token, or None on failure.
    """
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            return None
        uid = payload.get("sub")
        if uid is None:
            return None

        # Verify user still exists and is active
        user = get_user_by_id(int(uid))
        if not user or user.get("status") == "disabled":
            return None

        token_data = {
            "sub": uid,
            "user_id": int(uid),
            "username": payload.get("username"),
            "role": payload.get("role"),
        }
        new_access = create_access_token(token_data)
        new_refresh = create_refresh_token(token_data)
        return {"access_token": new_access, "refresh_token": new_refresh}
    except jwt.InvalidTokenError:
        return None


# ── Login attempt helpers ───────────────────────────────────────────────

def _increment_login_attempts(user_id: int, username: str):
    """Increment login attempts and lock account if threshold reached."""
    try:
        with DBConnection() as conn:
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
    except Exception as e:
        logger.warning("Failed to increment login attempts: %s", e)


def _reset_login_attempts(user_id: int):
    """Reset login attempts and clear lockout."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_users SET login_attempts = 0, locked_until = NULL, updated_at = %s WHERE id = %s",
                    (_now_str(), user_id),
                )
    except Exception as e:
        logger.warning("Failed to reset login attempts: %s", e)


def _update_last_login(user_id: int):
    """Update last login timestamp."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_users SET last_login = %s, updated_at = %s WHERE id = %s",
                    (_now_str(), _now_str(), user_id),
                )
    except Exception as e:
        logger.warning("Failed to update last login: %s", e)


# ── Ensure admin user ───────────────────────────────────────────────────

def _ensure_admin_user():
    """Create default admin user if no users exist."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_users")
                row = cur.fetchone()
                if row and row["cnt"] > 0:
                    return

                pw_hash = hash_password("admin123")
                now = _now_str()
                cur.execute(
                    "INSERT INTO adh_users "
                    "(id, username, password_hash, email, phone, avatar, user_role, status, "
                    "login_attempts, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (_ts_id(), "admin", pw_hash, "", "", "", "admin", "active", 0, now, now),
                )
                logger.info("Default admin user created.")
    except Exception as e:
        logger.warning("Failed to ensure admin user: %s", e)


# ── User CRUD ───────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> dict | None:
    """Get user by ID (without password hash). Sensitive fields are decrypted."""
    try:
        with DBConnection() as conn:
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


def get_user_by_username(username: str) -> dict | None:
    """Get user by username. Sensitive fields are decrypted."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, phone, avatar, user_role, status, "
                    "last_login, login_attempts, locked_until, created_at, updated_at "
                    "FROM adh_users WHERE username = %s",
                    (username,),
                )
                row = cur.fetchone()
                return _decrypt_user_row(row)
    except Exception as e:
        logger.error("Failed to get user by username: %s", e)
        return None


def list_users(page: int = 1, size: int = 50, search: str = "", role: str = "") -> tuple[list[dict], int]:
    """List users with pagination and filtering. Returns (items, total).

    Note: Search on encrypted fields (email, phone) won't work with encrypted storage.
    Only username search is supported for encrypted fields.
    """
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []
                if search:
                    # Can only search username directly; email/phone are encrypted
                    where_clauses.append("username LIKE %s")
                    params.append(f"%{search}%")
                if role:
                    where_clauses.append("user_role = %s")
                    params.append(role)

                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

                cur.execute(f"SELECT COUNT(*) AS cnt FROM adh_users{where_sql}", params)
                total = cur.fetchone()["cnt"]

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

        with DBConnection() as conn:
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

                # Auto-add to default workspace
                cur.execute("SELECT id FROM adh_workspaces WHERE is_default = 1 LIMIT 1")
                default_ws = cur.fetchone()
                if default_ws:
                    cur.execute(
                        """INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                           VALUES (%s, %s, 'member', 1)""",
                        (default_ws["id"], user_id),
                    )

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
        with DBConnection() as conn:
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
        return True, "更新成功"
    except Exception as e:
        logger.error("Failed to update user: %s", e)
        return False, "更新失败"


def update_user_status(user_id: int, status: str) -> tuple[bool, str]:
    """Enable or disable a user. Returns (success, message)."""
    if status not in ("active", "disabled"):
        return False, "无效的状态值"
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_users WHERE id = %s", (user_id,))
                if not cur.fetchone():
                    return False, "用户不存在"
                cur.execute(
                    "UPDATE adh_users SET status = %s, login_attempts = 0, locked_until = NULL, updated_at = %s WHERE id = %s",
                    (status, _now_str(), user_id),
                )
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
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password_hash FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False, "用户不存在"
                if not verify_password(old_password, user["password_hash"]):
                    return False, "旧密码不正确"
                new_hash = hash_password(new_password)
                cur.execute(
                    "UPDATE adh_users SET password_hash = %s, updated_at = %s WHERE id = %s",
                    (new_hash, _now_str(), user_id),
                )
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
        with DBConnection() as conn:
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
        return True, "密码重置成功"
    except Exception as e:
        logger.error("Failed to reset password: %s", e)
        return False, "密码重置失败"


def delete_user(user_id: int) -> tuple[bool, str]:
    """Delete a user by ID. Returns (success, message)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False, "用户不存在"
                cur.execute("DELETE FROM adh_users WHERE id = %s", (user_id,))
        return True, "删除成功"
    except Exception as e:
        logger.error("Failed to delete user: %s", e)
        return False, "删除失败"


# ── Audit logging ───────────────────────────────────────────────────────

def log_audit(user_id: int, username: str, action: str,
              target_type: str = "", target_id: int = 0,
              detail: str = "", ip_address: str = "", module: str = ""):
    """Write an audit log entry."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_audit_logs "
                    "(id, user_id, username, action, target_type, target_id, detail, ip_address, module, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (_ts_id(), user_id, username, action, target_type, target_id,
                     detail, ip_address, module, _now_str()),
                )
    except Exception as e:
        logger.warning("Failed to write audit log: %s", e)


def list_audit_logs(page: int = 1, size: int = 50, user_id: int = 0,
                    action: str = "") -> tuple[list[dict], int]:
    """List audit logs with pagination. Returns (items, total)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []
                if user_id:
                    where_clauses.append("user_id = %s")
                    params.append(user_id)
                if action:
                    where_clauses.append("action = %s")
                    params.append(action)

                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

                cur.execute(f"SELECT COUNT(*) AS cnt FROM adh_audit_logs{where_sql}", params)
                total = cur.fetchone()["cnt"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT id, user_id, username, action, target_type, target_id, "
                    f"detail, ip_address, created_at "
                    f"FROM adh_audit_logs{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                return cur.fetchall(), total
    except Exception as e:
        logger.error("Failed to list audit logs: %s", e)
        return [], 0
