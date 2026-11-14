"""可视化字模库服务(UI 库 / "活字") — 大屏可复用元素的增删改查。

设计:
- 每个字模(adh_vis_components)是一组**纯视觉配置**(style_config JSON),
  不含数据行, 属元数据级资源; 实际取数仍由大屏/图表经 governed_execute 治理。
- 预制 SQL 属于数据集职责(`adh_datasets`, 已治理的查询资产), 不在字模库。
- 状态两档各管一件事: is_active=0 是下架(软删除, 清单不再给出), is_enabled=0 是禁用
  (停用但保留: 管理页仍可查可再启用, 但一律不可被选用 —— 主题包不能套用、系统主题菜单里不再出现)。
- 系统内置字模(is_builtin=1)仅 admin 可编辑/下架(对齐内置组件 admin-only 保护规范);
  自定义字模(source=custom)由创建者/管理员维护, 支持从大屏"存为字模"回存沉淀。
"""

from __future__ import annotations

import json
import logging
import time

from backend.common.db import execute_query, execute_write, execute_insert

logger = logging.getLogger(__name__)

# 字模类别枚举(与迁移/前端管理页保持一致)
CATEGORIES = {
    "chart_style", "screen_background", "kpi_card",
    "layout_template", "decoration_frame", "color_theme",
    "theme_pack",
    # 动效预设(入场/图表/切板/正文出现效果): 与主题包正交 —— 主题管配色与版式,
    # 动效管时间维度, 每套主题都能搭任意预设。段值键清单见前端 lib/motion.ts 同名契约,
    # 漏登记这里的后果是「新建动效字模必然 400 未知字模类别」。
    "motion_preset",
    # 报告模板与字模库同源（版式契约的唯一存放处：任务表单/配置页/报表中心都读这一份）。
    # 漏登记的实际后果：报告模板配置页「新建模板」必然 400「未知字模类别」——
    # 配置功能本身配不出东西，只能改库里的内置行。
    "report_template",
}

_JSON_FIELDS = ("style_config",)


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


def is_usable(row: dict | None) -> bool:
    """字模行是否"在用"：未下架(is_active)且未禁用(is_enabled)。

    消费口（AS-BOT 取用工具、大屏组装）用它判定，不得另写一套：
    禁用=停用但保留，被引用时不能继续生效，否则"禁用"只管前端选框、服务端照用（半截口径）。
    is_enabled 列缺失（未跑迁移的存量库）按启用处理，避免把在用字模误判成停用。
    """
    if not row:
        return False
    if int(row.get("is_active") or 0) == 0:
        return False
    val = row.get("is_enabled")
    return True if val is None else int(val or 0) != 0


def _normalize(row: dict) -> dict:
    """JSON 文本列反序列化, 便于前端直接消费。"""
    for f in _JSON_FIELDS:
        if f in row:
            row[f] = _loads(row.get(f), {} if f == "style_config" else None)
    return row


class VisLibraryService:
    """字模库 CRUD(元数据级, 不返回数据行)。"""

    def list_components(self, category: str = "", include_inactive: bool = False,
                        include_disabled: bool = False) -> list:
        """字模清单(元数据级)。

        include_inactive / include_disabled 分别控制"已下架"与"已禁用"两档:
        禁用=停用但保留, 条目仍在库里可查可再启用, 但一律**不可被选用** ——
        编辑器字模面板、报表主题下拉、AS-BOT 清单都不得给出, theme_pack 被禁用还要让
        系统主题菜单里对应主题不再出现。默认 include_disabled=False 是 fail-closed:
        新消费方漏传参数只会少给, 不会把停用的字模当在用发出去。

        与分页方法 list_components_page 共用同一份筛选/排序构造(_build_filters/_ORDER_BY),
        不得各写一套谓词导致口径漂移;本方法不分页(选用面需要全量在手)。
        """
        where, params = self._build_filters(category, include_inactive, include_disabled, "")
        rows = execute_query("SELECT * FROM adh_vis_components" + where + self._ORDER_BY, params) or []
        return [_normalize(r) for r in rows]

    # 排序唯一口径：清单与分页必须同一 ORDER BY, 否则翻出去的两页会重行/漏行
    _ORDER_BY = " ORDER BY sort_order ASC, is_builtin DESC, id ASC"
    # 配置类清单的服务端每页硬上限(与 content/articles 同口径的"服务端说了算")
    _PAGE_SIZE_MAX = 200

    def _build_filters(self, category: str, include_inactive: bool,
                       include_disabled: bool, q: str) -> tuple[str, list]:
        """把筛选/搜索谓词下推成 SQL WHERE 片段与参数(清单与分页共用一份, 不分叉)。"""
        conds, params = [], []
        if category:
            conds.append("category = %s")
            params.append(category)
        if not include_inactive:
            conds.append("is_active = 1")
        if not include_disabled:
            conds.append("is_enabled = 1")
        if q:
            like = f"%{q}%"
            conds.append("(name LIKE %s OR code LIKE %s OR description LIKE %s)")
            params.extend([like, like, like])
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        return where, params

    def list_components_page(self, category: str = "", include_inactive: bool = False,
                             include_disabled: bool = False, q: str = "",
                             page: int = 1, page_size: int = 20) -> dict:
        """服务端**真分页**清单(元数据级): 行总数由 COUNT 定, 当前页由 LIMIT/OFFSET 取。

        与管理页"拉全量再前端切片"的假分页相对 —— 筛选(category)、搜索(q)、下架/禁用两档
        全部下推到 SQL, 偏移量由页号算出(调用方不递 offset), 每页行数服务端夹到
        [1, _PAGE_SIZE_MAX]。返回契约 {items,total,page,page_size} 与 content/articles 同源。
        """
        page = max(1, int(page or 1))
        page_size = min(self._PAGE_SIZE_MAX, max(1, int(page_size or 20)))
        where, params = self._build_filters(category, include_inactive, include_disabled, q)
        total = int((execute_query(
            "SELECT COUNT(*) AS cnt FROM adh_vis_components" + where, params, fetchone=True,
        ) or {}).get("cnt", 0))
        rows = execute_query(
            "SELECT * FROM adh_vis_components" + where + self._ORDER_BY + " LIMIT %s OFFSET %s",
            list(params) + [page_size, (page - 1) * page_size],
        ) or []
        return {"items": [_normalize(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}

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
            "(code, name, category, chart_type, style_config, thumbnail, "
            " description, source, workspace_id, is_active, is_builtin, sort_order, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                code, name, category, data.get("chart_type"),
                json.dumps(data.get("style_config") or {}, ensure_ascii=False),
                data.get("thumbnail"),
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

        allowed = ("name", "category", "chart_type", "thumbnail", "description", "sort_order",
                   "is_active", "is_enabled")
        sets, params = [], []
        for k in allowed:
            if k in data:
                sets.append(f"`{k}` = %s")
                params.append(data[k])
        if "style_config" in data:
            sets.append("style_config = %s")
            params.append(json.dumps(data.get("style_config") or {}, ensure_ascii=False))
        if not sets:
            return False

        params.append(component_id)
        affected = execute_write(
            f"UPDATE adh_vis_components SET {', '.join(sets)} WHERE id = %s", params,
        )
        return affected > 0

    def set_enabled(self, component_id: int, enabled: bool, user_id: int, is_admin: bool) -> dict:
        """启用/禁用字模(停用但保留); 内置字模 admin-only, 与编辑/下架同口径。

        返回字模摘要(id/code/name/category/is_enabled): 前端据此把影响面说清楚 ——
        禁用主题包会同时让系统主题里对应主题不可选, 这个后果必须显式告知操作者。
        """
        existing = self.get_component(component_id)
        if not existing:
            raise ValueError("字模不存在")
        if existing.get("is_builtin") and not is_admin:
            raise BuiltinProtectedError("系统内置字模仅管理员可启用/禁用")
        affected = execute_write("UPDATE adh_vis_components SET is_enabled = %s WHERE id = %s",
                                 (1 if enabled else 0, component_id))
        logger.info("[vis_library] component id=%s code=%s enabled=%s by=%s",
                    component_id, existing.get("code"), enabled, user_id)
        if not affected:
            raise ValueError("字模状态未变更，请刷新后重试")
        return {"id": int(component_id), "code": existing.get("code"), "name": existing.get("name"),
                "category": existing.get("category"), "is_enabled": 1 if enabled else 0}

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
