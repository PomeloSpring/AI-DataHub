"""Dashboard Service -- Business logic for dashboards, charts, and snapshots.

Migrated from backend/api/dashboard.py into service classes:
- DashboardService: Dashboard CRUD, reorder, copy
- ChartService: Chart CRUD, refresh, batch refresh, layout
- SnapshotService: Chart snapshot save/list/get

Uses shared DB connection from services/shared/common/db.
Tables: adh_dashboards, adh_charts, adh_chart_snapshots, adh_saved_queries
"""

import json
import logging
import math
import re
import time
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.db.datasource_db import get_datasource_by_id, get_datasource_conn
from services.shared.common.ttl_cache import dashboard_cache

logger = logging.getLogger(__name__)


# ── Utility Functions ────────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    return int(time.time() * 1000)


def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None and datetime with ISO string for JSON compliance."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _json_loads_safe(val):
    """Parse JSON string, return original if already dict/list."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _normalize_dashboard(row: dict) -> dict:
    """Normalize dashboard row for JSON serialization."""
    for field in ("layout", "filters", "params"):
        row[field] = _json_loads_safe(row.get(field))
    for ts in ("created_at", "updated_at"):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    row.setdefault("is_default", 0)
    row.setdefault("carousel_interval", 0)
    row.setdefault("params", [])
    row.setdefault("status", "designing")
    return row


def _normalize_chart(row: dict) -> dict:
    """Normalize chart row for JSON serialization."""
    for field in ("config", "position"):
        row[field] = _json_loads_safe(row.get(field))
    for ts in ("created_at", "updated_at"):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    row.setdefault("source_type", "query")
    row.setdefault("source_id", None)
    row.setdefault("data_cache", None)
    return row


def _sanitize_param_value(val: str) -> str:
    """Escape a string parameter value to prevent SQL injection.

    - Backslash-escapes single quotes, backslashes, and NUL bytes.
    - Rejects values containing semicolons (statement separator).
    - Rejects values containing comment markers.
    """
    if any(marker in val for marker in (";", "--", "/*", "*/", "\x00")):
        raise ValueError(f"参数值包含非法字符: {val[:50]}")
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

    flat = _flatten_params(params)

    _PAGINATION_DEFAULTS = {"page_limit": "20", "page_offset": "0"}

    def replacer(m):
        key = m.group(1).strip()
        val = flat.get(key, _PAGINATION_DEFAULTS.get(key, ""))
        if val is None or val == "":
            val = _PAGINATION_DEFAULTS.get(key, "")
        val_str = str(val)
        try:
            float(val_str)
            return val_str
        except ValueError:
            pass
        return _sanitize_param_value(val_str)

    return re.sub(r'\{\{(\w+(?:\.\w+)*)\}\}', replacer, sql)


def _get_chart_datasource_id(chart: dict) -> int:
    """Extract datasource_id from chart config."""
    config = chart.get("config")
    config = _json_loads_safe(config)
    if isinstance(config, dict):
        return config.get("datasource_id", 0)
    return 0


def _execute_on_datasource(sql: str, datasource_id: int) -> dict:
    """Execute SQL on the specified datasource and return result dict."""
    ds = get_datasource_by_id(datasource_id)
    if not ds:
        raise Exception(f"数据源 {datasource_id} 不存在")

    db_type = ds.get("db_type", "mysql")

    if db_type == "elasticsearch":
        from services.datamind.nl2sql.sql.query_executor import _build_es_client
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
        conn_kwargs = {
            "host": ds["host"],
            "port": ds["port"],
            "user": ds.get("username", ""),
            "password": ds.get("password", ""),
            "database": ds.get("database_name"),
            "charset": "utf8mb4",
            "cursorclass": __import__("pymysql").cursors.DictCursor,
            "connect_timeout": 10,
            "read_timeout": 30,
        }
        conn = __import__("pymysql").connect(**conn_kwargs)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                columns = list(rows[0].keys()) if rows else []
                data = _sanitize_floats(rows)
                return {"columns": columns, "rows": data, "row_count": len(data)}
        finally:
            conn.close()


def _clear_default(user_id: int, workspace_id: int = 0):
    """Clear is_default flag for all dashboards of a user within a workspace."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id:
                    cur.execute(
                        "UPDATE adh_dashboards SET is_default = 0 "
                        "WHERE owner_id = %s AND workspace_id = %s AND is_default = 1",
                        (user_id, workspace_id),
                    )
                else:
                    cur.execute(
                        "UPDATE adh_dashboards SET is_default = 0 "
                        "WHERE owner_id = %s AND is_default = 1",
                        (user_id,),
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to clear default dashboard: %s", e)


def _invalidate_dashboard_cache(user_id: int = None):
    """Clear dashboard cache. user_id=None clears all."""
    if user_id is None:
        dashboard_cache.invalidate()
    else:
        dashboard_cache.invalidate_prefix(f"dash:{user_id}:")


# ── DashboardService ─────────────────────────────────────────────────────────


class DashboardService:
    """Dashboard CRUD operations."""

    def list_dashboards(self, user_id: int, workspace_id: int = 0) -> list:
        """List dashboards scoped by workspace and user ownership.

        Uses TTL cache for performance.
        """
        cache_key = f"dash:{user_id}:{workspace_id}"
        return dashboard_cache.get_or_set(
            cache_key,
            lambda: self._fetch_dashboards_from_db(user_id, workspace_id),
        )

    def _fetch_dashboards_from_db(self, user_id: int, workspace_id: int = 0) -> list:
        """Fetch dashboards from database."""
        conn = get_metadata_conn()
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

                dash_ids = [d["id"] for d in dashboards]
                placeholders = ",".join(["%s"] * len(dash_ids))
                cur.execute(
                    f"SELECT * FROM adh_charts WHERE dashboard_id IN ({placeholders}) ORDER BY id",
                    dash_ids,
                )
                all_charts = cur.fetchall()

                charts_map: dict = {}
                for c in all_charts:
                    _normalize_chart(c)
                    charts_map.setdefault(c["dashboard_id"], []).append(c)

                for d in dashboards:
                    _normalize_dashboard(d)
                    d["charts"] = charts_map.get(d["id"], [])
                return dashboards
        finally:
            conn.close()

    def get_dashboard(self, dashboard_id: int, user_id: int) -> Optional[dict]:
        """Get a single dashboard with its charts."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (dashboard_id,))
                dashboard = cur.fetchone()
                if not dashboard:
                    return None

                cur.execute(
                    "SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()
                for c in charts:
                    _normalize_chart(c)

                _normalize_dashboard(dashboard)
                dashboard["charts"] = charts
                return dashboard
        finally:
            conn.close()

    def create_dashboard(self, data: dict, user_id: int) -> int:
        """Create a new dashboard. Returns the new dashboard ID."""
        did = _ts_id()
        now = _now()
        workspace_id = data.get("workspace_id") or 0

        if data.get("is_default"):
            _clear_default(user_id, workspace_id)

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_dashboards "
                    "(`id`, `name`, `description`, `layout`, `filters`, `params`, `status`, "
                    "`owner_id`, `workspace_id`, `is_public`, `is_default`, `carousel_interval`, "
                    "`created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        did,
                        data["name"],
                        data.get("description", ""),
                        json.dumps(data.get("layout") or []),
                        json.dumps(data.get("filters") or {}),
                        json.dumps(data.get("params") or []),
                        data.get("status", "designing"),
                        user_id,
                        workspace_id,
                        1 if data.get("is_public") else 0,
                        1 if data.get("is_default") else 0,
                        data.get("carousel_interval", 0),
                        now,
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        _invalidate_dashboard_cache(user_id)
        return did

    def update_dashboard(self, dashboard_id: int, data: dict, user_id: int) -> bool:
        """Update a dashboard. Returns True if updated."""
        if not data:
            return False

        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if data.get("is_default"):
                    cur.execute(
                        "SELECT workspace_id FROM adh_dashboards WHERE id = %s",
                        (dashboard_id,),
                    )
                    row = cur.fetchone()
                    ws_id = row["workspace_id"] if row else 0
                    _clear_default(user_id, ws_id)

                updates = ["updated_at = %s"]
                params = [now]

                field_map = {
                    "name": "name",
                    "description": "description",
                    "status": "`status`",
                }
                for key, col in field_map.items():
                    if key in data and data[key] is not None:
                        updates.append(f"{col} = %s")
                        params.append(data[key])

                for json_field in ("layout", "filters", "params"):
                    if json_field in data and data[json_field] is not None:
                        updates.append(f"`{json_field}` = %s")
                        params.append(json.dumps(data[json_field]))

                for bool_field in ("is_public", "is_default"):
                    if bool_field in data and data[bool_field] is not None:
                        updates.append(f"{bool_field} = %s")
                        params.append(1 if data[bool_field] else 0)

                if "carousel_interval" in data and data["carousel_interval"] is not None:
                    updates.append("carousel_interval = %s")
                    params.append(data["carousel_interval"])

                params.append(dashboard_id)
                cur.execute(
                    f"UPDATE adh_dashboards SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_dashboard(self, dashboard_id: int, user_id: int) -> bool:
        """Delete a dashboard and all its charts."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_charts WHERE dashboard_id = %s", (dashboard_id,))
                cur.execute(
                    "DELETE FROM adh_dashboards WHERE id = %s AND owner_id = %s",
                    (dashboard_id, user_id),
                )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            return cur.rowcount > 0
        finally:
            conn.close()

    def reorder_dashboards(self, user_id: int, orders: list) -> bool:
        """Update sort_order for dashboards."""
        if not orders:
            return True
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                for item in orders:
                    cur.execute(
                        "UPDATE adh_dashboards SET sort_order=%s, updated_at=%s "
                        "WHERE id=%s AND owner_id=%s",
                        (item.get("sort_order", 0), now, item["id"], user_id),
                    )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            return True
        finally:
            conn.close()

    def copy_dashboard(self, dashboard_id: int, user_id: int, new_name: str = None) -> Optional[dict]:
        """Copy a dashboard with all its charts (deep copy)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (dashboard_id,))
                src = cur.fetchone()
                if not src:
                    return None

                cur.execute(
                    "SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()

                now = _now()
                new_id = _ts_id()
                name = new_name or f"{src['name']} (副本)"

                cur.execute(
                    "INSERT INTO adh_dashboards "
                    "(`id`, `name`, `description`, `layout`, `filters`, `params`, `status`, "
                    "`owner_id`, `workspace_id`, `is_public`, `is_default`, "
                    "`carousel_interval`, `sort_order`, `created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        new_id, name, src.get("description"),
                        src.get("layout"), src.get("filters"), src.get("params"),
                        "designing", user_id, src.get("workspace_id", 0), 0, 0,
                        src.get("carousel_interval", 0), 0, now, now,
                    ),
                )

                for c in charts:
                    cid = _ts_id()
                    time.sleep(0.001)
                    cur.execute(
                        "INSERT INTO adh_charts "
                        "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, "
                        "`config`, `position`, `source_type`, `source_id`, `data_cache`, "
                        "`created_at`, `updated_at`) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            cid, new_id, c["name"], c["chart_type"], c.get("sql_query"),
                            c.get("config"), c.get("position"),
                            c.get("source_type", "query"),
                            c.get("source_id"), c.get("data_cache"), now, now,
                        ),
                    )

                conn.commit()

                # Fetch the newly created dashboard with charts
                cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (new_id,))
                new_dashboard = cur.fetchone()
                cur.execute(
                    "SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                    (new_id,),
                )
                new_charts = cur.fetchall()

                _normalize_dashboard(new_dashboard)
                for c in new_charts:
                    _normalize_chart(c)
                new_dashboard["charts"] = new_charts

                _invalidate_dashboard_cache(user_id)
                return new_dashboard
        finally:
            conn.close()


# ── ChartService ─────────────────────────────────────────────────────────────


class ChartService:
    """Chart CRUD, refresh, and layout operations."""

    def create_chart(self, dashboard_id: int, data: dict) -> int:
        """Add a chart to a dashboard. Returns the new chart ID."""
        cid = _ts_id()
        now = _now()

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_charts "
                    "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, "
                    "`config`, `position`, `source_type`, `source_id`, `data_cache`, "
                    "`created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        cid, dashboard_id,
                        data["name"], data["chart_type"], data.get("sql_query"),
                        json.dumps(data.get("config") or {}),
                        json.dumps(data.get("position") or {}),
                        data.get("source_type", "query"),
                        data.get("source_id"), data.get("data_cache"),
                        now, now,
                    ),
                )
            conn.commit()
            return cid
        finally:
            conn.close()

    def update_chart(self, dashboard_id: int, chart_id: int, data: dict) -> bool:
        """Update a chart within a dashboard."""
        now = _now()

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_charts SET name=%s, chart_type=%s, sql_query=%s, config=%s, "
                    "`position`=%s, source_type=%s, source_id=%s, data_cache=%s, updated_at=%s "
                    "WHERE id=%s AND dashboard_id=%s",
                    (
                        data.get("name"), data.get("chart_type"),
                        data.get("sql_query"),
                        json.dumps(data.get("config") or {}),
                        json.dumps(data.get("position") or {}),
                        data.get("source_type", "query"),
                        data.get("source_id"), data.get("data_cache"),
                        now, chart_id, dashboard_id,
                    ),
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_chart(self, dashboard_id: int, chart_id: int) -> bool:
        """Remove a chart from a dashboard."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_charts WHERE id = %s AND dashboard_id = %s",
                    (chart_id, dashboard_id),
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def refresh_chart(self, dashboard_id: int, chart_id: int, params: dict = None,
                      page_limit: int = None, page_offset: int = None,
                      count_sql: str = None) -> dict:
        """Re-execute a chart's SQL query on its configured datasource.

        Supports param substitution, server-side pagination, and cache update.
        """
        from services.datamind.nl2sql.sql.query_executor import validate_sql

        params = params or {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, sql_query, config FROM adh_charts "
                    "WHERE id = %s AND dashboard_id = %s",
                    (chart_id, dashboard_id),
                )
                chart = cur.fetchone()
                if not chart:
                    raise ValueError("图表不存在")

                sql = chart.get("sql_query", "")
                if not sql:
                    return {"columns": [], "rows": [], "row_count": 0}

                datasource_id = _get_chart_datasource_id(chart)
                sql = _substitute_params(sql, params)
                sql = sql.strip().rstrip(";")

                total = None

                if page_limit is not None:
                    # Count query
                    if count_sql:
                        count_sql = _substitute_params(count_sql, params).strip().rstrip(";")
                    else:
                        base_sql = re.sub(
                            r'\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$', '',
                            sql, flags=re.IGNORECASE,
                        ).strip()
                        count_sql = f"SELECT COUNT(*) AS cnt FROM ({base_sql}) _t"

                    ok, msg = validate_sql(count_sql, require_limit=False)
                    if ok:
                        try:
                            count_result = _execute_on_datasource(count_sql, datasource_id)
                            if count_result.get("rows"):
                                row = count_result["rows"][0]
                                total = (
                                    row.get("cnt")
                                    or row.get("count(*)")
                                    or row.get("COUNT(*)")
                                    or (list(row.values())[0] if row else 0)
                                )
                        except Exception as e:
                            logger.warning("[ChartRefresh] count query failed: %s", e)
                    else:
                        logger.warning("[ChartRefresh] count SQL validation failed: %s", msg)

                    sql = f"{sql} LIMIT {int(page_limit)} OFFSET {int(page_offset or 0)}"
                elif "limit" not in sql.lower():
                    sql += " LIMIT 500"

                # Validate SQL safety before execution
                ok, msg = validate_sql(sql)
                if not ok:
                    raise ValueError(f"SQL 校验失败: {msg}")

                logger.info(
                    "[ChartRefresh] chart_id=%s, params=%s, page_limit=%s, sql=%s",
                    chart_id, params, page_limit, sql,
                )

                result = _execute_on_datasource(sql, datasource_id)
                if total is not None:
                    result["total"] = total

                # Update data_cache
                now = _now()
                cache = json.dumps(
                    {"columns": result["columns"], "rows": result["rows"]},
                    ensure_ascii=False,
                )
                cur.execute(
                    "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
                    (cache, now, chart_id),
                )
                conn.commit()

                return result
        finally:
            conn.close()

    def refresh_all_charts(self, dashboard_id: int, params: dict = None) -> dict:
        """Re-execute all charts' SQL in a dashboard on their respective datasources."""
        from services.datamind.nl2sql.sql.query_executor import validate_sql

        params = params or {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, sql_query, config FROM adh_charts "
                    "WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()

                results = {}
                now = _now()

                for chart in charts:
                    cid = chart["id"]
                    sql = chart.get("sql_query", "")
                    if not sql:
                        results[cid] = {"columns": [], "rows": [], "row_count": 0}
                        continue

                    datasource_id = _get_chart_datasource_id(chart)
                    sql = _substitute_params(sql, params)
                    sql = sql.strip().rstrip(";")
                    if "limit" not in sql.lower():
                        sql += " LIMIT 500"

                    ok, msg = validate_sql(sql)
                    if not ok:
                        results[cid] = {"error": f"SQL 校验失败: {msg}"}
                        continue

                    logger.info(
                        "[ChartRefresh] chart_id=%s, params=%s, sql=%s",
                        cid, params, sql,
                    )

                    try:
                        result = _execute_on_datasource(sql, datasource_id)
                        results[cid] = result

                        cache = json.dumps(
                            {"columns": result["columns"], "rows": result["rows"]},
                            ensure_ascii=False,
                        )
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

    def update_layout(self, dashboard_id: int, layouts: list) -> bool:
        """Batch update chart positions (layout save)."""
        if not layouts:
            return True

        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                for item in layouts:
                    chart_id = item.get("chart_id")
                    position = item.get("position", {})
                    if chart_id:
                        cur.execute(
                            "UPDATE adh_charts SET `position`=%s, updated_at=%s "
                            "WHERE id=%s AND dashboard_id=%s",
                            (json.dumps(position), now, chart_id, dashboard_id),
                        )
            conn.commit()
            return True
        finally:
            conn.close()


# ── SnapshotService ──────────────────────────────────────────────────────────


class SnapshotService:
    """Chart snapshot save/list/get operations."""

    def save_snapshot(
        self,
        user_id: int,
        question: str,
        sql_query: str,
        chart_type: str,
        brief: str,
        columns: list,
        rows: list,
        row_count: int,
        datasource_id: int = 0,
    ) -> int:
        """Save a chart snapshot after successful query execution. Returns snapshot ID."""
        sid = _ts_id()
        now = _now()
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO adh_chart_snapshots "
                        "(`id`, `user_id`, `datasource_id`, `question`, `sql_query`, "
                        "`chart_type`, `brief`, `columns`, `data_snapshot`, "
                        "`row_count`, `created_at`) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            sid, user_id, datasource_id, question, sql_query,
                            chart_type, brief,
                            json.dumps(columns),
                            json.dumps(_sanitize_floats(rows[:500]), ensure_ascii=False),
                            row_count, now,
                        ),
                    )
                conn.commit()
                return sid
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Failed to save chart snapshot: %s", e)
            return 0

    def list_snapshots(self, user_id: int, days: int = 7) -> list:
        """Get recent chart snapshots from chat executions."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, question, sql_query, chart_type, brief, "
                    "`columns`, row_count, created_at "
                    "FROM adh_chart_snapshots "
                    "WHERE user_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                    "ORDER BY created_at DESC LIMIT 100",
                    (user_id, days),
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

    def get_snapshot_data(self, snapshot_id: int, user_id: int) -> Optional[dict]:
        """Get full snapshot data including data rows."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_chart_snapshots WHERE id = %s AND user_id = %s",
                    (snapshot_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if hasattr(row.get("created_at"), "isoformat"):
                    row["created_at"] = row["created_at"].isoformat()
                if isinstance(row.get("columns"), str):
                    row["columns"] = json.loads(row["columns"])
                if isinstance(row.get("data_snapshot"), str):
                    row["data_snapshot"] = _sanitize_floats(json.loads(row["data_snapshot"]))
                return row
        finally:
            conn.close()


# ── Preview / Datasource Aggregation ────────────────────────────────────────


def preview_saved_query(user_id: int, source_type: str, source_id: int) -> dict:
    """Execute a saved query/dataset SQL and return preview data."""
    from services.datamind.nl2sql.sql.query_executor import validate_sql

    if not source_id:
        return {"error": "Missing source_id"}

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            sql_query = None
            if source_type in ("query", "dataset"):
                cur.execute(
                    "SELECT sql_query FROM adh_saved_queries WHERE id = %s AND owner_id = %s",
                    (source_id, user_id),
                )
                row = cur.fetchone()
                if row:
                    sql_query = row.get("sql_query")

            if not sql_query:
                return {"error": "Query not found"}

            execute_sql = sql_query.strip().rstrip(";")
            if "limit" not in execute_sql.lower():
                execute_sql += " LIMIT 200"

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


def list_datasource_aggregations(user_id: int) -> list:
    """Aggregate available data sources: snapshots, saved queries, datasets."""
    result = []
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Recent chart snapshots (last 7 days)
            cur.execute(
                "SELECT id, question AS name, chart_type, brief AS description, created_at "
                "FROM adh_chart_snapshots "
                "WHERE user_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
                "ORDER BY created_at DESC LIMIT 50",
                (user_id,),
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
                (user_id,),
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
                (user_id,),
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


# ── Module-level singletons ──────────────────────────────────────────────────

dashboard_service = DashboardService()
chart_service = ChartService()
snapshot_service = SnapshotService()
