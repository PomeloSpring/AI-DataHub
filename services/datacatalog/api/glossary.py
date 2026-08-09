"""业务术语 API — 管理业务术语（Glossary）的 CRUD 端点。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...shared.common.auth import require_admin
from ..services.term_service import TermService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
def list_terms(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query("", description="搜索术语名称"),
    datasource_id: Optional[int] = Query(None, description="按数据源筛选"),
    admin: dict = Depends(require_admin),
):
    """分页查询业务术语。"""
    return TermService.list_terms(page, size, search, datasource_id)


@router.get("/{row_id}")
def get_term(row_id: int, admin: dict = Depends(require_admin)):
    """获取单条业务术语。"""
    row = TermService.get_term(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="术语不存在")
    return row


@router.post("/")
def create_term(req: dict, admin: dict = Depends(require_admin)):
    """创建业务术语。"""
    result = TermService.create_term(req)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    return result


@router.put("/{row_id}")
def update_term(row_id: int, req: dict, admin: dict = Depends(require_admin)):
    """更新业务术语。"""
    result = TermService.update_term(row_id, req)
    if not result.get("success"):
        status = 404 if "不存在" in result.get("message", "") else 400
        raise HTTPException(status_code=status, detail=result.get("message", "更新失败"))
    return result


@router.delete("/{row_id}")
def delete_term(row_id: int, admin: dict = Depends(require_admin)):
    """删除业务术语。"""
    return TermService.delete_term(row_id)


@router.put("/{row_id}/toggle")
def toggle_term(row_id: int, admin: dict = Depends(require_admin)):
    """切换业务术语的启用/禁用状态。"""
    result = TermService.toggle_term(row_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "术语不存在"))
    return result
