"""Dashboard Service -- Business logic for dashboards, charts, and snapshots.

Migrated from backend/api/dashboard.py into service classes:
- DashboardService: Dashboard CRUD, reorder, copy
- ChartService: Chart CRUD, refresh, batch refresh, layout
- SnapshotService: Chart snapshot save/list/get

Uses shared DB connection from backend/common/db.
Tables: adh_dashboards, adh_charts, adh_chart_snapshots, adh_saved_queries
"""

import copy
import json
import logging
import math
import re
import time
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn
from backend.common.ttl_cache import dashboard_cache
from backend.core.governed_query import governed_execute, NoIdentityError

logger = logging.getLogger(__name__)


# ── Utility Functions ────────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    return int(time.time() * 1000)


def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None and datetime with ISO string for JSON compliance."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _json_loads_safe(val):
    """Parse JSON string, return original if already dict/list."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _normalize_dashboard(row: dict) -> dict:
    """Normalize dashboard row for JSON serialization."""
    for field in ("layout", "filters", "params"):
        row[field] = _json_loads_safe(row.get(field))
    for ts in ("created_at", "updated_at"):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    row.setdefault("is_default", 0)
    row.setdefault("carousel_interval", 0)
    row.setdefault("params", [])
    row.setdefault("status", "designing")
    return row


def _normalize_chart(row: dict) -> dict:
    """Normalize chart row for JSON serialization."""
    for field in ("config", "position", "semantic_query"):
        row[field] = _json_loads_safe(row.get(field))
    for ts in ("created_at", "updated_at"):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    row.setdefault("source_type", "query")
    row.setdefault("source_id", None)
    row.setdefault("data_cache", None)
    row.setdefault("query_source", "raw_sql")
    if (row.get("config") or {}).get("as_bot_design"):
        # 设计者的 SQL/预览不能经普通图表详情或共享缓存分发。
        row.pop("sql_query", None)
        row["data_cache"] = None
    return row


def _geo_num(value, default: float) -> float:
    """取几何值: 非有限数字一律回落默认(脏 position 不得污染预览尺寸)。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    v = float(value)
    return v if math.isfinite(v) else default


def _chart_preview_rect(position, widget: bool) -> tuple:
    """单个图表区块的矩形(口径对齐前端 dashboardDesign.chartRect 的最小宽高兜底)。"""
    p = _json_loads_safe(position)
    p = p if isinstance(p, dict) else {}
    return (
        max(0.0, _geo_num(p.get("x"), 0.0)),
        max(0.0, _geo_num(p.get("y"), 0.0)),
        max(100.0 if widget else 200.0, _geo_num(p.get("w"), 300.0 if widget else 400.0)),
        max(36.0 if widget else 150.0, _geo_num(p.get("h"), 60.0 if widget else 300.0)),
    )


def card_catalog_fields(rows: list) -> list:
    """就地补齐看板目录行的「卡片瀑布清单」共享字段(可缓存, 与查看者无关)。

    补: description / updated_at(ISO) / chart_count(数据图表数) / preview(画布尺寸 + 每图摘要)。
    预览**只回几何与布局数据**，不含 SQL、语义查询、样式配置与数据行 —— 清单页不需要,
    浏览域响应也不该把口径带出去(护栏 §7); 原始 filters 用完即剔除。

    这里不再自己算「区块矩形」：几何事实源是前端的唯一渲染器 DashboardThumbnail
    （page 模式走 resolvePageLayout(config.pageLayout)，大屏模式走 position 像素）。
    曾在这里按 position 另算一套 blocks，导致两个看板列表缩略图长得不一样：
    page 布局的看板在浏览侧退化成大屏像素图（内容只占顶部一小块）。
    可写性(editable)与 owner_id 的外剥是每请求的事, 由 catalog_cards 处理。
    """
    from backend.common.db import execute_query
    for row in rows:
        filters = _json_loads_safe(row.pop("filters", None))
        canvas = {}
        page = None
        if isinstance(filters, dict):
            design = filters.get("design")
            if isinstance(design, dict):
                if isinstance(design.get("canvas"), dict):
                    canvas = design["canvas"]
                # 网格间距等页面级设置随预览下发，保证缩略图与真实画布同一尺寸口径
                if isinstance(design.get("page"), dict):
                    page = {k: v for k, v in design["page"].items() if k in ("version", "gap")}
        row["description"] = row.get("description") or ""
        if hasattr(row.get("updated_at"), "isoformat"):
            row["updated_at"] = row["updated_at"].isoformat()
        row["preview"] = {"width": _geo_num(canvas.get("width"), 1920.0),
                          "height": _geo_num(canvas.get("height"), 1080.0),
                          "page": page, "charts": []}
        row["chart_count"] = 0
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return rows
    marks = ",".join(["%s"] * len(ids))
    charts = execute_query(
        f"SELECT id, dashboard_id, chart_type, position, config FROM adh_charts "
        f"WHERE dashboard_id IN ({marks}) ORDER BY id", ids) or []
    by_dash: dict = {}
    for c in charts:
        widget = str(c.get("chart_type") or "").startswith("widget_")
        rect = _chart_preview_rect(c.get("position"), widget)
        cfg = _json_loads_safe(c.get("config"))
        cfg = cfg if isinstance(cfg, dict) else {}
        item = by_dash.setdefault(int(c["dashboard_id"]), {"charts": [], "count": 0})
        # chart_type 一并下发：卡片预览要按类型画可辨识缩影（柱/线/饼/表格/控件）；
        # pageLayout/hidden 只取布局与可见性两个键，不回其余 config（口径不外泄）。
        item["charts"].append({
            "id": int(c["id"]),
            "chart_type": c.get("chart_type") or "",
            "position": {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]},
            "pageLayout": cfg.get("pageLayout"),
            "hidden": bool(cfg.get("hidden")),
        })
        if not widget:
            item["count"] += 1
    for row in rows:
        item = by_dash.get(int(row["id"])) or {"charts": [], "count": 0}
        preview = row["preview"]
        preview["charts"] = item["charts"]
        # 区块超出画布时按实际外扩(与前端 getCanvas 同口径, 否则预览会被裁掉一块)
        extents = [(c["position"]["x"] + c["position"]["w"], c["position"]["y"] + c["position"]["h"])
                   for c in item["charts"] if not (c.get("pageLayout") or {})]
        preview["width"] = max([320.0, preview["width"]] + [e[0] for e in extents])
        preview["height"] = max([180.0, preview["height"]] + [e[1] for e in extents])
        row["chart_count"] = item["count"]
    return rows


def _sanitize_param_value(val: str) -> str:
    """Escape a string parameter value to prevent SQL injection.

    - Backslash-escapes single quotes, backslashes, and NUL bytes.
    - Rejects values containing semicolons (statement separator).
    - Rejects values containing comment markers.
    """
    if any(marker in val for marker in (";", "--", "/*", "*/", "\x00")):
        raise ValueError(f"参数值包含非法字符: {val[:50]}")
    val = val.replace("\\", "\\\\").replace("'", "\\'")
    return val


def _flatten_params(params: dict, prefix: str = "") -> dict:
    """Flatten nested dict: {time: {start: "x"}} -> {"time.start": "x"}."""
    flat = {}
    for k, v in params.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_params(v, key))
        else:
            flat[key] = v
    return flat


def _substitute_params(sql: str, params: dict) -> str:
    """Replace {{param_name}} placeholders in SQL with parameter values.

    Supports nested keys via dot notation: {{time.start}}, {{time.end}}.
    String values are escaped to prevent SQL injection.
    """
    if params is None or not sql:
        return sql

    flat = _flatten_params(params)

    _PAGINATION_DEFAULTS = {"page_limit": "20", "page_offset": "0"}

    def replacer(m):
        key = m.group(1).strip()
        val = flat.get(key, _PAGINATION_DEFAULTS.get(key, ""))
        if val is None or val == "":
            val = _PAGINATION_DEFAULTS.get(key, "")
        val_str = str(val)
        try:
            float(val_str)
            return val_str
        except ValueError:
            pass
        return _sanitize_param_value(val_str)

    return re.sub(r'\{\{(\w+(?:\.\w+)*)\}\}', replacer, sql)


def _get_chart_datasource_id(chart: dict) -> int:
    """Extract datasource_id from chart config."""
    config = chart.get("config")
    config = _json_loads_safe(config)
    if isinstance(config, dict):
        return config.get("datasource_id", 0)
    return 0


def _dataset_declared_args(dataset_id: int, params: dict) -> dict:
    """从看板 params 中取数据集已声明的变量(如 date_start/date_end)，作为 ${} 渲染参数。

    只保留数据集 variables 声明过的键：未声明的键交给 compile_dataset 会 fail-loud 报错，
    看板侧先按声明集过滤，不把无关全局 param 灌进不相关的图表。
    """
    if not dataset_id or not params:
        return {}
    from backend.modules.viz.services import dataset_service
    from backend.semantics.sql_params import parse_variables
    ds = dataset_service.get_dataset(dataset_id)
    if not ds:
        return {}
    declared = set(parse_variables(ds.get("variables")) or {})
    return {k: v for k, v in params.items() if k in declared}


def _clear_default(user_id: int, workspace_id: int = 0):
    """Clear is_default flag for all dashboards of a user within a workspace."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id:
                    cur.execute(
                        "UPDATE adh_dashboards SET is_default = 0 "
                        "WHERE owner_id = %s AND workspace_id = %s AND is_default = 1",
                        (user_id, workspace_id),
                    )
                else:
                    cur.execute(
                        "UPDATE adh_dashboards SET is_default = 0 "
                        "WHERE owner_id = %s AND is_default = 1",
                        (user_id,),
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to clear default dashboard: %s", e)


# ── 看板载荷缓存(只缓存共享载荷; 可见性/可写性每次实时裁决) ─────────────────────
#
# 远程元数据库单查询 ~60-130ms, 目录/详情每次全量回源要 4-6 个串行往返(几百毫秒)。
# 缓存口径(护栏 §9 / V3 撤权即时生效): 可见性裁决(visible_dashboard_ids /
# check_dashboard_visible)一律实时查询, **不进任何缓存**; 这里缓存的只是
# 「看板长什么样 / 有哪些组」的共享载荷 + 用户角色归属(仅用于分组编排,
# 不授予任何看板可见性)。写路径显式失效(_invalidate_dashboard_payload),
# 短 TTL 兑底防漏失效。
CARDS_KEY = "dash:cards:v3"            # 共享: 目录行 + 卡片预览摘要（v3: blocks → charts 摘要）
GROUPS_KEY = "dash:groups:v2"          # 共享: 组 + 组内看板 + 组→角色绑定
DETAIL_KEY = "dash:detail:{}"          # 共享: 单看板完整载荷(charts 含 data_cache)
USER_ROLES_KEY = "dash:user_roles:{}"  # 按用户: 角色 id 集(仅分组编排用)
PAYLOAD_TTL = 120                      # 共享载荷 TTL(写路径会提前失效)
USER_ROLES_TTL = 30


def _invalidate_dashboard_payload(dashboard_id: int = None, *, structural: bool = True):
    """失效看板共享载荷缓存。

    structural=True: 结构变更(看板/图表/布局/组) → 目录+组+单看板全失效;
    structural=False: 仅 data_cache 变化(图表刷新) → 只失效该看板详情,
    不把目录/组也带下水。
    """
    try:
        if structural:
            dashboard_cache.invalidate(CARDS_KEY)
            dashboard_cache.invalidate(GROUPS_KEY)
        if dashboard_id:
            dashboard_cache.invalidate(DETAIL_KEY.format(int(dashboard_id)))
    except Exception as e:  # noqa: BLE001 —— 缓存失效失败不得阻断主链路
        logger.warning("Failed to invalidate dashboard payload cache: %s", e)


def _invalidate_dashboard_cache(user_id: int = None):
    """Clear dashboard cache. user_id=None clears all."""
    _invalidate_dashboard_payload()
    if user_id is None:
        dashboard_cache.invalidate()
    else:
        dashboard_cache.invalidate_prefix(f"dash:{user_id}:")


def _position_to_pixels(pos, index: int = 0) -> dict:
    """设计 position 是 12 列网格制（x/y/w/h 为小整数），看板画布是像素绝对定位。

    发布时换算成像素（1 列=80px、1 行=90px，与默认全宽卡 960x360 对齐）；
    已是像素值则原样保留。position 缺失/非法时按全宽流式堆叠兜底（auto-layout），
    保证发布后排版可用（LLM 未给排版也不塌）。"""
    pos = pos if isinstance(pos, dict) else {}
    try:
        x, y, w, h = (float(pos.get(k) or 0) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        x = y = w = h = 0.0
    if max(w, h) <= 24:  # 网格制（像素值不会这么小）
        if w <= 0:
            w = 12.0
        if h <= 0:
            h = 4.0
        if x == 0 and y == 0 and index > 0:
            y = 4.0 * index  # 未给排版时按全宽流式错开，不重叠
        return {"x": x * 80, "y": y * 90, "w": w * 80, "h": h * 90}
    return {"x": x, "y": y, "w": w if w > 0 else 960, "h": h if h > 0 else 360}


def publish_design_in_transaction(cur, doc, queries, scope, user):
    """仅由已锁定并校验的审批事务调用，不自行提交。"""
    from backend.modules.viz.services.dashboard_design_service import dump
    selection = doc["selection"]
    if doc["operation"] == "create":
        cur.execute("INSERT INTO adh_dashboards (name,description,workspace_id,owner_id,status,is_public,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,'enabled',0,UTC_TIMESTAMP(),UTC_TIMESTAMP())",
                    (doc["name"], doc["request"], scope["workspace"], user["user_id"]))
        dashboard_id = cur.lastrowid
    else:
        dashboard_id = selection["dashboard_id"]
    chart_ids = []
    for widget, sql in zip(doc["widgets"], queries):
        cfg = {**widget["config"], "datasource_id": scope["datasource_id"], "as_bot_design": True}
        query = dump(widget["query"]) if widget["query_source"] == "semantic" else None
        values = (widget["title"], widget["chart_type"], sql, dump(cfg),
                  dump(_position_to_pixels(widget.get("position"), len(chart_ids))),
                  scope["datasource_id"], query, widget["query_source"], scope["workspace"])
        if doc["operation"] == "update":
            cid = selection["chart_id"]
            cur.execute("UPDATE adh_charts SET name=%s,chart_type=%s,sql_query=%s,config=%s,position=%s,source_id=%s,"
                        "semantic_query=%s,query_source=%s,workspace_id=%s,data_cache=NULL,updated_at=UTC_TIMESTAMP() "
                        "WHERE id=%s AND dashboard_id=%s", (*values, cid, dashboard_id))
        else:
            cur.execute("INSERT INTO adh_charts (name,chart_type,sql_query,config,position,source_id,semantic_query,"
                        "query_source,workspace_id,dashboard_id,source_type,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'query',UTC_TIMESTAMP(),UTC_TIMESTAMP())",
                        (*values, dashboard_id))
            cid = cur.lastrowid
        chart_ids.append(cid)
    return {"success": True, "dashboard_id": dashboard_id, "chart_ids": chart_ids,
            "operation": doc["operation"], "url": f"/dashboard/editor/{dashboard_id}"}


# ── Dashboard Visibility (看板可见性按角色授权) ───────────────────────────
#
# 可见性唯一裁决 = 用户角色授权(adh_user_roles ⋈ adh_role_dashboard_access，
# 经 role_service.get_user_allowed_dashboards 解析):
#   * admin 始终全可见(展示范围语义; 图表数据仍由 permission_enforcer 按查看者治理);
#   * 其余角色 fail-closed: 未配置授权 = 一律不可见(owner/is_public 不再授予“可看”);
#   * 可见性裁决必须实时查询共享层，不进任何进程内 TTL 缓存(撤权后不得留可见窗口)。


def _is_admin_user(user_id: int, user_role: str = "") -> bool:
    """admin 判定以服务端身份为准：优先信任服务端注入的 user_role，缺失时回查授权表。

    服务端注入的角色非空时直接采信(非 admin 一律按非 admin, 少一次远程查询);
    仅当角色缺失才回查 adh_user_roles ⋈ adh_roles —— 这与 docstring 原有口径一致,
    也与 list_current_permissions 的 JWT 口径一致(DB 里新增 admin 未换令牌前按最小权限)。
    """
    if (user_role or "").lower() == "admin":
        return True
    if not user_id:
        return False
    if user_role:
        return False
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM adh_user_roles ur "
                "JOIN adh_roles r ON r.id = ur.role_id "
                "WHERE ur.user_id = %s AND r.name = 'admin'",
                (user_id,),
            )
            return int((cur.fetchone() or {}).get("cnt") or 0) > 0
    finally:
        conn.close()


def visible_dashboard_ids(user_id: int, user_role: str = "", workspace_id: int = 0):
    """返回用户可见看板 id 集；admin 返回 None 表示全可见；空集 = 全不可见(fail-closed)。"""
    if _is_admin_user(user_id, user_role):
        return None
    from backend.core.role_service import role_service
    return set(role_service.get_user_allowed_dashboards(user_id, workspace_id))


def check_dashboard_visible(dashboard_id: int, user_id: int, user_role: str = "",
                            workspace_id: Optional[int] = None) -> bool:
    """单看板可见性裁决（查看链路唯一入口）。workspace_id 缺省时按看板归属解析。"""
    if not user_id:
        return False
    if _is_admin_user(user_id, user_role):
        return True
    if workspace_id is None:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT workspace_id FROM adh_dashboards WHERE id = %s", (dashboard_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return False
        workspace_id = int(row["workspace_id"] or 0)
    from backend.core.role_service import role_service
    return int(dashboard_id) in set(
        role_service.get_user_allowed_dashboards(user_id, workspace_id))


def check_dashboard_editable(dashboard_id: int, user_id: int, user_role: str = "") -> bool:
    """单看板「可编辑」裁决(写链路唯一口径): admin 或看板属主。

    角色授权(adh_role_dashboard_access)只授予「可看」, **不含写** —— 系统分配给
    角色的看板不得被角色成员改写; fail-closed: 无身份/看板不存在/非属主一律拒。
    同口径服务 AS-BOT 仪表盘设计通道(dashboard_design_service 同样按 owner 把关)。
    """
    if not user_id:
        return False
    if _is_admin_user(user_id, user_role):
        return True
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM adh_dashboards WHERE id = %s", (dashboard_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return bool(row) and int(row.get("owner_id") or 0) == int(user_id)


def _mark_editable(rows: list, viewer_id: int, viewer_role: str = "", *, drop_owner: bool = False,
                  is_admin: bool = None) -> list:
    """就地标注每行 editable(仅属主/admin 可写), 供前端隐藏写入口。

    drop_owner=True 时剥除 owner_id(浏览域清单不外泄属主 id; 详情页仍带, 供属主自检)。
    is_admin 已由同一请求裁决过时传入, 避免重复查库。
    """
    admin = _is_admin_user(viewer_id, viewer_role) if is_admin is None else is_admin
    for row in rows:
        row["editable"] = bool(admin or int(row.get("owner_id") or 0) == int(viewer_id or 0))
        if drop_owner:
            row.pop("owner_id", None)
    return rows


# ── 目录/详情载荷构建: 共享载荷走缓存, 可见性每请求实时裁决 ─────────────


def _card_rows() -> list:
    """共享卡片载荷(缓存): 目录行 + 卡片几何预览, 带 owner_id 供每请求判可写。"""
    def build():
        from backend.common.db import execute_query
        rows = execute_query(
            "SELECT id, name, description, status, is_default, sort_order, updated_at, owner_id, filters "
            "FROM adh_dashboards ORDER BY is_default DESC, sort_order, id") or []
        return card_catalog_fields(rows)
    return dashboard_cache.get_or_set(CARDS_KEY, build, ttl=PAYLOAD_TTL) or []


def _groups_structure() -> dict:
    """共享分组结构(缓存): 组 + 组内看板顺序 + 组→角色绑定。"""
    def build():
        from backend.common.db import execute_query
        groups = execute_query(
            "SELECT id, name, description, sort FROM adh_dashboard_groups ORDER BY sort, id") or []
        items: dict = {}
        for r in (execute_query(
                "SELECT group_id, dashboard_id FROM adh_dashboard_group_items ORDER BY sort, id") or []):
            items.setdefault(str(r["group_id"]), []).append(int(r["dashboard_id"]))
        role_groups: dict = {}
        for r in (execute_query("SELECT role_id, group_id FROM adh_role_dashboard_groups") or []):
            role_groups.setdefault(str(r["role_id"]), []).append(int(r["group_id"]))
        return {"groups": groups, "items": items, "role_groups": role_groups}
    return dashboard_cache.get_or_set(GROUPS_KEY, build, ttl=PAYLOAD_TTL) \
        or {"groups": [], "items": {}, "role_groups": {}}


def _user_role_ids(user_id: int) -> list:
    """用户角色 id 集(短 TTL)。**只用于分组编排** —— 看板可见性不走这里,
    每次由 get_user_allowed_dashboards 实时裁决(V3 撤权即时生效, 不留可见窗口)。"""
    def build():
        from backend.common.db import execute_query
        return [int(r["role_id"]) for r in (
            execute_query("SELECT role_id FROM adh_user_roles WHERE user_id = %s", (user_id,)) or [])]
    return dashboard_cache.get_or_set(USER_ROLES_KEY.format(int(user_id)), build, ttl=USER_ROLES_TTL) or []


def catalog_cards(visible_ids, viewer_id: int, viewer_role: str = "", is_admin: bool = None) -> list:
    """看板卡片清单: 实时可见集过滤 + 每请求标注可写性(不外泄 owner_id)。

    visible_ids=None 表示 admin 全可见; 空集由调用方提前短路(fail-closed)。
    卡片几何走共享缓存, 但「谁能看到哪些」永远以传入的实时可见集为准。
    """
    rows = [dict(r) for r in _card_rows()]
    if visible_ids is not None:
        allow = {int(i) for i in visible_ids}
        rows = [r for r in rows if int(r["id"]) in allow]
    return _mark_editable(rows, viewer_id, viewer_role, drop_owner=True, is_admin=is_admin)


def build_visible_groups(user_id: int, user_role: str = "") -> list:
    """当前用户可见的仪表盘组目录(组按角色分配, 含「未分组」兜底组)。

    分层: 可见集实时裁决(fail-closed) ∩ 共享载荷缓存(卡片几何/组编排)。
    缓存不赋予任何额外可见性 —— 组内看板最终仍与实时可见集求交。
    """
    visible = visible_dashboard_ids(user_id, user_role)
    if visible is not None and not visible:
        return []  # fail-closed: 无任何可见看板
    is_admin = visible is None  # visible_dashboard_ids 对 admin 返回 None
    cards = catalog_cards(visible, user_id, user_role, is_admin=is_admin)
    struct = _groups_structure()
    if is_admin:
        groups = struct["groups"]
    else:
        bound = {g for rid in _user_role_ids(user_id)
                 for g in struct["role_groups"].get(str(rid), [])}
        groups = [g for g in struct["groups"] if int(g["id"]) in bound]
    out, grouped_ids = [], set()
    for g in groups:
        member_ids = set(struct["items"].get(str(g["id"]), []))
        member_ids &= {c["id"] for c in cards}
        grouped_ids |= member_ids
        out.append({"id": g["id"], "name": g["name"], "description": g.get("description") or "",
                    "sort": g.get("sort") or 0,
                    "dashboards": [c for c in cards if c["id"] in member_ids]})
    out.append({"id": 0, "name": "未分组", "description": "未加入任何仪表盘组的可见看板",
                "sort": 9999, "dashboards": [c for c in cards if c["id"] not in grouped_ids]})
    return out


def refresh_designed_chart(chart, user_id):
    """设计图表不复用跨用户 data_cache；每次按查看者身份重新治理。"""
    from backend.common.auth import resolve_execution_owner
    from backend.common.db import execute_query
    from backend.modules.viz.services.dashboard_design_service import compile_widget, validate_query_sources, decoded, DesignError
    uid = int(user_id or 0)
    if not uid:
        raise NoIdentityError("缺少可信身份")
    dashboard = execute_query("SELECT owner_id,workspace_id,is_public FROM adh_dashboards WHERE id=%s",
                              (chart["dashboard_id"],), fetchone=True)
    if not dashboard or not check_dashboard_visible(
            chart["dashboard_id"], uid, workspace_id=int(dashboard["workspace_id"] or 0)):
        raise PermissionError("无权访问该仪表盘")
    live = resolve_execution_owner(uid, int(dashboard["workspace_id"] or 0))
    config = decoded(chart["config"], {})
    ds = int(chart.get("source_id") or config.get("datasource_id") or 0)
    if not ds:
        raise DesignError("图表业务绑定已失效")
    source = execute_query("SELECT db_type FROM adh_datasources WHERE id=%s", (ds,), fetchone=True)
    if not source:
        raise DesignError("图表业务域已失效")
    scope = {"datasource_id": ds, "workspace": live["workspace_id"],
             "dialect": "postgres" if source["db_type"] in ("postgres", "postgresql", "pg", "sls") else "mysql"}
    widget = {"query_source": chart["query_source"], "query": decoded(chart.get("semantic_query"), {}),
              "manual_sql": chart.get("sql_query")}
    sql, _ = compile_widget(widget, scope)
    validate_query_sources(sql, scope)
    return governed_execute(sql, ds, uid, scope["workspace"], live["username"])


# ── DashboardService ─────────────────────────────────────────────────────────


class DashboardService:
    """Dashboard CRUD operations."""

    def list_dashboards(self, user_id: int, workspace_id: int = 0, user_role: str = "") -> list:
        """List dashboards scoped by workspace and role visibility.

        可见性按请求实时裁决（不走 TTL 缓存）：进程内缓存无法跨实例失效，
        撤权后不得留可见窗口（分布式约束）。
        """
        return self._fetch_dashboards_from_db(user_id, workspace_id, user_role)

    def _fetch_dashboards_from_db(self, user_id: int, workspace_id: int = 0,
                                  user_role: str = "") -> list:
        """Fetch dashboards from database (角色可见集过滤, fail-closed)."""
        visible = visible_dashboard_ids(user_id, user_role, workspace_id)
        if visible is not None and not visible:
            return []
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                sql = "SELECT * FROM adh_dashboards"
                params: list = []
                conds = []
                if workspace_id:
                    conds.append("workspace_id = %s")
                    params.append(workspace_id)
                if visible is not None:
                    marks = ",".join(["%s"] * len(visible))
                    conds.append(f"id IN ({marks})")
                    params.extend(sorted(visible))
                if conds:
                    sql += " WHERE " + " AND ".join(conds)
                sql += " ORDER BY is_default DESC, sort_order ASC, updated_at DESC"
                cur.execute(sql, params)
                dashboards = cur.fetchall()
                if not dashboards:
                    return []

                dash_ids = [d["id"] for d in dashboards]
                placeholders = ",".join(["%s"] * len(dash_ids))
                cur.execute(
                    f"SELECT * FROM adh_charts WHERE dashboard_id IN ({placeholders}) ORDER BY id",
                    dash_ids,
                )
                all_charts = cur.fetchall()

                charts_map: dict = {}
                for c in all_charts:
                    _normalize_chart(c)
                    charts_map.setdefault(c["dashboard_id"], []).append(c)

                for d in dashboards:
                    _normalize_dashboard(d)
                    d["charts"] = charts_map.get(d["id"], [])
                # editable 仅供前端隐藏写入口: 角色授予的可见权不含编辑权(写在 API 层再裁决一次)
                _mark_editable(dashboards, user_id, user_role)
                return dashboards
        finally:
            conn.close()

    def _fetch_dashboard_payload(self, dashboard_id: int) -> Optional[dict]:
        """单看板完整载荷(共享, 可缓存): 不含可写性标注, 不做可见性裁决。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (dashboard_id,))
                dashboard = cur.fetchone()
                if not dashboard:
                    return None
                cur.execute(
                    "SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()
                for c in charts:
                    _normalize_chart(c)
                _normalize_dashboard(dashboard)
                dashboard["charts"] = charts
                return dashboard
        finally:
            conn.close()

    def get_dashboard(self, dashboard_id: int, user_id: int, user_role: str = "",
                      enforce_visibility: bool = True) -> Optional[dict]:
        """Get a single dashboard with its charts (角色不可见返回 None → API 404)。

        可见性裁决实时(check_dashboard_visible, 不进缓存); 载荷走共享缓存
        (写路径 _invalidate_dashboard_payload 失效), 每请求标注可写性。
        enforce_visibility=False 仅供 AS-BOT 大屏设计通道使用：该通道的访问控制
        仍由 resource_guard 的 owner/is_public 策略承担(本次改造边界外)，
        不适用角色可见性裁决。
        """
        if enforce_visibility and not check_dashboard_visible(dashboard_id, user_id, user_role):
            return None
        payload = dashboard_cache.get_or_set(
            DETAIL_KEY.format(int(dashboard_id)),
            lambda: self._fetch_dashboard_payload(dashboard_id), ttl=PAYLOAD_TTL)
        if not payload:
            return None
        result = copy.deepcopy(payload)  # 缓存对象不外泄: 调用方就地改写不得污染缓存
        # editable: 仅属主/admin 可写(角色授予的可见权不含编辑权); 保留 owner_id 供属主自检
        _mark_editable([result], user_id, user_role)
        return result

    def create_dashboard(self, data: dict, user_id: int) -> int:
        """Create a new dashboard. Returns the new dashboard ID."""
        did = _ts_id()
        now = _now()
        workspace_id = data.get("workspace_id") or 0

        if data.get("is_default"):
            _clear_default(user_id, workspace_id)

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_dashboards "
                    "(`id`, `name`, `description`, `layout`, `filters`, `params`, `status`, "
                    "`owner_id`, `workspace_id`, `is_public`, `is_default`, `carousel_interval`, "
                    "`created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        did,
                        data["name"],
                        data.get("description", ""),
                        json.dumps(data.get("layout") or []),
                        json.dumps(data.get("filters") or {}),
                        json.dumps(data.get("params") or []),
                        data.get("status", "designing"),
                        user_id,
                        workspace_id,
                        1 if data.get("is_public") else 0,
                        1 if data.get("is_default") else 0,
                        data.get("carousel_interval", 0),
                        now,
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        _invalidate_dashboard_cache(user_id)
        return did

    def update_dashboard(self, dashboard_id: int, data: dict, user_id: int) -> bool:
        """Update a dashboard. Returns True if updated."""
        if not data:
            return False

        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if data.get("is_default"):
                    cur.execute(
                        "SELECT workspace_id FROM adh_dashboards WHERE id = %s",
                        (dashboard_id,),
                    )
                    row = cur.fetchone()
                    ws_id = row["workspace_id"] if row else 0
                    _clear_default(user_id, ws_id)

                updates = ["updated_at = %s"]
                params = [now]

                field_map = {
                    "name": "name",
                    "description": "description",
                    "status": "`status`",
                }
                for key, col in field_map.items():
                    if key in data and data[key] is not None:
                        updates.append(f"{col} = %s")
                        params.append(data[key])

                for json_field in ("layout", "filters", "params"):
                    if json_field in data and data[json_field] is not None:
                        updates.append(f"`{json_field}` = %s")
                        params.append(json.dumps(data[json_field]))

                for bool_field in ("is_public", "is_default"):
                    if bool_field in data and data[bool_field] is not None:
                        updates.append(f"{bool_field} = %s")
                        params.append(1 if data[bool_field] else 0)

                if "carousel_interval" in data and data["carousel_interval"] is not None:
                    updates.append("carousel_interval = %s")
                    params.append(data["carousel_interval"])

                params.append(dashboard_id)
                cur.execute(
                    f"UPDATE adh_dashboards SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_dashboard(self, dashboard_id: int, user_id: int) -> bool:
        """Delete a dashboard and all its charts.

        写权限(仅 admin/属主)由 API 层 check_dashboard_editable 把关,
        这里只负责删除本身(否则 admin 删不了他人看板, 会静默报 404)。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_charts WHERE dashboard_id = %s", (dashboard_id,))
                # 级联清理角色可见性授权, 避免悬空授权行
                cur.execute("DELETE FROM adh_role_dashboard_access WHERE dashboard_id = %s", (dashboard_id,))
                cur.execute(
                    "DELETE FROM adh_dashboards WHERE id = %s",
                    (dashboard_id,),
                )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            return cur.rowcount > 0
        finally:
            conn.close()

    def reorder_dashboards(self, user_id: int, orders: list) -> bool:
        """Update sort_order for dashboards.

        写权限由 API 层 check_dashboard_editable 逐个把关(admin 或属主),
        这里不再按 owner_id 过滤(否则 admin 调不了他人看板的顺序, 静默无变化)。
        """
        if not orders:
            return True
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                for item in orders:
                    cur.execute(
                        "UPDATE adh_dashboards SET sort_order=%s, updated_at=%s "
                        "WHERE id=%s",
                        (item.get("sort_order", 0), now, item["id"]),
                    )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            return True
        finally:
            conn.close()

    def copy_dashboard(self, dashboard_id: int, user_id: int, new_name: str = None) -> Optional[dict]:
        """Copy a dashboard with all its charts (deep copy)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (dashboard_id,))
                src = cur.fetchone()
                if not src:
                    return None

                cur.execute(
                    "SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()

                now = _now()
                new_id = _ts_id()
                name = new_name or f"{src['name']} (副本)"

                cur.execute(
                    "INSERT INTO adh_dashboards "
                    "(`id`, `name`, `description`, `layout`, `filters`, `params`, `status`, "
                    "`owner_id`, `workspace_id`, `is_public`, `is_default`, "
                    "`carousel_interval`, `sort_order`, `created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        new_id, name, src.get("description"),
                        src.get("layout"), src.get("filters"), src.get("params"),
                        "designing", user_id, src.get("workspace_id", 0), 0, 0,
                        src.get("carousel_interval", 0), 0, now, now,
                    ),
                )

                for c in charts:
                    cid = _ts_id()
                    time.sleep(0.001)
                    cur.execute(
                        "INSERT INTO adh_charts "
                        "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, "
                        "`config`, `position`, `source_type`, `source_id`, `data_cache`, "
                        "`semantic_query`, `query_source`, `created_at`, `updated_at`) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            cid, new_id, c["name"], c["chart_type"], c.get("sql_query"),
                            c.get("config"), c.get("position"),
                            c.get("source_type", "query"),
                            c.get("source_id"), c.get("data_cache"),
                            c.get("semantic_query"), c.get("query_source") or "raw_sql",
                            now, now,
                        ),
                    )

                conn.commit()

                # Fetch the newly created dashboard with charts
                cur.execute("SELECT * FROM adh_dashboards WHERE id = %s", (new_id,))
                new_dashboard = cur.fetchone()
                cur.execute(
                    "SELECT * FROM adh_charts WHERE dashboard_id = %s ORDER BY id",
                    (new_id,),
                )
                new_charts = cur.fetchall()

                _normalize_dashboard(new_dashboard)
                for c in new_charts:
                    _normalize_chart(c)
                new_dashboard["charts"] = new_charts

                _invalidate_dashboard_cache(user_id)
                return new_dashboard
        finally:
            conn.close()


# ── ChartService ─────────────────────────────────────────────────────────────


class ChartService:
    """Chart CRUD, refresh, and layout operations."""

    def create_chart(self, dashboard_id: int, data: dict) -> int:
        """Add a chart to a dashboard. Returns the new chart ID."""
        cid = _ts_id()
        now = _now()

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_charts "
                    "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, "
                    "`config`, `position`, `source_type`, `source_id`, `data_cache`, "
                    "`semantic_query`, `query_source`, `created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        cid, dashboard_id,
                        data["name"], data["chart_type"], data.get("sql_query"),
                        json.dumps(data.get("config") or {}),
                        json.dumps(data.get("position") or {}),
                        data.get("source_type", "query"),
                        data.get("source_id"), data.get("data_cache"),
                        json.dumps(data.get("semantic_query"), ensure_ascii=False) if data.get("semantic_query") else None,
                        data.get("query_source") or "raw_sql",
                        now, now,
                    ),
                )
            conn.commit()
            _invalidate_dashboard_payload(dashboard_id)  # 图表结构变了: 目录预览/详情载荷都要重建
            return cid
        finally:
            conn.close()

    def update_chart(self, dashboard_id: int, chart_id: int, data: dict) -> bool:
        """Update a chart within a dashboard."""
        now = _now()

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 只更新已提交的白名单字段，视觉编辑不得清空查询和缓存。
                allowed = (
                    "name", "chart_type", "sql_query", "config", "position",
                    "source_type", "source_id", "data_cache", "semantic_query", "query_source",
                )
                sets, params = [], []
                for field in allowed:
                    if field not in data:
                        continue
                    value = data[field]
                    if field in ("name", "chart_type", "source_type", "query_source") and not value:
                        raise ValueError(f"{field} cannot be empty")
                    if field in ("config", "position", "semantic_query"):
                        value = json.dumps(value, ensure_ascii=False) if value is not None else None
                    sets.append(f"`{field}`=%s")
                    params.append(value)
                if sets:
                    sets.append("updated_at=%s")
                    params.extend([now, chart_id, dashboard_id])
                    cur.execute(
                        f"UPDATE adh_charts SET {', '.join(sets)} "
                        "WHERE id=%s AND dashboard_id=%s",
                        params,
                    )
                    if cur.rowcount > 0:
                        conn.commit()
                        return True
                # 空更新和相同值更新也是成功，只有对象确实不存在才返回 False。
                cur.execute(
                    "SELECT id FROM adh_charts WHERE id=%s AND dashboard_id=%s",
                    (chart_id, dashboard_id),
                )
                exists = cur.fetchone() is not None
            conn.commit()
            _invalidate_dashboard_payload(dashboard_id)
            return exists
        finally:
            conn.close()

    def delete_chart(self, dashboard_id: int, chart_id: int) -> bool:
        """Remove a chart from a dashboard."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_charts WHERE id = %s AND dashboard_id = %s",
                    (chart_id, dashboard_id),
                )
            conn.commit()
            _invalidate_dashboard_payload(dashboard_id)
            return cur.rowcount > 0
        finally:
            conn.close()

    def batch_save(self, dashboard_id: int, added: list, changed: dict, deleted: list) -> dict:
        """一次请求提交整板草稿，返回逐条结果供前端定位失败项。

        元数据库连接是 autocommit，跨多表的事务回滚不可用；因此采用
        「先全量预检、后逐条应用」：预检不过则一笔不写（不会留下半截草稿），
        预检过后的单条失败只影响该条，并在 results 里带可读原因上报（不静默吞）。
        """
        results: list = []
        errors: list = []

        for payload in added or []:
            if not (payload.get("name") or "").strip() or not (payload.get("chart_type") or "").strip():
                errors.append("新增图表缺少名称或类型，已中止保存（未写入任何变更）")
        targets = sorted({int(k) for k in (changed or {})} | {int(v) for v in (deleted or [])})
        existing: set = set()
        if targets:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM adh_charts WHERE dashboard_id=%s AND id IN ({})".format(
                            ",".join(["%s"] * len(targets))),
                        [dashboard_id, *targets],
                    )
                    existing = {row["id"] for row in (cur.fetchall() or [])}
            finally:
                conn.close()
        missing = [cid for cid in targets if cid not in existing]
        if missing:
            errors.append(f"图表 {missing} 已不在当前看板（可能被其他人删除），已中止保存（未写入任何变更）")
        if errors:
            return {"ok": False, "errors": errors, "results": results, "created": {}}

        created: dict = {}
        for index, payload in enumerate(added or []):
            try:
                new_id = self.create_chart(dashboard_id, payload)
                created[str(index)] = new_id
                results.append({"op": "create", "index": index, "chart_id": new_id, "ok": True})
            except Exception as exc:  # noqa: BLE001 — 逐条上报，不吞原因
                logger.exception("batch_save create failed (dashboard=%s)", dashboard_id)
                results.append({"op": "create", "index": index, "ok": False, "error": str(exc)})
        for chart_id, patch in (changed or {}).items():
            cid = int(chart_id)
            try:
                ok = self.update_chart(dashboard_id, cid, patch)
                results.append({"op": "update", "chart_id": cid, "ok": ok,
                                **({"error": "图表不存在或无变更可写"} if not ok else {})})
            except Exception as exc:  # noqa: BLE001
                logger.exception("batch_save update failed (dashboard=%s chart=%s)", dashboard_id, cid)
                results.append({"op": "update", "chart_id": cid, "ok": False, "error": str(exc)})
        for cid in [int(v) for v in (deleted or [])]:
            try:
                ok = self.delete_chart(dashboard_id, cid)
                results.append({"op": "delete", "chart_id": cid, "ok": ok,
                                **({"error": "图表不存在"} if not ok else {})})
            except Exception as exc:  # noqa: BLE001
                logger.exception("batch_save delete failed (dashboard=%s chart=%s)", dashboard_id, cid)
                results.append({"op": "delete", "chart_id": cid, "ok": False, "error": str(exc)})
        _invalidate_dashboard_payload(dashboard_id)
        return {"ok": all(r["ok"] for r in results), "errors": [], "results": results, "created": created}

    def _refresh_dataset_chart(self, chart: dict, params: dict,
                               user_id: int, workspace_id: int, username: str, cur) -> dict:
        """数据集图表(source_type='dataset'): 统一经数据集治理入口取数.

        不把数据集 SQL 复制进 adh_charts —— 保持数据集单口径(数据集改了图表即生效);
        取数经 dataset_service.query_dataset(governed_execute/七闸门), 无旁路。
        结果只回行列(护栏 §7), 执行 SQL/物理细节不入图表响应。
        """
        from backend.modules.viz.services import dataset_service
        if not user_id:
            raise NoIdentityError("缺少可信用户身份, 拒绝取数(数据合规护城河)")
        cfg = _json_loads_safe(chart.get("config")) or {}
        ds_params = {
            "filters": params.get("filters") or [],
            "order": params.get("order") or [],
            "limit": int(cfg.get("limit") or params.get("limit") or 500),
        }
        dims = params.get("dimensions") or cfg.get("dimensions") or []
        meas = params.get("measures") or params.get("metrics") or cfg.get("measures") or []
        if dims:
            ds_params["dimensions"] = dims
        if meas:
            ds_params["measures"] = meas
        # 看板参数(如日期区间 date_start/date_end)透传为数据集变量 ${...}，只取数据集已声明的
        # 变量名(避免把无关 param 当未声明参数使编译 fail-loud 报错)。
        args = _dataset_declared_args(int(chart["source_id"]), params)
        if args:
            ds_params["args"] = args
        # 行级 scope 按角色叠加, 角色由服务端解析(不信任请求体)
        from backend.common import auth as _auth
        live = _auth.get_user_by_id(user_id) or {}
        identity = {"user_id": user_id, "username": username,
                    "role": live.get("user_role") or "", "workspace_id": workspace_id}
        result = dataset_service.query_dataset(int(chart["source_id"]), ds_params, identity)
        data = {k: result[k] for k in ("columns", "rows", "row_count", "truncated") if k in result}
        cache = json.dumps(
            {"columns": data.get("columns") or [], "rows": data.get("rows") or []},
            ensure_ascii=False,
        )
        cur.execute(
            "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
            (cache, _now(), chart["id"]),
        )
        return data

    def refresh_chart(self, dashboard_id: int, chart_id: int, params: dict = None,
                      page_limit: int = None, page_offset: int = None,
                      count_sql: str = None, user_id: int = 0,
                      workspace_id: int = 0, username: str = "") -> dict:
        """Re-execute a chart's SQL query on its configured datasource.

        Supports param substitution, server-side pagination, and cache update.
        raw_sql 取数一律走治理护城河(身份由服务端传入, 无身份 fail-closed)。
        """
        from backend.core.query_executor import validate_sql

        if not check_dashboard_visible(dashboard_id, user_id):
            raise PermissionError("无权访问该仪表盘")

        params = params or {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,dashboard_id,workspace_id,source_type,source_id,semantic_query,query_source,sql_query,config FROM adh_charts "
                    "WHERE id = %s AND dashboard_id = %s",
                    (chart_id, dashboard_id),
                )
                chart = cur.fetchone()
                if not chart:
                    raise ValueError("图表不存在")

                if (_json_loads_safe(chart.get("config")) or {}).get("as_bot_design"):
                    return refresh_designed_chart(chart, user_id)
                if chart.get("source_type") == "dataset" and chart.get("source_id"):
                    return self._refresh_dataset_chart(chart, params, user_id, workspace_id, username, cur)
                sql = chart.get("sql_query", "")
                if not sql:
                    return {"columns": [], "rows": [], "row_count": 0}

                datasource_id = _get_chart_datasource_id(chart)
                sql = _substitute_params(sql, params)
                sql = sql.strip().rstrip(";")

                total = None

                if page_limit is not None:
                    # Count query
                    if count_sql:
                        count_sql = _substitute_params(count_sql, params).strip().rstrip(";")
                    else:
                        base_sql = re.sub(
                            r'\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$', '',
                            sql, flags=re.IGNORECASE,
                        ).strip()
                        count_sql = f"SELECT COUNT(*) AS cnt FROM ({base_sql}) _t"

                    ok, msg = validate_sql(count_sql, require_limit=False)
                    if ok:
                        try:
                            count_result = governed_execute(count_sql, datasource_id, user_id, workspace_id, username)
                            if count_result.get("rows"):
                                row = count_result["rows"][0]
                                total = (
                                    row.get("cnt")
                                    or row.get("count(*)")
                                    or row.get("COUNT(*)")
                                    or (list(row.values())[0] if row else 0)
                                )
                        except Exception as e:
                            logger.warning("[ChartRefresh] count query failed: %s", e)
                    else:
                        logger.warning("[ChartRefresh] count SQL validation failed: %s", msg)

                    sql = f"{sql} LIMIT {int(page_limit)} OFFSET {int(page_offset or 0)}"
                elif "limit" not in sql.lower():
                    sql += " LIMIT 500"

                # Validate SQL safety before execution
                ok, msg = validate_sql(sql)
                if not ok:
                    raise ValueError(f"SQL 校验失败: {msg}")

                logger.info(
                    "[ChartRefresh] chart_id=%s, params=%s, page_limit=%s, sql=%s",
                    chart_id, params, page_limit, sql,
                )

                result = governed_execute(sql, datasource_id, user_id, workspace_id, username)
                if total is not None:
                    result["total"] = total

                # Update data_cache
                now = _now()
                cache = json.dumps(
                    {"columns": result["columns"], "rows": result["rows"]},
                    ensure_ascii=False,
                )
                cur.execute(
                    "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
                    (cache, now, chart_id),
                )
                conn.commit()
                # 仅 data_cache 变化: 只失效该看板详情, 不动目录/组缓存
                _invalidate_dashboard_payload(dashboard_id, structural=False)
                return result
        finally:
            conn.close()

    def refresh_all_charts(self, dashboard_id: int, params: dict = None,
                           user_id: int = 0, workspace_id: int = 0,
                           username: str = "") -> dict:
        """Re-execute all charts' SQL in a dashboard on their respective datasources.

        取数走治理护城河; 无可信身份 -> fail-closed 整体拒绝(I5)。
        """
        from backend.core.query_executor import validate_sql

        if not user_id:
            raise NoIdentityError("缺少可信用户身份, 拒绝批量取数(数据合规护城河)")
        if not check_dashboard_visible(dashboard_id, user_id):
            raise PermissionError("无权访问该仪表盘")

        params = params or {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,dashboard_id,workspace_id,source_type,source_id,semantic_query,query_source,sql_query,config FROM adh_charts "
                    "WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()

                results = {}
                now = _now()

                for chart in charts:
                    cid = chart["id"]
                    if (_json_loads_safe(chart.get("config")) or {}).get("as_bot_design"):
                        results[cid] = refresh_designed_chart(chart, user_id)
                        continue
                    if chart.get("source_type") == "dataset" and chart.get("source_id"):
                        # 数据集图表走数据集治理入口(不复制 SQL, 单口径)
                        try:
                            results[cid] = self._refresh_dataset_chart(
                                chart, params, user_id, workspace_id, username, cur)
                        except Exception as e:  # noqa: BLE001  可诊断原因(query_dataset 已脱敏)
                            results[cid] = {"error": str(e)}
                        continue
                    sql = chart.get("sql_query", "")
                    if not sql:
                        results[cid] = {"columns": [], "rows": [], "row_count": 0}
                        continue

                    datasource_id = _get_chart_datasource_id(chart)
                    sql = _substitute_params(sql, params)
                    sql = sql.strip().rstrip(";")
                    if "limit" not in sql.lower():
                        sql += " LIMIT 500"

                    ok, msg = validate_sql(sql)
                    if not ok:
                        results[cid] = {"error": f"SQL 校验失败: {msg}"}
                        continue

                    logger.info(
                        "[ChartRefresh] chart_id=%s, params=%s, sql=%s",
                        cid, params, sql,
                    )

                    try:
                        result = governed_execute(sql, datasource_id, user_id, workspace_id, username)
                        results[cid] = result

                        cache = json.dumps(
                            {"columns": result["columns"], "rows": result["rows"]},
                            ensure_ascii=False,
                        )
                        cur.execute(
                            "UPDATE adh_charts SET data_cache = %s, updated_at = %s WHERE id = %s",
                            (cache, now, cid),
                        )
                    except Exception as e:
                        results[cid] = {"error": str(e)}

                conn.commit()
                _invalidate_dashboard_payload(dashboard_id, structural=False)
                return {"charts": results}
        finally:
            conn.close()

    def update_layout(self, dashboard_id: int, layouts: list) -> bool:
        """Batch update chart positions (layout save)."""
        if not layouts:
            return True

        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                for item in layouts:
                    chart_id = item.get("chart_id")
                    position = item.get("position", {})
                    if chart_id:
                        cur.execute(
                            "UPDATE adh_charts SET `position`=%s, updated_at=%s "
                            "WHERE id=%s AND dashboard_id=%s",
                            (json.dumps(position), now, chart_id, dashboard_id),
                        )
            conn.commit()
            _invalidate_dashboard_payload(dashboard_id)
            return True
        finally:
            conn.close()


# ── SnapshotService ──────────────────────────────────────────────────────────


class SnapshotService:
    """Chart snapshot save/list/get operations."""

    def save_snapshot(
        self,
        user_id: int,
        question: str,
        sql_query: str,
        chart_type: str,
        brief: str,
        columns: list,
        rows: list,
        row_count: int,
        datasource_id: int = 0,
    ) -> int:
        """Save a chart snapshot after successful query execution. Returns snapshot ID."""
        sid = _ts_id()
        now = _now()
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO adh_chart_snapshots "
                        "(`id`, `user_id`, `datasource_id`, `question`, `sql_query`, "
                        "`chart_type`, `brief`, `columns`, `data_snapshot`, "
                        "`row_count`, `created_at`) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            sid, user_id, datasource_id, question, sql_query,
                            chart_type, brief,
                            json.dumps(columns),
                            json.dumps(_sanitize_floats(rows[:500]), ensure_ascii=False),
                            row_count, now,
                        ),
                    )
                conn.commit()
                return sid
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Failed to save chart snapshot: %s", e)
            return 0

    def list_snapshots(self, user_id: int, days: int = 7) -> list:
        """Get recent chart snapshots from chat executions."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, question, sql_query, chart_type, brief, "
                    "`columns`, row_count, created_at "
                    "FROM adh_chart_snapshots "
                    "WHERE user_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                    "ORDER BY created_at DESC LIMIT 100",
                    (user_id, days),
                )
                rows = cur.fetchall()
                for r in rows:
                    if hasattr(r.get("created_at"), "isoformat"):
                        r["created_at"] = r["created_at"].isoformat()
                    if isinstance(r.get("columns"), str):
                        r["columns"] = json.loads(r["columns"])
                return rows
        finally:
            conn.close()

    def get_snapshot_data(self, snapshot_id: int, user_id: int) -> Optional[dict]:
        """Get full snapshot data including data rows."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_chart_snapshots WHERE id = %s AND user_id = %s",
                    (snapshot_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if hasattr(row.get("created_at"), "isoformat"):
                    row["created_at"] = row["created_at"].isoformat()
                if isinstance(row.get("columns"), str):
                    row["columns"] = json.loads(row["columns"])
                if isinstance(row.get("data_snapshot"), str):
                    row["data_snapshot"] = _sanitize_floats(json.loads(row["data_snapshot"]))
                return row
        finally:
            conn.close()


# ── Preview / Datasource Aggregation ────────────────────────────────────────


def preview_saved_query(user_id: int, source_type: str, source_id: int,
                        workspace_id: int = 0) -> dict:
    """Execute a saved query/dataset SQL and return preview data.

    元数据查询仍走 metadata 库, 但返回数据行的取数一律经统一治理护城河
    (敏感 block/mask + RLS + 审计), 无可信身份 -> fail-closed(I1/I2/I3/I5)。
    """
    from backend.core.query_executor import validate_sql

    if not source_id:
        return {"error": "Missing source_id"}

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            sql_query = None
            if source_type in ("query", "dataset"):
                cur.execute(
                    "SELECT sql_query FROM adh_saved_queries WHERE id = %s AND owner_id = %s",
                    (source_id, user_id),
                )
                row = cur.fetchone()
                if row:
                    sql_query = row.get("sql_query")

            if not sql_query:
                return {"error": "Query not found"}

            execute_sql = sql_query.strip().rstrip(";")
            if "limit" not in execute_sql.lower():
                execute_sql += " LIMIT 200"

            ok, msg = validate_sql(execute_sql)
            if not ok:
                return {"error": f"SQL 校验失败: {msg}"}

        return governed_execute(execute_sql, 0, user_id, workspace_id)
    except NoIdentityError:
        raise
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def list_datasource_aggregations(user_id: int) -> list:
    """Aggregate available data sources: snapshots, saved queries, datasets."""
    result = []
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Recent chart snapshots (last 7 days)
            cur.execute(
                "SELECT id, question AS name, chart_type, brief AS description, created_at "
                "FROM adh_chart_snapshots "
                "WHERE user_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
                "ORDER BY created_at DESC LIMIT 50",
                (user_id,),
            )
            for r in cur.fetchall():
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["type"] = "snapshot"
                result.append(r)

            # Saved queries
            cur.execute(
                "SELECT id, name, description, created_at FROM adh_saved_queries "
                "WHERE owner_id = %s AND (is_dataset = 0 OR is_dataset IS NULL) "
                "ORDER BY updated_at DESC LIMIT 50",
                (user_id,),
            )
            for r in cur.fetchall():
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["type"] = "query"
                r["chart_type"] = None
                result.append(r)

            # Saved datasets
            cur.execute(
                "SELECT id, name, description, created_at FROM adh_saved_queries "
                "WHERE owner_id = %s AND is_dataset = 1 "
                "ORDER BY updated_at DESC LIMIT 50",
                (user_id,),
            )
            for r in cur.fetchall():
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["type"] = "dataset"
                r["chart_type"] = None
                result.append(r)

        return result
    finally:
        conn.close()


# ── Module-level singletons ──────────────────────────────────────────────────

dashboard_service = DashboardService()
chart_service = ChartService()
snapshot_service = SnapshotService()
