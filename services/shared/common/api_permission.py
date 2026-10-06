"""API 权限校验中间件 — 按角色白名单控制接口访问.

使用方式:
    from services.shared.common.api_permission import add_api_permission_middleware
    add_api_permission_middleware(app)

规则:
    1. admin 角色始终放行
    2. 未配置任何权限码的角色 = 全部拒绝(fail-closed, 仅白名单路径放行)
    3. 配置了权限码的角色: 请求路径匹配任一已授权权限码绑定的 api_pattern → 放行; 否则 403
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
    ("POST", "/api/auth/refresh"),  # refresh 用 body 中的 refresh_token 鉴权，不依赖 access token
    ("GET", "/api/health"), ("GET", "/health"),
}
_SELF_ROUTES = {
    ("GET", "/api/auth/me"),
    ("GET", "/api/roles/current/permissions"),
    ("GET", "/api/roles/menu-registry"),
}
# 登录自举路由(工作空间随用户走, 个人工作站): 空间列表/详情/默认空间/生效执行层
# 人人可用, 不能按权限码拦(否则无 workspace 码的角色登录后无法自举);
# 属主校验由路由层负责(authorize_workspace/owner 检查)。
_SELF_ROUTE_PATTERNS = re.compile(
    r"^/api/workspaces(?:/\d+)?(?:/set-default)?$"
    r"|^/api/workspace-assets/\d+/.+$"
    r"|^/api/admin/execution-layers/workspaces/\d+/execution-layer$"
    # 会话文件伺服/结构化预览/编辑(session-file 与 xlsx 读写): 仅本人会话,路由层属主自检
    r"|^/api/chat/session-file(?:/.*)?$"
    # 个人自助(个人中心): 读写自己的资料/密码, 属主即本人; 管理域 /api/users/{id} 仍按码
    r"|^/api/users/me(?:/.*)?$"
    # 看板浏览域: 查自己的可见组/看板详情, 可见性由 adh_role_dashboard_access fail-closed 裁决
    # (无授权=空集/404), 不用权限码拦; 看板管理端点仍按码
    r"|^/api/dashboard/groups/visible$"
    r"|^/api/dashboard/\d+$")
# 仅将具体分享入口交给报告领域校验，不能放行整个 reports 前缀。
_REPORT_SHARE = re.compile(r"^/api/(?:reports/\d+(?:/public)?|scheduled-tasks/reports/\d+)$")
# UDF 目录是平台内置函数清单（用户决策：无数据权限隔离，有菜单功能即可见）：
# 普通读放开（仍需登录），管理写操作仍按 udf:manage 码门控。
_OPEN_READ_ROUTES = re.compile(
    r"^/api/udfs(?:/.*)?$"
    # 应用壳读（登录后外壳必需，所有角色可见；对应写仍按码门控）：
    # 品牌配置/菜单树是应用外壳渲染必需读，菜单树本身按角色过滤可见项
    r"|^/api/admin/brand$"
    r"|^/api/(?:admin/)?menu-tree(?:/.*)?$")
# SQL Playground 的纯分析端点（AST / 血缘 / RLS 改写预览 / 溯源）：
# 只做语法解析与绑定解析，**不返回任何数据行**，因此不按权限码门控，但仍要求登录。
# 注意同时覆盖 datamind 侧(/api/playground/*) 与语义层侧(/api/semantic/playground/*)，
# 前者会把请求代理到后者，漏一边就又变成“一边能用一边 401/403”。
_OPEN_ANALYZE_ROUTES = re.compile(
    r"^/api/playground/(?:ast|lineage|rls-diff|provenance)$"
    r"|^/api/semantic/playground/(?:ast|lineage|rls-diff|provenance)$")
# 服务间可信内部调用：无 Bearer 时改验 X-Internal-Identity（服务端签名）。
# datamind 把 playground 分析请求代理到语义层时必须带上该头，
# 否则语义层中间件拿不到任何身份 → 回 401“请先登录”并被代理原样抛给用户。
_INTERNAL_ROUTES = {
    "/api/semantic/query",
    "/api/semantic/playground/ast",
    "/api/semantic/playground/lineage",
    "/api/semantic/playground/rls-diff",
    "/api/semantic/playground/provenance",
}
# 只读文件下载/预览路由: 浏览器 <a download>/<img>/<iframe> 无法携带 Authorization 头,
# 允许用 ?token= 传令牌(与路由层 get_file_user 口径一致), 仅限这些具体路径, 不放行整个前缀。
_QUERY_TOKEN_ROUTES = re.compile(r"^/api/chat/session-file$")


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


def invalidate_perm_cache() -> None:
    """清空角色 API 规则缓存(权限码变更后由写路径调用)."""
    _cache.clear()


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


# ═══════════════════════════════════════════════════════════════════
# 配置变更审计（集中式，不靠各端点自觉补记）
# ═══════════════════════════════════════════════════════════════════
#
# 为什么放中间件：配置变更散落在 8 个服务的几十个写端点里，逐个补 log_audit
# 必然遗漏（此前就是这么漏成"什么都没记"的）。中间件是每个请求的唯一必经点，
# 在这里落审计**对新增端点天然免维护**。
#
# 记什么：谁(user) / 什么动作(action) / 改了哪个资源(target_type+target_id) /
# 请求了哪条路径与结果状态 / 改了哪些字段名。
# **只记字段名不记值** —— 请求体里可能有密码/密钥/连接串（security-guardrails §7），
# 值一律不入审计；要看具体内容走服务端日志。

_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# 不记审计的路径前缀：鉴权类另有 login 审计；取数/会话执行链路另有
# adh_query_audit 与 _log_permission_audit（护栏 §9），不重复记。
# 注意这里用**黑名单**而非白名单：漏一条黑名单 = 多一条噪音，
# 漏一条白名单 = 少一条审计（盲区），审计宁可多记不可漏记。
_AUDIT_SKIP_PREFIXES = (
    "/api/auth/login", "/api/auth/refresh", "/api/auth/me", "/api/auth/logout",
    "/api/health", "/health",
    "/api/chat", "/api/pipeline", "/api/execution", "/api/agent",
    "/api/query", "/api/playground", "/api/history", "/api/semantic",
    "/api/component-data",
)

# target_type → 审计页的模块下拉（对齐 AuditLog.tsx 的 MODULE_OPTIONS）
_AUDIT_MODULE_MAP = {
    "datasource": "datasource", "metadata": "metadata", "table_info": "metadata",
    "column_metadata": "metadata", "model": "model", "model-config": "model",
    "as_bot": "model", "mcp-server": "model", "skill": "model", "prompt": "model",
    "workspace": "workspace", "scheduled_task": "scheduled_task",
    "sync": "scheduled_task", "task_monitor": "scheduled_task",
}

_VERB_BY_METHOD = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}


def _derive_verb(method: str, target_id: int) -> str:
    """推导动作动词。

    POST 到**带 id** 的路径是更新/子资源操作（如 POST /api/roles/5/permissions），
    只有不带 id 的 POST 才是新建；不这样区分的话审计里全是 create，看不出改没改。
    """
    if method == "DELETE":
        return "delete"
    if method == "POST":
        return "update" if target_id else "create"
    return _VERB_BY_METHOD.get(method, "update")


def _should_audit(method: str, path: str) -> bool:
    """是否需要落配置变更审计。返回 False 只因"这不是配置变更"，不因"失败了"。"""
    if method not in _MUTATING_METHODS:
        return False
    p = path.rstrip("/")
    return not any(p == s or p.startswith(s + "/") for s in _AUDIT_SKIP_PREFIXES)


def _singularize(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _derive_audit_target(path: str) -> tuple[str, int, str]:
    """从路径推导 (target_type, target_id, module)。

    例：/api/admin/as-bots/123 -> ("as_bot", 123, "model")；
        /api/roles/5/permissions -> ("role_permission", 5, "system")。
    只做展示/分组用途，推不出就用原始段名，**不静默丢弃**。
    """
    segs = [s for s in path.split("/") if s and s != "api"]
    if segs and segs[0] == "admin":
        segs = segs[1:]
    target_id = 0
    named: list[str] = []
    for s in segs:
        if s.isdigit():
            if target_id == 0:
                target_id = int(s)
            continue
        if s.lower() not in ("edit", "update", "create", "delete"):
            named.append(_singularize(s.replace("-", "_")))
    if not named:
        return ("api", target_id, "system")
    target = "_".join(named[-2:]) if len(named) >= 2 else named[0]
    module = _AUDIT_MODULE_MAP.get(named[0], "system")
    return (target[:32], target_id, module)


async def _audit_body_fields(request: Request) -> list[str]:
    """取请求体的**顶层字段名**（只取名不取值），供审计回答"改了什么"。

    必须在中间件里 await 调用（读 body 是异步），并把结果传给 log_config_change_audit。
    ``await request.body()`` 会把内容缓存在 ``request._body``，下游路由仍能读到。

    非 JSON、或体太大（文件上传等）直接返回空——审计不是吞上传内容的地方。
    抛错也不影响主链路。
    """
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            return []
        if int(request.headers.get("content-length") or 0) > 65536:
            return []
        import json as _json
        body = await request.body()
        if not body:
            return []
        parsed = _json.loads(body.decode("utf-8", "ignore"))
        if isinstance(parsed, dict):
            return sorted(str(k) for k in parsed.keys())[:40]
    except Exception:  # noqa: BLE001 —— 审计增强信息拿不到就不记，不影响主链路
        return []
    return []


def log_config_change_audit(user: dict, request: Request, status: int,
                            note: str = "", body_fields: list[str] | None = None) -> None:
    """落一条配置变更审计（best-effort：失败只记日志，不得影响主链路）。

    合规口径 security-guardrails §9：成功与拒绝均落审计，且审计改动不得使主链路
    抛错。因此这里的异常只 log，不外抛。
    """
    try:
        from services.shared.common.auth import log_audit
        path = request.url.path
        target_type, target_id, module = _derive_audit_target(path)
        verb = _derive_verb(request.method, target_id)
        action = f"{verb}_{target_type}"[:64]
        fields = body_fields or []
        detail = f"{request.method} {path} -> {status}"
        if fields:
            detail += f"；字段: {','.join(fields)}"
        if note:
            detail += f"；{note}"
        ip = request.client.host if request.client else ""
        log_audit(
            int(user.get("user_id") or 0), str(user.get("username") or ""),
            action=action, target_type=target_type, target_id=target_id,
            detail=detail, ip_address=ip, module=module,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[audit] 配置变更审计写入失败（不影响主链路）")


def add_api_permission_middleware(app: FastAPI):
    """为 FastAPI 应用添加 API 权限校验中间件 + 配置变更审计."""

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
        # 先把请求体的字段名取出来（只取名不取值）；await request.body() 会缓存内容，
        # 下游路由仍可正常读到 body。
        audit_fields: list[str] = []
        if _should_audit(request.method, path):
            audit_fields = await _audit_body_fields(request)
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
            if route not in _SELF_ROUTES and not _SELF_ROUTE_PATTERNS.fullmatch(path.rstrip("/")) \
                    and not (request.method == "GET" and _OPEN_READ_ROUTES.fullmatch(path)) \
                    and not _OPEN_ANALYZE_ROUTES.fullmatch(path) \
                    and not await run_in_threadpool(
                check_api_permission, user["role"], request.method, path,
            ):
                # 被拒的变更同样要落审计（护栏 §9：成功与拒绝均落审计）
                await run_in_threadpool(
                    log_config_change_audit, user, request, 403, "权限拒绝", audit_fields)
                raise HTTPException(status_code=403, detail="无权访问该功能")
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except Exception:
            logger.exception("API 权限校验异常，拒绝访问")
            return JSONResponse(status_code=503, content={"detail": "权限服务暂不可用"})
        response = await call_next(request)
        # 配置变更审计：只要身份可信且是变更请求就落一行，不依赖各端点自觉补记。
        if _should_audit(request.method, path):
            await run_in_threadpool(
                log_config_change_audit, user, request, response.status_code, "",
                audit_fields)
        return response


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
