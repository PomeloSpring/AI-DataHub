"""元数据 API — 表信息、字段元数据管理端点。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...shared.common.auth import require_admin
from ..services.metadata_service import MetadataService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 同步操作 ─────────────────────────────────────────────────────────

@router.post("/sync/metadata")
def sync_metadata(req: dict = {}, admin: dict = Depends(require_admin)):
    """同步指定数据源的元数据。"""
    datasource_id = req.get("datasource_id", 0) if req else 0
    return MetadataService.sync_metadata(datasource_id)


@router.post("/sync/metadata/columns")
def sync_table_columns(req: dict, admin: dict = Depends(require_admin)):
    """同步指定表的字段元数据。"""
    datasource_id = req.get("datasource_id", 0)
    table_name = req.get("table_name", "").strip()
    return MetadataService.sync_table_columns(datasource_id, table_name)


# ── 字段元数据 CRUD ──────────────────────────────────────────────────

@router.get("/metadata")
def list_metadata(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    table_name: str = Query("", description="搜索表名"),
    column_name: str = Query("", description="搜索字段名"),
    datasource_id: Optional[int] = Query(None, description="按数据源筛选"),
    admin: dict = Depends(require_admin),
):
    """分页查询字段元数据。"""
    return MetadataService.list_metadata(page, size, table_name, column_name, datasource_id)


@router.get("/metadata/{row_id}")
def get_metadata(row_id: int, admin: dict = Depends(require_admin)):
    """获取单条字段元数据。"""
    row = MetadataService.get_metadata(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row


@router.post("/metadata")
def create_metadata(req: dict, admin: dict = Depends(require_admin)):
    """创建字段元数据。"""
    result = MetadataService.create_metadata(req)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    return result


@router.put("/metadata/{row_id}")
def update_metadata(row_id: int, req: dict, admin: dict = Depends(require_admin)):
    """更新字段元数据。"""
    result = MetadataService.update_metadata(row_id, req)
    if not result.get("success"):
        status = 404 if "不存在" in result.get("message", "") else 400
        raise HTTPException(status_code=status, detail=result.get("message", "更新失败"))
    return result


@router.delete("/metadata/{row_id}")
def delete_metadata(row_id: int, admin: dict = Depends(require_admin)):
    """删除字段元数据。"""
    return MetadataService.delete_metadata(row_id)


# ── 表信息 CRUD ──────────────────────────────────────────────────────

@router.get("/table-info")
def list_table_info(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=9999),
    table_name: str = Query("", description="搜索表名"),
    datasource_id: Optional[int] = Query(None, description="按数据源筛选"),
    admin: dict = Depends(require_admin),
):
    """分页查询表信息。"""
    return MetadataService.list_table_info(page, size, table_name, datasource_id)


@router.get("/table-info/{row_id}")
def get_table_info(row_id: int, admin: dict = Depends(require_admin)):
    """获取单条表信息。"""
    row = MetadataService.get_table_info(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="表信息不存在")
    return row


@router.post("/table-info")
def create_table_info(req: dict, admin: dict = Depends(require_admin)):
    """创建表信息。"""
    result = MetadataService.create_table_info(req)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    return result


@router.put("/table-info/{row_id}")
def update_table_info(row_id: int, req: dict, admin: dict = Depends(require_admin)):
    """更新表信息。"""
    result = MetadataService.update_table_info(row_id, req)
    if not result.get("success"):
        status = 404 if "不存在" in result.get("message", "") else 400
        raise HTTPException(status_code=status, detail=result.get("message", "更新失败"))
    return result


@router.delete("/table-info/{row_id}")
def delete_table_info(row_id: int, admin: dict = Depends(require_admin)):
    """删除表信息。"""
    return MetadataService.delete_table_info(row_id)


# ── 批量清理 ─────────────────────────────────────────────────────────

@router.post("/metadata/clear-by-datasource")
def clear_metadata_by_datasource(req: dict, admin: dict = Depends(require_admin)):
    """清理指定数据源的所有元数据。"""
    datasource_id = req.get("datasource_id", 0)
    result = MetadataService.clear_metadata_by_datasource(datasource_id)
    return result


@router.post("/metadata/clear-by-table")
def clear_metadata_by_table(req: dict, admin: dict = Depends(require_admin)):
    """清理指定表的元数据。"""
    datasource_id = req.get("datasource_id", 0)
    table_name = req.get("table_name", "").strip()
    result = MetadataService.clear_metadata_by_table(datasource_id, table_name)
    return result
