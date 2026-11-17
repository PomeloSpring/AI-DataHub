"""可视化字模库 API — 大屏可复用元素(字模)的浏览与管理。

均为元数据级端点(不含数据行)。系统内置字模(is_builtin)的编辑/下架仅 admin。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel
from typing import Optional

from backend.common.auth import get_current_user
from backend.common import i18n
from backend.modules.viz.services.vis_library_service import (
    VisLibraryService,
    BuiltinProtectedError,
    CATEGORIES,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_service = VisLibraryService()


# 字模双语在**响应组装层**做，不在 service 读路径做（有意口径）：
# `sdk_tools/screen_tools.py` 会把字模 `style_config.config` 合进图表 config，
# `report_service` 也会拿它生成报表体——若 service 直接回本地化后的行，
# 英文样式文案就会被写进存量图表配置（污染不可逆）。所以：service 永远回原始行，
# 只有面向人的响应才按语言改写。
_TEXT_FIELDS = ("name", "description")
_OVERLAY_FIELDS = ("style_config",)


def _display(row):
    """单行展示体本地化（命中译文时保留 `*_zh` 供编辑页回填中文事实源）。"""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    missing = i18n.localize_display(out, fields=_TEXT_FIELDS, overlays=_OVERLAY_FIELDS)
    if missing:
        out["i18n_fallback_fields"] = missing
    return out


def _display_rows(rows):
    return [_display(r) for r in (rows or [])]


def _display_page(payload):
    """分页返回体（{items,total,...}）只本地化 items，total 等计数口径不动。"""
    if not isinstance(payload, dict):
        return payload
    return {**payload, "items": _display_rows(payload.get("items"))}


class ComponentUpsert(BaseModel):
    code: Optional[str] = None
    name: str
    category: str
    chart_type: Optional[str] = None
    style_config: Optional[dict] = None
    thumbnail: Optional[str] = None
    description: Optional[str] = ""
    is_builtin: Optional[bool] = False
    workspace_id: Optional[int] = 0
    sort_order: Optional[int] = 0
    # 双语层（{field: {locale: text}} + style_config 覆盖层），创建时可一并带入
    i18n: Optional[dict] = None


class ComponentEnabled(BaseModel):
    """启用/禁用（停用但保留）。禁用=不可被选用：主题包套不上、系统主题菜单里不再出现该主题。"""
    enabled: bool


class ComponentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    chart_type: Optional[str] = None
    style_config: Optional[dict] = None
    thumbnail: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[int] = None
    is_enabled: Optional[int] = None
    i18n: Optional[dict] = None


@router.get("/categories")
def list_categories(user: dict = Depends(get_current_user)):
    return sorted(CATEGORIES)


@router.get("/components")
def list_components(
    category: str = QueryParam(""),
    include_inactive: bool = QueryParam(False),
    include_disabled: bool = QueryParam(False),
    q: str = QueryParam("", description="按名称/代码/描述模糊搜索（仅分页模式服务端下推）"),
    page: int = QueryParam(1, ge=1, description="页号（1 起），仅在传了 page_size 时生效"),
    page_size: Optional[int] = QueryParam(None, ge=1, le=200,
        description="每页行数；传了才走服务端真分页并返回 {items,total,page,page_size}"),
    user: dict = Depends(get_current_user),
):
    """字模清单。禁用态（is_enabled=0）默认不外发（fail-closed），
    只有管理页（include_disabled=true）需要把它们列出来给管理员再启用。

    两种返回契约按是否传 page_size 划分（不是同一份数据两种静默行为，而是显式声明的两档）：
    - 不传 page_size：一次性返回**全量数组**（选用面/编辑器字模面板需要全量在手，不分页）；
    - 传 page_size：返回 `{items,total,page,page_size}` 的**服务端真分页**（管理页专用），
      筛选/搜索/下架/禁用全部下推 SQL，总数由 COUNT 得到，不再把全表拉到前端切片。
    """
    try:
        if page_size is not None:
            return _display_page(_service.list_components_page(
                category, include_inactive, include_disabled, q, page, page_size,
            ))
        return _display_rows(_service.list_components(category, include_inactive, include_disabled))
    except Exception as e:
        logger.exception("Failed to list vis components")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/components/{component_id}")
def get_component(component_id: int, user: dict = Depends(get_current_user)):
    row = _service.get_component(component_id)
    if not row:
        raise HTTPException(status_code=404, detail="字模不存在")
    return _display(row)


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


@router.post("/components/{component_id}/enabled")
def set_component_enabled(
    component_id: int,
    req: ComponentEnabled,
    user: dict = Depends(get_current_user),
):
    """启用/禁用字模。与"下架"（软删除）分档：禁用后条目仍在库里可查、可再启用，但一律不可被选用。"""
    try:
        result = _service.set_enabled(
            component_id, req.enabled, user["user_id"], user.get("role") == "admin",
        )
        return {"success": True, **result}
    except BuiltinProtectedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to toggle vis component enabled state")
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
