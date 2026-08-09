"""SQL模板 API — 管理 SQL 模板的 CRUD 端点。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...shared.common.auth import require_admin
from ..services.template_service import TemplateService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
def list_templates(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query("", description="搜索模板名称或分类"),
    datasource_id: Optional[int] = Query(None, description="按数据源筛选"),
    admin: dict = Depends(require_admin),
):
    """分页查询 SQL 模板。"""
    return TemplateService.list_templates(page, size, search, datasource_id)


@router.get("/{row_id}")
def get_template(row_id: int, admin: dict = Depends(require_admin)):
    """获取单条 SQL 模板。"""
    row = TemplateService.get_template(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    return row


@router.post("/")
def create_template(req: dict, admin: dict = Depends(require_admin)):
    """创建 SQL 模板。"""
    result = TemplateService.create_template(req)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    return result


@router.put("/{row_id}")
def update_template(row_id: int, req: dict, admin: dict = Depends(require_admin)):
    """更新 SQL 模板。"""
    result = TemplateService.update_template(row_id, req)
    if not result.get("success"):
        status = 404 if "不存在" in result.get("message", "") else 400
        raise HTTPException(status_code=status, detail=result.get("message", "更新失败"))
    return result


@router.delete("/{row_id}")
def delete_template(row_id: int, admin: dict = Depends(require_admin)):
    """删除 SQL 模板。"""
    return TemplateService.delete_template(row_id)
