"""Playground API — saved queries, datasets, table browsing."""
import json
import time
import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from backend.api.auth import get_current_user
from backend.api.datasource import get_datasource_by_id, get_datasource_conn
from backend.models.schemas import UserInfo, SavedQueryCreate, SavedQueryUpdate
from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

router = APIRouter()


def _get_mysql_conn():
    """Get metadata DB connection."""
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


# ── Execute SQL via datasource ────────────────────────────────────────────────

@router.post("/execute")
def execute_via_playground(req: dict, user: UserInfo = Depends(get_current_user)):
    """Proxy SQL execution to the datasource executor.
    Expects { "sql": "...", "datasource_id": 123 }.
    Supports MySQL/Doris (SQL via pymysql) and Elasticsearch (SQL via ES SQL API).
    """
    ds_id = req.get("datasource_id")
    if ds_id is None:
        raise HTTPException(status_code=400, detail="datasource_id is required")
    ds = get_datasource_by_id(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    sql = (req.get("sql") or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL cannot be empty")

    import time as _time
    start = _time.time()

    db_type = ds.get("db_type", "mysql")

    if db_type == "elasticsearch":
        # Elasticsearch: use ES SQL API
        from backend.nl2sql.sql.query_executor import _build_es_client, _preprocess_es_sql
        # Preprocess ES SQL (fix _id, lowercase identifiers, etc.)
        sql = _preprocess_es_sql(sql)
        params = {
            "host": ds["host"], "port": ds["port"],
            "user": ds.get("username"), "password": ds.get("password"),
            "ssl": bool(ds.get("ssl", 0)),
        }
        es = _build_es_client(params)
        try:
            result = es.sql.query(body={"query": sql})
            columns_info = result.get("columns", [])
            rows_data = result.get("rows", [])
            if not columns_info or not rows_data:
                return {"columns": [], "rows": [], "row_count": 0, "elapsed_ms": int((_time.time() - start) * 1000)}
            col_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns_info)]
            rows = [dict(zip(col_names, row)) for row in rows_data]
            elapsed_ms = int((_time.time() - start) * 1000)
            return {"columns": col_names, "rows": rows, "row_count": len(rows), "elapsed_ms": elapsed_ms}
        except Exception as e:
            error_msg = str(e)
            # Provide helpful error hints for common ES SQL issues
            if "Unknown column" in error_msg:
                error_msg += "\n提示: ES 字段名区分大小写，请检查字段名是否正确。"
            elif "index_not_found" in error_msg or "no such index" in error_msg.lower():
                error_msg += "\n提示: 索引名需要用双引号包裹，例如: SELECT * FROM \"my_index\""
            elif "parsing_exception" in error_msg:
                error_msg += "\n提示: ES SQL 语法与标准 SQL 有差异，索引名需要用双引号包裹。"
            raise HTTPException(status_code=400, detail=error_msg)
        finally:
            es.close()
    else:
        # MySQL/Doris: validate and execute via pymysql
        upper = sql.upper().lstrip()
        if not (upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("SHOW") or upper.startswith("DESC")):
            raise HTTPException(status_code=400, detail="Only SELECT/SHOW/DESC queries allowed")

        conn = get_datasource_conn(ds)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            elapsed_ms = int((_time.time() - start) * 1000)
            columns = list(rows[0].keys()) if rows else []
            for row in rows:
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
                    elif isinstance(v, bytes):
                        row[k] = v.decode("utf-8", errors="replace")
            return {"columns": columns, "rows": rows, "row_count": len(rows), "elapsed_ms": elapsed_ms}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()


# ── Saved queries CRUD ────────────────────────────────────────────────────────

@router.get("/queries")
def list_queries(
    is_dataset: int = QueryParam(None),
    user: UserInfo = Depends(get_current_user),
):
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            if is_dataset is not None:
                cur.execute(
                    "SELECT * FROM adh_saved_queries WHERE owner_id = %s AND is_dataset = %s ORDER BY updated_at DESC",
                    (user.id, is_dataset),
                )
            else:
                cur.execute(
                    "SELECT * FROM adh_saved_queries WHERE owner_id = %s ORDER BY updated_at DESC",
                    (user.id,),
                )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                if hasattr(r.get("updated_at"), "isoformat"):
                    r["updated_at"] = r["updated_at"].isoformat()
            return rows
    finally:
        conn.close()


@router.post("/queries")
def create_query(req: SavedQueryCreate, user: UserInfo = Depends(get_current_user)):
    qid = int(time.time() * 1000)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_saved_queries (id, name, description, sql_query, is_dataset, dataset_keywords, owner_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (qid, req.name, req.description or "", req.sql_query,
                 1 if req.is_dataset else 0, req.dataset_keywords or "", user.id, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    return {"id": qid}


@router.put("/queries/{query_id}")
def update_query(query_id: int, req: SavedQueryUpdate, user: UserInfo = Depends(get_current_user)):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            updates = ["updated_at = %s"]
            params = [now]
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
            params.append(query_id)
            cur.execute(f"UPDATE adh_saved_queries SET {', '.join(updates)} WHERE id = %s AND owner_id = %s", params + [user.id])
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@router.delete("/queries/{query_id}")
def delete_query(query_id: int, user: UserInfo = Depends(get_current_user)):
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_saved_queries WHERE id = %s AND owner_id = %s", (query_id, user.id))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}
