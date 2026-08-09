"""RBAC Service — role and permission management.

Manages roles, permissions, and role-permission assignments.
Permissions are stored as resource:action strings (e.g. "users:read", "workspaces:write").
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime

from services.shared.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    return int(_time.time() * 1000)


# ── Role CRUD ───────────────────────────────────────────────────────────

def list_roles() -> list[dict]:
    """List all roles."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, description, is_system, created_at, updated_at "
                    "FROM adh_roles ORDER BY is_system DESC, name"
                )
                return cur.fetchall()
    except Exception as e:
        logger.error("Failed to list roles: %s", e)
        return []


def get_role(role_id: int) -> dict | None:
    """Get role by ID."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, description, is_system, created_at, updated_at "
                    "FROM adh_roles WHERE id = %s",
                    (role_id,),
                )
                return cur.fetchone()
    except Exception as e:
        logger.error("Failed to get role: %s", e)
        return None


def create_role(name: str, description: str = "") -> tuple[bool, str, int]:
    """Create a new role. Returns (success, message, role_id)."""
    try:
        role_id = _ts_id()
        now = _now_str()
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_roles WHERE name = %s", (name,))
                if cur.fetchone():
                    return False, "角色名已存在", 0
                cur.execute(
                    "INSERT INTO adh_roles (id, name, description, is_system, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (role_id, name, description, 0, now, now),
                )
        return True, "创建成功", role_id
    except Exception as e:
        logger.error("Failed to create role: %s", e)
        return False, "创建失败", 0


def update_role(role_id: int, name: str = None, description: str = None) -> tuple[bool, str]:
    """Update a role. Returns (success, message)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, is_system FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if not role:
                    return False, "角色不存在"
                if role["is_system"]:
                    return False, "系统角色不可修改"

                updates = []
                params = []
                if name is not None:
                    cur.execute("SELECT id FROM adh_roles WHERE name = %s AND id != %s", (name, role_id))
                    if cur.fetchone():
                        return False, "角色名已存在"
                    updates.append("name = %s")
                    params.append(name)
                if description is not None:
                    updates.append("description = %s")
                    params.append(description)

                if not updates:
                    return True, "无需更新"

                updates.append("updated_at = %s")
                params.append(_now_str())
                params.append(role_id)

                cur.execute(
                    f"UPDATE adh_roles SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
        return True, "更新成功"
    except Exception as e:
        logger.error("Failed to update role: %s", e)
        return False, "更新失败"


def delete_role(role_id: int) -> tuple[bool, str]:
    """Delete a role. Returns (success, message)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, is_system FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if not role:
                    return False, "角色不存在"
                if role["is_system"]:
                    return False, "系统角色不可删除"

                cur.execute("DELETE FROM adh_role_permissions WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_roles WHERE id = %s", (role_id,))
        return True, "删除成功"
    except Exception as e:
        logger.error("Failed to delete role: %s", e)
        return False, "删除失败"


# ── Permission management ───────────────────────────────────────────────

def get_role_permissions(role_id: int) -> list[str]:
    """Get all permissions for a role. Returns list of permission strings."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT permission FROM adh_role_permissions WHERE role_id = %s ORDER BY permission",
                    (role_id,),
                )
                rows = cur.fetchall()
                return [row["permission"] for row in rows]
    except Exception as e:
        logger.error("Failed to get role permissions: %s", e)
        return []


def set_role_permissions(role_id: int, permissions: list[str]) -> tuple[bool, str]:
    """Replace all permissions for a role. Returns (success, message)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_roles WHERE id = %s", (role_id,))
                if not cur.fetchone():
                    return False, "角色不存在"

                # Clear existing permissions
                cur.execute("DELETE FROM adh_role_permissions WHERE role_id = %s", (role_id,))

                # Insert new permissions
                for perm in permissions:
                    cur.execute(
                        "INSERT INTO adh_role_permissions (id, role_id, permission, created_at) "
                        "VALUES (%s, %s, %s, %s)",
                        (_ts_id(), role_id, perm, _now_str()),
                    )
        return True, "权限更新成功"
    except Exception as e:
        logger.error("Failed to set role permissions: %s", e)
        return False, "权限更新失败"


def add_permission_to_role(role_id: int, permission: str) -> tuple[bool, str]:
    """Add a single permission to a role. Returns (success, message)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM adh_role_permissions WHERE role_id = %s AND permission = %s",
                    (role_id, permission),
                )
                if cur.fetchone():
                    return True, "权限已存在"
                cur.execute(
                    "INSERT INTO adh_role_permissions (id, role_id, permission, created_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (_ts_id(), role_id, permission, _now_str()),
                )
        return True, "权限添加成功"
    except Exception as e:
        logger.error("Failed to add permission to role: %s", e)
        return False, "权限添加失败"


def remove_permission_from_role(role_id: int, permission: str) -> tuple[bool, str]:
    """Remove a single permission from a role. Returns (success, message)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_role_permissions WHERE role_id = %s AND permission = %s",
                    (role_id, permission),
                )
        return True, "权限移除成功"
    except Exception as e:
        logger.error("Failed to remove permission from role: %s", e)
        return False, "权限移除失败"


def check_user_permission(user_id: int, permission: str) -> bool:
    """Check if a user has a specific permission via their role.

    Admin users always have all permissions.
    """
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                # Admin users have all permissions
                cur.execute("SELECT user_role FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False
                if user["user_role"] == "admin":
                    return True

                # Check via user's role
                # Users have a user_role field that maps to adh_roles.name
                cur.execute(
                    "SELECT rp.permission FROM adh_role_permissions rp "
                    "JOIN adh_roles r ON r.id = rp.role_id "
                    "WHERE r.name = %s AND rp.permission = %s",
                    (user["user_role"], permission),
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error("Failed to check user permission: %s", e)
        return False


def get_user_permissions(user_id: int) -> list[str]:
    """Get all permissions for a user via their role."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_role FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return []

                if user["user_role"] == "admin":
                    # Admin has all permissions — return wildcard
                    return ["*"]

                cur.execute(
                    "SELECT rp.permission FROM adh_role_permissions rp "
                    "JOIN adh_roles r ON r.id = rp.role_id "
                    "WHERE r.name = %s ORDER BY rp.permission",
                    (user["user_role"],),
                )
                rows = cur.fetchall()
                return [row["permission"] for row in rows]
    except Exception as e:
        logger.error("Failed to get user permissions: %s", e)
        return []
