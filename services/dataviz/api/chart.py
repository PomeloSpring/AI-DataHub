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
from services.dataviz.services.governed_query import governed_execute, NoIdentityError

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


def _flatten_params(params: dict) -> dict:
    """把可能嵌套的运行时参数展平为 {a.b: val}(与 _substitute_params 同口径)。"""
    flat: dict = {}
    for k, v in (params or {}).items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                flat[f"{k}.{kk}"] = vv
        else:
            flat[k] = v
    return flat


def _coerce_param(val):
    """占位符值尽量还原为数字, 否则原样。"""
    if isinstance(val, str):
        s = val.strip()
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except ValueError:
            return val
    return val


def _substitute_semantic(node, flat: dict):
    """递归把 SemanticQuery 里的 {{param}} 占位符替换为运行时参数值。"""
    if isinstance(node, dict):
        return {k: _substitute_semantic(v, flat) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute_semantic(v, flat) for v in node]
    if isinstance(node, str):
        m = re.fullmatch(r"\{\{\s*([\w.]+)\s*\}\}", node.strip())
        if m and m.group(1) in flat:
            return _coerce_param(flat[m.group(1)])

        def _r(mm):
            key = mm.group(1).strip()
            return str(flat.get(key, "")) if key in flat else mm.group(0)

        return re.sub(r"\{\{(\w+(?:\.\w+)*)\}\}", _r, node)
    return node


def _execute_via_semantic(
    semantic_query: dict, params: dict, user_id, workspace_id,
    datasource_id: int, limit=None,
) -> dict:
    """Phase 5: 大屏走语义层取数(与 ChatBI run_semantic_query 同源)。

    存/发 SemanticQuery -> POST /api/semantic/query -> SemanticResult。
    将 columns(对象数组)+ rows(二维数组)转成图表缓存惯用的
    (列名列表 + dict 行), 保持与 raw_sql 图表一致的前端契约。
    """
    from services.shared.common.semantic_client import SemanticClient

    body = _substitute_semantic(dict(semantic_query), _flatten_params(params))
    if datasource_id and not body.get("datasource_id"):
        body["datasource_id"] = datasource_id
    if workspace_id and not body.get("workspace_id"):
        body["workspace_id"] = int(workspace_id)
    if user_id is not None:
        try:
            body["user_id"] = int(user_id)
        except (TypeError, ValueError):
            pass
    if limit is not None:
        body["limit"] = int(limit)

    res = SemanticClient().query(body)  # SemanticError 冒泡给调用方转 4xx
    cols = [c.get("name") for c in res.get("columns", [])]
    rows = [dict(zip(cols, r)) for r in res.get("rows", [])]

    prov = res.get("provenance", {}) or {}
    if prov.get("allowed") is False:
        raise Exception(
            f"语义层闸门拦截于 '{prov.get('blocked_at')}': {res.get('warnings') or prov}"
        )

    return {
        "columns": cols,
        "rows": _sanitize_floats(rows),
        "row_count": res.get("row_count", len(rows)),
        "query_source": "semantic",
        "provenance": prov,
        "applied_rls": res.get("applied_rls", []),
    }


def _cache_result(cur, chart_id: int, result: dict) -> None:
    """把取数结果写入 adh_charts.data_cache(语义/裸 SQL 两路共用)。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cache = json.dumps(
        {"columns": result["columns"], "rows": result["rows"]},
        ensure_ascii=False,
    )
    cur.execute(
        "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
        (cache, now, chart_id),
    )


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
                "SELECT id, dashboard_id, workspace_id, sql_query, config, "
                "query_source, semantic_query FROM adh_charts WHERE id = %s",
                (chart_id,),
            )
            chart = cur.fetchone()
            if not chart:
                raise HTTPException(status_code=404, detail="Chart not found")

            datasource_id = _get_chart_datasource_id(chart)
            query_source = chart.get("query_source") or "raw_sql"
            semantic_query = _json_loads_safe(chart.get("semantic_query"))

            # Phase 5: 语义化图表 -> 经 /api/semantic/query 取数(与 ChatBI 同源);
            #   存量 raw_sql 图表保持不变, 逐步迁移。
            if (
                query_source == "semantic"
                and isinstance(semantic_query, dict)
                and semantic_query.get("object")
            ):
                limit = req.page_limit if req.page_limit is not None else None
                try:
                    result = _execute_via_semantic(
                        semantic_query,
                        req.params or {},
                        user.get("user_id"),
                        chart.get("workspace_id"),
                        datasource_id,
                        limit,
                    )
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(status_code=400, detail=f"语义查询失败: {e}")
                _cache_result(cur, chart_id, result)
                return result

            sql = chart.get("sql_query", "")
            if not sql:
                return {"columns": [], "rows": [], "row_count": 0}

            # 身份只信服务端(JWT): raw_sql 取数一律走治理护城河(I1/I2/I3/I5),
            # 无可信身份 -> fail-closed(NoIdentityError -> 403), 绝不裸连数据源。
            user_id = user.get("user_id") or 0
            username = user.get("username", "") or ""
            ws_id = chart.get("workspace_id") or 0

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
                    count_result = governed_execute(count_sql, datasource_id, user_id, ws_id, username)
                    if count_result.get("rows"):
                        row = count_result["rows"][0]
                        total = row.get("cnt") or row.get("count(*)") or list(row.values())[0]
                except NoIdentityError as e:
                    raise HTTPException(status_code=403, detail=str(e))
                except Exception as e:
                    logger.warning("Count query failed: %s", e)

                sql = f"{sql} LIMIT {int(req.page_limit)} OFFSET {int(req.page_offset or 0)}"
            elif "limit" not in sql.lower():
                sql += " LIMIT 500"

            try:
                result = governed_execute(sql, datasource_id, user_id, ws_id, username)
            except NoIdentityError as e:
                raise HTTPException(status_code=403, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"SQL execution failed: {e}")

            if total is not None:
                result["total"] = total

            _cache_result(cur, chart_id, result)
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
                "SELECT id, name, chart_type, sql_query, config, data_cache, "
                "query_source, semantic_query FROM adh_charts WHERE id = %s",
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
                "query_source": chart.get("query_source") or "raw_sql",
            }
            sq = _json_loads_safe(chart.get("semantic_query"))
            if isinstance(sq, dict):
                result["semantic_query"] = sq

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
