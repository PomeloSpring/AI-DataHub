"""semantic_query 工具组 — `run_semantic_query` (Phase 3 · 决策 A)。

LLM 侧只允许产出**声明式意图**(SemanticQuery)：{object, metrics[], dimensions[],
filters[], order[], limit, time_grain}；本工具在进程内直调语义层
(`backend.semantics.*`) 完成 intent -> binding -> plan -> 执行。

Phase 6 之前，实际执行仍复用 `execute_query_with_permission`(带权限 + 审计) 走
secured_sql；Phase 6.2 将切至 `backend.semantics.execution`（datafusion-python 内嵌引擎），
本工具的对外契约不变。对外失败文案/泄露过滤口径唯一真源在 `backend.semantics.contract`。

约定：任何 SQL 字段(sql/raw_sql/statement/…) 都会在 intent 层直接拒绝，
      从而强制 LLM 用"对象 + 指标 + 维度 + 过滤"表达查询意图。
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text, record_execute_error
from backend.semantics.contract import (
    safe_warnings as _safe_warnings,
    sanitize_execute_reason as _sanitize_execute_reason,
)

logger = logging.getLogger(__name__)

_MAX_ROWS = 200


def _sanitize_execute_error(se) -> str:
    """执行阶段原始报错(可含 MySQL Access denied/IP)仅进服务端日志,对 LLM 回通用文案。

    口径委托 `semantics.contract.sanitize_execute_reason`（唯一真源）；
    兼容 SemanticExecution（.reason）与 SemanticError（.message）。
    """
    reason = getattr(se, "reason", "") or getattr(se, "message", "") or ""
    return _sanitize_execute_reason(reason, getattr(se, "blocked_at", "") or "")


async def _execute_single(raw_payload: dict, ctx) -> tuple[dict, bool]:
    """执行单个 intent, 返回 (结果 payload, is_error). 由单发/批量入口共用.

    datasource_id 对 LLM 是黑盒: 一律以执行上下文(会话选定的数据源)为权威,
    仅当上下文缺失时才回落到显式入参(供非 chat 的程序化调用路径)。
    """
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

    payload = dict(raw_payload or {})

    # 数据源寻址(v0.5): `datasource`(业务名) → 角色授权集内解析(waker-datasource-domain §2,
    # 复用 query_tools._resolve_datasource_arg / resource_guard._resolve_authorized_source);
    # 未传 → 会话生效源(权威)。禁止消费 LLM 传的数值 datasource_id(黑盒): 瞎猜的 id
    # 会让 resolve_binding 扫错范围, 误报 "object 'case' 未绑定到任何物理表"。
    # 服务端内部调用(eval/playground)直传 id 不受限。多站点=LLM 逐站编排(各带 datasource 名)。
    if (payload.get("datasource") or "").strip():
        from backend.modules.mind.execution.sdk_tools.query_tools import _resolve_datasource_arg
        try:
            payload["datasource_id"] = int(_resolve_datasource_arg(payload, ctx) or 0)
        except (ValueError, PermissionError) as exc:
            return ({"error": str(exc),
                     "hint": "datasource 传 list_datasources 显示的业务名; 不确定就问用户, 不要猜。"}, True)
    elif ctx:
        payload["datasource_id"] = int(ctx.datasource_id or 0)
    if ctx:
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

    # v0.5: 服务端跨站展开已退役——全球/多站点由 LLM 按站点编排多次单站查询
    # (各带 datasource 名, 见 GLOBAL_SEMANTIC_LAYER_DESIGN.md §7/§8);
    # 站点维度已在 parse_intent 拒收, 勿再引入服务端展开/合并(防旧机制回潮)。

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
        # 传入本次调用生效源 q.datasource_id（跨站编排时=按 datasource 名在授权集内解析出的源），
        # 使"会话绑在 A 站、单次查询指向 B 站"的逐站编排不被误拒（GLOBAL_SEMANTIC_LAYER_DESIGN §7/§8）。
        await asyncio.to_thread(validate_binding, ctx, binding, int(q.datasource_id or 0))
    except PermissionError as exc:
        return ({"error": str(exc)}, True)

    p = await asyncio.to_thread(plan, q, binding)

    # 进化候选入口（自进化闭环）：被拒近似词/未解析词自动落入待审队列。
    # 写入失败不得影响取数主链（FDE §4 旁路约束），但也不能静默：按 warning 带
    # 可诊断信息落日志，且落库失败会让“信号没收到”在服务端可查（不假成功）。
    _terms = (p.provenance or {}).get("unresolved_terms")
    if _terms:
        try:
            from backend.modules.catalog.services.evolution_service import record_parse_fail_terms
            await asyncio.to_thread(record_parse_fail_terms,
                                    int(q.datasource_id or 0), _terms, q.object)
        except Exception as e:  # noqa: BLE001 — 旁路不阻断，但必须留可诊断痕迹
            logger.warning("[run_semantic_query] 未解析词未能落入进化候选队列（取数不受影响，"
                           "但本轮自进化信号已丢）: %s: %s", type(e).__name__, e)

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

    # Phase 6.4：执行段统一经 semantics.execute.execute_plan（护栏 §1 统一执行口）；
    # 上方 bind/plan 间的 L2 职责（资源护栏/别名回流）保持不变。
    from backend.semantics.contract import SemanticError
    from backend.semantics.execute import PolicyContext, execute_plan

    policy_ctx = PolicyContext(
        user_id=int((ctx.user_id if ctx else 0) or 0),
        username=(ctx.username if ctx else "") or "",
        workspace_id=int(((ctx.workspace_id if ctx else 0) or 0)),
        datasource_id=int(q.datasource_id or 0),
    )

    # intent 回显中剔除系统注入的基础设施标识(datasource/workspace/user id)。
    intent_echo = {k: v for k, v in q.model_dump().items()
                   if k not in ("datasource_id", "workspace_id", "user_id")}

    try:
        result = await asyncio.to_thread(execute_plan, q, binding, p, policy_ctx, "")
    except SemanticError as e:
        # 执行阶段原始引擎错因写进管理端 trace(对外仍只回脱敏文案), 供区分
        # 编译缺陷/连接故障/权限拦截 —— 否则日志滚后无法回溯本轮问答。
        record_execute_error("run_semantic_query", e)
        payload_out = {
            "object": binding.object_key,
            "intent": intent_echo,
            "query_mode": binding.guardrail.query_mode,
            "applied_rls": list((e.detail or {}).get("applied_rls") or []),
            "masked_columns": list((e.detail or {}).get("masked_columns") or []),
            "warnings": _safe_warnings(p.warnings, bind_warnings),
            "columns": [], "rows": [], "row_count": 0, "execution_ms": None,
            "error": _sanitize_execute_error(e),
            "blocked_at": e.blocked_at,
            "needs_approval": bool((e.detail or {}).get("needs_approval")),
        }
        if (e.detail or {}).get("proposed_edit"):
            payload_out["proposed_edit"] = e.detail["proposed_edit"]
        return payload_out, True

    names = [c.name for c in result.columns]
    rows_records = [dict(zip(names, row)) for row in result.rows]  # LLM 契约为 records
    rows_result = {
        "columns": names,
        "rows": rows_records,
        "row_count": int(result.row_count),
        "execution_ms": result.elapsed_ms,
    }
    if int(result.row_count) > _MAX_ROWS:
        rows_result = {**rows_result, "truncated": True, "rows": rows_records[:_MAX_ROWS]}

    # 数据源对 LLM 是黑盒：只回声明式结果 + 安全告警,
    # 剥离 base_sql / secured_sql / datasource_id / catalog_ref / provenance / 原始报错。
    payload_out = {
        "object": binding.object_key,
        "intent": intent_echo,
        "query_mode": binding.guardrail.query_mode,
        "applied_rls": result.applied_rls,
        "masked_columns": result.masked_columns,
        "warnings": _safe_warnings(p.warnings, bind_warnings),
        **{k: rows_result[k] for k in
           ("columns", "rows", "row_count", "execution_ms", "truncated") if k in rows_result},
    }
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
            '"having": [{"dim": "<resolved_metric_name>", "op": "gt|gte|lt|lte|eq", "value": ...}], '
            '"computed_metrics": [{"name": "<derived_name>", "expr": "{<m1>} / {<m2>}", "operands": ["<m1>", "<m2>"]}], '
            '"nest": {"inner": {"dimensions": [...], "metrics": [...], "filters": [...]}, '
            '"outer": {"metrics": [{"name": "...", "agg": "SUM|COUNT|AVG|MIN|MAX|COUNT_DISTINCT", "of": "<inner_col|*>"}], '
            '"dimensions": [...], "filters": [...], "having": [...]}}, '
            '"order": [{"by": "...", "desc": false}], '
            '"limit": 100, "time_grain": "day|week|month|null", "datasource": "<source business name>", "dry_run": false, '
            '"time_window": "7d", "time_range": "last_week", "time_column": "<dim_name>", "params": {"<tpl_var>": ...}}. '
            "For multi-site/global questions: query each site separately by calling this tool once per site with "
            "\"datasource\" (business name as shown by list_datasources; site↔source mapping lives in the "
            "data-analysis skill) — sites are NOT a dimension and \"站点\" in dimensions is rejected. "
            "Extract all three elements (metrics / dimensions / time) from the question; NEVER drop a "
            "time expression. Relative rolling windows (e.g. last 7 days) MUST use time_window "
            "(Ns/Nm/Nh/Nd/Nw/NM like '7d','24h','2w','1M'); calendar ranges (e.g. last week / this month / "
            "last quarter) MUST use time_range (today|yesterday|this_week|last_week|this_month|last_month|"
            "this_quarter|last_quarter|this_year|last_year); never hand-computed absolute dates; "
            "time_column picks the event-time dimension (defaults to the bound time dimension). "
            "Cross-object references use qualified business names '<object_name>.<metric|dim_name>' "
            "(join paths are resolved from ontology links server-side; NEVER provide join/SQL). "
            "Structured window: {\"type\": \"rank|rolling|period_compare|share\", \"metric\": \"<measure_name>\"} "
            "compiles window functions server-side (free-form window SQL is not accepted). "
            "having filters AFTER aggregation; its dim MUST be an already-resolved metric name "
            "(never a raw physical column) and composes with GROUP BY. "
            "computed_metrics derive a NEW measure by CONTROLLED arithmetic over resolved metrics only: "
            "expr may contain {operand} placeholders, numbers, parentheses and + - * / (denominators are "
            "auto-guarded with NULLIF); ANY bare column/table/function name or SQL fragment is REJECTED. "
            "Conditional aggregates (e.g. completion-rate) belong in the metric dictionary formula, not here. "
            "nest is a fixed TWO-STAGE aggregate: inner groups by dimensions+metrics, outer re-aggregates "
            "over the inner output columns (of references an inner column name or '*'). nest takes over the "
            "projection: leave top-level metrics/dimensions/having/computed_metrics/window empty when using it. "
            "For multi-site/global questions: call this tool once per site with \"datasource\" "
            "(business name shown by list_datasources) and merge results yourself; sites are NOT a "
            "dimension — '站点' in dimensions is rejected with guidance. "
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
