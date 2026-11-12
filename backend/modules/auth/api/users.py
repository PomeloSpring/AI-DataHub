"""Users API routes — user management (admin) and current user profile."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel
from typing import Optional

from backend.common.auth import get_current_user, require_admin
from backend.modules.auth.services import auth_service

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


class UpdatePreferencesRequest(BaseModel):
    """个人偏好（云空间 = 对话产物备份 + 界面动效）。

    cloud_sync_enabled / motion_preset_code：None = 本次不改该项。
    motion_override 是**三态**：请求体里没出现 = 不改；显式传 null = 清除差量（回到纯预设）。
    区分靠 model_fields_set（pydantic 只记录「这次真的传了哪些字段」）——
    拿 None 兼作「不改」和「清空」会让前端一提交就把用户的差量偏好误清掉。
    """
    cloud_sync_enabled: Optional[bool] = None
    motion_preset_code: Optional[str] = None
    motion_override: Optional[dict] = None


# ── Current user (profile) ─────────────────────────────────────────────

@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    """Get current user profile（含角色可读名，个人设置/身份显示用）。"""
    row = auth_service.get_user_profile(user["user_id"])
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
    return auth_service.get_user_profile(user["user_id"])


@router.put("/me/password")
def change_my_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Change current user password."""
    ok, msg = auth_service.change_password(user["user_id"], req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_service.log_audit(user["user_id"], user["username"], "change_password", detail="修改密码")
    return {"success": True, "message": msg}


# ── Current user preferences (工作空间) ──────────────────────────────────

@router.get("/me/preferences")
def get_my_preferences(user: dict = Depends(get_current_user)):
    """个人偏好：会话产物自动上云（云空间备份）开关 + 界面动效预设/差量。

    动效两项无值时回 None（= 跟随系统默认 mp_default），前端不编造「已按默认档」的假状态。
    """
    from backend.modules.mind.execution.session_cloud import cloud_sync_enabled
    from backend.modules.auth.services.user_preference_service import get_motion_preferences
    motion = get_motion_preferences(int(user["user_id"]))
    return {
        "cloud_sync_enabled": cloud_sync_enabled(int(user["user_id"])),
        "motion_preset_code": motion["preset_code"],
        "motion_override": motion["override"],
    }


@router.put("/me/preferences")
def update_my_preferences(req: UpdatePreferencesRequest, user: dict = Depends(get_current_user)):
    """保存个人偏好（按项写，互不覆盖）；回读最新值而不是回显入参，
    避免「界面显示与库里不一致」（与云空间开关同一口径）。
    """
    from backend.modules.mind.execution.session_cloud import set_cloud_sync_enabled
    from backend.modules.auth.services.user_preference_service import (
        set_motion_override, set_motion_preset,
    )
    provided = req.model_fields_set
    user_id = int(user["user_id"])
    facts: list[str] = []
    if "cloud_sync_enabled" in provided and req.cloud_sync_enabled is not None:
        # 只看「传了这个字段且不是 null」：bool(None) 会把显式 null 当“关闭”静默改掉开关
        set_cloud_sync_enabled(user_id, bool(req.cloud_sync_enabled))
        facts.append(f"会话产物自动上云={'开' if req.cloud_sync_enabled else '关'}")
    if "motion_preset_code" in provided:
        try:
            set_motion_preset(user_id, req.motion_preset_code)
        except ValueError as e:
            # 预设已下架/禁用/不存在 → 显式拒收，不能存一个「选了没反应」的编码
            raise HTTPException(status_code=400, detail=str(e)) from e
        facts.append(f"动效预设={req.motion_preset_code or '跟随系统默认'}")
    if "motion_override" in provided:
        try:
            set_motion_override(user_id, req.motion_override)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        facts.append("动效差量=" + ("已清除" if req.motion_override is None else "已保存"))
    if not facts:
        # 只传来一个 null 开关 = 没有任何可保存项，显式拒绝而不是回 200 假装成功
        raise HTTPException(status_code=400, detail="没有需要保存的偏好项")
    auth_service.log_audit(user_id, user.get("username") or "", "update_preferences", detail="；".join(facts))
    return get_my_preferences(user)


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
