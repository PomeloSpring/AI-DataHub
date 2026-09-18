"""Metrics service - CRUD logic for metrics and dimensions."""

import logging
import time
from datetime import datetime
from typing import Optional

from ...shared.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 前端表单字段 ↔ adh_metrics 真实列的映射(页面用 name/display_name/metric_type/…,
# 语义层 mdl_compiler/run_semantic_query 读的是 name(中文名)/name_en/formula/agg_type)
_FRONTEND_TO_DB = {
    "display_name": "name",
    "metric_type": "category",
    "calculation_type": "agg_type",
    "expression": "formula",
    "unit": "unit",
    "description": "description",
    "target_table": "target_table",
    "target_column": "target_column",
    "is_active": "is_active",
    "category": "category",
    "bound_object_key": "bound_object_key",
    "formula_dsl": "formula_dsl",
}

_ALIAS_SELECT = (
    "id, name, name AS display_name, name_en, category AS metric_type, "
    "agg_type AS calculation_type, agg_type, formula AS expression, formula, "
    "unit, target_table, target_column, description, category, "
    "bound_object_key, is_active, workspace_id, created_at, updated_at"
)


def _isoformat(rows: list) -> list:
    for r in rows:
        for k in ("created_at", "updated_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
    return rows


def list_metrics(
    page: int = 1,
    size: int = 20,
    metric_type: Optional[str] = None,
    tags: Optional[str] = None,
    search: str = "",
    workspace_id: int = 0,
) -> dict:
    """List metrics with pagination and filters.

    Args:
        page: Page number (1-based)
        size: Page size
        metric_type: Filter by metric type (e.g., "basic", "derived", "compound")
        tags: Filter by tags (comma-separated)
        search: Search keyword
        workspace_id: Workspace isolation

    Returns:
        dict with total and items
    """
    conditions = []
    params = []

    if workspace_id:
        conditions.append("workspace_id = %s")
        params.append(workspace_id)
    if metric_type:
        conditions.append("(category = %s OR agg_type = %s)")
        params.extend([metric_type, metric_type])
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            conditions.append("FIND_IN_SET(%s, category)")
            params.append(tag)
    if search:
        conditions.append("(name LIKE %s OR name_en LIKE %s OR description LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_metrics {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT {_ALIAS_SELECT} "
                f"FROM adh_metrics {where} "
                f"ORDER BY name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            _isoformat(rows)

    return {"total": total, "items": rows}


def create_metric(data: dict) -> dict:
    """Create a new metric.

    Args:
        data: Metric data dict

    Returns:
        dict with id and success
    """
    now = _now()

    # 前端 name 是英文标识(如 gmv), display_name 是业务名(如 交易总额);
    # 语义层按 name 取指标, 因此 name 存中文业务名, name_en 存标识。
    name_en = (data.get("name") or "").strip()
    biz_name = (data.get("display_name") or data.get("name") or "").strip()
    if not biz_name:
        raise ValueError("metric name is required")
    agg_type = (data.get("calculation_type") or data.get("agg_type") or "count").upper()
    formula = data.get("expression") or data.get("formula") or ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check duplicate (同名中文名或英文标识)
            cur.execute(
                "SELECT id FROM adh_metrics WHERE (name = %s OR name_en = %s) AND workspace_id = %s",
                (biz_name, name_en or biz_name, data.get("workspace_id", 0)),
            )
            if cur.fetchone():
                raise ValueError(f"Metric '{biz_name}' already exists")

            # adh_metrics.id 是 INT, 不能用时间戳主键
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metrics")
            row_id = cur.fetchone()["nid"]
            cur.execute(
                "INSERT INTO adh_metrics "
                "(id, workspace_id, name, name_en, formula, unit, agg_type, "
                "target_table, target_column, description, owner, category, datasource_id, "
                "is_active, created_at, updated_at, default_agg, bound_object_key) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data.get("workspace_id", 0),
                    biz_name,
                    name_en,
                    formula,
                    data.get("unit", ""),
                    agg_type,
                    data.get("target_table", ""),
                    data.get("target_column", ""),
                    data.get("description", ""),
                    data.get("owner", ""),
                    data.get("metric_type") or data.get("category") or "业务",
                    data.get("datasource_id", 0),
                    data.get("is_active", 1),
                    now,
                    now,
                    agg_type,
                    data.get("bound_object_key") or None,
                ),
            )

    return {"id": row_id, "success": True}


def get_metric(metric_id: int) -> Optional[dict]:
    """Get metric detail with dimensions."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ALIAS_SELECT} FROM adh_metrics WHERE id = %s",
                (metric_id,),
            )
            metric = cur.fetchone()
            if not metric:
                return None

            _isoformat([metric])

            # Get dimensions(关系表 JOIN 维度字典)
            metric["dimensions"] = _fetch_metric_dimensions(cur, metric_id)

    return metric


def _fetch_metric_dimensions(cur, metric_id: int) -> list:
    cur.execute(
        "SELECT md.id, md.metric_id, md.relation_type, md.description, "
        "d.id AS dimension_id, d.name, d.name AS display_name, d.name_en, "
        "d.target_table, d.target_column AS column_name, d.category AS data_type "
        "FROM adh_metric_dimensions md "
        "JOIN adh_dimensions d ON md.dimension_id = d.id "
        "WHERE md.metric_id = %s "
        "ORDER BY d.name",
        (metric_id,),
    )
    return cur.fetchall()


def update_metric(metric_id: int, data: dict) -> bool:
    """Update a metric.

    Args:
        metric_id: Metric ID
        data: Fields to update

    Returns:
        True if updated, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                return False

            fields = []
            params = []
            # 前端键 → 真实列映射
            mapped: dict = {}
            for fe_key, db_key in _FRONTEND_TO_DB.items():
                if fe_key in data:
                    mapped[db_key] = data[fe_key]
            if "name" in data and "display_name" not in data and "name" not in mapped:
                # 前端 name 是标识 → 写 name_en, 避免误覆盖中文业务名
                mapped["name_en"] = data["name"]
            if "calculation_type" in data:
                mapped["agg_type"] = (data["calculation_type"] or "").upper()
            if "expression" in data:
                mapped["formula"] = data["expression"]
            for key in ("name", "name_en", "formula", "agg_type", "unit", "description",
                        "category", "target_table", "target_column", "bound_object_key",
                        "formula_dsl", "is_active", "default_agg", "certified", "owner"):
                if key in mapped:
                    fields.append(f"{key} = %s")
                    params.append(mapped[key])

            if not fields:
                return True

            fields.append("updated_at = %s")
            params.append(_now())
            params.append(metric_id)

            cur.execute(f"UPDATE adh_metrics SET {', '.join(fields)} WHERE id = %s", params)

    return True


def delete_metric(metric_id: int) -> bool:
    """Delete a metric and its dimensions.

    Args:
        metric_id: Metric ID

    Returns:
        True if deleted, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_metric_dimensions WHERE metric_id = %s", (metric_id,))
            cur.execute("DELETE FROM adh_metrics WHERE id = %s", (metric_id,))

    return True


def get_dimensions(metric_id: int) -> list:
    """Get dimensions for a metric (relation table JOIN adh_dimensions)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            return _fetch_metric_dimensions(cur, metric_id)


def add_dimension(metric_id: int, data: dict) -> dict:
    """Link a dimension to a metric.

    维度字典在 adh_dimensions, 本表只是 metric↔dimension 关系;
    传入的维度名在字典中不存在时自动创建字典行。
    """
    now = _now()

    dim_name = (data.get("display_name") or data.get("name") or "").strip()
    dim_en = (data.get("name") or "").strip() if data.get("display_name") else ""
    if not dim_name:
        raise ValueError("dimension name is required")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, target_table FROM adh_metrics WHERE id = %s", (metric_id,))
            metric_row = cur.fetchone()
            if not metric_row:
                raise ValueError(f"Metric {metric_id} not found")

            # 在维度字典中查找(中文名或英文标识命中即可)
            cur.execute(
                "SELECT id FROM adh_dimensions WHERE name = %s OR (name_en <> '' AND name_en = %s) "
                "ORDER BY id LIMIT 1",
                (dim_name, dim_en or dim_name),
            )
            hit = cur.fetchone()
            if hit:
                dimension_id = hit["id"]
            else:
                # adh_dimensions.id 是 INT, 不能用时间戳主键
                cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_dimensions")
                dimension_id = cur.fetchone()["nid"]
                cur.execute(
                    "INSERT INTO adh_dimensions "
                    "(id, name, name_en, level, target_table, target_column, description, "
                    "category, datasource_id, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, 0, %s, %s, %s, %s, 0, 1, %s, %s)",
                    (
                        dimension_id,
                        dim_name,
                        dim_en,
                        data.get("target_table") or metric_row["target_table"] or "",
                        data.get("column_name") or data.get("target_column") or "",
                        data.get("description", ""),
                        data.get("category") or "属性",
                        now,
                        now,
                    ),
                )

            # Check duplicate link
            cur.execute(
                "SELECT id FROM adh_metric_dimensions WHERE metric_id = %s AND dimension_id = %s",
                (metric_id, dimension_id),
            )
            if cur.fetchone():
                raise ValueError(f"Dimension '{dim_name}' already linked to this metric")

            # adh_metric_dimensions.id 是 INT
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM adh_metric_dimensions")
            row_id = cur.fetchone()["nid"]
            cur.execute(
                "INSERT INTO adh_metric_dimensions "
                "(id, metric_id, dimension_id, relation_type, description, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    metric_id,
                    dimension_id,
                    data.get("relation_type") or "GROUP_BY",
                    data.get("description", ""),
                    now,
                ),
            )

    return {"id": row_id, "dimension_id": dimension_id, "success": True}
