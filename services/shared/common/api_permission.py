"""API 权限校验 — 权限标识（permission code）驱动.

统一由「权限点注册表 adh_perm_registry」驱动: 每个权限码声明其覆盖的
API 路径模式(api_pattern + api_method)。角色通过 adh_role_perms 关联权限码。

两种用法:
  1. 全局中间件(推荐, 零侵入): add_api_permission_middleware(app)
     对每个 /api/ 请求, 找出路径+方法命中的权限码集合;
     若该集合非空(说明这些接口受权限管控)且角色不含其中任意一个 → 403。
  2. 路由依赖(精确): Depends(require_permission("ontology:generate"))

规则:
  - admin 角色始终放行
  - 角色未配置任何权限码 = 不限制(向后兼容, 视同拥有全部)
  - 路径未匹配任何权限码的 api_pattern = 不受本中间件管控(交由路由自身 auth)
  - 支持通配 module:* ; 登录/健康检查等白名单路径放行
"""

import fnmatch
import logging
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0
# 角色 → (ts, [perm_code])
_role_perm_cache: dict[str, tuple[float, list[str]]] = {}
# 全量权限规则 → (ts, [(perm_code, api_pattern, method)])
_rule_cache: tuple[float, list[tuple[str, str, str]]] = (0.0, [])

# 始终放行的路径前缀
_WHITELIST_PREFIXES = (
    "/api/auth/",
    "/api/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/roles/current/",        # 前端拉取自身权限
    "/api/roles/perm-registry",   # 权限注册表(配置页渲染用)
)


# ── 数据加载(带 TTL 缓存) ──────────────────────────────────────────

def _load_rules() -> list[tuple[str, str, str]]:
    """加载权限码 → API 模式映射。

    api_pattern / api_method 支持逗号分隔多值(一个权限码覆盖多条路径前缀/多种方法),
    展开为 (perm_code, 单一pattern, 单一method) 规则列表。
    """
    global _rule_cache
    now = time.time()
    if (now - _rule_cache[0]) < _CACHE_TTL:
        return _rule_cache[1]
    rules: list[tuple[str, str, str]] = []
    try:
        from services.shared.common.db import execute_query
        rows = execute_query(
            "SELECT perm_code, api_pattern, api_method FROM adh_perm_registry "
            "WHERE is_active=1 AND api_pattern <> ''"
        )
        for r in (rows or []):
            methods = [m.strip().upper() for m in (r.get("api_method") or "*").split(",") if m.strip()]
            for pat in (p.strip() for p in r["api_pattern"].split(",")):
                if not pat:
                    continue
                for m in methods or ["*"]:
                    rules.append((r["perm_code"], pat, m))
    except Exception as e:
        logger.warning("[Perm] load rules failed: %s", e)
    _rule_cache = (now, rules)
    return rules


def _load_role_perms(role_name: str) -> list[str]:
    """加载角色的权限码集合。"""
    now = time.time()
    hit = _role_perm_cache.get(role_name)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    perms: list[str] = []
    try:
        from services.shared.common.db import execute_query
        role_rows = execute_query("SELECT id FROM adh_roles WHERE name=%s", (role_name,))
        if role_rows:
            rows = execute_query(
                "SELECT perm_code FROM adh_role_perms WHERE role_id=%s", (role_rows[0]["id"],)
            )
            perms = [r["perm_code"] for r in (rows or [])]
    except Exception as e:
        logger.warning("[Perm] load role perms failed role=%s: %s", role_name, e)
    _role_perm_cache[role_name] = (now, perms)
    return perms


def invalidate_perm_cache():
    """权限配置变更后清缓存(角色权限/注册表更新时调用)。"""
    global _rule_cache
    _role_perm_cache.clear()
    _rule_cache = (0.0, [])


# ── 权限判定核心 ────────────────────────────────────────────────────

def _covers(pattern: str, path: str) -> bool:
    return pattern == path or fnmatch.fnmatch(path, pattern)


def _role_has_perm(perms: list[str], perm_code: str) -> bool:
    """角色是否拥有某权限码(支持 module:* 通配)。"""
    if "*" in perms:
        return True
    if perm_code in perms:
        return True
    module = perm_code.split(":", 1)[0]
    return f"{module}:*" in perms


def check_path_permission(role_name: str, method: str, path: str) -> bool:
    """中间件用: 判断角色能否访问 path+method。True=放行。"""
    if role_name == "admin":
        return True
    for prefix in _WHITELIST_PREFIXES:
        if path.startswith(prefix):
            return True

    method_upper = method.upper()
    # 找出覆盖该请求的权限码
    covering = [
        code for code, pat, m in _load_rules()
        if (m == "*" or m == method_upper) and _covers(pat, path)
    ]
    # 无权限码声明覆盖 → 不受管控, 放行(路由自身 auth 处理)
    if not covering:
        return True

    perms = _load_role_perms(role_name)
    # 角色未配置任何权限码 → 不限制(向后兼容)
    if not perms:
        return True

    # 命中覆盖但角色一个都不具备 → 拒绝
    return any(_role_has_perm(perms, c) for c in covering)


def has_permission(user: dict, perm_code: str) -> bool:
    """依赖用: 判断当前用户是否具备指定权限码。"""
    role = (user or {}).get("role") or ""
    if role == "admin":
        return True
    perms = _load_role_perms(role)
    if not perms:
        return True  # 未配置 = 不限制
    return _role_has_perm(perms, perm_code)


def require_permission(perm_code: str):
    """FastAPI 依赖: 要求当前用户具备指定权限码。

    用法: @router.post(...) def handler(user=Depends(require_permission("ontology:generate")))
    返回 user dict 供后续使用。
    """
    from services.shared.common.auth import get_current_user

    async def _dep(user: dict = Depends(get_current_user)):
        if not has_permission(user, perm_code):
            raise HTTPException(status_code=403, detail=f"权限不足: 需要 {perm_code}")
        return user
    return _dep


# ── 中间件注册 ──────────────────────────────────────────────────────

def add_api_permission_middleware(app: FastAPI):
    """为 FastAPI 应用添加权限码驱动的 API 鉴权中间件。"""

    @app.middleware("http")
    async def _api_permission_check(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        for prefix in _WHITELIST_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        role = _extract_role(request)
        if not role:
            return await call_next(request)  # 无 token → 交路由 auth 处理 401

        if not check_path_permission(role, request.method, path):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={"detail": f"权限不足: 角色 '{role}' 无权访问 {request.method} {path}"},
            )
        return await call_next(request)


def _extract_role(request: Request) -> Optional[str]:
    """从 JWT 轻量解析角色(不验签, 由路由 auth 负责校验有效性)。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        import jwt as pyjwt
        payload = pyjwt.decode(auth[7:], options={"verify_signature": False})
        return payload.get("role")
    except Exception:
        return None
