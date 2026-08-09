"""Role-Based Access Control Service.

Manages roles, role attributes (data scope), user-role assignments,
and workspace-role associations.

Permission model:
  Role → defines data scope (RLS attributes like region=cn)
  User → assigned to one or more roles
  Workspace → authorized to roles (not individual users)
  RLS Policy → uses role attributes for row filtering
"""

import logging
import time
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _gen_id():
    return int(time.time() * 1000000)


class RoleService:
    """Role and permission management service."""

    # ── Role CRUD ──────────────────────────────────────────────────

    def list_roles(self, workspace_id: int = None) -> list:
        """List all roles. If workspace_id given, mark which roles are assigned."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_roles WHERE is_active = 1 ORDER BY is_system DESC, name")
                roles = cur.fetchall()
                if workspace_id:
                    cur.execute(
                        "SELECT role_id FROM adh_workspace_roles WHERE workspace_id = %s",
                        (workspace_id,)
                    )
                    assigned = {r["role_id"] for r in cur.fetchall()}
                    for role in roles:
                        role["in_workspace"] = role["id"] in assigned
                return roles
        finally:
            conn.close()

    def get_role(self, role_id: int) -> Optional[dict]:
        """Get a single role with its attributes."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if role:
                    cur.execute(
                        "SELECT * FROM adh_role_attributes WHERE role_id = %s ORDER BY workspace_id, attr_key",
                        (role_id,)
                    )
                    role["attributes"] = cur.fetchall()
                    cur.execute(
                        "SELECT u.id as user_id, u.username FROM adh_user_roles ur JOIN adh_users u ON u.id = ur.user_id WHERE ur.role_id = %s",
                        (role_id,)
                    )
                    role["users"] = cur.fetchall()
                return role
        finally:
            conn.close()

    def create_role(self, name: str, display_name: str, description: str = "") -> int:
        """Create a new role."""
        role_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_roles (id, name, display_name, description) VALUES (%s, %s, %s, %s)",
                    (role_id, name, display_name, description)
                )
                conn.commit()
                return role_id
        finally:
            conn.close()

    def update_role(self, role_id: int, data: dict) -> bool:
        """Update role metadata."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["display_name", "description", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(role_id)
                cur.execute(f"UPDATE adh_roles SET {', '.join(fields)} WHERE id = %s", params)
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_role(self, role_id: int) -> bool:
        """Delete a role (only if not system role)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT is_system FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if not role or role.get("is_system"):
                    return False
                cur.execute("DELETE FROM adh_role_attributes WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_user_roles WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_workspace_roles WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_roles WHERE id = %s", (role_id,))
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Role Attributes (Data Scope) ───────────────────────────────

    def get_role_attributes(self, role_id: int, workspace_id: int = None) -> dict:
        """Get role attributes as a dict {attr_key: attr_value}."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id is not None:
                    cur.execute(
                        "SELECT attr_key, attr_value FROM adh_role_attributes WHERE role_id = %s AND workspace_id = %s",
                        (role_id, workspace_id)
                    )
                else:
                    cur.execute(
                        "SELECT attr_key, attr_value FROM adh_role_attributes WHERE role_id = %s",
                        (role_id,)
                    )
                return {r["attr_key"]: r["attr_value"] for r in cur.fetchall()}
        finally:
            conn.close()

    def set_role_attributes(self, role_id: int, workspace_id: int, attrs: dict) -> bool:
        """Set role attributes for a workspace (replace all)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_role_attributes WHERE role_id = %s AND workspace_id = %s",
                    (role_id, workspace_id)
                )
                for key, value in attrs.items():
                    cur.execute(
                        "INSERT INTO adh_role_attributes (id, role_id, workspace_id, attr_key, attr_value) VALUES (%s, %s, %s, %s, %s)",
                        (_gen_id(), role_id, workspace_id, key, str(value))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    # ── User-Role Assignment ───────────────────────────────────────

    def assign_user_role(self, user_id: int, role_id: int, workspace_id: int = 0) -> bool:
        """Assign a role to a user."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT IGNORE INTO adh_user_roles (id, user_id, role_id, workspace_id) VALUES (%s, %s, %s, %s)",
                    (_gen_id(), user_id, role_id, workspace_id)
                )
                conn.commit()
                return True
        finally:
            conn.close()

    def remove_user_role(self, user_id: int, role_id: int, workspace_id: int = 0) -> bool:
        """Remove a role from a user."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_user_roles WHERE user_id = %s AND role_id = %s AND workspace_id = %s",
                    (user_id, role_id, workspace_id)
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def get_user_roles(self, user_id: int, workspace_id: int = None) -> list:
        """Get roles assigned to a user."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id is not None:
                    cur.execute(
                        """SELECT r.* FROM adh_roles r
                           JOIN adh_user_roles ur ON ur.role_id = r.id
                           WHERE ur.user_id = %s AND (ur.workspace_id = %s OR ur.workspace_id = 0) AND r.is_active = 1""",
                        (user_id, workspace_id)
                    )
                else:
                    cur.execute(
                        """SELECT r.* FROM adh_roles r
                           JOIN adh_user_roles ur ON ur.role_id = r.id
                           WHERE ur.user_id = %s AND r.is_active = 1""",
                        (user_id,)
                    )
                return cur.fetchall()
        finally:
            conn.close()

    def get_user_effective_attributes(self, user_id: int, workspace_id: int) -> dict:
        """Get the merged attributes for a user from all their roles.

        Later roles override earlier ones if keys conflict.
        """
        roles = self.get_user_roles(user_id, workspace_id)
        merged = {}
        for role in roles:
            attrs = self.get_role_attributes(role["id"], workspace_id)
            merged.update(attrs)
        return merged

    # ── Workspace-Role Association ─────────────────────────────────

    def authorize_workspace_role(self, workspace_id: int, role_id: int) -> bool:
        """Authorize a role to access a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT IGNORE INTO adh_workspace_roles (id, workspace_id, role_id) VALUES (%s, %s, %s)",
                    (_gen_id(), workspace_id, role_id)
                )
                conn.commit()
                return True
        finally:
            conn.close()

    def revoke_workspace_role(self, workspace_id: int, role_id: int) -> bool:
        """Revoke a role's access to a workspace.

        Also removes workspace-scoped user assignments for this role.
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_workspace_roles WHERE workspace_id = %s AND role_id = %s",
                    (workspace_id, role_id)
                )
                revoked = cur.rowcount > 0
                cur.execute(
                    "DELETE FROM adh_user_roles WHERE role_id = %s AND workspace_id = %s",
                    (role_id, workspace_id)
                )
                conn.commit()
                return revoked
        finally:
            conn.close()

    def get_workspace_roles(self, workspace_id: int) -> list:
        """Get roles authorized for a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT r.* FROM adh_roles r
                       JOIN adh_workspace_roles wr ON wr.role_id = r.id
                       WHERE wr.workspace_id = %s AND r.is_active = 1""",
                    (workspace_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def get_workspace_role_member_counts(self, workspace_id: int) -> dict:
        """Count workspace members holding each role (workspace-scoped or global).

        Returns {role_id: member_count}.
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ur.role_id, COUNT(DISTINCT ur.user_id) AS cnt
                       FROM adh_user_roles ur
                       JOIN adh_workspace_users wu
                         ON wu.user_id = ur.user_id AND wu.workspace_id = %s
                       WHERE ur.workspace_id = %s OR ur.workspace_id = 0
                       GROUP BY ur.role_id""",
                    (workspace_id, workspace_id)
                )
                return {r["role_id"]: r["cnt"] for r in cur.fetchall()}
        finally:
            conn.close()

    def get_workspace_role_users(self, workspace_id: int, role_id: int) -> list:
        """Get workspace members holding a role, with the assignment scope.

        role_scope: workspace = assigned within this workspace, global = workspace_id 0.
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT u.id, u.username, u.email,
                              CASE WHEN ur.workspace_id = 0 THEN 'global' ELSE 'workspace' END AS role_scope
                       FROM adh_user_roles ur
                       JOIN adh_users u ON u.id = ur.user_id
                       JOIN adh_workspace_users wu
                         ON wu.user_id = ur.user_id AND wu.workspace_id = %s
                       WHERE ur.role_id = %s AND (ur.workspace_id = %s OR ur.workspace_id = 0)
                       ORDER BY role_scope, u.username""",
                    (workspace_id, role_id, workspace_id)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def check_user_workspace_access(self, user_id: int, workspace_id: int) -> bool:
        """Check if a user has access to a workspace via any of their roles."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) as cnt FROM adh_user_roles ur
                       JOIN adh_workspace_roles wr ON wr.role_id = ur.role_id AND wr.workspace_id = %s
                       WHERE ur.user_id = %s""",
                    (workspace_id, user_id)
                )
                return cur.fetchone()["cnt"] > 0
        finally:
            conn.close()

    # ── Datasource Access ──────────────────────────────────────────

    def get_role_datasources(self, role_id: int) -> list:
        """Get datasources a role can access."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rda.*, ds.name as datasource_name, ds.db_type
                       FROM adh_role_datasource_access rda
                       LEFT JOIN adh_datasources ds ON ds.id = rda.datasource_id
                       WHERE rda.role_id = %s ORDER BY ds.name""",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_datasources(self, role_id: int, datasource_ids: list) -> bool:
        """Replace all datasource access for a role."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_datasource_access WHERE role_id = %s", (role_id,))
                for ds_id in datasource_ids:
                    cur.execute(
                        "INSERT INTO adh_role_datasource_access (id, role_id, datasource_id) VALUES (%s, %s, %s)",
                        (_gen_id(), role_id, ds_id)
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_allowed_datasources(self, user_id: int, workspace_id: int = 0) -> list:
        """Get all datasource IDs a user can access via their roles.

        Returns list of datasource_ids. Empty list means no restriction (all allowed).
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT rda.datasource_id
                       FROM adh_user_roles ur
                       JOIN adh_role_datasource_access rda ON rda.role_id = ur.role_id
                       WHERE ur.user_id = %s AND (ur.workspace_id = %s OR ur.workspace_id = 0)""",
                    (user_id, workspace_id)
                )
                return [r["datasource_id"] for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Table Access ───────────────────────────────────────────────

    def get_role_tables(self, role_id: int) -> list:
        """Get tables a role can access."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_role_table_access WHERE role_id = %s ORDER BY datasource_id, table_name",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_tables(self, role_id: int, tables: list) -> bool:
        """Replace all table access for a role.

        Each table dict: {datasource_id, table_name, access_type}
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_table_access WHERE role_id = %s", (role_id,))
                for t in tables:
                    cur.execute(
                        """INSERT INTO adh_role_table_access
                           (id, role_id, datasource_id, table_name, access_type)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (_gen_id(), role_id, t.get("datasource_id", 0),
                         t["table_name"], t.get("access_type", "read"))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_allowed_tables(self, user_id: int, datasource_id: int, workspace_id: int = 0) -> list:
        """Get table names a user can access for a specific datasource.

        Returns list of table_names. Empty list means no restriction (all allowed).
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT rta.table_name
                       FROM adh_user_roles ur
                       JOIN adh_role_table_access rta ON rta.role_id = ur.role_id
                       WHERE ur.user_id = %s
                         AND (rta.datasource_id = %s OR rta.datasource_id = 0)
                         AND (ur.workspace_id = %s OR ur.workspace_id = 0)""",
                    (user_id, datasource_id, workspace_id)
                )
                return [r["table_name"] for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Column Access ──────────────────────────────────────────────

    def get_role_columns(self, role_id: int) -> list:
        """Get column permissions for a role."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_role_column_access WHERE role_id = %s ORDER BY datasource_id, table_name, column_name",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_columns(self, role_id: int, columns: list) -> bool:
        """Replace all column access for a role.

        Each column dict: {datasource_id, table_name, column_name, access_type, mask_pattern}
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_column_access WHERE role_id = %s", (role_id,))
                for c in columns:
                    cur.execute(
                        """INSERT INTO adh_role_column_access
                           (id, role_id, datasource_id, table_name, column_name, access_type, mask_pattern)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (_gen_id(), role_id, c.get("datasource_id", 0),
                         c["table_name"], c["column_name"],
                         c.get("access_type", "visible"), c.get("mask_pattern", ""))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_column_restrictions(self, user_id: int, datasource_id: int,
                                      table_name: str, workspace_id: int = 0) -> dict:
        """Get column restrictions for a user on a specific table.

        Returns:
            {
                "hidden_columns": ["salary", "ssn"],
                "masked_columns": {"phone": "partial"},
            }
            Empty lists = no restrictions (all columns visible).
        """
        hidden = []
        masked = {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rca.column_name, rca.access_type, rca.mask_pattern
                       FROM adh_user_roles ur
                       JOIN adh_role_column_access rca ON rca.role_id = ur.role_id
                       WHERE ur.user_id = %s
                         AND (rca.datasource_id = %s OR rca.datasource_id = 0)
                         AND rca.table_name = %s
                         AND (ur.workspace_id = %s OR ur.workspace_id = 0)""",
                    (user_id, datasource_id, table_name, workspace_id)
                )
                for r in cur.fetchall():
                    if r["access_type"] == "hidden":
                        if r["column_name"] not in hidden:
                            hidden.append(r["column_name"])
                    elif r["access_type"] == "masked":
                        masked[r["column_name"]] = r.get("mask_pattern") or "partial"
        finally:
            conn.close()
        return {"hidden_columns": hidden, "masked_columns": masked}


# Singleton instance
role_service = RoleService()
