"""Chart API — Chart data refresh and cached data retrieval.

Supports executing chart SQL against configured datasources
and caching results in adh_charts.data_cache.
"""

import json
import logging
import math
import re
import time
from decimal import Decimal
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.shared.common.auth import get_current_user
from services.shared.common.db import DBConnection

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None for JSON compliance."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _json_loads_safe(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _get_chart_datasource_id(chart: dict) -> int:
    """Extract datasource_id from chart config."""
    config = chart.get("config")
    config = _json_loads_safe(config)
    if isinstance(config, dict):
        return config.get("datasource_id", 0)
    return 0


def _substitute_params(sql: str, params: dict) -> str:
    """Replace {{param_name}} placeholders in SQL with parameter values."""
    if not params or not sql:
        return sql

    # Flatten nested params
    flat = {}
    for k, v in params.items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                flat[f"{k}.{kk}"] = vv
        else:
            flat[k] = v

    def replacer(m):
        key = m.group(1).strip()
        val = flat.get(key, "")
        if val is None:
            val = ""
        val_str = str(val)
        # Numeric values pass through directly
        try:
            float(val_str)
            return val_str
        except ValueError:
            pass
        # String values: basic escaping
        val_str = val_str.replace("\\", "\\\\").replace("'", "\\'")
        return val_str

    return re.sub(r'\{\{(\w+(?:\.\w+)*)\}\}', replacer, sql)


def _execute_on_datasource(sql: str, datasource_id: int) -> dict:
    """Execute SQL on the specified datasource."""
    # Import here to avoid circular imports at module level
    try:
        from services.shared.common.db import execute_query
        # For the shared DB (metadata), we can execute directly
        rows = execute_query(sql)
        columns = list(rows[0].keys()) if rows else []
        data = _sanitize_floats(rows)
        return {"columns": columns, "rows": data, "row_count": len(data)}
    except Exception as e:
        raise Exception(f"SQL execution failed: {e}")


# ── Pydantic Models ─────────────────────────────────────────────────────────


class ChartRefreshRequest(BaseModel):
    params: Optional[dict] = {}
    page_limit: Optional[int] = None
    page_offset: Optional[int] = None
    count_sql: Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/{chart_id}/refresh")
def refresh_chart(
    chart_id: int,
    req: ChartRefreshRequest = ChartRefreshRequest(),
    user: dict = Depends(get_current_user),
):
    """Refresh chart data — execute SQL, update cache.

    Supports parameter substitution, server-side pagination,
    and executes against the chart's configured datasource.
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, dashboard_id, sql_query, config FROM adh_charts WHERE id = %s",
                (chart_id,),
            )
            chart = cur.fetchone()
            if not chart:
                raise HTTPException(status_code=404, detail="Chart not found")

            sql = chart.get("sql_query", "")
            if not sql:
                return {"columns": [], "rows": [], "row_count": 0}

            datasource_id = _get_chart_datasource_id(chart)

            # Substitute parameters
            sql = _substitute_params(sql, req.params or {})
            sql = sql.strip().rstrip(";")

            total = None

            if req.page_limit is not None:
                # Count query
                count_sql = req.count_sql
                if count_sql:
                    count_sql = _substitute_params(count_sql, req.params or {}).strip().rstrip(";")
                else:
                    base_sql = re.sub(r'\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$', '', sql, flags=re.IGNORECASE).strip()
                    count_sql = f"SELECT COUNT(*) AS cnt FROM ({base_sql}) _t"

                try:
                    count_result = _execute_on_datasource(count_sql, datasource_id)
                    if count_result.get("rows"):
                        row = count_result["rows"][0]
                        total = row.get("cnt") or row.get("count(*)") or list(row.values())[0]
                except Exception as e:
                    logger.warning("Count query failed: %s", e)

                sql = f"{sql} LIMIT {int(req.page_limit)} OFFSET {int(req.page_offset or 0)}"
            elif "limit" not in sql.lower():
                sql += " LIMIT 500"

            try:
                result = _execute_on_datasource(sql, datasource_id)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"SQL execution failed: {e}")

            if total is not None:
                result["total"] = total

            # Update cache
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            cache = json.dumps(
                {"columns": result["columns"], "rows": result["rows"]},
                ensure_ascii=False,
            )
            cur.execute(
                "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
                (cache, now, chart_id),
            )

            return result


@router.get("/{chart_id}/data")
def get_chart_data(
    chart_id: int,
    user: dict = Depends(get_current_user),
):
    """Get cached chart data without re-executing SQL."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, chart_type, sql_query, config, data_cache "
                "FROM adh_charts WHERE id = %s",
                (chart_id,),
            )
            chart = cur.fetchone()
            if not chart:
                raise HTTPException(status_code=404, detail="Chart not found")

            result = {
                "id": chart["id"],
                "name": chart.get("name"),
                "chart_type": chart.get("chart_type"),
                "config": _json_loads_safe(chart.get("config")),
            }

            data_cache = chart.get("data_cache")
            if data_cache:
                cache = _json_loads_safe(data_cache)
                result["columns"] = cache.get("columns", [])
                result["rows"] = _sanitize_floats(cache.get("rows", []))
                result["row_count"] = len(result["rows"])
            else:
                result["columns"] = []
                result["rows"] = []
                result["row_count"] = 0

            return result
