"""Dashboard Service -- Business logic for dashboards, charts, and snapshots.

Migrated from backend/api/dashboard.py into service classes:
- DashboardService: Dashboard CRUD, reorder, copy
- ChartService: Chart CRUD, refresh, batch refresh, layout
- SnapshotService: Chart snapshot save/list/get

Uses shared DB connection from backend/common/db.
Tables: adh_dashboards, adh_charts, adh_chart_snapshots, adh_saved_queries
"""

import copy
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from typing import Optional

from backend.common.db import execute_query
from backend.common.db.metadata_db import get_metadata_conn
from backend.common import i18n
from backend.common.ttl_cache import dashboard_cache
from backend.core.governed_query import governed_execute, NoIdentityError

logger = logging.getLogger(__name__)


class ChartRefreshCancelled(RuntimeError):
    """刷新实例已取消/租约失效：丢弃当前结果，禁止继续写缓存。"""


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
    for field in ("layout", "filters", "params", i18n.I18N_COLUMN):
        row[field] = _json_loads_safe(row.get(field))
    # tags: JSON 列经 pymysql 回读为字符串, 与 layout/filters/params 同法解析; 归一为字符串数组
    row["tags"] = _clean_tags(row.get("tags"))
    for ts in ("created_at", "updated_at"):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    row.setdefault("is_default", 0)
    row.setdefault("carousel_interval", 0)
    row.setdefault("params", [])
    row.setdefault("status", "designing")
    return row


def _clean_tags(value) -> list:
    """标签归一: 接受 JSON 字符串/列表/None, 输出去空去重后的字符串数组(仅供管理页过滤, 无治理语义)。"""
    parsed = _json_loads_safe(value)
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    out: list = []
    for t in parsed:
        if t is None:
            continue
        s = str(t).strip()
        if s and s not in out:
            out.append(s)
    return out


def _normalize_chart(row: dict) -> dict:
    """Normalize chart row for JSON serialization."""
    for field in ("config", "position", "semantic_query", i18n.I18N_COLUMN):
        value = _json_loads_safe(row.get(field))
        if field in ("config", "position"):
            # text 列存过二次编码的 JSON（JSON 字符串里再包一层 JSON 文本），解析一次仍是 str；
            # 再解一层，否则下面 .get() 会让整个看板清单 500（一个脏图连坐整页）。
            if isinstance(value, str):
                value = _json_loads_safe(value)
            if value is not None and not isinstance(value, dict):
                logger.error("chart %s %s 不是 JSON 对象（%s），按空处理",
                             row.get("id"), field, type(value).__name__)
                value = {}
        row[field] = value
    for ts in ("created_at", "updated_at", "data_cache_at"):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    row.setdefault("source_type", "query")
    row.setdefault("source_id", None)
    row.setdefault("data_cache", None)
    row.setdefault("data_cache_at", None)
    row.setdefault("data_cache_key", None)
    row.setdefault("data_cache_scope", None)
    row.setdefault("query_source", "raw_sql")
    if (row.get("config") or {}).get("as_bot_design"):
        # 设计者的 SQL/预览不能经普通图表详情或共享缓存分发。
        row.pop("sql_query", None)
        row["data_cache"] = None
        row["data_cache_at"] = None
        row["data_cache_key"] = None
        row["data_cache_scope"] = None
    return row


# ── 双语本地化（消费面唯一入口，缓存之后做）──────────────────────────────
#: 为什么在**出缓存之后**做：DETAIL_KEY 载荷是语言无关的（同一看板全语言共享一份），
#: 把语言编进缓存键会让命中率减半并在缓存里存多份同源结构；在深拷贝后的副本上就地改写，
#: 缓存永不受某一请求语言污染。改写/回落行为与 `*_zh` 保留口径统一在 i18n.localize_display，
#: 本模块只决定哪些字段需要本地化。
_DASHBOARD_TEXT_FIELDS = ("name", "description")
_DASHBOARD_OVERLAYS = ("params",)   # 参数 label 与选项显示名在 params JSON 里
_CHART_TEXT_FIELDS = ("name",)      # 图表卡标题取 `name` 列（DashboardCanvas）
_CHART_OVERLAYS = ("config",)       # title / 说明卡 content / 控件 label 在 config 深处


def _localize_dashboard_row(dash: dict) -> None:
    """一个看板（含其图表）的就地本地化 + 回落记账。"""
    missing = i18n.localize_display(
        dash, fields=_DASHBOARD_TEXT_FIELDS, overlays=_DASHBOARD_OVERLAYS)
    for chart in dash.get("charts") or []:
        cm = i18n.localize_display(
            chart, fields=_CHART_TEXT_FIELDS, overlays=_CHART_OVERLAYS)
        if cm:
            chart["i18n_fallback_fields"] = cm
    if missing:
        dash["i18n_fallback_fields"] = missing


def _localize_dashboards(rows: list) -> list:
    for d in rows:
        _localize_dashboard_row(d)
    return rows


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

    补: description / updated_at(ISO) / chart_count(数据图表数) / tags(自由标签) / preview(画布尺寸 + 每图摘要)。
    tags 必须随目录下发：工作空间「数据看板」的组合内筛选靠它，缺了这个字段
    前端只能按名字模糊找（看板一多就翻不动）。
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
        # JSON 列经 pymysql 回读为字符串, 与 get_dashboard 同一归一口径(字符串数组, 去空去重)
        row["tags"] = _clean_tags(row.get("tags"))
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


def _get_chart_datasource_id(chart: dict) -> int:
    """Extract datasource_id from chart config."""
    config = chart.get("config")
    config = _json_loads_safe(config)
    if isinstance(config, dict):
        return config.get("datasource_id", 0)
    return 0


# ── 取数通道收口文案（2026-11-06拍板：图表只从数据集/声明式语义取数）───────────
# 存量库里仍可能有自带 SQL / 旧直连通道的图表（收编未覆盖、库外手写写入）。
# 既不能拿旧口径默默继续跑（那就等于旁路仍存在），也不能当空图刷（错数比无答案难查）：
# 指名要人去收编（no-silent-degradation）。
LEGACY_CHART_SQL_HINT = (
    "图表「{name}」仍携带旧版直写 SQL，该取数通道已下线。"
    "请到「数据集」页把这段口径定义成数据集（或选用已有的数据集），"
    "再在图表配置里改挂数据集。")
_LEGACY_SNAPSHOT_HINT = (
    "图表「{name}」是旧版快照/直连通道（不走数据集取数），该通道已下线。"
    "请在图表配置里改挂角色授权清单内的数据集。")
_SEMANTIC_UNPAGED_HINT = (
    "声明式语义图表不支持按页取数（语义层只产 LIMIT n，没有 OFFSET/COUNT 形状）；"
    "请改用数据集图表，或将该图的数据集关闭「支持分页」。")


def _execute_via_semantic(semantic_query: dict, params: dict, user_id,
                          workspace_id, datasource_id: int, limit=None) -> dict:
    """声明式语义图表取数（与 ChatBI run_semantic_query 同源，经 /api/semantic/query）。

    这类图表不携带人写的 SQL：存的是 SemanticQuery，SQL 由语义层编译并过七闸门。
    将 columns(对象数组)+ rows(二维数组) 转成图表缓存惯用的（列名列表 + dict 行），
    与数据集通道保持同一前端契约。SemanticError 冒泡给调用方（不回退其他通道）。
    """
    from backend.common.semantic_client import SemanticClient

    flat = _flatten_params(params)
    missing: set[str] = set()
    body = _substitute_semantic(dict(semantic_query), flat, missing)
    if missing:
        # 占位符没值时旧实现把 {{x}} 原样留在 filters/value 里（或整串匹配分支落回原样），
        # 于是那个字面量被当成一个**真实取值**去查 —— 出数且看着正常，其实条件写歪了。
        # 空串同样不行（等于把该条件筛成空值）。缺参必须报出来（no-silent-degradation）。
        raise ValueError(
            "本图的语义查询有 " + str(len(missing)) + " 个参数没给值："
            + "、".join(sorted(missing))
            + "。请在看板上补同名参数控件（或让控件绑到该参数），"
              "也可把该条件做成数据集入参后在数据集页定义；不按空值/字面量执行。")
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

    res = SemanticClient().query(body)
    cols = [c.get("name") for c in res.get("columns", [])]
    rows = [dict(zip(cols, r)) for r in res.get("rows", [])]

    prov = res.get("provenance", {}) or {}
    if prov.get("allowed") is False:
        raise ValueError(
            f"语义层闸门拦截于 '{prov.get('blocked_at')}': {res.get('warnings') or prov}"
        )

    return {
        "columns": cols,
        "rows": _sanitize_floats(rows),
        "row_count": res.get("row_count", len(rows)),
        "query_source": "semantic",
        "applied_rls": res.get("applied_rls", []),
    }


def _substitute_semantic(node, flat: dict, missing: "set[str] | None" = None):
    """递归把 SemanticQuery 里的 {{param}} 占位符替换为运行时参数值。

    未命中的键登记进 `missing`（由调用方一次性回抛清单），**不再原样留在查询里**：
    残留的 `{{x}}` 会被语义层当作真实取值，出一个"条件写歪但看着正常"的数。
    """
    if isinstance(node, dict):
        return {k: _substitute_semantic(v, flat, missing) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute_semantic(v, flat, missing) for v in node]
    if isinstance(node, str):
        m = re.fullmatch(r"\{\{\s*([\w.]+)\s*\}\}", node.strip())
        if m and m.group(1) in flat:
            return _coerce_param(flat[m.group(1)])

        def _r(mm):
            key = mm.group(1).strip()
            if key not in flat:
                if missing is not None:
                    missing.add(key)
                return mm.group(0)      # 由调用方据此报错，这里不改写值
            return str(flat[key])

        return re.sub(r"\{\{(\w+(?:\.\w+)*)\}\}", _r, node)
    return node


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


# ── 图表结果缓存的行/字节口径（数据集与语义两条写入通道共用）───────────────
# `data_cache` 的职责是“进看板时先渲染默认那一页”，不是取数结果的副本：
# 表值图即使本次刷新用的是 500 行大页，缓存也只留默认首页 pageSize 行（缺省 20）。
# 历史缺陷（2026-11-10 实测）：明细表 200 行宽文本把 TEXT 列（65,535 字节）顶到
# 65,506 字节，下一次刷新报 MySQL 1406 Data too long → 取数已经成功、整张图却显“刷新失败”。
TABLE_VALUE_DEFAULT_PAGE_SIZE = 20
# 与取数层 `es_pagination.PAGE_SIZE_MAX` 同一口径（表值图每页最大 100）：
# 缓存行上限不得比取数上限宽，否则旧配置能把大页又写回 TEXT 列。
TABLE_VALUE_PAGE_SIZE_MAX = 100
DATA_CACHE_MAX_BYTES = 60000   # 列上限 65535，留 UTF-8 与转义余量


def _cache_row_cap(chart_type: str, cfg: dict) -> "int | None":
    """表值图缓存只留默认首页；其他图型（聚合曲线/饼图）行小列少，保持全量。"""
    if str(chart_type or "") != "table_value":
        return None
    try:
        n = int((cfg or {}).get("pageSize") or TABLE_VALUE_DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        n = TABLE_VALUE_DEFAULT_PAGE_SIZE
    # 与取数层同一硬上限：旧配置残留的 500 行大页不能把缓存又顶回 1406
    return max(1, min(n, TABLE_VALUE_PAGE_SIZE_MAX))


def _paging_extra(result: dict) -> dict:
    """取数结果里的渲染元信息 → 要随首屏缓存一起落库的那几项。

    首屏渲染要从 `data_cache` 读出“这张图共多少行、按多大页取、能不能跳页”，
    少了它就只能拿行数反推 —— 会把首页当成全量（用户看到“共 20 行”而实际几百万）。
    字段显示格式同理：它只在取数那一次随结果下来，不写进缓存，首屏（不重查）
    就会比刷新后难看 —— 同一张图两种显示口径比统一难看更坑。
    """
    out: dict = {}
    if result.get("total") is not None:
        out["total"] = result["total"]
    if isinstance(result.get("paging"), dict):
        out["paging"] = result["paging"]
    if result.get("truncated"):
        out["truncated"] = True
    if result.get("unpaged"):
        # “本次结果即全部”随缓存一起带：小结果（没被首页行限截到）重载页面即可直接判读，
        # 不必再补一次重查。缓存真装不下全部行时由 `_chart_cache_json` 把这条抹掉，
        # 不会拿 20 行预览冒充 88 行全量
        out["unpaged"] = True
    if isinstance(result.get("field_formats"), dict) and result["field_formats"]:
        out["field_formats"] = result["field_formats"]
    return out


def _resolve_chart_page(chart: dict, page_size, page_num) -> tuple["int | None", "int | None"]:
    """表值图恒定按页取数：页大小缺省取图表配置 pageSize，页号缺省首页。

    为什么在服端兜底而不是让前端必传：看板首屏走 `data_cache`、整板重查走
    `refresh_all_charts`（不逐图带页参），两边必须拿到同一口径的“默认首页”；
    只在前端传时才分页 = 不传就拉全量，正是伪分页的源头。
    其他图型不注入分页（给曲线图套 20 行页限会把趋势砍没）；
    pageSize 越界收到 ≤100（与取数层同一硬上限，档位之外不放开）。
    """
    if str(chart.get("chart_type") or "") != "table_value":
        return None, None
    cfg = _json_loads_safe(chart.get("config")) or {}
    size = page_size or cfg.get("pageSize") or TABLE_VALUE_DEFAULT_PAGE_SIZE
    try:
        size = max(1, min(int(size), TABLE_VALUE_PAGE_SIZE_MAX))
    except (TypeError, ValueError):
        size = TABLE_VALUE_DEFAULT_PAGE_SIZE
    try:
        num = max(1, int(page_num or 1))
    except (TypeError, ValueError):
        num = 1
    return size, num


def _chart_cache_json(chart: dict, columns, rows, extra: dict | None = None) -> str:
    """取数结果 → 要写进 data_cache 的 JSON（行限 + 字节兜底，单一实现）。"""
    cfg = _json_loads_safe(chart.get("config")) or {}
    kept = list(rows or [])
    cap = _cache_row_cap(str(chart.get("chart_type") or ""), cfg)
    if cap is not None and not (extra or {}).get("unpaged"):
        kept = kept[:cap]
    # 服务端声明“本次即全部行”（统计型数据集）时不按首页行限截：装得下就整份留着，
    # 首屏因此不必补一次重查（转圈的源头）；装不下由下面的字节预算逐行退，
    # 退过行就把这条事实抹掉（见下方 reconciliation），不拿预览冒充全量
    cache = {"columns": list(columns or []), "rows": kept}
    if extra:
        cache.update(extra)
    text = json.dumps(cache, ensure_ascii=False)
    # 宽文本列（操作明细等）连默认首页也可能顶到上限：逐行退到预算内，至少留 1 行
    while len(text.encode("utf-8")) > DATA_CACHE_MAX_BYTES and len(cache["rows"]) > 1:
        cache["rows"].pop()
        text = json.dumps(cache, ensure_ascii=False)
    # 缓存只留默认首页（页限 + 字节预算）：真被截过行，“本次即全量”这句话就不再成立，
    # 必须把它从缓存里抹掉 —— 渲染面据此补一次真取数（留着它会把 20 行预览当 88 行全量）
    if cache.get("unpaged") and len(cache["rows"]) < len(rows or []):
        cache.pop("unpaged")
        text = json.dumps(cache, ensure_ascii=False)
    if len(text.encode("utf-8")) > DATA_CACHE_MAX_BYTES:
        # 单行就超限：不截字段、不拿半行冒充整行，指名让出题面收口（不静默降级）
        raise ValueError(
            f"图表「{chart.get('name') or chart.get('id')}」单行结果已超过缓存上限"
            f"（{DATA_CACHE_MAX_BYTES} 字节），无法写入首屏预览。请在表值图配置里减少列数或"
            "收窄长文本列（取数本身已成功，刷新后可正常查看）。")
    return text


def _chart_cache_key(chart: dict, params: dict | None = None, scope: str = "") -> str:
    """图表级缓存键：图表口径 + 默认参数 + 安全作用域。"""
    raw = json.dumps({
        "chart_id": int(chart.get("id") or 0),
        "source_type": chart.get("source_type"),
        "source_id": chart.get("source_id"),
        "config": _json_loads_safe(chart.get("config")) or {},
        "semantic_query": _json_loads_safe(chart.get("semantic_query")) or {},
        "params": params or {},
        "scope": scope or "",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _expected_cache_key(chart: dict, board_key: str, scope: str) -> str:
    """整板基键 + 当前图表口径 + 安全作用域，读侧与写侧必须同源。"""
    raw = f"{board_key}:{_chart_cache_key(chart, {}, scope)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_chart_cache(chart: dict, columns, rows, extra: dict | None = None,
                      cache_key: str | None = None, cache_scope: str = "",
                      refresh_run_id: int | None = None) -> None:
    """写图表首屏缓存（**短连接**：只在 UPDATE 这一瞬占用元数据库连接）。

    为什么不复用调用方的连接：取数可能卡在数据源上几十秒（实测单图 20~28s），而元
    数据库连接池是 max 8、全应用共享、blocking。"取数期间持着连接"等于让一批什么都
    不干的连接把池占满 —— 一块板 24 图并发刷新时，主应用的查询/保存全部排队（实测
    8 路并发刷新 24 图 15 分钟不返回，是等连接不是慢）。连接只用于写缓存，写完立刻归还。
    """
    cache = _chart_cache_json(chart, columns, rows, extra)
    key = _expected_cache_key(chart, cache_key or "", cache_scope)
    cache_at = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if refresh_run_id is not None:
                cur.execute(
                    "UPDATE adh_charts c JOIN adh_dashboard_refresh_runs r "
                    "ON r.dashboard_id=c.dashboard_id AND r.id=%s AND r.status='running' "
                    "SET c.data_cache=%s,c.data_cache_at=%s,c.data_cache_key=%s,c.data_cache_scope=%s,"
                    "c.updated_at=%s WHERE c.id=%s",
                    (int(refresh_run_id), cache, cache_at, key, cache_scope, _now(), chart["id"]))
                if cur.rowcount != 1:
                    raise ChartRefreshCancelled("刷新实例已取消或租约失效，结果已丢弃")
            else:
                cur.execute(
                    "UPDATE adh_charts SET data_cache=%s,data_cache_at=%s,data_cache_key=%s,"
                    "data_cache_scope=%s,updated_at=%s WHERE id=%s",
                    (cache, cache_at, key, cache_scope, _now(), chart["id"]))
        conn.commit()
    finally:
        conn.close()


def _cache_chart_result(chart: dict, result: dict, cache_key: str | None = None,
                        cache_scope: str = "", refresh_run_id: int | None = None) -> None:
    """把取数结果写进 adh_charts.data_cache(不另开一份口径, 只刷缓存)。"""
    _write_chart_cache(chart, result.get("columns"), result.get("rows"),
                       _paging_extra(result), cache_key=cache_key, cache_scope=cache_scope,
                       refresh_run_id=refresh_run_id)


# ── 模板示例数据（demo chart）──────────────────────────────────────────────────
DEMO_DATA_KEY = "demoData"
DEMO_SAMPLE_MAX_ROWS = 1000


def is_demo_chart(chart_or_config) -> bool:
    """模板导入且尚未绑定真实取数的图表（卡片展示预设演示数值）。"""
    cfg = chart_or_config.get("config") if isinstance(chart_or_config, dict) else None
    if isinstance(chart_or_config, dict) and cfg is None:
        cfg = chart_or_config
    cfg = _json_loads_safe(cfg)
    return isinstance(cfg, dict) and cfg.get(DEMO_DATA_KEY) is True


def demo_sample_without_source(chart: dict) -> bool:
    """样式展示图（示例标记）且未绑任何取数源 —— 不下发 SQL，原样回读示例缓存。

    仪表盘模板是**图表样式展示**载体：图卡不携带取数口径，只有内置演示数值。
    此时去执行 SQL 只会拿到空结果并把用户的样式效果抹成空白（用户得不到解释，
    no-silent-degradation.md）；而一旦用户绑了数据集/写了口径，服务端已同批清掉
    示例标记（_apply_demo_to_live_transition），不再走本分支。

    已挂数据集（source_type='dataset' + source_id）同样算“已绑源”：本判定原先只看
    旧版 config.datasource_id，不改这里会出现“数据集图表永远回读演示缓存”（挂上源却不取数）。
    """
    if not is_demo_chart(chart):
        return False
    if str(chart.get("source_type") or "") == "dataset" and chart.get("source_id"):
        return False
    return not _get_chart_datasource_id(chart)


def _demo_cached_result(chart: dict) -> dict:
    """示例图表未绑源时的刷新口径：原样回读已存示例数据（不执行演示 SQL、不清缓存）。"""
    cache = _json_loads_safe(chart.get("data_cache")) or {}
    columns = cache.get("columns") or []
    rows = cache.get("rows") or []
    data = {"columns": columns, "rows": rows, "row_count": len(rows), "demo": True}
    if str(chart.get("chart_type") or "") == "table_value":
        # 示例图未绑源：回读的就是全部示例行，直接说“本次即全量”，
        # 免得渲染面把它当状态未知、每次进看板都补一次无意义重查
        data["unpaged"] = True
    return data


def _serialize_sample_data(value, config) -> "str | None":
    """客户端提交的图表数据缓存只接受**模板示例数据**（护栏 §7：数据行不得由客户端注入）。

    要求 config.demoData is True、形状为 {columns: [...], rows: [...]}、行数受限；
    不合规一律拒并回可诊断原因（不静默丢弃 —— 否则用户以为看板有数据）。
    """
    if value is None:
        return None
    if isinstance(config, dict) and config.get(DEMO_DATA_KEY) is not True:
        raise ValueError("客户端写入图表数据缓存仅允许模板示例数据（config.demoData=true）")
    data = _json_loads_safe(value)
    if not isinstance(data, dict) or not isinstance(data.get("columns"), list) \
            or not isinstance(data.get("rows"), list):
        raise ValueError("示例数据格式无效：期望 {columns: [...], rows: [...]} 结构")
    if len(data["rows"]) > DEMO_SAMPLE_MAX_ROWS:
        raise ValueError(f"示例数据行数超限（最多 {DEMO_SAMPLE_MAX_ROWS} 行）")
    return json.dumps({"columns": data["columns"], "rows": data["rows"]}, ensure_ascii=False)


# 看板 params 里这些键是**取数上下文**，不是业务参数，不得当作逻辑参数值喂给数据集
_NON_PARAM_KEYS = frozenset({
    "filters", "order", "limit", "dimensions", "measures", "metrics",
    "preferred_datasource_id", "model_id", "user_id", "workspace_id",
    "page_size", "page_num", "page_limit", "page_offset", "page_count_sql", "count_sql",
})


def _dataset_bound_args(dataset_id: int, params: dict) -> tuple[dict, dict]:
    """看板逻辑参数值 → 该数据集正文需要的 `${}` 渲染参数（唯一翻译点）。

    旧实现是"同名才透传"（`k in declared`），于是数据集把时间窗口写成
    `start_time/end_time`、看板控件给的是 `date_start/date_end` 时，参数被**静默丢弃**，
    正文按默认值出数——用户看到一个"筛过了"的假象，比报错难查得多（2026-11-06 收口）。
    现在按声明的形状翻译（区间端点 / 相对天数 / 单值筛选），并把翻译结果之外的
    事实（哪些入参没给值、哪些按相对窗口派生了）原样回传给调用方显示。
    """
    if not dataset_id or not params:
        return {}, {"unbound": [], "notes": [], "skipped": 0}
    from backend.modules.viz.services import dataset_service
    from backend.semantics import param_plan

    ds = dataset_service.get_dataset(dataset_id)
    if not ds:
        return {}, {"unbound": [], "notes": [], "skipped": 0}
    values = {k: v for k, v in params.items() if k not in _NON_PARAM_KEYS}
    return param_plan.bound_args(ds, values)


# 图表 config 里随意图一起存的取数口径键（直建通道写入）：刷新时补回数据集入参。
# 相对时间窗（近 7 天/本月/今年）必须每次刷新按**当前时刻**重算 —— 把绝对日期
# 固化进数据集/图表就等于“建屏时对、第二天错”。
_INTENT_PASSTHROUGH_KEYS = ("time_window", "time_column", "time_grain",
                            "time_range", "time_start", "time_end")


def _chart_effective_source(data: dict, chart_id: int) -> dict:
    """patch 常只带局部字段（只改 source_id 或只改标题）→ 回读现有行拼出有效口径。"""
    row = (execute_query(
        "SELECT name, chart_type, source_type, source_id, sql_query, query_source, "
        "semantic_query, config FROM adh_charts WHERE id = %s", (chart_id,),
        fetchone=True) or {}) if chart_id else {}
    merged = {}
    for key in ("name", "chart_type", "source_type", "source_id", "sql_query",
                "query_source", "semantic_query", "config"):
        if data and data.get(key) is not None:
            merged[key] = data[key]
        elif row.get(key) is not None:
            merged[key] = row[key]
        else:
            merged[key] = data.get(key) if data else None
    return merged


# 不携带取数口径的图表形态：占位空图/拖入的字模图表卡/模板示例图/控件
_NON_DATA_SOURCES = {"", "empty", "template", "widget"}
# 已下线的旧取数通道（不带数据集口径）：写入口一律指名收编，不给“挂得上”的空承诺
_LEGACY_SQL_CHANNELS = {"query", "snapshot"}


def _semantic_intent(chart_or_eff: dict):
    """声明式语义图的可执行意图：`query_source='semantic'` 且 semantic_query 带 object。

    取数分派只认这一种严格形状（拿存量 semantic_query 去代替人写的 SQL 执行会默默
    换口径，比报错更难查）；形状不对就是不可执行，回到下面的旧通道判定。
    """
    if str(chart_or_eff.get("query_source") or "") != "semantic":
        return None
    sem = _json_loads_safe(chart_or_eff.get("semantic_query"))
    return sem if isinstance(sem, dict) and sem.get("object") else None


def _is_server_generated_channel(chart_or_eff: dict) -> bool:
    """SQL 由服务端生成的两条通道：声明式语义（语义层编译）与 AS-BOT 设计产物。

    写入口把关与复制过滤都走本函数：“图上行里有 SQL”不等同于“人在看板上写了 SQL”
    （AS-BOT 大屏工具存的就是自己编译出的 SQL）。注意它与 `_semantic_intent` 的宽窄
    有意不同：写入端宁可宽（带语义意图就不当人写 SQL 拒），执行端必须严（只按声明式
    意图取数），两者合并会造成“存时过、执行时默默换口径”。
    """
    cfg = _json_loads_safe(chart_or_eff.get("config"))
    if isinstance(cfg, dict) and cfg.get("as_bot_design"):
        return True
    return (str(chart_or_eff.get("query_source") or "") == "semantic"
            or bool(chart_or_eff.get("semantic_query")))


def assert_chart_data_source(data: dict, identity: dict, *, chart_id: int = 0) -> None:
    """看板图表取数写入口的唯一把关（2026-11-06 拍板：图表只能从数据集取数）。

    四条判定（缺一不可）：
      1) **取数通道唯一**：不接受图表自带 SQL（`sql_query`）与旧直连/快照通道
         （source_type='query'/'snapshot'）——口径编写收口到数据集（BI 治理建模层，
         有字段声明/认证/血缘/角色清单），看板只做可视化装配；
      2) **数据集在角色授权清单内**（`role_service.get_user_allowed_datasets`，
         fail-closed：空授权 = 不可挂，不解释为全量；admin 代码层 bypass），
         与编辑器/组件库下拉的唯一数据源同口径——不得出现"清单里看不到但能挂上"的双口径；
      3) **至少有一个可执行源**：可用范围 ∩ 角色授权源 ≠ ∅，否则不开"挂得上、刷新必报错"
         的空承诺入口（生效源由 `resolve_effective_source` 同口径定）；
      4) 数据集必须存在且 active。

    不受本闸门约束的形态（各有自己的把关人，不得拿本条一刀切）：
      - 控件（widget_*）：无取数口径；
      - 模板示例图（config.demoData=true）：样式展示载体，演示数值不下发查询；
      - AS-BOT 设计产物（config.as_bot_design）与声明式语义图（query_source='semantic'）：
        SQL 由服务端生成，设计面板是护栏 §7 的显式例外通道。
    错误文案只含业务名（护栏 §7）并给出出路。
    """
    eff = _chart_effective_source(data, chart_id)
    chart_type = str(eff.get("chart_type") or "").strip()
    if chart_type.startswith("widget_"):
        return

    cfg = _json_loads_safe(eff.get("config")) or {}
    if isinstance(cfg, dict) and cfg.get("as_bot_design"):
        return                                     # AS-BOT 设计通道自有关门（属主 + dashboard:manage）
    if _is_server_generated_channel(eff):
        # 声明式语义图：行上的 SQL 是语义层编译产物（AS-BOT 大屏工具就是这么存行），
        # 不是人写口径 —— 本闸门只封“人在看板上编 SQL”。此分支必须先于下面的 SQL 判定，
        # 否则服务端自己生成的口径会被当成旧通道拒掉（与 `_refresh_by_channel` 同序）。
        return

    # 提交了 SQL、或库里存量就带着 SQL，都要指名收编：
    # 不得拿“本次没改 SQL”当理由让旧口径继续挂在图上（不然存时不报错、刷时才坏）
    sql = str((data or {}).get("sql_query") or "").strip() or str(eff.get("sql_query") or "").strip()
    if sql:
        raise ValueError(
            "看板图表不再接受直接编写 SQL：取数口径请先到「数据集」页定义（或选用已有的数据集），"
            "然后在图表配置里改挂数据集。")

    source_type = str(eff.get("source_type") or "").strip()
    dataset_id = int(eff.get("source_id") or 0)
    if source_type in _NON_DATA_SOURCES:
        return                                     # 占位/示例图尚未绑数据，不报错（不给空承诺也不拦新建）
    if source_type in _LEGACY_SQL_CHANNELS:
        # 不带 SQL 的旧通道（快照/直连）：同样不得继续当能用的通道，指名改挂数据集
        raise ValueError(_LEGACY_SNAPSHOT_HINT.format(name=eff.get("name") or "未命名图表"))
    if source_type != "dataset":
        raise ValueError(
            f"看板图表只能从数据集取数（当前通道：{source_type or '未设置'}）："
            "请在图表配置里选择数据集，或从组件库「数据集」页引入。")
    if not dataset_id:
        raise ValueError("数据集图表缺少数据集标识，请重新从数据集清单选择。")
    if not int((identity or {}).get("user_id") or 0):
        raise NoIdentityError("缺少可信用户身份，不能挂数据集图表（数据合规护城河）")

    from backend.modules.viz.services import dataset_service

    ds = dataset_service.get_dataset(dataset_id)
    if not ds:
        raise ValueError("图表引用的数据集不存在，请重新从数据集列表引入")
    if ds.get("status") != "active":
        raise ValueError(f"数据集「{ds.get('name')}」已停用，不能挂到看板上；"
                         "请启用它或改用其他数据集。")
    # 可见性唯一裁决（与数据集页/取数口同源）：属主可挂自己的 private 数据集，
    # workspace 行按角色授权，admin 全可见。
    if not dataset_service.dataset_visible_to(ds, identity):
        if dataset_service.normalize_visibility(ds.get("visibility")) == "private":
            raise ValueError(
                f"数据集「{ds.get('name')}」是他人创建的个人数据集（仅创建者可见），不能挂到看板。"
                "请让创建者在数据集页发布共享（按角色授权），或改用自己被授权的数据集。")
        raise ValueError(
            f"当前角色未被授权数据集「{ds.get('name')}」，不能挂到看板。"
            "可挂清单来自「角色权限 → 数据集」；请联系管理员授权，"
            "或把本看板开放给该角色时一并勾选数据集。")

    scope, missing = dataset_service.usable_scope(ds, identity)
    if not scope:
        detail = (f"（可用数据源范围里的源已不存在：{'、'.join(missing)}，请在数据集页重新选择）"
                  if missing else "")
        raise ValueError(
            f"当前角色无权执行数据集「{ds.get('name')}」的任何可用数据源{detail}；"
            f"请找管理员补角色数据源授权，或改用已有权限的数据集。")

    # 看板级选源钉选校验：config.datasource（业务名）必须落在「可用范围 ∩ 授权源」内，
    # 否则保存后每次刷新必报错（空承诺）。未钉（空）= 交服务端定源，放行。
    # 与取数侧 `resolve_effective_source` 同一裁决口径（不开第二套判断）。
    pinned = str(cfg.get("datasource") or "").strip()
    if pinned and pinned not in {s.get("name") for s in scope}:
        raise ValueError(
            f"图表指定的数据源「{pinned}」不在数据集「{ds.get('name')}」当前可执行范围内"
            f"（可执行：{dataset_service._scope_label(scope)}）。"
            "请在图表配置里改选一个可执行的数据源，或改回「自动（服务端定源）」。")


def chart_identity_from_user(user: dict) -> dict:
    """图表写入口的服务端可信身份（只取 JWT，不取请求体；护栏 §2）。

    数据源/数据集授权按 `adh_user_roles` 裁决（与 workspace 无关），workspace_id 仅作
    数据集服务内部取用上下文，取不到则为 0（不影响源级 RBAC 口径）。
    与 datasets 写入口 `_identity` 同一形状，两处不得各写一套字段名。
    """
    return {
        "user_id": int((user or {}).get("user_id") or 0),
        "username": (user or {}).get("username") or "",
        "role": (user or {}).get("role") or (user or {}).get("user_role") or "",
        "workspace_id": int((user or {}).get("workspace_id") or 0),
    }


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
CARDS_KEY = "dash:cards:v4"            # 共享: 目录行 + 卡片预览摘要（v4: 补 tags 下发; v3: blocks → charts 摘要）
GROUPS_KEY = "dash:groups:v2"          # 共享: 组 + 组内看板 + 组→角色绑定
DETAIL_KEY = "dash:detail:v2:{}"       # 共享: 单看板完整载荷(v2 起含刷新管理能力与缓存 TTL 元数据)
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
                        "semantic_query=%s,query_source=%s,workspace_id=%s,data_cache=NULL,data_cache_at=NULL,"
                                                "data_cache_key=NULL,data_cache_scope=NULL,updated_at=UTC_TIMESTAMP() "
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


# ── 看板清单唯一口径（全量清单 / 分页清单 / 总数 COUNT / 标签候选共用一份）────────
#: 排序必须带唯一 tiebreaker：reorder/move 会把一批行写成同一个 updated_at，
#: LIMIT/OFFSET 在并列行上会翻出重行或漏行（分页清单的隐形错数源）。
_DASHBOARD_ORDER_BY = " ORDER BY is_default DESC, sort_order ASC, updated_at DESC, id DESC"
#: 元数据清单每页硬上限（服务端说了算，与字模库/资讯分页同口径）
_DASHBOARD_PAGE_SIZE_MAX = 200
_DASHBOARD_PAGE_SIZE_DEFAULT = 20
#: 清单响应需要的看板列（与 init.sql + tags/i18n/workspace 迁移对齐；少列会让
#: _normalize_dashboard/_mark_editable/本地化静默拿到默认值）
_DASHBOARD_LIST_COLS = ("id, name, description, layout, filters, params, tags, status, owner_id,"
                        " is_public, is_default, carousel_interval, sort_order,"
                        " created_at, updated_at, workspace_id, i18n")
#: 分页清单的图表瘦列：缩略图只用 id/chart_type/config/position，卡片计数只用 chart_type，
#: name/i18n 供标题本地化。sql_query/data_cache/semantic_query 属单看板详情载荷，
#: 不得进清单（缓存正文就是这条响应最重的部分），也不得让瘦行流入编辑/取数路径。
_CHART_SLIM_COLS = "id, dashboard_id, name, chart_type, config, position, i18n"
#: 看板状态可筛选值（与前端 DASHBOARD_STATUS_MAP 同源，越界值直接拒）
_DASHBOARD_STATUSES = ("designing", "enabled", "closed")


def _like_pattern(value: str) -> str:
    """搜索词转义 LIKE 元字符后包成包含式模式（未转义的 % _ 会变成通配符，误命中且无法排查）。"""
    esc = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _build_dashboard_filters(workspace_id: int = 0, visible: Optional[set] = None, *,
                             search: str = "", status: str = "", tags=(),
                             favorite_ids=()) -> tuple:
    """把清单的全部筛选/搜索谓词下推成 (WHERE 片段, 参数)。

    - visible=None = admin 全可见（不加 id IN）；空集由调用方先行 fail-closed 返回空清单；
    - favorite_ids 是浏览器 localStorage 的收藏，只当**过滤条件**（不参与权限），
      调用前必须已与可见集求交，服务端不信任前端递来的 id；
    - tags 多选为 OR（命中任一标签即显示），用 JSON_CONTAINS 精确匹配数组元素，
      不用 tags LIKE（子串会误命中，如 "订" 命中 "订单管理"）。
    """
    conds: list = []
    params: list = []
    if workspace_id:
        conds.append("workspace_id = %s")
        params.append(workspace_id)
    if visible is not None:
        conds.append("id IN (" + ",".join(["%s"] * len(visible)) + ")")
        params.extend(sorted(int(i) for i in visible))
    favs = sorted({int(i) for i in favorite_ids if str(i).strip() != ""})
    if favs:
        conds.append("id IN (" + ",".join(["%s"] * len(favs)) + ")")
        params.extend(favs)
    if search:
        like = _like_pattern(search)
        conds.append("(name LIKE %s OR description LIKE %s)")
        params.extend([like, like])
    if status:
        if status not in _DASHBOARD_STATUSES:
            raise ValueError(f"未知看板状态: {status}, 可选: {', '.join(_DASHBOARD_STATUSES)}")
        conds.append("status = %s")
        params.append(status)
    cleaned = [str(t).strip() for t in tags if str(t).strip()]
    if cleaned:
        conds.append("(" + " OR ".join(["JSON_CONTAINS(COALESCE(tags, '[]'), %s)"] * len(cleaned)) + ")")
        params.extend(json.dumps(t, ensure_ascii=False) for t in cleaned)
    return (" WHERE " + " AND ".join(conds)) if conds else "", params


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
            "SELECT id, name, description, status, is_default, sort_order, updated_at, owner_id, filters, tags "
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
    cards_by_id = {int(c["id"]): c for c in cards}
    for g in groups:
        # 组内顺序 = 管理端编排顺序(adh_dashboard_group_items.sort, _groups_structure 已按 sort 读出)。
        # 不得回退成按目录全局序(is_default→sort_order)过滤: 那样管理员在组合里排出来的顺序
        # 会被抹平, 点组合进哪块板 / 组内切换条左右 / 大屏轮播依次播放全都对不上编排意图。
        # 可见集只做交集过滤(fail-closed), 不改变相对次序。
        ordered = [cards_by_id[int(did)] for did in struct["items"].get(str(g["id"]), [])
                   if int(did) in cards_by_id]
        member_ids = {int(c["id"]) for c in ordered}
        grouped_ids |= member_ids
        out.append({"id": g["id"], "name": g["name"], "description": g.get("description") or "",
                    "sort": g.get("sort") or 0,
                    "dashboards": ordered})
    out.append({"id": 0, "name": "未分组", "description": "未加入任何仪表盘组的可见看板",
                "sort": 9999, "dashboards": [c for c in cards if c["id"] not in grouped_ids]})
    return out


def reorder_groups(orders: list) -> bool:
    """更新仪表盘组展示顺序(管理端「看板组合」卡片上移/下移)。

    只写 sort 一列: 整行 UPDATE 会把 name/description 抹成空值(与既有「幂等 upsert 覆盖列」
    同一类缺陷), 顺序本身也是编排的一部分, 逐条按 id 定位才谈得上可诊断。
    顺序写在库里即数据事实, 不做「前端本地排序兜底」—— 写失败必须抛出给管理员看。
    """
    if not orders:
        return True
    from backend.common.db import execute_query
    # 存在性只能靠 SELECT 判：MySQL 对「值未变的行」UPDATE 回 0 行，拿 rowcount 当存在性
    # 会把“原样重提交/部分未变”误报成组合不存在（与 update_group 同一历史坑）。
    known = {int(r["id"]) for r in (execute_query("SELECT id FROM adh_dashboard_groups") or [])}
    missing = [int(item.get("id") or 0) for item in orders if int(item.get("id") or 0) not in known]
    if missing:
        # 组合已被别人删掉：静默跳过会让这次排序整体丢一半，必须报错提醒重试
        raise ValueError(f"{len(missing)} 个仪表盘组已不存在，排序未保存，请刷新后重试")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for item in orders:
                cur.execute("UPDATE adh_dashboard_groups SET sort=%s WHERE id=%s",
                            (int(item.get("sort", 0) or 0), int(item["id"])))
        conn.commit()
        _invalidate_dashboard_payload()
        return True
    finally:
        conn.close()


class DashboardGroupNotFound(ValueError):
    """仪表盘组不存在（人或 AI 触发同一异常，端点回 404、工具回可操作提示）。"""


def list_group_rows() -> list:
    """全部仪表盘组原始行（按编排序）。成员/角色由 `group_members` 单独取，不在这里逐行查。"""
    return execute_query("SELECT * FROM adh_dashboard_groups ORDER BY sort, id") or []


def group_exists(group_id: int) -> bool:
    return bool(execute_query("SELECT id FROM adh_dashboard_groups WHERE id=%s",
                              (int(group_id),), fetchone=True))


def require_group(group_id: int) -> int:
    gid = int(group_id)
    if not group_exists(gid):
        raise DashboardGroupNotFound(f"仪表盘组不存在（id={gid}）")
    return gid


def group_members(group_id: int) -> dict:
    """组内看板 id（编排序，剔除已删除看板）+ 绑定角色 id。

    只回仍存在的看板：已删除看板不得进管理端组合清单/穿梭框（否则出现"看板已删除"悬空项）。
    按 adh_dashboards 全局存在性过滤（不按工作空间），以免把"存在但不在当前工作空间清单"
    的看板误判为已删除。
    """
    gid = int(group_id)
    dashboard_ids = [r["dashboard_id"] for r in (execute_query(
        "SELECT gi.dashboard_id FROM adh_dashboard_group_items gi "
        "JOIN adh_dashboards d ON d.id = gi.dashboard_id "
        "WHERE gi.group_id=%s ORDER BY gi.sort, gi.id", (gid,)) or [])]
    role_ids = [r["role_id"] for r in (execute_query(
        "SELECT role_id FROM adh_role_dashboard_groups WHERE group_id=%s", (gid,)) or [])]
    return {"dashboard_ids": dashboard_ids, "role_ids": role_ids}


def create_group(name: str, description: str = "", sort: Optional[int] = None) -> int:
    """新建仪表盘组，返回组 id。

    新组合默认排末尾(COALESCE(MAX(sort),0)+10): 固定写 0 会把刚建好的顺序顶掉一位，
    管理员"新建完再排"会变成"新建自动插队到第一"。显式传 sort 时尊重传值。
    """
    if sort is None:
        row = execute_query("SELECT COALESCE(MAX(sort), 0) AS max_sort FROM adh_dashboard_groups",
                            fetchone=True) or {}
        sort_value = int(row.get("max_sort") or 0) + 10
    else:
        sort_value = int(sort)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO adh_dashboard_groups (name, description, sort) VALUES (%s, %s, %s)",
                        (name, description or "", sort_value))
            group_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    _invalidate_dashboard_payload()
    return group_id


def update_group(group_id: int, name: str, description: Optional[str] = "",
                 sort: Optional[int] = None) -> None:
    """更新组名/说明/排序。

    存在性必须用 SELECT 判（与 /items、/roles 同口径）：拿 UPDATE 的影响行数当存在性
    会把"名称/说明/排序没改动的重复保存"当成 0 行受影响 → 误报「仪表盘组不存在」
    （MySQL 对值未变的行不计数，历史缺陷：用户在编辑弹窗里原样再点保存必报错）。
    未传 sort/description 时保留原值（只改名称不得把说明清空、不得把顺序抬回 0 = 首位）。
    """
    from backend.common.db import execute_write
    gid = require_group(group_id)
    current = execute_query("SELECT sort, description FROM adh_dashboard_groups WHERE id=%s",
                            (gid,), fetchone=True) or {}
    sort_value = int(sort) if sort is not None else int(current.get("sort") or 0)
    description_value = (current.get("description") or "") if description is None else (description or "")
    execute_write("UPDATE adh_dashboard_groups SET name=%s, description=%s, sort=%s WHERE id=%s",
                  (name, description_value, sort_value, gid))
    _invalidate_dashboard_payload()


def delete_group(group_id: int) -> None:
    """删组及其成员/角色绑定（不删看板本身）。"""
    from backend.common.db import execute_write
    gid = int(group_id)
    execute_write("DELETE FROM adh_dashboard_group_items WHERE group_id=%s", (gid,))
    execute_write("DELETE FROM adh_role_dashboard_groups WHERE group_id=%s", (gid,))
    if not execute_write("DELETE FROM adh_dashboard_groups WHERE id=%s", (gid,)):
        raise DashboardGroupNotFound(f"仪表盘组不存在（id={gid}）")
    _invalidate_dashboard_payload()


def set_group_items(group_id: int, dashboard_ids: list) -> dict:
    """全量替换组内看板（顺序即编排序），返回 {count, dropped}。

    只登记仍存在的看板：不存在的 id（已删除）不得写回组内，否则组合重新长出"看板已删除"悬空成员。
    dropped 由调用方如实上报（no-silent-degradation）：非空说明有陈旧/越权引用被拒登记。
    """
    from backend.common.db import execute_write
    gid = require_group(group_id)
    ids = [int(x) for x in dict.fromkeys(dashboard_ids or [])]
    existing: set[int] = set()
    if ids:
        marks = ",".join(["%s"] * len(ids))
        existing = {int(r["id"]) for r in (execute_query(
            f"SELECT id FROM adh_dashboards WHERE id IN ({marks})", ids) or [])}
    dropped = [x for x in ids if x not in existing]
    execute_write("DELETE FROM adh_dashboard_group_items WHERE group_id=%s", (gid,))
    for i, did in enumerate([x for x in ids if x in existing]):
        execute_write("INSERT IGNORE INTO adh_dashboard_group_items (group_id, dashboard_id, sort) "
                      "VALUES (%s,%s,%s)", (gid, int(did), i))
    _invalidate_dashboard_payload()
    return {"count": len(existing), "dropped": dropped}


def set_group_roles(group_id: int, role_ids: list) -> int:
    """全量替换组的角色绑定。组按角色分配，**不授予看板可见性**（可见性只由角色看板授权裁决）。"""
    from backend.common.db import execute_write
    gid = require_group(group_id)
    ids = {int(r) for r in (role_ids or [])}
    execute_write("DELETE FROM adh_role_dashboard_groups WHERE group_id=%s", (gid,))
    for rid in ids:
        execute_write("INSERT INTO adh_role_dashboard_groups (role_id, group_id) VALUES (%s,%s)",
                      (rid, gid))
    _invalidate_dashboard_payload()
    return len(ids)


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
        """Fetch dashboards from database (角色可见集过滤, fail-closed).

        这是「全量在手」那一档（编辑器切板、下钻链接选择器需要整份清单）；
        带分页条的管理清单走 list_dashboards_page，两档共用同一份谓词与排序。
        """
        visible = visible_dashboard_ids(user_id, user_role, workspace_id)
        if visible is not None and not visible:
            return []
        where, params = _build_dashboard_filters(workspace_id, visible)
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_dashboards" + where + _DASHBOARD_ORDER_BY, params)
                return self._assemble_dashboards(cur, cur.fetchall(), user_id, user_role)
        finally:
            conn.close()

    def list_dashboards_page(self, user_id: int, workspace_id: int = 0, user_role: str = "", *,
                             search: str = "", status: str = "", tags=(), favorite_ids=(),
                             page: int = 1, page_size: int = _DASHBOARD_PAGE_SIZE_DEFAULT) -> dict:
        """服务端**真分页**看板清单：total 来自过滤后的 COUNT，当前页由 LIMIT/OFFSET 取。

        筛选（search/status/tags/favorite_ids）全部下推 SQL，与全量清单共用
        `_build_dashboard_filters` + `_DASHBOARD_ORDER_BY`（两份谓词必然漂移）；
        偏移量由页号在服务端算出，不接受调用方递交 offset。
        图表只为当前页的看板取瘦列（旧口径把每块板的全部 charts 连 sql_query/data_cache 一起回，
        是这份响应最重的部分）。返回契约 {items,total,page,page_size} 与字模库/资讯分页同源。
        """
        page = max(1, int(page or 1))
        page_size = min(_DASHBOARD_PAGE_SIZE_MAX, max(1, int(page_size or _DASHBOARD_PAGE_SIZE_DEFAULT)))
        empty = {"items": [], "total": 0, "page": page, "page_size": page_size}
        visible = visible_dashboard_ids(user_id, user_role, workspace_id)
        if visible is not None and not visible:
            return empty
        favs: Optional[list] = None
        if favorite_ids:
            ids = {int(i) for i in favorite_ids if str(i).strip() != ""}
            # 收藏只当过滤条件，且必须落在可见集内（fail-closed）：交集为空即空清单，不发查询
            favs = sorted(ids & visible) if visible is not None else sorted(ids)
            if not favs:
                return empty
        where, params = _build_dashboard_filters(workspace_id, visible, search=search,
                                                 status=status, tags=tags, favorite_ids=favs or ())
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM adh_dashboards" + where, params)
                total = int((cur.fetchone() or {}).get("total") or 0)
                if not total:
                    return empty
                cur.execute("SELECT " + _DASHBOARD_LIST_COLS + " FROM adh_dashboards" + where
                            + _DASHBOARD_ORDER_BY + " LIMIT %s OFFSET %s",
                            list(params) + [page_size, (page - 1) * page_size])
                return {"items": self._assemble_dashboards(cur, cur.fetchall(), user_id, user_role,
                                                           chart_cols=_CHART_SLIM_COLS),
                        "total": total, "page": page, "page_size": page_size}
        finally:
            conn.close()

    def list_dashboard_tags(self, user_id: int, workspace_id: int = 0, user_role: str = "") -> list:
        """管理页标签候选 = 可见域内全部看板 tags 的去重并集。

        分页后前端不再持有全量清单，标签下拉不能再从当前页求并集（会随翻页抖动、且漏标签）。
        """
        visible = visible_dashboard_ids(user_id, user_role, workspace_id)
        if visible is not None and not visible:
            return []
        where, params = _build_dashboard_filters(workspace_id, visible)
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT tags FROM adh_dashboards" + where, params)
                rows = cur.fetchall()
        finally:
            conn.close()
        found: set = set()
        for row in rows:
            found.update(_clean_tags(row.get("tags")))
        return sorted(found)

    def move_dashboard(self, user_id: int, dashboard_id: int, direction: int,
                       workspace_id: int = 0, user_role: str = "") -> dict:
        """上移/下移 = 与清单排序口径下的相邻看板**交换 sort_order**（服务端定位邻居，跨页也成立）。

        旧「整份列表重排后回传全量 sort_order」在分页下不可用（前端只持有当前页）。
        边界/置顶区不静默假成功：返回 moved=false + 可读原因，由前端提示。
        写回显式钉住 updated_at（列是 ON UPDATE CURRENT_TIMESTAMP），否则移动会把看板
        甩到「更新时间」排序的另一页，用户看到的顺序与操作不一致。
        """
        if int(direction) not in (-1, 1):
            raise ValueError("direction 只能是 -1(上移) 或 1(下移)")
        # 邻居按「这个用户看到的同一份清单」定位（含角色可见集），否则可能与一块它看不见的
        # 看板交换 —— 移动后自己的清单纹丝不动，属静默无效操作。
        visible = visible_dashboard_ids(user_id, user_role, workspace_id)
        if visible is not None and not visible:
            # 这块板根本不在他的清单里（角色未授权）：显式回不可见，不发 id IN () 的畸形 SQL
            return {"moved": False, "reason": "not_found"}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where, params = _build_dashboard_filters(workspace_id, visible)
                cur.execute("SELECT id, is_default, sort_order FROM adh_dashboards" + where
                            + _DASHBOARD_ORDER_BY + " FOR UPDATE", params)
                rows = cur.fetchall() or []
                index = next((i for i, r in enumerate(rows) if int(r["id"]) == int(dashboard_id)), None)
                if index is None:
                    return {"moved": False, "reason": "not_found"}
                target = index + int(direction)
                if target < 0 or target >= len(rows):
                    return {"moved": False, "reason": "boundary"}
                if int(rows[target]["is_default"]) != int(rows[index]["is_default"]):
                    return {"moved": False, "reason": "pinned_zone"}
                cur_sort = int(rows[index]["sort_order"] or 0)
                nbr_sort = int(rows[target]["sort_order"] or 0)
                if cur_sort != nbr_sort:
                    pairs = [(dashboard_id, nbr_sort), (rows[target]["id"], cur_sort)]
                else:
                    # 存量并列（老数据 sort_order 全为 0）时换值等于没换：按当前全序重编一次消解并列
                    order = [int(r["id"]) for r in rows]
                    order[index], order[target] = order[target], order[index]
                    pairs = [(did, 1000 + rank) for rank, did in enumerate(order)]
                for did, sort_order in pairs:
                    cur.execute("UPDATE adh_dashboards SET sort_order=%s, updated_at=updated_at "
                                "WHERE id=%s", (sort_order, int(did)))
                conn.commit()
        finally:
            conn.close()
        _invalidate_dashboard_cache(user_id)
        _invalidate_dashboard_payload()
        return {"moved": True, "reason": ""}

    @staticmethod
    def _assemble_dashboards(cur, dashboards: list, user_id: int, user_role: str = "", *,
                             chart_cols: str = "*") -> list:
        """给看板行挂图表 → 归一 → 标注可写性 → 本地化（全量与分页清单共用同一条后处理链）。

        chart_cols=_CHART_SLIM_COLS 的瘦行**只允许出现在分页清单**里（渲染缩略图/计数用），
        编辑与取数一律走单看板详情载荷，不得复用瘦行（否则 sql_query 等缺列静默变默认值）。
        """
        if not dashboards:
            return []
        dash_ids = [d["id"] for d in dashboards]
        placeholders = ",".join(["%s"] * len(dash_ids))
        cur.execute(f"SELECT {chart_cols} FROM adh_charts WHERE dashboard_id IN ({placeholders}) ORDER BY id",
                    dash_ids)
        charts_map: dict = {}
        for c in cur.fetchall():
            _normalize_chart(c)
            charts_map.setdefault(c["dashboard_id"], []).append(c)
        for d in dashboards:
            _normalize_dashboard(d)
            d["charts"] = charts_map.get(d["id"], [])
        # editable 仅供前端隐藏写入口: 角色授予的可见权不含编辑权(写在 API 层再裁决一次)
        _mark_editable(dashboards, user_id, user_role)
        return _localize_dashboards(dashboards)

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
        # 本地化必须在深拷贝之后（缓存本体语言无关）
        _localize_dashboard_row(result)
        from backend.modules.viz.services.dashboard_refresh_service import decorate_dashboard_payload
        decorate_dashboard_payload(result, int(user_id or 0), user_role or "",
                                   workspace_id=int(result.get("workspace_id") or 0))
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
                    "(`id`, `name`, `description`, `layout`, `filters`, `params`, `tags`, `status`, "
                    "`i18n`, "
                    "`owner_id`, `workspace_id`, `is_public`, `is_default`, `carousel_interval`, "
                    "`created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        did,
                        data["name"],
                        data.get("description", ""),
                        json.dumps(data.get("layout") or []),
                        json.dumps(data.get("filters") or {}),
                        json.dumps(data.get("params") or []),
                        json.dumps(_clean_tags(data.get("tags"))),
                        data.get("status", "designing"),
                        json.dumps(data.get(i18n.I18N_COLUMN) or {}),
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

                for json_field in ("layout", "filters", "params", i18n.I18N_COLUMN):
                    if json_field in data and data[json_field] is not None:
                        updates.append(f"`{json_field}` = %s")
                        params.append(json.dumps(data[json_field]))
                if "params" in data and data["params"] is not None:
                    # 默认参数是共享缓存的查询口径之一；改口径必须同批清缓存，不能等 TTL 兜底。
                    cur.execute(
                        "UPDATE adh_charts SET data_cache=NULL,data_cache_at=NULL,data_cache_key=NULL,"
                        "data_cache_scope=NULL WHERE dashboard_id=%s", (dashboard_id,))

                for bool_field in ("is_public", "is_default"):
                    if bool_field in data and data[bool_field] is not None:
                        updates.append(f"{bool_field} = %s")
                        params.append(1 if data[bool_field] else 0)

                if "carousel_interval" in data and data["carousel_interval"] is not None:
                    updates.append("carousel_interval = %s")
                    params.append(data["carousel_interval"])

                # tags 单独走归一（去空去重为字符串数组）：[] 也属显式清空，不能被 exclude_none 吞掉
                if "tags" in data and data["tags"] is not None:
                    updates.append("`tags` = %s")
                    params.append(json.dumps(_clean_tags(data["tags"])))

                params.append(dashboard_id)
                cur.execute(
                    f"UPDATE adh_dashboards SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            # 看板级元信息(名称/状态/参数/tags)变了，必须同批失效该看板详情载荷：
            # _invalidate_dashboard_cache 只清目录/组/列表缓存而不清 DETAIL_KEY，否则
            # 编辑面/预览走 get_dashboard 最长要等一个 TTL 才看得到改动（含新加的 tags）。
            _invalidate_dashboard_payload(dashboard_id)
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_dashboard(self, dashboard_id: int, user_id: int) -> bool:
        """Delete a dashboard and all its charts.

        写权限(仅 admin/属主)由调用方按 `check_dashboard_editable` 把关(人侧 API 层 /
        AI 侧 resource_guard 同一裁决点), 这里只负责删除本身(否则 admin 删不了他人看板, 会静默报 404)。
        级联清理必须在这一层做完：`adh_dashboard_group_items` 对 `adh_dashboards` 没有外键级联,
        以前只在 REST 端点补一刀, 走服务层的调用方（AS-BOT 删板）会留下「看板已删除」悬空成员。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_charts WHERE dashboard_id = %s", (dashboard_id,))
                # 级联清理角色可见性授权, 避免悬空授权行
                cur.execute("DELETE FROM adh_role_dashboard_access WHERE dashboard_id = %s", (dashboard_id,))
                # 级联清理看板组合成员（组合只编排、不授予可见, 但成员行必须随板清除）
                cur.execute("DELETE FROM adh_dashboard_group_items WHERE dashboard_id = %s", (dashboard_id,))
                cur.execute(
                    "DELETE FROM adh_dashboards WHERE id = %s",
                    (dashboard_id,),
                )
            conn.commit()
            _invalidate_dashboard_cache(user_id)
            _invalidate_dashboard_payload()
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

                # 副本不得复制旧版人写 SQL 口径（否则一次复制 = 养出一条旁路图表，
                # 刷新时才报错用户只看到“复制的看板坏了”）。AS-BOT 设计图是服务端生成 SQL，
                # 不属本条（护栏 §7 显式例外通道）。
                stale = [
                    c.get("name") or "未命名图表"
                    for c in charts
                    if str(c.get("sql_query") or "").strip()
                    and not _is_server_generated_channel(c)
                    and not str(c.get("chart_type") or "").startswith("widget_")
                ]
                if stale:
                    raise ValueError(
                        "看板里的图表「" + "、".join(stale[:5]) + "」仍携带旧版直写 SQL，"
                        "不能复制；请先将其改为数据集取数（或删掉该图）后重试复制。")

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

    def create_chart(self, dashboard_id: int, data: dict, identity: dict = None) -> int:
        """Add a chart to a dashboard. Returns the new chart ID.

        取数口径写入口唯一把关（`assert_chart_data_source`）：新图要么挂角色授权内的
        数据集，要么是声明式语义/AS-BOT 设计/尚未绑数的占位图；自带 SQL 直接拒（不静默丢弃）。
        """
        payload = dict(data or {})
        assert_chart_data_source(payload, identity or {})
        cid = _ts_id()
        now = _now()
        cache = _serialize_sample_data(payload.get("data_cache"), payload.get("config") or {})
        # 未声明通道的历史调用方（不传 source_type）落成占位图而不是已下线的 'query'：
        # 新行不得默认长进死通道（旧行维持原值，由刷新时报收编提示）
        source_type = str(payload.get("source_type") or "").strip() or (
            "dataset" if payload.get("source_id") else "empty")

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_charts "
                    "(`id`, `dashboard_id`, `name`, `chart_type`, `sql_query`, "
                    "`config`, `position`, `source_type`, `source_id`, `data_cache`, "
                    "`semantic_query`, `query_source`, `i18n`, `created_at`, `updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        cid, dashboard_id,
                        payload["name"], payload["chart_type"], None,
                        json.dumps(payload.get("config") or {}),
                        json.dumps(payload.get("position") or {}),
                        source_type,
                        payload.get("source_id"), cache,
                        json.dumps(payload.get("semantic_query"), ensure_ascii=False) if payload.get("semantic_query") else None,
                        payload.get("query_source") or "raw_sql",
                        json.dumps(payload.get(i18n.I18N_COLUMN) or {}, ensure_ascii=False),
                        now, now,
                    ),
                )
            conn.commit()
            _invalidate_dashboard_payload(dashboard_id)  # 图表结构变了: 目录预览/详情载荷都要重建
            return cid
        finally:
            conn.close()

    def _apply_demo_to_live_transition(self, dashboard_id: int, chart_id: int, data: dict) -> dict:
        """样式展示图一旦转入真实取数（改挂数据集 / 切语义查询）就清掉示例标记与演示缓存。

        模板图本身不带口径；用户配了数据就意味着这块卡要从“样式示例”变成“真数”，
        旧演示数值不得跟着长期滞留（卡片看着有数、其实全是假的）。
        改挂数据集同样算转真实：`demo_sample_without_source` 只看旧 config.datasource_id，
        不清标记的话数据集图表会永远回读演示缓存（挂上源却不取数）。
        """
        if not any(k in data for k in ("query_source", "semantic_query", "config",
                                       "source_type", "source_id")):
            return data
        incoming_cfg = _json_loads_safe(data.get("config"))
        incoming_cfg = incoming_cfg if isinstance(incoming_cfg, dict) else {}
        live_touched = ("query_source" in data or bool(data.get("semantic_query"))
                        or str(data.get("source_type") or "") == "dataset")
        # 前端显式声明"不再是示例图"：直接清标记与缓存，无需回读当前行
        declared_live = incoming_cfg.get(DEMO_DATA_KEY) is False
        if not declared_live and not (live_touched or incoming_cfg.get(DEMO_DATA_KEY) is True):
            return data  # 纯外观/布局修改不动数据口径，不额外跳一次库读也不误清真实缓存
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT config, source_type, source_id FROM adh_charts WHERE id=%s AND dashboard_id=%s",
                    (chart_id, dashboard_id),
                )
                row = cur.fetchone() or {}
        finally:
            conn.close()
        cfg = _json_loads_safe(row.get("config")) or {}
        if not isinstance(cfg, dict) or cfg.get(DEMO_DATA_KEY) is not True:
            return data
        merged = {**cfg, **incoming_cfg}
        on_dataset = (str(data.get("source_type") or row.get("source_type") or "") == "dataset"
                      and bool(data.get("source_id") or row.get("source_id")))
        became_live = declared_live or on_dataset \
            or data.get("query_source") == "semantic" or bool(data.get("semantic_query"))
        if not became_live:
            return data
        patched = dict(data)
        patched.pop("data_cache", None)  # 不接受"边转真实边重新注入缓存"
        merged.pop(DEMO_DATA_KEY, None)
        merged.pop("demoSql", None)  # 旧版样式模板可能登记过占位口径，一并清除
        patched["config"] = merged
        patched["data_cache"] = None
        return patched

    def update_chart(self, dashboard_id: int, chart_id: int, data: dict,
                     identity: dict = None) -> bool:
        """Update a chart within a dashboard.

        patch 只带局部字段，因此把关时回读现有行拼出有效口径（`chart_id` 传入闸门）；
        `sql_query` 只允许**清空**（不能写回新 SQL）—— 旧版自带 SQL 不能经“改标题”这类
        请求顺手写回，而改挂数据集时必须能抹掉旧口径；存量行里本就有 SQL 都会被闸门指名收编
        （不静默受理也不静默清空）。
        """
        now = _now()
        payload = dict(data or {})
        assert_chart_data_source(payload, identity or {}, chart_id=chart_id)
        data = self._apply_demo_to_live_transition(dashboard_id, chart_id, payload)

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 只更新已提交的白名单字段，视觉编辑不得清空查询和缓存。
                allowed = (
                    "name", "chart_type", "config", "position",
                    "source_type", "source_id", "data_cache", "semantic_query", "query_source",
                    "sql_query",
                    i18n.I18N_COLUMN,
                )
                sets, params = [], []
                for field in allowed:
                    if field not in data:
                        continue
                    value = data[field]
                    if field == "data_cache" and value is None:
                        continue
                    if field in ("name", "chart_type", "source_type", "query_source") and not value:
                        raise ValueError(f"{field} cannot be empty")
                    if field == "sql_query" and str(value or "").strip():
                        # 只允许**清空**：改挂数据集时必须能抹掉旧口径，否则存量 SQL 会让
                        # 下一次编辑被闸门判成“人在看板上写 SQL”而把图锁死；但不得写回新 SQL。
                        raise ValueError(
                            "看板图表不再接受直接编写 SQL：取数口径请先到「数据集」页定义（或选用已有的数据集），"
                            "然后在图表配置里改挂数据集。")
                    if field in ("config", "position", "semantic_query", i18n.I18N_COLUMN):
                        value = json.dumps(value, ensure_ascii=False) if value is not None else None
                    sets.append(f"`{field}`=%s")
                    params.append(value)
                if any(field in data for field in (
                        "chart_type", "config", "source_type", "source_id", "semantic_query", "query_source")) \
                        or ("data_cache" in data and data.get("data_cache") is None):
                    sets.extend(("data_cache=NULL", "data_cache_at=NULL", "data_cache_key=NULL",
                                 "data_cache_scope=NULL"))
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
                        # 真实写入必须同批失效详情载荷：早期此处直接 return，名称/类型/位置/config/缓存
                        # 已落库但共享详情仍旧，读者最长要等一个 TTL 才看得到改动
                        # （模板示例图改写完 SQL 后“示例数据”角标不消失就是这条路径）。
                        _invalidate_dashboard_payload(dashboard_id)
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

    def batch_save(self, dashboard_id: int, added: list, changed: dict, deleted: list,
                   identity: dict = None) -> dict:
        """一次请求提交整板草稿，返回逐条结果供前端定位失败项。

        元数据库连接是 autocommit，跨多表的事务回滚不可用；因此采用
        「先全量预检、后逐条应用」：预检不过则一笔不写（不会留下半截草稿），
        预检过后的单条失败只影响该条，并在 results 里带可读原因上报（不静默吞）。
        取数口径闸门与 create/update 同一入口（此处逐条预检，整笔拒绝而不是半截落库）。
        """
        results: list = []
        errors: list = []

        # 先回读存量行：一次查询兼做「存在性核对」与「报错用可读名称」（不得在文案里裸显 id）
        targets = sorted({int(k) for k in (changed or {})} | {int(v) for v in (deleted or [])})
        names: dict[int, str] = {}
        existing: set = set()
        if targets:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, name FROM adh_charts WHERE dashboard_id=%s AND id IN ({})".format(
                            ",".join(["%s"] * len(targets))),
                        [dashboard_id, *targets],
                    )
                    for row in (cur.fetchall() or []):
                        existing.add(row["id"])
                        names[row["id"]] = row.get("name") or "未命名图表"
            finally:
                conn.close()
        missing = [cid for cid in targets if cid not in existing]
        if missing:
            shown = "、".join(names.get(cid) or f"#{cid}" for cid in missing)
            errors.append(f"图表「{shown}」已不在当前看板（可能被其他人删除），已中止保存（未写入任何变更）")

        for payload in added or []:
            if not (payload.get("name") or "").strip() or not (payload.get("chart_type") or "").strip():
                errors.append("新增图表缺少名称或类型，已中止保存（未写入任何变更）")
                continue
            try:
                assert_chart_data_source(dict(payload or {}), identity or {})
            except (ValueError, NoIdentityError) as exc:
                errors.append(f"新增图表「{payload.get('name')}」取数口径不合格：{exc}"
                              "，已中止保存（未写入任何变更）")
        for cid, patch in (changed or {}).items():
            if int(cid) not in existing:
                continue          # 上面已按名报过「已不在看板」，不重复报一条口径错
            try:
                assert_chart_data_source(dict(patch or {}), identity or {}, chart_id=int(cid))
            except (ValueError, NoIdentityError) as exc:
                errors.append(f"图表「{names.get(int(cid)) or '未命名图表'}」取数口径不合格：{exc}"
                              "，已中止保存（未写入任何变更）")
        if errors:
            return {"ok": False, "errors": errors, "results": results, "created": {}}

        created: dict = {}
        for index, payload in enumerate(added or []):
            try:
                new_id = self.create_chart(dashboard_id, payload, identity=identity)
                created[str(index)] = new_id
                results.append({"op": "create", "index": index, "chart_id": new_id, "ok": True})
            except Exception as exc:  # noqa: BLE001 — 逐条上报，不吞原因
                logger.exception("batch_save create failed (dashboard=%s)", dashboard_id)
                results.append({"op": "create", "index": index, "ok": False, "error": str(exc)})
        for chart_id, patch in (changed or {}).items():
            cid = int(chart_id)
            try:
                ok = self.update_chart(dashboard_id, cid, patch, identity=identity)
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
                               user_id: int, workspace_id: int, username: str,
                               *, page_size: int = None, page_num: int = None,
                               persist_cache: bool = True, cache_key: str | None = None,
                               cache_scope: str = "", refresh_run_id: int | None = None) -> dict:
        """数据集图表(source_type='dataset'): 统一经数据集治理入口取数.

        不把数据集 SQL 复制进图表行 —— 保持数据集单口径(数据集改了图表即生效);
        取数经 dataset_service.query_dataset(governed_execute/七闸门), 无旁路。
        结果只回行列(护栏 §7), 执行 SQL/物理细节不入图表响应。
        分页（page_size/page_num）也经数据集入口下递：偏移、行总数、游标全由服务端生成，
        **不接受调用方递交统计语句或 offset**（旧 count_sql 已废除）。
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
        # 看板参数优先（控件给了值就以控件为准）；未给时回落到图表 config 里随意图存的
        # 筛选/排序/时间窗等口径 —— 直建通道的图靠这条逐次重算“今年/近 7 天”，
        # 也保住建屏时写进意图的业务筛选（否则刷新后只剩维度/指标，数字默默变大）。
        for key in ("filters", "order", *_INTENT_PASSTHROUGH_KEYS):
            if params.get(key) not in (None, "", [], {}):
                continue
            value = cfg.get(key)
            if value not in (None, "", [], {}):
                ds_params[key] = value
        if page_size:
            # 分页只递两个业务参数（每页行数 + 页号），偏移量与行总数均由服务端算
            ds_params["page_size"] = int(page_size)
            ds_params["page_num"] = int(page_num or 1)
        dims = params.get("dimensions") or cfg.get("dimensions") or []
        meas = params.get("measures") or params.get("metrics") or cfg.get("measures") or []
        if dims:
            ds_params["dimensions"] = dims
        if meas:
            ds_params["measures"] = meas
        # 看板参数 → 数据集入参：按各数据集声明的**形状**翻译（区间端点/相对天数/单值），
        # 不再"同名才透传"；翻译不到的入参如实回传 unbound，供图卡显式提示。
        args, param_diag = _dataset_bound_args(int(chart["source_id"]), params)
        if args:
            ds_params["args"] = args
        # 选源上下文**不从看板业务参数里取**（2026-11-06 收口）：`site` 在现网 8 个数据集里
        # 是正文的业务筛选变量（`${site}`），把它再当一次"路由到哪个站点的数据源"读，
        # 就等于"用户在下拉里选了个站点值"静默换了执行库；只有值不是合法站点名时才报错
        # —— 靠运气暴露的缺陷不算缺陷（护栏 §1：定源裁决不得由控件值触发）。
        # 看板级选源（2026-11-12 落地）：图表可在数据集「可用范围 ∩ 角色授权源」内**钉**一个执行
        # 数据源，存的是**业务名**（config.datasource，与本体 deploy_sites 同口径；内部 datasource_id
        # 仍属黑盒 §7，绝不入库/入请求体）。只从图表 config 读，与业务 params 正交（不让控件值换库）。
        # 未钉（空）→ 仍由服务端在交集内自动定源（存量行为零变化）；钉了但越权/超范围由
        # `resolve_effective_source` 显式报错并回抛候选业务名，不静默换源（no-silent-degradation）。
        pinned_source = str(cfg.get("datasource") or "").strip()
        if pinned_source:
            ds_params["datasource"] = pinned_source
        # 行级 scope 按角色叠加, 角色由服务端解析(不信任请求体)
        from backend.common import auth as _auth
        live = _auth.get_user_by_id(user_id) or {}
        identity = {"user_id": user_id, "username": username,
                    "role": live.get("user_role") or "", "workspace_id": workspace_id}
        result = dataset_service.query_dataset(int(chart["source_id"]), ds_params, identity)
        # 完整性事实（truncated / unpaged）必须在这条白名单里：漏一项，前端就分不清
        # “缓存是完整结果”与“缓存是旧全量”，只能每次进看板都补一次重查
        data = {k: result[k] for k in ("columns", "rows", "row_count", "truncated", "unpaged",
                                      "total", "paging", "field_formats")
                if k in result}
        # 参数落地情况随结果一起回传，但只带业务名：
        #   unbound_params = 本图数据集有该入参、看板没给值 → 这次是按数据集默认口径出的数
        #   param_notes    = 派生事实（如按相对窗口落地），让"控件给区间、正文用天数"可解释
        if param_diag.get("unbound"):
            data["unbound_params"] = [{"key": u["key"], "label": u["label"] or u["key"],
                                       "kind": u["kind"]} for u in param_diag["unbound"]]
        if param_diag.get("notes"):
            data["param_notes"] = param_diag["notes"]
        # 时间参数留空时 ES 通道由服务端补滚动窗（`_es_default_window`）：这是“按默认值出数”的事实，
        # 必须让查看者看见——否则用户以为“没筛=全量”，实际拿的是近 N 天（no-silent-degradation）。
        # 只透业务可读的窗口值与原因（无 SQL / 无内部标识，护栏 §7）
        window = result.get("default_window") or {}
        if window:
            data["default_window"] = {k: window[k] for k in ("start_time", "end_time", "days", "reason")
                                      if window.get(k)}
            note = (f"未给时间参数，本次按数据集默认窗口出数（近 {window.get('days')} 天："
                    f"{window.get('start_time')} ~ {window.get('end_time')}）")
            notes = data.setdefault("param_notes", [])
            if note not in notes:
                notes.append(note)
        # 缓存只写默认首页（行限 + 字节兜底与语义通道同一实现 `_chart_cache_json`）：
        # 本次响应仍把完整行回给前端，不得拿“用户开了大页”去撑大首屏预览的存储。
        # 分页元信息（total/paging）随缓存一起写：首屏要知道“共多少行、能不能跳页”，
        # 少了它就会把首页当成全量。
        if persist_cache:
            _write_chart_cache(chart, data.get("columns"), data.get("rows"),
                               _paging_extra(data), cache_key=cache_key, cache_scope=cache_scope,
                               refresh_run_id=refresh_run_id)
        return data

    def refresh_chart(self, dashboard_id: int, chart_id: int, params: dict = None,
                      page_size: int = None, page_num: int = None,
                      user_id: int = 0,
                      workspace_id: int = 0, username: str = "", *,
                      persist_cache: bool = True, cache_key: str | None = None,
                      cache_scope: str = "", refresh_run_id: int | None = None) -> dict:
        """按查看者身份重查一个图表（取数通道唯一：数据集 / 声明式语义 / AS-BOT 设计）。

        2026-11-06 拍板后图表不再自带 SQL：旧的"执行图表 SQL + 客户端传 count_sql"
        一并退出执行链（count_sql 曾是可由前端任意递交的裸 SQL，属于第二取数通道）。
        行级权限/敏感屏蔽/审计仍在 `governed_execute` 与七闸门里, 本次不收窄。
        分页只在数据集通道可用（行限/偏移/总数/游标全由服务端生成）；声明式语义图
        遇到翻页需求时显式报错，不开"看起来能翻页、其实拿的是同一页"的空承诺。
        """
        if not check_dashboard_visible(dashboard_id, user_id):
            raise PermissionError("无权访问该仪表盘")

        params = params or {}
        # 读图配置用**短连接**：取数可能几十秒，取数期间不得持有元数据库连接
        # （连接池 max 8 全应用共享，理由见 `_write_chart_cache`）。
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,name,chart_type,dashboard_id,workspace_id,source_type,source_id,"
                    "semantic_query,query_source,sql_query,config,data_cache FROM adh_charts "
                    "WHERE id = %s AND dashboard_id = %s",
                    (chart_id, dashboard_id),
                )
                chart = cur.fetchone()
        finally:
            conn.close()
        if not chart:
            raise ValueError("图表不存在")
        if (_json_loads_safe(chart.get("config")) or {}).get("as_bot_design"):
            return refresh_designed_chart(chart, user_id)
        return self._refresh_by_channel(
            chart, params, user_id, workspace_id, username, dashboard_id,
            page_size=page_size, page_num=page_num,
            persist_cache=persist_cache, cache_key=cache_key, cache_scope=cache_scope,
            refresh_run_id=refresh_run_id)

    def _refresh_by_channel(self, chart: dict, params: dict, user_id: int,
                            workspace_id: int, username: str, dashboard_id: int,
                            *, page_size: int = None, page_num: int = None,
                            persist_cache: bool = True, cache_key: str | None = None,
                            cache_scope: str = "", refresh_run_id: int | None = None) -> dict:
        """单图重查的通道分派（`refresh_chart` 与 `refresh_all_charts` 共用一份实现）。

        分派次序即口径优先级；任何一条都不回退到下一条（拿别通道的数据充数 = 错数）。
        表值图恒定按页取数（未显式给页时按首页取，页大小取图表配置 pageSize），
        其他图型不注入分页参数 —— 给曲线图套 20 行页限会把趋势砍没。
        """
        # 控件（日期区间/搜索框等）不带取数口径，旧行可能还停在已下线的 'query' 通道上，
        # 不得因此报错（它们本来就不取数）。
        if str(chart.get("chart_type") or "").startswith("widget_"):
            return {"columns": [], "rows": [], "row_count": 0}
        if demo_sample_without_source(chart):
            # 模板样式展示图未绑源：不下发演示 SQL，原样回读示例数据（不抹空）
            return _demo_cached_result(chart)
        page_size, page_num = _resolve_chart_page(chart, page_size, page_num)
        if chart.get("source_type") == "dataset" and chart.get("source_id"):
            return self._refresh_dataset_chart(
                chart, params, user_id, workspace_id, username,
                page_size=page_size, page_num=page_num,
                persist_cache=persist_cache, cache_key=cache_key, cache_scope=cache_scope,
                refresh_run_id=refresh_run_id)
        server_channel = _is_server_generated_channel(chart)
        intent = _semantic_intent(chart)
        if intent is not None:
            # 声明式语义图（含 AS-BOT 大屏工具产的行：sql_query 是语义层编译产物）先于
            # “带 SQL 即旧通道”判定，否则服务端生成的口径会被当人写 SQL 拒掉
            if page_size or (page_num or 1) > 1:
                raise ValueError(_SEMANTIC_UNPAGED_HINT)
            result = _execute_via_semantic(intent, params, user_id,
                                           chart.get("workspace_id") or workspace_id,
                                           _get_chart_datasource_id(chart), None)
            if persist_cache:
                _cache_chart_result(chart, result, cache_key=cache_key, cache_scope=cache_scope,
                                    refresh_run_id=refresh_run_id)
            return result
        if not server_channel and str(chart.get("sql_query") or "").strip():
            # 存量直写 SQL 图表（收编未覆盖或库外手工写入）：不拿旧口径默默继续跑，
            # 也不回退成空图 —— 指名要人去收编（no-silent-degradation）
            raise ValueError(LEGACY_CHART_SQL_HINT.format(name=chart.get("name") or "未命名图表"))
        if not server_channel and str(chart.get("source_type") or "") in _LEGACY_SQL_CHANNELS:
            # 旧快照/直连通道且不带口径：不能再当“能用”的通道刷（刷出来是空图，
            # 用户以为是数据没了）
            raise ValueError(_LEGACY_SNAPSHOT_HINT.format(name=chart.get("name") or "未命名图表"))
        if server_channel:
            # query_source='semantic' 但意图形状不可执行（缺 object）：不拿旧 SQL 列兼程，
            # 也不回空图，指名重配（no-silent-degradation）
            raise ValueError(
                "图表「" + str(chart.get("name") or "未命名图表") + "」的声明式语义查询已失效"
                "（semantic_query 缺 object），请在图表配置里改挂数据集或重新保存。")
        # 未绑数据集的占位图（拖入字模/图表卡但未配数据）：本来就没有取数口径
        return {"columns": [], "rows": [], "row_count": 0}

    def refresh_all_charts(self, dashboard_id: int, params: dict = None,
                           user_id: int = 0, workspace_id: int = 0,
                           username: str = "", *,
                           persist_cache: bool = True, cache_key: str | None = None,
                           cache_scope: str = "", refresh_run_id: int | None = None) -> dict:
        """整板重查：逐图走与 `refresh_chart` 同一套通道分派。

        取数走治理护城河; 无可信身份 -> fail-closed 整体拒绝(I5)。
        单图失败只影响该图（带可诊断原因上报），不拿旧 SQL 旁路兼程重跑。
        """
        if not user_id:
            raise NoIdentityError("缺少可信用户身份, 拒绝批量取数(数据合规护城河)")
        if not check_dashboard_visible(dashboard_id, user_id):
            raise PermissionError("无权访问该仪表盘")

        params = params or {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,name,chart_type,dashboard_id,workspace_id,source_type,source_id,"
                    "semantic_query,query_source,sql_query,config,data_cache FROM adh_charts "
                    "WHERE dashboard_id = %s ORDER BY id",
                    (dashboard_id,),
                )
                charts = cur.fetchall()
        finally:
            # 配置一次读齐就归还连接：下面每张图要卡在数据源上数秒，不能占着元数据库连接
            conn.close()

        def _one(chart: dict) -> dict:
            """单图重查（跑在专用通道里，与主应用的线程/连接资源隔离）。"""
            cid = chart["id"]
            try:
                if refresh_run_id is not None:
                    active = execute_query(
                        "SELECT id FROM adh_dashboard_refresh_runs WHERE id=%s AND dashboard_id=%s "
                        "AND status='running'", (int(refresh_run_id), int(dashboard_id)), fetchone=True)
                    if not active:
                        raise ChartRefreshCancelled("刷新实例已取消或租约失效")
                if (_json_loads_safe(chart.get("config")) or {}).get("as_bot_design"):
                    return refresh_designed_chart(chart, user_id)
                return self._refresh_by_channel(
                    chart, params, user_id, workspace_id, username, dashboard_id,
                    persist_cache=persist_cache, cache_key=cache_key, cache_scope=cache_scope,
                    refresh_run_id=refresh_run_id)
            except ChartRefreshCancelled:
                raise
            except Exception as e:  # noqa: BLE001  逐图上报可诊断原因(已脱敏)
                logger.warning("[ChartRefresh] chart=%s 失败: %s", cid, e)
                return {"error": str(e)}

        from backend.core import viz_query_pool

        payloads: dict = {}
        futures: list = []
        busy_reason: str | None = None
        for chart in charts:
            if busy_reason is not None:
                continue
            try:
                futures.append((chart["id"], viz_query_pool.submit(_one, chart)))
            except viz_query_pool.VizQueryBusy as exc:
                # 通道已满：剩下的图如实标未执行，不排进看不见的内部队列
                busy_reason = str(exc)
        for cid, fut in futures:
            try:
                payloads[cid] = fut.result()
            except viz_query_pool.VizQueryExpired as exc:
                payloads[cid] = {"error": str(exc)}
        results = {}
        for chart in charts:
            cid = chart["id"]
            results[cid] = payloads.get(cid) or {
                "error": busy_reason or "本次未执行取数（取数通道已满），请稍后重试"}
        _invalidate_dashboard_payload(dashboard_id, structural=False)
        return {"charts": results}

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


# ── 已删除的旧入口（不得重建）──────────────────────────────────────


# （图表取数已收口到数据集：旧版按 saved-query 直接回数据行的 preview 与
#  「快照/保存查询/数据集」聚合入口已删除，不得再往本文件添第二套取数实现。）


# ── Module-level singletons ──────────────────────────────────────────────────

dashboard_service = DashboardService()
chart_service = ChartService()
snapshot_service = SnapshotService()
