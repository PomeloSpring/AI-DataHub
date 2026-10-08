"""semantic_query 工具组 — `run_semantic_query` (Phase 3 · 决策 A)。

LLM 侧只允许产出**声明式意图**(SemanticQuery)：{object, metrics[], dimensions[],
filters[], order[], limit, time_grain}；本工具在进程内直调语义层
(`backend.semantics.*`) 完成 intent -> binding -> plan -> 执行。

Phase 4 之前，实际执行仍复用 `execute_query_with_permission`(带权限 + 审计) 走
 secured_sql；Phase 4 会切至 DataFusion sidecar 通道，本工具的对外契约不变。

约定：任何 SQL 字段(sql/raw_sql/statement/…) 都会在 intent 层直接拒绝，
      从而强制 LLM 用"对象 + 指标 + 维度 + 过滤"表达查询意图。
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

_MAX_ROWS = 200

# 执行阶段失败时向 LLM 回输的通用文案:不暴露主机/账号/IP/SQL/密码等基础设施细节。
_EXEC_FAIL_HINT = (
    "取数在执行阶段失败（通常是数据源连接/凭据或物理表暂不可用）。这是系统侧问题，"
    "与你的查询意图无关。请勿猜测或向用户展示数据源主机、账号、IP、生成的 SQL 等细节，"
    "建议稍后重试或联系管理员核实数据源可用性。"
)

# 可能泄露物理表/数据源/catalog 的告警关键字，不随工具结果回输 LLM。
_LEAK_KEYWORDS = ("physical_table", "catalog", "datasource", "catalog_ref", "not in adh_table_info")


def _safe_warnings(*groups) -> list[str]:
    """过滤掉会泄露物理表/数据源/catalog 细节的告警。"""
    out: list[str] = []
    for ws in groups:
        for w in (ws or []):
            lw = str(w).lower()
            if any(k in lw for k in _LEAK_KEYWORDS):
                continue
            if w and w not in out:
                out.append(w)
    return out


def _sanitize_execute_error(se) -> str:
    """执行阶段原始报错(可含 MySQL Access denied/IP)仅进服务端日志,对 LLM 回通用文案。"""
    reason = getattr(se, "reason", "") or ""
    if getattr(se, "blocked_at", "") == "execute" or reason.startswith("执行失败"):
        logger.error("[run_semantic_query] execute-stage failed (detail server-side only): %s", reason)
        return _EXEC_FAIL_HINT
    return reason


async def _execute_single(raw_payload: dict, ctx) -> tuple[dict, bool]:
    """执行单个 intent, 返回 (结果 payload, is_error). 由单发/批量入口共用.

    datasource_id 对 LLM 是黑盒: 一律以执行上下文(会话选定的数据源)为权威,
    仅当上下文缺失时才回落到显式入参(供非 chat 的程序化调用路径)。
    """
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

    payload = dict(raw_payload or {})

    # 数据源黑盒: datasource_id 以执行上下文(会话选定数据源)为权威,
    # 只有上下文没有时才回落到显式入参。否则 LLM 瞎猜的 datasource_id(如 1)
    # 会覆盖真实 id, 导致 resolve_binding 只扫 datasource_id IN(1,0) 全落空 ->
    # 误报 "object 'case' 未绑定到任何物理表"。
    if ctx:
        payload["datasource_id"] = int(ctx.datasource_id or 0)
        payload["workspace_id"] = int(ctx.workspace_id or 0)
        payload["user_id"] = int(ctx.user_id or 0)

    # fail-loud: 业务会话未确定数据源时明确报错, 不下探 resolve_binding(datasource_id=0)
    # 把"未选源"误报成"对象未绑定"(系统域 ds=0 是合法系统域, 不拦)。
    from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
    if not int(payload.get("datasource_id") or 0) and ctx is not None and not system_scope(ctx):
        return ({"error": "当前会话未确定数据源，无法执行语义查询；请先在会话中选择一个已授权数据源。"}, True)

    q, err, notes = parse_intent(payload)
    if err:
        # 关键护栏：LLM 只允许声明式意图；SQL 旁路在这里被拒
        return ({
            "error": err,
            "notes": notes,
            "hint": "只允许 {object, metrics[], dimensions[], filters[], order[], limit}；禁止传任何 SQL 字段。",
        }, True)

    binding, bind_warnings = await asyncio.to_thread(
        resolve_binding, q.object, datasource_id=q.datasource_id,
    )
    if binding is None:
        return ({
            "error": f"object '{q.object}' 未绑定到任何物理表",
            "warnings": bind_warnings,
            "hint": "先用 knowledge_search 或 select_tables 找一个已在本体里 bound 的对象。",
        }, True)

    from backend.modules.mind.execution.resource_guard import validate_binding
    try:
        await asyncio.to_thread(validate_binding, ctx, binding)
    except PermissionError as exc:
        return ({"error": str(exc)}, True)

    p = await asyncio.to_thread(plan, q, binding)

    # 别名回流队列(自进化闭环入口): 被拒近似词/未解析词自动落入待审, best-effort 永不影响取数。
    _terms = (p.provenance or {}).get("unresolved_terms")
    if _terms:
        try:
            from backend.modules.mind.rag.alias_suggestion import record_unresolved_terms
            await asyncio.to_thread(record_unresolved_terms, q.datasource_id, _terms)
        except Exception as e:  # noqa: BLE001
            logger.debug("[run_semantic_query] alias suggestion record skipped: %s", e)

    # 护栏拒绝：planner 已经产出了空 SQL + warning。数据源对 LLM 是黑盒,
    # 这里只回声明式原因,不回 catalog_ref / provenance / 生成的 SQL。
    if not p.sql:
        return ({
            "error": "语义层护栏拒绝了本次查询",
            "reason": _safe_warnings(p.warnings) or ["查询被安全护栏拦截,请调整意图后重试"],
            "guardrail": {
                "query_mode": p.guardrail.query_mode,
                "size_class": p.guardrail.size_class,
                "allow_full_scan": bool(p.guardrail.allow_full_scan),
            },
        }, True)

    # Phase 4：统一走七闸门链(identity->permission->preflight->proposal->approval->execute->audit)。
    # RLS sqlglot 改写(语义层唯一可见改写)与审计都在 gates 内完成。
    from backend.semantics.gates import execute_semantic

    user_context = {
        "user_id": (ctx.user_id if ctx else None),
        "username": (ctx.username if ctx else None),
        "workspace_id": ((ctx.workspace_id if ctx else 0) or 0),
    }
    se = await asyncio.to_thread(execute_semantic, q, binding, p, user_context, "")

    rows_result = se.result or {
        "columns": [], "rows": [], "row_count": 0, "execution_ms": None,
    }
    if isinstance(rows_result, dict) and rows_result.get("row_count", 0) > _MAX_ROWS:
        rows_result = {**rows_result, "truncated": True,
                       "rows": rows_result.get("rows", [])[:_MAX_ROWS]}

    # 数据源对 LLM 是黑盒：只回声明式结果 + 安全告警,
    # 剥离 base_sql / secured_sql / datasource_id / catalog_ref / provenance / 原始报错。
    # intent 回显中剔除系统注入的基础设施标识(datasource/workspace/user id)。
    intent_echo = {k: v for k, v in q.model_dump().items()
                   if k not in ("datasource_id", "workspace_id", "user_id")}
    payload_out = {
        "object": binding.object_key,
        "intent": intent_echo,
        "query_mode": binding.guardrail.query_mode,
        "applied_rls": se.applied_rls,
        "masked_columns": se.masked_columns,
        "warnings": _safe_warnings(p.warnings, bind_warnings),
        **{k: rows_result[k] for k in
           ("columns", "rows", "row_count", "execution_ms", "truncated") if k in rows_result},
    }

    if not se.allowed:
        payload_out["error"] = _sanitize_execute_error(se)
        payload_out["blocked_at"] = se.blocked_at
        payload_out["needs_approval"] = se.needs_approval
        if se.proposed_edit:
            payload_out["proposed_edit"] = se.proposed_edit
        return payload_out, True
    return payload_out, False


_MAX_BATCH = 8


async def run_semantic_query(args):
    """LLM 面向语义层的主入口：intent -> binding -> plan -> (secured) SQL -> rows。

    支持两种入参(事二选一):
      intent_json:  单个 SemanticQuery JSON 字符串
      intents_json: JSON 数组(多个 intent 一次串行执行), 多维下钻/对比场景用,
                    返回 {results:[{intent摘要, ...各条结果}]}, 减少工具调用轮次。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    batch_raw = args.get("intents_json")
    batch_items: list | None = None
    if batch_raw and str(batch_raw).strip():
        try:
            items = json.loads(batch_raw) if isinstance(batch_raw, str) else batch_raw
        except (json.JSONDecodeError, TypeError):
            items = None
        if isinstance(items, dict):
            items = [items]  # LLM 误把单对象塞进 intents_json → 归一为 1 元素批, 不再报错白耗一轮推理
        if isinstance(items, list) and items:
            if len(items) > _MAX_BATCH:
                return _text({"error": f"批量上限 {_MAX_BATCH} 条, 当前 {len(items)} 条, 请拆分"}, is_error=True)
            batch_items = items
        # intents_json 解析失败/空但携了合法 intent_json 时, 不硬报错, 落到下方单 intent 路径(避免因参数形式耗额外一轮)
    if batch_items is not None:
        results: list[dict] = []
        err_count = 0
        for idx, it in enumerate(batch_items):
            if not isinstance(it, dict):
                results.append({"index": idx, "error": "批量元素必须是 intent 对象"})
                err_count += 1
                continue
            res, is_err = await _execute_single(it, ctx)
            results.append({"index": idx, **res})
            if is_err:
                err_count += 1
        return _text(
            {"results": results, "total": len(results), "failed": err_count,
             "all_ok": err_count == 0},
            is_error=err_count == len(results),
        )

    raw = args.get("intent_json") or "{}"
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except json.JSONDecodeError as e:
        return _text({"error": f"intent_json 不是合法 JSON: {e}"}, is_error=True)
    res, is_err = await _execute_single(payload, ctx)
    return _text(res, is_error=is_err)


TOOL_SPECS = [
    {
        "name": "run_semantic_query",
        "description": (
            "Execute a **declarative semantic query** against the ontology-bound datasource. "
            "You provide an Intent (JSON), the semantic layer resolves binding + guardrails + RLS "
            "and returns rows plus provenance. NEVER pass SQL — that field is rejected at parse time. "
            "Schema of intent_json: "
            '{"object": "<object_key or IRI>", '
            '"metrics": ["<measure_name>", ...], '
            '"dimensions": ["<dim_name>", ...], '
            '"filters": [{"dim": "...", "op": "eq|ne|gt|gte|lt|lte|in|like", "value": ...}], '
            '"order": [{"by": "...", "desc": false}], '
            '"limit": 100, "time_grain": "day|week|month|null", "dry_run": false, '
            '"time_window": "7d", "time_range": "last_week", "time_column": "<dim_name>", "params": {"<tpl_var>": ...}}. '
            "Extract all three elements (metrics / dimensions / time) from the question; NEVER drop a "
            "time expression. Relative rolling windows (e.g. last 7 days) MUST use time_window "
            "(Ns/Nm/Nh/Nd/Nw/NM like '7d','24h','2w','1M'); calendar ranges (e.g. last week / this month / "
            "last quarter) MUST use time_range (today|yesterday|this_week|last_week|this_month|last_month|"
            "this_quarter|last_quarter|this_year|last_year); never hand-computed absolute dates; "
            "time_column picks the event-time dimension (defaults to the bound time dimension). "
            "If the object is bound to a SQL template (bind_kind=sql_template), pass declared "
            "variables via params — run knowledge_search first to see the template's variables. "
            "Use knowledge_search first to discover legal object/metric/dimension names. "
            "Prefer this over execute_sql for agent-mode queries."
        ),
        "schema": {
            "intent_json": Annotated[Optional[str], "SemanticQuery JSON string (see description for shape); single query"],
            "intents_json": Annotated[Optional[str], 'JSON ARRAY of intent objects for one-shot batch execution (max 8), e.g. \'[{"object":"case","dimensions":["医院"]},{"object":"case","dimensions":["下单渠道"]}]\'. PREFER this over calling the tool repeatedly when drilling down by multiple dimensions; returns {results:[...]} aligned with input order.'],
        },
        "handler": run_semantic_query,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_semantic_query_server(backend: str = "qoder"):
    """构建 semantic_query 进程内 MCP server(qoder / claude)."""
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in TOOL_SPECS
    ]
    return make_server(backend, "datahub_semantic_query", tools)
