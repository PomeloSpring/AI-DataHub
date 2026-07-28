"""Workspace API — CRUD endpoints for workspace management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.auth import get_current_user, require_admin
from backend.models.schemas import UserInfo
from backend.services.workspace_service import get_workspace_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request/Response Models ─────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    workspace_type: Optional[str] = "custom"
    is_default: Optional[bool] = False
    is_public: Optional[bool] = False
    allowed_modes: Optional[str] = "quick,deep,agent"
    default_mode: Optional[str] = "quick"
    retrieval_strategy: Optional[str] = "full_table"
    config: Optional[dict] = {}
    icon: Optional[str] = "📊"
    color: Optional[str] = "#1890ff"
    datasource_ids: Optional[list[int]] = []
    mcp_server_ids: Optional[list[int]] = []
    agent_names: Optional[list[str]] = []


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workspace_type: Optional[str] = None
    is_default: Optional[bool] = None
    is_public: Optional[bool] = None
    allowed_modes: Optional[str] = None
    default_mode: Optional[str] = None
    retrieval_strategy: Optional[str] = None
    config: Optional[dict] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    datasource_ids: Optional[list[int]] = None
    mcp_server_ids: Optional[list[int]] = None
    agent_names: Optional[list[str]] = None


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("")
async def list_workspaces(user: UserInfo = Depends(get_current_user)):
    """List all workspaces for the current user."""
    service = get_workspace_service()
    workspaces = await service.get_user_workspaces(user.id)
    return workspaces


@router.get("/{workspace_id}")
async def get_workspace(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Get workspace details."""
    service = get_workspace_service()
    workspace = await service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return workspace


@router.post("")
async def create_workspace(req: WorkspaceCreate, user: UserInfo = Depends(get_current_user)):
    """Create a new workspace."""
    service = get_workspace_service()

    try:
        workspace = await service.create_workspace(user.id, req.model_dump())
        return workspace
    except Exception as e:
        logger.error("Failed to create workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"创建工作空间失败: {str(e)}")


@router.put("/{workspace_id}")
async def update_workspace(
    workspace_id: int,
    req: WorkspaceUpdate,
    user: UserInfo = Depends(get_current_user),
):
    """Update workspace configuration."""
    service = get_workspace_service()

    # Check workspace exists
    workspace = await service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    # Check ownership (only owner or admin can update)
    if workspace['user_id'] != user.id and user.role != 'admin':
        raise HTTPException(status_code=403, detail="无权修改此工作空间")

    try:
        updated = await service.update_workspace(workspace_id, req.model_dump(exclude_unset=True))
        return updated
    except Exception as e:
        logger.error("Failed to update workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"更新工作空间失败: {str(e)}")


@router.delete("/{workspace_id}")
async def delete_workspace(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Delete a workspace."""
    service = get_workspace_service()

    # Check workspace exists
    workspace = await service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    # Check ownership
    if workspace['user_id'] != user.id and user.role != 'admin':
        raise HTTPException(status_code=403, detail="无权删除此工作空间")

    # Prevent deleting default workspace
    if workspace.get('is_default'):
        raise HTTPException(status_code=400, detail="不能删除默认工作空间")

    try:
        await service.delete_workspace(workspace_id)
        return {"success": True, "message": "工作空间已删除"}
    except Exception as e:
        logger.error("Failed to delete workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"删除工作空间失败: {str(e)}")


@router.post("/{workspace_id}/set-default")
async def set_default_workspace(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Set a workspace as the user's default."""
    service = get_workspace_service()

    # Check workspace exists
    workspace = await service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    # Check ownership
    if workspace['user_id'] != user.id:
        raise HTTPException(status_code=403, detail="无权修改此工作空间")

    try:
        await service.set_default_workspace(user.id, workspace_id)
        return {"success": True, "message": "已设为默认工作空间"}
    except Exception as e:
        logger.error("Failed to set default workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"设置默认工作空间失败: {str(e)}")


@router.get("/{workspace_id}/tools")
async def get_workspace_tools(workspace_id: int, user: UserInfo = Depends(get_current_user)):
    """Get all available tools for a workspace."""
    service = get_workspace_service()

    # Check workspace exists
    workspace = await service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    try:
        tools = await service.get_workspace_tools(workspace_id)
        return tools
    except Exception as e:
        logger.error("Failed to get workspace tools: %s", e)
        raise HTTPException(status_code=500, detail=f"获取工作空间工具失败: {str(e)}")


# ── Resource Management ─────────────────────────────────────────────

@router.post("/{workspace_id}/datasources")
async def add_datasource_to_workspace(
    workspace_id: int,
    datasource_id: int,
    is_primary: bool = False,
    user: UserInfo = Depends(get_current_user),
):
    """Add a datasource to workspace."""
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()

    try:
        with conn.cursor() as cur:
            # Check if already exists
            cur.execute(
                "SELECT id FROM adh_workspace_datasources WHERE workspace_id = %s AND datasource_id = %s",
                (workspace_id, datasource_id)
            )
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="数据源已在此工作空间中")

            # If setting as primary, unset current primary
            if is_primary:
                cur.execute(
                    "UPDATE adh_workspace_datasources SET is_primary = 0 WHERE workspace_id = %s",
                    (workspace_id,)
                )

            cur.execute(
                """INSERT INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
                   VALUES (%s, %s, %s)""",
                (workspace_id, datasource_id, is_primary)
            )
            conn.commit()

        return {"success": True, "message": "数据源已添加到工作空间"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Failed to add datasource to workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"添加数据源失败: {str(e)}")
    finally:
        conn.close()


@router.delete("/{workspace_id}/datasources/{datasource_id}")
async def remove_datasource_from_workspace(
    workspace_id: int,
    datasource_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Remove a datasource from workspace."""
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()

    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_workspace_datasources WHERE workspace_id = %s AND datasource_id = %s",
                (workspace_id, datasource_id)
            )
            conn.commit()

        return {"success": True, "message": "数据源已从工作空间移除"}
    except Exception as e:
        conn.rollback()
        logger.error("Failed to remove datasource from workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"移除数据源失败: {str(e)}")
    finally:
        conn.close()


@router.post("/{workspace_id}/mcp-servers")
async def add_mcp_server_to_workspace(
    workspace_id: int,
    mcp_server_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Add an MCP server to workspace."""
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()

    try:
        with conn.cursor() as cur:
            # Check if already exists
            cur.execute(
                "SELECT id FROM adh_workspace_mcp_servers WHERE workspace_id = %s AND mcp_server_id = %s",
                (workspace_id, mcp_server_id)
            )
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="MCP服务已在此工作空间中")

            cur.execute(
                """INSERT INTO adh_workspace_mcp_servers (workspace_id, mcp_server_id)
                   VALUES (%s, %s)""",
                (workspace_id, mcp_server_id)
            )
            conn.commit()

        return {"success": True, "message": "MCP服务已添加到工作空间"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Failed to add MCP server to workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"添加MCP服务失败: {str(e)}")
    finally:
        conn.close()


@router.delete("/{workspace_id}/mcp-servers/{mcp_server_id}")
async def remove_mcp_server_from_workspace(
    workspace_id: int,
    mcp_server_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Remove an MCP server from workspace."""
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()

    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_workspace_mcp_servers WHERE workspace_id = %s AND mcp_server_id = %s",
                (workspace_id, mcp_server_id)
            )
            conn.commit()

        return {"success": True, "message": "MCP服务已从工作空间移除"}
    except Exception as e:
        conn.rollback()
        logger.error("Failed to remove MCP server from workspace: %s", e)
        raise HTTPException(status_code=500, detail=f"移除MCP服务失败: {str(e)}")
    finally:
        conn.close()
