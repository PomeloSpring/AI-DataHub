"""Component Data API -- Unified endpoint for all low-code component data fetching.

Migrated from backend/api/component_data.py. Uses service layer for all business logic.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from services.dataviz.services.component_service import component_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic Models ─────────────────────────────────────────────────────────


class ComponentDataOptions(BaseModel):
    page: Optional[int] = None
    size: Optional[int] = None
    sort_by: Optional[str] = None
    sort_order: Optional[str] = None
    agg_method: Optional[str] = None
    group_by: Optional[str] = None


class ComponentDataRequest(BaseModel):
    datasource_id: int
    sql: str
    params: dict = {}
    component_type: str = "table"
    options: Optional[ComponentDataOptions] = None


class ComponentDataResponse(BaseModel):
    success: bool
    data: list = []
    total: Optional[int] = None
    columns: list = []
    error: Optional[str] = None


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/component-data", response_model=ComponentDataResponse)
def get_component_data(req: ComponentDataRequest):
    """Unified endpoint for all component data fetching.

    Supports:
    - Parameter substitution via ${param} placeholders
    - Pagination (page/size) for table components
    - Sorting (sort_by/sort_order)
    - Safe SQL validation before execution
    """
    try:
        options = req.options.model_dump() if req.options else None
        result = component_service.get_component_data(
            datasource_id=req.datasource_id,
            sql=req.sql,
            params=req.params,
            component_type=req.component_type,
            options=options,
        )
        return ComponentDataResponse(**result)
    except Exception as e:
        logger.exception("Failed to get component data")
        return ComponentDataResponse(success=False, error=str(e))
