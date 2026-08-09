"""Metrics service - CRUD logic for metrics and dimensions."""

import logging
import time
from datetime import datetime
from typing import Optional

from ...shared.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        conditions.append("agg_type = %s")
        params.append(metric_type)
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
                f"SELECT id, name, name_en, agg_type, "
                f"formula, unit, description, category, is_active, "
                f"created_at, updated_at "
                f"FROM adh_metrics {where} "
                f"ORDER BY name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()

    return {"total": total, "items": rows}


def create_metric(data: dict) -> dict:
    """Create a new metric.

    Args:
        data: Metric data dict

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check duplicate metric_name
            cur.execute(
                "SELECT id FROM adh_metrics WHERE metric_name = %s AND workspace_id = %s",
                (data["metric_name"], data.get("workspace_id", 0)),
            )
            if cur.fetchone():
                raise ValueError(f"Metric '{data['metric_name']}' already exists")

            cur.execute(
                "INSERT INTO adh_metrics "
                "(id, metric_name, metric_display_name, metric_type, calculation, "
                "unit, description, tags, data_source, is_active, workspace_id, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data["metric_name"],
                    data.get("metric_display_name", ""),
                    data.get("metric_type", "basic"),
                    data.get("calculation", ""),
                    data.get("unit", ""),
                    data.get("description", ""),
                    data.get("tags", ""),
                    data.get("data_source", ""),
                    data.get("is_active", 1),
                    data.get("workspace_id", 0),
                    now,
                    now,
                ),
            )

    return {"id": row_id, "success": True}


def get_metric(metric_id: int) -> Optional[dict]:
    """Get metric detail with dimensions.

    Args:
        metric_id: Metric ID

    Returns:
        dict with metric info and dimensions, or None
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, metric_name, metric_display_name, metric_type, "
                "calculation, unit, description, tags, data_source, is_active, "
                "workspace_id, created_at, updated_at "
                "FROM adh_metrics WHERE id = %s",
                (metric_id,),
            )
            metric = cur.fetchone()
            if not metric:
                return None

            for k in ("created_at", "updated_at"):
                if hasattr(metric.get(k), "isoformat"):
                    metric[k] = metric[k].isoformat()

            # Get dimensions
            cur.execute(
                "SELECT id, dimension_name, dimension_display_name, data_type, "
                "description, is_active "
                "FROM adh_metric_dimensions "
                "WHERE metric_id = %s "
                "ORDER BY dimension_name",
                (metric_id,),
            )
            metric["dimensions"] = cur.fetchall()

    return metric


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
            for key in ("metric_name", "metric_display_name", "metric_type", "calculation",
                        "unit", "description", "tags", "data_source", "is_active"):
                if key in data:
                    fields.append(f"{key} = %s")
                    params.append(data[key])

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
    """Get dimensions for a metric.

    Args:
        metric_id: Metric ID

    Returns:
        list of dimension dicts
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, dimension_name, dimension_display_name, data_type, "
                "description, is_active "
                "FROM adh_metric_dimensions "
                "WHERE metric_id = %s "
                "ORDER BY dimension_name",
                (metric_id,),
            )
            return cur.fetchall()


def add_dimension(metric_id: int, data: dict) -> dict:
    """Add a dimension to a metric.

    Args:
        metric_id: Metric ID
        data: Dimension data

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Verify metric exists
            cur.execute("SELECT id FROM adh_metrics WHERE id = %s", (metric_id,))
            if not cur.fetchone():
                raise ValueError(f"Metric {metric_id} not found")

            # Check duplicate dimension name
            cur.execute(
                "SELECT id FROM adh_metric_dimensions WHERE metric_id = %s AND dimension_name = %s",
                (metric_id, data["dimension_name"]),
            )
            if cur.fetchone():
                raise ValueError(f"Dimension '{data['dimension_name']}' already exists for this metric")

            cur.execute(
                "INSERT INTO adh_metric_dimensions "
                "(id, metric_id, dimension_name, dimension_display_name, data_type, "
                "description, is_active, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    metric_id,
                    data["dimension_name"],
                    data.get("dimension_display_name", ""),
                    data.get("data_type", "string"),
                    data.get("description", ""),
                    data.get("is_active", 1),
                    now,
                    now,
                ),
            )

    return {"id": row_id, "success": True}
