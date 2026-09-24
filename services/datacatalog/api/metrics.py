"""Metrics API - Metrics management endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import metrics_service

router = APIRouter()


@router.get("/")
def list_metrics(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=500, description="Page size"),
    metric_type: Optional[str] = Query(None, description="Filter by metric type"),
    tags: Optional[str] = Query(None, description="Filter by tags (comma-separated)"),
    search: str = Query("", description="Search keyword"),
    workspace_id: int = Query(0, description="Workspace ID"),
    model_id: Optional[int] = Query(None, description="本体模型作用域(scope=model 时必填)"),
    scope: Optional[str] = Query(None, description="model=仅本模型对象资产 | unbound=仅待归属 | 空=全局"),
):
    """List metrics (paginated, filter by type/tags/model-scope)."""
    if scope not in (None, "", "model", "unbound"):
        raise HTTPException(status_code=400, detail="scope 仅支持 model/unbound")
    result = metrics_service.list_metrics(
        page=page,
        size=size,
        metric_type=metric_type,
        tags=tags,
        search=search,
        workspace_id=workspace_id,
        model_id=model_id,
        scope=scope or None,
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


@router.get("/dimensions")
def list_all_dimensions(
    model_id: Optional[int] = Query(None, description="本体模型作用域(scope=model 时必填)"),
    scope: Optional[str] = Query(None, description="model | unbound | 空=全局(旧行为)"),
):
    """维度字典列表(全局/模型工作区共用)。必须注册在 /{metric_id} 之前。"""
    if scope not in (None, "", "model", "unbound"):
        raise HTTPException(status_code=400, detail="scope 仅支持 model/unbound")
    return metrics_service.list_all_dimensions(model_id=model_id, scope=scope or None)


@router.get("/asset-pool")
def get_asset_pool():
    """未归属资产池聚合(指标/维度/术语/回流候选 + 计数)。必须注册在 /{metric_id} 之前。"""
    return metrics_service.asset_pool()


@router.put("/dimensions/{dim_id}")
def update_dimension(dim_id: int, req: dict):
    """编辑全局维度字典(白名单列: 英文名/分类/层级/别名/枚举标签/描述/认证)。"""
    success = metrics_service.update_dimension(dim_id, req)
    if not success:
        raise HTTPException(status_code=404, detail="Dimension not found")
    return {"success": True}


@router.delete("/dimensions/{dim_id}")
def delete_dimension(dim_id: int):
    """删除全局维度字典(连同指标关系行)。未归属资产池的清理入口之一。"""
    if not metrics_service.delete_dimension(dim_id):
        raise HTTPException(status_code=404, detail="Dimension not found")
    return {"success": True}


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
