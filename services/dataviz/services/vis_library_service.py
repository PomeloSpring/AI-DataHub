"""可视化字模库服务(UI 库 / "活字") — 大屏可复用元素的增删改查。

设计:
- 每个字模(adh_vis_components)是一组**纯配置**(style_config / query_template JSON),
  不含数据行, 属元数据级资源; 实际取数仍由大屏/图表经 governed_execute 治理。
- 系统内置字模(is_builtin=1)仅 admin 可编辑/下架(对齐内置组件 admin-only 保护规范);
  自定义字模(source=custom)由创建者/管理员维护, 支持从大屏"存为字模"回存沉淀。
"""

from __future__ import annotations

import json
import logging
import time

from services.shared.common.db import execute_query, execute_write, execute_insert

logger = logging.getLogger(__name__)

# 字模类别枚举(与迁移/前端管理页保持一致)
CATEGORIES = {
    "chart_style", "screen_background", "kpi_card",
    "layout_template", "decoration_frame", "color_theme", "sql_template",
}

_JSON_FIELDS = ("style_config", "query_template")


class BuiltinProtectedError(PermissionError):
    """内置字模需 admin 才能修改/下架。"""


def _ts_id() -> int:
    return int(time.time() * 1000)


def _loads(val, default=None):
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def _normalize(row: dict) -> dict:
    """JSON 文本列反序列化, 便于前端直接消费。"""
    for f in _JSON_FIELDS:
        if f in row:
            row[f] = _loads(row.get(f), {} if f == "style_config" else None)
    return row


class VisLibraryService:
    """字模库 CRUD(元数据级, 不返回数据行)。"""

    def list_components(self, category: str = "", include_inactive: bool = False) -> list:
        sql = "SELECT * FROM adh_vis_components"
        conds, params = [], []
        if category:
            conds.append("category = %s")
            params.append(category)
        if not include_inactive:
            conds.append("is_active = 1")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY sort_order ASC, is_builtin DESC, id ASC"
        rows = execute_query(sql, params) or []
        return [_normalize(r) for r in rows]

    def get_component(self, component_id: int):
        row = execute_query("SELECT * FROM adh_vis_components WHERE id = %s", (component_id,), fetchone=True)
        return _normalize(row) if row else None

    def get_component_by_code(self, code: str):
        row = execute_query("SELECT * FROM adh_vis_components WHERE code = %s", (code,), fetchone=True)
        return _normalize(row) if row else None

    def create_component(self, data: dict, user_id: int, is_admin: bool) -> int:
        category = (data.get("category") or "").strip()
        if category not in CATEGORIES:
            raise ValueError(f"未知字模类别: {category}, 可选: {', '.join(sorted(CATEGORIES))}")
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")

        # 内置/system 来源仅 admin 可声明, 其余一律落 custom
        want_builtin = bool(data.get("is_builtin")) or data.get("source") == "system"
        is_builtin = 1 if (want_builtin and is_admin) else 0
        source = "system" if is_builtin else "custom"

        code = (data.get("code") or "").strip() or f"{category}_{_ts_id()}"
        # code 冲突时补时间戳后缀, 保证唯一
        if self.get_component_by_code(code):
            code = f"{code}_{_ts_id()}"

        cid = execute_insert(
            "INSERT INTO adh_vis_components "
            "(code, name, category, chart_type, style_config, thumbnail, query_template, "
            " description, source, workspace_id, is_active, is_builtin, sort_order, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                code, name, category, data.get("chart_type"),
                json.dumps(data.get("style_config") or {}, ensure_ascii=False),
                data.get("thumbnail"),
                json.dumps(data.get("query_template"), ensure_ascii=False) if data.get("query_template") else None,
                data.get("description", ""), source, int(data.get("workspace_id") or 0),
                1, is_builtin, int(data.get("sort_order") or 0), user_id or 0,
            ),
        )
        logger.info("[vis_library] created component id=%s code=%s builtin=%s by=%s", cid, code, is_builtin, user_id)
        return cid

    def update_component(self, component_id: int, data: dict, user_id: int, is_admin: bool) -> bool:
        existing = self.get_component(component_id)
        if not existing:
            raise ValueError("字模不存在")
        if existing.get("is_builtin") and not is_admin:
            raise BuiltinProtectedError("系统内置字模仅管理员可修改")

        allowed = ("name", "category", "chart_type", "thumbnail", "description", "sort_order", "is_active")
        sets, params = [], []
        for k in allowed:
            if k in data:
                sets.append(f"`{k}` = %s")
                params.append(data[k])
        if "style_config" in data:
            sets.append("style_config = %s")
            params.append(json.dumps(data.get("style_config") or {}, ensure_ascii=False))
        if "query_template" in data:
            sets.append("query_template = %s")
            params.append(json.dumps(data.get("query_template"), ensure_ascii=False) if data.get("query_template") else None)
        if not sets:
            return False

        params.append(component_id)
        affected = execute_write(
            f"UPDATE adh_vis_components SET {', '.join(sets)} WHERE id = %s", params,
        )
        return affected > 0

    def delete_component(self, component_id: int, user_id: int, is_admin: bool) -> bool:
        """软删除(下架); 内置字模仅 admin 可下架。"""
        existing = self.get_component(component_id)
        if not existing:
            raise ValueError("字模不存在")
        if existing.get("is_builtin") and not is_admin:
            raise BuiltinProtectedError("系统内置字模仅管理员可下架")
        affected = execute_write("UPDATE adh_vis_components SET is_active = 0 WHERE id = %s", (component_id,))
        return affected > 0


vis_library_service = VisLibraryService()
