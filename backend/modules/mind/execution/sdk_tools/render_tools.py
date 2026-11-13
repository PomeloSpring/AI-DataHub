"""render 工具组 — 交付形态**渲染机**（模型自选通道，服务端只设硬门）。

为什么要做成 tool 而不是提示词约定：` ```chart ` 的正文是模型从查询结果抄进围栏的，
fence 没有回执 —— 未知图型被前端静默退成柱状图、JSON 解析失败静默退代码块、体检不过
只能整批拒收让用户干等。做成 tool 后：① 校验不通过把原因回给模型，它能当场改选；
② 选了哪条通道进 tool_calls，"是否过度使用 HTML"有真实分布可统计；③ 正文走 tool 入站、
回答里只留引用，体积膨胀与流式期源码外显一并消解。

职责边界（与方案的§4 护栏一致）：
- **裁决权在模型**：本模块不判"该不该用 HTML"，只拒"HTML 做不到/会泄露"的事（能力硬门）。
- **G1 单轨**：一轮一个主交付物。回合状态记在 ``ctx.extra``（ExecutionContext 每回合新建，
  天然回合级；不依赖观测的 message_uuid —— 观测关闭时它是空串，当身份用会撞键）。
- **G4 黑盒**：回执只含 doc_id/digest/提示，不含属主 id、数据源标识、正文回显。
- **G5 记账**：每次选道写 `delivery_channel` span（选了哪条、命中哪些门、是否重试），
  这是"先看效果"的评估地基，不可省。

阶段说明（顺序约束：未接线的形态不得在描述里承诺）：
- M1 接 ``render_html``，M2 接 ``render_chart``（数据图表渲染机）；``render_report``（M3，
  结构化章节 spec）到位后再加入 TOOL_SERVER_TOOLS 与描述清单。G2 的"本轮取到过富图型
  数据"事实由 render_chart 通过 ``record_rich_chart_facts`` 登记 —— HTML 通道的警告档
  从那时起吃真事实，不再靠猜测。"结构化章节"在 M3 接线前仍不得对外承诺（契约里写明）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

# 主交付物通道（一轮唯一）：chart 是可多次的片段，html/spec 互斥且唯一。
PRIMARY_CHANNELS = ("html", "spec")

# 图表数据行的入库上限：语义层本身只回 200 行，兜底通道（模型抄 rows）必须有个上限，
# 否则几万行明细会被当成图表正文落库（体积、前端渲染、token 三头都受不到约束）。
_MAX_CHART_ROWS = 500

# 图型别名（与前端 ChartPicker 的 TYPE_ALIASES 同一份叫法）：渲染机归一到真图型，
# 不再把未知图型丢给前端“静默退成柱状图”。
_CHART_ALIASES = {"map": "map_heat", "geo_heatmap": "map_heat", "map_heatmap": "map_heat",
                  "heatmap_map": "map_heat", "column": "bar"}
# table 不是图型（前端无此类型，旧契约里它被静默退成柱状图）：显式拒收并给出路
_NOT_A_CHART = ("table",)

# intent 取数走的执行工具完整名（与 TOOL_SERVER_TOOLS["semantic"] 同源）：渲染机不自带
# 第二条取数通道，也不能被当成绕过逐工具授权的入口。
SEMANTIC_TOOL = "mcp__datahub_semantic__run_semantic_query"

_STATE_KEY = "_render_channel"


def _ctx():
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    return get_execution_context()


def _identity(ctx):
    """服务端解析身份（不信任工具入参）：(user_id, workspace_id, conversation_id)。"""
    extra = getattr(ctx, "extra", None) or {}
    return (int(ctx.user_id or 0), int(ctx.workspace_id or 0),
            int(extra.get("conversation_id") or 0))


def _channel_state(ctx) -> dict:
    """回合级选道状态（ExecutionContext 每回合新建，故无需过期清理）。"""
    extra = getattr(ctx, "extra", None)
    if extra is None:
        return {}
    state = extra.setdefault(_STATE_KEY, {"primary": "", "docs": [], "attempts": {}})
    return state if isinstance(state, dict) else {}


def _message_uuid() -> str:
    """当前回合消息 id（仅审计定位用；观测未开启时为空串，不参与任何唯一性判定）。"""
    try:
        from backend import observability
        return str(observability.message_uuid() or "")
    except Exception:  # noqa: BLE001 — 定位信息拿不到不影响交付
        return ""


def record_rich_chart_facts(ctx, chart_types) -> None:
    """登记本轮已取到的图型事实（由 render_chart / 取数链路调用，供 G2 判 HTML 是否呈现得了）。

    只在真拿到事实时登记，不猜：没登记 = G2 不提示，这不是漏判后静默放行 ——
    HTML 自身画不出伪图的判定由体检②独立把关。
    """
    state = _channel_state(ctx)
    seen = state.setdefault("chart_types", [])
    for t in chart_types or []:
        name = str(t or "").strip().lower()
        if name and name not in seen:
            seen.append(name)


def _claim_primary(ctx, channel: str) -> Optional[str]:
    """G1 单轨：占用本轮主交付物；已被另一形态占用时回阻断原因（不改口、不并存）。"""
    if channel not in PRIMARY_CHANNELS:
        return None
    state = _channel_state(ctx)
    current = str(state.get("primary") or "")
    if current and current != channel:
        label = {"html": "整份 HTML 文档", "spec": "结构化报告"}
        return (f"本轮已有主交付物（{label.get(current, current)}）。一次回答只出一个主体："
                "请修改已有文档（带 doc_id 再调本工具），不要另起一种形态把结论拆成两份。")
    if not current:
        state["primary"] = channel
    return None


def _record_channel(ctx, *, channel: str, doc_id: str, status: str,
                    blockers=None, notes=None, started_at: float) -> None:
    """G5 选道记账：观测未开启即 no-op、绝不抛出（护栏 §9），不影响交付主链路。"""
    state = _channel_state(ctx)
    attempts = state.setdefault("attempts", {})
    attempts[channel] = int(attempts.get(channel) or 0) + 1
    if doc_id and doc_id not in state.setdefault("docs", []):
        state["docs"].append(doc_id)
    try:
        from backend.observability import record_span
        payload = {"channel": channel, "doc_id": doc_id,
                   "blockers": list(blockers or []), "notes": list(notes or []),
                   "attempt": attempts.get(channel, 1)}
        record_span(kind="delivery_channel", name=f"render_{channel}", status=status,
                    duration_ms=int((time.time() - started_at) * 1000),
                    input_text="", output_text=json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        logger.debug("[render] 选道记账不可用", exc_info=True)


async def render_html(args):
    """交付整份自包含 HTML 文档：体检通过则落受管文档对象，回引用 id（不回正文）。"""
    started = time.time()
    ctx = _ctx()
    user_id, workspace_id, conversation_id = _identity(ctx)
    title = str(args.get("title") or "").strip()
    html = str(args.get("html") or "")
    theme = str(args.get("theme") or "").strip()

    def fail(reason: str, blockers=None, notes=None):
        _record_channel(ctx, channel="html", doc_id="", status="error",
                        blockers=blockers or [reason], notes=notes, started_at=started)
        return _text({"error": reason,
                      "how_to_fix": "按原因修改正文后重试；若原因超出 HTML 通道能力，改用其它交付形态并向用户说明"},
                     is_error=True)

    if not title:
        return fail("缺少报告标题 title")
    if not html.strip():
        return fail("缺少报告正文 html")
    if user_id <= 0:
        return fail("无可信身份，不能生成交付文档")

    from backend.modules.viz.services import report_document_service as docs

    if not theme:
        theme = str((getattr(ctx, "extra", None) or {}).get("report_theme") or "").strip()

    # G1 单轨（先占位再校验：校验失败不占用本轮，模型仍可改选另一形态）
    blockers, notes = docs.validate_report_html(
        html, theme=theme,
        rich_chart_types=sorted(set(_channel_state(ctx).get("chart_types") or [])))
    if blockers:
        reason = ("HTML 体检未通过（不是风格问题，是这份文档会坏或会泄露），请修正后重试：\n- "
                  + "\n- ".join(blockers))
        _record_channel(ctx, channel="html", doc_id="", status="error",
                        blockers=blockers, notes=notes, started_at=started)
        return _text({"error": reason,
                      "blockers": blockers,
                      "notes": notes,
                      "hint": "外链需改为内联 SVG/base64；伪地图改 Top N 表格；配色只引用主题令牌；"
                              "正文含系统信息需改为业务表述。"}, is_error=True)

    blocked = _claim_primary(ctx, "html")
    if blocked:
        return fail(blocked)

    doc_id = str(args.get("doc_id") or "").strip()
    try:
        saved = docs.save_document(
            conversation_id=conversation_id, user_id=user_id, workspace_id=workspace_id,
            kind="doc", body_kind="html", title=title, body=html, theme=theme or None,
            message_uuid=_message_uuid(), doc_id=doc_id or None, warnings=notes)
    except docs.RenderDocumentError as exc:
        return fail(f"文档未能落库：{exc.message}")
    except Exception as exc:  # noqa: BLE001 — 错因进日志，对外给可操作文案
        logger.exception("[render_html] 文档落库失败")
        return fail("文档落库失败（服务端日志有详情），请稍后重试或改用其它交付形态")

    _record_channel(ctx, channel="html", doc_id=saved["doc_id"], status="success",
                    notes=notes, started_at=started)
    return _text({
        "doc_id": saved["doc_id"],
        "body_kind": "html",
        "title": title,
        "theme": theme or "",
        "digest": saved["digest"],
        "size_chars": saved["size_chars"],
        "notes": notes,
        "next": "在回答正文输出一个 ```report 代码块引用它（严格 JSON，无多余文本）："
                '{"doc_id": "%s", "body_kind": "html", "title": "%s"}' % (saved["doc_id"], title),
        "note_detail": "正文不要在回答里重复粘贴，前端会按引用渲染整份文档。",
    })


# ── render_chart（M2）：图表片段的渲染机，数字由服务端取数而非模型转抄 ──────────

async def _fetch_by_intent(intent, ctx) -> tuple[dict, str]:
    """取数只复用语义层那一条执行口（护栏 §1：不开第二条数据通道）。

    数字由服务端从取数结果里拿，不经模型转抄：旧 ` ```chart ` 的 rows 是模型从结果表抄进
    围栏的，抄错一位就是一份对不上账的报告（而它自己看不出来）。

    走 `_execute_single`（run_semantic_query 单发/批量共用的那条函数：binding → plan →
    受控执行 → 治理与脱敏）而不是它的 LLM 信封：`_text` 用 `json.dumps(default=str)`，
    会把 DECIMAL 列序列化成 "12.50" 这种字符串 —— 拿它当事实源就会把正常的金额列误判成
    “数值写成字符串”。类型保真必须由服务端自己拿。

    直接调内部函数会绕过 `make_tool` 里的 `check_tool`，所以这里先重裁决取数工具本身
    的授权：只勾了 render 而没勾 semantic 的 AS-BOT 不能拿渲染机当后门取数。
    """
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.modules.mind.execution.session_workspace import run_owned_sync

    try:
        from backend.modules.mind.execution.tool_policy import check_tool
        await run_owned_sync(check_tool, ctx, SEMANTIC_TOOL)
    except PermissionError as exc:
        if "未授权该工具" in str(exc):
            return {}, ("当前 AS-BOT 未授权取数工具（run_semantic_query），渲染机不能代它取数"
                        "（同一口径的授权裁决，不是新增能力）。"
                        "请改用本轮已取到的数据以 columns+rows 交付，或由管理员补上取数组授权")
        return {}, f"取数授权未能确认：{exc}。请重新发送消息，或改用本轮已取到的数据以 columns+rows 交付"
    except Exception as exc:  # noqa: BLE001 — 确认不了授权就当不通过，不静默放行
        logger.warning("[render_chart] 取数授权重裁决失败: %s", exc)
        return {}, "取数授权状态无法确认，本次不代取数（不得静默放行）。请重试或改用 columns+rows 兜底"

    payload, is_error = await sq._execute_single(dict(intent or {}), ctx)
    if is_error or payload.get("error"):
        reason = str(payload.get("error") or "语义查询失败")
        extra = payload.get("reason") or payload.get("hint") or payload.get("retry_with") or ""
        if isinstance(extra, list):
            extra = "；".join(str(x) for x in extra[:3])
        return payload, (reason + (f"（{extra}）" if extra else ""))
    return payload, ""


def valid_chart_types() -> tuple:
    """平台可用图型（唯一真源：与前端 DashboardChart CHART_TYPES 对齐的图表契约清单）。"""
    from backend.modules.mind.execution.chart_contract import CHART_TYPES
    return tuple(t for t in CHART_TYPES if t not in _NOT_A_CHART)


def _infer_chart_type(facts: dict) -> str:
    """未指定图型时的确定性推断：只看数据形态事实，不看措辞，也不猜审美。"""
    if facts.get("lat_col") and facts.get("lng_col"):
        return "map_heat"
    if facts.get("time_col"):
        return "line"
    return "bar"


def resolve_chart_type(requested: str, facts: dict) -> tuple[str, str, list]:
    """定出实际图型，回 ``(applied, 错因, 非阻断提示)``；未知图型**拒收而不退柱状图**。

    旧链路里这一条判定在前端：`resolveChartType` 认不出的图型一律退成 `bar`（雷达图画成
    柱子、用户以为是数据错）。现在拿回到服务端，并把原因回给模型当场改选。
    """
    notes: list[str] = []
    want = str(requested or "").strip().lower()
    if not want:
        applied = _infer_chart_type(facts)
        notes.append(f"未指定 chart_type，按数据形态取 {applied}；"
                     "图型不合适可在图表卡的图型下拉直接切换")
        return applied, "", notes
    alias = _CHART_ALIASES.get(want)
    if alias:
        notes.append(f"图型 {want} 不是平台类型名，已按 {alias} 处理")
        want = alias
    if want in _NOT_A_CHART:
        return "", (f"chart_type={want} 不是图型（平台没有这个类型，历史契约把它静默退成柱状图）。"
                    "明细表请在正文里用 Markdown 表格，或指定一个真实图型"), notes
    if want not in valid_chart_types():
        return "", (f"未知图型 chart_type={want}：平台不支持，不会退成柱状图糊弄过去。"
                    f"可选：{', '.join(valid_chart_types())}"), notes
    return want, "", notes


async def render_chart(args):
    """交付一个图表片段：服务端取数 + 图型形态校验，回引用 id（数据不回正文）。"""
    started = time.time()
    ctx = _ctx()
    user_id, workspace_id, conversation_id = _identity(ctx)
    title = str(args.get("title") or "").strip()
    chart_id = str(args.get("chart_id") or "").strip()

    def fail(reason: str, blockers=None, notes=None):
        _record_channel(ctx, channel="chart", doc_id="", status="error",
                        blockers=blockers or [reason], notes=notes, started_at=started)
        payload = {"error": reason,
                   "how_to_fix": "按原因重新取数或换图型后重试；确实画不出的形态改用 Markdown 表格/文字结论"}
        if blockers:
            payload["blockers"] = list(blockers)
        if notes:
            payload["notes"] = list(notes)
        return _text(payload, is_error=True)

    if not title:
        return fail("缺少图表标题 title")
    if user_id <= 0:
        return fail("无可信身份，不能生成交付对象")

    from backend.modules.viz.services import report_document_service as docs

    # G1 单轨的反向约束：已有整份文档主交付物时不再散铺图表片段
    # （否则同一结论两份形态并存、数字互相对不上）
    primary = str(_channel_state(ctx).get("primary") or "")
    if primary in PRIMARY_CHANNELS:
        label = "整页文档（render_html）" if primary == "html" else "结构化报告"
        return fail(f"本轮主交付物已是{label}：一次回答只出一个主体。"
                    "图位请在那份文档里呈现，不要再散铺图表卡片")

    intent = args.get("intent")
    if isinstance(intent, str):
        raw_intent = intent.strip()
        intent = None
        if raw_intent:
            try:
                intent = json.loads(raw_intent)
            except (ValueError, TypeError):
                return fail("intent 不是合法 JSON：需与 run_semantic_query 的 intent_json 同形"
                            "（对象/指标/维度/过滤/时间窗口）")
    columns = list(args.get("columns") or [])
    rows = list(args.get("rows") or [])
    sql = str(args.get("sql") or "").strip()
    notes: list[str] = []
    row_count = 0

    if isinstance(intent, dict) and intent:
        payload, err = await _fetch_by_intent(intent, ctx)
        if err:
            return fail(f"intent 取数未完成，不能出图：{err}")
        columns = list(payload.get("columns") or [])
        rows = list(payload.get("rows") or [])
        # row_count 取服务端事实（与取数对账相等），不拿 len(rows) 反推（截断时会少报）
        row_count = int(payload.get("row_count") or len(rows))
        if payload.get("truncated") or len(rows) < row_count:
            notes.append(f"结果已被截断展示：取数 {row_count} 行，图表只用前 {len(rows)} 行；"
                         "不得把图上的行当作全量值做求和/计数")
        if sql:
            notes.append("intent 取数的 SQL 由语义层治理改写且不对外下发，图表卡不展示 SQL 页签")
        sql = ""
    elif columns and rows:
        row_count = len(rows)
        notes.append("本图数据由对话侧提供（未经服务端取数对账）："
                     "数字必须与本轮已展示的查询结果一致，不得凭记忆补数")
        if sql:
            hits = docs.find_sensitive_hits(sql)
            if hits:
                return fail("sql 字段含有不应下发的系统信息（命中: " + ", ".join(hits) + "）："
                            "请去掉 sql 字段，或只写业务口径说明", ["sql_internal_identifiers"], notes)
    else:
        return fail("render_chart 需要 intent（首选：声明式语义意图，由服务端经语义层取数，"
                    "数字不经转抄）或 columns + rows（数据不落在单一 intent 时的兜底）。"
                    "不接受裸 SQL 取数：一切返回数据行的执行都必须经语义层（护栏 §1）")

    if len(rows) > _MAX_CHART_ROWS:
        return fail(f"图表数据 {len(rows)} 行超过上限 {_MAX_CHART_ROWS}：图表不是明细导出，"
                    "请改为聚合（Top N / 分组汇总），或用 artifact 交数据文件", None, notes)

    # 先归一类型再校验：校验对象必须是前端将要拿到的那份形状（Decimal 会被写成字符串）
    columns, rows = docs.normalize_chart_rows(columns, rows)
    facts = docs.chart_column_facts(columns, rows)
    applied, type_error, infer_notes = resolve_chart_type(str(args.get("chart_type") or ""), facts)
    notes += infer_notes
    if type_error:
        return fail(type_error, ["unknown_chart_type"], notes)
    blockers, shape_notes, facts = docs.validate_chart_shape(columns, rows, applied)
    notes += shape_notes
    if blockers:
        return fail("图表形态与图型不匹配（不会退成柱状图糊弄过去），请按原因重新取数或换图型：\n- "
                    + "\n- ".join(blockers), blockers, notes)

    # G2 事实登记：本轮出过地图/富图型 → render_html 的警告档从这里吃真事实，不靠猜
    record_rich_chart_facts(ctx, [applied])

    theme = str((getattr(ctx, "extra", None) or {}).get("report_theme") or "").strip()
    body = json.dumps({"chart_type": applied, "title": title, "columns": columns,
                       "rows": rows, "row_count": row_count, "sql": sql},
                      ensure_ascii=False, default=str)
    try:
        saved = docs.save_document(
            conversation_id=conversation_id, user_id=user_id, workspace_id=workspace_id,
            kind="chart", body_kind="chart-data", title=title, body=body, theme=theme or None,
            message_uuid=_message_uuid(), doc_id=chart_id or None, warnings=notes,
            row_counts={"row_count": row_count, "returned": len(rows)})
    except docs.RenderDocumentError as exc:
        return fail(f"图表数据未能落库：{exc.message}", None, notes)
    except Exception:  # noqa: BLE001 — 错因进日志，对外给可操作文案
        logger.exception("[render_chart] 图表数据落库失败")
        return fail("图表数据落库失败（服务端日志有详情），请稍后重试或改用 Markdown 表格交付",
                    None, notes)

    _record_channel(ctx, channel="chart", doc_id=saved["doc_id"], status="success",
                    notes=notes, started_at=started)
    return _text({
        "chart_id": saved["doc_id"],
        "chart_type_applied": applied,
        "title": title,
        "columns": columns,
        "row_count": row_count,
        "returned_rows": len(rows),
        "digest": saved["digest"],
        "notes": notes,
        "next": "在回答正文输出一个 ```chart 代码块引用它（严格 JSON、无多余文本）："
                '{"chart_ref": "%s", "title": "%s"}' % (saved["doc_id"], title),
        "note_detail": "不要再把 columns/rows 抄进正文：前端按 chart_ref 取回同一份数据渲染，"
                       "图表/明细/SQL 三视图与图上下钻都保留。",
    })


# ── render_report（M3）：结构化章节报告（spec）───────────────────────

def _spec_structure_errors(sections) -> tuple[list, list]:
    """只靠入参形状就能判的问题一次列全（不逐节报错白耗轮次）；回 ``(blockers, kinds)``。"""
    from backend.modules.viz.services import report_document_service as docs

    blockers: list[str] = []
    prepared: list[dict] = []
    for idx, raw in enumerate(sections):
        tag = f"第 {idx + 1} 章"
        if not isinstance(raw, dict):
            blockers.append(f"{tag}不是对象：章节形状为 {{title, kind, narrative?, intent?, chart_type?, code?}}")
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        title = str(raw.get("title") or "").strip()
        if not title:
            blockers.append(f"{tag}缺少 title（章节标题是版式校验与快照锚点，不得省）")
        if kind not in docs.SPEC_SECTION_KINDS:
            blockers.append(f"{tag}的 kind={kind or '空'} 不是可用章节形态，"
                            f"只能取 {'/'.join(docs.SPEC_SECTION_KINDS)}")
            continue
        section = {"title": title, "kind": kind}
        narrative = str(raw.get("narrative") or "").strip()
        if narrative:
            section["narrative"] = narrative
        needs_data = kind in ("kpi", "chart", "table")
        if needs_data:
            intent = raw.get("intent")
            if isinstance(intent, str):
                try:
                    intent = json.loads(intent or "{}")
                except (ValueError, TypeError):
                    blockers.append(f"{tag}的 intent 不是合法 JSON（需与 run_semantic_query 的 intent_json 同形）")
                    intent = None
            if not isinstance(intent, dict) or not intent:
                blockers.append(f"{tag}（{kind}）缺 intent：章节取数只接受声明式语义意图，"
                                "不接受 rows/sql（数据不落在单一 intent 时请拆成多个 intent 或改文本章节）")
                intent = None
            else:
                for banned in ("rows", "columns", "sql", "data"):
                    if banned in intent:
                        blockers.append(f"{tag}的 intent 里带了 {banned}：章节取数只写声明式意图，"
                                        "数据由服务端经语义层取")
                        intent = None
                        break
            if intent:
                section["intent"] = intent
        if kind == "chart":
            chart_type = str(raw.get("chart_type") or "").strip().lower()
            if chart_type and chart_type not in valid_chart_types():
                alias = _CHART_ALIASES.get(chart_type)
                if alias:
                    chart_type = alias
                else:
                    blockers.append(f"{tag}的图型 {chart_type} 平台不支持（不退成柱状图）："
                                    f"可选：{', '.join(valid_chart_types())}")
                    continue
            section["chart_type"] = chart_type
        if kind == "mermaid":
            code = str(raw.get("code") or "").strip()
            if not code:
                blockers.append(f"{tag}（mermaid）缺 code：流程/脑图要给出 mermaid 源码")
            else:
                section["code"] = code
        if kind == "text" and not narrative:
            blockers.append(f"{tag}（text）缺 narrative：文本章节要写成文结论")
        prepared.append(section)
    return blockers, prepared


async def render_report(args):
    """交付**结构化章节报告**（spec）：逐章 intent 取数，前端用平台组件按章节渲染。"""
    started = time.time()
    ctx = _ctx()
    user_id, workspace_id, conversation_id = _identity(ctx)
    title = str(args.get("title") or "").strip()
    doc_id = str(args.get("doc_id") or "").strip()

    def fail(reason: str, blockers=None, notes=None):
        _record_channel(ctx, channel="spec", doc_id="", status="error",
                        blockers=blockers or [reason], notes=notes, started_at=started)
        payload = {"error": reason,
                   "how_to_fix": "按原因修正章节定义后重试；只需整页文档时改用 render_html"}
        if blockers:
            payload["blockers"] = list(blockers)
        if notes:
            payload["notes"] = list(notes)
        return _text(payload, is_error=True)

    from backend.modules.viz.services import report_document_service as docs

    if not title:
        return fail("缺少报告标题 title")
    if user_id <= 0:
        return fail("无可信身份，不能生成交付文档")
    raw_sections = args.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        return fail("sections 必须是非空数组：每项 {title, kind: kpi|chart|table|text|mermaid, "
                    "narrative?, intent?, chart_type?, code?}")
    if len(raw_sections) > docs._MAX_SPEC_SECTIONS:
        return fail(f"章节数 {len(raw_sections)} 超过上限 {docs._MAX_SPEC_SECTIONS}："
                    "一份报告讲不完就拆主题，不要把章节当分页")

    blockers, sections = _spec_structure_errors(raw_sections)
    if blockers:
        return fail("章节定义不合格，本次未生成报告（不拿半份结构入库）：\n- "
                    + "\n- ".join(blockers[:8]), blockers[:8], [])

    theme = str(args.get("theme") or "").strip()
    template_name = str(args.get("template") or "").strip()
    template_key = ""
    if template_name:
        from backend.modules.viz.services.report_service import resolve_report_template_by_name
        tpl = resolve_report_template_by_name(template_name)
        if not tpl.get("found"):
            available = [str(n) for n in (tpl.get("available") or [])][:8]
            reason = str(tpl.get("reason") or f"报告模板「{template_name}」不可用")
            return fail(reason + (f"。可用模板：{'、'.join(available)}（按业务名选，不得猜 id）"
                                  if available else "。当前没有可用模板，请不传 template 自由排版"),
                        ["template_unavailable"], [])
        template_key = str(tpl.get("template_key") or "")
        theme = theme or str(tpl.get("theme") or "")
        sections_meta = {"template_name": template_name,
                         "layout": tpl.get("layout") or {}}
    else:
        sections_meta = {}
    theme = theme or str((getattr(ctx, "extra", None) or {}).get("report_theme") or "").strip()

    # 逐章取数：走与 run_semantic_query 同一条执行口（护栏 §1），数字不经模型转抄
    notes: list[str] = []
    row_counts: list[dict] = []
    failed: list[str] = []
    shape_blockers: list[str] = []
    for idx, section in enumerate(sections):
        kind = section["kind"]
        count = 0
        if section.get("intent"):
            payload, err = await _fetch_by_intent(section["intent"], ctx)
            if err:
                # 取数失败不沿用上一版数字，也不静默删掉这一章（计划 §3 分支 F）
                section["error"] = err
                failed.append(f"{idx + 1}. {section['title']}")
                row_counts.append({"index": idx + 1, "title": section["title"], "row_count": 0,
                                   "error": True})
                continue
            columns, rows = docs.normalize_chart_rows(list(payload.get("columns") or []),
                                                      list(payload.get("rows") or []))
            count = int(payload.get("row_count") or len(rows))
            if len(rows) > _MAX_CHART_ROWS:
                rows = rows[:_MAX_CHART_ROWS]
            if payload.get("truncated") or len(rows) < count:
                notes.append(f"第 {idx + 1} 章结果被截断：取数 {count} 行，图上用前 {len(rows)} 行")
            section["data"] = {"columns": columns, "rows": rows, "row_count": count}
        if kind == "chart":
            facts = docs.chart_column_facts(
                list((section.get("data") or {}).get("columns") or []),
                list((section.get("data") or {}).get("rows") or []))
            applied, type_error, infer_notes = resolve_chart_type(section.get("chart_type") or "", facts)
            if type_error:
                shape_blockers.append(f"第 {idx + 1} 章：{type_error}")
                continue
            if infer_notes:
                notes.append(f"第 {idx + 1} 章：{infer_notes[0]}")
            section["chart_type"] = applied
            bad, shape_notes, _ = docs.validate_chart_shape(
                list((section.get("data") or {}).get("columns") or []),
                list((section.get("data") or {}).get("rows") or []), applied)
            notes += [f"第 {idx + 1} 章：{n}" for n in shape_notes]
            if bad:
                shape_blockers += [f"第 {idx + 1} 章：{b}" for b in bad]
        record_rich_chart_facts(ctx, [section.get("chart_type")] if section.get("chart_type") else [])
        row_counts.append({"index": idx + 1, "title": section["title"], "row_count": count})

    if shape_blockers:
        return fail("章节形态与图型不匹配（不退错图、也不拿半份报告入库）：\n- "
                    + "\n- ".join(shape_blockers[:8]), shape_blockers[:8], notes)
    if failed:
        notes.append("以下章节本轮取数未成功，已写成「暂无数据 + 原因」而不是沿用历史数字："
                     + "、".join(failed))

    blocked = _claim_primary(ctx, "spec")
    if blocked:
        return fail(blocked, None, notes)

    body = json.dumps({"title": title, "theme": theme, "template_key": template_key,
                       **sections_meta, "sections": sections},
                      ensure_ascii=False, default=str)
    try:
        saved = docs.save_document(
            conversation_id=conversation_id, user_id=user_id, workspace_id=workspace_id,
            kind="doc", body_kind="spec", title=title, body=body, theme=theme or None,
            template_key=template_key or None, message_uuid=_message_uuid(),
            doc_id=doc_id or None, warnings=notes, row_counts=row_counts)
    except docs.RenderDocumentError as exc:
        return fail(f"报告未能落库：{exc.message}", None, notes)
    except Exception:  # noqa: BLE001
        logger.exception("[render_report] 报告落库失败")
        return fail("报告落库失败（服务端日志有详情），请稍后重试或改用 render_html", None, notes)

    _record_channel(ctx, channel="spec", doc_id=saved["doc_id"], status="success",
                    notes=notes, started_at=started)
    return _text({
        "doc_id": saved["doc_id"],
        "body_kind": "spec",
        "title": title,
        "sections_count": len(sections),
        "row_counts": row_counts,
        "failed_sections": failed,
        "theme": theme,
        "template": template_name,
        "digest": saved["digest"],
        "size_chars": saved["size_chars"],
        "notes": notes,
        "next": "在回答正文输出一个 ```report 代码块引用它（严格 JSON、无多余文本）："
                '{"doc_id": "%s", "body_kind": "spec", "title": "%s"}' % (saved["doc_id"], title),
        "note_detail": "章节正文不要重复粘贴到回答里：前端按引用逐章用平台组件渲染"
                       "（图表可下钻、主题随查看者换装）。",
    })


TOOL_SPECS = [
    {
        "name": "render_chart",
        "description": (
            "交付**图表片段**的渲染机：服务端经语义层取数（或收下你提供的 columns+rows 兜底），"
            "校验图型与数据形态是否匹配后落成图表数据对象，返回引用 id。"
            "适用：本轮结论主要是一两个数据形状（趋势/占比/对比/分布），叙述只需几句话；或用户点名要图。"
            "不适用：需要成文叙述 + 多个图表一起阅读的整体报告（用 render_html，一轮只保留一个主交付物）。"
            "数字由服务端取数得到：带 intent 时 row_count 与取数同源，**不要自己抄 rows 进块**。"
            "图型只能取平台支持的类型（未知图型会被拒收，不会静默退成柱状图）；形态不匹配也会被拒"
            "（雷达图缺多指标、地图缺地理列、时序图缺时间轴、数值列写成字符串）。"
            "收到错误请读原因后重新取数或换图型，不得声称「图已生成」（回执里没有 chart_id 就是没生成）。"
            "成功后在回答里输出 ```chart 引用块（严格 JSON）：chart_ref = 返回的 chart_id，不要再重复粘贴数据。"
        ),
        "schema": {
            "title": Annotated[str, "图表标题（业务表述，展示在卡片头）"],
            "intent": Annotated[Optional[dict],
                                "首选：声明式语义意图，与 run_semantic_query 的 intent_json 同形"
                                "（object/metrics/dimensions/filters/time_*）；由服务端自取数据"],
            "chart_type": Annotated[Optional[str],
                                    "图型（平台支持的类型之一）；留空则按数据形态推断，推断结果在回执里写明"],
            "columns": Annotated[Optional[list],
                                 "兜底：列名数组（数据不落在单一 intent 时用，如多次取数合并后的结果）"],
            "rows": Annotated[Optional[list],
                              "兜底：数据行（对象数组或数组的数组；数值不加引号）"],
            "sql": Annotated[Optional[str],
                             "兜底可选：生成该数据的查询语句（图表卡 SQL 页签展示）；不得含连接信息/物理标识"],
            "chart_id": Annotated[Optional[str], "修订同一张图时传回原 chart_id；新建则留空"],
        },
        "handler": render_chart,
    },
    {
        "name": "render_html",
        "description": (
            "交付**整体报告**用的渲染机之一：把一份自包含 HTML 文档落成受管交付对象，返回引用 id（不返回正文）。"
            "适用：本轮结论需要成文叙述 + 多个图/表一起阅读（分析广度和文字量都明显超出单张图表）；"
            "或用户点名要「整页 / 可直接转发 / 自定义排版」的报告。"
            "不适用：只有一个数据形状、结论两三句 —— 那用 ```chart 卡片更合适。"
            "选本通道前先自问：本轮结论里有没有地理分布或富图型（地图/雷达/桑基/树图/日历热力/时序动画）"
            "是**核心结论**？有就改用 render_report —— 它们由平台真渲染，而 HTML 单文件只能退成表格。"
            "走本通道时该退让的四项代价必须写进报告元信息：图型退表格 / 主题是生成时快照 / "
            "文档内数字来自本轮查询抄录 / 图表不可下钻；不得当作与平台渲染等价。"
            "硬约束：单文件自包含（禁外链/CDN/远程字体图片）、配色只能引用系统注入的「报告主题令牌」、"
            "不得自绘伪地图（画不出真底图就改 Top N 表格）；正文含物理表/连接信息/治理改写 SQL 会被拒。"
            "一次回答只允许一个主交付物：本工具与 render_report 互斥，可重复调用同一 doc_id 修订。"
            "成功后在回答里输出 ```report 引用块，不要重复粘贴正文。"
        ),
        "schema": {
            "title": Annotated[str, "报告标题（业务表述，展示在文档卡头）"],
            "html": Annotated[str, "整份自包含 HTML 文档正文（含内联 <style> 与主题令牌块）"],
            "theme": Annotated[Optional[str], "采用的主题 id；留空=继承用户当前主题"],
            "doc_id": Annotated[Optional[str], "修订已有报告时传回原 doc_id；新建则留空"],
        },
        "handler": render_html,
    },
    {
        "name": "render_report",
        "description": (
            "交付**结构化章节报告**的渲染机：你只交章节定义，数据由服务端逐章经语义层取，"
            "产出受管文档对象并返回引用 id。前端用平台组件逐章渲染 —— 地图与雷达/桑基/树图/"
            "日历热力/时序动画都是真渲染，主题随查看者即时换装，图表保留下钻与三视图。"
            "适用：多个发现 + 成文叙述 + 多个图表一起阅读，且结论里有地理/富图型（那是 HTML 单文件拿不到的）。"
            "章节 kind 只能取 kpi / chart / table / text / mermaid；每个数据章节必须带 intent"
            "（与 run_semantic_query 的 intent_json 同形），**不接受 rows/sql/data 字段**；"
            "chart 章节还要靠形态匹配（缺地理列/缺时间轴/缺多指标会被拒收并回原因）。"
            "某章取数失败时该章写「暂无数据 + 原因」，不得沿用上一版数字；其余章节照常交付。"
            "可选 template：报告模板的**业务名**（不可用会回可用清单，不得猜 id）。"
            "与 render_html 互斥（一轮只一个主交付物），可带原 doc_id 修订同一份。"
            "成功后在回答里输出 ```report 引用块（body_kind=spec），不要把章节正文再贴一遍。"
        ),
        "schema": {
            "title": Annotated[str, "报告标题（业务表述，展示在文档卡头）"],
            "sections": Annotated[list,
                                  "章节数组，每项 {title, kind: kpi|chart|table|text|mermaid, "
                                  "narrative?, intent?, chart_type?, code?}"],
            "theme": Annotated[Optional[str], "主题 id；留空=继承会话生效主题或模板锁定主题"],
            "template": Annotated[Optional[str], "报告模板的**业务名**（不是 id）；不可用会回可用清单"],
            "doc_id": Annotated[Optional[str], "修订已有报告时传回原 doc_id；新建则留空"],
        },
        "handler": render_report,
    },
]


def build_render_server(backend: str = "qoder", tool_names=None):
    """构建 render 进程内 MCP server（qoder / claude）。tool_names 给定时按 AS-BOT 逐工具粒度注册。"""
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = list(TOOL_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    # 渲染机都会落文档对象，不属只读工具（不给 readOnlyHint，避免被当成可随意并发调用）
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"], annotations=None)
        for s in specs
    ]
    return make_server(backend, "datahub_render", tools)
