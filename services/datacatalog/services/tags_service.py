"""Tags service - CRUD and query logic for tags, categories, and tag values."""

import logging
import time
from datetime import datetime
from typing import Optional

from ...shared.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_categories(workspace_id: int = 0) -> list:
    """List tag categories in tree structure.

    Args:
        workspace_id: Workspace isolation

    Returns:
        list of category dicts with children
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            ws_cond = "WHERE workspace_id = %s" if workspace_id else ""
            ws_params = [workspace_id] if workspace_id else []

            cur.execute(
                f"SELECT id, name, description, parent_id, sort_order, is_active "
                f"FROM adh_tag_categories {ws_cond} "
                f"ORDER BY sort_order, name",
                ws_params,
            )
            rows = cur.fetchall()

    # Build tree
    category_map = {}
    roots = []
    for row in rows:
        row["children"] = []
        category_map[row["id"]] = row

    for row in rows:
        parent_id = row.get("parent_id")
        if parent_id and parent_id in category_map:
            category_map[parent_id]["children"].append(row)
        else:
            roots.append(row)

    return roots


def create_category(data: dict) -> dict:
    """Create a tag category.

    Args:
        data: Category data

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_tag_categories "
                "(id, name, description, parent_id, sort_order, is_active, workspace_id, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data["name"],
                    data.get("description", ""),
                    data.get("parent_id"),
                    data.get("sort_order", 0),
                    data.get("is_active", 1),
                    data.get("workspace_id", 0),
                    now,
                    now,
                ),
            )

    return {"id": row_id, "success": True}


def list_tags(
    page: int = 1,
    size: int = 50,
    category_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    search: str = "",
    workspace_id: int = 0,
) -> dict:
    """List tags with pagination and filters.

    Args:
        page: Page number (1-based)
        size: Page size
        category_id: Filter by category
        entity_type: Filter by entity type (table, column, metric)
        search: Search keyword
        workspace_id: Workspace isolation

    Returns:
        dict with total and items
    """
    conditions = []
    params = []

    if workspace_id:
        conditions.append("t.workspace_id = %s")
        params.append(workspace_id)
    if category_id:
        conditions.append("t.category_id = %s")
        params.append(category_id)
    if entity_type:
        conditions.append("t.entity_type = %s")
        params.append(entity_type)
    if search:
        conditions.append("(t.name LIKE %s OR t.description LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM adh_tags t {where}",
                params,
            )
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT t.id, t.name, t.description, t.category_id, t.entity_type, "
                f"t.is_active, t.created_at, t.updated_at, "
                f"c.name AS category_name "
                f"FROM adh_tags t "
                f"LEFT JOIN adh_tag_categories c ON t.category_id = c.id "
                f"{where} "
                f"ORDER BY t.name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()

    return {"total": total, "items": rows}


def create_tag(data: dict) -> dict:
    """Create a tag.

    Args:
        data: Tag data

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check duplicate tag name in same category
            cur.execute(
                "SELECT id FROM adh_tags WHERE name = %s AND category_id = %s AND workspace_id = %s",
                (data["name"], data.get("category_id", 0), data.get("workspace_id", 0)),
            )
            if cur.fetchone():
                raise ValueError(f"Tag '{data['name']}' already exists in this category")

            cur.execute(
                "INSERT INTO adh_tags "
                "(id, name, description, category_id, entity_type, "
                "is_active, workspace_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    data["name"],
                    data.get("description", ""),
                    data.get("category_id"),
                    data.get("entity_type", ""),
                    
                    data.get("is_active", 1),
                    data.get("workspace_id", 0),
                    now,
                    now,
                ),
            )

    return {"id": row_id, "success": True}


def update_tag(tag_id: int, data: dict) -> bool:
    """Update a tag.

    Args:
        tag_id: Tag ID
        data: Fields to update

    Returns:
        True if updated, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_tags WHERE id = %s", (tag_id,))
            if not cur.fetchone():
                return False

            fields = []
            params = []
            for key in ("name", "description", "category_id", "entity_type", "is_active"):
                if key in data:
                    fields.append(f"{key} = %s")
                    params.append(data[key])

            if not fields:
                return True

            fields.append("updated_at = %s")
            params.append(_now())
            params.append(tag_id)

            cur.execute(f"UPDATE adh_tags SET {', '.join(fields)} WHERE id = %s", params)

    return True


def delete_tag(tag_id: int) -> bool:
    """Delete a tag and its values.

    Args:
        tag_id: Tag ID

    Returns:
        True if deleted, False if not found
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_tags WHERE id = %s", (tag_id,))
            if not cur.fetchone():
                return False

            cur.execute("DELETE FROM adh_tag_values WHERE tag_id = %s", (tag_id,))
            cur.execute("DELETE FROM adh_tags WHERE id = %s", (tag_id,))

    return True


def get_tag_values(tag_id: int, entity_type: Optional[str] = None) -> list:
    """Get tag values (entities tagged with this tag).

    Args:
        tag_id: Tag ID
        entity_type: Optional filter by entity type

    Returns:
        list of tag value dicts
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            conditions = ["tag_id = %s"]
            params = [tag_id]

            if entity_type:
                conditions.append("entity_type = %s")
                params.append(entity_type)

            where = f"WHERE {' AND '.join(conditions)}"

            cur.execute(
                f"SELECT id, entity_type, entity_id, entity_name, created_at "
                f"FROM adh_tag_values {where} "
                f"ORDER BY created_at DESC",
                params,
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()

    return rows


def set_tag_value(tag_id: int, data: dict) -> dict:
    """Set tag value for an entity.

    Args:
        tag_id: Tag ID
        data: Tag value data (entity_type, entity_id, entity_name)

    Returns:
        dict with id and success
    """
    now = _now()
    row_id = int(time.time() * 1000000)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Check if tag exists
            cur.execute("SELECT id FROM adh_tags WHERE id = %s", (tag_id,))
            if not cur.fetchone():
                raise ValueError(f"Tag {tag_id} not found")

            # Check duplicate
            cur.execute(
                "SELECT id FROM adh_tag_values "
                "WHERE tag_id = %s AND entity_type = %s AND entity_id = %s",
                (tag_id, data["entity_type"], data["entity_id"]),
            )
            if cur.fetchone():
                return {"success": True, "message": "Tag already applied to this entity"}

            cur.execute(
                "INSERT INTO adh_tag_values "
                "(id, tag_id, entity_type, entity_id, entity_name, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    tag_id,
                    data["entity_type"],
                    data["entity_id"],
                    data.get("entity_name", ""),
                    now,
                ),
            )

    return {"id": row_id, "success": True}


def query_entities_by_tags(conditions: list, operator: str = "AND", workspace_id: int = 0) -> list:
    """Query entities by tag conditions (intersection/union).

    Args:
        conditions: list of dicts with tag_id and optionally tag_name, value
        operator: "AND" for intersection, "OR" for union
        workspace_id: Workspace isolation

    Returns:
        list of entity dicts with matched tag info
    """
    if not conditions:
        return []

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Build query based on operator
            if operator.upper() == "AND":
                # Intersection: entities must have ALL specified tags
                tag_ids = [c["tag_id"] for c in conditions]
                placeholders = ", ".join(["%s"] * len(tag_ids))

                cur.execute(
                    f"SELECT tv.entity_type, tv.entity_id, tv.entity_name, "
                    f"COUNT(DISTINCT tv.tag_id) AS match_count, "
                    f"GROUP_CONCAT(DISTINCT t.name) AS matched_tags "
                    f"FROM adh_tag_values tv "
                    f"JOIN adh_tags t ON tv.tag_id = t.id "
                    f"WHERE tv.tag_id IN ({placeholders}) "
                    f"GROUP BY tv.entity_type, tv.entity_id, tv.entity_name "
                    f"HAVING match_count = %s "
                    f"ORDER BY tv.entity_type, tv.entity_name",
                    tag_ids + [len(tag_ids)],
                )
            else:
                # Union: entities with ANY specified tag
                tag_ids = [c["tag_id"] for c in conditions]
                placeholders = ", ".join(["%s"] * len(tag_ids))

                cur.execute(
                    f"SELECT tv.entity_type, tv.entity_id, tv.entity_name, "
                    f"COUNT(DISTINCT tv.tag_id) AS match_count, "
                    f"GROUP_CONCAT(DISTINCT t.name) AS matched_tags "
                    f"FROM adh_tag_values tv "
                    f"JOIN adh_tags t ON tv.tag_id = t.id "
                    f"WHERE tv.tag_id IN ({placeholders}) "
                    f"GROUP BY tv.entity_type, tv.entity_id, tv.entity_name "
                    f"ORDER BY match_count DESC, tv.entity_type, tv.entity_name",
                    tag_ids,
                )

            rows = cur.fetchall()

    return rows
