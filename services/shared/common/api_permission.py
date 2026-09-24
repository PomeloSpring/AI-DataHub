"""API 权限校验中间件 — 按角色白名单控制接口访问.

使用方式:
    from services.shared.common.api_permission import add_api_permission_middleware
    add_api_permission_middleware(app)

规则:
    1. admin 角色始终放行
    2. 未配置任何规则的角色 = 不限制(向后兼容)
    3. 配置了规则的角色: 请求路径匹配任一 is_allowed=1 的模式 → 放行; 否则 403
    4. 支持 * 通配符: '/api/ontology/*' 匹配 '/api/ontology/models/123'
    5. method='*' 匹配所有方法; 否则精确匹配
    6. 白名单路径(登录/健康检查)始终放行
"""

import fnmatch
import logging
import re
import time
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── 缓存: 角色 API 规则 (TTL 60s) ──────────────────────────────────

_CACHE_TTL = 0.0
_cache: dict[str, tuple[float, list[dict]]] = {}  # role_name → (timestamp, rules)

# 始终放行的路径前缀
_PUBLIC_ROUTES = {
    ("POST", "/api/auth/login"),
    ("GET", "/api/health"), ("GET", "/health"),
}
_SELF_ROUTES = {
    ("GET", "/api/auth/me"),
    ("GET", "/api/roles/current/permissions"),
    ("GET", "/api/roles/menu-registry"),
}
# 仅将具体分享入口交给报告领域校验，不能放行整个 reports 前缀。
_REPORT_SHARE = re.compile(r"^/api/(?:reports/\d+(?:/public)?|scheduled-tasks/reports/\d+)$")
_INTERNAL_ROUTES = {"/api/semantic/query", "/api/semantic/playground/rls-diff"}
# 只读文件下载/预览路由: 浏览器 <a download>/<img>/<iframe> 无法携带 Authorization 头,
# 允许用 ?token= 传令牌(与路由层 get_file_user 口径一致), 仅限这些具体路径, 不放行整个前缀。
_QUERY_TOKEN_ROUTES = re.compile(r"^/api/chat/(?:attachments/[^/]+/file|session-file)$")


def _load_role_apis(role_name: str) -> list[dict]:
    """从 DB 加载角色的 API 规则(带 TTL 缓存)."""
    now = time.time()
    hit = _cache.get(role_name)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]

    try:
        from services.shared.common.db import execute_query
        # 查角色 ID
        role_rows = execute_query("SELECT id FROM adh_roles WHERE name = %s", (role_name,))
        if not role_rows:
            rules = []
        else:
            role_id = role_rows[0]["id"]
            grants = execute_query(
                "SELECT perm_code FROM adh_role_perms WHERE role_id = %s", (role_id,))
            codes = {r["perm_code"] for r in (grants or [])}
            rows = execute_query(
                "SELECT perm_code, api_pattern, api_method FROM adh_perm_registry WHERE is_active=1") if codes else []
            rules = []
            for row in rows or []:
                code = row["perm_code"]
                if not (code in codes or "*" in codes or code.split(":", 1)[0] + ":*" in codes):
                    continue
                for pattern in (row.get("api_pattern") or "").split(","):
                    for method in (row.get("api_method") or "*").split(","):
                        if pattern.strip() and method.strip():
                            rules.append({"pattern": pattern.strip(), "method": method.strip().upper(), "allowed": True})
    except Exception as e:
        logger.warning("[ApiPerm] Failed to load rules for role=%s: %s", role_name, e)
        rules = []

    _cache[role_name] = (now, rules)
    return rules


def _match_path(pattern: str, path: str) -> bool:
    """fnmatch 风格匹配: /api/ontology/* 匹配 /api/ontology/models/123."""
    # 精确匹配
    if pattern == path:
        return True
    # 通配符匹配
    return fnmatch.fnmatch(path, pattern)


def check_api_permission(role_name: str, method: str, path: str) -> bool:
    """检查角色是否有权访问指定 API.

    Returns True = 允许, False = 拒绝.
    """
    # admin 始终放行
    if role_name == "admin":
        return True

    if (method.upper(), path.rstrip("/")) in _PUBLIC_ROUTES:
        return True
    if not role_name:
        return False
    return any(
        rule["allowed"] and rule["method"] in ("*", method.upper())
        and _match_path(rule["pattern"], path)
        for rule in _load_role_apis(role_name)
    )


def add_api_permission_middleware(app: FastAPI):
    """为 FastAPI 应用添加 API 权限校验中间件."""

    if getattr(app.state, "api_permission_registered", False):
        return
    app.state.api_permission_registered = True

    @app.middleware("http")
    async def api_permission_check(request: Request, call_next):
        # 跳过 OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # 非 /api/ 路径放行(静态资源等)
        if not path.startswith("/api/"):
            return await call_next(request)

        route = (request.method, path.rstrip("/"))
        if route in _PUBLIC_ROUTES:
            return await call_next(request)
        from services.shared.common import auth
        try:
            header = request.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                user = await run_in_threadpool(auth.resolve_current_user, header[7:])
                request.state.current_user = user
            elif request.method == "GET" and _REPORT_SHARE.fullmatch(path):
                return await call_next(request)
            elif request.method == "GET" and _QUERY_TOKEN_ROUTES.fullmatch(path) and request.query_params.get("token"):
                user = await run_in_threadpool(auth.resolve_current_user, request.query_params["token"])
                request.state.current_user = user
            elif path in _INTERNAL_ROUTES:
                identity = auth.verify_internal_identity(request.headers.get("X-Internal-Identity", ""))
                if not identity or not identity.get("user_id"):
                    raise HTTPException(status_code=401, detail="缺少可信身份")
                live = await run_in_threadpool(auth.get_user_by_id, identity["user_id"])
                if not live or live.get("status") != "active":
                    raise HTTPException(status_code=401, detail="身份已失效")
                user = {**identity, "role": live.get("user_role") or "viewer", "username": live.get("username") or ""}
                await run_in_threadpool(auth.authorize_workspace, user, identity["workspace_id"])
                request.state.current_user = user
            else:
                raise HTTPException(status_code=401, detail="请先登录")
            if route not in _SELF_ROUTES and not await run_in_threadpool(
                check_api_permission, user["role"], request.method, path,
            ):
                raise HTTPException(status_code=403, detail="无权访问该功能")
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except Exception:
            logger.exception("API 权限校验异常，拒绝访问")
            return JSONResponse(status_code=503, content={"detail": "权限服务暂不可用"})
        return await call_next(request)


def _extract_role_from_request(request: Request) -> Optional[str]:
    """从 JWT 中轻量提取角色(不做签名验证,由路由 auth 依赖负责).

    如果 token 无效/过期,返回 None 让路由层处理 401.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    try:
        from services.shared.common.auth import resolve_current_user
        return resolve_current_user(token).get("role")
    except Exception:
        return None
