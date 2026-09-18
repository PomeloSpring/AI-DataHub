"""Screen Tools — datahub_screen 工具组: 生成/查询/更新可视化数据大屏 + 字模库取用。

统一语义层下的取数护栏(见 security-guardrails §6/§7):
- Chat/Agent(AS-BOT)侧 widget 只接受**声明式语义意图**(query), 由服务端经语义层
  (intent -> binding -> plan) 解析出 base SQL, 再走 governed_execute 执行;
  **拒收任何 sql/raw_sql/statement 字段**, LLM 永不产出/看到裸 SQL。
- 生成的 base SQL 存入 adh_charts.sql_query(query_source=semantic, 保留 semantic_query
  意图) 以便前端渲染/刷新复用既有治理通道; 数据源对 LLM 是黑盒(结果不回显 SQL/数据源)。
- 传统 BI 手填 SQL 模板走 dataviz REST(图表配置), 不经本工具; 两者取数最终都过护城河。

字模库(adh_vis_components)是纯配置(背景/卡片/图表样式/布局/配色/SQL模板), 元数据级安全;
save/list/get 不返回数据行, 且对 LLM 隐藏 sql_template 的裸 SQL。
"""

import asyncio
import json
import logging

from services.datamind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

# 大屏画布基准(与前端 Screen 页 1920x1080 缩放布局一致)
_CANVAS_W = 1920
_CANVAS_H = 1080
_MAX_WIDGETS = 12
_KPI_H = 180
_ROW_GAP = 12

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

# LLM 严禁提供的裸 SQL 字段(数据护城河 §6)
_SQL_FIELDS = ("sql", "raw_sql", "statement", "query_sql", "base_sql")


def _error(message: str, **extra) -> dict:
    return _text({"error": message, **extra}, is_error=True)


def _default_datasource_id() -> int:
    """取默认数据源 ID(is_default=1), 无则取第一个(均排除内部数据源)。仅元数据查询。"""
    from services.shared.common.db import execute_query

    row = execute_query(
        "SELECT id FROM adh_datasources WHERE is_default = 1 AND is_internal = 0 ORDER BY id LIMIT 1",
        fetchone=True,
    ) or execute_query(
        "SELECT id FROM adh_datasources WHERE is_internal = 0 ORDER BY id LIMIT 1", fetchone=True
    )
    return int(row["id"]) if row else 0


def _safe_exec_error(exc: Exception, where: str) -> dict:
    """执行阶段失败: 原始细节仅进服务端日志, 对 LLM 回通用文案(数据源黑盒 §7)。"""
    from services.dataviz.services.governed_query import NoIdentityError

    if isinstance(exc, NoIdentityError):
        return _error("缺少可信用户身份, 拒绝取数(数据合规护城河)。")
    logger.warning("[screen] %s failed (detail server-side only): %s", where, exc)
    return _error(
        "取数在执行阶段失败(通常是数据源连接/凭据或物理表暂不可用)。请勿猜测或向用户展示"
        "数据源主机/账号/IP/SQL 等细节, 建议稍后重试或联系管理员核实数据源可用性。"
    )


async def _resolve_intent(intent_payload: dict, ctx):
    """声明式意图 -> (base_sql, datasource_id, result)。经语义层解析 + governed 执行。

    失败抛 ScreenIntentError(携带可安全回给 LLM 的声明式原因)。
    """
    from services.shared.semantics.binding_resolver import resolve_binding
    from services.shared.semantics.intent import parse_intent
    from services.shared.semantics.planner import plan
    from services.dataviz.services.governed_query import governed_execute, NoIdentityError

    payload = dict(intent_payload or {})
    # 数据源黑盒: 以执行上下文(会话选定数据源)为权威, 不让 LLM 指定
    if ctx is not None:
        if getattr(ctx, "datasource_id", 0):
            payload["datasource_id"] = int(ctx.datasource_id)
        if not payload.get("workspace_id") and getattr(ctx, "workspace_id", 0):
            payload["workspace_id"] = int(ctx.workspace_id)

    q, err, notes = parse_intent(payload)
    if err:
        raise ScreenIntentError(_error(f"图表查询意图不合法: {err}", notes=notes))

    binding, bind_warnings = await asyncio.to_thread(
        resolve_binding, q.object, datasource_id=q.datasource_id,
    )
    if binding is None:
        raise ScreenIntentError(_error(
            f"对象 '{q.object}' 未绑定到任何物理表。",
            warnings=[w for w in (bind_warnings or []) if "catalog" not in str(w).lower() and "datasource" not in str(w).lower()],
            hint="先用 knowledge_search 找一个已在本体里 bound 的对象。",
        ))

    p = await asyncio.to_thread(plan, q, binding)
    if not p.sql:
        raise ScreenIntentError(_error("语义层护栏拒绝了本次查询, 请调整对象/指标/维度或加过滤后重试。"))

    uid = (ctx.user_id if ctx else 0) or 0
    ws = (ctx.workspace_id if ctx else 0) or 0
    username = (ctx.username if ctx else "") or ""
    result = await asyncio.to_thread(
        governed_execute, p.sql, int(binding.datasource_id), uid, ws, username,
    )
    return p.sql, int(binding.datasource_id), result


class ScreenIntentError(Exception):
    """携带已构造好的(安全)错误响应。"""

    def __init__(self, response: dict):
        super().__init__("screen intent error")
        self.response = response


def _resolve_style_refs(refs, merged_config: dict) -> None:
    """把字模引用(id 或 code)对应的 style_config.config 合并进图表 config。"""
    from services.dataviz.services.vis_library_service import vis_library_service

    for ref in (refs or []):
        comp = None
        try:
            comp = vis_library_service.get_component(int(ref))
        except (TypeError, ValueError):
            comp = vis_library_service.get_component_by_code(str(ref))
        if not comp:
            continue
        sc = comp.get("style_config") or {}
        cfg = sc.get("config") if isinstance(sc, dict) else None
        if isinstance(cfg, dict):
            for k, v in cfg.items():
                merged_config.setdefault(k, v)


def _auto_layout(widgets: list[dict]) -> list[tuple]:
    """为未指定位置的组件自动布局: 铺满 1920x1080 画布。"""
    kpi_types = {"text_display", "big_number_trend", "gauge"}
    kpis = [w for w in widgets if w.get("chart_type") in kpi_types]
    others = [w for w in widgets if w.get("chart_type") not in kpi_types]

    laid: list[tuple] = []
    top_y = 0
    if kpis:
        kw = _CANVAS_W // len(kpis)
        for i, w in enumerate(kpis):
            laid.append((w, {"x": i * kw, "y": 0, "w": kw, "h": _KPI_H}))
        top_y = _KPI_H + _ROW_GAP

    n = len(others)
    if n:
        cols = 3 if n >= 5 else 2
        rows = -(-n // cols)
        ch = max(160, (_CANVAS_H - top_y - _ROW_GAP * (rows - 1)) // rows)
        idx = 0
        for r in range(rows):
            cnt = min(cols, n - idx)
            rw = _CANVAS_W // cnt
            for c in range(cnt):
                laid.append((others[idx], {
                    "x": c * rw, "y": top_y + r * (ch + _ROW_GAP), "w": rw, "h": ch,
                }))
                idx += 1
    return laid


# ── 工具 handler(SDK 无关) ─────────────────────────────────────────────

async def create_data_screen(args: dict) -> dict:
    """创建一个可视化数据大屏: 逐图按语义意图取数并预填充数据缓存。"""
    args = args or {}
    name = (args.get("name") or "").strip()
    widgets = args.get("widgets") or []
    if not name:
        return _error("name is required")
    if not widgets:
        return _error("widgets is required(至少一个图表组件)")
    if len(widgets) > _MAX_WIDGETS:
        return _error(f"组件数量不能超过 {_MAX_WIDGETS}")

    from services.datamind.execution.sdk_tools import ExecutionContextVar
    from services.dataviz.services.dashboard_service import ChartService, DashboardService

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0
    workspace_id = (ctx.workspace_id if ctx else 0) or 0

    # 参数定义(时间范围/自定义筛选): 供前端交互控件, 数据以意图预填充为准
    params: list[dict] = []
    for f in args.get("filters") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        params.append({
            "name": str(f["name"]),
            "label": str(f.get("label") or f["name"]),
            "type": "select" if f.get("options") else "text",
            "options": [str(o) for o in (f.get("options") or [])],
            "default": str(f.get("default") or ""),
        })

    # 大屏主题 + 字模引用(背景/配色/卡片/布局/装饰), 寄存 filters
    raw_theme = args.get("theme") if isinstance(args.get("theme"), dict) else {}
    theme = {k: v for k, v in raw_theme.items() if k in _THEME_KEYS and v not in (None, "")}
    theme_mode = theme.get("mode") if theme.get("mode") in ("light", "dark") else None
    style = args.get("style") if isinstance(args.get("style"), dict) else {}
    filters = {}
    if theme:
        filters["theme"] = theme
    if style:
        filters["components"] = style

    # 逐图: 校验 + 解析意图 + governed 预执行
    prepared = []
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            return _error(f"widgets[{i}] 必须是对象")
        # 数据护城河 §6: 拒收 LLM 直传的裸 SQL 字段
        leaked = [k for k in _SQL_FIELDS if isinstance(w.get(k), str) and w.get(k).strip()]
        if leaked:
            return _error(
                f"widgets[{i}] 不允许直接提供 SQL 字段({','.join(leaked)})。"
                "请用 query 传声明式语义意图 {object, metrics[], dimensions[], filters[], order, limit, time_window}。"
            )
        title = (w.get("title") or f"图表 {i + 1}").strip()
        chart_type = (w.get("chart_type") or "bar").strip().lower()
        if chart_type not in _CHART_TYPES:
            return _error(f"widgets[{i}] 不支持的图表类型: {chart_type}, 可选: {', '.join(sorted(_CHART_TYPES))}")

        cfg = w.get("config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except (json.JSONDecodeError, TypeError):
                cfg = {}
        cfg = dict(cfg)
        # 合并图表级字模引用
        _resolve_style_refs(w.get("component_ids") or ([w["style_ref"]] if w.get("style_ref") else None), cfg)

        # iframe: 无取数
        if chart_type in _NO_SQL_TYPES:
            iframe_url = str(cfg.get("url") or "").strip()
            if not iframe_url.startswith(("http://", "https://", "/")):
                return _error(f"widgets[{i}]({title}) iframe 组件需在 config.url 提供 http(s)/相对地址")
            prepared.append({"title": title, "chart_type": chart_type, "intent": None,
                             "sql": "", "ds": 0, "result": {"columns": [], "rows": [], "row_count": 0},
                             "config": cfg, "widget": w})
            continue

        intent = w.get("query") or w.get("semantic_query")
        if not intent:
            return _error(f"widgets[{i}]({title}) 缺少 query(声明式语义意图)")
        try:
            base_sql, ds_id, result = await _resolve_intent(intent, ctx)
        except ScreenIntentError as e:
            return e.response
        except Exception as e:
            return _safe_exec_error(e, f"create widget {title}")

        prepared.append({"title": title, "chart_type": chart_type, "intent": intent,
                         "sql": base_sql, "ds": ds_id, "result": result,
                         "config": cfg, "widget": w})

    # 创建大屏 + 图表
    dashboard_id = DashboardService().create_dashboard({
        "name": name,
        "description": args.get("description") or "",
        "params": params,
        "filters": filters,
        "status": "enabled",
        "is_public": True,
        "workspace_id": workspace_id,
    }, user_id)

    chart_service = ChartService()
    positions = {id(widget): pos for widget, pos in _auto_layout([it["widget"] for it in prepared])}
    layout_log = []
    for item in prepared:
        custom_pos = item["widget"].get("position") or {}
        position = custom_pos if all(k in custom_pos for k in ("x", "y", "w", "h")) else positions.get(id(item["widget"]), {"x": 0, "y": 0, "w": 640, "h": 360})
        layout_log.append({"title": item["title"], **{k: position.get(k) for k in ("x", "y", "w", "h")}})
        chart_service.create_chart(dashboard_id, {
            "name": item["title"],
            "chart_type": item["chart_type"],
            "sql_query": item["sql"] or None,
            "source_id": item["ds"] or None,
            "config": {**({"theme": theme_mode} if theme_mode else {}), **item["config"],
                       **({"datasource_id": item["ds"]} if item["ds"] else {})},
            "position": position,
            "source_type": "query",
            "semantic_query": item["intent"],
            "query_source": "semantic" if item["intent"] else "raw_sql",
            "data_cache": json.dumps({
                "columns": item["result"].get("columns") or [],
                "rows": item["result"].get("rows") or [],
                "row_count": item["result"].get("row_count", len(item["result"].get("rows") or [])),
            }, ensure_ascii=False, default=str),
        })

    url = f"/screen/{dashboard_id}"
    return _text({
        "dashboard_id": dashboard_id,
        "name": name,
        "url": url,
        "charts": len(prepared),
        "params": [p["name"] for p in params],
        "style": style or None,
        "layout": layout_log,
        "message": f"数据大屏「{name}」已创建({len(prepared)} 个图表), 访问路径: {url}",
    })


# 不得回给 LLM 的基础设施/物理层配置键(数据源黑盒 §7)
_LEAK_CONFIG_KEYS = {"datasource_id", "source_id", "physical_table", "catalog_ref", "catalog", "base_sql", "secured_sql", "provenance", "account", "host", "ip"}


def _sanitize_config(cfg) -> dict:
    """剔除 config 里的数据源/物理层标识, 只回视觉配置给 LLM。"""
    if not isinstance(cfg, dict):
        return {}
    return {k: v for k, v in cfg.items() if k not in _LEAK_CONFIG_KEYS}


def _chart_summary(c: dict) -> dict:
    """图表摘要: 剥离 sql_query / 数据源 id, 保留声明式意图与视觉配置(数据源黑盒 §7)。"""
    return {
        "chart_id": c.get("id"),
        "title": c.get("name"),
        "chart_type": c.get("chart_type"),
        "query": c.get("semantic_query"),
        "config": _sanitize_config(c.get("config")),
        "position": c.get("position"),
    }


async def get_data_screen(args: dict) -> dict:
    """获取数据大屏详情(含图表意图与配置, 不返回裸 SQL/数据源/数据行)。"""
    args = args or {}
    dashboard_id = int(args.get("dashboard_id") or 0)
    if not dashboard_id:
        return _error("dashboard_id is required")

    from services.datamind.execution.sdk_tools import ExecutionContextVar
    from services.dataviz.services.dashboard_service import DashboardService

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0

    dashboard = DashboardService().get_dashboard(dashboard_id, user_id)
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
    """更新大屏中的某个图表(标题/类型/意图/配置/位置), 如换意图则重新取数刷新缓存。"""
    args = args or {}
    dashboard_id = int(args.get("dashboard_id") or 0)
    chart_id = int(args.get("chart_id") or 0)
    if not dashboard_id or not chart_id:
        return _error("dashboard_id 和 chart_id 都是必填项")

    from services.datamind.execution.sdk_tools import ExecutionContextVar
    from services.dataviz.services.dashboard_service import ChartService, DashboardService, _invalidate_dashboard_cache

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0

    dashboard = DashboardService().get_dashboard(dashboard_id, user_id)
    if not dashboard:
        return _error(f"大屏 {dashboard_id} 不存在")

    # §6: 拒收 LLM 直传裸 SQL
    leaked = [k for k in _SQL_FIELDS if isinstance(args.get(k), str) and args.get(k).strip()]
    if leaked:
        return _error(f"不允许直接提供 SQL 字段({','.join(leaked)}); 请用 query 传声明式语义意图。")

    update_data: dict = {}
    if args.get("title"):
        update_data["name"] = args["title"].strip()
    if args.get("chart_type"):
        ct = args["chart_type"].strip().lower()
        if ct not in _CHART_TYPES:
            return _error(f"不支持的图表类型: {ct}")
        update_data["chart_type"] = ct
    if args.get("config"):
        update_data["config"] = args["config"]
    if args.get("position"):
        update_data["position"] = args["position"]

    intent = args.get("query") or args.get("semantic_query")
    if intent:
        update_data["semantic_query"] = intent
        update_data["query_source"] = "semantic"
        try:
            base_sql, ds_id, result = await _resolve_intent(intent, ctx)
        except ScreenIntentError as e:
            return e.response
        except Exception as e:
            return _safe_exec_error(e, "update chart")
        update_data["sql_query"] = base_sql
        update_data["source_id"] = ds_id
        update_data["data_cache"] = json.dumps({
            "columns": result.get("columns") or [],
            "rows": result.get("rows") or [],
            "row_count": result.get("row_count", len(result.get("rows") or [])),
        }, ensure_ascii=False, default=str)

    if not update_data:
        return _error("没有提供任何要更新的字段(title/chart_type/query/config/position)")

    # 保留原有未提供字段的 config/data_cache: 先读原图再浅合并
    existing = next((c for c in (dashboard.get("charts") or []) if c.get("id") == chart_id), None)
    if existing and "config" in update_data and isinstance(existing.get("config"), dict):
        update_data["config"] = {**existing["config"], **update_data["config"]}

    success = ChartService().update_chart(dashboard_id, chart_id, update_data)
    if not success:
        return _error("更新失败, 图表不存在或无变化")

    _invalidate_dashboard_cache(user_id)
    return _text({
        "dashboard_id": dashboard_id,
        "chart_id": chart_id,
        "url": f"/screen/{dashboard_id}",
        "updated_fields": list(update_data.keys()),
        "message": f"图表已更新, 访问路径: /screen/{dashboard_id}",
    })


# ── 字模库取用工具(元数据级, 不返回数据行; 对 LLM 隐藏 sql_template 裸 SQL) ──

async def list_vis_components(args: dict) -> dict:
    """列出可视化字模(按类别过滤)。返回名称/类别/编码/说明, 不含数据行/裸 SQL。"""
    args = args or {}
    from services.dataviz.services.vis_library_service import vis_library_service

    category = (args.get("category") or "").strip()
    comps = vis_library_service.list_components(category)
    summaries = [{
        "id": c.get("id"), "code": c.get("code"), "name": c.get("name"),
        "category": c.get("category"), "chart_type": c.get("chart_type"),
        "description": c.get("description"), "style_config": c.get("style_config"),
    } for c in comps]
    return _text({"count": len(summaries), "components": summaries})


async def get_vis_component(args: dict) -> dict:
    """获取单个字模详情(配置级); sql_template 仅回参数定义, 不回裸 SQL。"""
    args = args or {}
    ref = args.get("component_id") or args.get("code")
    if not ref:
        return _error("component_id 或 code 必填")
    from services.dataviz.services.vis_library_service import vis_library_service

    comp = None
    try:
        comp = vis_library_service.get_component(int(ref))
    except (TypeError, ValueError):
        comp = vis_library_service.get_component_by_code(str(ref))
    if not comp:
        return _error("字模不存在")
    qt = comp.get("query_template") or {}
    comp["query_template"] = {"params": qt.get("params", []), "bindTable": qt.get("bindTable", False)} if qt else None
    return _text(comp)


async def save_vis_component(args: dict) -> dict:
    """把大屏里设计好的一段视觉(图表样式/背景/卡片/配色)回存为自定义字模(活字沉淀)。"""
    args = args or {}
    from services.datamind.execution.sdk_tools import ExecutionContextVar
    from services.dataviz.services.vis_library_service import vis_library_service

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0
    is_admin = bool(ctx and getattr(ctx, "user_role", "") == "admin")
    try:
        cid = vis_library_service.create_component(args, user_id, is_admin)
    except ValueError as e:
        return _error(str(e))
    return _text({"component_id": cid, "message": "字模已保存到 UI 库(自定义)"})


# ── MCP server 构建 ────────────────────────────────────────────────────

TOOL_SPECS = [
    {
        "name": "create_data_screen",
        "description": (
            "Create a visualization data screen (BI dashboard) from declarative chart intents. "
            "Each widget MUST provide a `query` semantic intent "
            "({object, metrics[], dimensions[], filters[], order[], limit, time_window}) — "
            "NEVER pass raw SQL fields (sql/raw_sql/statement are rejected). "
            "The semantic layer resolves intent to SQL and executes it through the governed data moat. "
            "Optional per-widget `component_ids`/`style_ref` and top-level `style` reference vis-library "
            "blocks (background / card / chart style / layout / color theme) — list them via list_vis_components."
        ),
        "schema": {
            "name": "Screen title (required)",
            "widgets": "List of widget dicts: {title, chart_type, query<intent>, component_ids?, config?, position?}",
            "description": "Optional screen description",
            "theme": "Optional {mode, background, backgroundImage, header, card}",
            "style": "Optional {background?, colorTheme?, cardStyle?, layout?, decoration?} component refs",
            "filters": "Optional interactive filter param defs [{name,label,options?,default?}]",
        },
        "handler": create_data_screen,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "get_data_screen",
        "description": "Get a data screen's detail with each chart's declarative intent + visual config (no raw SQL / datasource / rows).",
        "schema": {"dashboard_id": "Screen id (required)"},
        "handler": get_data_screen,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "update_data_screen_chart",
        "description": (
            "Update one chart in a data screen (title/chart_type/query intent/config/position). "
            "To change data, pass a new declarative `query` intent — raw SQL is rejected."
        ),
        "schema": {
            "dashboard_id": "Screen id (required)", "chart_id": "Chart id (required)",
            "title": "New title (optional)", "chart_type": "New chart type (optional)",
            "query": "New declarative intent (optional)",
            "config": "Visual config merge (optional)", "position": "{x,y,w,h} (optional)",
        },
        "handler": update_data_screen_chart,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "list_vis_components",
        "description": "List reusable visualization building blocks (vis library / 字模): chart styles, backgrounds, KPI cards, layouts, decorations, color themes. Metadata only.",
        "schema": {"category": "Optional filter: chart_style|screen_background|kpi_card|layout_template|decoration_frame|color_theme|sql_template"},
        "handler": list_vis_components,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_vis_component",
        "description": "Get a single vis-library block by id or code (config level; sql_template shows param definitions only, not raw SQL).",
        "schema": {"component_id": "Block id", "code": "Or block code"},
        "handler": get_vis_component,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "save_vis_component",
        "description": "Save a designed visual block (chart style / background / card / color theme) back into the vis library as a reusable custom block (movable-type accumulation).",
        "schema": {
            "name": "Block display name (required)",
            "category": "chart_style|screen_background|kpi_card|layout_template|decoration_frame|color_theme (required)",
            "chart_type": "Applicable chart type for chart_style blocks (optional)",
            "style_config": "Style config JSON for the block (optional)",
            "description": "Description (optional)",
        },
        "handler": save_vis_component,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_screen_server(backend: str = "qoder", tool_names=None):
    """构建 datahub_screen 进程内 MCP server(qoder / claude)。tool_names 给定时按逐工具注册。"""
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                  annotations=s.get("annotations") or READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_screen", tools)
