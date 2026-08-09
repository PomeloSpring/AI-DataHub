"""Users API routes — user management (admin) and current user profile."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel
from typing import Optional

from services.shared.common.auth import get_current_user, require_admin
from services.authservice.services import auth_service

router = APIRouter()


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    email: Optional[str] = ""
    phone: Optional[str] = ""


class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    new_password: str


class UpdateUserStatusRequest(BaseModel):
    status: str


# ── Current user (profile) ─────────────────────────────────────────────

@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    """Get current user profile."""
    row = auth_service.get_user_by_id(user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return row


@router.put("/me")
def update_me(req: UpdateUserRequest, user: dict = Depends(get_current_user)):
    """Update current user profile."""
    ok, msg = auth_service.update_user(
        user["user_id"],
        username=req.username,
        email=req.email,
        phone=req.phone,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return auth_service.get_user_by_id(user["user_id"])


@router.put("/me/password")
def change_my_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Change current user password."""
    ok, msg = auth_service.change_password(user["user_id"], req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_service.log_audit(user["user_id"], user["username"], "change_password", detail="修改密码")
    return {"success": True, "message": msg}


# ── Admin: User management ─────────────────────────────────────────────

@router.get("/")
def list_users(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    search: str = QueryParam(""),
    role: str = QueryParam(""),
    admin: dict = Depends(require_admin),
):
    """List all users (admin only)."""
    items, total = auth_service.list_users(page=page, size=size, search=search, role=role)
    return {"items": items, "total": total}


@router.post("/")
def create_user(req: CreateUserRequest, admin: dict = Depends(require_admin)):
    """Create a new user (admin only)."""
    ok, msg, uid = auth_service.create_user(
        req.username, req.password, req.role, req.email or "", req.phone or "",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    auth_service.log_audit(admin["user_id"], admin["username"], "create_user",
                           target_type="user", target_id=uid,
                           detail=f"创建用户 {req.username}")
    return {"success": True, "id": uid, "message": msg}


@router.put("/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, admin: dict = Depends(require_admin)):
    """Update a user (admin only)."""
    ok, msg = auth_service.update_user(
        user_id,
        username=req.username,
        email=req.email,
        phone=req.phone,
        role=req.role,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_service.log_audit(admin["user_id"], admin["username"], "update_user",
                           target_type="user", target_id=user_id,
                           detail="更新用户信息")
    return {"success": True, "message": msg}


@router.put("/{user_id}/password")
def reset_user_password(user_id: int, req: ResetPasswordRequest, admin: dict = Depends(require_admin)):
    """Reset a user's password (admin only)."""
    if admin["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="不能通过此接口修改自己的密码，请使用个人设置")
    ok, msg = auth_service.reset_password(user_id, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_service.log_audit(admin["user_id"], admin["username"], "reset_password",
                           target_type="user", target_id=user_id,
                           detail="重置用户密码")
    return {"success": True, "message": msg}


@router.put("/{user_id}/status")
def update_user_status(user_id: int, req: UpdateUserStatusRequest, admin: dict = Depends(require_admin)):
    """Enable or disable a user (admin only)."""
    if admin["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    ok, msg = auth_service.update_user_status(user_id, req.status)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_service.log_audit(admin["user_id"], admin["username"], "update_user_status",
                           target_type="user", target_id=user_id,
                           detail=f"设置用户状态为 {req.status}")
    return {"success": True, "message": msg}


@router.delete("/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    """Delete a user (admin only)."""
    if admin["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    user_row = auth_service.get_user_by_id(user_id)
    target_name = user_row["username"] if user_row else str(user_id)
    ok, msg = auth_service.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_service.log_audit(admin["user_id"], admin["username"], "delete_user",
                           target_type="user", target_id=user_id,
                           detail=f"删除用户 {target_name}")
    return {"success": True, "message": msg}
