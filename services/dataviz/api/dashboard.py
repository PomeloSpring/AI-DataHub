"""Dashboard API -- CRUD for dashboards, charts, snapshots, and data sources.

Migrated from backend/api/dashboard.py. Uses service layer for all business logic.
"""

import logging

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel, field_validator
from typing import Optional

from services.shared.common.auth import get_current_user, get_workspace_id, require_admin
from services.shared.common.db import DBConnection
from services.dataviz.services.governed_query import NoIdentityError
from services.dataviz.services.dashboard_service import (
    dashboard_service,
    chart_service,
    snapshot_service,
    preview_saved_query,
    list_datasource_aggregations,
    visible_dashboard_ids,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic Models ─────────────────────────────────────────────────────────


class DashboardCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    layout: Optional[list] = []
    filters: Optional[dict] = {}
    params: Optional[list] = []
    status: Optional[str] = "designing"
    is_public: bool = False
    is_default: bool = False
    carousel_interval: int = 0
    workspace_id: Optional[int] = 0


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    layout: Optional[list] = None
    filters: Optional[dict] = None
    params: Optional[list] = None
    status: Optional[str] = None
    is_public: Optional[bool] = None
    is_default: Optional[bool] = None
    carousel_interval: Optional[int] = None


class ChartCreate(BaseModel):
    name: str
    chart_type: str
    sql_query: Optional[str] = None
    config: Optional[dict] = None
    position: Optional[dict] = None
    source_type: Optional[str] = "query"
    source_id: Optional[int] = None
    # Phase 5: 大屏语义化 — 存声明式 SemanticQuery 而非裸 SQL
    semantic_query: Optional[dict] = None
    query_source: Optional[str] = "raw_sql"  # raw_sql | semantic


class ChartUpdate(BaseModel):
    name: Optional[str] = None
    chart_type: Optional[str] = None
    sql_query: Optional[str] = None
    config: Optional[dict] = None
    position: Optional[dict] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    semantic_query: Optional[dict] = None
    query_source: Optional[str] = None

    @field_validator("name", "chart_type", "source_type", "query_source")
    @classmethod
    def validate_required_metadata(cls, value):
        if value is None or not value.strip():
            raise ValueError("已提交的元数据字段不能为空")
        return value


class ChartRefreshRequest(BaseModel):
    params: Optional[dict] = {}
    page_limit: Optional[int] = None
    page_offset: Optional[int] = None
    count_sql: Optional[str] = None


class LayoutItem(BaseModel):
    chart_id: int
    position: dict


class LayoutRequest(BaseModel):
    layouts: list[LayoutItem]


class ReorderRequest(BaseModel):
    orders: list[dict]


class SetVisibleRolesRequest(BaseModel):
    role_ids: list[int]


# ── Dashboard Endpoints ─────────────────────────────────────────────────────


@router.get("/")
def list_dashboards_endpoint(
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """List dashboards (workspace scoped, 按角色可见集过滤)."""
    try:
        return dashboard_service.list_dashboards(
            user["user_id"], workspace_id, user.get("role") or "")
    except Exception as e:
        logger.exception("Failed to list dashboards")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_dashboard_endpoint(
    req: DashboardCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new dashboard."""
    try:
        did = dashboard_service.create_dashboard(req.model_dump(), user["user_id"])
        return {"id": did}
    except Exception as e:
        logger.exception("Failed to create dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/snapshots")
def list_snapshots_endpoint(
    days: int = QueryParam(7),
    user: dict = Depends(get_current_user),
):
    """Get recent chart snapshots from chat executions."""
    try:
        return snapshot_service.list_snapshots(user["user_id"], days)
    except Exception as e:
        logger.exception("Failed to list snapshots")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/snapshots/{snapshot_id}/data")
def get_snapshot_data_endpoint(
    snapshot_id: int,
    user: dict = Depends(get_current_user),
):
    """Get full snapshot data including data rows."""
    try:
        row = snapshot_service.get_snapshot_data(snapshot_id, user["user_id"])
        if not row:
            raise HTTPException(status_code=404, detail="快照不存在")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get snapshot data")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reorder")
def reorder_dashboards_endpoint(
    req: ReorderRequest,
    user: dict = Depends(get_current_user),
):
    """Update sort_order for dashboards."""
    try:
        dashboard_service.reorder_dashboards(user["user_id"], req.orders)
        return {"success": True}
    except Exception as e:
        logger.exception("Failed to reorder dashboards")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
def preview_datasource_endpoint(
    req: dict,
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """Execute a saved query/dataset SQL and return preview data."""
    try:
        source_type = req.get("source_type", "")
        source_id = req.get("source_id", 0)
        result = preview_saved_query(user["user_id"], source_type, source_id, workspace_id)
        return result
    except NoIdentityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("Failed to preview datasource")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasources")
def list_datasources_endpoint(
    user: dict = Depends(get_current_user),
):
    """Aggregate available data sources: snapshots, saved queries, datasets."""
    try:
        return list_datasource_aggregations(user["user_id"])
    except Exception as e:
        logger.exception("Failed to list datasources")
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard Groups (仪表盘组/看板组合: 看板目录切换维度) ────────────


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    sort: int = 0


class GroupItemsRequest(BaseModel):
    dashboard_ids: list[int]


class GroupRolesRequest(BaseModel):
    role_ids: list[int]


def _group_payload(g: dict) -> dict:
    from services.shared.common.db import execute_query
    g["dashboard_ids"] = [r["dashboard_id"] for r in (execute_query(
        "SELECT dashboard_id FROM adh_dashboard_group_items WHERE group_id=%s ORDER BY sort, id",
        (g["id"],)) or [])]
    g["role_ids"] = [r["role_id"] for r in (execute_query(
        "SELECT role_id FROM adh_role_dashboard_groups WHERE group_id=%s", (g["id"],)) or [])]
    return g


@router.get("/groups")
def list_groups_endpoint(user: dict = Depends(get_current_user)):
    """全部仪表盘组(管理端组合管理用; 含组内看板与绑定角色)."""
    from services.shared.common.db import execute_query
    groups = execute_query("SELECT * FROM adh_dashboard_groups ORDER BY sort, id") or []
    return [_group_payload(g) for g in groups]


@router.get("/groups/visible")
def list_visible_groups_endpoint(user: dict = Depends(get_current_user)):
    """当前用户可见的仪表盘组(组按角色分配), 每组带 组内∩角色可见 的看板清单.

    可见但未入组的看板归"未分组"兑底组(id=0); 组只做编排不授予可见(fail-closed)。
    """
    from services.shared.common.db import execute_query
    uid = user["user_id"]
    is_admin = (user.get("role") or "") == "admin"
    visible = visible_dashboard_ids(uid, user.get("role") or "")
    if visible is not None and not visible:
        return []  # fail-closed: 无任何可见看板
    rows = execute_query(
        "SELECT id, name, status, is_default, sort_order FROM adh_dashboards "
        "ORDER BY is_default DESC, sort_order, id") or []
    if visible is not None:
        rows = [r for r in rows if r["id"] in visible]
    if is_admin:
        # 管理员可见全部组(含未绑角色的编排, 便于自检)
        groups = execute_query("SELECT * FROM adh_dashboard_groups ORDER BY sort, id") or []
    else:
        groups = execute_query(
            """SELECT DISTINCT g.* FROM adh_dashboard_groups g
               JOIN adh_role_dashboard_groups rg ON rg.group_id = g.id
               JOIN adh_user_roles ur ON ur.role_id = rg.role_id
               WHERE ur.user_id = %s ORDER BY g.sort, g.id""", (uid,)) or []
    out = []
    grouped_ids: set = set()
    for g in groups:
        member = {r["dashboard_id"] for r in (execute_query(
            "SELECT dashboard_id FROM adh_dashboard_group_items WHERE group_id=%s ORDER BY sort, id",
            (g["id"],)) or [])}
        grouped_ids |= member
        out.append({"id": g["id"], "name": g["name"], "description": g.get("description") or "",
                    "sort": g.get("sort") or 0, "dashboards": [b for b in rows if b["id"] in member]})
    out.append({"id": 0, "name": "未分组", "description": "未加入任何仪表盘组的可见看板",
                "sort": 9999, "dashboards": [b for b in rows if b["id"] not in grouped_ids]})
    return out


@router.post("/groups")
def create_group_endpoint(req: GroupCreate, admin: dict = Depends(require_admin)):
    """新建仪表盘组(仅 admin)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_dashboard_groups (name, description, sort) VALUES (%s, %s, %s)",
                (req.name, req.description or "", req.sort or 0))
            group_id = cur.lastrowid
    return {"success": True, "id": group_id}


@router.put("/groups/{group_id}")
def update_group_endpoint(group_id: int, req: GroupCreate, admin: dict = Depends(require_admin)):
    """更新仪表盘组(仅 admin)."""
    from services.shared.common.db import execute_write
    n = execute_write(
        "UPDATE adh_dashboard_groups SET name=%s, description=%s, sort=%s WHERE id=%s",
        (req.name, req.description or "", req.sort or 0, group_id))
    if not n:
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    return {"success": True}


@router.delete("/groups/{group_id}")
def delete_group_endpoint(group_id: int, admin: dict = Depends(require_admin)):
    """删除仪表盘组及其成员/角色绑定(仅 admin, 不删看板本身)."""
    from services.shared.common.db import execute_write
    execute_write("DELETE FROM adh_dashboard_group_items WHERE group_id=%s", (group_id,))
    execute_write("DELETE FROM adh_role_dashboard_groups WHERE group_id=%s", (group_id,))
    n = execute_write("DELETE FROM adh_dashboard_groups WHERE id=%s", (group_id,))
    if not n:
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    return {"success": True}


@router.put("/groups/{group_id}/items")
def set_group_items_endpoint(group_id: int, req: GroupItemsRequest,
                             admin: dict = Depends(require_admin)):
    """全量替换组内看板(仅 admin)."""
    from services.shared.common.db import execute_write, execute_query
    if not execute_query("SELECT id FROM adh_dashboard_groups WHERE id=%s", (group_id,), fetchone=True):
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    execute_write("DELETE FROM adh_dashboard_group_items WHERE group_id=%s", (group_id,))
    for i, did in enumerate(dict.fromkeys(req.dashboard_ids or [])):
        execute_write(
            "INSERT IGNORE INTO adh_dashboard_group_items (group_id, dashboard_id, sort) VALUES (%s,%s,%s)",
            (group_id, int(did), i))
    return {"success": True, "count": len(set(req.dashboard_ids or []))}


@router.put("/groups/{group_id}/roles")
def set_group_roles_endpoint(group_id: int, req: GroupRolesRequest,
                             admin: dict = Depends(require_admin)):
    """全量替换组的角色绑定(仅 admin). 组按角色分配, 不授予看板可见性."""
    from services.shared.common.db import execute_write, execute_query
    if not execute_query("SELECT id FROM adh_dashboard_groups WHERE id=%s", (group_id,), fetchone=True):
        raise HTTPException(status_code=404, detail="仪表盘组不存在")
    execute_write("DELETE FROM adh_role_dashboard_groups WHERE group_id=%s", (group_id,))
    for rid in set(req.role_ids or []):
        execute_write(
            "INSERT IGNORE INTO adh_role_dashboard_groups (role_id, group_id) VALUES (%s,%s)",
            (int(rid), group_id))
    return {"success": True, "count": len(set(req.role_ids or []))}


@router.get("/{dashboard_id}")
def get_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Get a dashboard with its charts (角色不可见 → 404, 不暴露存在性)."""
    try:
        dashboard = dashboard_service.get_dashboard(
            dashboard_id, user["user_id"], user.get("role") or "")
        if not dashboard:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return dashboard
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{dashboard_id}")
def update_dashboard_endpoint(
    dashboard_id: int,
    req: DashboardUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a dashboard."""
    try:
        updated = dashboard_service.update_dashboard(
            dashboard_id, req.model_dump(exclude_none=True), user["user_id"],
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Dashboard not found or no changes")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{dashboard_id}")
def delete_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Delete a dashboard and its charts."""
    try:
        deleted = dashboard_service.delete_dashboard(dashboard_id, user["user_id"])
        if not deleted:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete dashboard")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{dashboard_id}/copy")
def copy_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Copy a dashboard with all its charts."""
    try:
        new_dashboard = dashboard_service.copy_dashboard(dashboard_id, user["user_id"])
        if not new_dashboard:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        return new_dashboard
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to copy dashboard")
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard Visibility Roles (看板可见角色配置, 双向入口的看板侧) ────


@router.get("/{dashboard_id}/visible-roles")
def get_visible_roles_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """获取可看到该看板的角色列表."""
    from services.authservice.services.role_service import role_service
    return role_service.get_dashboard_roles(dashboard_id)


@router.put("/{dashboard_id}/visible-roles")
def set_visible_roles_endpoint(
    dashboard_id: int,
    req: SetVisibleRolesRequest,
    admin: dict = Depends(require_admin),
):
    """全量替换看板的可见角色(仅 admin). 空列表=一律不可见(fail-closed)."""
    from services.authservice.services.role_service import role_service
    if not dashboard_service.get_dashboard(
            dashboard_id, admin["user_id"], admin.get("role") or ""):
        raise HTTPException(status_code=404, detail="Dashboard not found")
    ok = role_service.set_dashboard_roles(dashboard_id, req.role_ids)
    return {"success": ok, "count": len(set(req.role_ids or []))}


# ── Chart Endpoints ─────────────────────────────────────────────────────────


@router.post("/{dashboard_id}/charts")
def add_chart_endpoint(
    dashboard_id: int,
    req: ChartCreate,
    user: dict = Depends(get_current_user),
):
    """Add a chart to a dashboard."""
    try:
        cid = chart_service.create_chart(dashboard_id, req.model_dump())
        return {"id": cid}
    except Exception as e:
        logger.exception("Failed to add chart")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{dashboard_id}/charts/{chart_id}")
def update_chart_endpoint(
    dashboard_id: int,
    chart_id: int,
    req: ChartUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a chart within a dashboard."""
    try:
        updated = chart_service.update_chart(
            dashboard_id, chart_id, req.model_dump(exclude_unset=True),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Chart not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update chart")
        raise HTTPException(status_code=500, detail="图表配置保存失败，请重试") from e


@router.delete("/{dashboard_id}/charts/{chart_id}")
def delete_chart_endpoint(
    dashboard_id: int,
    chart_id: int,
    user: dict = Depends(get_current_user),
):
    """Remove a chart from a dashboard."""
    try:
        deleted = chart_service.delete_chart(dashboard_id, chart_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chart not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete chart")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{dashboard_id}/charts/{chart_id}/refresh")
def refresh_chart_endpoint(
    dashboard_id: int,
    chart_id: int,
    req: ChartRefreshRequest = ChartRefreshRequest(),
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """Re-execute a chart's SQL query on its configured datasource."""
    try:
        result = chart_service.refresh_chart(
            dashboard_id, chart_id,
            params=req.params,
            page_limit=req.page_limit,
            page_offset=req.page_offset,
            count_sql=req.count_sql,
            user_id=user.get("user_id") or 0,
            workspace_id=workspace_id,
            username=user.get("username", "") or "",
        )
        return result
    except NoIdentityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except pymysql.Error as e:
        raise HTTPException(status_code=400, detail=f"SQL 执行失败: {e}")
    except Exception as e:
        logger.exception("Failed to refresh chart")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{dashboard_id}/refresh")
def refresh_all_charts_endpoint(
    dashboard_id: int,
    req: dict = {},
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """Re-execute all charts' SQL in a dashboard on their respective datasources."""
    try:
        result = chart_service.refresh_all_charts(
            dashboard_id, params=req.get("params", {}),
            user_id=user.get("user_id") or 0,
            workspace_id=workspace_id,
            username=user.get("username", "") or "",
        )
        return result
    except NoIdentityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("Failed to refresh all charts")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{dashboard_id}/layout")
def update_layout_endpoint(
    dashboard_id: int,
    req: dict,
    user: dict = Depends(get_current_user),
):
    """Batch update chart positions (layout save)."""
    try:
        layouts = req.get("layouts", [])
        chart_service.update_layout(dashboard_id, layouts)
        return {"success": True}
    except Exception as e:
        logger.exception("Failed to update layout")
        raise HTTPException(status_code=500, detail=str(e))
