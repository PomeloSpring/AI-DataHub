"""Notification API — Manage notification channels and send messages.

Supported channel types: dingtalk, feishu, wecom, email, webhook

Table: adh_notification_channels
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.dataflow.services.notification_service import notification_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════════════════
# Request / Response Models
# ════════════════════════════════════════════════════════════════════


VALID_CHANNEL_TYPES = ("dingtalk", "feishu", "wecom", "email", "webhook")


class NotificationChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict
    workspace_id: int = 0


class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = None
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
):
    """List notification channels, scoped by workspace."""
    return notification_service.list_channels(workspace_id=workspace_id)


@router.post("/channels")
def create_notification_channel(req: NotificationChannelCreate):
    """Create a new notification channel."""
    if req.channel_type not in VALID_CHANNEL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported channel type. Must be one of: {', '.join(VALID_CHANNEL_TYPES)}",
        )

    channel_id = notification_service.create_channel(
        data=req.model_dump(),
        workspace_id=req.workspace_id,
    )
    return {"id": channel_id}


@router.put("/channels/{channel_id}")
def update_notification_channel(channel_id: int, req: NotificationChannelUpdate):
    """Update a notification channel."""
    existing = notification_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    data = req.model_dump(exclude_unset=True)
    success = notification_service.update_channel(channel_id, data)
    return {"success": success}


@router.post("/channels/{channel_id}/test")
def test_notification_channel(channel_id: int):
    """Send a test message to verify channel connectivity."""
    existing = notification_service.get_channel(channel_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    try:
        result = notification_service.test_channel(channel_id)
        notification_service.update_channel_status(channel_id, "success")
        return {"success": True, "result": result}
    except Exception as e:
        notification_service.update_channel_status(channel_id, "failed")
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
