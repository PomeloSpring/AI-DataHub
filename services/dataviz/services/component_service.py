"""Component Data Service -- Unified endpoint for low-code component data fetching.

Migrated from backend/api/component_data.py.
Supports parameter substitution, pagination, sorting, and safe SQL validation.
"""

import logging
import math
import re

from services.shared.common.db.datasource_db import get_datasource_by_id, get_datasource_conn

logger = logging.getLogger(__name__)


# ── Utility Functions ────────────────────────────────────────────────────────


def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None and datetime with ISO string for JSON compliance."""
    import datetime as _dt
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
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


def _substitute_params(sql: str, params: dict) -> str:
    """Replace ${param_name} placeholders in SQL with parameter values.

    String values are escaped to prevent SQL injection.
    Numeric values are passed through directly.
    """
    if params is None or not sql:
        return sql

    def replacer(m):
        key = m.group(1).strip()
        val = params.get(key, "")
        if val is None or val == "":
            return ""
        val_str = str(val)
        try:
            float(val_str)
            return val_str
        except ValueError:
            pass
        return _sanitize_param_value(val_str)

    return re.sub(r'\$\{(\w+)\}', replacer, sql)


def _execute_on_datasource(sql: str, datasource_id: int) -> dict:
    """Execute SQL on the specified datasource and return result dict."""
    import pymysql as _pymysql

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
            "cursorclass": _pymysql.cursors.DictCursor,
            "connect_timeout": 10,
            "read_timeout": 30,
        }
        conn = _pymysql.connect(**conn_kwargs)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                columns = list(rows[0].keys()) if rows else []
                data = _sanitize_floats(rows)
                return {"columns": columns, "rows": data, "row_count": len(data)}
        finally:
            conn.close()


# ── Component Data Service ───────────────────────────────────────────────────


class ComponentService:
    """Unified service for low-code component data fetching."""

    def get_component_data(
        self,
        datasource_id: int,
        sql: str,
        params: dict = None,
        component_type: str = "table",
        options: dict = None,
    ) -> dict:
        """Execute component SQL with parameter substitution, pagination, and sorting.

        Args:
            datasource_id: Target datasource ID.
            sql: SQL query with ${param} placeholders.
            params: Parameter values for substitution.
            component_type: Component type (e.g., "table").
            options: Pagination/sorting options (page, size, sort_by, sort_order).

        Returns:
            dict with success, data, total, columns, error fields.
        """
        from services.datamind.nl2sql.sql.query_executor import validate_sql

        params = params or {}
        sql = sql.strip().rstrip(";")
        if not sql:
            return {"success": False, "error": "SQL 不能为空"}

        # Substitute parameters
        sql = _substitute_params(sql, params)

        total = None
        page = None
        size = None

        # For table components with pagination
        if component_type == "table" and options:
            page = options.get("page")
            size = options.get("size")
            sort_by = options.get("sort_by")
            sort_order = options.get("sort_order")

            if page is not None and size is not None:
                # Count query first
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
                        logger.warning("[ComponentData] count query failed: %s", e)

                # Apply sorting
                if sort_by:
                    direction = "ASC"
                    if sort_order and sort_order.upper() in ("ASC", "DESC"):
                        direction = sort_order.upper()
                    sql = f"SELECT * FROM ({sql}) _t ORDER BY {sort_by} {direction}"

                # Apply pagination
                page = max(page, 1)
                size = max(size, 1)
                offset = (page - 1) * size
                sql = f"{sql} LIMIT {size} OFFSET {offset}"
            else:
                if "limit" not in sql.lower():
                    sql += " LIMIT 500"
        else:
            if "limit" not in sql.lower():
                sql += " LIMIT 500"

        # Validate SQL safety
        ok, msg = validate_sql(sql)
        if not ok:
            return {"success": False, "error": f"SQL 校验失败: {msg}"}

        try:
            result = _execute_on_datasource(sql, datasource_id)
            return {
                "success": True,
                "data": result.get("rows", []),
                "total": total,
                "columns": result.get("columns", []),
            }
        except Exception as e:
            logger.error("[ComponentData] execution failed: %s", e)
            return {"success": False, "error": str(e)}


# ── Module-level singleton ───────────────────────────────────────────────────

component_service = ComponentService()
