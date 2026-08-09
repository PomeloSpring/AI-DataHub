"""DataEngine API — DataEngine management endpoints.

Provides endpoints for:
- Checking DataEngine health
- Validating columns against metadata
- Executing queries through DataEngine
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request Models ────────────────────────────────────────────────

class ManifestRequest(BaseModel):
    datasource_id: int
    table_names: Optional[list[str]] = None  # None = all tables
    workspace_id: int = 0


class DryPlanRequest(BaseModel):
    sql: str
    datasource_id: int
    workspace_id: int = 0
    table_names: Optional[list[str]] = None


class ValidateColumnRequest(BaseModel):
    datasource_id: int
    table_name: str
    column_name: str


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/manifest")
def get_manifest(req: ManifestRequest, admin: dict = Depends(require_admin)):
    """Build MDL manifest from datasource metadata.

    Returns a manifest compatible with engine-server-rust API.
    Includes RLS policies with user-attribute substitution.
    """
    from services.datamind.nl2sql.sql.manifest_builder import build_manifest
    try:
        manifest = build_manifest(
            datasource_id=req.datasource_id,
            workspace_id=req.workspace_id,
            table_names=req.table_names,
            user_id=admin.get("user_id", 0),
        )
        return manifest
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/query")
def execute_query_via_engine(
    req: DryPlanRequest,
    admin: dict = Depends(require_admin),
):
    """Execute SQL through DataEngine.

    Routes SQL through the Rust DataFusion engine for execution.
    """
    from services.datamind.nl2sql.sql.query_executor import execute_query_via_engine
    try:
        df, elapsed_ms, row_count = execute_query_via_engine(
            sql=req.sql,
            datasource_id=req.datasource_id,
            user_context=admin,
            table_names=req.table_names,
            workspace_id=req.workspace_id,
        )
        return {
            "columns": list(df.columns) if not df.empty else [],
            "rows": df.values.tolist() if not df.empty else [],
            "row_count": row_count,
            "execution_time_ms": elapsed_ms,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/validate/column")
def validate_column(req: ValidateColumnRequest, admin: dict = Depends(require_admin)):
    """Validate that a column exists in the datasource metadata."""
    from services.shared.common.db.metadata_db import get_metadata_conn
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM adh_column_metadata "
                    "WHERE datasource_id = %s AND table_name = %s AND column_name = %s",
                    (req.datasource_id, req.table_name, req.column_name),
                )
                row = cur.fetchone()
                return {"valid": row is not None}
        finally:
            conn.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/health")
def engine_health():
    """Check engine-server-rust health."""
    from services.shared.common.engine_client import engine_client, ENGINE_ENABLED
    if not ENGINE_ENABLED:
        return {"status": "disabled", "healthy": False}
    healthy = engine_client.health()
    return {"status": "ok" if healthy else "unhealthy", "healthy": healthy}
