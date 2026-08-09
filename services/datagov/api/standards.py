"""Data Standards API — 数据标准管理."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from services.shared.common.db import execute_query, execute_insert, execute_write

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
):
    """获取数据标准列表."""
    try:
        sql = "SELECT * FROM adh_data_standards WHERE workspace_id = %s"
        params = [workspace_id]
        if standard_type:
            sql += " AND standard_type = %s"
            params.append(standard_type)
        sql += " ORDER BY created_at DESC"
        return execute_query(sql, params)
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
