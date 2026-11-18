"""Audit API routes — audit log viewing."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam

from backend.common.auth import require_admin
from backend.modules.auth.services import auth_service

router = APIRouter()


@router.get("/logs")
def list_audit_logs(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    user_id: int = QueryParam(0),
    action: str = QueryParam(""),
    module: str = QueryParam(""),
    keyword: str = QueryParam(""),
    admin: dict = Depends(require_admin),
):
    """List audit logs with optional filters (admin only).

    筛选（模块/关键词）必须下推 SQL：前端带参而来却在此丢弃，
    会让「筛选了但看起来没生效」被静默成一次全量查询。
    """
    try:
        items, total = auth_service.list_audit_logs(
            page=page, size=size, user_id=user_id, action=action,
            module=module, keyword=keyword,
        )
    except Exception as e:  # noqa: BLE001 —— 查询失败必须显式暴露，不能回 []
        raise HTTPException(status_code=500, detail=f"审计日志查询失败: {e}") from e
    return {"items": items, "total": total}
