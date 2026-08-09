"""Catalog API - Data discovery endpoints."""

from typing import Optional

from fastapi import APIRouter, Query

from ..services import catalog_service

router = APIRouter()


@router.get("/search")
def search(
    keyword: str = Query(..., description="Search keyword"),
    type: Optional[str] = Query(None, description="Filter by type: table, column, metric, term"),
    workspace_id: int = Query(0, description="Workspace ID"),
    limit: int = Query(20, ge=1, le=100, description="Max results per category"),
):
    """Global search across tables, columns, metrics, and terms."""
    results = catalog_service.global_search(
        keyword=keyword,
        search_type=type,
        workspace_id=workspace_id,
        limit=limit,
    )
    return results


@router.get("/tables")
def list_tables(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Page size"),
    datasource_id: Optional[int] = Query(None, description="Filter by datasource ID"),
    search: str = Query("", description="Search keyword"),
    workspace_id: int = Query(0, description="Workspace ID"),
):
    """List tables with metadata (paginated, filter by datasource_id)."""
    result = catalog_service.list_tables(
        page=page,
        size=size,
        datasource_id=datasource_id,
        search=search,
        workspace_id=workspace_id,
    )
    return result


@router.get("/tables/{table_name}")
def get_table_detail(
    table_name: str,
    workspace_id: int = Query(0, description="Workspace ID"),
):
    """Get table detail with columns."""
    result = catalog_service.get_table_detail(
        table_name=table_name,
        workspace_id=workspace_id,
    )
    if not result:
        return {"error": "Table not found", "table_name": table_name}, 404
    return result
