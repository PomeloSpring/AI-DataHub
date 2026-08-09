"""Catalog service - Search and metadata business logic."""

import logging
from typing import Optional

from ...shared.common.db import DBConnection

logger = logging.getLogger(__name__)


def global_search(keyword: str, search_type: Optional[str] = None, workspace_id: int = 0, limit: int = 20) -> dict:
    """Global search across tables, columns, metrics, and terms.

    Args:
        keyword: Search keyword
        search_type: Optional filter - "table", "column", "metric", "term"
        workspace_id: Workspace isolation
        limit: Max results per category

    Returns:
        dict with tables, columns, metrics, terms lists
    """
    results = {"tables": [], "columns": [], "metrics": [], "terms": []}
    pattern = f"%{keyword}%"

    with DBConnection() as conn:
        with conn.cursor() as cur:
            ws_cond = "AND workspace_id = %s" if workspace_id else ""
            ws_params = [workspace_id] if workspace_id else []

            if search_type is None or search_type == "table":
                cur.execute(
                    f"SELECT id, table_name, table_comment, table_business_desc, datasource_id "
                    f"FROM adh_table_info "
                    f"WHERE (table_name LIKE %s OR table_comment LIKE %s OR keywords LIKE %s) {ws_cond} "
                    f"ORDER BY table_name LIMIT %s",
                    [pattern, pattern, pattern] + ws_params + [limit],
                )
                results["tables"] = cur.fetchall()

            if search_type is None or search_type == "column":
                cur.execute(
                    f"SELECT id, table_name, column_name, data_type, column_comment, business_desc "
                    f"FROM adh_column_metadata "
                    f"WHERE (column_name LIKE %s OR column_comment LIKE %s OR business_desc LIKE %s) {ws_cond} "
                    f"ORDER BY table_name, column_name LIMIT %s",
                    [pattern, pattern, pattern] + ws_params + [limit],
                )
                results["columns"] = cur.fetchall()

            if search_type is None or search_type == "metric":
                cur.execute(
                    f"SELECT id, metric_name, metric_display_name, metric_type, description "
                    f"FROM adh_metrics "
                    f"WHERE (metric_name LIKE %s OR metric_display_name LIKE %s OR description LIKE %s) {ws_cond} "
                    f"ORDER BY metric_name LIMIT %s",
                    [pattern, pattern, pattern] + ws_params + [limit],
                )
                results["metrics"] = cur.fetchall()

            if search_type is None or search_type == "term":
                cur.execute(
                    f"SELECT id, term_cn, term_en, description "
                    f"FROM adh_business_terms "
                    f"WHERE (term_cn LIKE %s OR term_en LIKE %s OR description LIKE %s) "
                    f"ORDER BY term_cn LIMIT %s",
                    [pattern, pattern, pattern, limit],
                )
                results["terms"] = cur.fetchall()

    return results


def list_tables(
    page: int = 1,
    size: int = 20,
    datasource_id: Optional[int] = None,
    search: str = "",
    workspace_id: int = 0,
) -> dict:
    """List tables with metadata, paginated.

    Args:
        page: Page number (1-based)
        size: Page size
        datasource_id: Filter by datasource
        search: Search keyword for table name
        workspace_id: Workspace isolation

    Returns:
        dict with total and items
    """
    conditions = []
    params = []

    if workspace_id:
        conditions.append("workspace_id = %s")
        params.append(workspace_id)
    if datasource_id:
        conditions.append("datasource_id = %s")
        params.append(datasource_id)
    if search:
        conditions.append("(table_name LIKE %s OR table_comment LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_table_info {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, table_name, table_comment, table_business_desc, keywords, "
                f"region_tag, domain_tag, is_active, datasource_id, sync_time "
                f"FROM adh_table_info {where} "
                f"ORDER BY table_name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("sync_time"), "isoformat"):
                    r["sync_time"] = r["sync_time"].isoformat()

    return {"total": total, "items": rows}


def get_table_detail(table_name: str, workspace_id: int = 0) -> Optional[dict]:
    """Get table detail with columns.

    Args:
        table_name: Table name
        workspace_id: Workspace isolation

    Returns:
        dict with table info and columns, or None
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            ws_cond = "AND workspace_id = %s" if workspace_id else ""
            ws_params = [workspace_id] if workspace_id else []

            # Get table info
            cur.execute(
                f"SELECT id, table_name, table_comment, table_business_desc, keywords, "
                f"region_tag, domain_tag, is_active, datasource_id, sync_time "
                f"FROM adh_table_info WHERE table_name = %s {ws_cond}",
                [table_name] + ws_params,
            )
            table = cur.fetchone()
            if not table:
                return None

            if hasattr(table.get("sync_time"), "isoformat"):
                table["sync_time"] = table["sync_time"].isoformat()

            # Get columns
            datasource_id = table.get("datasource_id", 0)
            cur.execute(
                "SELECT id, column_name, data_type, column_comment, business_desc, "
                "keywords, is_key, is_nullable, is_active "
                "FROM adh_column_metadata "
                "WHERE table_name = %s AND datasource_id = %s "
                "ORDER BY column_name",
                [table_name, datasource_id],
            )
            table["columns"] = cur.fetchall()

    return table
