"""任务监控 API — 系统配置「任务监控」页后端。

覆盖定时任务（系统内置 + 用户创建）、队列任务（执行实例）与同步任务的查询与运维操作。
权限口径：任务监控是系统配置运维能力，全路由仅 admin 可用（fail-closed，身份只信服务端）；
写操作另受权限码中间件 `task-monitor:manage` 门控。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.common.auth import resolve_current_user
from backend.modules.flow.services.task_monitor_service import task_monitor_service

logger = logging.getLogger(__name__)


async def _monitor_access(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        user = resolve_current_user(header[7:])
        request.state.current_user = user
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="任务监控仅管理员可用")
    return user


router = APIRouter(dependencies=[Depends(_monitor_access)])


class PauseRequest(BaseModel):
    paused: bool


class ToggleRequest(BaseModel):
    is_active: bool


def _call(operation, *args, **kwargs):
    """服务层异常映射：不存在→404，终态/禁改→409，其余→400。"""
    try:
        return operation(*args, **kwargs)
    except ValueError as exc:
        message = str(exc)
        code = 404 if ("不存在" in message) else 409 if ("已结束" in message or "不可移除" in message) else 400
        raise HTTPException(status_code=code, detail=message) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ════════════════════════════════════════════════════════════════════
# 总览 / 定时任务
# ════════════════════════════════════════════════════════════════════


@router.get("/summary")
def monitor_summary():
    """任务总览：定时任务、队列实例、卡住/失败计数、同步任务。"""
    return _call(task_monitor_service.summary)


@router.get("/tasks")
def list_monitored_tasks(
    source: Optional[str] = Query(None, description="system=系统内置 / user=用户创建，缺省全部"),
    keyword: Optional[str] = Query(None, description="按任务名/描述过滤"),
):
    """定时任务清单：系统内置任务 + 用户创建任务（统一视图）。"""
    return {"items": _call(task_monitor_service.list_tasks, source=source, keyword=keyword)}


@router.post("/tasks/{task_id}/stop")
def stop_monitored_task(task_id: int, user: dict = Depends(_monitor_access)):
    """停止任务：停用定义并取消其全部排队/运行中的执行实例。"""
    return _call(task_monitor_service.stop_task, task_id)


@router.delete("/tasks/{task_id}")
def remove_monitored_task(task_id: int, user: dict = Depends(_monitor_access)):
    """移除用户创建的任务（含执行记录）；系统内置任务显式拒绝。"""
    return _call(task_monitor_service.remove_task, task_id)


@router.post("/system-jobs/{job_key}/pause")
def pause_system_job(job_key: str, req: PauseRequest, user: dict = Depends(_monitor_access)):
    """暂停/恢复系统内置任务；内置任务不支持移除。"""
    return _call(task_monitor_service.pause_system_job, job_key, req.paused, user.get("username", ""))


# ════════════════════════════════════════════════════════════════════
# 队列任务（执行实例）
# ════════════════════════════════════════════════════════════════════


@router.get("/runs")
def list_queued_runs(
    kind: Optional[str] = Query(None, description="scheduled=定时执行 / report=报表生成 / sync=同步执行"),
    status: Optional[str] = Query(None, description="active / stuck / success / partial / failed / timeout / cancelled"),
    task_id: Optional[int] = Query(None, description="按任务过滤其执行历史"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """跨队列执行实例列表（进度、结果、卡死标记）；task_id 供任务详情弹窗查执行历史。"""
    return _call(task_monitor_service.list_runs, kind=kind, status=status, task_id=task_id,
                 page=page, size=size)


@router.post("/runs/{kind}/{run_id}/stop")
def stop_queued_run(kind: str, run_id: int, user: dict = Depends(_monitor_access)):
    """停止一个排队/运行中的执行实例（终态保护，已结束的实例返回 409）。"""
    return _call(task_monitor_service.stop_run, kind, run_id)


@router.post("/cleanup-stale")
def cleanup_stale_runs(
    timeout_minutes: int = Query(10, ge=1, description="超过 N 分钟仍在排队/运行的实例按超时清理"),
    user: dict = Depends(_monitor_access),
):
    """手动卡死清理（系统内置任务「运行对账与卡死清理」也会自动执行）。"""
    return _call(task_monitor_service.cleanup_stale, timeout_minutes)


# ════════════════════════════════════════════════════════════════════
# 同步任务
# ════════════════════════════════════════════════════════════════════


@router.get("/sync-tasks")
def list_monitored_sync_tasks():
    """数据同步任务清单（含运行中实例计数）。"""
    return {"items": _call(task_monitor_service.list_sync_tasks)}


@router.post("/sync-tasks/{task_id}/toggle")
def toggle_monitored_sync_task(task_id: int, req: ToggleRequest,
                               user: dict = Depends(_monitor_access)):
    """启用/停用数据同步任务。"""
    return _call(task_monitor_service.toggle_sync_task, task_id, req.is_active)


@router.post("/sync-tasks/{task_id}/stop")
def stop_monitored_sync_task(task_id: int, user: dict = Depends(_monitor_access)):
    """停止同步任务：停用定义并取消其运行中的执行实例。"""
    return _call(task_monitor_service.stop_sync_task, task_id)


@router.delete("/sync-tasks/{task_id}")
def remove_monitored_sync_task(task_id: int, user: dict = Depends(_monitor_access)):
    """移除数据同步任务（含执行记录）。"""
    return _call(task_monitor_service.remove_sync_task, task_id)


@router.get("/kb-sync")
def list_kb_sync_state():
    """本体知识库同步水位线（模型名 + 目标库 + 同步版本/状态）。"""
    return {"items": _call(task_monitor_service.kb_sync_state)}
