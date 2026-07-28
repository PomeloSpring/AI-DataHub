"""Workspace Service v2 — system-level workspace management.

This service manages workspaces as top-level isolation units.
Each workspace has its own dashboards, conversations, MCP servers,
agents, prompts, and metadata.

Datasources are shared globally but can be associated with workspaces.
"""

import json
import logging
from typing import Optional
from dataclasses import dataclass

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceContext:
    """Workspace context for a request."""
    workspace_id: int
    user_id: int
    user_role: str  # owner, admin, member, viewer


class WorkspaceService:
    """Service for managing workspaces."""

    # ── Workspace CRUD ─────────────────────────────────────────────

    async def get_workspace(self, workspace_id: int) -> Optional[dict]:
        """Get workspace by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_workspaces WHERE id = %s",
                    (workspace_id,)
                )
                workspace = cur.fetchone()
                if workspace and isinstance(workspace.get('config'), str):
                    workspace['config'] = json.loads(workspace['config'])
                return workspace
        finally:
            conn.close()

    async def get_user_workspaces(self, user_id: int) -> list[dict]:
        """Get all workspaces for a user."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT w.*, wu.role, wu.is_default as user_default
                       FROM adh_workspaces w
                       JOIN adh_workspace_users wu ON wu.workspace_id = w.id
                       WHERE wu.user_id = %s
                       ORDER BY wu.is_default DESC, w.name""",
                    (user_id,)
                )
                workspaces = cur.fetchall()
                for ws in workspaces:
                    if isinstance(ws.get('config'), str):
                        ws['config'] = json.loads(ws['config'])
                return workspaces
        finally:
            conn.close()

    async def create_workspace(self, owner_id: int, data: dict) -> dict:
        """Create a new workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Create workspace
                cur.execute(
                    """INSERT INTO adh_workspaces (name, description, icon, color, owner_id, config)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        data['name'],
                        data.get('description', ''),
                        data.get('icon', '📊'),
                        data.get('color', '#1890ff'),
                        owner_id,
                        json.dumps(data.get('config', {})),
                    )
                )
                workspace_id = cur.lastrowid

                # Add owner to workspace
                cur.execute(
                    """INSERT INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                       VALUES (%s, %s, 'owner', 0)""",
                    (workspace_id, owner_id)
                )

                conn.commit()
                return await self.get_workspace(workspace_id)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def update_workspace(self, workspace_id: int, data: dict) -> dict:
        """Update workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                updates = []
                params = []
                for field in ['name', 'description', 'icon', 'color']:
                    if field in data:
                        updates.append(f"{field} = %s")
                        params.append(data[field])

                if 'config' in data:
                    updates.append("config = %s")
                    params.append(json.dumps(data['config']))

                if updates:
                    params.append(workspace_id)
                    cur.execute(
                        f"UPDATE adh_workspaces SET {', '.join(updates)} WHERE id = %s",
                        params
                    )
                    conn.commit()

                return await self.get_workspace(workspace_id)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def delete_workspace(self, workspace_id: int) -> bool:
        """Delete workspace and all associated data."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Delete workspace associations
                cur.execute("DELETE FROM adh_workspace_users WHERE workspace_id = %s", (workspace_id,))
                cur.execute("DELETE FROM adh_workspace_datasources WHERE workspace_id = %s", (workspace_id,))

                # Delete workspace
                cur.execute("DELETE FROM adh_workspaces WHERE id = %s", (workspace_id,))

                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    # ── User Management ────────────────────────────────────────────

    async def get_workspace_users(self, workspace_id: int) -> list[dict]:
        """Get all users in a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT u.id, u.username, u.email, u.avatar, wu.role, wu.joined_at
                       FROM adh_workspace_users wu
                       JOIN adh_users u ON u.id = wu.user_id
                       WHERE wu.workspace_id = %s
                       ORDER BY wu.role, u.username""",
                    (workspace_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    async def add_user_to_workspace(self, workspace_id: int, user_id: int, role: str = 'member') -> bool:
        """Add a user to a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_workspace_users (workspace_id, user_id, role)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE role = %s""",
                    (workspace_id, user_id, role, role)
                )
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def update_user_role(self, workspace_id: int, user_id: int, role: str) -> bool:
        """Update user role in workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE adh_workspace_users SET role = %s
                       WHERE workspace_id = %s AND user_id = %s""",
                    (role, workspace_id, user_id)
                )
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def remove_user_from_workspace(self, workspace_id: int, user_id: int) -> bool:
        """Remove a user from a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user_id)
                )
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def set_default_workspace(self, user_id: int, workspace_id: int) -> bool:
        """Set a workspace as user's default."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Unset current default
                cur.execute(
                    "UPDATE adh_workspace_users SET is_default = 0 WHERE user_id = %s",
                    (user_id,)
                )
                # Set new default
                cur.execute(
                    """UPDATE adh_workspace_users SET is_default = 1
                       WHERE workspace_id = %s AND user_id = %s""",
                    (workspace_id, user_id)
                )
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def get_user_default_workspace(self, user_id: int) -> Optional[dict]:
        """Get user's default workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT w.* FROM adh_workspaces w
                       JOIN adh_workspace_users wu ON wu.workspace_id = w.id
                       WHERE wu.user_id = %s AND wu.is_default = 1""",
                    (user_id,)
                )
                workspace = cur.fetchone()
                if workspace and isinstance(workspace.get('config'), str):
                    workspace['config'] = json.loads(workspace['config'])
                return workspace
        finally:
            conn.close()

    # ── Datasource Management ──────────────────────────────────────

    async def get_workspace_datasources(self, workspace_id: int) -> list[dict]:
        """Get datasources associated with a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT d.*, wd.is_primary
                       FROM adh_workspace_datasources wd
                       JOIN adh_datasources d ON d.id = wd.datasource_id
                       WHERE wd.workspace_id = %s
                       ORDER BY wd.is_primary DESC, d.name""",
                    (workspace_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    async def add_datasource_to_workspace(self, workspace_id: int, datasource_id: int, is_primary: bool = False) -> bool:
        """Add a datasource to workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # If setting as primary, unset current primary
                if is_primary:
                    cur.execute(
                        "UPDATE adh_workspace_datasources SET is_primary = 0 WHERE workspace_id = %s",
                        (workspace_id,)
                    )

                cur.execute(
                    """INSERT INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE is_primary = %s""",
                    (workspace_id, datasource_id, is_primary, is_primary)
                )
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    async def remove_datasource_from_workspace(self, workspace_id: int, datasource_id: int) -> bool:
        """Remove a datasource from workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_workspace_datasources WHERE workspace_id = %s AND datasource_id = %s",
                    (workspace_id, datasource_id)
                )
                conn.commit()
                return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    # ── Workspace Context ──────────────────────────────────────────

    async def get_workspace_context(self, workspace_id: int, user_id: int) -> Optional[WorkspaceContext]:
        """Get workspace context for a request."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT role FROM adh_workspace_users
                       WHERE workspace_id = %s AND user_id = %s""",
                    (workspace_id, user_id)
                )
                row = cur.fetchone()

                # If user not in workspace, auto-add to default workspace
                if not row:
                    cur.execute("SELECT id, is_default FROM adh_workspaces WHERE id = %s", (workspace_id,))
                    ws = cur.fetchone()
                    if ws and ws.get('is_default'):
                        # Check if user is system admin → give owner role
                        cur.execute("SELECT role FROM adh_users WHERE id = %s", (user_id,))
                        user_row = cur.fetchone()
                        ws_role = 'owner' if user_row and user_row.get('role') == 'admin' else 'member'
                        cur.execute(
                            """INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                               VALUES (%s, %s, %s, 1)""",
                            (workspace_id, user_id, ws_role)
                        )
                        conn.commit()
                        row = {'role': ws_role}

                if not row:
                    return None

                return WorkspaceContext(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    user_role=row['role'],
                )
        finally:
            conn.close()

    async def check_user_access(self, workspace_id: int, user_id: int) -> bool:
        """Check if user has access to workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user_id)
                )
                if cur.fetchone():
                    return True

                # Auto-add to default workspace if not already a member
                cur.execute("SELECT id, is_default FROM adh_workspaces WHERE id = %s", (workspace_id,))
                ws = cur.fetchone()
                if ws and ws.get('is_default'):
                    cur.execute("SELECT role FROM adh_users WHERE id = %s", (user_id,))
                    user_row = cur.fetchone()
                    ws_role = 'owner' if user_row and user_row.get('role') == 'admin' else 'member'
                    cur.execute(
                        """INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                           VALUES (%s, %s, %s, 1)""",
                        (workspace_id, user_id, ws_role)
                    )
                    conn.commit()
                    return True

                return False
        finally:
            conn.close()


# Singleton
_workspace_service: Optional[WorkspaceService] = None


def get_workspace_service() -> WorkspaceService:
    """Get the global workspace service singleton."""
    global _workspace_service
    if _workspace_service is None:
        _workspace_service = WorkspaceService()
    return _workspace_service
