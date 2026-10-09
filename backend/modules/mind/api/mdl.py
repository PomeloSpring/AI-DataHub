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

from backend.common.auth import require_admin

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
    from backend.modules.mind.nl2sql.sql.manifest_builder import build_manifest
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
def run_mdl_query(
    req: DryPlanRequest,
    admin: dict = Depends(require_admin),
):
    """Execute SQL through the unified governed executor.

    MDL/引擎管理面的 SQL 试跑：统一经 `semantics.execute.execute_sql`
    （护栏 §1 全平台唯一取数执行口，Phase 6.4 收口），admin 门禁保留。
    """
    from backend.semantics.contract import SemanticError
    from backend.semantics.execute import PolicyContext, execute_sql
    try:
        result = execute_sql(req.sql, PolicyContext(
            user_id=int(admin.get("user_id") or 0),
            username=str(admin.get("username") or ""),
            workspace_id=int(req.workspace_id or 0),
            datasource_id=int(req.datasource_id or 0),
        ))
        return {
            "columns": [c.name for c in result.columns],
            "rows": [list(row) for row in result.rows],
            "row_count": result.row_count,
            "execution_time_ms": result.elapsed_ms,
        }
    except SemanticError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/validate/column")
def validate_column(req: ValidateColumnRequest, admin: dict = Depends(require_admin)):
    """Validate that a column exists in the datasource metadata."""
    from backend.common.db.metadata_db import get_metadata_conn
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
    """语义执行引擎健康（semantics.execution）。"""
    try:
        from backend.semantics.execution import check_version

        check_version()
        return {"status": "ok", "healthy": True}
    except Exception as e:  # noqa: BLE001 — 健康面只报状态不抛
        return {"status": "unhealthy", "healthy": False, "error": str(e)}
