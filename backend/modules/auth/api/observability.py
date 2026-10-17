"""Observability API — 管理端 LLM 交互可观测（只读）。

挂载于 authservice（prefix=/api/observability）。全部 require_admin 把关。
提供：概览 KPI + 趋势 / 模型用量 / 用户用量 / 会话清单 / Trace 列表 / Trace 详情（含各产物 span）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.common.auth import require_admin
from backend.modules.auth.services import observability_service as obs

router = APIRouter()


@router.get("/usage/summary")
def usage_summary(
    user_id: int = Query(0), workspace_id: int = Query(0), datasource_id: int = Query(0),
    entrypoint: str = Query(""), status: str = Query(""), model_ref: str = Query(""),
    handled_by: str = Query("", description="处理方式: agent=走了模型 / intent_gate=前置意图层直答"),
    start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """概览：回合/会话/用户/请求/token/credit/成本/错误率 + 满意率 + 拦截率 + 按日趋势。"""
    return obs.usage_summary({
        "user_id": user_id, "workspace_id": workspace_id, "datasource_id": datasource_id,
        "entrypoint": entrypoint, "status": status, "model_ref": model_ref,
        "handled_by": handled_by, "start": start, "end": end,
    })


@router.get("/funnel/stats")
def funnel_stats(
    workspace_id: int = Query(0), user_id: int = Query(0), datasource_id: int = Query(0),
    conversation_id: int = Query(0), as_bot_key: str = Query(""), mode: str = Query(""),
    label: str = Query(""), decision: str = Query(""),
    start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """前置意图漏斗判定统计（拦截率/可拦比例/按 label 分布/网关异常数）。

    表不可达时返回 `available=false` + 原因（提示跑迁移），不拿空数据冒充"一切正常"。
    """
    return obs.funnel_stats({
        "workspace_id": workspace_id, "user_id": user_id, "datasource_id": datasource_id,
        "conversation_id": conversation_id, "as_bot_key": as_bot_key, "mode": mode,
        "label": label, "decision": decision, "start": start, "end": end,
    })


@router.get("/funnel/intercepts")
def funnel_intercepts(
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200),
    workspace_id: int = Query(0), user_id: int = Query(0), datasource_id: int = Query(0),
    conversation_id: int = Query(0), as_bot_key: str = Query(""), mode: str = Query(""),
    label: str = Query(""), decision: str = Query(""),
    start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """判定流水（误拦复盘）：每一轮的分流判定、依据与输入摘要。"""
    return obs.funnel_intercepts({
        "page": page, "size": size,
        "workspace_id": workspace_id, "user_id": user_id, "datasource_id": datasource_id,
        "conversation_id": conversation_id, "as_bot_key": as_bot_key, "mode": mode,
        "label": label, "decision": decision, "start": start, "end": end,
    })


class InterceptReviewRequest(BaseModel):
    reviewed: bool = True
    note: str = ""


@router.post("/funnel/intercepts/{intercept_id}/review")
def review_intercept(
    intercept_id: int,
    req: InterceptReviewRequest,
    admin: dict = Depends(require_admin),
):
    """误拦复盘标注：这条判定是否站得住（后续阈值迭代的依据数据）。"""
    obs.review_intercept(intercept_id, req.reviewed, req.note)
    return {"ok": True, "id": intercept_id, "reviewed": 1 if req.reviewed else 0}


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


@router.get("/quota")
async def quota(refresh: bool = Query(False), admin: dict = Depends(require_admin)):
    """Qoder 账号 credit 配额（执行层实测值，账号级真实消耗）。

    Qoder 不回报美元成本，credit 配额是唯一可审计的实际消耗口径；
    读取失败返回 `available=false` + 原因（前端可见），不返回空数据掩盖。
    """
    from backend.modules.mind.execution.qoder_quota import read_quota

    return await read_quota(refresh=refresh)


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
    model_ref: str = Query(""), handled_by: str = Query(""),
    start: str = Query(""), end: str = Query(""),
    admin: dict = Depends(require_admin),
):
    """回合级 Trace 列表（可按会话/用户/状态/处理方式/时间过滤）。"""
    return obs.list_traces({
        "page": page, "size": size, "conversation_id": conversation_id, "user_id": user_id,
        "workspace_id": workspace_id, "datasource_id": datasource_id, "entrypoint": entrypoint,
        "status": status, "model_ref": model_ref, "handled_by": handled_by,
        "start": start, "end": end,
    })


@router.get("/traces/{trace_id}")
def trace_detail(trace_id: str, admin: dict = Depends(require_admin)):
    """单条 Trace 详情：摘要 + 其全部 span（语义层/权限前后 SQL/执行输出/工具产物）+ 赞踩反馈。"""
    detail = obs.get_trace(trace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return detail
