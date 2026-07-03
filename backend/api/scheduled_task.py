"""Scheduled Task API — CRUD for tasks, execution logs, and notification channels.

All endpoints are workspace-scoped for multi-tenant isolation.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.api.auth import get_current_user
from backend.models.schemas import (
    UserInfo,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskResponse, ScheduledTaskListResponse,
    ScheduledLogResponse, ScheduledLogListResponse,
    NotificationChannelCreate, NotificationChannelUpdate, NotificationChannelResponse,
    ReportTemplateCreate, ReportTemplateUpdate, ReportTemplateResponse,
    ReportResponse,
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
    # Validate cron expression
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

    # Validate cron if provided
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
