"""Query API — Direct SQL execution.

Migrated from backend/api/query.py
"""

import io
import json
import logging
import math
import time
from datetime import datetime

import pymysql
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query as db_execute_query

logger = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    sql: str
    datasource_id: int = 0


def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None for JSON compliance."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _get_datasource_conn(ds_id: int):
    """Get connection to a datasource."""
    row = db_execute_query(
        "SELECT * FROM adh_datasources WHERE id = %s",
        (ds_id,),
        fetchone=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Datasource not found")

    db_type = row.get("db_type", "mysql")
    if db_type == "elasticsearch":
        from elasticsearch import Elasticsearch
        protocol = "https" if row.get("ssl") else "http"
        es_url = f"{protocol}://{row['host']}:{row['port']}"
        es_kwargs = {"hosts": [es_url], "request_timeout": 30, "meta_header": False}
        if row.get("username") and row.get("password"):
            es_kwargs["basic_auth"] = (row["username"], row["password"])
        return Elasticsearch(**es_kwargs), "elasticsearch"
    else:
        conn_kwargs = {
            "host": row["host"],
            "port": row["port"],
            "user": row["username"],
            "password": row["password"],
            "database": row.get("database_name") or None,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": 10,
            "read_timeout": 30,
        }
        if row.get("ssl"):
            conn_kwargs["ssl"] = {
                "ssl_disabled": False,
                "ssl_verify_cert": False,
                "ssl_verify_identity": False,
            }
        return pymysql.connect(**conn_kwargs), "mysql"


@router.post("/execute")
def execute_sql(req: QueryRequest):
    """Execute SQL query."""
    try:
        start = time.time()

        if req.datasource_id:
            conn, db_type = _get_datasource_conn(req.datasource_id)
            if db_type == "elasticsearch":
                result = conn.sql.query(body={"query": req.sql})
                columns_info = result.get("columns", [])
                rows_data = result.get("rows", [])
                col_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns_info)]
                rows = [dict(zip(col_names, row)) for row in rows_data]
                conn.close()
            else:
                with conn.cursor() as cur:
                    cur.execute(req.sql)
                    rows = cur.fetchall()
                conn.close()
                col_names = list(rows[0].keys()) if rows else []
        else:
            # Default: use metadata DB
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    cur.execute(req.sql)
                    rows = cur.fetchall()
            col_names = list(rows[0].keys()) if rows else []

        # Sanitize
        for row in rows:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")

        rows = _sanitize_floats(rows)
        elapsed_ms = int((time.time() - start) * 1000)

        return {
            "columns": col_names,
            "rows": rows,
            "row_count": len(rows),
            "elapsed_ms": elapsed_ms,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("SQL execution failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/export")
async def export_sql(req: QueryRequest):
    """Export SQL result as Excel."""
    try:
        import pandas as pd

        if req.datasource_id:
            conn, db_type = _get_datasource_conn(req.datasource_id)
            if db_type == "elasticsearch":
                raise HTTPException(status_code=400, detail="Excel export not supported for Elasticsearch")
            with conn.cursor() as cur:
                cur.execute(req.sql)
                rows = cur.fetchall()
            conn.close()
        else:
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    cur.execute(req.sql)
                    rows = cur.fetchall()

        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
        buf.seek(0)

        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=export.xlsx"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Export failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
