"""Screen Tools — datahub_screen 工具组: 直建/查询/更新可视化数据大屏 + 字模库取用。

直建通道(2026-11-17 拍板: 会话内不再打开设计面板、不让人填/确认 SQL):
- Chat/Agent(AS-BOT)侧 widget 只接受**声明式语义意图**(query), 由服务端经语义层
  (intent -> binding -> plan) 校验; **拒收任何 sql/raw_sql/statement 字段**,
  LLM 永不产出/看到裸 SQL。
- 每张图先按意图落成**语义型数据集**(adh_datasets: object_key + datasource_id +
  chart_preset.intent), 看板图只挂 `source_type='dataset'`。口径留在数据集这一层
  (有字段声明/角色授权/血缘), 刷新经 `dataset_service.query_dataset` 治理口取数。
- 图表行不存 sql_query/semantic_query: 非空 `semantic_query` 会被判为“服务端生成
  通道”而**绕过**数据集授权闸门; 数据源对 LLM 是黑盒(不回显 SQL/数据源/id)。
- 传统 BI 手填 SQL 模板走 dataviz REST(图表配置), 不经本工具; 两者取数最终都过护城河。

字模库(adh_vis_components)是纯配置(背景/卡片/图表样式/布局/配色), 元数据级安全;
save/list/get 不返回数据行。预制 SQL 属于数据集职责（已治理的查询资产），不在字模库。
"""

import asyncio
import json
import logging
import re

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text
from backend.modules.mind.execution.outbound_guard import SENSITIVE_OUTPUT_KEYS

logger = logging.getLogger(__name__)

# 大屏画布基准(与前端 Screen 页 1920x1080 缩放布局一致)
_CANVAS_W = 1920
_CANVAS_H = 1080
# 单屏上限：跨站对比（5 站 × 4 指标）放**同一块板**是常见诉求，12 张会把多站需求
# 逼成"每站一块板"（2026-11-17 反馈：用户要一屏看 5 个站，结果拿到 5 块看板）。
_MAX_WIDGETS = 24
_KPI_H = 180
_ROW_GAP = 12
# KPI 单卡宽度上限：卡片被拉成整屏宽时迷你趋势线会被拉平、数字挤在中间（历史观感缺陷）
_KPI_MAX_W = 480
_KPI_MIN_W = 300
# 前端渲染事实（frontend/src/lib/dashboardPageLayout.ts）：像素 position 按比例映射到
# 64/48/24 列栅格，行高 PAGE_ROW=4px，**自动带高被截到 600px**，并按类型抬到最小高
# （表格 360 / KPI 128 / 其他 280）。抬高会顶破下一带 —— 就是"卡片一张比一张靠下、
# 右侧一大片空白"的斜阶梯根因。服务端出版面时必须自己守住这三条，不指望渲染层兜。
_ROW_UNIT = 4
_TOP_MARGIN = 32          # 画布顶部留白：内容贴上边缘会和播放页标题栏挤在一起（用户反馈）
_CARD_MAX_H = 600         # 前端截顶值：超过它等于白留高度
_MIN_CARD_H = 280         # 前端最小高：普通图
_MIN_TABLE_H = 360        # 前端最小高：表格/明细
_MIN_KPI_H = 128          # 前端最小高：KPI 数字卡
_MAX_CONTENT_CARDS = 12   # 还能排出主次的上限：超了显式拒并建议拆板，不做等分挤压
_MAX_PER_ROW = 4          # 常规每行张数（放不下时才放宽到 6/8）

# 布局分层：KPI 数字卡一行，趋势族一行，其余（占比/明细/地图）一行
_KPI_TYPES = {"text_display", "big_number_trend", "gauge"}
_TREND_TYPES = {"line", "area", "timeseries_line", "timeseries_bar", "timeseries_area"}
_TABLE_TYPES = {"table", "table_value"}
_MAP_TYPES = {"china_map", "world_map"}
_SHARE_TYPES = {"pie", "rose", "treemap", "radar", "funnel", "sankey", "word_cloud",
                "radial_bar", "boxplot", "waterfall", "tree", "heatmap", "calendar_heatmap",
                "scatter", "bubble"}

# 大屏主题/字模可持久化键(挂在 adh_dashboards.filters 上, 前端 Screen 页读取)
_THEME_KEYS = {"mode", "background", "backgroundImage", "header", "card"}

# 前端 DashboardChart 支持的图表类型(对齐渲染器 switch 分支)
_CHART_TYPES = {
    "bar", "line", "area", "pie", "scatter", "radar", "gauge", "funnel", "heatmap",
    "text_display", "big_number_trend", "timeseries_line", "timeseries_bar",
    "timeseries_area", "tree", "treemap", "waterfall", "sankey", "boxplot", "bubble",
    "calendar_heatmap", "rose", "radial_bar", "word_cloud", "china_map", "world_map",
    "table", "table_value", "iframe",
}

# 无需取数的组件类型(iframe: 嵌入外部网页, 仅需 config.url)
_NO_SQL_TYPES = {"iframe"}

# 图表类型别名归一（无损，不猜口径）。
# 历史缺陷（2026-11-17）：模型传 `big_number`（本意是 KPI 数字卡）直接被拒"不支持的图表类型"，
# 白白多耗一轮工具调用；别名表只把同一渲染分支的常见叫法映到真实类型，
# 表里没有的值仍按 `_CHART_TYPES` 显式拒绝并列出可选清单（no-silent-degradation）。
_CHART_TYPE_ALIASES = {
    # KPI 数字卡（前端 big_number_trend 分支）
    "big_number": "big_number_trend", "bignumber": "big_number_trend",
    "big_number_card": "big_number_trend", "kpi": "big_number_trend",
    "kpi_card": "big_number_trend", "number": "big_number_trend",
    "number_card": "big_number_trend", "statistic": "big_number_trend",
    "stat": "big_number_trend", "metric": "big_number_trend", "metric_card": "big_number_trend",
    # 基础图形（带 _chart/_plot 后缀或列图叫法）
    "line_chart": "line", "trend": "line", "bar_chart": "bar",
    "column": "bar", "column_chart": "bar", "pie_chart": "pie",
    "area_chart": "area", "radar_chart": "radar", "funnel_chart": "funnel",
    "scatter_plot": "scatter", "bubble_chart": "bubble", "gauge_chart": "gauge",
    "heat_map": "heatmap", "heatmap_chart": "heatmap", "heat_calendar": "calendar_heatmap",
    "calendar": "calendar_heatmap", "wordcloud": "word_cloud", "tree_map": "treemap",
    "sankey_diagram": "sankey", "box_plot": "boxplot", "rose_chart": "rose",
    "ring": "radial_bar", "radial": "radial_bar",
    # 时序族
    "time_series_line": "timeseries_line", "timeseriesline": "timeseries_line",
    "time_series_bar": "timeseries_bar", "time_series_area": "timeseries_area",
    # 表格/文本/地图
    "data_table": "table", "table_chart": "table", "table_value_chart": "table_value",
    "text": "text_display", "text_card": "text_display",
    "china_map_chart": "china_map", "world_map_chart": "world_map",
}


def _normalize_chart_type(raw) -> str:
    """图表类型归一：小写 + 空格/连字符转下划线，再按常见别名映射。

    只做**无损**归一：别名表里没有的值原样返回，由调用方按 `_CHART_TYPES` 显式拒绝
    并列出可选类型——绝不静默改成另一个图型。
    """
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _CHART_TYPE_ALIASES.get(text, text)

# LLM 严禁提供的裸 SQL 字段(数据护城河 §6)
_SQL_FIELDS = ("sql", "raw_sql", "statement", "query_sql", "base_sql")

def _error(message: str, **extra) -> dict:
    return _text({"error": message, **extra}, is_error=True)


def _safe_exec_error(exc: Exception, where: str) -> dict:
    """执行阶段失败: 原始细节仅进服务端日志, 对 LLM 回**按错因分类**的文案(数据源黑盒 §7)。

    不再手写一份“连接/凭据暂不可用, 建议稍后重试”：那是把方言不符、表不存在这类
    确定性失败也说成抖动（同一口径已在 `semantics.contract.classify_exec_failure` 收敛，
    历史缺陷：会话 1783069459277）。NoIdentityError 等声明式拒绝原因仍原样返回。
    """
    from backend.core.governed_query import NoIdentityError
    from backend.semantics.contract import classify_exec_failure

    if isinstance(exc, NoIdentityError):
        return _error("缺少可信用户身份, 拒绝取数(数据合规护城河)。")
    category, hint = classify_exec_failure(str(exc))
    logger.warning("[screen] %s failed (%s, detail server-side only): %s", where, category, exc)
    return _error(hint)


class ScreenIntentError(Exception):
    """携带已构造好的(安全)错误响应。"""

    def __init__(self, response: dict):
        super().__init__("screen intent error")
        self.response = response


def _type_label(expect) -> str:
    names = {list: "数组", dict: "对象", str: "字符串", int: "整数"}
    if isinstance(expect, tuple):
        return " 或 ".join(names.get(t, getattr(t, "__name__", str(t))) for t in expect)
    return names.get(expect, getattr(expect, "__name__", str(expect)))


def _coerce_struct(value, label: str, expect, default=None):
    """结构化入参归一(object / array)。

    历史缺陷（2026-11-17）: datahub_screen 全组的 TOOL_SPECS 只写了参数描述、没写类型，
    `to_json_schema` 于是把所有参数（含 widgets/query/config/theme/style）一律声明成
    **string**。模型只能把数组/对象塞成 JSON 字符串：`len(widgets)` 数的是字符数，
    所以 1 张图也报「组件数量不能超过 12」；传 `"[]"` 又能过长度校验、在
    enumerate 字符串时炸成「widgets[0] 必须是对象」——同一条 schema 缺陷的两种表现。

    根治在 TOOL_SPECS 补真实类型；这里再做一层宽容归一：字符串按 JSON 解析，
    类型不符**显式报错**，绝不静默当空值（no-silent-degradation §1）。
    """
    if value is None or value == "":
        return default
    if isinstance(value, expect):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError) as e:
            raise ScreenIntentError(_error(
                f"{label} 需要 {_type_label(expect)}，收到无法解析为 JSON 的字符串: {text[:120]}")) from e
    else:
        parsed = value
    if isinstance(parsed, expect):
        return parsed
    raise ScreenIntentError(_error(
        f"{label} 需要 {_type_label(expect)}，实际收到 {type(parsed).__name__}"))


def _coerce_theme(raw_theme, label: str = "theme") -> dict:
    """主题入参归一：既收对象也收 'light'/'dark' 简写。

    模型只想改明暗时会直接传字符串，而 SDK 按 schema 类型先拒
    （历史反馈 2026-11-17：Input validation error: 'light' is not of type 'object'）。
    建屏与改板共用这一份口径，避免两处各写一份字符串白名单。
    """
    if isinstance(raw_theme, str):
        mode = raw_theme.strip().lower()
        if mode not in ("light", "dark"):
            raise ScreenIntentError(_error(
                f"{label} 字符串只接受 'light'/'dark'（要背景/卡片等更多字段请传对象 "
                f"{{mode, background, ...}}），收到: {raw_theme[:40]}"))
        return {"mode": mode}
    return dict(raw_theme or {})


# ── 直建通道：每图落数据集(adh_datasets) + 挂数据集图 ────────────────────────


def identity_of(ctx) -> dict:
    """服务端可信身份（只取执行上下文，不取工具参数；护栏 §2）。

    与 `chart_identity_from_user` / datasets 写入口 `_identity` 同形，三处不得各写一套字段名。
    """
    from backend.common import auth

    uid = int((ctx.user_id if ctx else 0) or 0)
    live = (auth.get_user_by_id(uid) if uid else {}) or {}
    return {
        "user_id": uid,
        "username": (getattr(ctx, "username", "") or live.get("username") or ""),
        "role": live.get("user_role") or "",
        "workspace_id": int((ctx.workspace_id if ctx else 0) or 0),
    }


def require_screen_perms(ctx, *, need_dataset: bool = True) -> None:
    """写动作门槛与人类能力一致：建/改大屏要 dashboard:manage，落数据集要 dataset:manage。

    口径唯一 = `perm_link`（adh_user_roles ⋈ adh_role_perms ⋈ ai_access），fail-closed：
    没有权限码、或 ai_access 不是 write，就拒——不“先建了再说”，也不另造第二套判断。
    只改标题/类型/位置（不碰数据集）时传 `need_dataset=False`：不因为一次改标题而
    多要一项建数据集的权限。
    """
    from backend.core import perm_link

    identity = identity_of(ctx)
    if not identity["user_id"]:
        raise PermissionError("缺少可信用户身份, 拒绝创建数据大屏(数据合规护城河)")
    perm_link.require_write_perm(identity["user_id"], identity["workspace_id"],
                                 "dashboard:manage", "创建/修改数据大屏")
    if need_dataset:
        perm_link.require_write_perm(identity["user_id"], identity["workspace_id"],
                                     "dataset:manage", "创建数据集")


def resolve_screen_source(args: dict, ctx, object_key: str) -> int:
    """本次图表的生效源：site(站点业务名) > datasource(源业务名) > 会话生效源。

    只认业务名（waker-datasource-domain §2）：LLM 拿不到真实 id，任何数字 id 都不消费。
    聚合态（全部站点）没有单一源时必须显式指定站点，不得猜一个源开跑。
    """
    from backend.modules.mind.execution.sdk_tools.query_tools import _resolve_datasource_arg

    source = int(_resolve_datasource_arg(
        {"site": args.get("site"), "datasource": args.get("datasource"), "object": object_key},
        ctx) or 0)
    if not source:
        raise ScreenIntentError(_error(
            "建大屏需要明确的生效数据源：当前会话没有单一数据源（未选源/全部站点聚合态），"
            "请用 site（站点业务名）或 datasource（源业务名）指定本次建屏的数据源。"))
    return source


def _intent_key(intent: dict) -> str:
    return json.dumps(intent or {}, sort_keys=True, ensure_ascii=False, default=str)


def _dataset_preset(intent: dict) -> dict:
    """数据集图表预设：x/y/group 列与行数从意图派生（只放业务名，不放物理列/id）。"""
    dims = [str(d) for d in (intent.get("dimensions") or [])]
    metrics = [str(m) for m in (intent.get("metrics") or [])]
    preset: dict = {"intent": intent}
    if dims:
        preset["xCol"] = dims[0]
    if len(dims) > 1:
        preset["groupCol"] = dims[1]
    if metrics:
        preset["yCol"] = metrics[0]
    if intent.get("limit"):
        try:
            preset["limit"] = int(intent["limit"])
        except (TypeError, ValueError):
            pass
    return preset


def _unique_dataset_name(base: str) -> str:
    """adh_datasets.name 全局唯一且限长 128：重名自动加序号，不靠覆盖别人口径。"""
    from backend.common.db import execute_query

    name = (base or "AI 大屏图表数据集").strip()[:120] or "AI 大屏图表数据集"
    candidate, n = name, 1
    while execute_query("SELECT id FROM adh_datasets WHERE name=%s", (candidate,), fetchone=True):
        n += 1
        candidate = f"{name} ({n})"
    return candidate


def _grant_dataset_to_user_roles(user_id: int, dataset_id: int) -> list[int]:
    """数据集可见性 = 角色授权（fail-closed）：创建者所属角色逐条补授。

    不补授就出现“工具说建好了、图表挂不上”（assert_chart_data_source 按角色清单裁决）。
    账号没有任何角色时直接拒——那不是本工具能替他决定的事。
    """
    from backend.common.db import execute_query, execute_write

    rows = execute_query("SELECT role_id FROM adh_user_roles WHERE user_id=%s", (int(user_id),)) or []
    roles = sorted({int(r["role_id"]) for r in rows if r.get("role_id")})
    if not roles:
        # fail-loud：没角色就挂不上图（assert_chart_data_source 按角色清单裁决），
        # 不能“建了再说”——也不把这条咽成“数据源不可用”。
        raise ScreenIntentError(_error(
            "当前账号没有任何角色，新建数据集无法授权给角色（图表会挂不上）；"
            "请先为该账号配置角色后重试。"))
    for rid in roles:
        execute_write("INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id) VALUES (%s,%s)",
                      (rid, int(dataset_id)))
    return roles


def _grant_dashboard_to_user_roles(user_id: int, dashboard_id: int, user_role: str = "") -> list[int]:
    """看板可见性 = 角色授权（fail-closed）：建屏同批把看板授给创建者所属角色。

    「数据看板」页与 /screen/<id> 的可看裁决只看 adh_role_dashboard_access——属主与
    is_public 都不授予可见性（tests/test_dashboard_role_visibility.py 锁死）。漏这一步
    就是「工具说建好了、用户自己找不到这块板」，下一次"改一下刚才那个看板"人和 AI
    都拿不到它，只能重建。admin 全可见（visible_dashboard_ids 返回 None），不需补授。
    """
    from backend.common.db import execute_query, execute_write
    from backend.modules.viz.services.dashboard_service import _is_admin_user

    if _is_admin_user(int(user_id), user_role or ""):
        return []
    rows = execute_query("SELECT role_id FROM adh_user_roles WHERE user_id=%s", (int(user_id),)) or []
    roles = sorted({int(r["role_id"]) for r in rows if r.get("role_id")})
    if not roles:
        raise ScreenIntentError(_error(
            "当前账号没有任何角色，新建的看板无法授权给角色（本人也看不见）；"
            "请先为该账号配置角色后重试。"))
    for rid in roles:
        execute_write("INSERT IGNORE INTO adh_role_dashboard_access (role_id, dashboard_id) VALUES (%s,%s)",
                      (rid, int(dashboard_id)))
    return roles


def _find_reusable_dataset(identity: dict, object_key: str, source: int, intent_key: str) -> dict | None:
    """同用户 + 同对象 + 同源 + 同意图 → 复用既有 ai_session 数据集（改标题不新增资产）。"""
    from backend.common.db import execute_query

    rows = execute_query(
        "SELECT id, name, chart_preset FROM adh_datasets "
        "WHERE owner_id=%s AND origin='ai_session' AND object_key=%s AND datasource_id=%s "
        "AND status='active' ORDER BY id DESC LIMIT 50",
        (int(identity.get("user_id") or 0), object_key, int(source))) or []
    for r in rows:
        preset = r.get("chart_preset")
        if isinstance(preset, str):
            try:
                preset = json.loads(preset)
            except (json.JSONDecodeError, TypeError):
                preset = {}
        if isinstance(preset, dict) and _intent_key(preset.get("intent") or {}) == intent_key:
            return {"id": int(r["id"]), "name": r.get("name"), "reused": True}
    return None


def _materialize_dataset(*, identity: dict, screen_name: str, title: str, chart_type: str,
                         object_key: str, source: int, intent: dict) -> dict:
    """声明式意图 → 数据集资产：等价则复用，否则新建（origin=ai_session）+ 角色补授。"""
    from backend.modules.viz.services import dataset_service

    key = _intent_key(intent)
    reusable = _find_reusable_dataset(identity, object_key, source, key)
    if reusable:
        return reusable
    ds = dataset_service.create_dataset({
        "name": _unique_dataset_name(f"{screen_name}·{title}"),
        "description": f"AI 大屏「{screen_name}」图表「{title}」的数据集（会话直建产物）",
        "object_key": object_key,
        "datasource_id": int(source),
        "chart_type": chart_type,
        "chart_preset": _dataset_preset(intent),
    }, identity, origin="ai_session")
    roles = _grant_dataset_to_user_roles(int(identity["user_id"]), int(ds["id"]))
    return {"id": int(ds["id"]), "name": ds.get("name"), "roles": roles, "reused": False}


def _fetch_dataset(dataset_id: int, intent: dict, identity: dict) -> dict:
    """数据集治理口取数（七闸门/governed_execute 唯一通道），供建屏预填与行数记账。

    预填与看板刷新走同一个入口 → 建屏时看到的数就是刷新后看到的数（不会“建时有、刷时变”）。
    """
    from backend.modules.viz.services import dataset_service

    params: dict = {}
    for key in ("dimensions", "filters", "order", "time_window", "time_column", "time_grain",
                "time_range", "time_start", "time_end"):
        if intent.get(key):
            params[key] = intent[key]
    if intent.get("metrics"):
        params["measures"] = intent["metrics"]
    if intent.get("limit"):
        try:
            params["limit"] = int(intent["limit"])
        except (TypeError, ValueError):
            pass
    return dataset_service.query_dataset(int(dataset_id), params, identity)


def _dataset_chart_config(intent: dict, dataset: dict, visual: dict, theme_mode) -> dict:
    """图表 config：视觉配置 + 意图口径（业务名，无物理/id）+ 数据集业务名。

    刷新时 `_refresh_dataset_chart` 从这份 config 取回 dimensions/measures/filters/time_window，
    相对时间窗（今年/近 7 天）因此在每次刷新时按当前时刻重算，而不是把绝对日期固化进数据集。
    """
    cfg = _sanitize_config(visual)
    if theme_mode:
        cfg["theme"] = theme_mode
    if intent.get("metrics"):
        cfg["measures"] = intent["metrics"]
    for key in ("dimensions", "filters", "order", "limit", "time_grain", "time_column",
                "time_window", "time_range", "time_start", "time_end"):
        if intent.get(key):
            cfg[key] = intent[key]
    if dataset.get("name"):
        cfg["dataset_name"] = dataset["name"]
    return cfg


async def _prepare_widget(w, index: int, args: dict, ctx, identity: dict,
                          screen_name: str, theme_mode) -> dict:
    """单图：校验意图 → 定源 → 校验绑定 → 落数据集 → 预取数（任何一步失败即整单拒绝）。"""
    from backend.modules.mind.execution.resource_guard import validate_binding
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.intent import parse_intent

    from backend.modules.viz.services.dashboard_service import _chart_cache_json, _paging_extra

    if not isinstance(w, dict):
        raise ScreenIntentError(_error(f"widgets[{index}] 必须是对象"))
    # 数据护城河 §6: 拒收 LLM 直传的裸 SQL 字段
    leaked = [k for k in _SQL_FIELDS if isinstance(w.get(k), str) and w.get(k).strip()]
    if leaked:
        raise ScreenIntentError(_error(
            f"widgets[{index}] 不允许直接提供 SQL 字段({','.join(leaked)})。"
            "请用 query 传声明式语义意图 {object, metrics[], dimensions[], filters[], order, limit, time_window}。"))
    title = str(w.get("title") or f"图表 {index + 1}").strip()
    chart_type = _normalize_chart_type(w.get("chart_type") or "bar")
    if chart_type.startswith("widget_"):
        # 参数控件单独说明：否则“不支持的图表类型”会引导模型去换别的图，而不是解释口径边界
        raise ScreenIntentError(_error(
            f"widgets[{index}]({title}) 是参数控件（{chart_type}）：直建通道不产控件——每图口径"
            "已固化为数据集意图，控件绑定的变量没有消费方（填了不出数）。需要筛选交互时如实告知"
            "用户：数据集正文改 ${变量} 外置 + 声明入参，再到看板编辑器挂 widget_* 绑定，"
            "或改用相对时间窗 time_window（口径见 screen-builder 技能 B2.5）。"))
    if chart_type not in _CHART_TYPES:
        raise ScreenIntentError(_error(
            f"widgets[{index}] 不支持的图表类型: {w.get('chart_type')}, 可选: {', '.join(sorted(_CHART_TYPES))}"))

    raw_cfg = w.get("config") or {}
    if isinstance(raw_cfg, str):
        try:
            raw_cfg = json.loads(raw_cfg)
        except (json.JSONDecodeError, TypeError):
            # 坏 JSON 不得静默当空配置（否则视觉旋钮全部丢失且无提示）
            raise ScreenIntentError(_error(
                f"widgets[{index}]({title}) 的 config 需要对象，收到无法解析的 JSON 字符串"))
    if not isinstance(raw_cfg, dict):
        raise ScreenIntentError(_error(
            f"widgets[{index}]({title}) 的 config 需要对象，实际收到 {type(raw_cfg).__name__}"))
    raw_cfg = dict(raw_cfg)
    # 字模引用：单个 id/code 不得被当字符串逐字枚举（历史隐患："12" 会被拆成 "1"、"2"）
    refs = w.get("component_ids") if w.get("component_ids") is not None else w.get("style_ref")
    if isinstance(refs, (str, int)) and not isinstance(refs, bool):
        refs = [refs]
    elif not isinstance(refs, list):
        refs = []
    _resolve_style_refs(refs, raw_cfg)

    # iframe: 无取数（不落数据集）
    if chart_type in _NO_SQL_TYPES:
        iframe_url = str(raw_cfg.get("url") or "").strip()
        if not iframe_url.startswith(("http://", "https://", "/")):
            raise ScreenIntentError(_error(
                f"widgets[{index}]({title}) iframe 组件需在 config.url 提供 http(s)/相对地址"))
        cfg = _sanitize_config(raw_cfg)
        if theme_mode:
            cfg["theme"] = theme_mode
        return {"title": title, "chart_type": chart_type, "intent": None, "dataset": None,
                "result": {"columns": [], "rows": [], "row_count": 0}, "cache": None,
                "config": cfg, "widget": w}

    intent = w.get("query") or w.get("semantic_query")
    if isinstance(intent, str):
        # 逐图 query 是对象；模型把它当 JSON 字符串传时按解析处理（parse_intent 只吃 dict）
        try:
            intent = json.loads(intent)
        except (json.JSONDecodeError, TypeError):
            raise ScreenIntentError(_error(
                f"widgets[{index}]({title}) 的 query 需要对象（声明式意图），收到无法解析的字符串"))
    if not isinstance(intent, dict) or not intent:
        raise ScreenIntentError(_error(f"widgets[{index}]({title}) 缺少 query(声明式语义意图)"))

    source = resolve_screen_source(
        {"site": w.get("site") or args.get("site"),
         "datasource": w.get("datasource") or args.get("datasource")},
        ctx, str(intent.get("object") or ""))
    payload = {**intent, "datasource_id": int(source)}
    if ctx is not None and getattr(ctx, "workspace_id", 0):
        payload.setdefault("workspace_id", int(ctx.workspace_id))
    q, err, notes = parse_intent(payload)
    if err:
        raise ScreenIntentError(_error(f"widgets[{index}]({title}) 查询意图不合法: {err}", notes=notes))

    binding, bind_warnings = await asyncio.to_thread(
        resolve_binding, q.object, datasource_id=int(source))
    if binding is None:
        raise ScreenIntentError(_error(
            f"对象 '{q.object}' 在本次生效数据源上未绑定到任何物理表。",
            warnings=[x for x in (bind_warnings or [])
                      if "catalog" not in str(x).lower() and "datasource" not in str(x).lower()],
            hint="未绑定 ≠ 无数据：这表示该源尚未为此对象建模/绑定物理表，不代表库里没数据；"
                 "严禁据此向用户回答“该站点暂无数据”。换个已建模对象（search_metadata / "
                 "knowledge_search）或先为该源生成业务本体。",
        ))
    # 本次生效源必须一起传：跨站建屏时 binding 属于该站源，缺省按会话源比对会被误拒
    await asyncio.to_thread(validate_binding, ctx, binding, int(source))

    dataset = await asyncio.to_thread(
        _materialize_dataset, identity=identity, screen_name=screen_name, title=title,
        chart_type=chart_type, object_key=q.object, source=int(source), intent=dict(intent))
    try:
        result = await asyncio.to_thread(_fetch_dataset, int(dataset["id"]), dict(intent), identity)
    except Exception as e:  # noqa: BLE001 — 原始细节仅进日志, 回分类文案
        raise ScreenIntentError(_safe_exec_error(e, f"widgets[{index}]({title}) 取数"))
    config = _dataset_chart_config(dict(intent), dataset, raw_cfg, theme_mode)
    # 生效站点（业务名）随图存档：整板重排（`update_data_screen` 的 relayout）只能拿
    # 存量图重建布局，丢了站点就把跨站屏排成单站一块、站点列对不齐。
    site_label = str(w.get("site") or args.get("site") or "").strip()
    if site_label:
        config["site"] = site_label
    cache = _chart_cache_json({"chart_type": chart_type, "config": config, "name": title},
                              result.get("columns"), result.get("rows"), _paging_extra(result))
    return {"title": title, "chart_type": chart_type, "intent": dict(intent), "dataset": dataset,
            "result": result, "cache": cache, "config": config, "widget": w}


def _resolve_style_refs(refs, merged_config: dict) -> None:
    """把字模引用(id 或 code)对应的 style_config.config 合并进图表 config。"""
    from backend.modules.viz.services.vis_library_service import (
        is_usable, vis_library_service)

    for ref in (refs or []):
        comp = None
        try:
            comp = vis_library_service.get_component(int(ref))
        except (TypeError, ValueError):
            comp = vis_library_service.get_component_by_code(str(ref))
        if not comp:
            continue
        if not is_usable(comp):
            # 已下架/已禁用的字模不参与组装：禁用是管理员显式停用，服务端照用等于绕过它
            logger.warning("[screen] 字模不可用已跳过 ref=%s code=%s is_active=%s is_enabled=%s",
                           ref, comp.get("code"), comp.get("is_active"), comp.get("is_enabled"))
            continue
        sc = comp.get("style_config") or {}
        cfg = sc.get("config") if isinstance(sc, dict) else None
        if isinstance(cfg, dict):
            for k, v in cfg.items():
                merged_config.setdefault(k, v)


def _widget_family(chart_type: str) -> str:
    """图表家族（决定自动布局落在哪一层）。"""
    if chart_type in _KPI_TYPES:
        return "kpi"
    if chart_type in _TREND_TYPES:
        return "trend"
    return "other"


def _hero_rank(chart_type: str) -> int:
    """主图优先级（越小越该占大位）：趋势 > 构成 > 地图 > 其他 > 表格。

    表格不当主图：它要整宽带才读得动（挤在 1/3 列里每列都要横向滚动）。
    """
    ct = _normalize_chart_type(chart_type or "bar")
    if ct in _TREND_TYPES:
        return 0
    if ct in _SHARE_TYPES:
        return 1
    if ct in _MAP_TYPES:
        return 2
    if ct in _TABLE_TYPES:
        return 4
    return 3


def _snap4(value: int) -> int:
    """取到 4px 行格（前端 PAGE_ROW=4）；不足一格按一格。"""
    return max(_ROW_UNIT, int(value) // _ROW_UNIT * _ROW_UNIT)


def _tile(total: int, parts: int, gap: int = 0) -> list[int]:
    """把 total 切成 parts 份（含 (parts-1)*gap 间隙），每份 4px 倍数，余量给末份。"""
    if parts <= 0:
        return []
    avail = total - gap * (parts - 1)
    base = _snap4(avail // parts)
    sizes = [base] * parts
    sizes[-1] = max(_ROW_UNIT, avail - base * (parts - 1))
    return sizes


def _card_min_h(chart_type: str) -> int:
    """前端 pageMinHeight：抬高是渲染层做的，服务端不守住就会被顶破下一带。"""
    ct = _normalize_chart_type(chart_type or "bar")
    if ct in _TABLE_TYPES:
        return _MIN_TABLE_H
    if ct in _KPI_TYPES:
        return _MIN_KPI_H
    return _MIN_CARD_H


def _band_floor(band: list[dict], stacked: int) -> int:
    """一带所需最小高：主图与右列辅图**并行**。辅图多于一张时纵向堆叠，需要堆叠总高。"""
    hero_min = _card_min_h(band[0].get("chart_type") or "")
    sides = [_card_min_h(w.get("chart_type") or "") for w in band[1:]]
    flat = max([hero_min] + sides)
    if stacked > 1 and sides:
        return max(flat, _ROW_GAP * len(sides) + sum(sides))
    return flat


def _alloc_band_heights(avail: int, floors: list[int], hero_first: bool) -> list[int] | None:
    """带高分配：首带明显高（主次来自高度差 + 宽度差），其余均分。

    任何一带低于自身最小高即回 None 由调用方换配方——**不得**压破最小高后交给渲染层抬高
    （抬高会顶到下一带，就是"卡片一张比一张靠下、右侧一大片空白"的斜阶梯）。
    """
    n = len(floors)
    usable = avail - _ROW_GAP * (n - 1)
    if usable < sum(floors):
        return None
    if n == 1:
        return [_snap4(min(_CARD_MAX_H, usable))]
    even = _snap4(usable // n)
    heights = [min(_CARD_MAX_H, max(floors[i], even)) for i in range(n)]
    if hero_first:
        heights[0] = min(_CARD_MAX_H, max(floors[0], _snap4(usable - even * (n - 1))))
    deficit = sum(heights) - usable
    for i in range(n - 1, -1, -1):        # 先削辅带，保住首带主次
        if deficit <= 0:
            break
        cut = min(deficit, heights[i] - floors[i]) // _ROW_UNIT * _ROW_UNIT
        heights[i] -= cut
        deficit -= cut
    if deficit > 0:
        return None
    spare = usable - sum(heights)
    if spare >= _ROW_UNIT:
        idx = 0 if hero_first else n - 1
        heights[idx] += min(_snap4(spare), spare)
    return heights


def _plan_content_bands(rest: list[dict], avail: int) -> tuple[list[list[dict]], list[int], bool] | None:
    """内容区配方搜索：返回 (各带成员, 各带高, 首带是否为 hero 结构)。

    依次尝试「hero + 右列 2 张」→「hero + 右列 1 张」→「每行 N 张的等高网格」，
    每档再放宽每行张数（4→6→8）直到放得下；全都不行返回 None（调用方显式拒）。
    """
    n = len(rest)
    hero_ok = bool(rest) and _hero_rank(rest[0].get("chart_type") or "") < 4
    for stacked in (2, 1, 0):
        if stacked > 0 and (not hero_ok or n == 1):
            continue
        head = stacked + 1 if stacked > 0 else 0
        for per_row in (_MAX_PER_ROW, 6, 8):
            if stacked > 0:
                bands = [rest[:head]] + [rest[i:i + per_row] for i in range(head, n, per_row)]
            else:
                bands = [rest[i:i + per_row] for i in range(0, n, per_row)]
            bands = [b for b in bands if b]
            floors = [_band_floor(b, len(b) - 1 if stacked > 0 and i == 0 else 0)
                      for i, b in enumerate(bands)]
            heights = _alloc_band_heights(avail, floors, stacked > 0)
            if heights:
                return bands, heights, stacked > 0
    return None


def _idle_rows(clean: list[dict]) -> list[dict]:
    """整行留白：把纵向有重叠的卡归成一带，按横向**并集**算实际占用宽度。

    不能直接加宽（主图带的右列是两张辅图上下堆叠，加宽会算出 2560 > 1920 的假"占满"，
    也会把铺得正好的一行误报成留白）。
    """
    groups: list[dict] = []
    for r in sorted(clean, key=lambda r: (r["y"], r["x"])):
        hit = [g for g in groups if r["y"] < g["y1"] and r["y"] + r["h"] > g["y0"]]
        if hit:
            g = hit[0]
            for extra in hit[1:]:                       # 跨带的卡把两组并起来
                g["items"] += extra["items"]
                groups.remove(extra)
            g["items"].append(r)
            g["y0"], g["y1"] = min(g["y0"], r["y"]), max(g["y1"], r["y"] + r["h"])
        else:
            groups.append({"y0": r["y"], "y1": r["y"] + r["h"], "items": [r]})
    idle: list[dict] = []
    for g in sorted(groups, key=lambda g: g["y0"]):
        cover, cur = 0, None
        for x0, x1 in sorted((it["x"], it["x"] + it["w"]) for it in g["items"]):
            if cur is None or x0 > cur[1]:
                cover += x1 - x0
                cur = (x0, x1)
            elif x1 > cur[1]:
                cover += x1 - cur[1]
                cur = (cur[0], x1)
        if _CANVAS_W - cover > 0:
            idle.append({"y": g["y0"], "unused_px": _CANVAS_W - cover})
    return idle


def _layout_audit(rects: list[dict]) -> dict:
    """版面自检：重叠 / 超出画布 / 整行留白 / 挤破前端最小高 / 被截顶 / 有无主次。

    坐标是模型自己填的，光靠生成端"觉得排好了"没有依据（历史缺陷：五连 KPI 坐标互相压住，
    渲染层避让后成一排斜阶梯，用户截图才发现）。这里把判据算好随结果回传，
    模型可据此在交付前重排——不静默、也不代替重排。

    `squeezed` 只对**带 chart_type 的矩形**判定：前端会按类型把过矮的卡抬到最小高
    （表格 360 / KPI 128 / 其他 280），抬起来就压下一带；不带类型的矩形（历史数据、
    非图卡）不猜类型，不据此判失败。
    """
    clean = [{"title": str(r.get("title") or ""), "chart_type": r.get("chart_type"),
              "x": int(r.get("x") or 0), "y": int(r.get("y") or 0),
              "w": int(r.get("w") or 0), "h": int(r.get("h") or 0)} for r in rects]
    overlaps = [[a["title"], b["title"]] for i, a in enumerate(clean) for b in clean[i + 1:]
                if a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"]
                and a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]]
    outside = [r["title"] for r in clean if r["x"] < 0 or r["y"] < 0
               or r["x"] + r["w"] > _CANVAS_W or r["y"] + r["h"] > _CANVAS_H]
    squeezed = [r["title"] for r in clean
                if r["chart_type"] and 0 < r["h"] < _card_min_h(r["chart_type"])]
    over_tall = [r["title"] for r in clean if r["h"] > _CARD_MAX_H]
    idle = _idle_rows(clean)
    # 主次：内容卡（非 KPI 行）全等宽等高 = "一排一样大"，正是用户反馈的没审美
    content = [r for r in clean if _normalize_chart_type(r.get("chart_type") or "") not in _KPI_TYPES]
    hierarchy = len(content) < 3 or len({(r["w"], r["h"]) for r in content}) > 1
    ok = not overlaps and not outside and not squeezed
    audit = {"ok": ok, "overlaps": overlaps, "out_of_canvas": outside, "idle_rows": idle,
             "squeezed": squeezed, "over_tall": over_tall, "hierarchy": hierarchy}
    if not ok:
        audit["hint"] = ("版面自检未通过：先修重叠/越界/带高不足再交付（可省略 position 让服务端自动布局；"
                         "异构卡不当对比项，见 layout-guide）")
    elif not hierarchy:
        audit["hint"] = ("版面所有卡片等宽等高（没有主次）：减少图表、或给 emphasis 指定主图后重排"
                        "（服务端自动布局已按 hero+辅图分层，手写 position 才会 flatten）")
    return audit


def _auto_layout(widgets: list[dict], emphasis: dict | None = None) -> list[tuple]:
    """为未指定位置的组件自动布局: 铺满 1920x1080 画布。

    两条口径（2026-11-17 直建整改，反馈："多站要放一块板，而且排布很乱"）：

    * **多站一屏**（widget 上出现 ≥2 个不同 site）：按**站点列**排 —— 一列一个站，
      列内自上而下 KPI → 趋势 → 其他。同屏横向对比各站，而不是每站各建一块板。
    * **单站/无站**：KPI 一行（单卡最宽 480，左对齐不拉伸），内容区按**带**出主次
      （首带主图 2/3 宽 + 右列辅图，其余带每行 2~4 张；带高拉开），不再一律等分。

    `emphasis`（{hero, detail} 图标题）只影响单站分层；跨站对比本来就该等大，忽略它。
    坐标只保证相对比例（播放页按响应式列格重排，见 layout-guide）。
    """
    enriched = [
        (w, _widget_family(_normalize_chart_type(w.get("chart_type") or "bar")),
         str(w.get("site") or "").strip())
        for w in widgets
    ]
    sites = [s for s in dict.fromkeys(site for _, _, site in enriched) if s]
    if len(sites) >= 2:
        return _layout_by_site(enriched, sites)
    return _layout_stacked(enriched, emphasis)


def _layout_by_site(enriched: list, sites: list[str]) -> list[tuple]:
    """站点矩阵布局：**行 = 指标家族、列 = 站点**。

    早期写法是“列内自上而下排”，某站少一张图（如只有中国有状态构成）时，
    其余站的趋势图会被拉成两倍高 —— 横向对比失真（反馈：卡片大小不一、排布乱）。
    改成矩阵后同族图同行等高、同城图同列对齐，缺格留空不拉伸。

    例外：「other」家族（状态构成/表格等异构卡）不是跨站对比项，缺格只会让整行右侧
    空一大片（反馈：右侧错位空白）。该家族未铺满所有站点时改为整行均分并居中。

    跨站对比保持**等大**（主次由站点列承担，不靠卡片大小），但顶部留白、带高按
    前端行格/最小高/600px 截顶收敛，避免抬高压破下一带。
    """
    col_w = _CANVAS_W // len(sites)
    grid: dict[tuple, list] = {(fam, site): [] for fam in ("kpi", "trend", "other") for site in sites}
    loose: list = []
    for w, fam, site in enriched:
        if site in sites:
            grid[(fam, site)].append(w)
        else:
            loose.append((w, fam))

    def _cell_need(items: list) -> int:
        """同站内纵向堆几张，就需要几个最小高 + 间隙（渲染层抬高会顶破下一带）。"""
        return _ROW_GAP * (len(items) - 1) + sum(_card_min_h(it.get("chart_type") or "") for it in items)

    fams = [f for f in ("kpi", "trend", "other") if any(grid[(f, s)] for s in sites)]
    floors = {f: max([_cell_need(grid[(f, s)]) for s in sites if grid[(f, s)]] or [_MIN_CARD_H])
              for f in fams}
    kpi_h = _snap4(min(_CARD_MAX_H, max(_KPI_H, floors.get("kpi", _KPI_H)))) if "kpi" in fams else 0
    top = _TOP_MARGIN + kpi_h + (_ROW_GAP if kpi_h else 0)
    rest_fams = [f for f in fams if f != "kpi"]
    avail = _CANVAS_H - top - (_ROW_GAP if loose else 0)
    # 带太多塞不进最小高时回退均分（版面自检会如实报出挤压，不静默）
    band_h = (_alloc_band_heights(avail, [floors[f] for f in rest_fams], False)
              or _tile(avail, len(rest_fams), _ROW_GAP)) if rest_fams else []

    laid: list[tuple] = []
    for fam in fams:
        if fam == "kpi":
            y, h = _TOP_MARGIN, kpi_h
        else:
            idx = rest_fams.index(fam)
            y, h = top + sum(band_h[:idx]) + _ROW_GAP * idx, band_h[idx]
        covered = [s for s in sites if grid[(fam, s)]]
        if fam == "other" and len(covered) < len(sites):
            items = [w for s in sites for w in grid[(fam, s)]]
            cw = min(_CANVAS_W // len(items), 2 * col_w, _CANVAS_W * 2 // 3)  # 最多 2 列宽且不超 2/3 屏
            x0 = (_CANVAS_W - cw * len(items)) // 2           # 居中，两侧留白对称
            for j, w in enumerate(items):
                laid.append((w, {"x": x0 + j * cw, "y": y, "w": cw, "h": h}))
            continue
        for i, site in enumerate(sites):
            items = grid[(fam, site)]
            if not items:
                continue                      # 缺格留空，不把同城图拉伸占位
            each = _tile(h, len(items), _ROW_GAP) if len(items) > 1 else [h]
            oy = 0
            for j, w in enumerate(items):
                laid.append((w, {"x": i * col_w, "y": y + oy, "w": col_w, "h": each[j]}))
                oy += each[j] + _ROW_GAP

    # 未写 site 的图（跨站屏里漏标）落到底部整行，不静默丢图
    if loose:
        row_h = _snap4(max(_MIN_CARD_H, _CANVAS_H // max(1, len(loose) + 2)))
        width = _CANVAS_W // len(loose)
        y = _CANVAS_H - row_h
        for j, (w, _) in enumerate(loose):
            laid.append((w, {"x": j * width, "y": y, "w": width, "h": row_h}))
    return laid


def _order_for_hierarchy(rest: list[dict], emphasis: dict) -> list[dict]:
    """内容图读序：指定主图 → 主图优先级（趋势优先、表格垫后）→ 指定整宽明细 → 其余原序。"""
    hero = str((emphasis or {}).get("hero") or "").strip()
    detail = str((emphasis or {}).get("detail") or "").strip()
    titles = [str(w.get("title") or w.get("name") or "").strip() for w in rest]
    unknown = [(label, want) for label, want in (("emphasis.hero", hero), ("emphasis.detail", detail))
               if want and want not in titles]
    if unknown:
        label, want = unknown[0]
        raise ScreenIntentError(_error(
            f"{label} 「{want}」没有匹配到任何图表标题；现有标题：{'、'.join(t for t in titles if t)}。"
            "请原样使用图表标题，或去掉该指定让服务端按图型自动选主图。"))

    def _key(pair):
        i, w = pair
        title = str(w.get("title") or w.get("name") or "").strip()
        if title and title == hero:
            return (-2, 0, i)
        if title and title == detail:
            return (10, 0, i)
        return (0, _hero_rank(w.get("chart_type") or ""), i)

    return [w for _, w in sorted(enumerate(rest), key=_key)]


def _layout_stacked(enriched: list, emphasis: dict | None = None) -> list[tuple]:
    """单站/无站布局：顶部留白 → KPI 一行 → 内容区**按带出主次**。

    反馈（2026-11）两条一起修：
    * "输出的看板图标大小和布局没有审美，全是一样的大小和排布" —— 旧写法只在两张内容图
      时做 2/3+1/3，三张起一律 cols=2/3 等分等高；现在首带是主图（2/3 宽）+ 右列辅图，
      带高也拉开，明细/表格落整宽带。
    * "看板顶部要留一些空隙，不要完全靠上" —— 内容区从 `_TOP_MARGIN` 起排，不再贴画布上沿。

    带高受三条前端事实约束：`h ≤ 600`（截顶）、`h ≥` 类型最小高、`y/h` 取 4 的倍数；
    放不下时放宽每行张数，仍放不下就**显式拒绝并建议拆板**（不静默挤压）。
    """
    kpis = [w for w, fam, _ in enriched if fam == "kpi"]
    rest = [w for w, fam, _ in enriched if fam != "kpi"]

    laid: list[tuple] = []
    top = _TOP_MARGIN
    if kpis:
        # 单卡最宽 480：整屏一个 KPI 时拉满会让迷你趋势被拉平、数字悬空
        kw = min(_KPI_MAX_W, max(_KPI_MIN_W, _CANVAS_W // len(kpis)))
        for i, w in enumerate(kpis):
            laid.append((w, {"x": i * kw, "y": top, "w": kw, "h": _KPI_H}))
        top += _KPI_H + _ROW_GAP
    if not rest:
        return laid
    if len(rest) > _MAX_CONTENT_CARDS:
        raise ScreenIntentError(_error(
            f"内容图表 {len(rest)} 张超过单屏可分层的上限 {_MAX_CONTENT_CARDS} 张"
            f"（1920×1080 画布，另可加一行 KPI）；请减少图表或拆成多块大屏——"
            "硬塞只能等分挤压，会被渲染层抬高压破下一带（斜阶梯），属用户可见的版面缺陷。"))

    plan = _plan_content_bands(_order_for_hierarchy(rest, emphasis or {}), _CANVAS_H - top)
    if plan is None:
        raise ScreenIntentError(_error(
            f"内容图表 {len(rest)} 张 + KPI 行在 1920×1080 内排不出「互不重叠且不低于前端最小高」"
            "的版面（表格带要 360px、其他图 280px）；请减少图表、把表格改到别的屏，"
            "或删掉 position 让服务端在更少图数下重排。"))
    bands, heights, hero_mode = plan
    y = top
    for bi, band in enumerate(bands):
        h = heights[bi]
        stacked = len(band) - 1 if (hero_mode and bi == 0) else 0
        if stacked > 0:
            hero_w = _CANVAS_W * 2 // 3
            laid.append((band[0], {"x": 0, "y": y, "w": hero_w, "h": h}))
            side_h = _tile(h, stacked, _ROW_GAP)
            oy = 0
            for j, w in enumerate(band[1:]):
                laid.append((w, {"x": hero_w, "y": y + oy,
                                 "w": _CANVAS_W - hero_w, "h": side_h[j]}))
                oy += side_h[j] + _ROW_GAP
        else:
            widths = _tile(_CANVAS_W, len(band))
            x = 0
            for w, cw in zip(band, widths):
                laid.append((w, {"x": x, "y": y, "w": cw, "h": h}))
                x += cw
        y += h + _ROW_GAP
    return laid


# ── 工具 handler(SDK 无关) ─────────────────────────────────────────────

def _mount_chart(chart_service, dashboard_id: int, item: dict, position: dict, identity: dict) -> int:
    """把 `_prepare_widget` 的产物挂成数据集图并预填首屏缓存（建屏与改板加图同一通道）。

    图表行不存口径：SQL 由数据集身份持有（`source_type='dataset'` + `source_id`=数据集 id）。
    """
    payload: dict = {
        "name": item["title"],
        "chart_type": item["chart_type"],
        "config": item["config"],
        "position": position,
    }
    if item["dataset"]:
        payload.update({
            "source_type": "dataset",
            "source_id": int(item["dataset"]["id"]),
            "query_source": "dataset",
            "semantic_query": None,
        })
    cid = chart_service.create_chart(dashboard_id, payload, identity=identity)
    if item["cache"]:
        # 预填首屏缓存：走看板同一份序列化（行限/字节兜底/完整性标记同口径）
        from backend.common.db import execute_write

        execute_write("UPDATE adh_charts SET data_cache=%s WHERE id=%s", (item["cache"], int(cid)))
    return cid


async def create_data_screen(args: dict) -> dict:
    """创建一个可视化数据大屏：逐图落数据集（adh_datasets）并挂成数据集图。
    
    2026-11-17 拍板：会话内**不再走设计面板**（不让人填 SQL、不让人确认 SQL 再发布）。
    每张图先按声明式意图建/复用语义型数据集，再把图挂到该数据集上：看板只做可视化装配，
    口径留在数据集（BI 治理层，有字段声明/角色授权/血缘）。系统域也不再重定向到设计工具。
    """
    args = args or {}
    try:
        name = str(args.get("name") or "").strip()
        widgets = _coerce_struct(args.get("widgets"), "widgets", (list, dict))
        raw_theme = _coerce_theme(_coerce_struct(args.get("theme"), "theme", (dict, str)))
        style = _coerce_struct(args.get("style"), "style", dict) or {}
    except ScreenIntentError as e:
        return e.response
    if isinstance(widgets, dict):        # 单品对象容错：语义无歧义，包成数组继续
        widgets = [widgets]
    widgets = widgets or []
    if not name:
        return _error("name is required")
    if not widgets:
        return _error("widgets is required(至少一个图表对象)")
    bad = next((i for i, w in enumerate(widgets) if not isinstance(w, dict)), None)
    if bad is not None:
        return _error(
            f"widgets[{bad}] 必须是对象，当前是 {type(widgets[bad]).__name__}；"
            "widgets 是对象数组 [{title, chart_type, query, ...}]")
    if len(widgets) > _MAX_WIDGETS:
        return _error(f"组件数量 {len(widgets)} 超过上限 {_MAX_WIDGETS}；请拆成多块大屏或减少图表")
    if args.get("filters"):
        # 数据集正文/意图已固化为本次口径，看板参数没有消费方 → 生成了就是死控件
        return _error(
            "直建大屏不支持交互筛选参数：每张图的口径存放在数据集里（已固定为本次意图），"
            "看板参数没有消费方，生成了就是“能填不出数”的死控件。确实需要参数控件（日期区间/"
            "下拉/搜索按钮等）时，请告知用户：到「数据集」页把正文改成 ${参数} 外置并声明入参，"
            "再在看板编辑器挂 widget_* 控件绑定该参数（口径见 screen-builder 技能 B2.5）；"
            "控件没有取数语义，不要把它当图表提交。")
    
    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import (
        ChartService, DashboardService, _invalidate_dashboard_payload)
    
    ctx = ExecutionContextVar.get()
    try:
        require_screen_perms(ctx)
    except PermissionError as e:
        return _error(str(e))
    identity = identity_of(ctx)
    
    # 大屏主题 + 字模引用(背景/配色/卡片/布局/装饰), 寄存 filters
    theme = {k: v for k, v in (raw_theme or {}).items() if k in _THEME_KEYS and v not in (None, "")}
    theme_mode = theme.get("mode") if theme.get("mode") in ("light", "dark") else None
    filters = {}
    if theme:
        filters["theme"] = theme
    if style:
        filters["components"] = style
    
    # 逐图: 校验意图 + 定源 + 落数据集 + 预取数（失败即整单不落库）
    prepared = []
    for i, w in enumerate(widgets):
        try:
            prepared.append(await _prepare_widget(w, i, args, ctx, identity, name, theme_mode))
        except ScreenIntentError as e:
            return e.response
        except Exception as e:
            return _safe_exec_error(e, f"create widget {i + 1}")
    
    # 版面在落库前算：排不出主次/放不下就整单拒掉，不留 0 图空屏（此前布局在 create 之后，
    # 布局报错会留下半成品并走"清看板"路径，用户看到的是一个莫名消失的请求）。
    # 布局要按**生效站点**分组：widget 没写 site 时用顶层 site 补齐，
    # 否则跨站屏会被当成单站排成一块，站点列对不齐（2026-11 反馈：用户要一屏看 5 个站）。
    top_site = str(args.get("site") or "").strip()
    layout_widgets = []
    for item in prepared:
        w = item["widget"]
        if not str(w.get("site") or "").strip() and top_site:
            w = {**w, "site": top_site}
            item["widget"] = w
        layout_widgets.append(w)
    try:
        emphasis = _coerce_struct(args.get("emphasis"), "emphasis", dict) or {}
        positions = {id(widget): pos for widget, pos in _auto_layout(layout_widgets, emphasis)}
    except ScreenIntentError as e:
        return e.response
    except Exception as e:  # noqa: BLE001 — 版面是服务端算的，算不出来属系统侧问题
        return _safe_exec_error(e, "auto layout")

    # 创建大屏 + 图表。落库异常必须按错因分类回给模型：裸异常会被工具壳兜成
    # 「权限、资源或执行环境不满足要求」，把 schema 漂移（如 query_source 枚举缺值）
    # 误报成权限问题（no-silent-degradation）。中途失败一并清掉半成品看板,
    # 不留 0 图空屏误导用户。
    try:
        dashboard_id = DashboardService().create_dashboard({
            "name": name,
            "description": args.get("description") or "",
            "params": [],
            "filters": filters,
            "status": "enabled",
            "is_public": False,
            "workspace_id": identity["workspace_id"],
        }, identity["user_id"])
    except Exception as e:  # noqa: BLE001 — 原始细节仅进日志, 回分类文案
        return _safe_exec_error(e, "create dashboard")

    try:
        granted_roles = _grant_dashboard_to_user_roles(
            identity["user_id"], dashboard_id, identity.get("role") or "")
    except ScreenIntentError as e:
        # 授不上权 = 一块谁都看不见的半成品，清掉再回错因（留着它只会变成“建好了但找不到”）
        try:
            DashboardService().delete_dashboard(dashboard_id, identity["user_id"])
        except Exception:  # noqa: BLE001 — 清理失败不得盖住原始错因
            logger.exception("[screen] 清理未授权看板失败 dashboard=%s", dashboard_id)
        return e.response

    chart_service = ChartService()
    try:
        layout_log = []
        for item in prepared:
            custom_pos = item["widget"].get("position") or {}
            position = custom_pos if all(k in custom_pos for k in ("x", "y", "w", "h")) else positions.get(id(item["widget"]), {"x": 0, "y": 0, "w": 640, "h": 360})
            layout_log.append({"title": item["title"], "chart_type": item["chart_type"],
                               **{k: position.get(k) for k in ("x", "y", "w", "h")}})
            _mount_chart(chart_service, dashboard_id, item, position, identity)
        _invalidate_dashboard_payload(dashboard_id)
    except Exception as e:  # noqa: BLE001 — 原始细节仅进日志, 回分类文案
        try:
            DashboardService().delete_dashboard(dashboard_id, identity["user_id"])
        except Exception:  # noqa: BLE001 — 清理失败不得盖住原始错因
            logger.exception("[screen] 清理半成品看板失败 dashboard=%s", dashboard_id)
        return _safe_exec_error(e, "create charts")
    
    url = f"/screen/{dashboard_id}"
    # 逐图取数结果记账: 零行与满行在返回里一模一样的话, 模型会把"创建成功"当"数据正常"
    # 报给用户(空图被静默吞掉)。行数不属于黑盒项。
    data_audit = [{
        "title": item["title"],
        "chart_type": item["chart_type"],
        "row_count": (None if item["chart_type"] in _NO_SQL_TYPES else
                      int(item["result"].get("row_count") if item["result"].get("row_count") is not None
                          else len(item["result"].get("rows") or []))),
    } for item in prepared]
    empty_charts = [a["title"] for a in data_audit if a["row_count"] == 0]
    layout_audit = _layout_audit(layout_log)
    message = f"数据大屏「{name}」已创建({len(prepared)} 个图表), 访问路径: {url}"
    if empty_charts:
        message += (f"；其中 {len(empty_charts)} 个图表取数为空: {'、'.join(empty_charts)} —— "
                    "空图不是成功, 请核对时间窗/过滤条件/口径覆盖, 并向用户逐图说明(不得换近似指标凑数)")
    if not layout_audit["ok"]:
        message += (f"；版面自检未通过(重叠 {len(layout_audit['overlaps'])} 处、"
                    f"越界 {len(layout_audit['out_of_canvas'])} 张、"
                    f"带高不足 {len(layout_audit['squeezed'])} 张) —— 请按 layout-guide 重排后再交付")
    elif layout_audit["hierarchy"] is False:
        message += ("；版面所有卡片等宽等高（没有主次）—— 减少图数或用 emphasis 指定主图后重排，"
                    "不要把“已创建 N 个图表”当版面合格")
    return _text({
        "dashboard_id": dashboard_id,
        "name": name,
        "url": url,
        "charts": len(prepared),
        "data_audit": data_audit,
        "empty_charts": empty_charts,
        "datasets": [{"title": it["title"],
                      "dataset": (it["dataset"] or {}).get("name")} for it in prepared],
        "style": style or None,
        "layout": layout_log,
        "layout_audit": layout_audit,
        # 可看性记账：非 admin 建完能不能在自己的「数据看板」页看到，取决于这次补授
        "visibility": {"granted_roles": len(granted_roles),
                       "note": ("管理员全可见" if not granted_roles
                                else f"已把看板授权给当前账号的 {len(granted_roles)} 个角色，"
                                     "本人在「数据看板」即可看到；其他人需按角色授权")},
        # 会话内的可点入口：chat 前端按这个渲染「预览大屏」按钮（不再只靠模型
        # 把 /screen/<id> 当纯文本讲给用户——那样用户点不了，等于没有预览入口）。
        "actions": [{"type": "open_screen", "label": "预览大屏",
                     "dashboard_id": dashboard_id, "url": url}],
        "message": message,
    })
    

# 不得回给 LLM 的基础设施/物理层配置键(数据源黑盒 §7)
_LEAK_CONFIG_KEYS = {"datasource_id", "source_id", "physical_table", "catalog_ref", "catalog", "base_sql", "secured_sql", "provenance", "account", "host", "ip"}


def _sanitize_config(cfg) -> dict:
    """剔除 config 里的数据源/物理层标识, 只回视觉配置给 LLM。"""
    if not isinstance(cfg, dict):
        return {}
    return {k: v for k, v in cfg.items() if k not in _LEAK_CONFIG_KEYS}


def _chart_summary(c: dict) -> dict:
    """图表摘要: 剥离 sql_query / 数据源 id, 保留视觉配置与数据集业务名(数据源黑盒 §7)。"""
    cfg = c.get("config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    return {
        "chart_id": c.get("id"),
        "title": c.get("name"),
        "chart_type": c.get("chart_type"),
        "dataset_name": (cfg or {}).get("dataset_name") if isinstance(cfg, dict) else None,
        "config": _sanitize_config(cfg),
        "position": c.get("position"),
    }


async def get_data_screen(args: dict) -> dict:
    """获取数据大屏详情(含图表意图与配置, 不返回裸 SQL/数据源/数据行)。"""
    args = args or {}
    dashboard_id = int(args.get("dashboard_id") or 0)
    if not dashboard_id:
        return _error("dashboard_id is required")

    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import DashboardService

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0

    dashboard = DashboardService().get_dashboard(dashboard_id, user_id, enforce_visibility=False)
    if not dashboard:
        return _error(f"大屏 {dashboard_id} 不存在")

    charts_summary = [_chart_summary(c) for c in (dashboard.get("charts") or [])]
    return _text({
        "dashboard_id": dashboard_id,
        "name": dashboard.get("name"),
        "description": dashboard.get("description"),
        "url": f"/screen/{dashboard_id}",
        "params": dashboard.get("params"),
        "filters": dashboard.get("filters"),
        "charts": charts_summary,
        "chart_count": len(charts_summary),
    })


async def update_data_screen_chart(args: dict) -> dict:
    """更新大屏中的某个图表：只改标题/类型/位置→原样写；带 query→重落数据集并换图。

    2026-11-17 拍板：这里不再重定向到设计工具（那是“对话里打开设计面板”的直接根因）。
    换数据 = 重新走直建通道（意图→数据集→数据集图），图表的 `sql_query`/`semantic_query`
    同批清空（残留会让图被数据集闸门判成“人写了 SQL”而锁死）。
    """
    args = args or {}
    dashboard_id = int(args.get("dashboard_id") or 0)
    chart_id = int(args.get("chart_id") or 0)
    if not dashboard_id or not chart_id:
        return _error("dashboard_id 和 chart_id 都是必填项")

    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import ChartService, DashboardService, _invalidate_dashboard_cache
    from backend.modules.viz.services.dashboard_service import _invalidate_dashboard_payload

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0

    # AS-BOT 大屏通道: 访问控制由 resource_guard 的 owner/is_public 策略承担,
    # 不适用角色可见性裁决(看板"可看"改造的明确边界)。
    dashboard = DashboardService().get_dashboard(dashboard_id, user_id, enforce_visibility=False)
    if not dashboard:
        return _error(f"大屏 {dashboard_id} 不存在")

    existing = next((c for c in (dashboard.get("charts") or []) if c.get("id") == chart_id), None)
    if not existing:
        return _error("更新失败, 图表不存在")

    # §6: 拒收 LLM 直传裸 SQL
    leaked = [k for k in _SQL_FIELDS if isinstance(args.get(k), str) and args.get(k).strip()]
    if leaked:
        return _error(f"不允许直接提供 SQL 字段({','.join(leaked)}); 请用 query 传声明式语义意图。")

    try:
        intent = _coerce_struct(args.get("query") if args.get("query") is not None
                                else args.get("semantic_query"), "query", dict)
        cfg_arg = _coerce_struct(args.get("config"), "config", dict)
        pos_arg = _coerce_struct(args.get("position"), "position", dict)
    except ScreenIntentError as e:
        return e.response

    update_data: dict = {}
    if args.get("title"):
        update_data["name"] = str(args["title"]).strip()
    if args.get("chart_type"):
        ct = _normalize_chart_type(args["chart_type"])
        if ct.startswith("widget_"):
            return _error(
                f"{ct} 是参数控件：直建/改图通道不产控件（控件绑定的变量没有消费方，填了不出数）。"
                "需要筛选交互请告知用户：数据集正文改 ${变量} 外置 + 声明入参，再到看板编辑器挂 widget_* 绑定；"
                "口径见 screen-builder 技能 B2.5")
        if ct not in _CHART_TYPES:
            return _error(f"不支持的图表类型: {args['chart_type']}, 可选: {', '.join(sorted(_CHART_TYPES))}")
        update_data["chart_type"] = ct
    if cfg_arg:
        update_data["config"] = cfg_arg
    if pos_arg:
        update_data["position"] = pos_arg

    row_count: int | None = None
    dataset_name: str | None = None
    cache: str | None = None
    if intent:
        title = str(args.get("title") or existing.get("name") or "图表").strip()
        chart_type = str(args.get("chart_type") or existing.get("chart_type") or "bar").strip().lower()
        # 只换口径不动几何: 原图位置由调用方显式传入时才改
        widget = {
            "title": title, "chart_type": chart_type, "query": intent,
            "site": args.get("site"), "datasource": args.get("datasource"),
        }
        merged_visual = existing.get("config") if isinstance(existing.get("config"), dict) else {}
        if cfg_arg:
            merged_visual = {**merged_visual, **cfg_arg}
        widget["config"] = merged_visual
        theme_mode = merged_visual.get("theme") if merged_visual.get("theme") in ("light", "dark") else None
        try:
            require_screen_perms(ctx)          # 重落数据集 → 需 dashboard:manage + dataset:manage
            identity = identity_of(ctx)
            item = await _prepare_widget(
                widget, 0, args, ctx, identity, str(dashboard.get("name") or title), theme_mode)
        except ScreenIntentError as e:
            return e.response
        except PermissionError as e:
            return _error(str(e))
        except Exception as e:
            return _safe_exec_error(e, "update chart")
        row_count = int(item["result"].get("row_count")
                        if item["result"].get("row_count") is not None
                        else len(item["result"].get("rows") or []))
        dataset_name = (item["dataset"] or {}).get("name")
        cache = item["cache"]
        update_data["config"] = item["config"]
        if item["dataset"]:
            # 图表只认数据集通道: SQL 由数据集身份持有; 旧 SQL/意图必须同批清空
            update_data.update({
                "source_type": "dataset",
                "source_id": int(item["dataset"]["id"]),
                "query_source": "dataset",
                "semantic_query": None,
                "sql_query": None,
            })
    elif update_data:
        # 纯视觉/标题编辑：不碰数据集，只要看板写权限
        try:
            require_screen_perms(ctx, need_dataset=False)
        except PermissionError as e:
            return _error(str(e))

    if not update_data:
        return _error("没有提供任何要更新的字段(title/chart_type/query/config/position)")

    # 纯视觉编辑保留原图其余 config: 先读原图再浅合并（换意图时 config 由直建通道重建）
    if "config" in update_data and not intent and isinstance(existing.get("config"), dict):
        update_data["config"] = {**existing["config"], **update_data["config"]}

    try:
        success = ChartService().update_chart(dashboard_id, chart_id, update_data,
                                              identity=identity_of(ctx))
        if not success:
            return _error("更新失败, 图表不存在或无变化")

        if cache:
            # 预填缓存走同一份序列化（行限/字节兜底/完整性标记同口径），创建后单独写
            from backend.common.db import execute_write

            execute_write("UPDATE adh_charts SET data_cache=%s WHERE id=%s", (cache, int(chart_id)))
        _invalidate_dashboard_payload(dashboard_id)
    except Exception as e:  # noqa: BLE001 — 原始细节仅进日志, 回分类文案
        return _safe_exec_error(e, "update chart persist")
    _invalidate_dashboard_cache(user_id)

    # 版面自检必须在**整板**上算：只挪一张图也可能压住邻居（逐图改位置正是模型的常见动作），
    # 单图视角看永远"没重叠"。
    rects = []
    for c in (dashboard.get("charts") or []):
        pos = c.get("position") or {}
        if isinstance(pos, str):
            try:
                pos = json.loads(pos)
            except Exception:  # noqa: BLE001 — 脏数据不得盖住主流程, 仅跳过该图几何
                pos = {}
        rects.append({"id": c.get("id"), "title": c.get("name"), **pos})
    if pos_arg or update_data.get("name"):
        for r in rects:
            if r.get("id") != chart_id:
                continue
            r.update({k: pos_arg[k] for k in ("x", "y", "w", "h") if k in (pos_arg or {})})
            # 名字也同步成本次改后的：自检报告里指旧名会让人找错图
            if update_data.get("name"):
                r["title"] = update_data["name"]
    layout_audit = _layout_audit(rects)

    message = f"图表已更新, 访问路径: /screen/{dashboard_id}"
    if row_count == 0:
        message += ("；本次取数为空 —— 空图不是成功, 请核对时间窗/过滤条件/口径覆盖"
                    "并向用户说明(不得换近似指标凑数)")
    if not layout_audit["ok"]:
        message += (f"；版面自检未通过(重叠 {len(layout_audit['overlaps'])} 处、"
                    f"越界 {len(layout_audit['out_of_canvas'])} 张、"
                    f"带高不足 {len(layout_audit['squeezed'])} 张) —— 请按 layout-guide 重排后再交付")
    elif layout_audit["hierarchy"] is False:
        message += ("；版面所有卡片等宽等高（没有主次）—— 减少图数或用 emphasis 指定主图后重排，"
                    "不要把“已创建 N 个图表”当版面合格")
    return _text({
        "dashboard_id": dashboard_id,
        "chart_id": chart_id,
        "url": f"/screen/{dashboard_id}",
        "updated_fields": list(update_data.keys()),
        "dataset_name": dataset_name,
        "row_count": row_count,
        "layout_audit": layout_audit,
        "message": message,
    })


# ── 板级工具：找板 / 原地改板 / 删板 ─────────────────────────────────────────
#
# 「每次让改板都新建一块」的根因是这一层入口缺失：旧工具面只有 `get_data_screen`
# （要数字 dashboard_id）和 `update_data_screen_chart`（要数字 chart_id），模型既没有
# 按业务名找板的通道，也没有板级改法，结构性修改只能退回 create。

_DASHBOARD_STATUSES = ("designing", "enabled", "closed")


def _json_dict(value) -> dict:
    """JSON 字段归一成 dict（库里存的可能是字符串）。无法解析回 {}，由调用方如实判定。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _chart_geom(c: dict) -> dict:
    """存量图表的版面事实：标题/类型/站点业务名 + 矩形（整板自检与重排都吃这一份）。"""
    pos = _json_dict(c.get("position"))
    cfg = _json_dict(c.get("config"))
    return {
        "chart_id": int(c["id"]),
        "title": str(c.get("name") or ""),
        "chart_type": _normalize_chart_type(str(c.get("chart_type") or "bar")),
        "site": str(cfg.get("site") or "").strip(),
        "x": int(pos.get("x") or 0), "y": int(pos.get("y") or 0),
        "w": int(pos.get("w") or 0), "h": int(pos.get("h") or 0),
    }


def _rect(geom: dict) -> dict:
    return {k: geom.get(k) for k in ("title", "chart_type", "x", "y", "w", "h")}


def _append_band_layout(kept: list[dict], new_geoms: list[dict]) -> list[dict]:
    """在既有版面**下方**追加新图：不挪存量卡（人的编排照旧），只给新卡找位置。

    排不下即拒并给出路（删空图 / relayout=true 整板重排），不静默压矮、也不叠在旧卡上。
    返回新卡矩形清单（含 title/chart_type + x/y/w/h）。
    """
    occupied = [g for g in kept if int(g.get("h") or 0) > 0]
    top = max((int(g["y"]) + int(g["h"]) for g in occupied), default=_TOP_MARGIN - _ROW_GAP)
    top = max(top + (_ROW_GAP if occupied else 0), _TOP_MARGIN)
    bands = [new_geoms[i:i + _MAX_PER_ROW] for i in range(0, len(new_geoms), _MAX_PER_ROW)]
    heights = _alloc_band_heights(_CANVAS_H - top, [_band_floor(b, 0) for b in bands], False)
    if not heights:
        raise ScreenIntentError(_error(
            f"画布已排到 y={top}px，剩余高度放不下新增的 {len(new_geoms)} 张图"
            f"（每类图有前端最小高，压不下也不能叠在旧卡上）。请先用 remove_charts 删掉空图/废图，"
            "或带 relayout=true 让服务端整板重排（会重排存量卡位置）。"))
    out: list[dict] = []
    y = top
    for band, h in zip(bands, heights):
        x = 0
        for geom, cw in zip(band, _tile(_CANVAS_W, len(band))):
            out.append({**geom, "x": x, "y": y, "w": cw, "h": h})
            x += cw
        y += h + _ROW_GAP
    return out


def _relayout_positions(geoms: list[dict], emphasis: dict) -> list[dict]:
    """整板重排：对存量卡 + 新增卡一起按主次配方重算，返回与入参同序的矩形清单。

    会覆盖人手工排的坐标 —— 只在用户明确要求「重新排版」时由 `relayout=true` 触发。
    排不出主次（内容卡超上限/高度不够）由 `_auto_layout` 显式拒绝，不落半版。
    """
    widgets = [{"_i": i, "title": g.get("title"), "chart_type": g.get("chart_type"),
                "site": g.get("site") or ""} for i, g in enumerate(geoms)]
    laid = _auto_layout(widgets, emphasis)
    pos_by_i = {int(w["_i"]): pos for w, pos in laid}
    if len(pos_by_i) != len(geoms):
        raise ScreenIntentError(_error(
            f"整板重排只覆盖到 {len(pos_by_i)}/{len(geoms)} 张图，版面结果不完整；"
            "请核对是否有图表行缺少类型/坐标后重试（不静默保留未排定的卡）。"))
    return [{**g, **pos_by_i[i]} for i, g in enumerate(geoms)]


async def list_data_screens(args: dict) -> dict:
    """按业务名定位看板（改板/删板前的第一步）。

    清单范围 = 角色授权的可看集 ∪ 本人属主的看板（可写面）；admin 为全量。
    **这里不裁决权限**：能不能改/删仍由 `check_dashboard_editable` 在写链路上判（唯一裁决点），
    本工具只回 editable 供模型如实告知用户，绝不拿它当第二套判断。
    """
    args = args or {}
    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import (
        _groups_structure, visible_dashboard_ids)
    from backend.common.db import execute_query

    ctx = ExecutionContextVar.get()
    user_id = int((ctx.user_id if ctx else 0) or 0)
    if not user_id:
        return _error("当前执行上下文没有可信用户身份，无法列出看板清单（护栏 §2：身份只信服务端）")
    workspace_id = int((ctx.workspace_id if ctx else 0) or 0)
    keyword = str(args.get("keyword") or "").strip()
    try:
        limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        return _error(f"limit 需要整数，收到 {str(args.get('limit'))[:20]}")
    limit = max(1, min(limit, 100))

    visible = visible_dashboard_ids(user_id, str(getattr(ctx, "user_role", "") or ""), workspace_id)
    note = ""
    scope_ids: set[int] | None = None
    if visible is not None:
        own = {int(r["id"]) for r in (execute_query(
            "SELECT id FROM adh_dashboards WHERE workspace_id=%s AND owner_id=%s",
            (workspace_id, user_id)) or [])}
        scope_ids = set(visible) | own
        if not scope_ids:
            # 空授权是 fail-closed 的事实，不是"没有数据"：必须说清来路（建板会自动授权给本人角色）
            return _text({
                "count": 0, "dashboards": [],
                "note": ("当前账号的角色未获授任何看板，也没有本人创建的看板。"
                         "要改板先建板（create_data_screen 会把新板授权给本人角色）；"
                         "改别人的板需该板属主或管理员操作，或由管理员在角色权限里授予看板可见权。"),
            })

    where = ["d.workspace_id=%s"]
    params: list = [workspace_id]
    if scope_ids is not None:
        marks = ",".join(["%s"] * len(scope_ids))
        where.append(f"d.id IN ({marks})")
        params.extend(sorted(scope_ids))
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(d.name LIKE %s OR d.description LIKE %s)")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    params.append(limit)
    rows = execute_query(
        "SELECT d.id, d.name, d.description, d.`status`, d.updated_at, d.owner_id, "
        "(SELECT COUNT(*) FROM adh_charts c WHERE c.dashboard_id = d.id) AS chart_count "
        "FROM adh_dashboards d WHERE " + " AND ".join(where) +
        " ORDER BY d.updated_at DESC, d.id DESC LIMIT %s", params) or []

    structure = _groups_structure()
    group_names = {int(g["id"]): str(g.get("name") or "") for g in (structure.get("groups") or [])}
    boards_in: dict[int, list[str]] = {}
    for gid, dids in (structure.get("items") or {}).items():
        for did in dids:
            boards_in.setdefault(int(did), []).append(group_names.get(int(gid), ""))

    items = [{
        "dashboard_id": int(r["id"]),
        "name": r.get("name"),
        "description": r.get("description") or "",
        "status": r.get("status") or "designing",
        "chart_count": int(r.get("chart_count") or 0),
        "updated_at": str(r.get("updated_at") or ""),
        "editable": bool(visible is None or int(r.get("owner_id") or 0) == user_id),
        "groups": [n for n in (boards_in.get(int(r["id"])) or []) if n],
        "url": f"/screen/{int(r['id'])}",
    } for r in rows]
    if not items and keyword:
        note = f"没有名称/说明匹配「{keyword}」的看板；去掉关键词看完整清单，或先与用户确认看板名称"
    elif not items:
        note = "当前范围内没有看板"
    else:
        note = ("改名/加图/删图/重排用 update_data_screen（按 name 即可，不必先拿 id）；"
                "editable=false 的板只能看不能改（写链路按属主/管理员裁决）")
    return _text({"count": len(items), "dashboards": items, "note": note})


async def update_data_screen(args: dict) -> dict:
    """原地修改一块看板：板级元信息/主题/字模 + 加图 + 删图 + 整板重排。

    落库顺序刻意安排成「先算后写、能回滚的先回滚」：意图解析、定源、落数据集、预取数、
    版面计算全部在写之前（预检不过一笔不写）；新增图中途失败只清掉本次新增，
    不动存量卡；删除是不可逆步，放在新增成功之后。
    """
    args = args or {}
    try:
        dashboard_id = int(args.get("dashboard_id") or 0)
        raw_theme = _coerce_struct(args.get("theme"), "theme", (dict, str))
        theme = _coerce_theme(raw_theme) if raw_theme is not None else None
        style = _coerce_struct(args.get("style"), "style", dict)
        add_charts = _coerce_struct(args.get("add_charts"), "add_charts", (list, dict))
        remove_charts = _coerce_struct(args.get("remove_charts"), "remove_charts", (list, str))
        tags_arg = _coerce_struct(args.get("tags"), "tags", (list, str))
        emphasis = _coerce_struct(args.get("emphasis"), "emphasis", dict) or {}
    except ScreenIntentError as e:
        return e.response
    if isinstance(add_charts, dict):
        add_charts = [add_charts]
    if isinstance(remove_charts, str):
        remove_charts = [remove_charts]
    if isinstance(tags_arg, str):
        tags_arg = re.split(r"[,，、]", tags_arg) if tags_arg.strip() else None
    add_charts = add_charts or []
    remove_charts = [str(x).strip() for x in (remove_charts or []) if str(x).strip()]
    relayout = bool(args.get("relayout"))
    if not dashboard_id:
        return _error("请提供看板业务名 name（歧义会回候选清单）或 dashboard_id 指明改哪块板；"
                      "不知道是哪块先用 list_data_screens 查清单")

    meta: dict = {}
    if args.get("new_name") is not None:
        nn = str(args["new_name"]).strip()
        if not nn:
            return _error("new_name 不能是空串；清空名称不是有效改法")
        meta["name"] = nn
    if args.get("description") is not None:
        meta["description"] = str(args["description"])
    if args.get("status") is not None:
        st = str(args["status"]).strip()
        if st not in _DASHBOARD_STATUSES:
            return _error(f"status 只接受 {'/'.join(_DASHBOARD_STATUSES)}，收到 {st[:40]}")
        meta["status"] = st
    if tags_arg is not None:
        meta["tags"] = [str(t).strip() for t in tags_arg if str(t).strip()]
    if not (meta or theme or style or add_charts or remove_charts or relayout):
        return _error("没有提供任何要修改的内容：new_name/description/status/tags/theme/style/"
                      "add_charts/remove_charts/relayout 至少给一项")

    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import (
        ChartService, DashboardService, _invalidate_dashboard_cache, _invalidate_dashboard_payload)

    ctx = ExecutionContextVar.get()
    try:
        # 只有新增图需要落数据集 → 才多要一项 dataset:manage；改名/删图/重排不碰数据集
        require_screen_perms(ctx, need_dataset=bool(add_charts))
    except PermissionError as e:
        return _error(str(e))
    identity = identity_of(ctx)
    user_id = identity["user_id"]

    dashboard = DashboardService().get_dashboard(dashboard_id, user_id, enforce_visibility=False)
    if not dashboard:
        return _error("该看板不存在（可能已被删除），请用 list_data_screens 核对清单")
    charts = dashboard.get("charts") or []

    # ── filters 读-改-写：主题存 filters.theme、字模存 filters.components，
    #    而人侧页面编排存 filters.design。整份覆盖会抹掉人的编排（历史缺陷口径）。
    #    theme/components 内部同样逐键合并：只改 mode 不该丢 background，只改阴影不该丢圆角。
    filters = _json_dict(dashboard.get("filters"))
    merged = dict(filters)
    if theme:
        base_theme = dict(merged.get("theme") or {}) if isinstance(merged.get("theme"), dict) else {}
        base_theme.update({k: v for k, v in theme.items() if k in _THEME_KEYS and v not in (None, "")})
        merged["theme"] = base_theme
    if style:
        comps = dict(merged.get("components") or {})
        for key, val in style.items():
            if val in (None, ""):
                continue
            if isinstance(val, dict) and isinstance(comps.get(key), dict):
                comps[key] = {**comps[key], **val}
            else:
                comps[key] = val
        merged["components"] = comps
    theme_mode = merged.get("theme", {}).get("mode") if isinstance(merged.get("theme"), dict) else None
    theme_mode = theme_mode if theme_mode in ("light", "dark") else None
    if merged != filters:
        meta["filters"] = merged

    # ── 删图定位：只认图表业务标题（精确命中唯一），歧义/未命中一律回清单不猜
    removed_ids: list[int] = []
    removed_titles: list[str] = []
    removed_names: dict[int, str] = {}
    if remove_charts:
        by_title: dict[str, list[dict]] = {}
        for c in charts:
            by_title.setdefault(str(c.get("name") or "").strip(), []).append(c)
        missing = [t for t in remove_charts if t not in by_title]
        if missing:
            return _error(
                f"remove_charts 未匹配到图表：{'、'.join(missing)}；"
                f"本板现有标题：{'、'.join(t for t in by_title if t) or '（无图）'}。"
                "请原样使用图表标题（明细见 get_data_screen）")
        dup = [t for t in remove_charts if len(by_title[t]) > 1]
        if dup:
            return _error(
                f"图表标题「{'、'.join(dup)}」在本板出现多次，无法确定删哪一张；"
                "请先用 update_data_screen_chart 把标题改成唯一值（或向用户确认要保留哪一张）")
        removed_titles = list(remove_charts)
        removed_ids = [int(c["id"]) for t in remove_charts for c in by_title[t]]
        removed_names = {int(c["id"]): str(c.get("name") or "") for t in remove_charts
                         for c in by_title[t]}

    # ── 加图：全部预检（意图/绑定/数据集/取数）在任何写之前完成
    board_name = str(dashboard.get("name") or "")
    bad_widget = next((i for i, w in enumerate(add_charts) if not isinstance(w, dict)), None)
    if bad_widget is not None:
        return _error(f"add_charts[{bad_widget}] 必须是对象 {{title, chart_type, query, ...}}")
    prepared_new: list[dict] = []
    for i, w in enumerate(add_charts):
        try:
            prepared_new.append(await _prepare_widget(w, i, args, ctx, identity, board_name, theme_mode))
        except ScreenIntentError as e:
            return e.response
        except Exception as e:  # noqa: BLE001 — 原始细节只进日志, 对外回分类文案
            return _safe_exec_error(e, f"add chart {i + 1}")

    # ── 版面：重排 = 整板按主次配方重算；否则存量不动、新卡追加到底部带
    kept_geoms = [_chart_geom(c) for c in charts if int(c["id"]) not in set(removed_ids)]
    new_geoms = [{"chart_id": None, "title": it["title"], "chart_type": it["chart_type"],
                  "site": str(_json_dict(it["config"]).get("site") or "")} for it in prepared_new]
    layout_updates: list[dict] = []
    try:
        if relayout:
            laid = _relayout_positions(kept_geoms + new_geoms, emphasis)
            kept_geoms, new_geoms = laid[:len(kept_geoms)], laid[len(kept_geoms):]
            layout_updates = [{"chart_id": g["chart_id"],
                               "position": {k: g[k] for k in ("x", "y", "w", "h")}}
                              for g in kept_geoms]
        elif new_geoms:
            new_geoms = _append_band_layout(kept_geoms, new_geoms)
        final_rects = [_rect(g) for g in kept_geoms] + [_rect(g) for g in new_geoms]
    except ScreenIntentError as e:
        return e.response

    # ── 写：新增 → 重排 → 删除（删除不可逆，排在新增成功之后）
    chart_service = ChartService()
    mounted: list[int] = []
    apply_errors: list[str] = []
    try:
        for item, geom in zip(prepared_new, new_geoms):
            mounted.append(_mount_chart(chart_service, dashboard_id, item,
                                        {k: geom[k] for k in ("x", "y", "w", "h")}, identity))
        if layout_updates:
            chart_service.update_layout(dashboard_id, layout_updates)
        for cid in removed_ids:
            if not chart_service.delete_chart(dashboard_id, cid):
                apply_errors.append(f"图表「{removed_names.get(cid) or cid}」删除未生效（可能已被其他人删除）")
    except Exception as e:  # noqa: BLE001 — 半成品图回滚干净再报错，不留半张板
        for cid in mounted:
            try:
                chart_service.delete_chart(dashboard_id, cid)
            except Exception:  # noqa: BLE001 — 清理失败不得盖住原始错因
                logger.exception("[screen] 回滚新增图表失败 dashboard=%s chart=%s", dashboard_id, cid)
        return _safe_exec_error(e, "update data screen")
    if meta:
        try:
            DashboardService().update_dashboard(dashboard_id, meta, user_id)
        except Exception as e:  # noqa: BLE001 — 图表已改到位，元信息失败如实上报不掩
            apply_errors.append(f"看板元信息未更新：{type(e).__name__}")
    _invalidate_dashboard_payload(dashboard_id)
    _invalidate_dashboard_cache(user_id)

    data_audit = [{
        "title": item["title"], "chart_type": item["chart_type"],
        "row_count": (None if item["chart_type"] in _NO_SQL_TYPES else
                      int(item["result"].get("row_count") if item["result"].get("row_count") is not None
                          else len(item["result"].get("rows") or []))),
    } for item in prepared_new]
    empty_charts = [a["title"] for a in data_audit if a["row_count"] == 0]
    layout_audit = _layout_audit(final_rects)
    final_name = meta.get("name") or board_name
    changed = [k for k in ("name", "description", "status", "tags", "filters") if k in meta]
    if theme:
        changed.append("theme")
    if style:
        changed.append("style")
    if add_charts:
        changed.append("add_charts")
    if remove_charts:
        changed.append("remove_charts")
    if relayout:
        changed.append("relayout")

    message = f"看板「{final_name}」已原地更新（未新建资产）；访问路径: /screen/{dashboard_id}"
    if add_charts:
        message += f"，新增 {len(add_charts)} 张图"
    if remove_charts:
        message += f"，删除 {len(remove_charts)} 张图"
    if relayout:
        message += "，整板按主次配方重排（存量卡位置已重写）"
    if not kept_geoms and not prepared_new:
        message += ("；注意：该看板已无图表 —— 整板不要了请用 delete_data_screen（需用户确认）")
    if empty_charts:
        message += (f"；其中 {len(empty_charts)} 个新图取数为空: {'、'.join(empty_charts)} —— "
                    "空图不是成功, 请核对时间窗/过滤条件/口径覆盖并向用户逐图说明(不得换近似指标凑数)")
    if apply_errors:
        message += f"；部分改动未生效: {'；'.join(apply_errors)}（请如实告知用户，不要当作全部完成）"
    if not layout_audit["ok"]:
        message += (f"；版面自检未通过(重叠 {len(layout_audit['overlaps'])} 处、"
                    f"越界 {len(layout_audit['out_of_canvas'])} 张、"
                    f"带高不足 {len(layout_audit['squeezed'])} 张) —— 请按 layout-guide 重排后再交付")
    elif layout_audit["hierarchy"] is False:
        message += ("；版面所有卡片等宽等高（没有主次）—— 减少图数或用 emphasis 指定主图后带 relayout=true 重排，"
                    "不要把“已更新 N 个图表”当版面合格")
    return _text({
        "dashboard_id": dashboard_id,
        "name": final_name,
        "url": f"/screen/{dashboard_id}",
        "changed": changed,
        "added_charts": [item["title"] for item in prepared_new],
        "removed_charts": removed_titles,
        "relayout": relayout,
        "chart_count": len(kept_geoms) + len(prepared_new),
        "data_audit": data_audit,
        "empty_charts": empty_charts,
        "layout": [_rect(g) for g in final_rects],
        "layout_audit": layout_audit,
        "apply_errors": apply_errors or None,
        "actions": [{"type": "open_screen", "label": "预览大屏",
                     "dashboard_id": dashboard_id, "url": f"/screen/{dashboard_id}"}],
        "message": message,
    })


async def delete_data_screen(args: dict) -> dict:
    """删除一块看板（不可逆）：先要用户确认，再按属主/管理员裁决。

    权限口径与人侧 `DELETE /api/dashboard/{id}` 完全一致（`check_dashboard_editable`）：
    角色授予的可见权**不含删除**；本层不另造判断。
    """
    args = args or {}
    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import DashboardService

    dashboard_id = int(args.get("dashboard_id") or 0)
    if not dashboard_id:
        return _error("请提供看板业务名 name 或 dashboard_id 指明删哪块板；"
                      "不知道是哪块先用 list_data_screens 查清单")

    ctx = ExecutionContextVar.get()
    if not args.get("confirmed"):
        # 破坏性动作：先回确认提示，由模型走 ask_user 拿到同意（沿用 publish_ontology_model 的机制）
        board = DashboardService().get_dashboard(dashboard_id,
                                                int((ctx.user_id if ctx else 0) or 0),
                                                enforce_visibility=False) or {}
        return _text({
            "needs_user_confirmation": True,
            "dashboard_id": dashboard_id,
            "name": board.get("name"),
            "chart_count": len(board.get("charts") or []),
            "note": (f"删除看板「{board.get('name') or dashboard_id}」不可逆：整板与其全部图表、"
                     "角色可见授权、看板组合成员一并清除。请先用 ask_user 向用户确认，"
                     "拿到同意后再带 confirmed=true 调用本工具"),
        })
    try:
        require_screen_perms(ctx, need_dataset=False)
    except PermissionError as e:
        return _error(str(e))
    identity = identity_of(ctx)
    # 内容级把关（仅属主或管理员）已由 resource_guard 在同一裁决点判过，此处不重复放水
    board = DashboardService().get_dashboard(dashboard_id, identity["user_id"],
                                             enforce_visibility=False) or {}
    board_name = str(board.get("name") or "").strip()
    try:
        deleted = DashboardService().delete_dashboard(dashboard_id, identity["user_id"])
    except Exception as e:  # noqa: BLE001 — 原始细节仅进日志, 回分类文案
        return _safe_exec_error(e, "delete dashboard")
    if not deleted:
        return _error("该看板不存在或已被删除，未做任何改动")
    return _text({
        "deleted": True,
        "name": board_name or "（名称已随看板清除）",
        "message": f"看板「{board_name or '未命名'}」已删除（含其图表、角色可见授权与看板组合成员）",
    })


# ── 看板组合（仪表盘组）：编排维度，不是权限维度 ──────────────────────

_GROUP_ACTIONS = ("create", "rename", "delete", "set_charts", "set_roles", "reorder")


def _group_note() -> str:
    return ("看板组合=仪表盘组（adh_dashboard_groups），是**编排**维度：把已有看板分组、排展示/轮播顺序、"
            "按角色分配给谁；它**不授予可见权** —— 一块板能不能看，只由角色的看板授权裁决。"
            "「组合」不是把几张图合成一张图（那是在同一块看板里排布，用 create_data_screen/update_data_screen）。")


def _resolve_group_id(ref: str) -> int:
    """按组合业务名定位（模型不猜 id）：精确名唯一命中，否则回候选清单。"""
    from backend.common.db import execute_query
    name = str(ref or "").strip()
    if not name:
        raise ScreenIntentError(_error("请提供组合名称 group（不知道有哪些组合先用 list_dashboard_groups 查清单）"))
    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = execute_query(
        "SELECT id, name FROM adh_dashboard_groups WHERE name=%s OR name LIKE %s ORDER BY sort, id LIMIT 11",
        (name, f"%{escaped}%")) or []
    hits = [r for r in rows if str(r.get("name") or "") == name] or rows
    if not hits:
        raise ScreenIntentError(_error(f"没有名为「{name}」的看板组合；用 list_dashboard_groups 查现有组合"))
    if len(hits) > 1:
        shown = "、".join(f"「{r.get('name')}」" for r in hits[:10])
        raise ScreenIntentError(_error(
            f"组合名称「{name}」命中多个：{shown}；请与用户确认是哪一个（或用更完整的名称）"))
    return int(hits[0]["id"])


def _resolve_dashboard_ids_by_name(names: list) -> list:
    """组内成员按**看板业务名**解析成 id（ui-resource-display：不让模型猜内部 id）。

    任一名字解析不到即整单拒绝并列候选，绝不"跳过没匹配到的板"静默少登记。
    """
    from backend.common.db import execute_query
    wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not wanted:
        return []
    marks = ",".join(["%s"] * len(wanted))
    rows = execute_query(f"SELECT id, name FROM adh_dashboards WHERE name IN ({marks})", wanted) or []
    by_name: dict[str, list[int]] = {}
    for r in rows:
        by_name.setdefault(str(r.get("name") or ""), []).append(int(r["id"]))
    ids: list[int] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    for n in wanted:
        hits = by_name.get(n) or []
        if not hits:
            missing.append(n)
        elif len(hits) > 1:
            ambiguous.append(n)
        else:
            ids.append(hits[0])
    if missing or ambiguous:
        parts = []
        if missing:
            near = [str(r["name"]) for r in (execute_query(
                "SELECT name FROM adh_dashboards ORDER BY updated_at DESC LIMIT 20") or [])]
            parts.append(f"没有名为 {'、'.join(missing)} 的看板（最近看板如：{'、'.join(near) or '（无）'}）")
        if ambiguous:
            parts.append(f"{'、'.join(ambiguous)} 同名多块，无法确定是哪一块")
        raise ScreenIntentError(_error("；".join(parts) + "。请向用户确认看板名称后重试"))
    return ids


def _resolve_role_ids_by_name(names: list) -> list:
    """角色按业务名解析成 id（组合的角色分配对象是角色，不是用户）。"""
    from backend.common.db import execute_query
    wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not wanted:
        return []
    marks = ",".join(["%s"] * len(wanted))
    rows = execute_query(f"SELECT id, name FROM adh_roles WHERE name IN ({marks})", wanted) or []
    by_name = {str(r.get("name") or ""): int(r["id"]) for r in rows}
    missing = [n for n in wanted if n not in by_name]
    if missing:
        known = [str(r["name"]) for r in (execute_query(
            "SELECT name FROM adh_roles ORDER BY name") or [])]
        raise ScreenIntentError(_error(
            f"没有名为 {'、'.join(missing)} 的角色；系统现有角色：{'、'.join(known) or '（无）'}"))
    return [by_name[n] for n in wanted]


async def list_dashboard_groups(args: dict) -> dict:
    """当前身份可见的看板组合清单（含组内看板业务名与编排顺序）。"""
    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import build_visible_groups

    ctx = ExecutionContextVar.get()
    user_id = int((ctx.user_id if ctx else 0) or 0)
    if not user_id:
        return _error("当前执行上下文没有可信用户身份，无法列出看板组合（护栏 §2：身份只信服务端）")
    groups = build_visible_groups(user_id, str(getattr(ctx, "user_role", "") or "")) or []
    items = [{
        "group_id": int(g.get("id") or 0),
        "name": g.get("name"),
        "description": g.get("description") or "",
        "is_ungrouped": int(g.get("id") or 0) == 0,
        "dashboards": [str(d.get("name") or "") for d in (g.get("dashboards") or [])],
    } for g in groups]
    note = _group_note()
    if not [i for i in items if not i["is_ungrouped"]]:
        note += "；当前身份没有已分配的组合（「未分组」里的板尚未编组）"
    return _text({"count": len(items), "groups": items, "note": note})


async def manage_dashboard_groups(args: dict) -> dict:
    """改看板组合（仅管理员）：建组/改名/删组/组内看板/角色分配/整体顺序。

    与人侧 `require_admin` 同口径：组合是跨看板的编排配置，角色授予的看板可见权不含改组合。
    成员与角色一律按**业务名**解析，解析不到回候选清单（不猜 id、不静默少登记）。
    """
    args = args or {}
    try:
        action = str(args.get("action") or "").strip().lower()
        dashboards = _coerce_struct(args.get("dashboards"), "dashboards", (list, str))
        roles = _coerce_struct(args.get("roles"), "roles", (list, str))
        order = _coerce_struct(args.get("order"), "order", (list, str))
    except ScreenIntentError as e:
        return e.response
    dashboards = [dashboards] if isinstance(dashboards, str) else (dashboards or [])
    roles = [roles] if isinstance(roles, str) else (roles or [])
    order = [order] if isinstance(order, str) else (order or [])
    if action not in _GROUP_ACTIONS:
        return _error(f"action 只接受 {'/'.join(_GROUP_ACTIONS)}，收到 {str(args.get('action'))[:40]}")

    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import (
        DashboardGroupNotFound, create_group, delete_group, reorder_groups, set_group_items,
        set_group_roles, update_group, _is_admin_user)

    ctx = ExecutionContextVar.get()
    try:
        require_screen_perms(ctx, need_dataset=False)
    except PermissionError as e:
        return _error(str(e))
    identity = identity_of(ctx)
    if not _is_admin_user(identity["user_id"], identity["role"]):
        return _error("看板组合的编排（分组/顺序/角色分配）是全局配置，仅管理员可改："
                      "请如实告知用户，并由管理员在「看板组合」管理页操作（角色授予的看板可见权不含改组合）")

    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "")
    try:
        if action == "create":
            if not name:
                return _error("create 需要组合名称 name")
            group_id = create_group(name, description)
            outcome = {"action": action, "group_id": group_id, "name": name}
        elif action == "reorder":
            # 整体顺序只吃 order 清单，不针对单个组合 → 不走下面的目标组合解析
            if not order:
                return _error("reorder 需要完整顺序清单 order（全部组合名，按展示先后排列）")
            orders = [{"id": _resolve_group_id(g), "sort": (i + 1) * 10}
                      for i, g in enumerate(order)]
            reorder_groups(orders)
            outcome = {"action": action, "count": len(orders), "order": [str(g) for g in order]}
        else:
            group_id = _resolve_group_id(args.get("group") or name)
            if action == "rename":
                if not name:
                    return _error("rename 需要新名称 name")
                update_group(group_id, name, description or None)
                outcome = {"action": action, "name": name}
            elif action == "delete":
                if not args.get("confirmed"):
                    return _text({
                        "needs_user_confirmation": True, "group_id": group_id,
                        "note": (f"删除看板组合「{args.get('group') or name}」会连同其成员编排与角色分配一并清除"
                                 "（看板本身不删）。请先用 ask_user 向用户确认，再带 confirmed=true 调用本工具"),
                    })
                delete_group(group_id)
                outcome = {"action": action, "deleted": True}
            elif action == "set_charts":
                if args.get("dashboards") is None:
                    return _error("set_charts 需要 dashboards（看板业务名清单，顺序即展示/轮播顺序）；"
                                  "要清空组内成员请显式传 dashboards=[]")
                ids = _resolve_dashboard_ids_by_name(dashboards)
                result = set_group_items(group_id, ids)
                outcome = {"action": action, "count": result["count"],
                           "dashboards": [str(d) for d in dashboards], "dropped": result["dropped"]}
            elif action == "set_roles":
                if args.get("roles") is None:
                    return _error("set_roles 需要 roles（角色业务名清单）；"
                                  "要取消全部角色分配请显式传 roles=[]。注意：组合不授予可见权")
                rids = _resolve_role_ids_by_name(roles)
                count = set_group_roles(group_id, rids)
                outcome = {"action": action, "count": count, "roles": [str(r) for r in roles]}
    except ScreenIntentError as e:
        return e.response
    except DashboardGroupNotFound as e:
        return _error(f"{e}；用 list_dashboard_groups 查现有组合")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:  # noqa: BLE001 — 原始细节只进日志, 对外回分类文案
        return _safe_exec_error(e, "manage dashboard groups")
    outcome["note"] = _group_note()
    outcome["message"] = "看板组合已更新（顺序即展示/轮播顺序；组合不授予可见权）"
    return _text(outcome)


# ── 字模库取用工具（元数据级，不返回数据行）──

# 出站投影：字模配置里的凭据/内部标识键一律不下发（与 outbound_guard 同源名单）
_SECRET_KEYS = frozenset(k.lower() for k in SENSITIVE_OUTPUT_KEYS)


def _public_vis_value(value):
    """递归剔除凭据/内部标识键。字典按键名（大小写不敏感、完整键名）比，值不动。"""
    if isinstance(value, dict):
        return {k: _public_vis_value(v) for k, v in value.items()
                if str(k).strip().lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_public_vis_value(v) for v in value]
    return value


def _public_vis_component(comp: dict) -> dict:
    """字模的**下发视图**：剥掉凭据键，只留视觉配置。

    地图瓦片字模的 style_config 带 `apiKey` 配置位（值为空也算凭据信号：键名本身
    不该出现在 LLM 视野）。原样下发会被 outbound_guard 判成 sensitive_field 把
    **整条**工具结果拒发 —— 全量 list_vis_components 必中 map_tile，带 category
    过滤却通过，AI 侧表现为“字模列表时好时坏”。守卫黑名单不为此放水
    （security-guardrails §7：凭据不得进模型视野），在投影处剥离（与
    catalog_tools._strip_physical_ids 同范式）。
    """
    out = dict(comp)
    if "style_config" in out:
        out["style_config"] = _public_vis_value(out.get("style_config"))
    return out


async def list_vis_components(args: dict) -> dict:
    """列出可视化字模(按类别过滤)。返回名称/类别/编码/说明, 不含数据行/裸 SQL。"""
    args = args or {}
    from backend.modules.viz.services.vis_library_service import vis_library_service

    category = (args.get("category") or "").strip()
    comps = vis_library_service.list_components(category)
    summaries = [{
        "id": c.get("id"), "code": c.get("code"), "name": c.get("name"),
        "category": c.get("category"), "chart_type": c.get("chart_type"),
        "description": c.get("description"), "style_config": _public_vis_value(c.get("style_config")),
    } for c in comps]
    return _text({"count": len(summaries), "components": summaries})


async def get_vis_component(args: dict) -> dict:
    """获取单个字模详情（纯视觉配置级）。"""
    args = args or {}
    ref = args.get("component_id") or args.get("code")
    if not ref:
        return _error("component_id 或 code 必填")
    from backend.modules.viz.services.vis_library_service import (
        is_usable, vis_library_service)

    comp = None
    try:
        comp = vis_library_service.get_component(int(ref))
    except (TypeError, ValueError):
        comp = vis_library_service.get_component_by_code(str(ref))
    if not comp:
        return _error("字模不存在")
    if not is_usable(comp):
        # 显式告知不可用（而不是回一份看起来能用的配置）：禁用=停用但保留，不可被选用
        return _error(f"字模「{comp.get('name') or comp.get('code')}」已"
                      f"{'禁用' if int(comp.get('is_active') or 0) != 0 else '下架'}，不可使用")
    return _text(_public_vis_component(comp))


async def save_vis_component(args: dict) -> dict:
    """把大屏里设计好的一段视觉(图表样式/背景/卡片/配色)回存为自定义字模(活字沉淀)。"""
    args = args or {}
    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.vis_library_service import vis_library_service

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0
    is_admin = bool(ctx and getattr(ctx, "user_role", "") == "admin")
    payload = dict(args)
    try:
        # style_config 入库存 json.dumps：字符串原样传会被二次编码，存成一段“配置字符串”
        payload["style_config"] = _coerce_struct(args.get("style_config"), "style_config", dict) or {}
    except ScreenIntentError as e:
        return e.response
    for key in ("name", "category", "chart_type", "description", "code"):
        if payload.get(key) is not None:
            payload[key] = str(payload[key])
    try:
        cid = vis_library_service.create_component(payload, user_id, is_admin)
    except ValueError as e:
        return _error(str(e))
    return _text({"component_id": cid, "message": "字模已保存到 UI 库(自定义)"})


# ── MCP server 构建 ────────────────────────────────────────────────────

# ── 工具 schema 片段 ────────────────────────────────────────────────
#
# 注意（2026-11-17 缺陷根因）：这里必须是**完整 JSON Schema**（type/properties/items/required）。
# 旧写法是“参数名 → 描述字符串”，`compat.to_json_schema()` 只能把它们全声明成 string，
# 模型于是把 widgets/query/config/theme/style 塞成 JSON 串：`len(widgets)` 数的是字符数
# （1 张图也报「组件数量不能超过 12」），传 `"[]"` 又能过长度校验并在 enumerate 时炸成
# 「widgets[0] 必须是对象」。参数类型必须与 handler 真实消费的形状一致。

_INTENT_PROPERTIES = {
    "object": {"type": "string",
               "description": "业务对象 key（已建模；可用 search_metadata/knowledge_search 查）"},
    "metrics": {"type": "array", "items": {"type": "string"}, "description": "指标业务名列表"},
    "dimensions": {"type": "array", "items": {"type": "string"}, "description": "维度业务名列表"},
    "filters": {"type": "array", "items": {"type": "object"}, "description": "[{field, op, value}]"},
    "order": {"type": "array", "items": {"type": "object"}, "description": "[{field, direction}]"},
    "limit": {"type": "integer"},
    "time_window": {"type": "string", "description": "相对时间窗 7d/24h/2w/1M（不得手算绝对日期）"},
    "time_column": {"type": "string"},
    "time_grain": {"type": "string"},
}

_POSITION_SCHEMA = {
    "type": "object",
    "description": "画布位置 {x,y,w,h}（1920x1080 基准；不传则自动排布）",
    "properties": {k: {"type": "integer"} for k in ("x", "y", "w", "h")},
}

TOOL_SPECS = [
    {
        "name": "create_data_screen",
        "description": (
            "Create a visualization data screen (BI dashboard) directly from declarative chart intents — "
            "NO design panel, NO SQL authoring/confirmation. "
            "Each widget MUST provide a `query` intent "
            "({object, metrics[], dimensions[], filters[], order[], limit, time_window, time_column, time_grain}). "
            "The tool materializes one semantic **dataset** per chart (adh_datasets) and mounts each chart "
            "as a dataset chart (source_type='dataset'); the dataset row holds the metrics scope, "
            "so the screen never stores SQL. Raw SQL fields (sql/raw_sql/statement) are rejected. "
            "Per-source screens: pass `site` (datasource business name) — never numeric ids. "
            "Optional per-widget `component_ids`/`style_ref` and top-level `style` reference vis-library "
            "blocks (background / card / chart style / layout / color theme) — list them via list_vis_components. "
            "Interactive filter params are NOT supported on this channel (pass them and the call is rejected). "
            "Returns `url`, `datasets`, `data_audit` (per-chart row_count) and `empty_charts`: a created chart "
            "with 0 rows is NOT a success — tell the user which charts are empty and why instead of reporting 'created'."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Screen title (required)"},
                "widgets": {
                    "type": "array", "minItems": 1, "maxItems": _MAX_WIDGETS,
                    "description": f"Chart widgets (1~{_MAX_WIDGETS}); one dataset per widget. Pass an ARRAY of objects.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "chart_type": {"type": "string",
                                           "description": "bar|line|area|pie|big_number_trend|table_value|"
                                                          "timeseries_line|timeseries_bar|…（完整清单见工具说明）"},
                            "query": {"type": "object", "properties": _INTENT_PROPERTIES,
                                      "description": "声明式语义意图（必填，iframe 组件除外）；裸 SQL 会被拒"},
                            "site": {"type": "string", "description": "本图数据源业务名（不传则用顶层 site）"},
                            "datasource": {"type": "string", "description": "同 site（site 优先）"},
                            "component_ids": {"type": "array",
                                              "items": {"type": ["integer", "string"],
                                                        "description": "字模 id 或 code"},
                                              "description": "要套用的字模引用列表"},
                            "style_ref": {"type": ["integer", "string"],
                                          "description": "单个字模 id 或 code"},
                            "config": {"type": "object", "description": "视觉配置（颜色/标签/坐标轴等）"},
                            "position": _POSITION_SCHEMA,
                        },
                        "required": ["title", "chart_type"],
                    },
                },
                "description": {"type": "string", "description": "Optional screen description"},
                "site": {"type": "string",
                         "description": "默认数据源业务名，应用于未单独指定 site 的 widget"},
                "datasource": {"type": "string", "description": "同 site（site 优先）"},
                "theme": {
                    "type": ["object", "string"],
                    "description": ("Optional {mode, background, backgroundImage, header, card}；"
                                    "也接受 'light'/'dark' 字符串简写（= {mode}）"),
                    "properties": {
                        "mode": {"type": "string", "enum": ["light", "dark"]},
                        "background": {"type": "string"},
                        "backgroundImage": {"type": "string"},
                        "header": {"type": "object"},
                        "card": {"type": "object"},
                    },
                },
                "style": {
                    "type": "object",
                    "description": "Optional {background?, colorTheme?, cardStyle?, layout?, decoration?} 字模引用",
                    "properties": {k: {"type": ["integer", "string"], "description": "字模 id 或 code"}
                                   for k in
                                   ("background", "colorTheme", "cardStyle", "layout", "decoration")},
                },
            },
            "required": ["name", "widgets"],
        },
        "handler": create_data_screen,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "get_data_screen",
        "description": "Get a data screen's detail with each chart's declarative intent + visual config (no raw SQL / datasource / rows).",
        "schema": {
            "type": "object",
            "properties": {"dashboard_id": {"type": "integer", "description": "Screen id (required)"}},
            "required": ["dashboard_id"],
        },
        "handler": get_data_screen,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "update_data_screen_chart",
        "description": (
            "Update one chart in a data screen (title/chart_type/config/position). "
            "To change data, pass a new declarative `query` intent: the tool re-materializes (or reuses) "
            "a dataset and remounts the chart on it — raw SQL is rejected and no design panel is opened. "
            "Returns `dataset_name` and `row_count` of the re-executed intent (0 = empty chart, must be surfaced)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "dashboard_id": {"type": "integer", "description": "Screen id (required)"},
                "chart_id": {"type": "integer", "description": "Chart id (required)"},
                "title": {"type": "string", "description": "New title (optional)"},
                "chart_type": {"type": "string", "description": "New chart type (optional)"},
                "query": {"type": "object", "properties": _INTENT_PROPERTIES,
                          "description": "New declarative intent (optional)；裸 SQL 会被拒"},
                "site": {"type": "string", "description": "New intent 的数据源业务名（不传数字 id）"},
                "datasource": {"type": "string", "description": "同 site（site 优先）"},
                "config": {"type": "object", "description": "Visual config merge (optional)"},
                "position": _POSITION_SCHEMA,
            },
            "required": ["dashboard_id", "chart_id"],
        },
        "handler": update_data_screen_chart,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "list_data_screens",
        "description": (
            "Locate existing dashboards/screens by business name BEFORE modifying one — this is the entry point "
            "for 'modify / rename / add-chart / remove-chart / relayout / delete my board' requests. "
            "Returns name, description, status, chart_count, updated_at, the groups each board belongs to, and "
            "`editable` (owner or admin only). NEVER call create_data_screen because a dashboard_id was not found: "
            "list first and match by name. Empty result for a non-admin means the role was granted no boards "
            "(the response says so) — report that honestly instead of creating a duplicate board."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Optional filter on board name/description (business name, not id)"},
                "limit": {"type": "integer", "description": "Max rows (1~100, default 50)"},
            },
        },
        "handler": list_data_screens,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "update_data_screen",
        "description": (
            "Modify an EXISTING dashboard in place — the ONLY correct path for 'change that board' requests "
            "(never create a new board for an edit). Address the board by `name` (business name) or `dashboard_id`. "
            "Supports board-level metadata (new_name/description/status/tags), theme ('light'/'dark' or object) and "
            "vis-library style refs — theme/style are merged into filters, the human page layout (filters.design) is "
            "preserved, never overwritten. `add_charts` mounts new declarative-intent charts (same direct-build "
            "channel as create_data_screen: raw SQL rejected, one dataset per chart); `remove_charts` deletes charts "
            "by their business titles; `relayout=true` recomputes the whole board with the server-side "
            "hero/side/band hierarchy (it rewrites positions, so only use it when the user asks to re-lay out; "
            "use `emphasis` {hero, detail} to pick the hero chart). Without relayout new charts are appended in a "
            "bottom band and the call is refused with a hint if the canvas is full. Returns layout_audit, "
            "data_audit, empty_charts and apply_errors — a partially applied change must be reported as such."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "看板业务名（与 dashboard_id 二选一；歧义会回候选清单，不猜）"},
                "dashboard_id": {"type": "integer", "description": "看板 id（已知的可直接传；不知道就用 name）"},
                "new_name": {"type": "string", "description": "改名"},
                "description": {"type": "string", "description": "改说明"},
                "status": {"type": "string", "enum": list(_DASHBOARD_STATUSES),
                           "description": "designing|enabled|closed"},
                "tags": {"type": ["array", "string"], "items": {"type": "string"},
                         "description": "标签清单（数组或逗号分隔串；显式覆盖，[] 即清空）"},
                "theme": {
                    "type": ["object", "string"],
                    "description": "看板主题（合并写 filters.theme）；也接受 'light'/'dark' 简写",
                    "properties": {
                        "mode": {"type": "string", "enum": ["light", "dark"]},
                        "background": {"type": "string"},
                        "backgroundImage": {"type": "string"},
                        "header": {"type": "object"},
                        "card": {"type": "object"},
                    },
                },
                "style": {
                    "type": "object",
                    "description": "字模引用（合并写 filters.components）",
                    "properties": {k: {"type": ["integer", "string"], "description": "字模 id 或 code"}
                                   for k in ("background", "colorTheme", "cardStyle", "layout", "decoration")},
                },
                "add_charts": {
                    "type": "array", "maxItems": _MAX_WIDGETS,
                    "description": "新增图表（与 create_data_screen.widgets 同形状：{title, chart_type, query, site?, config?, position?, component_ids?}）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "chart_type": {"type": "string"},
                            "query": {"type": "object", "properties": _INTENT_PROPERTIES,
                                      "description": "声明式语义意图（必填，iframe 除外）；裸 SQL 会被拒"},
                            "site": {"type": "string", "description": "本图数据源业务名（不传则用顶层 site）"},
                            "datasource": {"type": "string", "description": "同 site（site 优先）"},
                            "component_ids": {"type": "array",
                                              "items": {"type": ["integer", "string"]}},
                            "config": {"type": "object"},
                            "position": _POSITION_SCHEMA,
                        },
                        "required": ["title", "chart_type"],
                    },
                },
                "remove_charts": {
                    "type": "array", "items": {"type": "string"},
                    "description": "按图表业务标题删除（精确命中且唯一才删；未命中/重名会报错并列清单）",
                },
                "relayout": {"type": "boolean",
                             "description": "true=整板按服务端主次配方重排（会重写存量卡坐标，只在用户要求重排时用）"},
                "emphasis": {
                    "type": "object",
                    "description": "重排时的主次指定：{hero: 主图标题, detail: 整宽明细卡标题}",
                    "properties": {"hero": {"type": "string"}, "detail": {"type": "string"}},
                },
                "site": {"type": "string", "description": "add_charts 的默认数据源业务名"},
                "datasource": {"type": "string", "description": "同 site（site 优先）"},
            },
        },
        "handler": update_data_screen,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "delete_data_screen",
        "description": (
            "Delete a dashboard (IRREVERSIBLE: charts, role visibility grants and board-group membership are "
            "removed). Address it by `name` or `dashboard_id`. WITHOUT `confirmed=true` this tool does NOT delete — "
            "it returns needs_user_confirmation, so call ask_user first and only then retry with confirmed=true. "
            "Only the board owner or an admin may delete (role-granted visibility does not include delete); "
            "a permission error here must be reported to the user as-is."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "看板业务名（与 dashboard_id 二选一）"},
                "dashboard_id": {"type": "integer", "description": "看板 id"},
                "confirmed": {"type": "boolean",
                              "description": "必须为 true（先用 ask_user 取得用户同意）"},
            },
        },
        "handler": delete_data_screen,
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
    {
        "name": "list_dashboard_groups",
        "description": (
            "List 看板组合 (dashboard groups) as the user can see them. A group is an ARRANGEMENT object — it "
            "collects existing dashboards, fixes their display/carousel order and assigns the group to roles. "
            "It is NOT 'a combo of charts' and it grants NO visibility: whether a dashboard can be opened is "
            "decided only by role dashboard grants. Use this before managing groups. "
            "管理看板组合：看组合与成员清单，不授予可见权。"
        ),
        "schema": {"type": "object", "properties": {}},
        "handler": list_dashboard_groups,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "manage_dashboard_groups",
        "description": (
            "Edit 看板组合 (dashboard groups): create / rename / delete / set member dashboards / assign roles / "
            "reorder groups. Admin only (same rule as the human admin page) — a non-admin caller must be told so "
            "verbatim. Members are addressed by dashboard BUSINESS NAMES and roles by ROLE NAMES (never ids); "
            "unresolvable or ambiguous names come back as a candidate list. Order of `dashboards` is the "
            "display/carousel order. Deleting a group needs confirmed=true after ask_user. "
            "组合只做编排（分组 + 顺序 + 角色分配），不授予看板可见权。"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_GROUP_ACTIONS)},
                "group": {"type": "string", "description": "目标组合业务名（除 create 外必填）"},
                "name": {"type": "string",
                         "description": "create=新组合名；rename=新名（此时 group 仍指旧名）"},
                "description": {"type": "string", "description": "组合说明（rename 不传则保留原说明）"},
                "dashboards": {"type": "array", "items": {"type": "string"},
                               "description": "set_charts：看板业务名清单，顺序即展示/轮播顺序；[] = 清空"},
                "roles": {"type": "array", "items": {"type": "string"},
                          "description": "set_roles：角色业务名清单；[] = 取消全部角色分配"},
                "order": {"type": "array", "items": {"type": "string"},
                          "description": "reorder：全部组合名，按展示先后排列"},
                "confirmed": {"type": "boolean", "description": "delete 需先经用户确认（ask_user）后传 true"},
            },
            "required": ["action"],
        },
        "handler": manage_dashboard_groups,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "list_vis_components",
        "description": "List reusable visualization building blocks (vis library / 字模): chart styles, backgrounds, KPI cards, layouts, decorations, color themes. Metadata only.",
        "schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string",
                             "description": "Optional filter: chart_style|screen_background|kpi_card|"
                                            "layout_template|decoration_frame|color_theme"},
            },
        },
        "handler": list_vis_components,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_vis_component",
        "description": "Get a single vis-library block by id or code (visual config level only).",
        "schema": {
            "type": "object",
            "properties": {
                "component_id": {"type": ["integer", "string"], "description": "Block id"},
                "code": {"type": "string", "description": "Or block code"},
            },
        },
        "handler": get_vis_component,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "save_vis_component",
        "description": "Save a designed visual block (chart style / background / card / color theme) back into the vis library as a reusable custom block (movable-type accumulation).",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Block display name (required)"},
                "category": {"type": "string",
                             "description": "chart_style|screen_background|kpi_card|layout_template|"
                                            "decoration_frame|color_theme (required)"},
                "chart_type": {"type": "string", "description": "Applicable chart type for chart_style blocks"},
                "style_config": {"type": "object", "description": "Style config object for the block"},
                "description": {"type": "string"},
            },
            "required": ["name", "category"],
        },
        "handler": save_vis_component,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_screen_server(backend: str = "qoder", tool_names=None):
    """构建 datahub_screen 进程内 MCP server(qoder / claude)。tool_names 给定时按逐工具注册。"""
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                  annotations=s.get("annotations") or {"readOnlyHint": False})
        for s in specs
    ]
    return make_server(backend, "datahub_screen", tools)
