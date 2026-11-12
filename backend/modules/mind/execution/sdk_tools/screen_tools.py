"""Screen Tools — datahub_screen 工具组: 生成/查询/更新可视化数据大屏 + 字模库取用。

统一语义层下的取数护栏(见 security-guardrails §6/§7):
- Chat/Agent(AS-BOT)侧 widget 只接受**声明式语义意图**(query), 由服务端经语义层
  (intent -> binding -> plan) 解析出 base SQL, 再走 governed_execute 执行;
  **拒收任何 sql/raw_sql/statement 字段**, LLM 永不产出/看到裸 SQL。
- 生成的 base SQL 存入 adh_charts.sql_query(query_source=semantic, 保留 semantic_query
  意图) 以便前端渲染/刷新复用既有治理通道; 数据源对 LLM 是黑盒(结果不回显 SQL/数据源)。
- 传统 BI 手填 SQL 模板走 dataviz REST(图表配置), 不经本工具; 两者取数最终都过护城河。

字模库(adh_vis_components)是纯配置(背景/卡片/图表样式/布局/配色), 元数据级安全;
save/list/get 不返回数据行。预制 SQL 属于数据集职责（已治理的查询资产），不在字模库。
"""

import asyncio
import json
import logging
import re

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

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

# 参数类型词汇: 与前端 `shapeOfParam`(dashboardParamAssembly.ts) 同一门说法。
# 历史缺陷: 后端只写死 select/text, 前端已支持 date_range/date/number 的控件分支,
# 于是 AI 生成的大屏拿不到日期区间控件 —— 类型"存了但渲染不生效"的另一面是
# "渲染支持但上游生不成"。两侧必须同词汇, 新增形状时改这一处即可被发现。
_PARAM_TYPES = {"text", "number", "date", "date_range", "select"}
_PARAM_TYPE_ALIASES = {"string": "text", "enum": "select", "list": "select"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _range_default(value, where: str) -> str:
    """date_range 默认值归一为 `"start,end"` 字符串。

    不存 {start,end} 对象: 前端 `DashboardParam.default` 声明的是 string, 而
    `rangeOf()` 同时读得懂"a,b"文本与控件写回的对象 —— 用对象存会越出已声明契约,
    用户后续在编辑器里一次保存就可能被抹成字符串。两端都空时给空串。
    """
    if isinstance(value, dict):
        start, end = str(value.get("start") or ""), str(value.get("end") or "")
    elif value in (None, ""):
        return ""
    elif isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 2:
            raise ValueError(f"{where} date_range 默认值需为 'YYYY-MM-DD,YYYY-MM-DD'")
        start, end = parts
    else:
        raise ValueError(f"{where} date_range 默认值需为 'YYYY-MM-DD,YYYY-MM-DD'")
    for label, v in (("start", start), ("end", end)):
        if v and not _DATE_RE.match(v):
            raise ValueError(f"{where} date_range 默认值 {label}={v!r} 不是 YYYY-MM-DD")
    if not start and not end:
        return ""
    return f"{start},{end}"


def _build_params(raw_filters) -> tuple[list[dict], str]:
    """filters 参数定义 → 看板 params(与前端控件渲染同源)。返回 (params, 错误文案)。

    宁可报错也不静默退化成文本框: 退化的结果是一个"能填但不出数"的死输入口。
    """
    params: list[dict] = []
    for i, f in enumerate(raw_filters or []):
        if not isinstance(f, dict):
            return [], f"filters[{i}] 必须是对象({{name, label?, type?, options?, default?}})"
        name = str(f.get("name") or "").strip()
        if not name:
            return [], f"filters[{i}] 缺少 name(参数名是控件与 {{{{参数}}}} 占位符对得上的唯一键)"
        where = f"filters[{name}]"
        options = [str(o) for o in (f.get("options") or []) if str(o).strip()]
        declared = str(f.get("type") or "").strip().lower()
        ptype = _PARAM_TYPE_ALIASES.get(declared, declared)
        if declared and ptype not in _PARAM_TYPES:
            return [], (f"{where} 不支持的参数类型 '{declared}', 可选: "
                        f"{', '.join(sorted(_PARAM_TYPES))}(date_range 会渲染成起止两个日期选择器)")
        if not ptype:
            ptype = "select" if options else "text"
        if ptype == "select" and not options:
            return [], f"{where} 声明为 select 但没有候选值 —— 前端会渲染成'点开是空的'死控件。请补 options, 或改用 type='text'。"
        if options and ptype != "select":
            return [], f"{where} 参数类型 '{ptype}' 不使用候选值, options 请去掉(或改 type='select')"
        try:
            if ptype == "date_range":
                entry_default = _range_default(f.get("default"), where)
            elif ptype == "date":
                entry_default = "" if f.get("default") in (None, "") else str(f.get("default"))
                if entry_default and not _DATE_RE.match(entry_default):
                    raise ValueError(f"{where} date 默认值 {entry_default!r} 不是 YYYY-MM-DD")
            else:
                entry_default = "" if f.get("default") is None else str(f.get("default"))
        except ValueError as e:
            return [], str(e)
        entry: dict = {"name": name, "label": str(f.get("label") or name), "type": ptype,
                       "default": entry_default}
        if ptype == "select":
            entry["options"] = options
        params.append(entry)
    return params, ""


def _error(message: str, **extra) -> dict:
    return _text({"error": message, **extra}, is_error=True)


def _default_datasource_id() -> int:
    """取默认数据源 ID(is_default=1), 无则取第一个(均排除内部数据源)。仅元数据查询。"""
    from backend.common.db import execute_query

    row = execute_query(
        "SELECT id FROM adh_datasources WHERE is_default = 1 AND is_internal = 0 ORDER BY id LIMIT 1",
        fetchone=True,
    ) or execute_query(
        "SELECT id FROM adh_datasources WHERE is_internal = 0 ORDER BY id LIMIT 1", fetchone=True
    )
    return int(row["id"]) if row else 0


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


async def _resolve_intent(intent_payload: dict, ctx):
    """声明式意图 -> (base_sql, datasource_id, result)。经语义层解析 + governed 执行。

    失败抛 ScreenIntentError(携带可安全回给 LLM 的声明式原因)。
    """
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan
    from backend.core.governed_query import governed_execute, NoIdentityError

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

    # 未定出生效源时不得下探 resolve_binding(ds=0)——那会把“本次没定源”报成
    # “对象未绑定/未建模”，模型据此向用户说“该站没这个数据”（历史缺陷：会话
    # 1783069459277 同类；与 run_semantic_query 同一口径：先拿事实判“该对象在 ds=0
    # 上到底有没有绑定”，系统域对象绑定就在 ds=0，照常放行）。
    if not int(q.datasource_id or 0) and ctx is not None:
        probe, _pw = await asyncio.to_thread(resolve_binding, q.object, datasource_id=0)
        if probe is None:
            from backend.modules.mind.execution.sdk_tools.semantic_query import (
                unresolved_source_payload,
            )

            bad = unresolved_source_payload(ctx, q.object)
            raise ScreenIntentError(_error(bad["error"], hint=bad.get("hint")))

    binding, bind_warnings = await asyncio.to_thread(
        resolve_binding, q.object, datasource_id=q.datasource_id,
    )
    if binding is None:
        raise ScreenIntentError(_error(
            f"对象 '{q.object}' 在本次生效数据源上未绑定到任何物理表。",
            warnings=[w for w in (bind_warnings or []) if "catalog" not in str(w).lower() and "datasource" not in str(w).lower()],
            hint="未绑定 ≠ 无数据：这表示该源尚未为此对象建模/绑定物理表，不代表库里没数据；"
                 "严禁据此向用户回答“该站点暂无数据”。换个已建模对象（search_metadata / "
                 "knowledge_search）或先为该源生成业务本体。",
        ))

    from backend.modules.mind.execution.resource_guard import validate_binding
    await asyncio.to_thread(validate_binding, ctx, binding)
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
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    current = get_execution_context()
    from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
    if current and system_scope(current):
        from backend.modules.mind.execution.sdk_tools.dashboard_design_tools import request_dashboard_design
        return await request_dashboard_design({"request": f"创建仪表盘：{args.get('name', '')}",
                                                "name": args.get("name", ""), "operation": "create"})
    name = (args.get("name") or "").strip()
    widgets = args.get("widgets") or []
    if not name:
        return _error("name is required")
    if not widgets:
        return _error("widgets is required(至少一个图表组件)")
    if len(widgets) > _MAX_WIDGETS:
        return _error(f"组件数量不能超过 {_MAX_WIDGETS}")

    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import ChartService, DashboardService

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0
    workspace_id = (ctx.workspace_id if ctx else 0) or 0

    # 参数定义(时间范围/自定义筛选): 供前端交互控件, 数据以意图预填充为准
    params, param_err = _build_params(args.get("filters"))
    if param_err:
        return _error(param_err)

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
        "is_public": False,
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
    # 逐图取数结果记账: 本工具过去只回"建了几张图", 零行与满行在返回里一模一样,
    # 于是模型拿"创建成功"当"数据正常"报给用户(空图被静默吞掉)。行数不属于黑盒项。
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
    if params:
        message += "；参数控件已生成, 但只有被某张图 query 里的 {{参数名}} 引用才会真正参与取数"
    return _text({
        "dashboard_id": dashboard_id,
        "name": name,
        "url": url,
        "charts": len(prepared),
        "data_audit": data_audit,
        "empty_charts": empty_charts,
        "params": [p["name"] for p in params],
        "style": style or None,
        "layout": layout_log,
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
    """更新大屏中的某个图表(标题/类型/意图/配置/位置), 如换意图则重新取数刷新缓存。"""
    args = args or {}
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    current = get_execution_context()
    from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
    if current and system_scope(current):
        from backend.modules.mind.execution.sdk_tools.dashboard_design_tools import request_dashboard_design
        return await request_dashboard_design({"request": f"修改仪表盘 {args.get('dashboard_id')} 的图表 {args.get('chart_id')}",
                                                "operation": "update"})
    dashboard_id = int(args.get("dashboard_id") or 0)
    chart_id = int(args.get("chart_id") or 0)
    if not dashboard_id or not chart_id:
        return _error("dashboard_id 和 chart_id 都是必填项")

    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.dashboard_service import ChartService, DashboardService, _invalidate_dashboard_cache

    ctx = ExecutionContextVar.get()
    user_id = (ctx.user_id if ctx else 0) or 0

    # AS-BOT 大屏设计通道: 访问控制由 resource_guard 的 owner/is_public 策略承担,
    # 不适用角色可见性裁决(看板"可看"改造的明确边界)。
    dashboard = DashboardService().get_dashboard(dashboard_id, user_id, enforce_visibility=False)
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
    row_count: int | None = None
    if intent:
        update_data["semantic_query"] = intent
        update_data["query_source"] = "semantic"
        try:
            base_sql, ds_id, result = await _resolve_intent(intent, ctx)
        except ScreenIntentError as e:
            return e.response
        except Exception as e:
            return _safe_exec_error(e, "update chart")
        row_count = int(result.get("row_count") if result.get("row_count") is not None
                        else len(result.get("rows") or []))
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
    message = f"图表已更新, 访问路径: /screen/{dashboard_id}"
    if row_count == 0:
        message += ("；本次取数为空 —— 空图不是成功, 请核对时间窗/过滤条件/口径覆盖"
                    "并向用户说明(不得换近似指标凑数)")
    return _text({
        "dashboard_id": dashboard_id,
        "chart_id": chart_id,
        "url": f"/screen/{dashboard_id}",
        "updated_fields": list(update_data.keys()),
        "row_count": row_count,
        "message": message,
    })


# ── 字模库取用工具（元数据级，不返回数据行）──

async def list_vis_components(args: dict) -> dict:
    """列出可视化字模(按类别过滤)。返回名称/类别/编码/说明, 不含数据行/裸 SQL。"""
    args = args or {}
    from backend.modules.viz.services.vis_library_service import vis_library_service

    category = (args.get("category") or "").strip()
    comps = vis_library_service.list_components(category)
    summaries = [{
        "id": c.get("id"), "code": c.get("code"), "name": c.get("name"),
        "category": c.get("category"), "chart_type": c.get("chart_type"),
        "description": c.get("description"), "style_config": c.get("style_config"),
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
    return _text(comp)


async def save_vis_component(args: dict) -> dict:
    """把大屏里设计好的一段视觉(图表样式/背景/卡片/配色)回存为自定义字模(活字沉淀)。"""
    args = args or {}
    from backend.modules.mind.execution.sdk_tools import ExecutionContextVar
    from backend.modules.viz.services.vis_library_service import vis_library_service

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
            "blocks (background / card / chart style / layout / color theme) — list them via list_vis_components. "
            "Returns `data_audit` (per-chart row_count) and `empty_charts`: a created chart with 0 rows is NOT a "
            "success — tell the user which charts are empty and why instead of reporting 'created'."
        ),
        "schema": {
            "name": "Screen title (required)",
            "widgets": "List of widget dicts: {title, chart_type, query<intent>, component_ids?, config?, position?}",
            "description": "Optional screen description",
            "theme": "Optional {mode, background, backgroundImage, header, card}",
            "style": "Optional {background?, colorTheme?, cardStyle?, layout?, decoration?} component refs",
            "filters": (
                "Optional interactive param defs [{name, label?, type?(text|number|date|date_range|select), "
                "options?(required for select), default?}]. date_range renders two date pickers, its default is "
                "'YYYY-MM-DD,YYYY-MM-DD', and it is consumed as {{name.start}} / {{name.end}}; a param only affects "
                "data when some widget query references {{name}} — do not declare params nothing uses"
            ),
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
            "To change data, pass a new declarative `query` intent — raw SQL is rejected. "
            "Returns `row_count` of the re-executed intent (0 = empty chart, must be surfaced to the user)."
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
        "schema": {"category": "Optional filter: chart_style|screen_background|kpi_card|layout_template|decoration_frame|color_theme"},
        "handler": list_vis_components,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_vis_component",
        "description": "Get a single vis-library block by id or code (visual config level only).",
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
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                  annotations=s.get("annotations") or {"readOnlyHint": s["name"] == "get_dashboard_design"})
        for s in specs
    ]
    return make_server(backend, "datahub_screen", tools)
