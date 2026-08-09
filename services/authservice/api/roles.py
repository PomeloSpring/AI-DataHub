"""Roles API routes — RBAC role and permission management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from services.shared.common.auth import get_current_user, require_admin
from services.authservice.services import rbac_service
from services.authservice.services.role_service import role_service
from services.authservice.services.rls_service import rls_service

router = APIRouter()


class CreateRoleRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


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
def list_roles(user: dict = Depends(get_current_user)):
    """List all roles."""
    return rbac_service.list_roles()


@router.post("/")
def create_role(req: CreateRoleRequest, admin: dict = Depends(require_admin)):
    """Create a new role (admin only)."""
    ok, msg, role_id = rbac_service.create_role(req.name, req.description or "")
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "id": role_id, "message": msg}


@router.put("/{role_id}")
def update_role(role_id: int, req: UpdateRoleRequest, admin: dict = Depends(require_admin)):
    """Update a role (admin only)."""
    ok, msg = rbac_service.update_role(role_id, name=req.name, description=req.description)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@router.delete("/{role_id}")
def delete_role(role_id: int, admin: dict = Depends(require_admin)):
    """Delete a role (admin only)."""
    ok, msg = rbac_service.delete_role(role_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


# ── Permission management ───────────────────────────────────────────────

@router.get("/{role_id}/permissions")
def get_role_permissions(role_id: int, user: dict = Depends(get_current_user)):
    """Get permissions for a role."""
    role = rbac_service.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    permissions = rbac_service.get_role_permissions(role_id)
    return {"role_id": role_id, "role_name": role["name"], "permissions": permissions}


@router.put("/{role_id}/permissions")
def set_role_permissions(role_id: int, req: SetPermissionsRequest,
                         admin: dict = Depends(require_admin)):
    """Set all permissions for a role (admin only). Replaces existing permissions."""
    ok, msg = rbac_service.set_role_permissions(role_id, req.permissions)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


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
