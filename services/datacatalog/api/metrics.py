"""Metrics API - Metrics management endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import metrics_service

router = APIRouter()


@router.get("/")
def list_metrics(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Page size"),
    metric_type: Optional[str] = Query(None, description="Filter by metric type"),
    tags: Optional[str] = Query(None, description="Filter by tags (comma-separated)"),
    search: str = Query("", description="Search keyword"),
    workspace_id: int = Query(0, description="Workspace ID"),
):
    """List metrics (paginated, filter by type/tags)."""
    result = metrics_service.list_metrics(
        page=page,
        size=size,
        metric_type=metric_type,
        tags=tags,
        search=search,
        workspace_id=workspace_id,
    )
    return result


@router.post("/")
def create_metric(req: dict):
    """Create metric."""
    try:
        result = metrics_service.create_metric(req)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{metric_id}")
def get_metric(metric_id: int):
    """Get metric detail with dimensions."""
    result = metrics_service.get_metric(metric_id)
    if not result:
        raise HTTPException(status_code=404, detail="Metric not found")
    return result


@router.put("/{metric_id}")
def update_metric(metric_id: int, req: dict):
    """Update metric."""
    success = metrics_service.update_metric(metric_id, req)
    if not success:
        raise HTTPException(status_code=404, detail="Metric not found")
    return {"success": True}


@router.delete("/{metric_id}")
def delete_metric(metric_id: int):
    """Delete metric."""
    success = metrics_service.delete_metric(metric_id)
    if not success:
        raise HTTPException(status_code=404, detail="Metric not found")
    return {"success": True}


@router.get("/{metric_id}/dimensions")
def get_dimensions(metric_id: int):
    """Get metric dimensions."""
    # Verify metric exists
    metric = metrics_service.get_metric(metric_id)
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")
    return metrics_service.get_dimensions(metric_id)


@router.post("/{metric_id}/dimensions")
def add_dimension(metric_id: int, req: dict):
    """Add dimension to metric."""
    try:
        result = metrics_service.add_dimension(metric_id, req)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
