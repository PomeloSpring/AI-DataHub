"""Workspace API routes — workspace management and user membership."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from services.shared.common.auth import get_current_user, require_admin
from services.shared.common.db import DBConnection
from services.authservice.services.role_service import role_service

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateWorkspaceRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    icon: Optional[str] = "📊"
    color: Optional[str] = "#1890ff"


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class AddWorkspaceUserRequest(BaseModel):
    user_id: int
    role: str = "member"


class GrantWorkspaceRoleRequest(BaseModel):
    role_id: int


class AssignWorkspaceRoleUserRequest(BaseModel):
    user_id: int


# ── Workspace CRUD ──────────────────────────────────────────────────────

@router.get("/")
def list_workspaces(user: dict = Depends(get_current_user)):
    """List workspaces the current user belongs to."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT w.*, wu.role, wu.is_default as user_default
                       FROM adh_workspaces w
                       JOIN adh_workspace_users wu ON wu.workspace_id = w.id
                       WHERE wu.user_id = %s
                       ORDER BY wu.is_default DESC, w.name""",
                    (user["user_id"],),
                )
                workspaces = cur.fetchall()
                for ws in workspaces:
                    if isinstance(ws.get("config"), str):
                        ws["config"] = json.loads(ws["config"])
                return workspaces
    except Exception as e:
        logger.error("Failed to list workspaces: %s", e)
        raise HTTPException(status_code=500, detail="获取工作空间列表失败")


@router.get("/{workspace_id}")
def get_workspace(workspace_id: int, user: dict = Depends(get_current_user)):
    """Get a single workspace by ID. User must be a member."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT w.*, wu.role, wu.is_default as user_default
                       FROM adh_workspaces w
                       JOIN adh_workspace_users wu ON wu.workspace_id = w.id
                       WHERE w.id = %s AND wu.user_id = %s""",
                    (workspace_id, user["user_id"]),
                )
                workspace = cur.fetchone()
                if not workspace:
                    raise HTTPException(status_code=404, detail="工作空间不存在")
                if isinstance(workspace.get("config"), str):
                    workspace["config"] = json.loads(workspace["config"])
                return workspace
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get workspace %d: %s", workspace_id, e)
        raise HTTPException(status_code=500, detail="获取工作空间失败")


@router.post("/")
def create_workspace(req: CreateWorkspaceRequest, user: dict = Depends(get_current_user)):
    """Create a new workspace. Current user becomes owner."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_workspaces (name, description, icon, color, owner_id, config)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (req.name, req.description, req.icon, req.color,
                     user["user_id"], json.dumps({})),
                )
                workspace_id = cur.lastrowid

                # Add creator as owner
                cur.execute(
                    """INSERT INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                       VALUES (%s, %s, 'owner', 0)""",
                    (workspace_id, user["user_id"]),
                )

                # Fetch the created workspace
                cur.execute("SELECT * FROM adh_workspaces WHERE id = %s", (workspace_id,))
                workspace = cur.fetchone()
                if workspace and isinstance(workspace.get("config"), str):
                    workspace["config"] = json.loads(workspace["config"])
                return workspace
    except Exception as e:
        logger.error("Failed to create workspace: %s", e)
        raise HTTPException(status_code=500, detail="创建工作空间失败")


@router.put("/{workspace_id}")
def update_workspace(workspace_id: int, req: UpdateWorkspaceRequest,
                     user: dict = Depends(get_current_user)):
    """Update a workspace. Requires owner or admin role in workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                # Check access
                cur.execute(
                    "SELECT role FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user["user_id"]),
                )
                membership = cur.fetchone()
                if not membership:
                    raise HTTPException(status_code=403, detail="无权访问此工作空间")
                if membership["role"] not in ("owner", "admin") and user["role"] != "admin":
                    raise HTTPException(status_code=403, detail="需要管理员权限")

                updates = []
                params = []
                for field, value in [
                    ("name", req.name),
                    ("description", req.description),
                    ("icon", req.icon),
                    ("color", req.color),
                ]:
                    if value is not None:
                        updates.append(f"{field} = %s")
                        params.append(value)

                if not updates:
                    cur.execute("SELECT * FROM adh_workspaces WHERE id = %s", (workspace_id,))
                    ws = cur.fetchone()
                    if ws and isinstance(ws.get("config"), str):
                        ws["config"] = json.loads(ws["config"])
                    return ws

                params.append(workspace_id)
                cur.execute(
                    f"UPDATE adh_workspaces SET {', '.join(updates)} WHERE id = %s",
                    params,
                )

                cur.execute("SELECT * FROM adh_workspaces WHERE id = %s", (workspace_id,))
                workspace = cur.fetchone()
                if workspace and isinstance(workspace.get("config"), str):
                    workspace["config"] = json.loads(workspace["config"])
                return workspace
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update workspace: %s", e)
        raise HTTPException(status_code=500, detail="更新工作空间失败")


@router.delete("/{workspace_id}")
def delete_workspace(workspace_id: int, user: dict = Depends(require_admin)):
    """Delete a workspace (admin only)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_workspaces WHERE id = %s", (workspace_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="工作空间不存在")

                cur.execute("DELETE FROM adh_workspace_users WHERE workspace_id = %s", (workspace_id,))
                cur.execute("DELETE FROM adh_workspace_datasources WHERE workspace_id = %s", (workspace_id,))
                cur.execute("DELETE FROM adh_workspaces WHERE id = %s", (workspace_id,))
        return {"success": True, "message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete workspace: %s", e)
        raise HTTPException(status_code=500, detail="删除工作空间失败")


# ── Workspace user management ──────────────────────────────────────────

@router.get("/{workspace_id}/users")
def list_workspace_users(workspace_id: int, user: dict = Depends(get_current_user)):
    """List users in a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                # Check access
                cur.execute(
                    "SELECT 1 FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user["user_id"]),
                )
                if not cur.fetchone() and user["role"] != "admin":
                    raise HTTPException(status_code=403, detail="无权访问此工作空间")

                cur.execute(
                    """SELECT u.id, u.username, u.email, u.avatar, wu.role, wu.joined_at
                       FROM adh_workspace_users wu
                       JOIN adh_users u ON u.id = wu.user_id
                       WHERE wu.workspace_id = %s
                       ORDER BY wu.role, u.username""",
                    (workspace_id,),
                )
                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list workspace users: %s", e)
        raise HTTPException(status_code=500, detail="获取工作空间用户列表失败")


@router.post("/{workspace_id}/users")
def add_user_to_workspace(workspace_id: int, req: AddWorkspaceUserRequest,
                          user: dict = Depends(get_current_user)):
    """Add a user to a workspace. Requires owner/admin role in workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                # Check requester has permission
                cur.execute(
                    "SELECT role FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user["user_id"]),
                )
                membership = cur.fetchone()
                if not membership:
                    raise HTTPException(status_code=403, detail="无权访问此工作空间")
                if membership["role"] not in ("owner", "admin") and user["role"] != "admin":
                    raise HTTPException(status_code=403, detail="需要管理员权限")

                # Check target user exists
                cur.execute("SELECT id FROM adh_users WHERE id = %s", (req.user_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="用户不存在")

                # Check workspace exists
                cur.execute("SELECT id FROM adh_workspaces WHERE id = %s", (workspace_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="工作空间不存在")

                cur.execute(
                    """INSERT INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                       VALUES (%s, %s, %s, 0)
                       ON DUPLICATE KEY UPDATE role = %s""",
                    (workspace_id, req.user_id, req.role, req.role),
                )
        return {"success": True, "message": "用户已添加到工作空间"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to add user to workspace: %s", e)
        raise HTTPException(status_code=500, detail="添加用户失败")


@router.delete("/{workspace_id}/users/{target_user_id}")
def remove_user_from_workspace(workspace_id: int, target_user_id: int,
                               user: dict = Depends(get_current_user)):
    """Remove a user from a workspace. Requires owner/admin role in workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                # Check requester has permission
                cur.execute(
                    "SELECT role FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user["user_id"]),
                )
                membership = cur.fetchone()
                if not membership:
                    raise HTTPException(status_code=403, detail="无权访问此工作空间")
                if membership["role"] not in ("owner", "admin") and user["role"] != "admin":
                    raise HTTPException(status_code=403, detail="需要管理员权限")

                # Cannot remove the owner
                cur.execute(
                    "SELECT role FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, target_user_id),
                )
                target = cur.fetchone()
                if target and target["role"] == "owner":
                    raise HTTPException(status_code=400, detail="不能移除工作空间所有者")

                cur.execute(
                    "DELETE FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, target_user_id),
                )
        return {"success": True, "message": "用户已从工作空间移除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to remove user from workspace: %s", e)
        raise HTTPException(status_code=500, detail="移除用户失败")


def _check_membership(cur, workspace_id: int, user: dict, require_admin_role: bool = False):
    """Verify the user belongs to the workspace; optionally require owner/admin."""
    cur.execute(
        "SELECT role FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
        (workspace_id, user["user_id"]),
    )
    membership = cur.fetchone()
    if not membership and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="无权访问此工作空间")
    if require_admin_role and membership and membership["role"] not in ("owner", "admin") \
            and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return membership


# ── Workspace v2: default selection & resources ───────────────────────

@router.post("/{workspace_id}/set-default")
def set_default_workspace(workspace_id: int, user: dict = Depends(get_current_user)):
    """Set the workspace as the current user's default."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)
                cur.execute(
                    "UPDATE adh_workspace_users SET is_default = 0 WHERE user_id = %s",
                    (user["user_id"],),
                )
                cur.execute(
                    "UPDATE adh_workspace_users SET is_default = 1 "
                    "WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user["user_id"]),
                )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to set default workspace: %s", e)
        raise HTTPException(status_code=500, detail="设置默认工作空间失败")


@router.get("/{workspace_id}/tools")
def get_workspace_tools(workspace_id: int, user: dict = Depends(get_current_user)):
    """Get datasources, MCP servers and agents bound to a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)

                cur.execute(
                    """SELECT d.id, d.name, d.db_type, wd.is_primary, '' AS alias
                       FROM adh_workspace_datasources wd
                       JOIN adh_datasources d ON d.id = wd.datasource_id
                       WHERE wd.workspace_id = %s""",
                    (workspace_id,),
                )
                datasources = cur.fetchall()

                cur.execute(
                    """SELECT m.id, m.name, m.description, COALESCE(wm.alias, '') AS alias
                       FROM adh_workspace_mcp_servers wm
                       JOIN adh_mcp_servers m ON m.id = wm.mcp_server_id
                       WHERE wm.workspace_id = %s""",
                    (workspace_id,),
                )
                mcp_servers = cur.fetchall()

                cur.execute(
                    """SELECT a.id, a.name, a.display_name, a.description, wa.is_enabled
                       FROM adh_workspace_agents wa
                       JOIN adh_agents a ON a.name = wa.agent_name
                       WHERE wa.workspace_id = %s""",
                    (workspace_id,),
                )
                agents = cur.fetchall()

                return {
                    "datasources": datasources,
                    "mcp_servers": mcp_servers,
                    "agents": agents,
                    "mcp_tools": [],
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get workspace tools: %s", e)
        raise HTTPException(status_code=500, detail="获取工作空间工具失败")


@router.get("/{workspace_id}/datasources")
def list_workspace_datasources(workspace_id: int, user: dict = Depends(get_current_user)):
    """List datasources bound to a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)
                cur.execute(
                    """SELECT d.*, wd.is_primary
                       FROM adh_workspace_datasources wd
                       JOIN adh_datasources d ON d.id = wd.datasource_id
                       WHERE wd.workspace_id = %s
                       ORDER BY wd.is_primary DESC, d.name""",
                    (workspace_id,),
                )
                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list workspace datasources: %s", e)
        raise HTTPException(status_code=500, detail="获取数据源列表失败")


@router.post("/{workspace_id}/datasources")
def add_workspace_datasource(workspace_id: int,
                             datasource_id: int = Query(...),
                             is_primary: bool = Query(False),
                             user: dict = Depends(get_current_user)):
    """Bind a datasource to a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
                if is_primary:
                    cur.execute(
                        "UPDATE adh_workspace_datasources SET is_primary = 0 WHERE workspace_id = %s",
                        (workspace_id,),
                    )
                cur.execute(
                    """INSERT INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE is_primary = %s""",
                    (workspace_id, datasource_id, int(is_primary), int(is_primary)),
                )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to add workspace datasource: %s", e)
        raise HTTPException(status_code=500, detail="添加数据源失败")


@router.delete("/{workspace_id}/datasources/{datasource_id}")
def remove_workspace_datasource(workspace_id: int, datasource_id: int,
                                user: dict = Depends(get_current_user)):
    """Unbind a datasource from a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
                cur.execute(
                    "DELETE FROM adh_workspace_datasources WHERE workspace_id = %s AND datasource_id = %s",
                    (workspace_id, datasource_id),
                )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to remove workspace datasource: %s", e)
        raise HTTPException(status_code=500, detail="移除数据源失败")


@router.post("/{workspace_id}/mcp-servers")
def add_workspace_mcp_server(workspace_id: int,
                             mcp_server_id: int = Query(...),
                             user: dict = Depends(get_current_user)):
    """Bind an MCP server to a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
                cur.execute(
                    """INSERT INTO adh_workspace_mcp_servers (workspace_id, mcp_server_id)
                       VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE mcp_server_id = mcp_server_id""",
                    (workspace_id, mcp_server_id),
                )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to add workspace MCP server: %s", e)
        raise HTTPException(status_code=500, detail="添加MCP服务失败")


@router.delete("/{workspace_id}/mcp-servers/{mcp_server_id}")
def remove_workspace_mcp_server(workspace_id: int, mcp_server_id: int,
                                user: dict = Depends(get_current_user)):
    """Unbind an MCP server from a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
                cur.execute(
                    "DELETE FROM adh_workspace_mcp_servers WHERE workspace_id = %s AND mcp_server_id = %s",
                    (workspace_id, mcp_server_id),
                )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to remove workspace MCP server: %s", e)
        raise HTTPException(status_code=500, detail="移除MCP服务失败")


# ── Workspace Roles (RBAC) ─────────────────────────────────────────────

@router.get("/{workspace_id}/roles")
def list_workspace_roles(workspace_id: int, user: dict = Depends(get_current_user)):
    """List all roles, marking which are granted to this workspace (with member count)."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)
        roles = role_service.list_roles(workspace_id)
        member_counts = role_service.get_workspace_role_member_counts(workspace_id)
        for role in roles:
            if role.get("in_workspace"):
                role["member_count"] = member_counts.get(role["id"], 0)
        return roles
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list workspace roles: %s", e)
        raise HTTPException(status_code=500, detail="获取角色列表失败")


@router.post("/{workspace_id}/roles")
def grant_workspace_role(workspace_id: int, req: GrantWorkspaceRoleRequest,
                         user: dict = Depends(get_current_user)):
    """Grant a role access to a workspace. Requires owner/admin role in workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
        if not role_service.get_role(req.role_id):
            raise HTTPException(status_code=404, detail="角色不存在")
        role_service.authorize_workspace_role(workspace_id, req.role_id)
        return {"success": True, "message": "角色已授权"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to grant workspace role: %s", e)
        raise HTTPException(status_code=500, detail="授权角色失败")


@router.delete("/{workspace_id}/roles/{role_id}")
def revoke_workspace_role(workspace_id: int, role_id: int,
                          user: dict = Depends(get_current_user)):
    """Revoke a role's access to a workspace. Requires owner/admin role in workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
        role_service.revoke_workspace_role(workspace_id, role_id)
        return {"success": True, "message": "角色已回收"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to revoke workspace role: %s", e)
        raise HTTPException(status_code=500, detail="回收角色失败")


@router.get("/{workspace_id}/roles/{role_id}/users")
def list_workspace_role_users(workspace_id: int, role_id: int,
                              user: dict = Depends(get_current_user)):
    """List workspace members holding a role."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)
        return role_service.get_workspace_role_users(workspace_id, role_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list workspace role users: %s", e)
        raise HTTPException(status_code=500, detail="获取角色成员失败")


@router.post("/{workspace_id}/roles/{role_id}/users")
def assign_workspace_role_user(workspace_id: int, role_id: int,
                               req: AssignWorkspaceRoleUserRequest,
                               user: dict = Depends(get_current_user)):
    """Assign a workspace-granted role to a workspace member."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
                cur.execute(
                    "SELECT role FROM adh_workspace_users WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, req.user_id),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="该用户不是工作空间成员，请先添加成员")
        if not any(r["id"] == role_id for r in role_service.get_workspace_roles(workspace_id)):
            raise HTTPException(status_code=400, detail="该角色尚未授权给此工作空间")
        role_service.assign_user_role(req.user_id, role_id, workspace_id)
        return {"success": True, "message": "角色已分配"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to assign workspace role user: %s", e)
        raise HTTPException(status_code=500, detail="分配角色失败")


@router.delete("/{workspace_id}/roles/{role_id}/users/{target_user_id}")
def remove_workspace_role_user(workspace_id: int, role_id: int, target_user_id: int,
                               user: dict = Depends(get_current_user)):
    """Remove a workspace-scoped role assignment from a member."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)
        role_service.remove_user_role(target_user_id, role_id, workspace_id)
        return {"success": True, "message": "角色已取消"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to remove workspace role user: %s", e)
        raise HTTPException(status_code=500, detail="取消角色失败")


# ── Workspace Workflow Configuration ─────────────────────────────────

class WorkflowConfigRequest(BaseModel):
    workflow_template_id: Optional[int] = None
    pipeline_mode: str = "agent"
    retrieval_strategy: str = "hybrid"
    max_iterations: int = 10
    config_json: Optional[dict] = None
    is_active: bool = True


@router.get("/{workspace_id}/workflow-config")
def get_workspace_workflow_config(workspace_id: int, user: dict = Depends(get_current_user)):
    """Get workflow configuration for a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)
                cur.execute(
                    """SELECT * FROM adh_workspace_workflow_configs
                       WHERE workspace_id = %s""",
                    (workspace_id,)
                )
                config = cur.fetchone()
                if not config:
                    # Return default config
                    return {
                        "workspace_id": workspace_id,
                        "workflow_template_id": None,
                        "pipeline_mode": "agent",
                        "retrieval_strategy": "hybrid",
                        "max_iterations": 10,
                        "is_active": True,
                    }
                if isinstance(config.get("config_json"), str):
                    config["config_json"] = json.loads(config["config_json"])
                return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get workflow config: %s", e)
        raise HTTPException(status_code=500, detail="获取工作流配置失败")


@router.put("/{workspace_id}/workflow-config")
def update_workspace_workflow_config(workspace_id: int, req: WorkflowConfigRequest,
                                     user: dict = Depends(get_current_user)):
    """Update workflow configuration for a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)

                # Check if config exists
                cur.execute(
                    "SELECT id FROM adh_workspace_workflow_configs WHERE workspace_id = %s",
                    (workspace_id,)
                )
                existing = cur.fetchone()

                config_json_str = json.dumps(req.config_json) if req.config_json else None

                if existing:
                    cur.execute(
                        """UPDATE adh_workspace_workflow_configs
                           SET workflow_template_id = %s, pipeline_mode = %s,
                               retrieval_strategy = %s, max_iterations = %s,
                               config_json = %s, is_active = %s
                           WHERE workspace_id = %s""",
                        (req.workflow_template_id, req.pipeline_mode,
                         req.retrieval_strategy, req.max_iterations,
                         config_json_str, int(req.is_active), workspace_id)
                    )
                else:
                    cur.execute(
                        """INSERT INTO adh_workspace_workflow_configs
                           (workspace_id, workflow_template_id, pipeline_mode,
                            retrieval_strategy, max_iterations, config_json, is_active)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (workspace_id, req.workflow_template_id, req.pipeline_mode,
                         req.retrieval_strategy, req.max_iterations,
                         config_json_str, int(req.is_active))
                    )

                return {"success": True, "message": "工作流配置已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update workflow config: %s", e)
        raise HTTPException(status_code=500, detail="更新工作流配置失败")


# ── Workspace Skills Configuration ───────────────────────────────────

class SkillConfigRequest(BaseModel):
    is_enabled: bool = True


@router.get("/{workspace_id}/skills")
def get_workspace_skills(workspace_id: int, user: dict = Depends(get_current_user)):
    """Get skills configuration for a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)

                # Check if workspace has skills config table
                cur.execute("""
                    SELECT COUNT(*) as cnt FROM information_schema.tables
                    WHERE table_name = 'adh_workspace_skills'
                """)
                table_exists = cur.fetchone()['cnt'] > 0

                if not table_exists:
                    # Return empty array if table doesn't exist
                    return []

                cur.execute(
                    """SELECT skill_key, is_enabled
                       FROM adh_workspace_skills
                       WHERE workspace_id = %s""",
                    (workspace_id,)
                )
                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get workspace skills: %s", e)
        raise HTTPException(status_code=500, detail="获取工作空间技能配置失败")


@router.put("/{workspace_id}/skills/{skill_key}")
def update_workspace_skill(workspace_id: int, skill_key: str, req: SkillConfigRequest,
                           user: dict = Depends(get_current_user)):
    """Update skill configuration for a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)

                # Create table if not exists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS adh_workspace_skills (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id BIGINT NOT NULL,
                        skill_key VARCHAR(100) NOT NULL,
                        is_enabled TINYINT DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_workspace_skill (workspace_id, skill_key)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

                cur.execute(
                    """INSERT INTO adh_workspace_skills (workspace_id, skill_key, is_enabled)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE is_enabled = %s""",
                    (workspace_id, skill_key, int(req.is_enabled), int(req.is_enabled))
                )

                return {"success": True, "message": "技能配置已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update workspace skill: %s", e)
        raise HTTPException(status_code=500, detail="更新技能配置失败")


# ── Workspace Knowledge Configuration ────────────────────────────────

class KnowledgeConfigRequest(BaseModel):
    semantic_model_ids: list = []
    retrieval_strategy: str = "hybrid"
    max_results: int = 10
    similarity_threshold: float = 0.7
    is_active: bool = True


@router.get("/{workspace_id}/knowledge-config")
def get_workspace_knowledge_config(workspace_id: int, user: dict = Depends(get_current_user)):
    """Get knowledge configuration for a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)

                # Check if table exists
                cur.execute("""
                    SELECT COUNT(*) as cnt FROM information_schema.tables
                    WHERE table_name = 'adh_workspace_knowledge_config'
                """)
                table_exists = cur.fetchone()['cnt'] > 0

                if not table_exists:
                    return {
                        "workspace_id": workspace_id,
                        "semantic_model_ids": [],
                        "retrieval_strategy": "hybrid",
                        "max_results": 10,
                        "similarity_threshold": 0.7,
                        "is_active": True,
                    }

                cur.execute(
                    """SELECT * FROM adh_workspace_knowledge_config
                       WHERE workspace_id = %s""",
                    (workspace_id,)
                )
                config = cur.fetchone()
                if not config:
                    return {
                        "workspace_id": workspace_id,
                        "semantic_model_ids": [],
                        "retrieval_strategy": "hybrid",
                        "max_results": 10,
                        "similarity_threshold": 0.7,
                        "is_active": True,
                    }

                # Parse JSON field
                if isinstance(config.get("semantic_model_ids"), str):
                    import json
                    config["semantic_model_ids"] = json.loads(config["semantic_model_ids"])

                return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get knowledge config: %s", e)
        raise HTTPException(status_code=500, detail="获取知识库配置失败")


@router.put("/{workspace_id}/knowledge-config")
def update_workspace_knowledge_config(workspace_id: int, req: KnowledgeConfigRequest,
                                      user: dict = Depends(get_current_user)):
    """Update knowledge configuration for a workspace."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user, require_admin_role=True)

                # Create table if not exists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS adh_workspace_knowledge_config (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id BIGINT NOT NULL UNIQUE,
                        semantic_model_ids JSON,
                        retrieval_strategy VARCHAR(50) DEFAULT 'hybrid',
                        max_results INT DEFAULT 10,
                        similarity_threshold FLOAT DEFAULT 0.7,
                        is_active TINYINT DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

                import json
                model_ids_str = json.dumps(req.semantic_model_ids)

                # Check if config exists
                cur.execute(
                    "SELECT id FROM adh_workspace_knowledge_config WHERE workspace_id = %s",
                    (workspace_id,)
                )
                existing = cur.fetchone()

                if existing:
                    cur.execute(
                        """UPDATE adh_workspace_knowledge_config
                           SET semantic_model_ids = %s, retrieval_strategy = %s,
                               max_results = %s, similarity_threshold = %s, is_active = %s
                           WHERE workspace_id = %s""",
                        (model_ids_str, req.retrieval_strategy, req.max_results,
                         req.similarity_threshold, int(req.is_active), workspace_id)
                    )
                else:
                    cur.execute(
                        """INSERT INTO adh_workspace_knowledge_config
                           (workspace_id, semantic_model_ids, retrieval_strategy,
                            max_results, similarity_threshold, is_active)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (workspace_id, model_ids_str, req.retrieval_strategy,
                         req.max_results, req.similarity_threshold, int(req.is_active))
                    )

                return {"success": True, "message": "知识库配置已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update knowledge config: %s", e)
        raise HTTPException(status_code=500, detail="更新知识库配置失败")
