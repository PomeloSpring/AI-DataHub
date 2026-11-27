"""能力流程 API — 受控 auto 场景的运行/观察/取消/人工复核（P3-4）。

挂载: ``/api/capability-flows``（backend/app/main.py，platform 组）

权限门控（**唯一裁决点是 API 权限中间件**，见 backend/common/api_permission.py）：

=====================  ============================  =========================
权限码                  api_pattern / method          端点
=====================  ============================  =========================
``capability:run``     GET,POST /api/capability-flows*  运行清单/详情/创建/取消/复核
=====================  ============================  =========================

``capability:run`` 是**编排入口权限**（创建/观察/取消流程），不是执行权限：
创建前服务层预检全部自动步骤权限（缺任一即拒，不建半截运行），每个自动步骤
执行时仍由该动作原有权限码再次裁决（capability_runtime 的 require_write_perm）。

身份一律取服务端 JWT（护栏 §2）：发起人/操作者字段由服务端注入，不信任请求体。
人工步骤复核（resume）读**内容服务状态**确认发布事实，不接受客户端"已完成"宣告。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.common.auth import (
    authorize_workspace, get_current_user, resolve_user_default_workspace_id,
)
from backend.modules.platform.services import capability_flow_service as flows

logger = logging.getLogger("api.capability_flows")
router = APIRouter()


class RunIn(BaseModel):
    scenario: str
    input: dict = {}


def _flow_error(exc: flows.FlowError) -> HTTPException:
    return HTTPException(status_code=404 if exc.code == "RUN_NOT_FOUND"
                         else 409 if exc.code in ("IDEMPOTENCY_CONFLICT", "RUN_TERMINAL",
                                                  "RUN_NOT_WAITING", "HUMAN_PENDING")
                         else 403 if exc.code == "PERMISSION_DENIED"
                         else 422 if exc.code in ("SCHEMA_INVALID", "QUERY_INVALID",
                                                  "BINDING_UNRESOLVED")
                         else 503 if exc.code in ("FLOW_DISABLED", "SCENARIO_NOT_ALLOWLISTED",
                                                  "SCENARIO_NOT_AUTO")
                         else 400, detail=str(exc))


async def _workspace(user: dict = Depends(get_current_user)) -> int:
    ws = await run_in_threadpool(resolve_user_default_workspace_id,
                                 int(user.get("user_id") or 0))
    ws = authorize_workspace(user, ws)
    if ws <= 0:
        raise HTTPException(status_code=403, detail="能力流程需要已授权工作空间")
    return ws


@router.post("/runs")
async def create_run(body: RunIn, request_id: str = Header(default="", alias="Idempotency-Key"),
                     user: dict = Depends(get_current_user),
                     workspace_id: int = Depends(_workspace)):
    """创建并启动流程运行（幂等键唯一；重复请求返回首次运行并标 replayed）。"""
    try:
        return await run_in_threadpool(
            flows.create_run,
            scenario_key=body.scenario, input_payload=body.input,
            idempotency_key=request_id,
            user_id=int(user.get("user_id") or 0),
            username=str(user.get("username") or ""), workspace_id=workspace_id)
    except flows.FlowError as exc:
        raise _flow_error(exc) from exc


@router.get("/runs")
def list_runs(page: int = Query(1, ge=1),
              page_size: int = Query(20, ge=1, le=50),
              status: str = Query(""),
              q: str = Query("")):
    """运行清单（服务端真分页；total 来自 COUNT，排序含 id tiebreaker）。"""
    try:
        return flows.list_runs(page=page, page_size=page_size, status=status, q=q)
    except flows.FlowError as exc:
        raise _flow_error(exc) from exc


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    """运行详情：步骤（含人工入口与失败原因）+ 事件账（安全摘要）。"""
    run = flows.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="流程运行不存在")
    return run


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, user: dict = Depends(get_current_user),
                     workspace_id: int = Depends(_workspace)):
    """取消运行：不自动删除已创建业务数据（动作无补偿器声明），半态留可诊断。"""
    try:
        return await run_in_threadpool(
            flows.cancel_run, run_id,
            actor=str(user.get("username") or ""),
            user_id=int(user.get("user_id") or 0), workspace_id=workspace_id)
    except flows.FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: int, user: dict = Depends(get_current_user)):
    """人工步骤完成后复核推进：读内容服务状态确认发布事实（不信任客户端宣告）。"""
    try:
        return await run_in_threadpool(
            flows.resume_human_step, run_id, actor=str(user.get("username") or ""))
    except flows.FlowError as exc:
        raise _flow_error(exc) from exc
