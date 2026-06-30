"""Workspace API v2 — system-level workspace management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.auth import get_current_user
from backend.models.schemas import UserInfo
from backend.services.workspace_service_v2 import get_workspace_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request/Response Models ─────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    icon: Optional[str] = "📊"
    color: Optional[str] = "#1890ff"
    config: Optional[dict] = {}


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    config: Optional[dict] = None


class UserAdd(BaseModel):
    user_id: int
    role: str = "member"


class UserUpdate(BaseModel):
    role: str


class DatasourceAdd(BaseModel):
    datasource_id: int
    is_primary: bool = False


# ── Workspace CRUD ──────────────────────────────────────────────────

@router.get("")
async def list_workspaces(user: UserInfo = Depends(get_current_user)):
    """List all workspaces for the current user."""
    service = get_workspace_service()
    return await service.get_user_workspaces(user.id)


@router.post("")
async def create_workspace(req: WorkspaceCreate, user: UserInfo = Depends(get_current_user)):
    """Create a new workspace."""
    service = get_workspace_service()
    try:
        return await service.create_workspace(user.id, req.model_dump())
    except Exception as e:
        logger.error("Failed to create workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workspace_id}")
async def get_workspace(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Get workspace details."""
    service = get_workspace_service()

    # Check access
    if not await service.check_user_access(workspace_id, user.id):
        raise HTTPException(status_code=403, detail="无权访问此工作空间")

    workspace = await service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    return workspace


@router.put("/{workspace_id}")
async def update_workspace(workspace_id: int, req: WorkspaceUpdate, user: UserInfo = Depends(get_current_user)):
    """Update workspace. Any workspace member can edit."""
    service = get_workspace_service()

    # Check access (any member can update)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx:
        raise HTTPException(status_code=403, detail="无权修改此工作空间")

    try:
        return await service.update_workspace(workspace_id, req.model_dump(exclude_unset=True))
    except Exception as e:
        logger.error("Failed to update workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}")
async def delete_workspace(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Delete workspace."""
    service = get_workspace_service()

    # Check access (only owner can delete)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx or ctx.user_role != 'owner':
        raise HTTPException(status_code=403, detail="只有所有者可以删除工作空间")

    # Check if it's the default workspace
    workspace = await service.get_workspace(workspace_id)
    if workspace and workspace.get('is_default'):
        raise HTTPException(status_code=400, detail="不能删除默认工作空间")

    try:
        await service.delete_workspace(workspace_id)
        return {"success": True, "message": "工作空间已删除"}
    except Exception as e:
        logger.error("Failed to delete workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workspace_id}/set-default")
async def set_default_workspace(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Set workspace as user's default."""
    service = get_workspace_service()

    # Check access
    if not await service.check_user_access(workspace_id, user.id):
        raise HTTPException(status_code=403, detail="无权访问此工作空间")

    try:
        await service.set_default_workspace(user.id, workspace_id)
        return {"success": True, "message": "已设为默认工作空间"}
    except Exception as e:
        logger.error("Failed to set default workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── User Management ─────────────────────────────────────────────────

@router.get("/{workspace_id}/users")
async def list_workspace_users(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """List all users in a workspace."""
    service = get_workspace_service()

    # Check access
    if not await service.check_user_access(workspace_id, user.id):
        raise HTTPException(status_code=403, detail="无权访问此工作空间")

    return await service.get_workspace_users(workspace_id)


@router.post("/{workspace_id}/users")
async def add_user_to_workspace(workspace_id: int, req: UserAdd, user: UserInfo = Depends(get_current_user)):
    """Add a user to workspace."""
    service = get_workspace_service()

    # Check access (only owner/admin can add users)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx or ctx.user_role not in ('owner', 'admin'):
        raise HTTPException(status_code=403, detail="无权添加用户")

    try:
        await service.add_user_to_workspace(workspace_id, req.user_id, req.role)
        return {"success": True, "message": "用户已添加"}
    except Exception as e:
        logger.error("Failed to add user to workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{workspace_id}/users/{user_id}")
async def update_user_role(workspace_id: int, user_id: int, req: UserUpdate, user: UserInfo = Depends(get_current_user)):
    """Update user role in workspace."""
    service = get_workspace_service()

    # Check access (only owner/admin can update roles)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx or ctx.user_role not in ('owner', 'admin'):
        raise HTTPException(status_code=403, detail="无权修改用户角色")

    try:
        await service.update_user_role(workspace_id, user_id, req.role)
        return {"success": True, "message": "用户角色已更新"}
    except Exception as e:
        logger.error("Failed to update user role: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}/users/{user_id}")
async def remove_user_from_workspace(workspace_id: int, user_id: int, user: UserInfo = Depends(get_current_user)):
    """Remove a user from workspace."""
    service = get_workspace_service()

    # Check access (only owner/admin can remove users)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx or ctx.user_role not in ('owner', 'admin'):
        raise HTTPException(status_code=403, detail="无权移除用户")

    # Cannot remove owner
    target_ctx = await service.get_workspace_context(workspace_id, user_id)
    if target_ctx and target_ctx.user_role == 'owner':
        raise HTTPException(status_code=400, detail="不能移除工作空间所有者")

    try:
        await service.remove_user_from_workspace(workspace_id, user_id)
        return {"success": True, "message": "用户已移除"}
    except Exception as e:
        logger.error("Failed to remove user from workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Datasource Management ──────────────────────────────────────────

@router.get("/{workspace_id}/datasources")
async def list_workspace_datasources(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """List datasources associated with a workspace."""
    service = get_workspace_service()

    # Check access
    if not await service.check_user_access(workspace_id, user.id):
        raise HTTPException(status_code=403, detail="无权访问此工作空间")

    return await service.get_workspace_datasources(workspace_id)


@router.post("/{workspace_id}/datasources")
async def add_datasource_to_workspace(workspace_id: int, req: DatasourceAdd, user: UserInfo = Depends(get_current_user)):
    """Add a datasource to workspace. Any member can add."""
    service = get_workspace_service()

    # Check access (any member can add)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx:
        raise HTTPException(status_code=403, detail="无权添加数据源")

    try:
        await service.add_datasource_to_workspace(workspace_id, req.datasource_id, req.is_primary)
        return {"success": True, "message": "数据源已添加"}
    except Exception as e:
        logger.error("Failed to add datasource to workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}/datasources/{datasource_id}")
async def remove_datasource_from_workspace(workspace_id: int, datasource_id: int, user: UserInfo = Depends(get_current_user)):
    """Remove a datasource from workspace. Any member can remove."""
    service = get_workspace_service()

    # Check access (any member can remove)
    ctx = await service.get_workspace_context(workspace_id, user.id)
    if not ctx:
        raise HTTPException(status_code=403, detail="无权移除数据源")

    try:
        await service.remove_datasource_from_workspace(workspace_id, datasource_id)
        return {"success": True, "message": "数据源已移除"}
    except Exception as e:
        logger.error("Failed to remove datasource from workspace: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
