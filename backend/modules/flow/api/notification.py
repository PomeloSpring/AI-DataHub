"""Notification API — Manage notification channels and send messages.

Supported channel types: dingtalk, feishu, wecom, email, webhook, sms(本期占位)

Table: adh_notification_channels

渠道 CRUD 只一份实现：本路由读写都委派 ``scheduled_task_service``（与
``/scheduled-tasks/channels`` 同一服务）。以前这里走 ``notification_service`` 自己那份重复实现，
它往根本不存在的列 ``test_status`` 插值、又不填 ``owner_id NOT NULL``，而 ``id`` 列没有
AUTO_INCREMENT —— 所以「新建渠道」始终 500、「测试发送」发完在写状态那步炸成 400、
「删除渠道」则根本没有对应路由（405）。两套真值源的代价由本次实测暴露。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.common.auth import get_current_user
from backend.modules.flow.services.notification_service import notification_service
from backend.modules.flow.services.scheduled_task_service import scheduled_task_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════════════════
# Request / Response Models
# ════════════════════════════════════════════════════════════════════


# sms 为本期占位通道：可建渠道、可引用短信模板，但发送会显式报"服务商未配置"（不静默成功）
VALID_CHANNEL_TYPES = ("dingtalk", "feishu", "wecom", "email", "webhook", "sms")


class NotificationChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict
    workspace_id: int = 0
    # 页面上有「启用」开关：不收 is_active 就是“关了开关仍建出一个启用渠道”
    is_active: bool = True


class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = None
    # 渠道类型也得能改（不接就会被 pydantic 默默丢掉：改完点保存仍回“更新成功”，实际什么没变）
    channel_type: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[int] = None


class SendNotificationRequest(BaseModel):
    channel_id: int
    content: str
    title: str = "AI-DataHub Notification"


# ════════════════════════════════════════════════════════════════════
# Notification Channels
# ════════════════════════════════════════════════════════════════════


@router.get("/channels")
def list_notification_channels(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: dict = Depends(get_current_user),
):
    """List notification channels.

    渠道 config 里是 webhook 密钥 / SMTP 口令，所以非 admin 只能看到自己建的（
    与 ``/scheduled-tasks/channels`` 同一口径，不在两个入口上给两套可见性）。
    """
    owner_id = None if user.get("role") == "admin" else user["user_id"]
    return scheduled_task_service.list_channels(workspace_id=workspace_id, owner_id=owner_id)


@router.get("/channels/{channel_id}")
def get_notification_channel(channel_id: int):
    """Get a single notification channel by ID."""
    channel = scheduled_task_service.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Notification channel not found")
    return channel


@router.post("/channels")
def create_notification_channel(req: NotificationChannelCreate,
                                user: dict = Depends(get_current_user)):
    """Create a new notification channel."""
    if req.channel_type not in VALID_CHANNEL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported channel type. Must be one of: {', '.join(VALID_CHANNEL_TYPES)}",
        )

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
        raise HTTPException(status_code=404, detail="Notification channel not found")

    data = req.model_dump(exclude_unset=True)
    if data.get("channel_type") and data["channel_type"] not in VALID_CHANNEL_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported channel type. Must be one of: {', '.join(VALID_CHANNEL_TYPES)}")
    success = scheduled_task_service.update_channel(channel_id, data)
    return {"success": success}


@router.delete("/channels/{channel_id}")
def delete_notification_channel(channel_id: int):
    """Delete a notification channel（旧版该路由不存在，前端的删除按钮始终吃 405）."""
    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    scheduled_task_service.delete_channel(channel_id)
    return {"success": True}


@router.post("/channels/{channel_id}/test")
def test_notification_channel(channel_id: int):
    """Send a test message through the same render chain as scheduled runs."""
    existing = scheduled_task_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    try:
        # 测试发送与定时任务走同一条链（渠道引用的消息模板 → render → 传输），
        # 响应里带 status/reasons/warnings：“发的是默认文案”不能糊成一句成功
        result = notification_service.test_channel(channel_id)
        scheduled_task_service.update_channel_test_status(channel_id, "success")
        return {"success": True, "result": result}
    except Exception as e:
        scheduled_task_service.update_channel_test_status(channel_id, "failed")
        raise HTTPException(status_code=400, detail=f"Test failed: {e}")


@router.post("/send")
def send_notification(req: SendNotificationRequest):
    """Send a notification through a configured channel."""
    try:
        result = notification_service.send(
            channel_id=req.channel_id,
            content=req.content,
            title=req.title,
        )
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to send notification: %s", e)
        raise HTTPException(status_code=502, detail=f"Notification send failed: {e}")
