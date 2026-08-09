"""菜单服务 — 管理层级菜单项的业务逻辑。

从 backend/api/menu.py 迁移而来。
提供菜单树的 CRUD、排序、移动等功能。
"""

import logging
import time
from datetime import datetime
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.ttl_cache import menu_cache

logger = logging.getLogger(__name__)


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _now() -> str:
    """返回当前时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    """生成基于时间戳的 ID。"""
    return int(time.time() * 1000)


# ── 菜单服务 ──────────────────────────────────────────────────────────

class MenuService:
    """菜单管理服务。"""

    def get_menu_tree(self, workspace_id: int = 0) -> list[dict]:
        """获取完整菜单树（带缓存）。

        Args:
            workspace_id: 工作空间 ID，0 表示系统级菜单。

        Returns:
            菜单树结构列表。
        """
        cache_key = f"menu_tree_{workspace_id}"
        return menu_cache.get_or_set(
            cache_key,
            lambda: self._fetch_menu_tree_from_db(workspace_id),
        )

    def create_menu_item(self, data: dict) -> dict:
        """创建菜单项。

        Args:
            data: 菜单项信息，包含 name, icon, parent_id, page_id, link_type, is_system, workspace_id。

        Returns:
            包含 success 和 id 的字典。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                workspace_id = data.get("workspace_id", 0) or 0
                parent_id = data.get("parent_id")

                # 获取同级最大排序号
                if parent_id:
                    cur.execute(
                        "SELECT COALESCE(MAX(sort_order), 0) AS max_sort "
                        "FROM adh_menu_items WHERE parent_id = %s",
                        (parent_id,),
                    )
                else:
                    if workspace_id:
                        cur.execute(
                            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort "
                            "FROM adh_menu_items WHERE parent_id IS NULL AND workspace_id = %s",
                            (workspace_id,),
                        )
                    else:
                        cur.execute(
                            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort "
                            "FROM adh_menu_items WHERE parent_id IS NULL"
                        )
                max_sort = cur.fetchone()["max_sort"]

                new_id = _ts_id()
                now = _now()
                cur.execute(
                    "INSERT INTO adh_menu_items "
                    "(id, name, icon, parent_id, page_id, link_type, is_system, "
                    "sort_order, workspace_id, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        new_id, data["name"], data.get("icon", ""),
                        parent_id, data.get("page_id"),
                        data.get("link_type", "page"),
                        1 if data.get("is_system") else 0,
                        max_sort + 1, workspace_id, now, now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        self._invalidate_menu_cache()
        return {"success": True, "id": new_id}

    def update_menu_item(self, item_id: int, data: dict) -> dict:
        """更新菜单项。

        Args:
            item_id: 菜单项 ID。
            data: 要更新的字段字典。

        Returns:
            操作结果字典。

        Raises:
            ValueError: 菜单项不存在或参数无效时抛出。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查是否存在
                cur.execute(
                    "SELECT id, is_system FROM adh_menu_items WHERE id = %s",
                    (item_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("菜单项不存在")

                # 构建更新字段
                updates = []
                params = []

                if data.get("name") is not None:
                    updates.append("name = %s")
                    params.append(data["name"])
                if data.get("icon") is not None:
                    updates.append("icon = %s")
                    params.append(data["icon"])
                if data.get("page_id") is not None:
                    updates.append("page_id = %s")
                    params.append(data["page_id"])
                if data.get("link_type") is not None:
                    updates.append("link_type = %s")
                    params.append(data["link_type"])
                if data.get("sort_order") is not None:
                    updates.append("sort_order = %s")
                    params.append(data["sort_order"])
                if data.get("parent_id") is not None:
                    if data["parent_id"] == item_id:
                        raise ValueError("不能将菜单项设为自己的子项")
                    updates.append("parent_id = %s")
                    params.append(data["parent_id"])

                if updates:
                    updates.append("updated_at = %s")
                    params.append(_now())
                    params.append(item_id)
                    cur.execute(
                        f"UPDATE adh_menu_items SET {', '.join(updates)} WHERE id = %s",
                        params,
                    )
                    conn.commit()
        finally:
            conn.close()

        self._invalidate_menu_cache()
        return {"success": True}

    def delete_menu_item(self, item_id: int) -> dict:
        """删除菜单项及其子项。

        Args:
            item_id: 菜单项 ID。

        Returns:
            操作结果字典。

        Raises:
            ValueError: 菜单项不存在或为系统菜单时抛出。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, is_system FROM adh_menu_items WHERE id = %s",
                    (item_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("菜单项不存在")
                if row["is_system"]:
                    raise PermissionError("系统菜单不可删除")

                # 递归收集所有后代 ID
                to_delete = [item_id]
                queue = [item_id]
                while queue:
                    parent = queue.pop(0)
                    cur.execute(
                        "SELECT id FROM adh_menu_items WHERE parent_id = %s",
                        (parent,),
                    )
                    children = [r["id"] for r in cur.fetchall()]
                    to_delete.extend(children)
                    queue.extend(children)

                # 批量删除
                placeholders = ",".join(["%s"] * len(to_delete))
                cur.execute(
                    f"DELETE FROM adh_menu_items WHERE id IN ({placeholders})",
                    to_delete,
                )
                conn.commit()
        finally:
            conn.close()

        self._invalidate_menu_cache()
        return {"success": True}

    def move_menu_item(self, item_id: int, direction: str) -> dict:
        """移动菜单项的排序位置。

        Args:
            item_id: 菜单项 ID。
            direction: 移动方向 ("up" 或 "down")。

        Returns:
            操作结果字典。

        Raises:
            ValueError: 方向无效或菜单项不存在时抛出。
        """
        if direction not in ("up", "down"):
            raise ValueError("direction 必须为 'up' 或 'down'")

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, parent_id, sort_order FROM adh_menu_items WHERE id = %s",
                    (item_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("菜单项不存在")

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
                        f"WHERE {parent_condition} AND sort_order < %s "
                        f"ORDER BY sort_order DESC LIMIT 1",
                        parent_params + [sort_order],
                    )
                else:
                    cur.execute(
                        f"SELECT id, sort_order FROM adh_menu_items "
                        f"WHERE {parent_condition} AND sort_order > %s "
                        f"ORDER BY sort_order ASC LIMIT 1",
                        parent_params + [sort_order],
                    )

                neighbor = cur.fetchone()
                if not neighbor:
                    return {"success": True, "message": "已在边界位置"}

                neighbor_id = neighbor["id"]
                neighbor_sort = neighbor["sort_order"]

                cur.execute(
                    "UPDATE adh_menu_items SET sort_order = %s WHERE id = %s",
                    (neighbor_sort, item_id),
                )
                cur.execute(
                    "UPDATE adh_menu_items SET sort_order = %s WHERE id = %s",
                    (sort_order, neighbor_id),
                )
                conn.commit()
        finally:
            conn.close()

        self._invalidate_menu_cache()
        return {"success": True}

    # ── 内部方法 ──────────────────────────────────────────────────────

    def _fetch_menu_tree_from_db(self, workspace_id: int = 0) -> list[dict]:
        """从数据库获取菜单项并构建树结构。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id:
                    cur.execute(
                        "SELECT id, parent_id, name, icon, page_id, link_type, "
                        "is_system, sort_order, workspace_id, created_at, updated_at "
                        "FROM adh_menu_items WHERE workspace_id = %s "
                        "ORDER BY sort_order, id",
                        (workspace_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, parent_id, name, icon, page_id, link_type, "
                        "is_system, sort_order, workspace_id, created_at, updated_at "
                        "FROM adh_menu_items ORDER BY sort_order, id"
                    )
                items = cur.fetchall()
        finally:
            conn.close()

        # 转换 datetime 为 ISO 字符串
        for item in items:
            for key in ("created_at", "updated_at"):
                if item.get(key) and hasattr(item[key], "isoformat"):
                    item[key] = item[key].isoformat()
                elif item.get(key):
                    item[key] = str(item[key])

        # 构建树
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

    def _invalidate_menu_cache(self):
        """清除所有菜单树缓存。"""
        menu_cache.invalidate("menu_tree_0")
        menu_cache.invalidate("menu_tree")


# ── 模块级单例 ────────────────────────────────────────────────────────

menu_service = MenuService()
