"""Audit API routes — audit log viewing."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam

from services.shared.common.auth import require_admin
from services.authservice.services import auth_service

router = APIRouter()


@router.get("/logs")
def list_audit_logs(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    user_id: int = QueryParam(0),
    action: str = QueryParam(""),
    admin: dict = Depends(require_admin),
):
    """List audit logs with optional filters (admin only)."""
    items, total = auth_service.list_audit_logs(page=page, size=size, user_id=user_id, action=action)
    return {"items": items, "total": total}
