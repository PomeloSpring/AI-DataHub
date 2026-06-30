"""Dashboard API — CRUD for dashboards, charts, snapshots, and data sources."""
import json
import logging
import re
import time
import math

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from backend.api.auth import get_current_user
from backend.models.schemas import (
    UserInfo, DashboardCreate, DashboardUpdate, ChartConfig,
)
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn
from backend.nl2sql.sql.query_executor import validate_sql
from backend.common.ttl_cache import dashboard_cache

logger = logging.getLogger(__name__)
router = APIRouter()


def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None and datetime with ISO string for JSON compliance."""
    import datetime as _dt
    from decimal import Decimal
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, _dt.timedelta):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts_id():
    return int(time.time() * 1000)


# ── Dashboard CRUD ──────────────────────────────────────────────────────────

def _fetch_dashboards_from_db(user_id: int, workspace_id: int = 0) -> list:
    """Fetch dashboards from database."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id:
                cur.execute(
                    "SELECT * FROM adh_dashboards "
                    "WHERE workspace_id = %s AND (owner_id = %s OR is_public = 1) "
                    "ORDER BY is_default DESC, sort_order ASC, updated_at DESC",
                    (workspace_id, user_id),
                )
            else:
                cur.execute(
                    "SELECT * FROM adh_dashboards "
                    "WHERE owner_id = %s OR is_public = 1 "
                    "ORDER BY is_default DESC, sort_order ASC, updated_at DESC",
                    (user_id,),
                )
            dashboards = cur.fetchall()
            if not dashboards:
                return []

            # Fetch all charts in one query
            dash_ids = [d["id"] for d in dashboards]
            placeholders = ",".join(["%s"] * len(dash_ids))
            cur.execute(f"SELECT * FROM adh_charts WHERE dashboard_id IN ({placeholders}) ORDER BY id", dash_ids)
            all_charts = cur.fetchall()

            # Group charts by dashboard_id
            charts_map: dict = {}
            for c in all_charts:
                for field in ("config", "position"):
                    if isinstance(c.get(field), str):
                        c[field] = json.loads(c[field])
                c.setdefault("source_type", "query")
                c.setdefault("source_id", None)
                c.setdefault("data_cache", None)
                charts_map.setdefault(c["dashboard_id"], []).append(c)

            for d in dashboards:
                for field in ("layout", "filters", "params"):
                    if isinstance(d.get(field), str):
                        d[field] = json.loads(d[field])
                d.setdefault("is_default", 0)
                d.setdefault("carousel_interval", 0)
                d.setdefault("params", [])
                d.setdefault("status", "designing")
                d["charts"] = charts_map.get(d["id"], [])
            return dashboards
    finally:
        conn.close()


def _invalidate_dashboard_cache(user_id: int = None):
    """Clear dashboard cache. user_id=None clears all."""
    if user_id is None:
        dashboard_cache.invalidate()
    else:
        dashboard_cache.invalidate(f"dash:{user_id}")


@router.get("/")
def list_dashboards(user: UserInfo = Depends(get_current_user), workspace_id: int = 0):
    cache_key = f"dash:{user.id}:{workspace_id}"
    return dashboard_cache.get_or_set(cache_key, lambda: _fetch_dashboards_from_db(user.id, workspace_id))


@router.post("/")
def create_dashboard(req: DashboardCreate, user: UserInfo = Depends(get_current_user)):
    did = _ts_id()
    now = _now()

    if req.is_default:
        _clear_default(user.id)

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_dashboards "
                "(`id`, `name`, `description`, `layout`, `filters`, `params`, `status`, `owner_id`, `workspace_id`, `is_public`, `is_default`, `carousel_interval`, `created_at`, `updated_at`) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (did, req.name, req.description, json.dumps(req.layout or []),
                 json.dumps(req.filters or {}), json.dumps(req.params or []),
                 req.status or "designing", user.id, req.workspace_id or 0,
                 1 if req.is_public else 0, 1 if req.is_default else 0,
                 req.carousel_interval, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    _invalidate_dashboard_cache(user.id)
    return {"id": did}


@router.put("/{dashboard_id}")
def update_dashboard(dashboard_id: int, req: DashboardUpdate, user: UserInfo = Depends(get_current_user)):
    now = _now()

    if req.is_default:
        _clear_default(user.id)

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            updates = ["updated_at = %s"]
            params = [now]
            if req.name is not None:
                updates.append("name = %s"); params.append(req.name)
            if req.description is not None:
                updates.append("description = %s"); params.append(req.description)
            if req.layout is not None:
                updates.append("`layout` = %s"); params.append(json.dumps(req.layout))
            if req.filters is not None:
                updates.append("`filters` = %s"); params.append(json.dumps(req.filters))
            if req.params is not None:
                updates.append("`params` = %s"); params.append(json.dumps(req.params))
            if req.status is not None:
                updates.append("`status` = %s"); params.append(req.status)
            if req.is_public is not None:
                updates.append("is_public = %s"); params.append(1 if req.is_public else 0)
            if req.is_default is not None:
                updates.append("is_default = %s"); params.append(1 if req.is_default else 0)
            if req.carousel_interval is not None:
                updates.append("carousel_interval = %s"); params.append(req.carousel_interval)
            params.append(dashboard_id)
            cur.execute(f"UPDATE adh_dashboards SET {', '.join(updates)} WHERE id = %s", params)
        conn.commit()
    finally:
        conn.close()
    _invalidate_dashboard_cache(user.id)
    return {"success": True}


@router.delete("/{dashboard_id}")
def delete_dashboard(dashboard_id: int, user: UserInfo = Depends(get_current_user)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_charts WHERE dashboard_id = %s", (dashboard_id,))
            cur.execute("DELETE FROM adh_dashboards WHERE id = %s AND owner_id = %s", (dashboard_id, user.id))
        conn.commit()
    finally:
        conn.close()
    _invalidate_dashboard_cache(user.id)
    return {"success": True}


@router.post("/reorder")
def reorder_dashboards(req: dict, user: UserInfo = Depends(get_current_user)):
    """Update sort_order for dashboards."""
    orders = req.get("orders", [])
    if not orders:
        return {"success": True}
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for item in orders:
                cur.execute(
                    "UPDATE adh_dashboards SET sort_order=%s, updated_at=%s WHERE id=%s AND owner_id=%s",
                    (item.get("sort_order", 0), _now(), item["id"], user.id),
                )
        conn.commit()
    finally:
        conn.close()
    _invalidate_dashboard_cache(user.id)
    return {"success": True}


def _clear_default(user_id: int):
    """Clear is_default flag for all dashboards of a user."""
    try:
        conn = _get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_dashboards SET is_default = 0 WHERE owner_id = %s AND is_default = 1",
                    (user_id,),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to clear default dashboard: %s", e)


@router.post("/{dashboard_id}/copy")
def copy_dashboard(dashboard_id: int, user: UserInfo = Depends(get_current_user)):
    """Copy a dashboard with all its charts."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (dashboard_id,))
            src = cur.fetchone()
            if not src:
                raise HTTPException(404, "仪表盘不存在")

            cur.execute("SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id", (dashboard_id,))
            charts = cur.fetchall()

            now = _now()
            new_id = _ts_id()
            cur.execute(
                "INSERT INTO adh_dashboards "
                "(`id`, `name`, `description`, `layout`, `filters`, `params`, `status`, "
                "`owner_id`, `is_public`, `is_default`, `carousel_interval`, `sort_order`, `created_at`, `updated_at`) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (new_id, f"{src['name']} (副本)", src.get("description"),
                 src.get("layout"), src.get("filters"), src.get("params"),
                 "designing", user.id, 0, 0,
                 src.get("carousel_interval", 0), 0, now, now),
            )

            for c in charts:
                cid = _ts_id()
                time.sleep(0.001)
                cur.execute(
                    "INSERT INTO adh_charts "
                    "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, "
                    "`config`, `position`, `source_type`, `source_id`, `data_cache`, `created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cid, new_id, c["name"], c["chart_type"], c.get("sql_query"),
                     c.get("config"), c.get("position"), c.get("source_type", "query"),
                     c.get("source_id"), c.get("data_cache"), now, now),
                )

            conn.commit()

            cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (new_id,))
            new_dashboard = cur.fetchone()
            cur.execute("SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id", (new_id,))
            new_charts = cur.fetchall()

            for field in ("layout", "filters", "params"):
                if isinstance(new_dashboard.get(field), str):
                    new_dashboard[field] = json.loads(new_dashboard[field])
            new_dashboard.setdefault("is_default", 0)
            new_dashboard.setdefault("carousel_interval", 0)
            new_dashboard.setdefault("params", [])
            new_dashboard.setdefault("status", "designing")

            for c in new_charts:
                for field in ("config", "position"):
                    if isinstance(c.get(field), str):
                        c[field] = json.loads(c[field])
                c.setdefault("source_type", "query")
                c.setdefault("source_id", None)
                c.setdefault("data_cache", None)

            new_dashboard["charts"] = new_charts
            return new_dashboard
    finally:
        conn.close()


# ── Chart CRUD ──────────────────────────────────────────────────────────────

@router.post("/{dashboard_id}/charts")
def add_chart(dashboard_id: int, req: ChartConfig, user: UserInfo = Depends(get_current_user)):
    cid = _ts_id()
    now = _now()
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_charts "
                "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, `config`, `position`, `source_type`, `source_id`, `data_cache`, `created_at`, `updated_at`) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, dashboard_id, req.name, req.chart_type, req.sql_query,
                 json.dumps(req.config or {}), json.dumps(req.position or {}),
                 req.source_type or "query", req.source_id,
                 req.data_cache, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    return {"id": cid}


@router.put("/{dashboard_id}/charts/{chart_id}")
def update_chart(dashboard_id: int, chart_id: int, req: ChartConfig, user: UserInfo = Depends(get_current_user)):
    now = _now()
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_charts SET name=%s, chart_type=%s, sql_query=%s, config=%s, `position`=%s, "
                "source_type=%s, source_id=%s, data_cache=%s, updated_at=%s "
                "WHERE id=%s AND dashboard_id=%s",
                (req.name, req.chart_type, req.sql_query,
                 json.dumps(req.config or {}), json.dumps(req.position or {}),
                 req.source_type or "query", req.source_id,
                 req.data_cache, now, chart_id, dashboard_id),
            )
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@router.delete("/{dashboard_id}/charts/{chart_id}")
def delete_chart(dashboard_id: int, chart_id: int, user: UserInfo = Depends(get_current_user)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_charts WHERE id = %s AND dashboard_id = %s", (chart_id, dashboard_id))
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


def _sanitize_param_value(val: str) -> str:
    """Escape a string parameter value to prevent SQL injection.

    - Backslash-escapes single quotes, backslashes, and NUL bytes.
    - Rejects values containing semicolons (statement separator).
    - Rejects values containing comment markers.
    """
    if any(marker in val for marker in (";", "--", "/*", "*/", "\x00")):
        raise HTTPException(status_code=400, detail=f"参数值包含非法字符: {val[:50]}")
    # Escape backslashes first, then single quotes
    val = val.replace("\\", "\\\\").replace("'", "\\'")
    return val


def _flatten_params(params: dict, prefix: str = "") -> dict:
    """Flatten nested dict: {time: {start: "x"}} -> {"time.start": "x"}."""
    flat = {}
    for k, v in params.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_params(v, key))
        else:
            flat[key] = v
    return flat


def _substitute_params(sql: str, params: dict) -> str:
    """Replace {{param_name}} placeholders in SQL with parameter values.

    Supports nested keys via dot notation: {{time.start}}, {{time.end}}.
    String values are escaped to prevent SQL injection.
    """
    if params is None or not sql:
        return sql

    # Flatten nested params for dot-notation access
    flat = _flatten_params(params)

    # Default values for pagination params
    _PAGINATION_DEFAULTS = {"page_limit": "20", "page_offset": "0"}

    def replacer(m):
        key = m.group(1).strip()
        val = flat.get(key, _PAGINATION_DEFAULTS.get(key, ""))
        if val is None or val == "":
            val = _PAGINATION_DEFAULTS.get(key, "")
        val_str = str(val)
        # If value looks numeric (int or float), pass through directly
        try:
            float(val_str)
            return val_str
        except ValueError:
            pass
        # String value — escape for SQL safety
        return _sanitize_param_value(val_str)

    return re.sub(r'\{\{(\w+(?:\.\w+)*)\}\}', replacer, sql)


def _get_chart_datasource_id(chart: dict) -> int:
    """Extract datasource_id from chart config."""
    config = chart.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {}
    if isinstance(config, dict):
        return config.get("datasource_id", 0)
    return 0


def _execute_on_datasource(sql: str, datasource_id: int) -> dict:
    """Execute SQL on the specified datasource and return result dict."""
    from backend.api.datasource import get_datasource_by_id, get_datasource_conn
    ds = get_datasource_by_id(datasource_id)
    if not ds:
        raise Exception(f"数据源 {datasource_id} 不存在")

    db_type = ds.get("db_type", "mysql")

    if db_type == "elasticsearch":
        # Elasticsearch: use ES SQL API
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
                return {"columns": [], "rows": [], "row_count": 0}
            col_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns_info)]
            rows = [dict(zip(col_names, row)) for row in rows_data]
            data = _sanitize_floats(rows)
            return {"columns": col_names, "rows": data, "row_count": len(data)}
        finally:
            es.close()
    else:
        # MySQL/Doris
        conn = get_datasource_conn(ds)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                columns = list(rows[0].keys()) if rows else []
                data = _sanitize_floats(rows)
                return {"columns": columns, "rows": data, "row_count": len(data)}
        finally:
            conn.close()


@router.post("/{dashboard_id}/charts/{chart_id}/refresh")
def refresh_chart(dashboard_id: int, chart_id: int, req: dict = {}, user: UserInfo = Depends(get_current_user)):
    """Re-execute a chart's SQL query on its configured datasource. Supports param substitution."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, sql_query, config FROM adh_charts WHERE id = %s AND dashboard_id = %s",
                (chart_id, dashboard_id),
            )
            chart = cur.fetchone()
            if not chart:
                raise HTTPException(status_code=404, detail="图表不存在")

            sql = chart.get("sql_query", "")
            if not sql:
                return {"columns": [], "rows": [], "row_count": 0}

            # Get datasource from chart config
            datasource_id = _get_chart_datasource_id(chart)

            # Substitute parameters
            runtime_params = req.get("params", {})
            sql = _substitute_params(sql, runtime_params)

            # Server-side pagination: page_limit/page_offset from request body
            page_limit = req.get("page_limit")
            page_offset = req.get("page_offset")
            custom_count_sql = req.get("count_sql")
            logger.info("[ChartRefresh] req keys=%s, page_limit=%s, page_offset=%s, count_sql=%s", list(req.keys()), page_limit, page_offset, custom_count_sql)
            total = None

            sql = sql.strip().rstrip(";")

            if page_limit is not None:
                # Get total count
                if custom_count_sql:
                    count_sql = _substitute_params(custom_count_sql, runtime_params).strip().rstrip(";")
                else:
                    base_sql = re.sub(r'\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$', '', sql, flags=re.IGNORECASE).strip()
                    count_sql = f"SELECT COUNT(*) AS cnt FROM ({base_sql}) _t"
                logger.info("[ChartRefresh] count_sql=%s, custom=%s", count_sql, bool(custom_count_sql))
                # Count queries don't need LIMIT, validate without LIMIT check
                ok, msg = validate_sql(count_sql, require_limit=False)
                if ok:
                    try:
                        count_result = _execute_on_datasource(count_sql, datasource_id)
                        logger.info("[ChartRefresh] count_result=%s", count_result)
                        if count_result["rows"]:
                            row = count_result["rows"][0]
                            # Support various column names: cnt, count(*), COUNT(*), etc.
                            total = row.get("cnt") or row.get("count(*)") or row.get("COUNT(*)") or list(row.values())[0] if row else 0
                        logger.info("[ChartRefresh] count total=%s", total)
                    except Exception as e:
                        logger.warning("[ChartRefresh] count query failed: %s", e)
                else:
                    logger.warning("[ChartRefresh] count SQL validation failed: %s", msg)
                # Apply pagination LIMIT/OFFSET
                sql = f"{sql} LIMIT {int(page_limit)} OFFSET {int(page_offset or 0)}"
            elif "limit" not in sql.lower():
                sql += " LIMIT 500"

            # Validate SQL safety before execution
            ok, msg = validate_sql(sql)
            if not ok:
                raise HTTPException(status_code=400, detail=f"SQL 校验失败: {msg}")

            logger.info("[ChartRefresh] chart_id=%s, params=%s, page_limit=%s, page_offset=%s, sql=%s", chart_id, runtime_params, page_limit, page_offset, sql)

            # Execute on chart's datasource
            result = _execute_on_datasource(sql, datasource_id)
            if total is not None:
                result["total"] = total

            # Update data_cache in system DB
            now = _now()
            cache = json.dumps({"columns": result["columns"], "rows": result["rows"]}, ensure_ascii=False)
            cur.execute(
                "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
                (cache, now, chart_id),
            )
            conn.commit()

            return result
    except pymysql.Error as e:
        raise HTTPException(status_code=400, detail=f"SQL 执行失败: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@router.post("/{dashboard_id}/refresh")
def refresh_all_charts(dashboard_id: int, req: dict = {}, user: UserInfo = Depends(get_current_user)):
    """Re-execute all charts' SQL in a dashboard on their respective datasources."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, sql_query, config FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                (dashboard_id,),
            )
            charts = cur.fetchall()

            runtime_params = req.get("params", {})
            results = {}
            now = _now()

            for chart in charts:
                cid = chart["id"]
                sql = chart.get("sql_query", "")
                if not sql:
                    results[cid] = {"columns": [], "rows": [], "row_count": 0}
                    continue

                datasource_id = _get_chart_datasource_id(chart)
                sql = _substitute_params(sql, runtime_params)
                sql = sql.strip().rstrip(";")
                if "limit" not in sql.lower():
                    sql += " LIMIT 500"

                # Validate SQL safety before execution
                ok, msg = validate_sql(sql)
                if not ok:
                    results[cid] = {"error": f"SQL 校验失败: {msg}"}
                    continue

                logger.info("[ChartRefresh] chart_id=%s, params=%s, sql=%s", cid, runtime_params, sql)

                try:
                    result = _execute_on_datasource(sql, datasource_id)
                    results[cid] = result

                    cache = json.dumps({"columns": result["columns"], "rows": result["rows"]}, ensure_ascii=False)
                    cur.execute(
                        "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
                        (cache, now, cid),
                    )
                except Exception as e:
                    results[cid] = {"error": str(e)}

            conn.commit()
            return {"charts": results}
    finally:
        conn.close()


@router.put("/{dashboard_id}/layout")
def update_layout(dashboard_id: int, req: dict, user: UserInfo = Depends(get_current_user)):
    """Batch update chart positions (layout save)."""
    layouts = req.get("layouts", [])
    if not layouts:
        return {"success": True}
    now = _now()
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for item in layouts:
                chart_id = item.get("chart_id")
                position = item.get("position", {})
                if chart_id:
                    cur.execute(
                        "UPDATE adh_charts SET `position`=%s, updated_at=%s WHERE id=%s AND dashboard_id=%s",
                        (json.dumps(position), now, chart_id, dashboard_id),
                    )
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


# ── Chart Snapshots ─────────────────────────────────────────────────────────

@router.get("/snapshots")
def list_snapshots(
    days: int = QueryParam(7),
    user: UserInfo = Depends(get_current_user),
):
    """Get recent chart snapshots from chat executions."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, question, sql_query, chart_type, brief, `columns`, row_count, created_at "
                "FROM adh_chart_snapshots "
                "WHERE user_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                "ORDER BY created_at DESC LIMIT 100",
                (user.id, days),
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                if isinstance(r.get("columns"), str):
                    r["columns"] = json.loads(r["columns"])
            return rows
    finally:
        conn.close()


@router.get("/snapshots/{snapshot_id}/data")
def get_snapshot_data(snapshot_id: int, user: UserInfo = Depends(get_current_user)):
    """Get full snapshot data including data rows."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adh_chart_snapshots WHERE id = %s AND user_id = %s",
                (snapshot_id, user.id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="快照不存在")
            if hasattr(row.get("created_at"), "isoformat"):
                row["created_at"] = row["created_at"].isoformat()
            if isinstance(row.get("columns"), str):
                row["columns"] = json.loads(row["columns"])
            if isinstance(row.get("data_snapshot"), str):
                row["data_snapshot"] = _sanitize_floats(json.loads(row["data_snapshot"]))
            return row
    finally:
        conn.close()


def save_snapshot(
    user_id: int, question: str, sql_query: str,
    chart_type: str, brief: str, columns: list,
    rows: list, row_count: int, datasource_id: int = 0,
):
    """Save a chart snapshot after successful query execution."""
    sid = _ts_id()
    now = _now()
    try:
        conn = _get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_chart_snapshots "
                    "(`id`, `user_id`, `datasource_id`, `question`, `sql_query`, `chart_type`, `brief`, `columns`, `data_snapshot`, `row_count`, `created_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, user_id, datasource_id, question, sql_query, chart_type, brief,
                     json.dumps(columns), json.dumps(_sanitize_floats(rows[:500]), ensure_ascii=False),
                     row_count, now),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to save chart snapshot: %s", e)


@router.post("/preview")
def preview_datasource(req: dict, user: UserInfo = Depends(get_current_user)):
    """Execute a saved query/dataset SQL and return preview data."""
    source_type = req.get("source_type", "")
    source_id = req.get("source_id", 0)
    if not source_id:
        return {"error": "Missing source_id"}

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            sql_query = None
            if source_type == "query" or source_type == "dataset":
                cur.execute("SELECT sql_query FROM adh_saved_queries WHERE id = %s AND owner_id = %s", (source_id, user.id))
                row = cur.fetchone()
                if row:
                    sql_query = row.get("sql_query")

            if not sql_query:
                return {"error": "Query not found"}

            # Execute the SQL (with LIMIT for safety)
            execute_sql = sql_query.strip().rstrip(";")
            if "limit" not in execute_sql.lower():
                execute_sql += " LIMIT 200"

            # Validate SQL safety
            ok, msg = validate_sql(execute_sql)
            if not ok:
                return {"error": f"SQL 校验失败: {msg}"}

            cur.execute(execute_sql)
            rows = cur.fetchall()
            columns = list(rows[0].keys()) if rows else []
            return {"columns": columns, "rows": _sanitize_floats(rows), "row_count": len(rows)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ── Data Sources Aggregation ────────────────────────────────────────────────

@router.get("/datasources")
def list_datasources(user: UserInfo = Depends(get_current_user)):
    """Aggregate available data sources: snapshots, saved queries, datasets."""
    result = []
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Recent chart snapshots (last 7 days)
            cur.execute(
                "SELECT id, question AS name, chart_type, brief AS description, created_at "
                "FROM adh_chart_snapshots "
                "WHERE user_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
                "ORDER BY created_at DESC LIMIT 50",
                (user.id,),
            )
            for r in cur.fetchall():
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["type"] = "snapshot"
                result.append(r)

            # Saved queries
            cur.execute(
                "SELECT id, name, description, created_at FROM adh_saved_queries "
                "WHERE owner_id = %s AND (is_dataset = 0 OR is_dataset IS NULL) "
                "ORDER BY updated_at DESC LIMIT 50",
                (user.id,),
            )
            for r in cur.fetchall():
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["type"] = "query"
                r["chart_type"] = None
                result.append(r)

            # Saved datasets
            cur.execute(
                "SELECT id, name, description, created_at FROM adh_saved_queries "
                "WHERE owner_id = %s AND is_dataset = 1 "
                "ORDER BY updated_at DESC LIMIT 50",
                (user.id,),
            )
            for r in cur.fetchall():
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["type"] = "dataset"
                r["chart_type"] = None
                result.append(r)

        return result
    finally:
        conn.close()
