"""表关联关系 API — 管理表间关联关系（ER 图谱）的 CRUD 端点。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...shared.common.auth import require_admin
from ..services.relation_service import RelationService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
def list_relations(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    table_name: str = Query("", description="搜索源表或目标表"),
    datasource_id: Optional[int] = Query(None, description="按数据源筛选"),
    admin: dict = Depends(require_admin),
):
    """分页查询表关联关系。"""
    return RelationService.list_relations(page, size, table_name, datasource_id)


@router.get("/{row_id}")
def get_relation(row_id: int, admin: dict = Depends(require_admin)):
    """获取单条表关联关系。"""
    row = RelationService.get_relation(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="关联关系不存在")
    return row


@router.post("/")
def create_relation(req: dict, admin: dict = Depends(require_admin)):
    """创建表关联关系。"""
    result = RelationService.create_relation(req)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    return result


@router.put("/{row_id}")
def update_relation(row_id: int, req: dict, admin: dict = Depends(require_admin)):
    """更新表关联关系。"""
    result = RelationService.update_relation(row_id, req)
    if not result.get("success"):
        status = 404 if "不存在" in result.get("message", "") else 400
        raise HTTPException(status_code=status, detail=result.get("message", "更新失败"))
    return result


@router.delete("/{row_id}")
def delete_relation(row_id: int, admin: dict = Depends(require_admin)):
    """删除表关联关系。"""
    return RelationService.delete_relation(row_id)


@router.post("/sync")
def sync_relations(req: dict = {}, admin: dict = Depends(require_admin)):
    """自动检测并同步 MySQL 外键作为表关联关系。"""
    datasource_id = req.get("datasource_id", 0) if req else 0
    return RelationService.sync_relations(datasource_id)
