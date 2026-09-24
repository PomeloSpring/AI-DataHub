"""Dataset API — BI 治理建模层(语义对象 / SQL 双来源)的 HTTP 入口。

数据护城河(强制): 身份只信服务端 JWT(护栏 §2), 取数一律经 dataset_service 的
统一治理入口(semantic→七闸门 execute_semantic / sql→governed_execute);
无可信身份 fail-closed(NoIdentityError→403), 对外不回显 SQL/数据源/原始报错(护栏 §7)。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam

from services.shared.common.auth import get_current_user, get_workspace_id
from services.dataviz.services import dataset_service
from services.dataviz.services.governed_query import NoIdentityError, governed_execute

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────

def _identity(user: dict, workspace_id: int) -> dict:
    """服务端解析的可信身份(user_id/username/role 来自 JWT, 绝不取 body 字段)。"""
    return {
        "user_id": int(user.get("user_id") or 0),
        "username": user.get("username") or "",
        "role": user.get("role") or "",
        "workspace_id": int(workspace_id or 0),
    }


def _infer_fields(columns: list, first_row: dict) -> list:
    """由结果列/首行推断字段角色: 数值→度量, 其余→维度(详情页可调整)。"""
    fields = []
    for c in columns or []:
        v = (first_row or {}).get(c)
        role = "measure" if isinstance(v, (int, float)) and not isinstance(v, bool) else "dimension"
        fields.append({"field": str(c), "role": role, "label": str(c)})
    return fields


def _raise(e: Exception):
    """服务层异常 → HTTP 状态映射(对外仅回声明式原因, 原始栈仅入日志)。"""
    if isinstance(e, NoIdentityError):
        raise HTTPException(status_code=403, detail=str(e) or "缺少可信用户身份, 拒绝取数")
    if isinstance(e, PermissionError):
        raise HTTPException(status_code=403, detail=str(e) or "查询被安全闸门拦截")
    if isinstance(e, ValueError):
        raise HTTPException(status_code=400, detail=str(e))
    logger.error("[datasets] unexpected error: %s", e, exc_info=True)
    raise HTTPException(status_code=500, detail="数据集操作失败")


# ── SQL 校验 + 字段推断(必须在 /{dataset_id} 之前注册) ──────────────────

@router.post("/validate-sql")
def validate_sql_endpoint(
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """校验 SQL 源(仅 SELECT/WITH)并经治理入口探测字段(不回传数据行)。"""
    sql = (req.get("sql") or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    ident = _identity(user, workspace_id)
    if not ident["user_id"]:
        raise HTTPException(status_code=403, detail="缺少可信用户身份, 拒绝取数")
    try:
        dataset_service._validate_select(sql)  # 非法 SQL → ValueError
        inner = sql.rstrip(";")
        if "limit" not in inner.lower():
            inner += " LIMIT 1"
        probe = f"SELECT * FROM ({inner}) AS _ds_valid LIMIT 1"
        ds_id = int(req.get("datasource_id") or 0) or None  # 0 → 默认引擎
        result = governed_execute(
            probe, ds_id, ident["user_id"], ident["workspace_id"], ident["username"])
        first_row = (result.get("rows") or [{}])[0]
        return {"fields": _infer_fields(result.get("columns"), first_row),
                "row_count": result.get("row_count", 0)}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        _raise(e)


# ── CRUD ───────────────────────────────────────────────────────────────

@router.get("/")
def list_datasets(
    keyword: str = QueryParam("", description="名称/描述/对象 关键字"),
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """按可见性列出数据集。"""
    try:
        items = dataset_service.list_datasets(_identity(user, workspace_id), keyword=keyword)
        return {"items": items}
    except Exception as e:  # noqa: BLE001
        _raise(e)


@router.post("/")
def create_dataset(
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """新建数据集(语义对象或 SQL 源)。"""
    try:
        ds = dataset_service.create_dataset(req, _identity(user, workspace_id))
        return ds
    except Exception as e:  # noqa: BLE001
        _raise(e)


@router.get("/{dataset_id}")
def get_dataset(dataset_id: int):
    """数据集详情: 附字段定义(semantic 动态解析 / sql 读 field_config)与看板引用。"""
    ds = dataset_service.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据集不存在")
    ds["fields"] = dataset_service.resolve_fields(ds)
    ds["references"] = dataset_service._references(dataset_id)
    return ds


@router.put("/{dataset_id}")
def update_dataset(
    dataset_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """更新数据集(仅创建者/管理员; SQL 变更重新校验)。"""
    try:
        return dataset_service.update_dataset(dataset_id, req, _identity(user, workspace_id))
    except Exception as e:  # noqa: BLE001
        _raise(e)


@router.delete("/{dataset_id}")
def delete_dataset(
    dataset_id: int,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """删除数据集(被看板引用时拒绝)。"""
    try:
        return dataset_service.delete_dataset(dataset_id, _identity(user, workspace_id))
    except Exception as e:  # noqa: BLE001
        _raise(e)


# ── 行级范围(分享时生效; 只收紧不放宽) ──────────────────────────────────

@router.get("/{dataset_id}/scopes")
def list_scopes(dataset_id: int):
    """列出数据集行级范围配置(全量替换语义)。"""
    try:
        return {"scopes": dataset_service.list_scopes(dataset_id)}
    except Exception as e:  # noqa: BLE001
        _raise(e)


@router.put("/{dataset_id}/scopes")
def set_scopes(
    dataset_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """全量替换数据集行范围: {scopes:[{subject_type, subject_id, filters:[{field,op,value}]}]}。"""
    try:
        return dataset_service.set_scopes(dataset_id, req.get("scopes") or [],
                                          _identity(user, workspace_id))
    except Exception as e:  # noqa: BLE001
        _raise(e)


# ── 统一治理取数(看板/预览/Chat 共用) ──────────────────────────────────

@router.post("/{dataset_id}/preview")
def preview_dataset(
    dataset_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """按当前身份预览取数: 权限/RLS/敏感屏蔽/审计/行级范围全部服务端施加。"""
    try:
        params = {
            "dimensions": req.get("dimensions") or [],
            "measures": req.get("measures") or req.get("metrics") or [],
            "filters": req.get("filters") or [],
            "order": req.get("order") or [],
            "limit": req.get("limit") or 100,
        }
        return dataset_service.query_dataset(dataset_id, params, _identity(user, workspace_id))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        _raise(e)
