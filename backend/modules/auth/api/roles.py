"""Roles API routes — RBAC role and permission management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.common.auth import get_current_user, require_admin
from backend.common.db import execute_query, execute_write
from backend.modules.auth.services import rbac_service
from backend.core.role_service import role_service
from backend.core.rls_service import rls_service

router = APIRouter()


class CreateRoleRequest(BaseModel):
    name: str
    display_name: Optional[str] = ""
    description: Optional[str] = ""


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[int] = None


class AssignUserRoleRequest(BaseModel):
    user_id: int
    workspace_id: int = 0


class SetPermissionsRequest(BaseModel):
    permissions: list[str]


class SetDatasourceAccessRequest(BaseModel):
    datasource_ids: list[int]


class SetDashboardAccessRequest(BaseModel):
    dashboard_ids: list[int]


class SetRoleRLSPoliciesRequest(BaseModel):
    policy_ids: list[int]


class CreateRLSPolicyRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    workspace_id: int
    datasource_id: int
    table_name: str
    policy_type: str = "both"  # row/column/both
    filter_type: str = "condition"  # condition/user_attribute
    filter_expr: str = ""
    user_attribute: str = ""
    is_active: int = 1


class SetRLSColumnPoliciesRequest(BaseModel):
    columns: list[dict]  # [{column_name, access_type, mask_pattern, description}]


# ── Role CRUD ───────────────────────────────────────────────────────────

@router.get("/")
def list_roles(workspace_id: int = Query(None), user: dict = Depends(get_current_user)):
    """List all roles."""
    return role_service.list_roles(workspace_id)


@router.get("/perm-registry")
def list_perm_registry(user: dict = Depends(get_current_user)):
    """获取全部权限点, 供角色配置页「菜单与功能」分组渲染.

    - groups: 按模块分组(兼容保留)
    - menu_groups: 按菜单分组(菜单→功能), menu_key 为空或指向不存在菜单的权限码归 standalone
    - 每个权限点附 api_pattern/api_method 只读字段(接口→权限码绑定由注册表维护, 角色侧不可编辑)
    - 每个权限点附 AI 能力字段: ai_access(配置级别)/ai_effective(生效级别)/
      ai_locked(涉密硬上界)/ai_action_key(登记了才生成 LLM 功能工具)/ai_note(原因)
    """
    rows = execute_query(
        "SELECT perm_code, label, module, module_label, description, menu_key, sort, "
        "       api_pattern, api_method, ai_access, ai_action_key, ai_note "
        "FROM adh_perm_registry WHERE is_active=1 ORDER BY sort"
    ) or []
    menus = {
        m["menu_key"]: m
        for m in (execute_query(
            "SELECT menu_key, label, section, module, sort, ai_access FROM adh_menu_registry WHERE is_active=1") or [])
    }

    # 涉密硬上界在代码层（perm_link），DB 配置抬不上去；
    # 前端需要同时看到「配置值」与「生效值」，否则会以为改了就生效。
    from backend.modules.mind.execution import perm_link

    def _item(r):
        code = r["perm_code"]
        configured = r.get("ai_access") or "none"
        effective = perm_link.cap_level(code, configured)
        locked = code in perm_link.SECRET_BOUND_NONE_PERMS or code in perm_link.SECRET_BOUND_READ_ONLY_PERMS
        return {
            "perm_code": code, "label": r["label"],
            "description": r.get("description") or "", "menu_key": r.get("menu_key") or "",
            "api_pattern": r.get("api_pattern") or "", "api_method": r.get("api_method") or "*",
            "ai_access": configured, "ai_effective": effective, "ai_locked": bool(locked),
            "ai_action_key": r.get("ai_action_key") or "", "ai_note": r.get("ai_note") or "",
        }

    grouped: dict[str, dict] = {}
    by_menu: dict[str, dict] = {}
    standalone: list = []
    for r in rows:
        g = grouped.setdefault(r["module"], {"module": r["module"], "module_label": r.get("module_label") or r["module"], "permissions": []})
        g["permissions"].append(_item(r))
        menu_key = (r.get("menu_key") or "").strip()
        menu = menus.get(menu_key)
        if not menu:
            standalone.append(_item(r))
            continue
        mg = by_menu.setdefault(menu_key, {
            "menu_key": menu_key, "label": menu["label"], "section": menu.get("section") or "",
            "module": menu.get("module") or r["module"], "module_label": r.get("module_label") or r["module"],
            "sort": menu.get("sort") or 0, "permissions": [],
            # 菜单级 AI 默认值，仅供前端批量套用；生效级别以各权限点为准。
            "ai_access": menu.get("ai_access") or "none",
        })
        mg["permissions"].append(_item(r))

    module_order = {"system": 0, "data": 1, "workspace": 2}
    menu_groups = sorted(
        by_menu.values(),
        key=lambda g: (module_order.get(g["module"], 99), g["sort"], g["label"]),
    )
    return {"groups": list(grouped.values()), "menu_groups": menu_groups, "standalone": standalone}


class SetAiAccessRequest(BaseModel):
    ai_access: str  # none | read | write
    ai_note: Optional[str] = None  # 缺省 = 保留原有原因文案


@router.put("/perm-registry/{perm_code}/ai-access")
def set_perm_ai_access(perm_code: str, body: SetAiAccessRequest,
                       user: dict = Depends(require_admin)):
    """设置功能项的 AI 可调用级别（admin）。

    涉密项被代码层硬上界（perm_link.SECRET_BOUND_*）卡死：请求把它设为
    更高级别时**明确拒绝并说明原因**，不做静默降级 —— 让管理员知道改不动，
    而不是以为改成功了（no-silent-degradation §1）。
    """
    from backend.modules.mind.execution import perm_link

    try:
        level = perm_link.normalize_level(body.ai_access)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = execute_query("SELECT perm_code, label FROM adh_perm_registry WHERE perm_code=%s",
                         (perm_code,)) or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"权限点「{perm_code}」不存在")
    label = rows[0].get("label") or perm_code

    effective = perm_link.cap_level(perm_code, level)
    if effective != level:
        raise HTTPException(
            status_code=409,
            detail=(f"「{label}」涉及敏感信息，AI 可调用级别最高只能是"
                    f"「{'只读可见' if effective == perm_link.AI_LEVEL_READ else '不开放给 AI'}」，"
                    f"不能设为「{'可读可写' if level == perm_link.AI_LEVEL_WRITE else '只读可见'}」。"
                    f"原因：{body.ai_note or '该功能涉及密钥/凭据/权限配置'}"))

    note = (body.ai_note or "").strip()[:255] if body.ai_note is not None else None
    if note is None:
        execute_write("UPDATE adh_perm_registry SET ai_access=%s WHERE perm_code=%s",
                      (level, perm_code))
        current_note = (execute_query("SELECT ai_note FROM adh_perm_registry WHERE perm_code=%s",
                                      (perm_code,)) or [{}])[0].get("ai_note") or ""
    else:
        execute_write("UPDATE adh_perm_registry SET ai_access=%s, ai_note=%s WHERE perm_code=%s",
                      (level, note, perm_code))
        current_note = note
    return {"perm_code": perm_code, "label": label, "ai_access": level,
            "ai_effective": effective, "ai_note": current_note}


@router.get("/current/permissions")
def get_current_user_permissions(user: dict = Depends(get_current_user)):
    """返回当前用户的权限码集合与可访问菜单 key(前端据此渲染菜单与按钮).

    - admin: unrestricted=True, 全部放行
    - 角色未绑定任何权限码: unrestricted=False, 不可用任何功能/菜单(fail-closed)
    - 否则: 返回权限码列表 + 由权限码反查的可访问菜单 key
    """
    role_name = user.get("role") or ""
    all_menus = [r["menu_key"] for r in (execute_query(
        "SELECT menu_key FROM adh_menu_registry WHERE is_active=1") or [])]
    if role_name == "admin":
        return {"permissions": ["*"], "menus": all_menus, "unrestricted": True}

    role_rows = execute_query("SELECT id FROM adh_roles WHERE name=%s", (role_name,))
    if not role_rows:
        return {"permissions": [], "menus": [], "unrestricted": False}
    role_id = role_rows[0]["id"]
    rows = execute_query("SELECT perm_code FROM adh_role_perms WHERE role_id=%s", (role_id,))
    perms = [r["perm_code"] for r in (rows or [])]
    # 未配置不授予任何功能权限。
    if not perms:
        return {"permissions": [], "menus": [], "unrestricted": False}
    # 由权限码反查可访问菜单
    perm_set = set(perms)
    menus = set()
    for mrow in (execute_query(
        "SELECT menu_key, perm_code FROM adh_menu_registry WHERE is_active=1 AND perm_code<>''") or []):
        pc = mrow["perm_code"]
        module = pc.split(":", 1)[0]
        if pc in perm_set or f"{module}:*" in perm_set or "*" in perm_set:
            menus.add(mrow["menu_key"])
    return {"permissions": perms, "menus": sorted(menus), "unrestricted": False}


@router.get("/dashboard-catalog")
def list_dashboard_catalog(admin: dict = Depends(require_admin)):
    """全部看板清单 — 供角色配置页「看板」选择器(仅 admin)。

    必须定义在 /{role_id} 之前, 否则会被当作 role_id 解析(422)。
    """
    return role_service.list_dashboard_catalog()


@router.get("/{role_id}")
def get_role(role_id: int, user: dict = Depends(get_current_user)):
    """Get a single role with its attributes and assigned users."""
    role = role_service.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@router.post("/")
def create_role(req: CreateRoleRequest, admin: dict = Depends(require_admin)):
    """Create a new role (admin only)."""
    if not req.name:
        raise HTTPException(status_code=400, detail="角色标识不能为空")
    try:
        role_id = role_service.create_role(
            req.name, req.display_name or req.name, req.description or ""
        )
    except Exception:  # noqa: BLE001 — duplicate name / constraint violation
        raise HTTPException(status_code=400, detail="角色名已存在或创建失败")
    return {"success": True, "id": role_id}


@router.put("/{role_id}")
def update_role(role_id: int, req: UpdateRoleRequest, admin: dict = Depends(require_admin)):
    """Update a role (admin only)."""
    data = {k: v for k, v in {
        "display_name": req.display_name,
        "description": req.description,
        "is_active": req.is_active,
    }.items() if v is not None}
    if not data:
        return {"success": True, "message": "无需更新"}
    ok = role_service.update_role(role_id, data)
    if not ok:
        raise HTTPException(status_code=400, detail="更新失败")
    return {"success": True}


@router.delete("/{role_id}")
def delete_role(role_id: int, admin: dict = Depends(require_admin)):
    """Delete a role (admin only)."""
    ok = role_service.delete_role(role_id)
    if not ok:
        raise HTTPException(status_code=400, detail="系统角色不可删除或不存在")
    return {"success": True}


# ── Permission management (权限码模型: adh_perm_registry + adh_role_perms) ──

@router.get("/{role_id}/permissions")
def get_role_permissions(role_id: int, user: dict = Depends(get_current_user)):
    """获取角色绑定的权限码."""
    rows = execute_query("SELECT perm_code FROM adh_role_perms WHERE role_id=%s ORDER BY perm_code", (role_id,))
    return {"role_id": role_id, "permissions": [r["perm_code"] for r in (rows or [])]}


@router.put("/{role_id}/permissions")
def set_role_permissions(role_id: int, req: SetPermissionsRequest,
                         admin: dict = Depends(require_admin)):
    """全量替换角色权限码(仅 admin). 空列表=清空全部功能权限(fail-closed, 该角色不可用)."""
    role = execute_query("SELECT id FROM adh_roles WHERE id=%s", (role_id,), fetchone=True)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    execute_write("DELETE FROM adh_role_perms WHERE role_id=%s", (role_id,))
    for pc in set(req.permissions or []):
        pc = (pc or "").strip()
        if pc:
            execute_write("INSERT IGNORE INTO adh_role_perms (role_id, perm_code) VALUES (%s,%s)", (role_id, pc))
    # 使中间件缓存失效
    try:
        from backend.common.api_permission import invalidate_perm_cache
        invalidate_perm_cache()
    except Exception:
        pass
    return {"success": True, "count": len(req.permissions or [])}


# ── Datasource Access ───────────────────────────────────────────────────

@router.get("/{role_id}/datasources")
def get_role_datasources(role_id: int, user: dict = Depends(get_current_user)):
    """Get datasources a role can access."""
    return role_service.get_role_datasources(role_id)


@router.put("/{role_id}/datasources")
def set_role_datasources(role_id: int, req: SetDatasourceAccessRequest,
                         admin: dict = Depends(require_admin)):
    """Set datasource access for a role (admin only)."""
    ok = role_service.set_role_datasources(role_id, req.datasource_ids)
    return {"success": ok}


# ── Dashboard Visibility (看板可见性按角色授权) ───────────────────────

@router.get("/{role_id}/dashboards")
def get_role_dashboards(role_id: int, user: dict = Depends(get_current_user)):
    """Get dashboard IDs visible to a role (fail-closed: 空=一律不可见)."""
    return role_service.get_role_dashboards(role_id)


@router.put("/{role_id}/dashboards")
def set_role_dashboards(role_id: int, req: SetDashboardAccessRequest,
                        admin: dict = Depends(require_admin)):
    """全量替换角色可见看板(仅 admin). 空列表=全部不可见."""
    role = execute_query("SELECT id FROM adh_roles WHERE id=%s", (role_id,), fetchone=True)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    ok = role_service.set_role_dashboards(role_id, req.dashboard_ids)
    return {"success": ok, "count": len(set(req.dashboard_ids or []))}


# ── RLS Policy Binding (数据安全策略绑定: 勾选才生效) ──────────────────

@router.get("/{role_id}/rls-policies")
def get_role_rls_policies(role_id: int, user: dict = Depends(get_current_user)):
    """Get data-security policy IDs bound to a role."""
    rows = execute_query(
        "SELECT policy_id FROM adh_role_rls_policies WHERE role_id=%s ORDER BY policy_id",
        (role_id,))
    return {"role_id": role_id, "policy_ids": [r["policy_id"] for r in (rows or [])]}


@router.put("/{role_id}/rls-policies")
def set_role_rls_policies(role_id: int, req: SetRoleRLSPoliciesRequest,
                          admin: dict = Depends(require_admin)):
    """全量替换角色绑定的数据安全策略(仅 admin).

    勾选才生效: 仅绑定的策略施加行/列限制; 空列表=不施加任何 RLS 限制。
    """
    role = execute_query("SELECT id FROM adh_roles WHERE id=%s", (role_id,), fetchone=True)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    execute_write("DELETE FROM adh_role_rls_policies WHERE role_id=%s", (role_id,))
    for pid in set(req.policy_ids or []):
        execute_write(
            "INSERT IGNORE INTO adh_role_rls_policies (role_id, policy_id) VALUES (%s,%s)",
            (role_id, int(pid)))
    # 使 enforcer 侧访问缓存失效(行/列限制变更需即时生效)
    try:
        from backend.core.enforcer import invalidate_access_cache
        invalidate_access_cache()
    except Exception:  # noqa: BLE001 — enforcer 不可用时不影响绑定保存
        pass
    return {"success": True, "count": len(set(req.policy_ids or []))}


# ── Role User Assignment ───────────────────────────────────────

@router.post("/{role_id}/users")
def assign_role_user(role_id: int, req: AssignUserRoleRequest,
                     admin: dict = Depends(require_admin)):
    """Assign a user to a role (admin only)."""
    ok = role_service.assign_user_role(req.user_id, role_id, req.workspace_id)
    return {"success": ok}


@router.delete("/{role_id}/users/{user_id}")
def remove_role_user(role_id: int, user_id: int, workspace_id: int = Query(0),
                     admin: dict = Depends(require_admin)):
    """Remove a user from a role (admin only)."""
    ok = role_service.remove_user_role(user_id, role_id, workspace_id)
    return {"success": ok}


# ── User Permission Summary ────────────────────────────────────────────

@router.get("/user/{user_id}/permissions")
def get_user_permissions(
    user_id: int,
    workspace_id: int = Query(0),
    admin: dict = Depends(require_admin),
):
    """Get effective permissions for a user in a workspace."""
    roles = role_service.get_user_roles(user_id, workspace_id)
    return {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "roles": [{"id": r["id"], "name": r["name"]} for r in roles],
        "attributes": role_service.get_user_effective_attributes(user_id, workspace_id),
    }


# ── RLS Policies ────────────────────────────────────────────────────────

@router.get("/rls/policies")
def list_rls_policies(
    workspace_id: int = Query(...),
    datasource_id: int = Query(None),
    table_name: str = Query(None),
    page: int = Query(1),
    size: int = Query(20),
    user: dict = Depends(get_current_user),
):
    """List RLS policies."""
    return rls_service.list_policies(workspace_id, datasource_id, table_name, page, size)


@router.post("/rls/policies")
def create_rls_policy(req: CreateRLSPolicyRequest, admin: dict = Depends(require_admin)):
    """Create a new RLS policy (admin only)."""
    policy_id = rls_service.create_policy(req.model_dump())
    return {"success": True, "id": policy_id}


@router.put("/rls/policies/{policy_id}")
def update_rls_policy(policy_id: int, data: dict, admin: dict = Depends(require_admin)):
    """Update an RLS policy (admin only)."""
    ok = rls_service.update_policy(policy_id, data)
    return {"success": ok}


@router.delete("/rls/policies/{policy_id}")
def delete_rls_policy(policy_id: int, admin: dict = Depends(require_admin)):
    """Delete an RLS policy (admin only)."""
    ok = rls_service.delete_policy(policy_id)
    return {"success": ok}


@router.get("/rls/policies/{policy_id}/columns")
def get_rls_column_policies(policy_id: int, user: dict = Depends(get_current_user)):
    """Get column policies for an RLS policy."""
    return rls_service.get_column_policies(policy_id)


@router.put("/rls/policies/{policy_id}/columns")
def set_rls_column_policies(
    policy_id: int,
    req: SetRLSColumnPoliciesRequest,
    admin: dict = Depends(require_admin),
):
    """Set column policies for an RLS policy (admin only)."""
    ok = rls_service.set_column_policies(policy_id, req.columns)
    return {"success": ok}

