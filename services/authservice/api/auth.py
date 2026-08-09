"""Auth API routes — login, token refresh, logout."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.authservice.services import auth_service

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
def login(req: LoginRequest):
    """Authenticate with username and password. Returns JWT token pair."""
    result = auth_service.login(req.username, req.password)

    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if "error" in result:
        raise HTTPException(status_code=403, detail=result["message"])

    return result


@router.post("/refresh")
def refresh(req: RefreshRequest):
    """Exchange a refresh token for a new access token."""
    result = auth_service.refresh_access_token(req.refresh_token)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return result


@router.post("/logout")
def logout():
    """Logout endpoint. JWT tokens are stateless, so this is a no-op.

    Clients should discard their tokens. In a production system this could
    invalidate refresh tokens stored in a database blocklist.
    """
    return {"success": True, "message": "已退出登录"}
