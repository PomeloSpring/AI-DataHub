"""Auth API — JWT login, user management, profile, audit logs."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from backend.common.config import ADH_SECRET_KEY
from backend.common import auth as auth_core
from backend.models.schemas import (
    LoginRequest, LoginResponse, UserInfo, CreateUserRequest,
    UpdateUserRequest, ChangePasswordRequest, ResetPasswordRequest,
    UpdateUserStatusRequest, UserProfile, UserListResponse,
    AuditLogResponse,
)

router = APIRouter()
security = HTTPBearer()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
REFRESH_TOKEN_EXPIRE_DAYS = 7


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, ADH_SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, ADH_SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    return jwt.decode(token, ADH_SECRET_KEY, algorithms=[ALGORITHM])


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserInfo:
    token = credentials.credentials
    try:
        payload = _decode_token(token)
        uid = payload.get("sub")
        if uid is None or payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token")
        return UserInfo(id=int(uid), username=payload.get("username"), role=payload.get("role"))
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _to_user_profile(row: dict) -> UserProfile:
    return UserProfile(
        id=row["id"],
        username=row["username"],
        role=row.get("user_role", "viewer"),
        email=row.get("email", ""),
        phone=row.get("phone", ""),
        avatar=row.get("avatar", ""),
        status=row.get("status", "active"),
        last_login=row.get("last_login"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


# ── Login ───────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    result = auth_core.authenticate(req.username, req.password)

    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if "error" in result:
        raise HTTPException(status_code=403, detail=result["message"])

    ui = UserInfo(id=result["id"], username=result["username"], role=result["role"])
    token_data = {
        "sub": str(result["id"]),
        "username": result["username"],
        "role": result["role"],
    }
    tok = create_access_token(token_data)
    refresh_tok = create_refresh_token(token_data)
    return LoginResponse(access_token=tok, refresh_token=refresh_tok, user=ui)


@router.post("/refresh")
def refresh_token(refresh_token: str):
    """Exchange a refresh token for a new access token."""
    try:
        payload = _decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        uid = payload.get("sub")
        if uid is None:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        # Verify user still exists
        user = auth_core.get_user_by_id(int(uid))
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        # Issue new access token
        token_data = {
            "sub": uid,
            "username": payload.get("username"),
            "role": payload.get("role"),
        }
        new_access = create_access_token(token_data)
        new_refresh = create_refresh_token(token_data)
        return {"access_token": new_access, "refresh_token": new_refresh}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


# ── Current user (profile) ─────────────────────────────────────────────

@router.get("/me", response_model=UserProfile)
def me(user: UserInfo = Depends(get_current_user)):
    row = auth_core.get_user_by_id(user.id)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _to_user_profile(row)


@router.put("/me", response_model=UserProfile)
def update_me(req: UpdateUserRequest, user: UserInfo = Depends(get_current_user)):
    ok, msg = auth_core.update_user(
        user.id,
        username=req.username,
        email=req.email,
        phone=req.phone,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    row = auth_core.get_user_by_id(user.id)
    return _to_user_profile(row)


@router.put("/me/password")
def change_my_password(req: ChangePasswordRequest, user: UserInfo = Depends(get_current_user)):
    ok, msg = auth_core.change_password(user.id, req.old_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_core.log_audit(user.id, user.username, "change_password", detail="修改密码")
    return {"success": True, "message": msg}


# ── Admin: User management ─────────────────────────────────────────────

@router.get("/users", response_model=UserListResponse)
def list_users(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    search: str = QueryParam(""),
    role: str = QueryParam(""),
    admin: UserInfo = Depends(require_admin),
):
    items, total = auth_core.list_users(page=page, size=size, search=search, role=role)
    return UserListResponse(items=[_to_user_profile(u) for u in items], total=total)


@router.post("/users")
def create_user(req: CreateUserRequest, admin: UserInfo = Depends(require_admin)):
    ok, msg, uid = auth_core.create_user(
        req.username, req.password, req.role, req.email or "", req.phone or "",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # Auto-add new user to default workspace
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_workspaces WHERE is_default = 1 LIMIT 1")
                default_ws = cur.fetchone()
                if default_ws:
                    cur.execute(
                        """INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                           VALUES (%s, %s, 'member', 1)""",
                        (default_ws['id'], uid)
                    )
                    conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to add user to default workspace: %s", e)

    auth_core.log_audit(admin.id, admin.username, "create_user",
                        target_type="user", target_id=uid,
                        detail=f"创建用户 {req.username}")
    return {"success": True, "id": uid, "message": msg}


@router.put("/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, admin: UserInfo = Depends(require_admin)):
    ok, msg = auth_core.update_user(
        user_id,
        username=req.username,
        email=req.email,
        phone=req.phone,
        role=req.role,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_core.log_audit(admin.id, admin.username, "update_user",
                        target_type="user", target_id=user_id,
                        detail=f"更新用户信息")
    return {"success": True, "message": msg}


@router.put("/users/{user_id}/password")
def reset_user_password(user_id: int, req: ResetPasswordRequest, admin: UserInfo = Depends(require_admin)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="不能通过此接口修改自己的密码，请使用个人设置")
    ok, msg = auth_core.reset_password(user_id, req.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_core.log_audit(admin.id, admin.username, "reset_password",
                        target_type="user", target_id=user_id,
                        detail="重置用户密码")
    return {"success": True, "message": msg}


@router.put("/users/{user_id}/status")
def update_user_status(user_id: int, req: UpdateUserStatusRequest, admin: UserInfo = Depends(require_admin)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    ok, msg = auth_core.update_user_status(user_id, req.status)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_core.log_audit(admin.id, admin.username, "update_user_status",
                        target_type="user", target_id=user_id,
                        detail=f"设置用户状态为 {req.status}")
    return {"success": True, "message": msg}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: UserInfo = Depends(require_admin)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    # Get username before deletion for audit
    user_row = auth_core.get_user_by_id(user_id)
    target_name = user_row["username"] if user_row else str(user_id)
    ok, msg = auth_core.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    auth_core.log_audit(admin.id, admin.username, "delete_user",
                        target_type="user", target_id=user_id,
                        detail=f"删除用户 {target_name}")
    return {"success": True, "message": msg}


# ── Admin: Audit logs ──────────────────────────────────────────────────

@router.get("/audit-logs", response_model=AuditLogResponse)
def list_audit_logs(
    page: int = QueryParam(1, ge=1),
    size: int = QueryParam(50, ge=1, le=200),
    user_id: int = QueryParam(0),
    action: str = QueryParam(""),
    module: str = QueryParam(""),
    start_date: str = QueryParam(""),
    end_date: str = QueryParam(""),
    keyword: str = QueryParam(""),
    admin: UserInfo = Depends(require_admin),
):
    items, total = auth_core.list_audit_logs(
        page=page, size=size, user_id=user_id, action=action,
        module=module, start_date=start_date, end_date=end_date, keyword=keyword,
    )
    from backend.models.schemas import AuditLogItem
    return AuditLogResponse(
        items=[AuditLogItem(**item) for item in items],
        total=total,
    )
