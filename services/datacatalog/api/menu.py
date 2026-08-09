"""菜单树 API — 层级菜单项的 CRUD。

从 backend/api/menu.py 迁移而来。
表: adh_menu_items
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from services.shared.common.auth import get_current_user, require_admin
from services.datacatalog.services.menu_service import menu_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 端点 ──────────────────────────────────────────────────────────────


@router.get("/menu-tree")
def get_menu_tree(
    workspace_id: int = Query(0, description="工作空间 ID"),
):
    """获取完整菜单树 — 公开接口，无需认证。

    Args:
        workspace_id: 工作空间 ID，0 表示系统级菜单。
    """
    return menu_service.get_menu_tree(workspace_id)


@router.post("/menu-tree")
def create_menu_item(
    req: dict,
    admin: dict = Depends(require_admin),
):
    """创建菜单项（需要管理员权限）。"""
    return menu_service.create_menu_item(req)


@router.put("/menu-tree/{item_id}")
def update_menu_item(
    item_id: int,
    req: dict,
    admin: dict = Depends(require_admin),
):
    """更新菜单项（需要管理员权限）。"""
    try:
        return menu_service.update_menu_item(item_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/menu-tree/{item_id}")
def delete_menu_item(
    item_id: int,
    admin: dict = Depends(require_admin),
):
    """删除菜单项及其子项（需要管理员权限）。"""
    try:
        return menu_service.delete_menu_item(item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/menu-tree/{item_id}/move")
def move_menu_item(
    item_id: int,
    direction: str = Query(..., description="移动方向: up 或 down"),
    admin: dict = Depends(require_admin),
):
    """移动菜单项的排序位置（需要管理员权限）。"""
    try:
        return menu_service.move_menu_item(item_id, direction)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
