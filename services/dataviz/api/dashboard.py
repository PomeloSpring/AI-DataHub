"""Dashboard API -- CRUD for dashboards, charts, snapshots, and data sources.

Migrated from backend/api/dashboard.py. Uses service layer for all business logic.
"""

import logging

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel
from typing import Optional

from services.shared.common.auth import get_current_user, get_workspace_id
from services.dataviz.services.dashboard_service import (
    dashboard_service,
    chart_service,
    snapshot_service,
    preview_saved_query,
    list_datasource_aggregations,
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


class ChartUpdate(BaseModel):
    name: Optional[str] = None
    chart_type: Optional[str] = None
    sql_query: Optional[str] = None
    config: Optional[dict] = None
    position: Optional[dict] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None


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


# ── Dashboard Endpoints ─────────────────────────────────────────────────────


@router.get("/")
def list_dashboards_endpoint(
    user: dict = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
):
    """List dashboards (workspace scoped)."""
    try:
        return dashboard_service.list_dashboards(user["user_id"], workspace_id)
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
):
    """Execute a saved query/dataset SQL and return preview data."""
    try:
        source_type = req.get("source_type", "")
        source_id = req.get("source_id", 0)
        result = preview_saved_query(user["user_id"], source_type, source_id)
        return result
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


@router.get("/{dashboard_id}")
def get_dashboard_endpoint(
    dashboard_id: int,
    user: dict = Depends(get_current_user),
):
    """Get a dashboard with its charts."""
    try:
        dashboard = dashboard_service.get_dashboard(dashboard_id, user["user_id"])
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
            dashboard_id, chart_id, req.model_dump(exclude_none=True),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Chart not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update chart")
        raise HTTPException(status_code=500, detail=str(e))


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
):
    """Re-execute a chart's SQL query on its configured datasource."""
    try:
        result = chart_service.refresh_chart(
            dashboard_id, chart_id,
            params=req.params,
            page_limit=req.page_limit,
            page_offset=req.page_offset,
            count_sql=req.count_sql,
        )
        return result
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
):
    """Re-execute all charts' SQL in a dashboard on their respective datasources."""
    try:
        result = chart_service.refresh_all_charts(
            dashboard_id, params=req.get("params", {}),
        )
        return result
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
