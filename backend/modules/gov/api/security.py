"""Sensitive Data Management API — 敏感数据标记和脱敏."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from backend.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter(tags=["数据安全"])

# 与 adh_sensitive_fields.mask_type 枚举一致
# block = 不能查询出来(合规屏蔽基线): 结果剔除 + 显式请求拒绝, 对所有用户含管理员生效
MASK_TYPES = ("full", "partial", "hash", "none", "block")


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
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """获取敏感字段列表（服务端分页，返回 {items, total} 供统一分页条使用）.

    分页口径与全站列表一致：默认每页 20，上限 100；total 必须是筛选后的总数，
    否则前端分页条算不出页数（历史缺陷：只回数组无 total，分页功能等于没做）。
    """
    try:
        where = "WHERE workspace_id IN (%s, 0)"
        params: list = [workspace_id]
        if datasource_id is not None:
            # 取"全局(datasource_id=0) ∪ 指定数据源"并集, 与执行侧加载口径一致
            where += " AND datasource_id IN (%s, 0)"
            params.append(datasource_id)
        if sensitivity_level:
            where += " AND sensitivity_level = %s"
            params.append(sensitivity_level)

        count_rows = execute_query(f"SELECT COUNT(*) AS total FROM adh_sensitive_fields {where}", params)
        total = int((count_rows or [{}])[0].get("total") or 0)
        rows = execute_query(
            f"SELECT * FROM adh_sensitive_fields {where}"
            " ORDER BY sensitivity_level DESC, table_name, column_name LIMIT %s OFFSET %s",
            params + [size, (page - 1) * size],
        )
        return {"items": rows or [], "total": total, "page": page, "size": size}
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
