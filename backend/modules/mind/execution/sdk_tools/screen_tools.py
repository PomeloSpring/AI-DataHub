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

# 布局分层：KPI 数字卡一行，趋势族一行，其余（占比/明细/地图）一行
_KPI_TYPES = {"text_display", "big_number_trend", "gauge"}
_TREND_TYPES = {"line", "area", "timeseries_line", "timeseries_bar", "timeseries_area"}

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


def _auto_layout(widgets: list[dict]) -> list[tuple]:
    """为未指定位置的组件自动布局: 铺满 1920x1080 画布。

    两条口径（2026-11-17 直建整改，反馈："多站要放一块板，而且排布很乱"）：

    * **多站一屏**（widget 上出现 ≥2 个不同 site）：按**站点列**排 —— 一列一个站，
      列内自上而下 KPI → 趋势 → 其他。同屏横向对比各站，而不是每站各建一块板。
    * **单站/无站**：KPI 一行（单卡最宽 480，左对齐不拉伸），内容区按趋势/其他分层，
      两张内容图时趋势占 2/3、另一张占 1/3（驾驶舱主左辅右）。

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
    return _layout_stacked(enriched)


def _layout_by_site(enriched: list, sites: list[str]) -> list[tuple]:
    """站点矩阵布局：**行 = 指标家族、列 = 站点**。

    早期写法是“列内自上而下排”，某站少一张图（如只有中国有状态构成）时，
    其余站的趋势图会被拉成两倍高 —— 横向对比失真（反馈：卡片大小不一、排布乱）。
    改成矩阵后同族图同行等高、同城图同列对齐，缺格留空不拉伸。

    例外：「other」家族（状态构成/表格等异构卡）不是跨站对比项，缺格只会让整行右侧
    空一大片（反馈：右侧错位空白）。该家族未铺满所有站点时改为整行均分并居中。
    """
    col_w = _CANVAS_W // len(sites)
    grid: dict[tuple, list] = {(fam, site): [] for fam in ("kpi", "trend", "other") for site in sites}
    loose: list = []
    for w, fam, site in enriched:
        if site in sites:
            grid[(fam, site)].append(w)
        else:
            loose.append((w, fam))

    fams = [f for f in ("kpi", "trend", "other") if any(grid[(f, s)] for s in sites)]
    # KPI 带高：单站多张 KPI 时按 120 保底向上撑开，不压成一条线
    max_kpi = max((len(grid[("kpi", s)]) for s in sites), default=0)
    kpi_h = max(_KPI_H, 120 * max_kpi) if "kpi" in fams else 0
    top = kpi_h + (_ROW_GAP if kpi_h else 0)
    rest_fams = [f for f in fams if f != "kpi"]
    band_h = (max(180, (_CANVAS_H - top - _ROW_GAP * (len(rest_fams) - 1)) // len(rest_fams))
              if rest_fams else 0)

    laid: list[tuple] = []
    for fam in fams:
        y = 0 if fam == "kpi" else top + rest_fams.index(fam) * (band_h + _ROW_GAP)
        h = kpi_h if fam == "kpi" else band_h
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
            each = h // len(items)
            for j, w in enumerate(items):
                laid.append((w, {"x": i * col_w, "y": y + j * each, "w": col_w, "h": each}))

    # 未写 site 的图（跨站屏里漏标）落到底部整行，不静默丢图
    if loose:
        row_h = max(180, _CANVAS_H // max(1, len(loose) + 2))
        width = _CANVAS_W // len(loose)
        y = _CANVAS_H - row_h
        for j, (w, _) in enumerate(loose):
            laid.append((w, {"x": j * width, "y": y, "w": width, "h": row_h}))
    return laid


def _layout_stacked(enriched: list) -> list[tuple]:
    """单站/无站布局：KPI 行 + 内容区分层。"""
    kpis = [w for w, fam, _ in enriched if fam == "kpi"]
    rest = [(w, fam) for w, fam, _ in enriched if fam != "kpi"]

    laid: list[tuple] = []
    top_y = 0
    if kpis:
        # 单卡最宽 480：整屏一个 KPI 时拉满会让迷你趋势被拉平、数字悬空
        kw = min(_KPI_MAX_W, max(_KPI_MIN_W, _CANVAS_W // len(kpis)))
        for i, w in enumerate(kpis):
            laid.append((w, {"x": i * kw, "y": 0, "w": kw, "h": _KPI_H}))
        top_y = _KPI_H + _ROW_GAP

    n = len(rest)
    if n == 1:
        laid.append((rest[0][0], {"x": 0, "y": top_y, "w": _CANVAS_W, "h": _CANVAS_H - top_y}))
    elif n == 2:
        # 驾驶舱主左辅右：趋势图占 2/3，另一张占 1/3（两张同族时对半）
        wide = (_CANVAS_W * 2 // 3) if (rest[0][1] == "trend" and rest[1][1] != "trend") else _CANVAS_W // 2
        laid.append((rest[0][0], {"x": 0, "y": top_y, "w": wide, "h": _CANVAS_H - top_y}))
        laid.append((rest[1][0], {"x": wide, "y": top_y, "w": _CANVAS_W - wide, "h": _CANVAS_H - top_y}))
    elif n:
        cols = 3 if n >= 5 else 2
        rows = -(-n // cols)
        ch = max(160, (_CANVAS_H - top_y - _ROW_GAP * (rows - 1)) // rows)
        idx = 0
        for r in range(rows):
            cnt = min(cols, n - idx)
            rw = _CANVAS_W // cnt
            for c in range(cnt):
                laid.append((rest[idx][0], {
                    "x": c * rw, "y": top_y + r * (ch + _ROW_GAP), "w": rw, "h": ch,
                }))
                idx += 1
    return laid


# ── 工具 handler(SDK 无关) ─────────────────────────────────────────────

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
        # theme 既收对象也收 'light'/'dark' 简写：模型只想改明暗时会直接传字符串，
        # 而 SDK 按 schema 类型先拒（历史反馈 2026-11-17：Input validation error: 'light' is not of type 'object'）。
        raw_theme = _coerce_struct(args.get("theme"), "theme", (dict, str))
        if isinstance(raw_theme, str):
            mode = raw_theme.strip().lower()
            if mode not in ("light", "dark"):
                return _error(
                    f"theme 字符串只接受 'light'/'dark'（要背景/卡片等更多字段请传对象 {{mode, background, ...}}），"
                    f"收到: {str(raw_theme)[:40]}")
            raw_theme = {"mode": mode}
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
            "直建大屏不支持交互筛选参数：“每张图的口径存放在数据集里（已固定为本次意图），"
            "看板参数没有消费方，生成了就是“能填不出数”的死控件。确实需要参数控件的，"
            "请到「数据集」页把正文改成 ${参数} 外置形式后人工挂图。")
    
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
    
    chart_service = ChartService()
    try:
        # 布局要按**生效站点**分组：widget 没写 site 时用顶层 site 补齐，
        # 否则跨站屏会被当成单站排成一块，站点列对不齐（同屏多站是本次主诉求）。
        top_site = str(args.get("site") or "").strip()
        layout_widgets = []
        for item in prepared:
            w = item["widget"]
            if not str(w.get("site") or "").strip() and top_site:
                w = {**w, "site": top_site}
                item["widget"] = w
            layout_widgets.append(w)
        positions = {id(widget): pos for widget, pos in _auto_layout(layout_widgets)}
        layout_log = []
        for item in prepared:
            custom_pos = item["widget"].get("position") or {}
            position = custom_pos if all(k in custom_pos for k in ("x", "y", "w", "h")) else positions.get(id(item["widget"]), {"x": 0, "y": 0, "w": 640, "h": 360})
            layout_log.append({"title": item["title"], **{k: position.get(k) for k in ("x", "y", "w", "h")}})
            payload: dict = {
                "name": item["title"],
                "chart_type": item["chart_type"],
                "config": item["config"],
                "position": position,
            }
            if item["dataset"]:
                # 图表只认数据集通道：SQL 由数据集身份持有，图表行不存口径
                payload.update({
                    "source_type": "dataset",
                    "source_id": int(item["dataset"]["id"]),
                    "query_source": "dataset",
                    "semantic_query": None,
                })
            cid = chart_service.create_chart(dashboard_id, payload, identity=identity)
            if item["cache"]:
                # 预填首屏缓存：走看板同一份缓存序列化（行限/字节兜底/完整性标记同口径）
                from backend.common.db import execute_write
        
                execute_write("UPDATE adh_charts SET data_cache=%s WHERE id=%s", (item["cache"], int(cid)))
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
    message = f"数据大屏「{name}」已创建({len(prepared)} 个图表), 访问路径: {url}"
    if empty_charts:
        message += (f"；其中 {len(empty_charts)} 个图表取数为空: {'、'.join(empty_charts)} —— "
                    "空图不是成功, 请核对时间窗/过滤条件/口径覆盖, 并向用户逐图说明(不得换近似指标凑数)")
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

    message = f"图表已更新, 访问路径: /screen/{dashboard_id}"
    if row_count == 0:
        message += ("；本次取数为空 —— 空图不是成功, 请核对时间窗/过滤条件/口径覆盖"
                    "并向用户说明(不得换近似指标凑数)")
    return _text({
        "dashboard_id": dashboard_id,
        "chart_id": chart_id,
        "url": f"/screen/{dashboard_id}",
        "updated_fields": list(update_data.keys()),
        "dataset_name": dataset_name,
        "row_count": row_count,
        "message": message,
    })


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
