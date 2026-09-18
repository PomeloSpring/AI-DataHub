"""可视化字模库 API — 大屏可复用元素(字模)的浏览与管理。

均为元数据级端点(不含数据行)。系统内置字模(is_builtin)的编辑/下架仅 admin。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel
from typing import Optional

from services.shared.common.auth import get_current_user
from services.dataviz.services.vis_library_service import (
    VisLibraryService,
    BuiltinProtectedError,
    CATEGORIES,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_service = VisLibraryService()


class ComponentUpsert(BaseModel):
    code: Optional[str] = None
    name: str
    category: str
    chart_type: Optional[str] = None
    style_config: Optional[dict] = None
    thumbnail: Optional[str] = None
    query_template: Optional[dict] = None
    description: Optional[str] = ""
    is_builtin: Optional[bool] = False
    workspace_id: Optional[int] = 0
    sort_order: Optional[int] = 0


class ComponentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    chart_type: Optional[str] = None
    style_config: Optional[dict] = None
    thumbnail: Optional[str] = None
    query_template: Optional[dict] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[int] = None


@router.get("/categories")
def list_categories(user: dict = Depends(get_current_user)):
    return sorted(CATEGORIES)


@router.get("/components")
def list_components(
    category: str = QueryParam(""),
    include_inactive: bool = QueryParam(False),
    user: dict = Depends(get_current_user),
):
    try:
        return _service.list_components(category, include_inactive)
    except Exception as e:
        logger.exception("Failed to list vis components")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/components/{component_id}")
def get_component(component_id: int, user: dict = Depends(get_current_user)):
    row = _service.get_component(component_id)
    if not row:
        raise HTTPException(status_code=404, detail="字模不存在")
    return row


@router.post("/components")
def create_component(req: ComponentUpsert, user: dict = Depends(get_current_user)):
    try:
        cid = _service.create_component(req.model_dump(), user["user_id"], user.get("role") == "admin")
        return {"id": cid}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to create vis component")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/components/{component_id}")
def update_component(
    component_id: int,
    req: ComponentUpdate,
    user: dict = Depends(get_current_user),
):
    try:
        ok = _service.update_component(
            component_id, req.model_dump(exclude_none=True), user["user_id"], user.get("role") == "admin",
        )
        if not ok:
            raise HTTPException(status_code=404, detail="字模不存在或无变化")
        return {"success": True}
    except BuiltinProtectedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update vis component")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/components/{component_id}")
def delete_component(component_id: int, user: dict = Depends(get_current_user)):
    try:
        ok = _service.delete_component(component_id, user["user_id"], user.get("role") == "admin")
        if not ok:
            raise HTTPException(status_code=404, detail="字模不存在")
        return {"success": True}
    except BuiltinProtectedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete vis component")
        raise HTTPException(status_code=500, detail=str(e))
