"""Roles API routes — RBAC role and permission management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from services.shared.common.auth import get_current_user, require_admin
from services.shared.common.db import execute_query, execute_write
from services.authservice.services import rbac_service
from services.authservice.services.role_service import role_service
from services.authservice.services.rls_service import rls_service

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


class SetRoleAttributesRequest(BaseModel):
    workspace_id: int = 0
    attributes: dict = {}


class AssignUserRoleRequest(BaseModel):
    user_id: int
    workspace_id: int = 0


class SetPermissionsRequest(BaseModel):
    permissions: list[str]


class SetDatasourceAccessRequest(BaseModel):
    datasource_ids: list[int]


class SetTableAccessRequest(BaseModel):
    tables: list[dict]  # [{datasource_id, table_name, access_type}]


class SetColumnAccessRequest(BaseModel):
    columns: list[dict]  # [{datasource_id, table_name, column_name, access_type, mask_pattern}]


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


class SetUserAttributesRequest(BaseModel):
    attributes: dict  # {attr_key: attr_value}


# ── Role CRUD ───────────────────────────────────────────────────────────

@router.get("/")
def list_roles(workspace_id: int = Query(None), user: dict = Depends(get_current_user)):
    """List all roles."""
    return role_service.list_roles(workspace_id)


@router.get("/perm-registry")
def list_perm_registry(user: dict = Depends(get_current_user)):
    """获取全部权限点(按模块分组), 供角色配置页渲染."""
    rows = execute_query(
        "SELECT perm_code, label, module, module_label, description, menu_key, sort "
        "FROM adh_perm_registry WHERE is_active=1 ORDER BY sort"
    )
    grouped: dict[str, dict] = {}
    for r in (rows or []):
        g = grouped.setdefault(r["module"], {"module": r["module"], "module_label": r.get("module_label") or r["module"], "permissions": []})
        g["permissions"].append({
            "perm_code": r["perm_code"], "label": r["label"],
            "description": r.get("description") or "", "menu_key": r.get("menu_key") or "",
        })
    return {"groups": list(grouped.values())}


@router.get("/current/permissions")
def get_current_user_permissions(user: dict = Depends(get_current_user)):
    """返回当前用户的权限码集合与可访问菜单 key(前端据此渲染菜单与按钮).

    - admin: unrestricted=True, 全部放行
    - 角色未绑定任何权限码: unrestricted=True(不限制, 向后兼容)
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
    # 未配置 = 不限制
    if not perms:
        return {"permissions": [], "menus": all_menus, "unrestricted": True}
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
    """全量替换角色权限码(仅 admin). 空列表=不限制(拥有全部)."""
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
        from services.shared.common.api_permission import invalidate_perm_cache
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


# ── Table Access ────────────────────────────────────────────────────────

@router.get("/{role_id}/tables")
def get_role_tables(role_id: int, user: dict = Depends(get_current_user)):
    """Get tables a role can access."""
    return role_service.get_role_tables(role_id)


@router.put("/{role_id}/tables")
def set_role_tables(role_id: int, req: SetTableAccessRequest,
                    admin: dict = Depends(require_admin)):
    """Set table access for a role (admin only)."""
    ok = role_service.set_role_tables(role_id, req.tables)
    return {"success": ok}


# ── Column Access ───────────────────────────────────────────────────────

@router.get("/{role_id}/columns")
def get_role_columns(role_id: int, user: dict = Depends(get_current_user)):
    """Get column permissions for a role."""
    return role_service.get_role_columns(role_id)


@router.put("/{role_id}/columns")
def set_role_columns(role_id: int, req: SetColumnAccessRequest,
                     admin: dict = Depends(require_admin)):
    """Set column access for a role (admin only)."""
    ok = role_service.set_role_columns(role_id, req.columns)
    return {"success": ok}


# ── Role Attributes (Data Scope) ───────────────────────────────

@router.get("/{role_id}/attributes")
def get_role_attributes(role_id: int, workspace_id: int = Query(0),
                        user: dict = Depends(get_current_user)):
    """Get role attributes as {key: value} for a workspace."""
    return role_service.get_role_attributes(role_id, workspace_id)


@router.put("/{role_id}/attributes")
def set_role_attributes(role_id: int, req: SetRoleAttributesRequest,
                        admin: dict = Depends(require_admin)):
    """Set (replace) role attributes for a workspace (admin only)."""
    ok = role_service.set_role_attributes(role_id, req.workspace_id, req.attributes)
    return {"success": ok}


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


# ── User RLS Attributes ────────────────────────────────────────────────

@router.get("/rls/users/{user_id}/attributes")
def get_user_rls_attributes(
    user_id: int,
    workspace_id: int = Query(...),
    user: dict = Depends(get_current_user),
):
    """Get RLS attributes for a user."""
    return rls_service.get_user_attributes(user_id, workspace_id)


@router.put("/rls/users/{user_id}/attributes")
def set_user_rls_attributes(
    user_id: int,
    workspace_id: int = Query(...),
    req: SetUserAttributesRequest = ...,
    admin: dict = Depends(require_admin),
):
    """Set RLS attributes for a user (admin only)."""
    ok = rls_service.set_user_attributes(user_id, workspace_id, req.attributes)
    return {"success": ok}
