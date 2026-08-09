"""History API — Query audit log.

Migrated from backend/api/history.py
Table: adh_query_audit
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.shared.common.db import DBConnection

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def get_history(
    days: int = Query(7, ge=1, le=90),
    status: Optional[str] = Query(None, description="Filter by status: success/error"),
    datasource_id: Optional[int] = Query(None, description="Filter by datasource"),
    workspace_id: int = Query(0),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
):
    """Get query history with pagination."""
    try:
        where_clauses = ["a.created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"]
        params = [days]

        if workspace_id:
            where_clauses.append("a.workspace_id = %s")
            params.append(workspace_id)

        if status and status in ("success", "error"):
            where_clauses.append("a.execution_status = %s")
            params.append(status)

        if datasource_id:
            where_clauses.append("a.datasource_id = %s")
            params.append(datasource_id)

        where_sql = " AND ".join(where_clauses)

        with DBConnection() as conn:
            with conn.cursor() as cur:
                # Count
                cur.execute(f"SELECT COUNT(*) as total FROM adh_query_audit a WHERE {where_sql}", params)
                total = cur.fetchone()["total"]

                # Data
                offset = (page - 1) * page_size
                cur.execute(
                    f"""SELECT a.id, a.username, a.user_role, a.question, a.generated_sql,
                           NULL AS query_type, a.execution_status, a.row_count, a.execution_time_ms,
                           a.error_message, a.created_at, a.datasource_id,
                           d.name AS datasource_name
                    FROM adh_query_audit a
                    LEFT JOIN adh_datasources d ON a.datasource_id = d.id
                    WHERE {where_sql}
                    ORDER BY a.created_at DESC
                    LIMIT %s OFFSET %s""",
                    params + [page_size, offset],
                )
                rows = cur.fetchall()

                for r in rows:
                    if hasattr(r.get("created_at"), "isoformat"):
                        r["created_at"] = r["created_at"].isoformat()

        return {
            "data": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
        }
    except Exception as e:
        logger.error("History query error: %s", e)
        return {
            "data": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0,
        }
