"""Datasource API — manage multiple database connections for Playground."""
import time
import pymysql
from fastapi import APIRouter, Depends, HTTPException
from backend.api.auth import get_current_user, require_admin
from backend.common.auth import log_audit
from backend.models.schemas import UserInfo, DatasourceCreate, DatasourceUpdate
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, DORIS_DATABASE, METADATA_DB_DATABASE,
)
from backend.common.crypto import encrypt_password, decrypt_password, is_encrypted
from backend.nl2sql.sql.query_executor import invalidate_datasource_cache

# Try to import elasticsearch
try:
    from elasticsearch import Elasticsearch
    HAS_ELASTICSEARCH = True
except ImportError:
    HAS_ELASTICSEARCH = False

router = APIRouter()


def _get_mysql_conn():
    """Get metadata DB connection (MySQL)."""
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def get_datasource_conn(ds: dict):
    """Create a connection from a datasource dict based on db_type."""
    db_type = ds.get("db_type", "mysql")

    if db_type == "elasticsearch":
        if not HAS_ELASTICSEARCH:
            raise HTTPException(status_code=400, detail="Elasticsearch 库未安装，请执行: pip install elasticsearch")
        # Return Elasticsearch client - use https if ssl is enabled
        protocol = "https" if ds.get("ssl") else "http"
        es_url = f"{protocol}://{ds['host']}:{ds['port']}"
        es_kwargs = {"hosts": [es_url], "request_timeout": 30, "meta_header": False}
        if ds.get("ssl"):
            es_kwargs["verify_certs"] = False  # Skip cert verification for self-signed certs
            es_kwargs["ssl_show_warn"] = False
        if ds.get("username") and ds.get("password"):
            es_kwargs["basic_auth"] = (ds["username"], ds["password"])
        elif ds.get("username"):
            es_kwargs["basic_auth"] = (ds["username"], "")
        return Elasticsearch(**es_kwargs)
    else:
        # MySQL/Doris - return pymysql connection
        conn_kwargs = {
            "host": ds["host"],
            "port": ds["port"],
            "user": ds["username"],
            "password": ds["password"],
            "database": ds.get("database_name") or None,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": 10,
            "read_timeout": 30,
        }
        # 如果配置了 SSL，添加 SSL 参数
        if ds.get("ssl"):
            conn_kwargs["ssl"] = {
                "ssl_disabled": False,
                "ssl_verify_cert": False,
                "ssl_verify_identity": False,
            }
        return pymysql.connect(**conn_kwargs)


def get_datasource_by_id(ds_id: int) -> dict:
    try:
        conn = _get_mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_datasources WHERE id = %s", (ds_id,))
                row = cur.fetchone()
                if row and row.get("password"):
                    password = row["password"]
                    if is_encrypted(password):
                        try:
                            row["password"] = decrypt_password(password)
                        except ValueError as e:
                            # Log but continue - will fail at connection time
                            import logging
                            logging.getLogger(__name__).warning(
                                "Failed to decrypt password for datasource %s: %s", ds_id, e
                            )
                return row
        finally:
            conn.close()
    except Exception:
        # MySQL unavailable — return default Doris config for id=0
        if ds_id == 0:
            return {
                "id": 0, "name": f"Doris ({DORIS_HOST})", "db_type": "doris",
                "host": DORIS_HOST, "port": DORIS_PORT, "username": DORIS_USER,
                "password": DORIS_PASSWORD, "database_name": DORIS_DATABASE,
                "is_default": 1, "ssl": 0, "owner_id": 0,
            }
        return None



_default_ds_checked = False

def _ensure_default_datasource():
    """Auto-create a default Doris datasource from .env config if none exists."""
    global _default_ds_checked
    if _default_ds_checked:
        return
    try:
        conn = _get_mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_datasources")
                row = cur.fetchone()
                if row and row["cnt"] > 0:
                    _default_ds_checked = True
                    return
                ds_id = int(time.time() * 1000)
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    "INSERT INTO adh_datasources (id, name, db_type, host, port, username, password, database_name, is_default, owner_id, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (ds_id, f"Doris ({DORIS_HOST})", "doris", DORIS_HOST, DORIS_PORT,
                     DORIS_USER, encrypt_password(DORIS_PASSWORD), DORIS_DATABASE, 1, 0, now, now),
                )
            conn.commit()
            _default_ds_checked = True
        finally:
            conn.close()
    except Exception:
        pass  # MySQL unavailable, skip default creation

@router.get("/")
def list_datasources(user: UserInfo = Depends(get_current_user)):
    _ensure_default_datasource()
    try:
        conn = _get_mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, db_type, host, port, username, database_name, is_default, `ssl`, owner_id, created_at, updated_at "
                    "FROM adh_datasources ORDER BY is_default DESC, name ASC"
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
    except Exception:
        # MySQL unavailable — return default Doris from .env as fallback
        return [{
            "id": 0,
            "name": f"Doris ({DORIS_HOST})",
            "db_type": "doris",
            "host": DORIS_HOST,
            "port": DORIS_PORT,
            "database_name": DORIS_DATABASE,
            "is_default": 1,
            "ssl": 0,
            "owner_id": 0,
            "created_at": "",
            "updated_at": "",
        }]


@router.post("/")
def create_datasource(req: DatasourceCreate, user: UserInfo = Depends(require_admin)):
    ds_id = int(time.time() * 1000)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            if req.is_default:
                cur.execute("UPDATE adh_datasources SET is_default = 0")
            cur.execute(
                "INSERT INTO adh_datasources (id, name, db_type, host, port, username, password, database_name, is_default, `ssl`, owner_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ds_id, req.name, req.db_type, req.host, req.port,
                 req.username, encrypt_password(req.password), req.database_name or "",
                 1 if req.is_default else 0, 1 if req.ssl else 0, user.id, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    invalidate_datasource_cache(ds_id)
    log_audit(user.id, user.username, "create_datasource",
              target_type="datasource", target_id=ds_id,
              detail=f"创建数据源 {req.name}", module="datasource")
    return {"id": ds_id}


@router.put("/{ds_id}")
def update_datasource(ds_id: int, req: DatasourceUpdate, user: UserInfo = Depends(require_admin)):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            updates = ["updated_at = %s"]
            params = [now]
            if req.name is not None:
                updates.append("name = %s"); params.append(req.name)
            if req.db_type is not None:
                updates.append("db_type = %s"); params.append(req.db_type)
            if req.host is not None:
                updates.append("host = %s"); params.append(req.host)
            if req.port is not None:
                updates.append("port = %s"); params.append(req.port)
            if req.username is not None:
                updates.append("username = %s"); params.append(req.username)
            if req.password:  # skip empty password to avoid overwriting existing
                updates.append("password = %s"); params.append(encrypt_password(req.password))
            if req.database_name is not None:
                updates.append("database_name = %s"); params.append(req.database_name)
            if req.is_default is not None:
                if req.is_default:
                    cur.execute("UPDATE adh_datasources SET is_default = 0")
                updates.append("is_default = %s"); params.append(1 if req.is_default else 0)
            if req.ssl is not None:
                updates.append("`ssl` = %s"); params.append(1 if req.ssl else 0)
            params.append(ds_id)
            cur.execute(f"UPDATE adh_datasources SET {', '.join(updates)} WHERE id = %s", params)
        conn.commit()
    finally:
        conn.close()
    invalidate_datasource_cache(ds_id)
    log_audit(user.id, user.username, "update_datasource",
              target_type="datasource", target_id=ds_id,
              detail=f"更新数据源", module="datasource")
    return {"success": True}


@router.delete("/{ds_id}")
def delete_datasource(ds_id: int, user: UserInfo = Depends(require_admin)):
    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_datasources WHERE id = %s", (ds_id,))
        conn.commit()
    finally:
        conn.close()
    invalidate_datasource_cache(ds_id)
    log_audit(user.id, user.username, "delete_datasource",
              target_type="datasource", target_id=ds_id,
              detail=f"删除数据源 {ds_id}", module="datasource")
    return {"success": True}


@router.post("/{ds_id}/test")
def test_connection(ds_id: int, user: UserInfo = Depends(get_current_user)):
    ds = get_datasource_by_id(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        conn = get_datasource_conn(ds)
        if ds.get("db_type") == "elasticsearch":
            # Test Elasticsearch connection
            info = conn.info()
            conn.close()
            return {"success": True, "message": f"连接成功 - ES {info.get('version', {}).get('number', 'unknown')}"}
        else:
            # Test MySQL/Doris connection
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            return {"success": True, "message": "连接成功"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "Datasource test connection failed for ds_id=%s: %s (type=%s)",
            ds_id, e, type(e).__name__,
            exc_info=True
        )
        # 提供更友好的错误信息
        error_msg = str(e)
        if hasattr(e, 'errno'):
            error_msg = f"[{e.errno}] {getattr(e, 'errmsg', str(e))}"
        return {"success": False, "message": error_msg}


@router.get("/{ds_id}/tables")
def list_tables(ds_id: int, user: UserInfo = Depends(get_current_user)):
    ds = get_datasource_by_id(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")

    if ds.get("db_type") == "elasticsearch":
        # List Elasticsearch indices
        conn = get_datasource_conn(ds)
        try:
            try:
                indices = conn.indices.get_alias(index="*")
            except Exception:
                # Fallback: use cat.indices if get_alias fails
                cat = conn.cat.indices(format="json", h="index,docs.count,store.size")
                result = []
                for row in cat:
                    index_name = row.get("index", "")
                    if not index_name.startswith('.'):
                        result.append({
                            "TABLE_NAME": index_name,
                            "TABLE_COMMENT": f"ES Index",
                            "TABLE_ROWS": int(row.get("docs.count", 0) or 0),
                        })
                return result
            result = []
            for index_name, alias_info in sorted(indices.items()):
                if not index_name.startswith('.'):  # Skip system indices
                    result.append({
                        "TABLE_NAME": index_name,
                        "TABLE_COMMENT": f"ES Index",
                        "TABLE_ROWS": 0,
                    })
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"获取 ES 索引列表失败: {e}")
        finally:
            conn.close()
    else:
        # MySQL/Doris
        conn = get_datasource_conn(ds)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS, DATA_LENGTH "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                    "ORDER BY TABLE_NAME",
                    (ds.get("database_name") or "",),
                )
                return cur.fetchall()
        finally:
            conn.close()


@router.get("/{ds_id}/tables/{table_name}/columns")
def list_columns(ds_id: int, table_name: str, user: UserInfo = Depends(get_current_user)):
    ds = get_datasource_by_id(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")

    if ds.get("db_type") == "elasticsearch":
        # Get Elasticsearch index mapping
        conn = get_datasource_conn(ds)
        try:
            mapping = conn.indices.get_mapping(index=table_name)
            result = []
            if table_name in mapping:
                # ES 8.x returns ObjectApiResponse, handle both dict and response formats
                mapping_body = mapping[table_name] if isinstance(mapping[table_name], dict) else mapping[table_name].body if hasattr(mapping[table_name], 'body') else {}
                properties = mapping_body.get('mappings', {}).get('properties', {})
                for field_name, field_info in properties.items():
                    result.append({
                        "COLUMN_NAME": field_name,
                        "DATA_TYPE": field_info.get('type', 'text'),
                        "COLUMN_COMMENT": "",
                        "COLUMN_KEY": "",
                        "IS_NULLABLE": "YES",
                    })
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"获取 ES 索引字段失败: {e}")
        finally:
            conn.close()
    else:
        # MySQL/Doris
        conn = get_datasource_conn(ds)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (ds.get("database_name") or "", table_name),
                )
                return cur.fetchall()
        finally:
            conn.close()


@router.post("/{ds_id}/execute")
def execute_sql(ds_id: int, req: dict, user: UserInfo = Depends(get_current_user)):
    ds = get_datasource_by_id(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    sql = req.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL cannot be empty")

    import time as _time
    start = _time.time()

    if ds.get("db_type") == "elasticsearch":
        # Elasticsearch: execute SQL via ES SQL API
        from backend.nl2sql.sql.query_executor import _build_es_client
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
            log_audit(user.id, user.username, "execute_sql",
                      target_type="datasource", target_id=ds_id,
                      detail=f"执行SQL: {sql[:200]}", module="datasource")
            return {"columns": col_names, "rows": rows, "row_count": len(rows), "elapsed_ms": elapsed_ms}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            es.close()
    else:
        # MySQL/Doris
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
            log_audit(user.id, user.username, "execute_sql",
                      target_type="datasource", target_id=ds_id,
                      detail=f"执行SQL: {sql[:200]}", module="datasource")
            return {"columns": columns, "rows": rows, "row_count": len(rows), "elapsed_ms": elapsed_ms}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()
