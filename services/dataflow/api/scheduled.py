"""Scheduled Task API — CRUD for tasks, execution logs, notification channels, and reports.

All endpoints are workspace-scoped for multi-tenant isolation.
"""

import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Depends, Request
from services.shared.common.auth import get_current_user, authorize_workspace, resolve_current_user
from services.dataviz.services import report_service
from pydantic import BaseModel

from services.dataflow.services.scheduled_task_service import scheduled_task_service

logger = logging.getLogger(__name__)


async def _scheduled_access(request: Request):
    if request.method == "GET" and re.fullmatch(r"/api/scheduled-tasks/reports/\d+", request.url.path):
        return
    user = getattr(request.state, "current_user", None)
    if not user:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        user = resolve_current_user(header[7:])
        request.state.current_user = user
    resource = None
    params = request.path_params
    if params.get("task_id"):
        resource = scheduled_task_service.get_task(int(params["task_id"]))
    elif params.get("log_id"):
        log = scheduled_task_service.get_log(int(params["log_id"]))
        resource = scheduled_task_service.get_task(log["scheduled_task_id"]) if log else None
    elif params.get("report_id"):
        resource = report_service.load_report(int(params["report_id"]))
    elif params.get("channel_id"):
        resource = scheduled_task_service.get_channel(int(params["channel_id"]))
    elif params.get("template_id"):
        resource = scheduled_task_service.get_template(int(params["template_id"]))
    if params and resource is None:
        raise HTTPException(status_code=404, detail="资源不存在或无权访问")
    if resource:
        if resource.get("is_system") and request.method == "GET":
            return
        authorize_workspace(user, resource.get("workspace_id") or 0)
        if ("task_id" in params or "log_id" in params) and user.get("role") != "admin":
            if int(resource.get("owner_id") or 0) != int(user["user_id"]):
                raise HTTPException(status_code=404, detail="资源不存在或无权访问")
    else:
        ws = request.query_params.get("workspace_id") or request.headers.get("X-Workspace-Id")
        if ws is None and request.method in ("POST", "PUT"):
            try:
                body = await request.json()
                ws = body.get("workspace_id", 0) if isinstance(body, dict) else 0
            except ValueError:
                ws = 0
        if ws:
            authorize_workspace(user, int(ws))
        elif request.method in ("POST", "PUT"):
            # 写操作必须明确工作空间（非 admin ws=0 拒绝）
            authorize_workspace(user, 0)
        # GET 无 ws：放行——列表端点按身份圈范围（tasks 按 owner、reports 按 user、
        # channels/templates 按 owner+内置），非 admin 不得看全量；
        # 此处不再 authorize_workspace(user, 0)（非 admin 恒 403，曾致任务调度页打不开）
    if "/logs/cleanup" in request.url.path and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行全局清理")


def _safe_log(value):
    if isinstance(value, list):
        return [_safe_log(item) for item in value]
    if not isinstance(value, dict):
        return value
    hidden = {"result_data", "report_content", "questions_executed", "channel_response", "error_message",
                  "access_token", "report_access_token", "share_token_hash"}
    return {key: _safe_log(item) for key, item in value.items() if key not in hidden}


router = APIRouter(dependencies=[Depends(_scheduled_access)])


# ════════════════════════════════════════════════════════════════════
# Request / Response Models
# ════════════════════════════════════════════════════════════════════


class ScheduledTaskCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    task_type: str = "query"  # query / agent
    task_config: dict
    report_template_key: Optional[str] = None
    cron_expression: str
    timezone: Optional[str] = "Asia/Shanghai"
    channel_id: Optional[int] = None
    notify_on_success: bool = True
    notify_on_failure: bool = True
    is_active: bool = True
    workspace_id: int = 0
    timeout_seconds: int = 300
    max_retries: int = 0


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    task_config: Optional[dict] = None
    report_template_key: Optional[str] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    channel_id: Optional[int] = None
    notify_on_success: Optional[bool] = None
    notify_on_failure: Optional[bool] = None
    is_active: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None


class NotificationChannelCreate(BaseModel):
    name: str
    channel_type: str  # dingtalk / feishu / wecom / email / webhook
    config: dict
    is_active: bool = True
    workspace_id: int = 0


class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = None
    channel_type: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class ReportTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    content: str
    format: str = "markdown"  # markdown / html
    workspace_id: int = 0


class ReportTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    format: Optional[str] = None


# ════════════════════════════════════════════════════════════════════
# Scheduled Tasks CRUD
# ════════════════════════════════════════════════════════════════════


@router.get("/tasks")
def list_scheduled_tasks(
    workspace_id: int = Query(0, description="Workspace ID"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """List scheduled tasks with pagination, scoped by workspace."""
    return scheduled_task_service.list_tasks(workspace_id=workspace_id, page=page, size=size,
        owner_id=None if user.get("role") == "admin" else user["user_id"])


@router.get("/tasks/{task_id}")
def get_scheduled_task(task_id: int):
    """Get a single scheduled task by ID."""
    task = scheduled_task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return task


def _as_bot_checked(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (PermissionError, ValueError) as exc:
        logger.warning("定时任务 AS-BOT 配置校验拒绝: %s", exc)
        raise HTTPException(422, str(exc)) from exc


@router.get("/as-bot-options")
def scheduled_as_bot_options(workspace_id: int = Query(0), user: dict = Depends(get_current_user)):
    from services.datamind.execution.scheduled_analysis import as_bot_options
    authorize_workspace(user, workspace_id)
    return _as_bot_checked(as_bot_options, user["user_id"], workspace_id)


@router.get("/tasks/{task_id}/as-bot-options")
def existing_task_as_bot_options(task_id: int):
    from services.datamind.execution.scheduled_analysis import as_bot_options
    task = scheduled_task_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "定时任务不存在")
    return _as_bot_checked(as_bot_options, task["owner_id"], task.get("workspace_id") or 0)


@router.post("/tasks")
def create_scheduled_task(req: ScheduledTaskCreate, user: dict = Depends(get_current_user)):
    """Create a new scheduled task."""
    # Validate cron expression
    from services.dataflow.tasks.beat_schedule import parse_cron_expression
    try:
        parse_cron_expression(req.cron_expression)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的 Cron 表达式: {e}")

    # Validate task_config
    if not req.task_config:
        raise HTTPException(status_code=400, detail="task_config 不能为空")

    if req.task_type not in ("query", "agent"):
        raise HTTPException(status_code=400, detail="task_type 必须为 query 或 agent")

    task_id = _as_bot_checked(scheduled_task_service.create_task,
        data=req.model_dump(),
        owner_id=user["user_id"],
        workspace_id=req.workspace_id,
    )
    return {"id": task_id}


@router.put("/tasks/{task_id}")
def update_scheduled_task(task_id: int, req: ScheduledTaskUpdate):
    """Update a scheduled task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    # Validate cron if provided
    if req.cron_expression:
        from services.dataflow.tasks.beat_schedule import parse_cron_expression
        try:
            parse_cron_expression(req.cron_expression)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"无效的 Cron 表达式: {e}")

    # Validate task_type if provided
    if req.task_type and req.task_type not in ("query", "agent"):
        raise HTTPException(status_code=400, detail="task_type 必须为 query 或 agent")

    data = req.model_dump(exclude_unset=True)
    success = _as_bot_checked(scheduled_task_service.update_task, task_id, data)
    return {"success": success}


@router.delete("/tasks/{task_id}")
def delete_scheduled_task(task_id: int):
    """Delete a scheduled task and its logs."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    scheduled_task_service.delete_task(task_id)
    return {"success": True}


@router.patch("/tasks/{task_id}/toggle")
def toggle_scheduled_task(
    task_id: int,
    is_active: bool = Query(..., description="Enable or disable"),
):
    """Enable or disable a scheduled task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    success = _as_bot_checked(scheduled_task_service.toggle_task, task_id, 1 if is_active else 0)
    return {"success": success}


@router.post("/tasks/{task_id}/trigger", status_code=202)
async def manual_trigger_scheduled_task(task_id: int, background_tasks: BackgroundTasks):
    """Manually trigger a scheduled task.

    Uses FastAPI BackgroundTasks to run in the main event loop (avoids
    thread/event-loop conflicts with async agent_generate). Returns
    immediately; check execution history for results.
    """
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    from services.datamind.execution.scheduled_analysis import validate_task_as_bot
    _as_bot_checked(validate_task_as_bot, existing)
    # Auto-cleanup stale running logs before triggering
    scheduled_task_service.cleanup_stale_running_logs(timeout_minutes=10)

    import os
    from uuid import uuid4
    from services.dataflow.tasks.executor import execute_scheduled_task_async, execute_scheduled_task
    if int(existing.get("owner_id") or 0) <= 0:
        raise HTTPException(status_code=409, detail="任务待认领，不能执行")
    run_key = uuid4().hex
    log_id = scheduled_task_service.create_log(task_id, "manual", "queued",
        run_key=run_key, celery_task_id=run_key, workspace_id=existing.get("workspace_id") or 0)
    mode = os.getenv("ADH_TASK_EXECUTION_MODE", "celery")
    try:
        if mode == "background":
            background_tasks.add_task(execute_scheduled_task_async, task_id, "manual", run_key)
        elif mode == "celery":
            timeout = max(1, min(int(existing.get("timeout_seconds") or 300), 3600))
            execute_scheduled_task.apply_async(args=(task_id, "manual", run_key), task_id=run_key,
                                               soft_time_limit=timeout, time_limit=timeout + 30)
        else:
            raise ValueError("不支持的执行适配器")
    except Exception:
        logger.exception("任务派发失败")
        scheduled_task_service.finish_log(log_id, status="failed", stage_error_code="DISPATCH_FAILED",
                                         error_message="任务队列暂不可用", finished_at=datetime.now())
        raise HTTPException(status_code=503, detail="任务队列暂不可用，派发未完成") from None
    return {"status": "queued", "mode": mode, "log_id": log_id, "run_key": run_key}


# ════════════════════════════════════════════════════════════════════
# Execution Logs
# ════════════════════════════════════════════════════════════════════


@router.get("/tasks/{task_id}/logs")
def list_scheduled_task_logs(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status"),
):
    """List execution logs for a scheduled task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return _safe_log(scheduled_task_service.list_logs(
        task_id=task_id, page=page, size=size, status=status,
    ))


@router.get("/logs/{log_id}")
def get_scheduled_log(log_id: int):
    """Get a single execution log by ID."""
    log = scheduled_task_service.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="执行日志不存在")
    return _safe_log(log)


@router.patch("/logs/{log_id}/status")
def update_log_status(
    log_id: int,
    status: str = Query(..., description="New status: success / failed / cancelled / timeout"),
    error_message: Optional[str] = Query(None, description="Error message (for failed/cancelled)"),
):
    """Manually update an execution log status. Use 'cancelled' to mark a running task as stopped."""
    valid_statuses = ("cancelled",)
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"状态必须为 {', '.join(valid_statuses)} 之一")

    log = scheduled_task_service.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="执行日志不存在")

    from services.dataflow.tasks.executor import cancel_running_task
    if not cancel_running_task(log_id):
        raise HTTPException(status_code=409, detail="任务已结束，不能修改终态")
    return {"success": True}


@router.post("/logs/cleanup-stale")
def cleanup_stale_logs(
    timeout_minutes: int = Query(10, ge=1, description="Mark running logs older than N minutes as timeout"),
):
    """Detect and mark stale 'running' logs as timeout (task likely crashed)."""
    cleaned = scheduled_task_service.cleanup_stale_running_logs(timeout_minutes)
    return {"success": True, "cleaned": cleaned}


@router.get("/tasks/{task_id}/stats")
def get_scheduled_task_stats(task_id: int):
    """Get execution statistics for a task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return scheduled_task_service.get_log_stats(task_id)


@router.delete("/logs/cleanup")
def cleanup_scheduled_logs(
    days: int = Query(30, ge=1, description="Delete logs older than N days"),
):
    """Clean up old execution logs."""
    deleted = scheduled_task_service.cleanup_logs(days)
    return {"success": True, "deleted": deleted}


# ════════════════════════════════════════════════════════════════════
# Notification Channels
# ════════════════════════════════════════════════════════════════════


@router.get("/channels")
def list_notification_channels(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: dict = Depends(get_current_user),
):
    """List notification channels, scoped by workspace."""
    # 非 admin 全局视图只给自建 + 无主内置渠道（config 含 webhook/密钥，不泄露他人）
    owner_id = None if user.get("role") == "admin" else user["user_id"]
    return scheduled_task_service.list_channels(workspace_id=workspace_id, owner_id=owner_id)


@router.get("/channels/{channel_id}")
def get_notification_channel(channel_id: int):
    """Get a single notification channel by ID."""
    channel = scheduled_task_service.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    return channel


@router.post("/channels")
def create_notification_channel(req: NotificationChannelCreate, user: dict = Depends(get_current_user)):
    """Create a new notification channel."""
    if req.channel_type not in ("dingtalk", "feishu", "wecom", "email", "webhook"):
        raise HTTPException(status_code=400, detail="不支持的渠道类型")

    channel_id = scheduled_task_service.create_channel(
        data=req.model_dump(),
        owner_id=user["user_id"],
        workspace_id=req.workspace_id,
    )
    return {"id": channel_id}


@router.put("/channels/{channel_id}")
def update_notification_channel(channel_id: int, req: NotificationChannelUpdate):
    """Update a notification channel."""
    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="通知渠道不存在")

    data = req.model_dump(exclude_unset=True)
    success = scheduled_task_service.update_channel(channel_id, data)
    return {"success": success}


@router.delete("/channels/{channel_id}")
def delete_notification_channel(channel_id: int):
    """Delete a notification channel."""
    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="通知渠道不存在")

    scheduled_task_service.delete_channel(channel_id)
    return {"success": True}


@router.post("/channels/{channel_id}/test")
def test_notification_channel(channel_id: int):
    """Send a test message to verify channel connectivity."""
    from services.dataflow.tasks.notification import notification_sender

    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="通知渠道不存在")

    try:
        result = notification_sender.test(channel_id)
        scheduled_task_service.update_channel_test_status(channel_id, "success")
        return {"success": True, "result": result}
    except Exception as e:
        scheduled_task_service.update_channel_test_status(channel_id, "failed")
        raise HTTPException(status_code=400, detail=f"测试失败: {e}")


# ════════════════════════════════════════════════════════════════════
# Report Templates
# ════════════════════════════════════════════════════════════════════


@router.get("/templates")
def list_report_templates(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: dict = Depends(get_current_user),
):
    """List report templates (system built-in + workspace custom)."""
    # 非 admin 全局视图只给系统内置 + 自建模板
    owner_id = None if user.get("role") == "admin" else user["user_id"]
    return scheduled_task_service.list_templates(workspace_id=workspace_id, owner_id=owner_id)


@router.get("/templates/{template_id}")
def get_report_template(template_id: int):
    """Get a single report template by ID."""
    tpl = scheduled_task_service.get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    return tpl


@router.post("/templates")
def create_report_template(req: ReportTemplateCreate, user: dict = Depends(get_current_user)):
    """Create a new report template."""
    if req.format not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="格式必须为 markdown 或 html")
    template_id = scheduled_task_service.create_template(
        data=req.model_dump(),
        owner_id=user["user_id"],
        workspace_id=req.workspace_id,
    )
    return {"id": template_id}


@router.put("/templates/{template_id}")
def update_report_template(template_id: int, req: ReportTemplateUpdate):
    """Update a report template. System templates cannot be modified."""
    existing = scheduled_task_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    try:
        data = req.model_dump(exclude_unset=True)
        success = scheduled_task_service.update_template(template_id, data)
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/templates/{template_id}")
def delete_report_template(template_id: int):
    """Delete a report template. System templates cannot be deleted."""
    existing = scheduled_task_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    try:
        scheduled_task_service.delete_template(template_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ════════════════════════════════════════════════════════════════════
# Generated Reports (viewable via link)
# ════════════════════════════════════════════════════════════════════


@router.get("/reports")
def list_reports(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: dict = Depends(get_current_user),
):
    """报表中心: 成功执行生成的报告摘要清单(附任务名, 时间倒序)."""
    return scheduled_task_service.list_reports(workspace_id=workspace_id, user=user)


@router.post("/reports/generate", status_code=202)
def generate_report_alias(req: report_service.ReportSubmission, background_tasks: BackgroundTasks,
                          user: dict = Depends(get_current_user)):
    authorize_workspace(user, req.workspace_id)
    return report_service.submit_report(req.model_dump(), user, background_tasks)


@router.get("/reports/{report_id}/detail")
def get_report_detail(report_id: int, user: dict = Depends(get_current_user)):
    """报表中心应用内查看: 返回完整内容, 免外链 access_token 校验."""
    report = scheduled_task_service.get_report_detail(report_id, user=user)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@router.get("/reports/{report_id}")
def get_report(report_id: int, request: Request, token: Optional[str] = Query(None, description="Access token for private reports")):
    """Get a generated report by ID. Public reports are accessible to all; private require token."""
    report = scheduled_task_service.get_report(report_id, access_token=token,
                                                user=getattr(request.state, "current_user", None))
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.get("error"):
        raise HTTPException(status_code=403, detail=report["error"])
    return report


# ════════════════════════════════════════════════════════════════════
# Report Templates Router (alias for /api/report-templates)
# ════════════════════════════════════════════════════════════════════

templates_router = APIRouter(dependencies=[Depends(_scheduled_access)])


@templates_router.get("/")
def list_report_templates_alias(
    workspace_id: int = Query(0, description="Workspace ID"),
):
    """List report templates (system built-in + workspace custom)."""
    return scheduled_task_service.list_templates(workspace_id=workspace_id)


@templates_router.get("/{template_id}")
def get_report_template_alias(template_id: int):
    """Get a single report template by ID."""
    tpl = scheduled_task_service.get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    return tpl


@templates_router.post("/")
def create_report_template_alias(req: ReportTemplateCreate, user: dict = Depends(get_current_user)):
    """Create a new report template."""
    if req.format not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="格式必须为 markdown 或 html")
    template_id = scheduled_task_service.create_template(
        data=req.model_dump(),
        owner_id=user["user_id"],
        workspace_id=req.workspace_id,
    )
    return {"id": template_id}


@templates_router.put("/{template_id}")
def update_report_template_alias(template_id: int, req: ReportTemplateUpdate):
    """Update a report template. System templates cannot be modified."""
    existing = scheduled_task_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    try:
        data = req.model_dump(exclude_unset=True)
        success = scheduled_task_service.update_template(template_id, data)
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@templates_router.delete("/{template_id}")
def delete_report_template_alias(template_id: int):
    """Delete a report template. System templates cannot be deleted."""
    existing = scheduled_task_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    try:
        scheduled_task_service.delete_template(template_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
