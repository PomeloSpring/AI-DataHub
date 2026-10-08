"""Workspace API routes — workspace management and user membership."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from typing import Optional

from backend.common.auth import get_current_user, require_admin
from backend.common.db import DBConnection
from backend.core.role_service import role_service

logger = logging.getLogger(__name__)

router = APIRouter()


class WorkspacePayload(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_resource_bindings(cls, data):
        if isinstance(data, dict) and {"mcp_server_ids", "mcp_server_id", "knowledge_base_ids", "knowledge_base_id"}.intersection(data):
            raise ValueError("知识库和 MCP 请在 AS-BOT 中绑定，不再支持工作空间直接绑定")
        return data


class CreateWorkspaceRequest(WorkspacePayload):
    name: str
    description: Optional[str] = ""
    icon: Optional[str] = "📊"
    color: Optional[str] = "#1890ff"


class UpdateWorkspaceRequest(WorkspacePayload):
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

# ── 管理员统管: 全用户工作空间与双配额(须在 /{workspace_id} 之前定义) ──

class SetWorkspaceQuotaRequest(BaseModel):
    max_workspaces: int = 5
    disk_quota_bytes: int = 5 * 1024 ** 3


@router.get("/quotas")
def list_workspace_quotas(admin: dict = Depends(require_admin)):
    """管理员统管: 全用户工作空间清单 + 双配额 + 每空间磁盘用量。"""
    from backend.modules.mind.execution.session_workspace import workspace_disk_usage
    users = role_service.list_workspace_quotas()
    for item in users:
        for ws in item.get("workspaces", []):
            ws["disk_usage_bytes"] = workspace_disk_usage(ws["id"])
    return users


@router.put("/quotas/{user_id}")
def set_workspace_quota(user_id: int, req: SetWorkspaceQuotaRequest,
                        admin: dict = Depends(require_admin)):
    """设置用户工作空间配额(可建空间数 + 每空间磁盘)。"""
    if req.max_workspaces < 1 or req.disk_quota_bytes < 1:
        raise HTTPException(status_code=400, detail="配额必须为正数")
    ok = role_service.set_user_workspace_quota(user_id, req.max_workspaces, req.disk_quota_bytes)
    return {"success": ok}


@router.get("/")
def list_workspaces(user: dict = Depends(get_current_user)):
    """列出当前用户的工作空间(个人工作站, 随用户走); 首次访问自动创建默认空间。"""
    uid = int(user["user_id"])
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                # 懒创建默认工作站(幂等): 每个用户随用户走自动拥有一个默认空间
                cur.execute("SELECT id FROM adh_workspaces WHERE owner_id = %s LIMIT 1", (uid,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO adh_workspaces (name, description, icon, color, owner_id, is_default, config) "
                        "VALUES (%s, %s, %s, %s, %s, 1, %s)",
                        (f"{user.get('username') or 'user'}的工作站", "个人工作站(随用户自动创建)",
                         "🏠", "#1890ff", uid, json.dumps({})),
                    )
                    conn.commit()
                cur.execute(
                    """SELECT w.*, 'owner' AS role, w.is_default AS user_default
                       FROM adh_workspaces w
                       WHERE w.owner_id = %s
                       ORDER BY w.is_default DESC, w.name""",
                    (uid,),
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
    """Get a single workspace by ID. 仅属主(或 admin)可访问。"""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT w.*, 'owner' AS role, w.is_default AS user_default
                       FROM adh_workspaces w
                       WHERE w.id = %s""",
                    (workspace_id,),
                )
                workspace = cur.fetchone()
                if not workspace:
                    raise HTTPException(status_code=404, detail="工作空间不存在")
                if user.get("role") != "admin" and workspace.get("owner_id") != user["user_id"]:
                    raise HTTPException(status_code=403, detail="无权访问该工作空间")
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
    """Create a new workspace. Current user becomes owner. 受每用户空间数配额限制。"""
    uid = int(user["user_id"])
    quota = role_service.get_user_workspace_quota(uid)
    if role_service.count_user_workspaces(uid) >= quota["max_workspaces"]:
        raise HTTPException(
            status_code=409,
            detail=f"工作空间数量已达上限({quota['max_workspaces']}个)，请先删除闲置空间或联系管理员调整配额")
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
    """工作空间随用户走: 仅属主可操作(成员体系已退役; 参数保留兼容)。"""
    cur.execute("SELECT owner_id FROM adh_workspaces WHERE id = %s", (workspace_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    is_owner = row["owner_id"] == user["user_id"]
    if not is_owner and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="无权访问此工作空间")
    return {"role": "owner" if is_owner else "admin"}


# ── Workspace v2: default selection & resources ───────────────────────

@router.post("/{workspace_id}/set-default")
def set_default_workspace(workspace_id: int, user: dict = Depends(get_current_user)):
    """Set the workspace as the current user's default."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                _check_membership(cur, workspace_id, user)
                # 默认空间语义随属主落在 adh_workspaces.is_default(成员表已冻结)
                cur.execute(
                    "UPDATE adh_workspaces SET is_default = 0 WHERE owner_id = %s",
                    (user["user_id"],),
                )
                cur.execute(
                    "UPDATE adh_workspaces SET is_default = 1 WHERE id = %s AND owner_id = %s",
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

                # 纯角色裁决: 工作空间不再绑定数据源(adh_workspace_datasources 退役);
                # 数据源可用集=当前用户角色授权, 空授权 fail-closed 空列表。
                from backend.core.role_service import role_service
                allowed = sorted(set(role_service.get_user_allowed_datasources(
                    user.get("user_id") or 0, workspace_id)))
                datasources = []
                if allowed:
                    marks = ','.join(['%s'] * len(allowed))
                    cur.execute(
                        f"""SELECT d.id, d.name, d.db_type, 0 AS is_primary, '' AS alias
                            FROM adh_datasources d WHERE d.id IN ({marks}) ORDER BY d.name""",
                        tuple(allowed),
                    )
                    datasources = cur.fetchall()

                from backend.modules.mind.execution.as_bots import visible_resource_ids
                ids = visible_resource_ids(user, workspace_id, "mcp_server_ids")
                mcp_servers = []
                if ids:
                    marks = ','.join(['%s'] * len(ids))
                    cur.execute(f"SELECT id,name,description,'' AS alias FROM adh_mcp_servers "
                                f"WHERE id IN ({marks}) AND is_active=1 ORDER BY name", tuple(ids))
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


@router.post("/{workspace_id}/mcp-servers")
def add_workspace_mcp_server(workspace_id: int,
                             mcp_server_id: int = Query(...),
                             user: dict = Depends(get_current_user)):
    """已退役的绑定入口：保留鉴权及可诊断迁移提示，不执行写入。"""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            _check_membership(cur, workspace_id, user, require_admin_role=True)
    raise HTTPException(410, "MCP 工作空间绑定已停用，请在 AS-BOT 中配置服务和逐工具授权")


@router.delete("/{workspace_id}/mcp-servers/{mcp_server_id}")
def remove_workspace_mcp_server(workspace_id: int, mcp_server_id: int,
                                user: dict = Depends(get_current_user)):
    """旧解绑不再改变任何资源授权。"""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            _check_membership(cur, workspace_id, user, require_admin_role=True)
    raise HTTPException(410, "MCP 工作空间绑定已停用，请在 AS-BOT 中调整服务和逐工具授权")


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
