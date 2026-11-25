"""Data Standards API — 数据标准管理."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from backend.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter(tags=["数据标准"])


class StandardCreate(BaseModel):
    standard_type: str
    name: str
    description: Optional[str] = None
    rule_config: dict


class StandardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rule_config: Optional[dict] = None
    is_active: Optional[int] = None


@router.get("/")
def list_standards(
    workspace_id: int = Query(0),
    standard_type: Optional[str] = None,
    q: Optional[str] = Query(None, description="名称/描述模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=200),
):
    """获取数据标准列表。

    双档契约（.qoder/rules/list-pagination-server-side.md）：
    * 不传 page_size → 返回全量数组（选择器/旧调用）；
    * 传 page_size → 返回 {items,total,page,page_size}，total 为服务端 COUNT，
      筛选/搜索下推 SQL，偏移由页号算出，排序含唯一 id 兜底（翻页不重行/漏行）。
    """
    try:
        conditions = ["workspace_id = %s"]
        params: list = [workspace_id]
        if standard_type:
            conditions.append("standard_type = %s")
            params.append(standard_type)
        if q and q.strip():
            like = f"%{q.strip()}%"
            conditions.append("(name LIKE %s OR description LIKE %s)")
            params.extend([like, like])
        where = " AND ".join(conditions)
        order = "ORDER BY created_at DESC, id DESC"

        if page_size is None:
            return execute_query(f"SELECT * FROM adh_data_standards WHERE {where} {order}", params)

        total_row = execute_query(
            f"SELECT COUNT(*) AS total FROM adh_data_standards WHERE {where}", params, fetchone=True
        )
        offset = (page - 1) * page_size
        rows = execute_query(
            f"SELECT * FROM adh_data_standards WHERE {where} {order} LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        return {
            "items": rows,
            "total": int((total_row or {}).get("total") or 0),
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.error("List standards failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_standard(req: StandardCreate, workspace_id: int = Query(0)):
    """创建数据标准."""
    try:
        std_id = execute_insert(
            """INSERT INTO adh_data_standards (workspace_id, standard_type, name, description, rule_config)
               VALUES (%s, %s, %s, %s, %s)""",
            (workspace_id, req.standard_type, req.name, req.description, json.dumps(req.rule_config)),
        )
        return {"id": std_id, "success": True}
    except Exception as e:
        logger.error("Create standard failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{standard_id}")
def update_standard(standard_id: int, req: StandardUpdate, workspace_id: int = Query(0)):
    """更新数据标准."""
    try:
        updates = []
        params = []
        if req.name is not None:
            updates.append("name = %s")
            params.append(req.name)
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if req.rule_config is not None:
            updates.append("rule_config = %s")
            params.append(json.dumps(req.rule_config))
        if req.is_active is not None:
            updates.append("is_active = %s")
            params.append(req.is_active)
        if not updates:
            return {"success": True, "message": "No changes"}
        params.extend([standard_id, workspace_id])
        execute_write(
            f"UPDATE adh_data_standards SET {', '.join(updates)} WHERE id = %s AND workspace_id = %s",
            params,
        )
        return {"success": True}
    except Exception as e:
        logger.error("Update standard failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{standard_id}")
def delete_standard(standard_id: int, workspace_id: int = Query(0)):
    """删除数据标准."""
    try:
        execute_write("DELETE FROM adh_data_standards WHERE id = %s AND workspace_id = %s", (standard_id, workspace_id))
        return {"success": True}
    except Exception as e:
        logger.error("Delete standard failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{standard_id}/check")
def check_compliance(standard_id: int, workspace_id: int = Query(0)):
    """检查数据是否符合标准（简化实现）."""
    try:
        standard = execute_query(
            "SELECT * FROM adh_data_standards WHERE id = %s AND workspace_id = %s",
            (standard_id, workspace_id),
            fetchone=True,
        )
        if not standard:
            raise HTTPException(status_code=404, detail="Standard not found")

        # Placeholder: actual compliance check depends on standard_type
        return {
            "standard_id": standard_id,
            "standard_name": standard["name"],
            "standard_type": standard["standard_type"],
            "status": "check_not_implemented",
            "message": "Compliance check requires datasource connection - implement per standard type",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Check compliance failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
