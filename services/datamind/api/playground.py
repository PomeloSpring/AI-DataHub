"""Playground API — SQL 可观测分析 + 受治理的交互式取数(Phase 6 / data-moat)。

Playground 定位: 不是数据库直连工具 —— 返回数据行的执行一律**继承当前用户角色
权限**, 与可视化大屏 raw_sql 同源: 经统一护城河 execute_query_with_permission
(permission_enforcer: 敏感 block 剔除 / mask 脱敏 + RLS 行级 + RBAC + 审计), 身份
只信服务端 JWT, 无可信身份 -> fail-closed。静态/预览分析走语义层。本模块对前端
保留路径 `/api/playground/*`:
  - execute -> 受治理取数(当前用户角色权限), 本服务内经护城河执行
  - ast/lineage/rls-diff/provenance -> 透传语义层(不返回数据行)
  - rls-diff 以服务端 JWT 身份签发可信内部头透传, 供语义层解析该用户的改写预览
  - 已保存查询(adh_saved_queries)CRUD 与语义无关, 仍由本服务落库

Tables: adh_saved_queries
"""

import logging
import os
import time
from datetime import datetime
from typing import Any, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.auth import get_current_user, get_workspace_id, sign_internal_identity
from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()

SEMANTIC_SERVICE_URL = os.getenv("SEMANTIC_SERVICE_URL", "http://localhost:8012").rstrip("/")
_PG_PREFIX = "/api/semantic/playground"


def _proxy_pg(path: str, body: dict[str, Any], timeout: int = 60, headers: Optional[dict] = None):
    """POST 到语义层 playground。返回 (status_code, json_or_text, conn_error)。
    headers: 透传可信内部身份头(仅 rls-diff 预览需要), 下游据此解析 user, 不信任 body。"""
    url = f"{SEMANTIC_SERVICE_URL}{_PG_PREFIX}{path}"
    try:
        r = requests.post(url, json=body, timeout=timeout, headers=headers or None)
    except requests.exceptions.RequestException as e:  # noqa: BLE001
        return 0, str(e), True
    try:
        return r.status_code, r.json(), False
    except Exception:  # noqa: BLE001
        return r.status_code, r.text, False


def _unwrap_detail(payload: Any) -> Any:
    """语义层 FastAPI 错误体 {"detail": ...} 解包, 避免代理二次包裹。"""
    if isinstance(payload, dict) and set(payload.keys()) == {"detail"}:
        return payload["detail"]
    return payload


class SavedQueryCreate(BaseModel):
    name: str
    description: str = ""
    sql_query: str
    is_dataset: bool = False
    dataset_keywords: str = ""


class SavedQueryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sql_query: Optional[str] = None
    is_dataset: Optional[bool] = None
    dataset_keywords: Optional[str] = None


# ── 可观测能力: 纯透传语义层 Playground ──────────────────────────

def _proxy_or_502(path: str, req: dict, headers: Optional[dict] = None) -> dict:
    status, payload, conn_err = _proxy_pg(path, req, headers=headers)
    if conn_err:
        raise HTTPException(status_code=502, detail=f"语义层不可达: {payload}")
    if status >= 400:
        raise HTTPException(status_code=status, detail=_unwrap_detail(payload))
    return payload


@router.post("/ast")
def ast_via_playground(req: dict):
    """SQL -> JSON AST + 引用表(语义层 sqlglot)。"""
    return _proxy_or_502("/ast", req)


@router.post("/lineage")
def lineage_via_playground(req: dict):
    """SQL -> 列级血缘(语义层 sqlglot.lineage)。"""
    return _proxy_or_502("/lineage", req)


@router.post("/rls-diff")
def rls_diff_via_playground(req: dict, user: dict = Depends(get_current_user)):
    """baseSql vs securedSql 行级+列级改写预览(不返回数据行)。
    身份取自 JWT(服务端解析), 经签名内部头透传语义层; body 里的 user_id/workspace_id 不信任。"""
    body = {k: v for k, v in req.items() if k not in ("user_id", "workspace_id")}
    headers = {"X-Internal-Identity": sign_internal_identity(
        user.get("user_id") or 0, req.get("workspace_id") or 0)}
    return _proxy_or_502("/rls-diff", body, headers)


@router.post("/provenance")
def provenance_via_playground(req: dict):
    """object -> binding / query_mode / size_class 溯源。"""
    return _proxy_or_502("/provenance", req)


@router.post("/execute")
def execute_via_playground(
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """交互式 SQL 执行 —— 继承当前用户角色权限(与可视化大屏 raw_sql 同源)。

    本平台不是数据库直连工具: 取数一律经统一护城河 `execute_query_with_permission`
    (permission_enforcer: 敏感 block 列剔除 / mask 脱敏 + RLS 行级过滤 + RBAC + 审计),
    身份只信服务端 JWT(I2), 无可信身份 -> fail-closed 4xx(I5)。仅只读语句。
    """
    from services.datamind.nl2sql.sql.query_executor import (
        execute_query_with_permission, validate_sql,
    )

    sql = (req.get("sql") or "").strip().rstrip(";")
    if not sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空")

    ok, msg = validate_sql(sql, require_limit=False)
    if not ok:
        raise HTTPException(status_code=400, detail=f"SQL 校验失败: {msg}")

    user_id = user.get("user_id") or 0
    if not user_id:
        # I5: 无可信身份一律拒绝, 不回退到无过滤的裸执行
        raise HTTPException(status_code=403, detail="缺少可信用户身份, 拒绝取数(数据合规护城河)")

    ds_raw = req.get("datasource_id")
    try:
        datasource_id = int(ds_raw) if ds_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        datasource_id = None

    t0 = time.time()
    try:
        df, elapsed_ms, row_count = execute_query_with_permission(
            sql,
            datasource_id=datasource_id,
            query_type="sql",
            user_context={"user_id": user_id, "username": user.get("username", "") or ""},
            workspace_id=int(workspace_id or 0),
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"权限拦截: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"SQL 校验失败: {e}")
    except Exception as e:  # noqa: BLE001
        logger.warning("Playground execute failed: %s", e)
        raise HTTPException(status_code=400, detail=f"SQL 执行失败: {e}")

    from services.shared.common.df_serialize import df_to_columns_rows
    # 关联查询(t1.*, t2.*)会带重复列名, 统一走无损消歧序列化, 避免 to_json 500
    columns, rows = df_to_columns_rows(df)
    if elapsed_ms is None:
        elapsed_ms = int((time.time() - t0) * 1000)
    return {"columns": columns, "rows": rows, "row_count": row_count, "elapsed_ms": elapsed_ms}


@router.get("/queries")
def list_queries(
    is_dataset: Optional[int] = Query(None),
    workspace_id: int = Query(0),
):
    """List saved queries."""
    try:
        conditions = ["workspace_id = %s"]
        params = [workspace_id]

        if is_dataset is not None:
            conditions.append("is_dataset = %s")
            params.append(is_dataset)

        where = " AND ".join(conditions)
        rows = execute_query(
            f"SELECT * FROM adh_saved_queries WHERE {where} ORDER BY updated_at DESC",
            params,
        )
        for r in rows:
            for k in ("created_at", "updated_at"):
                if hasattr(r.get(k), "isoformat"):
                    r[k] = r[k].isoformat()
        return rows
    except Exception as e:
        logger.error("List queries failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queries")
def create_query(req: SavedQueryCreate, workspace_id: int = Query(0)):
    """Create saved query."""
    try:
        qid = int(time.time() * 1000)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute_insert(
            """INSERT INTO adh_saved_queries
               (id, name, description, sql_query, is_dataset, dataset_keywords, workspace_id, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (qid, req.name, req.description, req.sql_query,
             1 if req.is_dataset else 0, req.dataset_keywords, workspace_id, now, now),
        )
        return {"id": qid}
    except Exception as e:
        logger.error("Create query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/queries/{query_id}")
def update_query(query_id: int, req: SavedQueryUpdate):
    """Update saved query."""
    try:
        updates = []
        params = []
        if req.name is not None:
            updates.append("name = %s")
            params.append(req.name)
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if req.sql_query is not None:
            updates.append("sql_query = %s")
            params.append(req.sql_query)
        if req.is_dataset is not None:
            updates.append("is_dataset = %s")
            params.append(1 if req.is_dataset else 0)
        if req.dataset_keywords is not None:
            updates.append("dataset_keywords = %s")
            params.append(req.dataset_keywords)

        if not updates:
            return {"success": True}

        updates.append("updated_at = %s")
        params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        params.append(query_id)

        execute_write(f"UPDATE adh_saved_queries SET {', '.join(updates)} WHERE id = %s", params)
        return {"success": True}
    except Exception as e:
        logger.error("Update query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/queries/{query_id}")
def delete_query(query_id: int):
    """Delete saved query."""
    try:
        execute_write("DELETE FROM adh_saved_queries WHERE id = %s", (query_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
