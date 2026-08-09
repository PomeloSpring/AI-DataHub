"""Playground API — SQL execution, saved queries, datasets.

Migrated from backend/api/playground.py
Tables: adh_saved_queries, adh_datasources
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

import pymysql
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


class SavedQueryCreate(BaseModel):
    name: str
    description: str = ""
    sql_query: str
    is_dataset: bool = False
    dataset_keywords: str = ""


class SavedQueryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sql_query: Optional[str] = None
    is_dataset: Optional[bool] = None
    dataset_keywords: Optional[str] = None


def _get_datasource_conn(ds_id: int):
    """Get connection to a datasource."""
    row = execute_query(
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
def execute_via_playground(req: dict):
    """Execute SQL via datasource."""
    ds_id = req.get("datasource_id")
    if ds_id is None:
        raise HTTPException(status_code=400, detail="datasource_id is required")

    sql = (req.get("sql") or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL cannot be empty")

    try:
        start = time.time()
        conn, db_type = _get_datasource_conn(ds_id)

        if db_type == "elasticsearch":
            result = conn.sql.query(body={"query": sql})
            columns_info = result.get("columns", [])
            rows_data = result.get("rows", [])
            if not columns_info or not rows_data:
                conn.close()
                return {"columns": [], "rows": [], "row_count": 0, "elapsed_ms": int((time.time() - start) * 1000)}
            col_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns_info)]
            rows = [dict(zip(col_names, row)) for row in rows_data]
            conn.close()
        else:
            # Validate: only allow SELECT/SHOW/DESC
            upper = sql.upper().lstrip()
            if not (upper.startswith("SELECT") or upper.startswith("WITH") or
                    upper.startswith("SHOW") or upper.startswith("DESC")):
                conn.close()
                raise HTTPException(status_code=400, detail="Only SELECT/SHOW/DESC queries allowed")

            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            conn.close()
            col_names = list(rows[0].keys()) if rows else []

        # Sanitize
        for row in rows:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")

        elapsed_ms = int((time.time() - start) * 1000)
        return {"columns": col_names, "rows": rows, "row_count": len(rows), "elapsed_ms": elapsed_ms}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Playground execution failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/queries")
def list_queries(
    is_dataset: Optional[int] = Query(None),
    workspace_id: int = Query(0),
):
    """List saved queries."""
    try:
        conditions = ["workspace_id = %s"]
        params = [workspace_id]

        if is_dataset is not None:
            conditions.append("is_dataset = %s")
            params.append(is_dataset)

        where = " AND ".join(conditions)
        rows = execute_query(
            f"SELECT * FROM adh_saved_queries WHERE {where} ORDER BY updated_at DESC",
            params,
        )
        for r in rows:
            for k in ("created_at", "updated_at"):
                if hasattr(r.get(k), "isoformat"):
                    r[k] = r[k].isoformat()
        return rows
    except Exception as e:
        logger.error("List queries failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queries")
def create_query(req: SavedQueryCreate, workspace_id: int = Query(0)):
    """Create saved query."""
    try:
        qid = int(time.time() * 1000)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute_insert(
            """INSERT INTO adh_saved_queries
               (id, name, description, sql_query, is_dataset, dataset_keywords, workspace_id, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (qid, req.name, req.description, req.sql_query,
             1 if req.is_dataset else 0, req.dataset_keywords, workspace_id, now, now),
        )
        return {"id": qid}
    except Exception as e:
        logger.error("Create query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/queries/{query_id}")
def update_query(query_id: int, req: SavedQueryUpdate):
    """Update saved query."""
    try:
        updates = []
        params = []
        if req.name is not None:
            updates.append("name = %s")
            params.append(req.name)
        if req.description is not None:
            updates.append("description = %s")
            params.append(req.description)
        if req.sql_query is not None:
            updates.append("sql_query = %s")
            params.append(req.sql_query)
        if req.is_dataset is not None:
            updates.append("is_dataset = %s")
            params.append(1 if req.is_dataset else 0)
        if req.dataset_keywords is not None:
            updates.append("dataset_keywords = %s")
            params.append(req.dataset_keywords)

        if not updates:
            return {"success": True}

        updates.append("updated_at = %s")
        params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        params.append(query_id)

        execute_write(f"UPDATE adh_saved_queries SET {', '.join(updates)} WHERE id = %s", params)
        return {"success": True}
    except Exception as e:
        logger.error("Update query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/queries/{query_id}")
def delete_query(query_id: int):
    """Delete saved query."""
    try:
        execute_write("DELETE FROM adh_saved_queries WHERE id = %s", (query_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
