"""Unified Component Data Endpoint — single endpoint for all low-code component data fetching."""
import logging
import math
import re

from fastapi import APIRouter, HTTPException

from backend.models.schemas import ComponentDataRequest, ComponentDataResponse
from backend.nl2sql.sql.query_executor import validate_sql
from backend.api.datasource import get_datasource_by_id, get_datasource_conn

logger = logging.getLogger(__name__)
router = APIRouter()


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
        raise HTTPException(status_code=400, detail=f"参数值包含非法字符: {val[:50]}")
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
        # If value looks numeric (int or float), pass through directly
        try:
            float(val_str)
            return val_str
        except ValueError:
            pass
        # String value -- escape for SQL safety
        return _sanitize_param_value(val_str)

    return re.sub(r'\$\{(\w+)\}', replacer, sql)


def _execute_on_datasource(sql: str, datasource_id: int) -> dict:
    """Execute SQL on the specified datasource and return result dict."""
    ds = get_datasource_by_id(datasource_id)
    if not ds:
        raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")

    db_type = ds.get("db_type", "mysql")

    if db_type == "elasticsearch":
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


@router.post("/component-data", response_model=ComponentDataResponse)
def get_component_data(req: ComponentDataRequest):
    """Unified endpoint for all component data fetching.

    Supports:
    - Parameter substitution via ${param} placeholders
    - Pagination (page/size) for table components
    - Sorting (sort_by/sort_order)
    - Safe SQL validation before execution
    """
    sql = req.sql.strip().rstrip(";")
    if not sql:
        return ComponentDataResponse(success=False, error="SQL 不能为空")

    # Substitute parameters
    sql = _substitute_params(sql, req.params)

    total = None
    options = req.options

    # For table components with pagination
    if req.component_type == "table" and options and options.page is not None and options.size is not None:
        # Run COUNT query first to get total
        base_sql = re.sub(r'\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$', '', sql, flags=re.IGNORECASE).strip()
        count_sql = f"SELECT COUNT(*) AS cnt FROM ({base_sql}) _t"
        ok, msg = validate_sql(count_sql, require_limit=False)
        if ok:
            try:
                count_result = _execute_on_datasource(count_sql, req.datasource_id)
                if count_result["rows"]:
                    row = count_result["rows"][0]
                    total = row.get("cnt") or row.get("count(*)") or row.get("COUNT(*)") or list(row.values())[0] if row else 0
            except Exception as e:
                logger.warning("[ComponentData] count query failed: %s", e)

        # Apply sorting if specified
        if options.sort_by:
            direction = "ASC"
            if options.sort_order and options.sort_order.upper() in ("ASC", "DESC"):
                direction = options.sort_order.upper()
            sql = f"SELECT * FROM ({sql}) _t ORDER BY {options.sort_by} {direction}"

        # Apply pagination LIMIT/OFFSET
        page = max(options.page, 1)
        size = max(options.size, 1)
        offset = (page - 1) * size
        sql = f"{sql} LIMIT {size} OFFSET {offset}"
    else:
        # Non-table components or no pagination: add default LIMIT for safety
        if "limit" not in sql.lower():
            sql += " LIMIT 500"

    # Validate SQL safety before execution
    ok, msg = validate_sql(sql)
    if not ok:
        return ComponentDataResponse(success=False, error=f"SQL 校验失败: {msg}")

    try:
        result = _execute_on_datasource(sql, req.datasource_id)
        return ComponentDataResponse(
            success=True,
            data=result.get("rows", []),
            total=total,
            columns=result.get("columns", []),
        )
    except Exception as e:
        logger.error("[ComponentData] execution failed: %s", e)
        return ComponentDataResponse(success=False, error=str(e))
