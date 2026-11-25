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
# block = 不能查询出来(合规屏蔽基线): 结果剔除 + 显式请求拒绝, 对所有用户含管理 员生效
MASK_TYPES = ("full", "partial", "hash", "none", "block")

# 与 adh_sensitive_fields.sensitivity_level 枚举一致（选项顺序 = 前端展示顺序）
SENSITIVITY_LEVELS = ("low", "medium", "high", "critical")

# 规则生效范围：global = 只按列名匹配（table_name 为空，对所有表生效）；table = 指定表规则
SENSITIVE_SCOPES = ("global", "table")


def _escape_like(value: str) -> str:
    """LIKE 元字符转义后包成包含式模式（未转义的 % _ 会变成通配符，误命中且无法排查）。"""
    esc = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _sensitive_where(workspace_id: int, *, datasource_id: Optional[int] = None,
                     sensitivity_level: str = "", mask_type: str = "",
                     keyword: str = "", scope: str = "",
                     skip: tuple = ()) -> tuple:
    """敏感字段清单与筛选项（facets）共用的唯一谓词构造 —— 两档口径必须同源。

    非法枚举值一律 ValueError（带候选），不得静默当"不限"：筛错了却看到全量
    会让管理员以为筛选生效（no-silent-degradation）。

    datasource_id 沿执行侧语义：传 0 = 只看全局规则；传 N = 全局 ∪ 该数据源
    （对该源生效的规则集合）；不传 = 不限。

    skip 仅供 facets 统计使用：算某一维度的候选/计数时剔除该维度自身的条件，
    否则下拉里永远只剩当前选中项。
    """
    level = str(sensitivity_level or "").strip().lower()
    if level and level not in SENSITIVITY_LEVELS:
        raise ValueError(f"不支持的敏感级别：{sensitivity_level}；可选 "
                         + " / ".join(SENSITIVITY_LEVELS))
    mt = str(mask_type or "").strip().lower()
    if mt and mt not in MASK_TYPES:
        raise ValueError(f"不支持的脱敏方式：{mask_type}；可选 " + " / ".join(MASK_TYPES))
    sc = str(scope or "").strip().lower()
    if sc and sc not in SENSITIVE_SCOPES:
        raise ValueError(f"不支持的生效范围：{scope}；可选 " + " / ".join(SENSITIVE_SCOPES)
                         + "（global=全局列名规则，table=指定表规则）")

    where = "WHERE workspace_id IN (%s, 0)"
    params: list = [workspace_id]
    if datasource_id is not None and "datasource_id" not in skip:
        where += " AND datasource_id IN (%s, 0)"
        params.append(int(datasource_id))
    if level and "sensitivity_level" not in skip:
        where += " AND sensitivity_level = %s"
        params.append(level)
    if mt and "mask_type" not in skip:
        where += " AND mask_type = %s"
        params.append(mt)
    if sc and "scope" not in skip:
        where += " AND IFNULL(table_name, '') = ''" if sc == "global" \
            else " AND IFNULL(table_name, '') <> ''"
    if keyword and "keyword" not in skip:
        pattern = _escape_like(str(keyword).strip())
        where += (" AND (IFNULL(table_name, '') LIKE %s OR IFNULL(column_name, '') LIKE %s"
                  " OR IFNULL(description, '') LIKE %s)")
        params += [pattern, pattern, pattern]
    return where, params


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
    sensitivity_level: str = Query("", description="敏感级别：''=全部，low/medium/high/critical"),
    mask_type: str = Query("", description="脱敏方式：''=全部，block/full/partial/hash/none"),
    keyword: str = Query("", description="按表名/字段名/说明模糊搜索"),
    scope: str = Query("", description="生效范围：''=全部，global=全局列名规则，table=指定表规则"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """获取敏感字段列表（服务端分页，返回 {items, total} 供统一分页条使用）.

    分页口径与全站列表一致：默认每页 20，上限 100；total 必须是筛选后的总数，
    否则前端分页条算不出页数（历史缺陷：只回数组无 total，分页功能等于没做）。
    筛选全部下推 SQL（关键字/级别/脱敏方式/数据源/生效范围），不在前端二次切片。
    """
    try:
        where, params = _sensitive_where(
            workspace_id, datasource_id=datasource_id, sensitivity_level=sensitivity_level,
            mask_type=mask_type, keyword=keyword, scope=scope)

        count_rows = execute_query(f"SELECT COUNT(*) AS total FROM adh_sensitive_fields {where}", params)
        total = int((count_rows or [{}])[0].get("total") or 0)
        rows = execute_query(
            f"SELECT * FROM adh_sensitive_fields {where}"
            " ORDER BY sensitivity_level DESC, table_name, column_name LIMIT %s OFFSET %s",
            params + [size, (page - 1) * size],
        )
        return {"items": rows or [], "total": total, "page": page, "size": size}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("List sensitive fields failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sensitive-fields/facets")
def sensitive_field_facets(
    workspace_id: int = Query(0),
    datasource_id: Optional[int] = None,
    sensitivity_level: str = Query(""),
    mask_type: str = Query(""),
    keyword: str = Query(""),
    scope: str = Query(""),
):
    """筛选项候选与计数（服务端下发，与清单共用 _sensitive_where 谓词）。

    - 每个维度统计时剔除该维度自身的条件（否则下拉只剩当前选中项）；
    - 全部档位恒定返回（计数可为 0），前端才能给出稳定的「全部 X（0）」；
    - `datasources` 只回有行命中的数据源 id + 计数（名称由 /datasources/ 清单映射，
      本接口不编造名称），0 = 全局规则。
    """

    def _count_by(expr: str, skip: tuple) -> dict:
        where, params = _sensitive_where(
            workspace_id, datasource_id=datasource_id, sensitivity_level=sensitivity_level,
            mask_type=mask_type, keyword=keyword, scope=scope, skip=skip)
        rows = execute_query(
            f"SELECT {expr} AS v, COUNT(*) AS n FROM adh_sensitive_fields {where} GROUP BY {expr}",
            params) or []
        return {str(r.get("v")): int(r.get("n") or 0) for r in rows}

    try:
        by_level = _count_by("sensitivity_level", ("sensitivity_level",))
        by_mask = _count_by("mask_type", ("mask_type",))
        # 生效范围：table_name 为空 = 全局列名规则
        by_scope = _count_by("IFNULL(table_name, '') = ''", ("scope",))
        by_ds = _count_by("datasource_id", ("datasource_id",))
        where, params = _sensitive_where(
            workspace_id, datasource_id=datasource_id, sensitivity_level=sensitivity_level,
            mask_type=mask_type, keyword=keyword, scope=scope)
        total_rows = execute_query(
            f"SELECT COUNT(*) AS total FROM adh_sensitive_fields {where}", params)
        return {
            "levels": [{"value": lv, "count": by_level.get(lv, 0)}
                       for lv in SENSITIVITY_LEVELS],
            "mask_types": [{"value": mt, "count": by_mask.get(mt, 0)}
                           for mt in MASK_TYPES],
            "scopes": [{"value": "global", "count": by_scope.get("1", 0)},
                       {"value": "table", "count": by_scope.get("0", 0)}],
            "datasources": [{"datasource_id": int(ds or 0), "count": n}
                            for ds, n in sorted(by_ds.items(), key=lambda kv: int(kv[0] or 0))],
            "total": int((total_rows or [{}])[0].get("total") or 0),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Sensitive field facets failed: %s", e)
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
