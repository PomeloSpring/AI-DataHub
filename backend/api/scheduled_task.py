"""Scheduled Task API — CRUD for tasks, execution logs, and notification channels.

All endpoints are workspace-scoped for multi-tenant isolation.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from backend.api.auth import get_current_user
from backend.common.auth import log_audit
from backend.models.schemas import (
    UserInfo,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskResponse, ScheduledTaskListResponse,
    ScheduledLogResponse, ScheduledLogListResponse,
    NotificationChannelCreate, NotificationChannelUpdate, NotificationChannelResponse,
    ReportTemplateCreate, ReportTemplateUpdate, ReportTemplateResponse,
    ReportResponse, WebhookTriggerRequest,
)
from backend.services.scheduled_task_service import scheduled_task_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════════════════
# Scheduled Tasks CRUD
# ════════════════════════════════════════════════════════════════════

@router.get("/scheduled-tasks", response_model=ScheduledTaskListResponse)
def list_scheduled_tasks(
    workspace_id: int = Query(0, description="Workspace ID"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: UserInfo = Depends(get_current_user),
):
    """List scheduled tasks with pagination, scoped by workspace."""
    return scheduled_task_service.list_tasks(workspace_id=workspace_id, page=page, size=size)


@router.get("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
def get_scheduled_task(
    task_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Get a single scheduled task by ID."""
    task = scheduled_task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return task


@router.post("/scheduled-tasks", response_model=dict)
def create_scheduled_task(
    req: ScheduledTaskCreate,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new scheduled task."""
    # Validate trigger_type
    if req.trigger_type not in ("cron", "webhook", "both"):
        raise HTTPException(status_code=400, detail="trigger_type 必须为 cron、webhook 或 both")

    # Validate cron expression (required for cron/both)
    if req.trigger_type in ("cron", "both"):
        if not req.cron_expression or not req.cron_expression.strip():
            raise HTTPException(status_code=400, detail="定时触发模式下 Cron 表达式不能为空")
        from backend.tasks.beat_schedule import parse_cron_expression
        try:
            parse_cron_expression(req.cron_expression)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"无效的 Cron 表达式: {e}")

    # Validate task_config
    if not req.task_config:
        raise HTTPException(status_code=400, detail="task_config 不能为空")

    if req.task_type not in ("query", "agent"):
        raise HTTPException(status_code=400, detail="task_type 必须为 query 或 agent")

    task_id = scheduled_task_service.create_task(
        data=req.model_dump(),
        owner_id=user.id,
        workspace_id=req.workspace_id,
    )
    log_audit(user.id, user.username, "create_scheduled_task",
              target_type="scheduled_task", target_id=task_id,
              detail=f"创建定时任务 {req.name}", module="scheduled_task")
    return {"id": task_id}


@router.put("/scheduled-tasks/{task_id}", response_model=dict)
def update_scheduled_task(
    task_id: int,
    req: ScheduledTaskUpdate,
    user: UserInfo = Depends(get_current_user),
):
    """Update a scheduled task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    # Validate trigger_type if provided
    if req.trigger_type and req.trigger_type not in ("cron", "webhook", "both"):
        raise HTTPException(status_code=400, detail="trigger_type 必须为 cron、webhook 或 both")

    # Determine effective trigger_type for cron validation
    effective_trigger = req.trigger_type or existing.get("trigger_type", "cron")
    if effective_trigger in ("cron", "both"):
        cron = req.cron_expression if req.cron_expression is not None else existing.get("cron_expression", "")
        if not cron or not cron.strip():
            raise HTTPException(status_code=400, detail="定时触发模式下 Cron 表达式不能为空")
        if req.cron_expression:
            from backend.tasks.beat_schedule import parse_cron_expression
            try:
                parse_cron_expression(req.cron_expression)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"无效的 Cron 表达式: {e}")

    # Validate task_type if provided
    if req.task_type and req.task_type not in ("query", "agent"):
        raise HTTPException(status_code=400, detail="task_type 必须为 query 或 agent")

    data = req.model_dump(exclude_unset=True)
    success = scheduled_task_service.update_task(task_id, data)
    log_audit(user.id, user.username, "update_scheduled_task",
              target_type="scheduled_task", target_id=task_id,
              detail=f"更新定时任务 id={task_id}", module="scheduled_task")
    return {"success": success}


@router.delete("/scheduled-tasks/{task_id}", response_model=dict)
def delete_scheduled_task(
    task_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Delete a scheduled task and its logs."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    scheduled_task_service.delete_task(task_id)
    log_audit(user.id, user.username, "delete_scheduled_task",
              target_type="scheduled_task", target_id=task_id,
              detail=f"删除定时任务 id={task_id}", module="scheduled_task")
    return {"success": True}


@router.patch("/scheduled-tasks/{task_id}/toggle", response_model=dict)
def toggle_scheduled_task(
    task_id: int,
    is_active: bool = Query(..., description="Enable or disable"),
    user: UserInfo = Depends(get_current_user),
):
    """Enable or disable a scheduled task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    success = scheduled_task_service.toggle_task(task_id, 1 if is_active else 0)
    log_audit(user.id, user.username, "toggle_scheduled_task",
              target_type="scheduled_task", target_id=task_id,
              detail=f"{'启用' if is_active else '禁用'}定时任务 id={task_id}", module="scheduled_task")
    return {"success": success}


@router.post("/scheduled-tasks/{task_id}/trigger", response_model=dict)
async def manual_trigger_scheduled_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    user: UserInfo = Depends(get_current_user),
):
    """Manually trigger a scheduled task.

    Uses FastAPI BackgroundTasks to run in the main event loop (avoids
    thread/event-loop conflicts with async agent_generate). Returns
    immediately; check execution history for results.
    """
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    # Auto-cleanup stale running logs before triggering
    scheduled_task_service.cleanup_stale_running_logs(timeout_minutes=10)

    from backend.tasks.executor import execute_scheduled_task_async

    background_tasks.add_task(execute_scheduled_task_async, task_id, "manual")
    return {"success": True, "mode": "background"}


@router.post("/scheduled-tasks/{task_id}/regenerate-webhook-token", response_model=dict)
def regenerate_webhook_token(
    task_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Regenerate webhook token for a task. Old webhook URL becomes invalid immediately."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    if existing.get("trigger_type") not in ("webhook", "both"):
        raise HTTPException(status_code=400, detail="该任务未启用 Webhook 触发")

    new_token = scheduled_task_service.regenerate_webhook_token(task_id)
    if not new_token:
        raise HTTPException(status_code=500, detail="重新生成 Token 失败")

    log_audit(user.id, user.username, "regenerate_webhook_token",
              target_type="scheduled_task", target_id=task_id,
              detail=f"重新生成 Webhook Token id={task_id}", module="scheduled_task")
    return {"success": True, "webhook_token": new_token}


# ════════════════════════════════════════════════════════════════════
# Execution Logs
# ════════════════════════════════════════════════════════════════════

@router.get("/scheduled-tasks/{task_id}/logs", response_model=ScheduledLogListResponse)
def list_scheduled_task_logs(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str = Query(None, description="Filter by status"),
    user: UserInfo = Depends(get_current_user),
):
    """List execution logs for a scheduled task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return scheduled_task_service.list_logs(
        task_id=task_id, page=page, size=size, status=status,
    )


@router.get("/scheduled-logs/{log_id}", response_model=ScheduledLogResponse)
def get_scheduled_log(
    log_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Get a single execution log by ID."""
    log = scheduled_task_service.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="执行日志不存在")
    return log


@router.patch("/scheduled-logs/{log_id}/status", response_model=dict)
def update_log_status(
    log_id: int,
    status: str = Query(..., description="New status: success / failed / cancelled / timeout"),
    error_message: str = Query(None, description="Error message (for failed/cancelled)"),
    user: UserInfo = Depends(get_current_user),
):
    """Manually update an execution log status. Use 'cancelled' to mark a running task as stopped."""
    valid_statuses = ("success", "failed", "cancelled", "timeout")
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"状态必须为 {', '.join(valid_statuses)} 之一")

    log = scheduled_task_service.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="执行日志不存在")

    from backend.tasks.executor import cancel_running_task
    if status == "cancelled":
        cancel_running_task(log_id)

    scheduled_task_service.update_log(
        log_id,
        status=status,
        error_message=error_message,
        finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return {"success": True}


@router.post("/scheduled-logs/cleanup-stale", response_model=dict)
def cleanup_stale_logs(
    timeout_minutes: int = Query(10, ge=1, description="Mark running logs older than N minutes as timeout"),
    user: UserInfo = Depends(get_current_user),
):
    """Detect and mark stale 'running' logs as timeout (task likely crashed)."""
    cleaned = scheduled_task_service.cleanup_stale_running_logs(timeout_minutes)
    return {"success": True, "cleaned": cleaned}


@router.get("/scheduled-tasks/{task_id}/stats", response_model=dict)
def get_scheduled_task_stats(
    task_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Get execution statistics for a task."""
    existing = scheduled_task_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    return scheduled_task_service.get_log_stats(task_id)


@router.delete("/scheduled-logs/cleanup", response_model=dict)
def cleanup_scheduled_logs(
    days: int = Query(30, ge=1, description="Delete logs older than N days"),
    user: UserInfo = Depends(get_current_user),
):
    """Clean up old execution logs."""
    deleted = scheduled_task_service.cleanup_logs(days)
    return {"success": True, "deleted": deleted}


# ════════════════════════════════════════════════════════════════════
# Notification Channels
# ════════════════════════════════════════════════════════════════════

@router.get("/notification-channels", response_model=list)
def list_notification_channels(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """List notification channels, scoped by workspace."""
    return scheduled_task_service.list_channels(workspace_id=workspace_id)


@router.get("/notification-channels/{channel_id}", response_model=NotificationChannelResponse)
def get_notification_channel(
    channel_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Get a single notification channel by ID."""
    channel = scheduled_task_service.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    return channel


@router.post("/notification-channels", response_model=dict)
def create_notification_channel(
    req: NotificationChannelCreate,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new notification channel."""
    if req.channel_type not in ("dingtalk", "feishu", "wecom", "email", "webhook"):
        raise HTTPException(status_code=400, detail="不支持的渠道类型")

    channel_id = scheduled_task_service.create_channel(
        data=req.model_dump(),
        owner_id=user.id,
        workspace_id=req.workspace_id,
    )
    log_audit(user.id, user.username, "create_notification_channel",
              target_type="notification_channel", target_id=channel_id,
              detail=f"创建通知渠道 {req.name}", module="scheduled_task")
    return {"id": channel_id}


@router.put("/notification-channels/{channel_id}", response_model=dict)
def update_notification_channel(
    channel_id: int,
    req: NotificationChannelUpdate,
    user: UserInfo = Depends(get_current_user),
):
    """Update a notification channel."""
    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="通知渠道不存在")

    data = req.model_dump(exclude_unset=True)
    success = scheduled_task_service.update_channel(channel_id, data)
    log_audit(user.id, user.username, "update_notification_channel",
              target_type="notification_channel", target_id=channel_id,
              detail=f"更新通知渠道 id={channel_id}", module="scheduled_task")
    return {"success": success}


@router.delete("/notification-channels/{channel_id}", response_model=dict)
def delete_notification_channel(
    channel_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Delete a notification channel."""
    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="通知渠道不存在")

    scheduled_task_service.delete_channel(channel_id)
    log_audit(user.id, user.username, "delete_notification_channel",
              target_type="notification_channel", target_id=channel_id,
              detail=f"删除通知渠道 id={channel_id}", module="scheduled_task")
    return {"success": True}


@router.post("/notification-channels/{channel_id}/test", response_model=dict)
def test_notification_channel(
    channel_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Send a test message to verify channel connectivity."""
    from backend.tasks.notification import notification_sender

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

@router.get("/report-templates", response_model=list)
def list_report_templates(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """List report templates (system built-in + workspace custom)."""
    return scheduled_task_service.list_templates(workspace_id=workspace_id)


@router.get("/report-templates/{template_id}", response_model=ReportTemplateResponse)
def get_report_template(
    template_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Get a single report template by ID."""
    tpl = scheduled_task_service.get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    return tpl


@router.post("/report-templates", response_model=dict)
def create_report_template(
    req: ReportTemplateCreate,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new report template."""
    if req.format not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="格式必须为 markdown 或 html")
    template_id = scheduled_task_service.create_template(
        data=req.model_dump(),
        owner_id=user.id,
        workspace_id=req.workspace_id,
    )
    log_audit(user.id, user.username, "create_report_template",
              target_type="report_template", target_id=template_id,
              detail=f"创建报告模板 {req.name}", module="scheduled_task")
    return {"id": template_id}


@router.put("/report-templates/{template_id}", response_model=dict)
def update_report_template(
    template_id: int,
    req: ReportTemplateUpdate,
    user: UserInfo = Depends(get_current_user),
):
    """Update a report template. System templates cannot be modified."""
    existing = scheduled_task_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    try:
        data = req.model_dump(exclude_unset=True)
        success = scheduled_task_service.update_template(template_id, data)
        log_audit(user.id, user.username, "update_report_template",
                  target_type="report_template", target_id=template_id,
                  detail=f"更新报告模板 id={template_id}", module="scheduled_task")
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/report-templates/{template_id}", response_model=dict)
def delete_report_template(
    template_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Delete a report template. System templates cannot be deleted."""
    existing = scheduled_task_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="报告模板不存在")
    try:
        scheduled_task_service.delete_template(template_id)
        log_audit(user.id, user.username, "delete_report_template",
                  target_type="report_template", target_id=template_id,
                  detail=f"删除报告模板 id={template_id}", module="scheduled_task")
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ════════════════════════════════════════════════════════════════════
# Generated Reports (viewable via link)
# ════════════════════════════════════════════════════════════════════

@router.get("/reports/{report_id}", response_model=ReportResponse)
def get_report(report_id: int, token: str = Query(None, description="Access token for private reports")):
    """Get a generated report by ID. Public reports are accessible to all; private require token."""
    report = scheduled_task_service.get_report(report_id, access_token=token)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.get("error"):
        raise HTTPException(status_code=403, detail=report["error"])
    return report


# ════════════════════════════════════════════════════════════════════
# Webhook Trigger (public, no JWT auth)
# ════════════════════════════════════════════════════════════════════

@router.post("/webhook/tasks/{task_id}/{token}", response_model=dict)
async def webhook_trigger_scheduled_task(
    task_id: int,
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Trigger a scheduled task via webhook (no JWT auth required).

    External systems POST to this endpoint to trigger task execution.
    The webhook_token in the URL authenticates the request.
    If the task has webhook_secret configured, the caller must include
    an X-Webhook-Signature header with HMAC-SHA256 signature of the body.
    """
    # Lookup task by ID + token
    task = scheduled_task_service.get_task_by_webhook_token(task_id, token)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或 Token 无效")

    # Verify trigger_type allows webhook
    if task.get("trigger_type") not in ("webhook", "both"):
        raise HTTPException(status_code=400, detail="该任务未启用 Webhook 触发")

    # Read body for signature verification
    body = await request.body()

    # Verify HMAC signature if webhook_secret is configured
    webhook_secret = task.get("webhook_secret")
    if webhook_secret:
        signature_header = request.headers.get("X-Webhook-Signature", "")
        if not signature_header:
            raise HTTPException(status_code=401, detail="缺少 X-Webhook-Signature 头")
        expected = "sha256=" + hmac.new(
            webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature_header, expected):
            raise HTTPException(status_code=401, detail="签名验证失败")

    # Parse request body
    webhook_body = {}
    if body:
        try:
            webhook_body = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="请求体必须是有效的 JSON")

    # Store webhook context for the executor (variables, override_questions)
    # We pass these through the log entry
    variables = webhook_body.get("variables") if isinstance(webhook_body, dict) else None
    override_questions = webhook_body.get("override_questions") if isinstance(webhook_body, dict) else None

    # Auto-cleanup stale running logs
    scheduled_task_service.cleanup_stale_running_logs(timeout_minutes=10)

    from backend.tasks.executor import execute_scheduled_task_async

    # If override_questions provided, temporarily update task_config
    if override_questions and isinstance(override_questions, list):
        import copy
        task_config = copy.deepcopy(task.get("task_config", {}))
        task_config["questions"] = override_questions
        scheduled_task_service.update_task(task_id, {"task_config": task_config})

    background_tasks.add_task(execute_scheduled_task_async, task_id, "webhook")
    logger.info(f"Webhook triggered task {task_id}")

    return {"success": True, "message": "任务已触发"}
