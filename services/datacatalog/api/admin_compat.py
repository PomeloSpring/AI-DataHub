"""Admin Compatibility API — backward-compatible endpoints for frontend.

Maps old /api/admin/* routes to DataCatalog functionality.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ...shared.common.db import DBConnection, execute_query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/metadata")
def list_metadata_compat(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    table_name: str = Query("", description="Search table name"),
    column_name: str = Query("", description="Search column name"),
    datasource_id: Optional[int] = Query(None, description="Filter by datasource"),
):
    """List column metadata (backward compatible with old API)."""
    conditions = []
    params = []

    if datasource_id:
        conditions.append("datasource_id = %s")
        params.append(datasource_id)
    if table_name:
        conditions.append("table_name LIKE %s")
        params.append(f"%{table_name}%")
    if column_name:
        conditions.append("column_name LIKE %s")
        params.append(f"%{column_name}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM adh_column_metadata {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT id, table_name, column_name, data_type, "
                    f"column_comment, business_desc, keywords, is_key, is_nullable, is_active, sync_time, datasource_id "
                    f"FROM adh_column_metadata {where} "
                    f"ORDER BY table_name, column_name LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()
                for r in rows:
                    if hasattr(r.get("sync_time"), "isoformat"):
                        r["sync_time"] = r["sync_time"].isoformat()
                return {"total": total, "items": rows}
    except Exception as e:
        logger.error("List metadata failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metadata/{row_id}")
def get_metadata_compat(row_id: int):
    """Get single column metadata by ID."""
    try:
        row = execute_query(
            "SELECT id, table_name, column_name, data_type, "
            "column_comment, business_desc, keywords, is_key, is_nullable, is_active, sync_time, datasource_id "
            "FROM adh_column_metadata WHERE id = %s",
            (row_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Metadata not found")
        if hasattr(row.get("sync_time"), "isoformat"):
            row["sync_time"] = row["sync_time"].isoformat()
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get metadata failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/metadata/{row_id}")
def update_metadata_compat(row_id: int, data: dict):
    """Update column metadata."""
    try:
        updates = []
        params = []
        for key in ("column_comment", "business_desc", "keywords", "is_active"):
            if key in data:
                updates.append(f"{key} = %s")
                params.append(data[key])

        if not updates:
            return {"success": True, "message": "No changes"}

        params.append(row_id)
        from ...shared.common.db import execute_write
        execute_write(
            f"UPDATE adh_column_metadata SET {', '.join(updates)} WHERE id = %s",
            params,
        )
        return {"success": True}
    except Exception as e:
        logger.error("Update metadata failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/table-info")
def list_table_info_compat(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    table_name: str = Query(""),
    datasource_id: Optional[int] = Query(None),
):
    """List table info (backward compatible)."""
    conditions = []
    params = []

    if datasource_id:
        conditions.append("datasource_id = %s")
        params.append(datasource_id)
    if table_name:
        conditions.append("table_name LIKE %s")
        params.append(f"%{table_name}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    try:
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
    except Exception as e:
        logger.error("List table info failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/menu-tree")
def get_menu_tree(workspace_id: int = Query(0)):
    """Get menu tree for workspace."""
    try:
        rows = execute_query(
            "SELECT * FROM adh_menu_items WHERE workspace_id = %s ORDER BY sort_order",
            (workspace_id,),
        )
        # Build tree structure
        items = {r["id"]: {**dict(r), "children": []} for r in rows}
        tree = []
        for item in items.values():
            parent_id = item.get("parent_id")
            if parent_id and parent_id in items:
                items[parent_id]["children"].append(item)
            else:
                tree.append(item)
        return tree
    except Exception as e:
        logger.error("Get menu tree failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
