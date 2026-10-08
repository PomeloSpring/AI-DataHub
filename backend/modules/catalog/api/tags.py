"""Tags API - Tag management endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import tags_service

router = APIRouter()


@router.get("/categories")
def list_categories(workspace_id: int = Query(0, description="Workspace ID")):
    """List tag categories (tree structure)."""
    return tags_service.list_categories(workspace_id=workspace_id)


@router.post("/categories")
def create_category(req: dict):
    """Create tag category."""
    try:
        result = tags_service.create_category(req)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
def list_tags(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(50, ge=1, le=200, description="Page size"),
    category_id: Optional[int] = Query(None, description="Filter by category"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    search: str = Query("", description="Search keyword"),
    workspace_id: int = Query(0, description="Workspace ID"),
):
    """List tags (filter by category, entity_type)."""
    result = tags_service.list_tags(
        page=page,
        size=size,
        category_id=category_id,
        entity_type=entity_type,
        search=search,
        workspace_id=workspace_id,
    )
    return result


@router.post("/")
def create_tag(req: dict):
    """Create tag."""
    try:
        result = tags_service.create_tag(req)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{tag_id}")
def update_tag(tag_id: int, req: dict):
    """Update tag."""
    success = tags_service.update_tag(tag_id, req)
    if not success:
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"success": True}


@router.delete("/{tag_id}")
def delete_tag(tag_id: int):
    """Delete tag."""
    success = tags_service.delete_tag(tag_id)
    if not success:
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"success": True}


@router.get("/{tag_id}/values")
def get_tag_values(
    tag_id: int,
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
):
    """Get tag values."""
    return tags_service.get_tag_values(tag_id, entity_type=entity_type)


@router.post("/{tag_id}/values")
def set_tag_value(tag_id: int, req: dict):
    """Set tag value for entity."""
    try:
        result = tags_service.set_tag_value(tag_id, req)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/query")
def query_entities_by_tags(req: dict):
    """Query entities by tag conditions (tag intersection/union).

    Request body:
    {
        "conditions": [{"tag_id": 1}, {"tag_id": 2}],
        "operator": "AND",  // or "OR"
        "workspace_id": 0
    }
    """
    conditions = req.get("conditions", [])
    operator = req.get("operator", "AND")
    workspace_id = req.get("workspace_id", 0)

    if not conditions:
        raise HTTPException(status_code=400, detail="At least one tag condition is required")

    result = tags_service.query_entities_by_tags(
        conditions=conditions,
        operator=operator,
        workspace_id=workspace_id,
    )
    return {"items": result, "total": len(result)}
