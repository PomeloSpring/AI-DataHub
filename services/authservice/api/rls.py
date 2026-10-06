"""RLS (Row-Level Security) API — Policy management endpoints.

Covers:
- RLS policy CRUD (row + column + both)
- Column policy management (per-policy)
- User attribute management (for dynamic row filtering)
- Audit logs
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request Models ────────────────────────────────────────────────

class RLSPolicyCreate(BaseModel):
    name: str
    description: str = ""
    workspace_id: int = 0
    datasource_id: int = 0
    table_name: str
    policy_type: str = "both"         # row / column / both
    filter_type: str = "condition"    # condition / user_attribute
    filter_expr: str = ""
    user_attribute: str = ""
    is_active: int = 1


class RLSPolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    datasource_id: Optional[int] = None
    table_name: Optional[str] = None
    policy_type: Optional[str] = None
    filter_type: Optional[str] = None
    filter_expr: Optional[str] = None
    user_attribute: Optional[str] = None
    is_active: Optional[int] = None


class ColumnPolicyItem(BaseModel):
    column_name: str
    access_type: str = "visible"      # visible / hidden / masked
    mask_pattern: str = ""
    description: str = ""


class ColumnPoliciesBody(BaseModel):
    columns: list[ColumnPolicyItem]


# ── RLS Policy CRUD ──────────────────────────────────────────────

@router.get("/rls-policies")
def list_rls_policies(
    workspace_id: int = Query(0),
    datasource_id: int = Query(None),
    table_name: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _admin: dict = Depends(require_admin),
):
    """List RLS policies."""
    from services.authservice.services.rls_service import rls_service
    return rls_service.list_policies(workspace_id, datasource_id, table_name, page, size)


@router.get("/rls-policies/{policy_id}")
def get_rls_policy(policy_id: int, _admin: dict = Depends(require_admin)):
    """Get a single RLS policy."""
    from services.authservice.services.rls_service import rls_service
    policy = rls_service.get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.post("/rls-policies")
def create_rls_policy(req: RLSPolicyCreate, admin: dict = Depends(require_admin)):
    """Create a new RLS policy."""
    from services.authservice.services.rls_service import rls_service
    try:
        policy_id = rls_service.create_policy({
            "name": req.name,
            "description": req.description,
            "workspace_id": req.workspace_id,
            "datasource_id": req.datasource_id,
            "table_name": req.table_name,
            "policy_type": req.policy_type,
            "filter_type": req.filter_type,
            "filter_expr": req.filter_expr,
            "user_attribute": req.user_attribute,
            "is_active": req.is_active,
            "created_by": admin.get("user_id"),
        })
        # 审计日志：创建策略
        rls_service.log_audit(
            user_id=admin.get("user_id", 0),
            workspace_id=req.workspace_id,
            policy_id=policy_id,
            policy_name=req.name,
            table_name=req.table_name,
            action="policy_create",
            original_sql="",
            filtered_sql=f"type={req.policy_type}, datasource={req.datasource_id}, filter={req.filter_expr}",
        )
        return {"success": True, "id": policy_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/rls-policies/{policy_id}")
def update_rls_policy(policy_id: int, req: RLSPolicyUpdate, admin: dict = Depends(require_admin)):
    """Update an RLS policy."""
    from services.authservice.services.rls_service import rls_service
    try:
        # 先获取旧策略用于审计; 策略不存在必须显式报错, 不得假成功
        old_policy = rls_service.get_policy(policy_id)
        if not old_policy:
            raise HTTPException(status_code=404, detail="策略不存在")
        data = req.model_dump(exclude_unset=True)
        if not rls_service.update_policy(policy_id, data):
            # fail-loud: 无有效可更新字段或更新未落库时不得回"保存成功"
            raise HTTPException(status_code=400, detail="更新未生效：没有可更新的字段")
        # 审计日志：更新策略
        rls_service.log_audit(
            user_id=admin.get("user_id", 0),
            workspace_id=old_policy.get("workspace_id", 0),
            policy_id=policy_id,
            policy_name=old_policy.get("name", ""),
            table_name=old_policy.get("table_name", ""),
            action="policy_update",
            original_sql="",
            filtered_sql=str(data)[:500],
        )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/rls-policies/{policy_id}")
def delete_rls_policy(policy_id: int, admin: dict = Depends(require_admin)):
    """Delete a RLS policy and its column policies."""
    from services.authservice.services.rls_service import rls_service
    try:
        # 先获取旧策略用于审计
        old_policy = rls_service.get_policy(policy_id)
        rls_service.delete_policy(policy_id)
        # 审计日志：删除策略
        if old_policy:
            rls_service.log_audit(
                user_id=admin.get("user_id", 0),
                workspace_id=old_policy.get("workspace_id", 0),
                policy_id=policy_id,
                policy_name=old_policy.get("name", ""),
                table_name=old_policy.get("table_name", ""),
                action="policy_delete",
                original_sql="",
                filtered_sql="",
            )
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Column Policy Endpoints ──────────────────────────────────────

@router.get("/rls-policies/{policy_id}/columns")
def get_column_policies(policy_id: int, _admin: dict = Depends(require_admin)):
    """Get column policies for a given RLS policy."""
    from services.authservice.services.rls_service import rls_service
    return rls_service.get_column_policies(policy_id)


@router.put("/rls-policies/{policy_id}/columns")
def set_column_policies(policy_id: int, body: ColumnPoliciesBody, admin: dict = Depends(require_admin)):
    """Replace all column policies for a given RLS policy."""
    from services.authservice.services.rls_service import rls_service
    try:
        columns = [c.model_dump() for c in body.columns]
        rls_service.set_column_policies(policy_id, columns)
        # 审计日志：更新列策略
        old_policy = rls_service.get_policy(policy_id)
        rls_service.log_audit(
            user_id=admin.get("user_id", 0),
            workspace_id=old_policy.get("workspace_id", 0) if old_policy else 0,
            policy_id=policy_id,
            policy_name=old_policy.get("name", "") if old_policy else "",
            table_name=old_policy.get("table_name", "") if old_policy else "",
            action="column_policy_update",
            original_sql="",
            filtered_sql=str(columns)[:500],
        )
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Audit Logs ───────────────────────────────────────────────────

@router.get("/rls-audit-logs")
def list_audit_logs(
    workspace_id: int = Query(0),
    user_id: int = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _admin: dict = Depends(require_admin),
):
    """List RLS audit logs."""
    from services.authservice.services.rls_service import rls_service
    return rls_service.list_audit_logs(workspace_id, user_id, page, size)


# ── Effective Policies (for other services) ──────────────────────

@router.get("/rls-policies/match/{datasource_id}")
def match_rls_policies(
    datasource_id: int,
    table_name: str = Query(...),
    _admin: dict = Depends(require_admin),
):
    """Get matching RLS policies for a datasource table."""
    from services.authservice.services.rls_service import rls_service
    policies = rls_service.get_matching_policies(datasource_id, table_name) if hasattr(rls_service, 'get_matching_policies') else []
    return {"policies": policies}
