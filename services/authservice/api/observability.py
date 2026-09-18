"""Observability API — 管理端 LLM 交互可观测（只读）。

挂载于 authservice（prefix=/api/observability）。全部 require_admin 把关。
提供：概览 KPI + 趋势 / 模型用量 / 用户用量 / 会话清单 / Trace 列表 / Trace 详情（含各产物 span）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from services.shared.common.auth import require_admin
from services.authservice.services import observability_service as obs

router = APIRouter()


@router.get("/usage/summary")
def usage_summary(
    user_id: int = Query(0), workspace_id: int = Query(0), datasource_id: int = Query(0),
    entrypoint: str = Query(""), status: str = Query(""), model_ref: str = Query(""),
    start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """概览：回合/会话/用户/请求/token/credit/成本/错误率 + 满意率 + 按日趋势。"""
    return obs.usage_summary({
        "user_id": user_id, "workspace_id": workspace_id, "datasource_id": datasource_id,
        "entrypoint": entrypoint, "status": status, "model_ref": model_ref,
        "start": start, "end": end,
    })


@router.get("/usage/models")
def usage_models(
    user_id: int = Query(0), workspace_id: int = Query(0), datasource_id: int = Query(0),
    entrypoint: str = Query(""), status: str = Query(""), start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """模型维度用量（credit/token 排序）。"""
    return {"items": obs.usage_by_model({
        "user_id": user_id, "workspace_id": workspace_id, "datasource_id": datasource_id,
        "entrypoint": entrypoint, "status": status, "start": start, "end": end,
    })}


@router.get("/usage/users")
def usage_users(
    workspace_id: int = Query(0), datasource_id: int = Query(0), entrypoint: str = Query(""),
    status: str = Query(""), start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """用户维度用量 rollup。"""
    return {"items": obs.usage_by_user({
        "workspace_id": workspace_id, "datasource_id": datasource_id,
        "entrypoint": entrypoint, "status": status, "start": start, "end": end,
    })}


@router.get("/sessions")
def sessions(
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200),
    user_id: int = Query(0), workspace_id: int = Query(0), datasource_id: int = Query(0),
    entrypoint: str = Query(""), model_ref: str = Query(""),
    start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """跨用户会话清单（按 conversation 聚合，含标题/回合数/token/credit/错误/活跃）。"""
    return obs.list_sessions({
        "page": page, "size": size, "user_id": user_id, "workspace_id": workspace_id,
        "datasource_id": datasource_id, "entrypoint": entrypoint, "model_ref": model_ref,
        "start": start, "end": end,
    })


@router.get("/traces")
def traces(
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200),
    conversation_id: int = Query(0), user_id: int = Query(0), workspace_id: int = Query(0),
    datasource_id: int = Query(0), entrypoint: str = Query(""), status: str = Query(""),
    model_ref: str = Query(""), start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """回合级 Trace 列表（可按会话/用户/状态/时间过滤）。"""
    return obs.list_traces({
        "page": page, "size": size, "conversation_id": conversation_id, "user_id": user_id,
        "workspace_id": workspace_id, "datasource_id": datasource_id, "entrypoint": entrypoint,
        "status": status, "model_ref": model_ref, "start": start, "end": end,
    })


@router.get("/traces/{trace_id}")
def trace_detail(trace_id: str, admin: dict = Depends(require_admin)):
    """单条 Trace 详情：摘要 + 其全部 span（语义层/权限前后 SQL/执行输出/工具产物）+ 赞踩反馈。"""
    detail = obs.get_trace(trace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return detail
