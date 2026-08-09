"""Sensitive Data Management API — 敏感数据标记和脱敏."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from services.shared.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter(tags=["数据安全"])

# 与 adh_sensitive_fields.mask_type 枚举一致
MASK_TYPES = ("full", "partial", "hash", "none")


class SensitiveFieldCreate(BaseModel):
    datasource_id: int
    table_name: str
    column_name: str
    sensitivity_level: str = "medium"
    mask_type: str = "partial"
    mask_config: Optional[dict] = None
    description: Optional[str] = None


class SensitiveFieldUpdate(BaseModel):
    sensitivity_level: Optional[str] = None
    mask_type: Optional[str] = None
    mask_config: Optional[dict] = None
    description: Optional[str] = None


@router.get("/sensitive-fields")
def list_sensitive_fields(
    workspace_id: int = Query(0),
    datasource_id: Optional[int] = None,
    sensitivity_level: Optional[str] = None,
):
    """获取敏感字段列表."""
    try:
        sql = "SELECT * FROM adh_sensitive_fields WHERE workspace_id = %s"
        params = [workspace_id]
        if datasource_id:
            sql += " AND datasource_id = %s"
            params.append(datasource_id)
        if sensitivity_level:
            sql += " AND sensitivity_level = %s"
            params.append(sensitivity_level)
        sql += " ORDER BY sensitivity_level DESC, table_name, column_name"
        return execute_query(sql, params)
    except Exception as e:
        logger.error("List sensitive fields failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensitive-fields")
def create_sensitive_field(req: SensitiveFieldCreate, workspace_id: int = Query(0)):
    """标记敏感字段."""
    if req.mask_type not in MASK_TYPES:
        raise HTTPException(status_code=400, detail=f"mask_type 必须是: {', '.join(MASK_TYPES)}")
    try:
        field_id = execute_insert(
            """INSERT INTO adh_sensitive_fields
               (workspace_id, datasource_id, table_name, column_name, sensitivity_level, mask_type, mask_config, description)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (workspace_id, req.datasource_id, req.table_name, req.column_name,
             req.sensitivity_level, req.mask_type, json.dumps(req.mask_config) if req.mask_config else None, req.description),
        )
        return {"id": field_id, "success": True}
    except Exception as e:
        logger.error("Create sensitive field failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sensitive-fields/{field_id}")
def update_sensitive_field(field_id: int, req: SensitiveFieldUpdate, workspace_id: int = Query(0)):
    """更新敏感字段配置."""
    try:
        updates = []
        params = []
        if req.sensitivity_level is not None:
            updates.append("sensitivity_level = %s")
            params.append(req.sensitivity_level)
        if req.mask_type is not None:
            updates.append("mask_type = %s")
            params.append(req.mask_type)
        if req.mask_config is not None:
            updates.append("mask_config = %s")
            params.append(json.dumps(req.mask_config))
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if not updates:
            return {"success": True}
        params.extend([field_id, workspace_id])
        execute_write(f"UPDATE adh_sensitive_fields SET {', '.join(updates)} WHERE id = %s AND workspace_id = %s", params)
        return {"success": True}
    except Exception as e:
        logger.error("Update sensitive field failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sensitive-fields/{field_id}")
def delete_sensitive_field(field_id: int, workspace_id: int = Query(0)):
    """移除敏感标记."""
    try:
        execute_write("DELETE FROM adh_sensitive_fields WHERE id = %s AND workspace_id = %s", (field_id, workspace_id))
        return {"success": True}
    except Exception as e:
        logger.error("Delete sensitive field failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan")
def scan_sensitive_fields(
    datasource_id: int,
    workspace_id: int = Query(0),
    persist: bool = Query(False, description="将扫描结果写入敏感字段标记（已存在的不覆盖）"),
):
    """自动扫描敏感字段（基于关键词匹配列名和注释），可选落库."""
    try:
        # Sensitive patterns to match
        patterns = {
            "phone": ["phone", "mobile", "tel", "手机", "电话"],
            "email": ["email", "mail", "邮箱"],
            "id_card": ["id_card", "identity", "身份证", "证件号"],
            "name": ["real_name", "true_name", "姓名", "真实姓名"],
            "address": ["address", "addr", "地址", "住址"],
            "bank_card": ["bank_card", "card_no", "银行卡"],
            "password": ["password", "pwd", "密码", "secret"],
        }

        # Get all columns for this datasource
        columns = execute_query(
            "SELECT id, table_name, column_name, column_comment FROM adh_column_metadata WHERE datasource_id = %s AND is_active = 1",
            (datasource_id,),
        )

        found = []
        for col in columns:
            col_name_lower = (col["column_name"] or "").lower()
            comment_lower = (col.get("column_comment") or "").lower()
            combined = f"{col_name_lower} {comment_lower}"

            for category, keywords in patterns.items():
                if any(kw in combined for kw in keywords):
                    found.append({
                        "table_name": col["table_name"],
                        "column_name": col["column_name"],
                        "category": category,
                        "sensitivity_level": "high" if category in ("id_card", "password", "bank_card") else "medium",
                    })
                    break

        persisted = 0
        if persist and found:
            for f in found:
                try:
                    execute_insert(
                        """INSERT IGNORE INTO adh_sensitive_fields
                           (workspace_id, datasource_id, table_name, column_name,
                            sensitivity_level, mask_type, description)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (workspace_id, datasource_id, f["table_name"], f["column_name"],
                         f["sensitivity_level"], "partial", f"自动扫描识别({f['category']})"),
                    )
                    persisted += 1
                except Exception as e:
                    logger.warning("Persist sensitive field failed: %s", e)

        return {
            "scanned_columns": len(columns),
            "found_sensitive": len(found),
            "persisted": persisted if persist else None,
            "fields": found,
        }
    except Exception as e:
        logger.error("Scan sensitive fields failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
