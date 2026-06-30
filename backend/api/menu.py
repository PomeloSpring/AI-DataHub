"""Menu Tree API -- CRUD for hierarchical menu items stored in Doris."""

import time
from datetime import datetime

import pymysql
from fastapi import APIRouter, Depends, HTTPException

from backend.api.auth import require_admin
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn
from backend.common.ttl_cache import menu_cache
from backend.models.schemas import UserInfo, MenuItemCreate, MenuItemUpdate

router = APIRouter()


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id():
    return int(time.time() * 1000)


def _fetch_menu_tree_from_db(workspace_id: int = 0) -> list[dict]:
    """Fetch all menu items and build tree structure from database."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id:
                # Only fetch items belonging to this workspace (strict isolation)
                cur.execute(
                    "SELECT id, parent_id, name, icon, page_id, link_type, is_system, sort_order, workspace_id, created_at, updated_at "
                    "FROM adh_menu_items WHERE workspace_id = %s ORDER BY sort_order, id",
                    (workspace_id,)
                )
            else:
                # System-level menu (no workspace filter) — returns all items
                cur.execute(
                    "SELECT id, parent_id, name, icon, page_id, link_type, is_system, sort_order, workspace_id, created_at, updated_at "
                    "FROM adh_menu_items ORDER BY sort_order, id"
                )
            items = cur.fetchall()
    finally:
        conn.close()

    # Convert datetime to ISO string
    for item in items:
        for key in ("created_at", "updated_at"):
            if item.get(key) and hasattr(item[key], "isoformat"):
                item[key] = item[key].isoformat()
            elif item.get(key):
                item[key] = str(item[key])

    # Build tree
    by_id = {item["id"]: {**item, "children": []} for item in items}
    roots = []
    for item in items:
        node = by_id[item["id"]]
        parent_id = item.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    return roots


def _fetch_menu_tree(workspace_id: int = 0) -> list[dict]:
    """Fetch menu tree with caching (1 minute TTL)."""
    cache_key = f"menu_tree_{workspace_id}"
    return menu_cache.get_or_set(cache_key, lambda: _fetch_menu_tree_from_db(workspace_id))


def _invalidate_menu_cache():
    """Clear all menu tree caches."""
    menu_cache.invalidate("menu_tree_0")
    # Also clear legacy key if present
    menu_cache.invalidate("menu_tree")


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/menu-tree")
def get_menu_tree(workspace_id: int = 0):
    """Get full menu tree -- public, no auth required. Pass workspace_id to filter."""
    return _fetch_menu_tree(workspace_id)


@router.post("/menu-tree")
def create_menu_item(req: MenuItemCreate, admin: UserInfo = Depends(require_admin)):
    """Create a new menu item."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Get max sort_order among siblings
            workspace_id = getattr(req, 'workspace_id', 0) or 0
            if req.parent_id:
                cur.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM adh_menu_items WHERE parent_id = %s",
                    (req.parent_id,)
                )
            else:
                if workspace_id:
                    cur.execute(
                        "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM adh_menu_items WHERE parent_id IS NULL AND workspace_id = %s",
                        (workspace_id,)
                    )
                else:
                    cur.execute(
                        "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM adh_menu_items WHERE parent_id IS NULL"
                    )
            max_sort = cur.fetchone()["max_sort"]

            new_id = _ts_id()
            now = _now()
            cur.execute(
                "INSERT INTO adh_menu_items (id, name, icon, parent_id, page_id, link_type, is_system, sort_order, workspace_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id, req.name, req.icon, req.parent_id, req.page_id,
                 req.link_type, 1 if req.is_system else 0, max_sort + 1, workspace_id, now, now)
            )
            conn.commit()
    finally:
        conn.close()

    _invalidate_menu_cache()
    return {"success": True, "id": new_id}


@router.put("/menu-tree/{item_id}")
def update_menu_item(item_id: int, req: MenuItemUpdate, admin: UserInfo = Depends(require_admin)):
    """Update a menu item."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check exists
            cur.execute("SELECT id, is_system FROM adh_menu_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="菜单项不存在")

            # Build update fields
            updates = []
            params = []
            if req.name is not None:
                updates.append("name = %s")
                params.append(req.name)
            if req.icon is not None:
                updates.append("icon = %s")
                params.append(req.icon)
            if req.page_id is not None:
                updates.append("page_id = %s")
                params.append(req.page_id)
            if req.link_type is not None:
                updates.append("link_type = %s")
                params.append(req.link_type)
            if req.sort_order is not None:
                updates.append("sort_order = %s")
                params.append(req.sort_order)
            if req.parent_id is not None:
                if req.parent_id == item_id:
                    raise HTTPException(status_code=400, detail="不能将菜单项设为自己的子项")
                updates.append("parent_id = %s")
                params.append(req.parent_id)

            if updates:
                updates.append("updated_at = %s")
                params.append(_now())
                params.append(item_id)
                cur.execute(
                    f"UPDATE adh_menu_items SET {', '.join(updates)} WHERE id = %s",
                    params
                )
                conn.commit()
    finally:
        conn.close()

    _invalidate_menu_cache()
    return {"success": True}


@router.delete("/menu-tree/{item_id}")
def delete_menu_item(item_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a menu item and its children."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, is_system FROM adh_menu_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="菜单项不存在")
            if row["is_system"]:
                raise HTTPException(status_code=400, detail="系统菜单不可删除")

            # Recursively collect all descendant IDs
            to_delete = [item_id]
            queue = [item_id]
            while queue:
                parent = queue.pop(0)
                cur.execute("SELECT id FROM adh_menu_items WHERE parent_id = %s", (parent,))
                children = [r["id"] for r in cur.fetchall()]
                to_delete.extend(children)
                queue.extend(children)

            # Delete all at once
            placeholders = ",".join(["%s"] * len(to_delete))
            cur.execute(f"DELETE FROM adh_menu_items WHERE id IN ({placeholders})", to_delete)
            conn.commit()
    finally:
        conn.close()

    _invalidate_menu_cache()
    return {"success": True}


@router.put("/menu-tree/{item_id}/move")
def move_menu_item(item_id: int, direction: str, admin: UserInfo = Depends(require_admin)):
    """Move menu item up or down in sort order."""
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, parent_id, sort_order FROM adh_menu_items WHERE id = %s",
                (item_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="菜单项不存在")

            parent_id = row["parent_id"]
            sort_order = row["sort_order"]

            if parent_id:
                parent_condition = "parent_id = %s"
                parent_params = [parent_id]
            else:
                parent_condition = "parent_id IS NULL"
                parent_params = []

            if direction == "up":
                cur.execute(
                    f"SELECT id, sort_order FROM adh_menu_items "
                    f"WHERE {parent_condition} AND sort_order < %s ORDER BY sort_order DESC LIMIT 1",
                    parent_params + [sort_order]
                )
            else:
                cur.execute(
                    f"SELECT id, sort_order FROM adh_menu_items "
                    f"WHERE {parent_condition} AND sort_order > %s ORDER BY sort_order ASC LIMIT 1",
                    parent_params + [sort_order]
                )

            neighbor = cur.fetchone()
            if not neighbor:
                return {"success": True, "message": "已在边界位置"}

            neighbor_id = neighbor["id"]
            neighbor_sort = neighbor["sort_order"]
            cur.execute("UPDATE adh_menu_items SET sort_order = %s WHERE id = %s", (neighbor_sort, item_id))
            cur.execute("UPDATE adh_menu_items SET sort_order = %s WHERE id = %s", (sort_order, neighbor_id))
            conn.commit()
    finally:
        conn.close()

    _invalidate_menu_cache()
    return {"success": True}
