"""History API - Query audit log."""
import logging
from fastapi import APIRouter, Depends, Query
from backend.api.auth import get_current_user
from backend.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/")
def get_history(
    days: int = Query(7, ge=1, le=90),
    status: str = Query(None, description="Filter by status: success/error"),
    datasource_id: int = Query(None, description="Filter by datasource"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: UserInfo = Depends(get_current_user),
):
    from backend.common.db.metadata_db import get_vector_conn
    conn = get_vector_conn()
    try:
        with conn.cursor() as cur:
            # Build WHERE clause
            where_clauses = ["a.created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"]
            params = [days]

            if status and status in ("success", "error"):
                where_clauses.append("a.execution_status = %s")
                params.append(status)

            if datasource_id:
                where_clauses.append("a.datasource_id = %s")
                params.append(datasource_id)

            where_sql = " AND ".join(where_clauses)

            # Get total count
            count_sql = f"SELECT COUNT(*) as total FROM adh_query_audit a WHERE {where_sql}"
            logger.info("Executing count query: %s with params %s", count_sql, params)
            cur.execute(count_sql, params)
            count_result = cur.fetchone()
            total = int(count_result["total"]) if count_result else 0
            logger.info("Total records: %d", total)

            # Get paginated data with datasource name
            offset = (page - 1) * page_size
            data_sql = f"""
                SELECT a.id, a.username, a.user_role, a.question, a.generated_sql,
                       a.query_type, a.execution_status, a.row_count, a.execution_time_ms,
                       a.error_message, a.created_at, a.datasource_id,
                       d.name AS datasource_name
                FROM adh_query_audit a
                LEFT JOIN adh_datasources d ON a.datasource_id = d.id
                WHERE {where_sql}
                ORDER BY a.created_at DESC
                LIMIT {offset}, {page_size}
            """
            logger.info("Executing data query: %s", data_sql)
            cur.execute(data_sql, params)
            rows = cur.fetchall()
            logger.info("Fetched %d rows", len(rows))

            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()

            result = {
                "data": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
            }
            logger.info("Returning result: page=%d, total=%d, data_count=%d", result["page"], result["total"], len(result["data"]))
            return result
    except Exception as e:
        logger.error("History query error: %s", e)
        return {
            "data": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0,
        }
    finally:
        conn.close()
