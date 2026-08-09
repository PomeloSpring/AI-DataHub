"""Sync Metadata API — Sync table/column metadata from datasources.

Migrated from backend/api/admin.py (sync/metadata section)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query

logger = logging.getLogger(__name__)
router = APIRouter()


class SyncRequest(BaseModel):
    datasource_id: int
    table_name: Optional[str] = None


@router.post("/metadata")
def sync_metadata(req: SyncRequest):
    """Sync metadata from datasource."""
    try:
        # Get datasource info
        ds = execute_query(
            "SELECT * FROM adh_datasources WHERE id = %s",
            (req.datasource_id,),
            fetchone=True,
        )
        if not ds:
            raise HTTPException(status_code=404, detail="Datasource not found")

        # TODO: Implement actual metadata sync
        # This would connect to the datasource and sync table/column metadata
        return {
            "success": True,
            "message": "Metadata sync initiated",
            "datasource_id": req.datasource_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Sync metadata failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metadata/columns")
def sync_columns(req: SyncRequest):
    """Sync column metadata from datasource."""
    try:
        # Get datasource info
        ds = execute_query(
            "SELECT * FROM adh_datasources WHERE id = %s",
            (req.datasource_id,),
            fetchone=True,
        )
        if not ds:
            raise HTTPException(status_code=404, detail="Datasource not found")

        # TODO: Implement actual column sync
        return {
            "success": True,
            "message": "Column sync initiated",
            "datasource_id": req.datasource_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Sync columns failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
