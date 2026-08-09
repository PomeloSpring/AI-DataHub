"""数据源 API — 管理数据库连接。

从 backend/api/datasource.py 迁移而来。
表: adh_datasources
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from services.shared.common.auth import get_current_user, require_admin
from services.datacatalog.services.datasource_service import datasource_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 端点 ──────────────────────────────────────────────────────────────


@router.get("/")
async def list_datasources(
    workspace_id: int = Query(0, description="工作空间 ID"),
    user: dict = Depends(get_current_user),
):
    """列出所有数据源。"""
    return await datasource_service.list_datasources(workspace_id)


@router.get("/{ds_id}")
async def get_datasource(
    ds_id: int,
    user: dict = Depends(get_current_user),
):
    """获取单个数据源（密码已脱敏）。"""
    ds = await datasource_service.get_datasource(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return ds


@router.post("/")
async def create_datasource(
    req: dict,
    user: dict = Depends(require_admin),
):
    """创建数据源（需要管理员权限）。"""
    return await datasource_service.create_datasource(req, owner_id=user.get("user_id", 0))


@router.put("/{ds_id}")
async def update_datasource(
    ds_id: int,
    req: dict,
    user: dict = Depends(require_admin),
):
    """更新数据源（需要管理员权限）。"""
    return await datasource_service.update_datasource(ds_id, req)


@router.delete("/{ds_id}")
async def delete_datasource(
    ds_id: int,
    user: dict = Depends(require_admin),
):
    """删除数据源（需要管理员权限）。"""
    return await datasource_service.delete_datasource(ds_id)


@router.post("/{ds_id}/test")
async def test_connection(
    ds_id: int,
    user: dict = Depends(get_current_user),
):
    """测试数据源连接。"""
    result = await datasource_service.test_connection(ds_id)
    if not result["success"] and result["message"] == "数据源不存在":
        raise HTTPException(status_code=404, detail="数据源不存在")
    return result


@router.get("/{ds_id}/tables")
async def list_tables(
    ds_id: int,
    user: dict = Depends(get_current_user),
):
    """列出数据源中的表。"""
    try:
        return await datasource_service.list_tables(ds_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{ds_id}/tables/{table_name}/columns")
async def list_columns(
    ds_id: int,
    table_name: str,
    user: dict = Depends(get_current_user),
):
    """列出表的列信息。"""
    try:
        return await datasource_service.list_columns(ds_id, table_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{ds_id}/execute")
async def execute_sql(
    ds_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
):
    """执行 SQL 查询。"""
    sql = req.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空")

    try:
        return await datasource_service.execute_sql(ds_id, sql)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
