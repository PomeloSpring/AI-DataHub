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
    safe_governance_notes as _safe_governance_notes,
    sanitize_execute_reason as _sanitize_execute_reason,
)

logger = logging.getLogger(__name__)

_MAX_ROWS = 200


def _needs_realtime(q) -> bool:
    """本次提问是否要“今天/实时”口径（T+1 与滞后路径不参与）。

    保守判定：只有明确落在当天/小时级窗口时才算。判不出来时返回 False（宁多不拒），
    因为“排除汇总层”只是少一条可选路径，而错判不会造成错数。
    """
    tw = str(getattr(q, "time_window", "") or "").strip().lower()
    if tw.endswith("h"):
        return True
    if tw.endswith("d"):
        try:
            return int(tw[:-1]) <= 2
        except ValueError:
            return False
    blob = " ".join([
        str(getattr(q, "time_range", "") or ""),
        str(getattr(q, "time_start", "") or ""),
    ])
    return any(w in blob for w in ("今天", "今日", "实时", "当前"))


def _attach_degraded_hint(payload_out: dict, decision: dict) -> None:
    """全局/日志类取数路径执行失败时，必须显式标降级边界（拍板决策 6）。

    “有权限但执行失败”与“无权限被裁剪”不同：前者是**降级**，退成逐站后必须让用户
    看见“全局口径不可用”这个事实，不得拿各站数字相加冒充全局值静默交差；
    后者是作用域裁剪，对用户不提（⑥ 叙事规则）。本函数只动非 detail 的路径。
    """
    ctype = str(((decision or {}).get("carrier") or {}).get("type") or "")
    if not ctype or ctype == "detail":
        return
    cname = str(((decision or {}).get("carrier") or {}).get("name")
                or ((decision or {}).get("carrier") or {}).get("key") or "")
    payload_out["degraded"] = True
    payload_out["degraded_reason"] = (
        f"全局口径取数路径「{cname}」本次执行失败，该口径暂不可用")
    payload_out["degraded_hint"] = (
        "如改按站点分别取数，必须向用户明说“这是降级结果：各站分别统计，相加不等于去重后的全局值”；"
        "不得拿各站数字相加冒充全局口径静默交差，也不得归为“无权限”（本例是有权限但执行失败）。")
    logger.warning("[run_semantic_query] 取数路径 %s(%s) 执行失败，如需改逐站取数必须向用户标注降级",
                   str(((decision or {}).get("carrier") or {}).get("key") or ""), ctype)


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

    # 数据源寻址(v0.6): `site`(站点业务名/代码) > `datasource`(源业务名) > 会话生效源。
    # 两者都由服务端在**角色授权集**内解析(waker-datasource-domain §2, 复用
    # query_tools._resolve_datasource_arg / site_service.resolve_site_to_source)。
    # 禁止消费 LLM 传的数值 datasource_id(黑盒): 瞎猜的 id 会让 resolve_binding 扫错
    # 范围, 误报 "object 'case' 未绑定到任何物理表"。服务端内部调用(eval/playground)
    # 直传 id 不受限。多站点 = LLM 逐站编排(各带 site)。
    site_arg = str(payload.get("site") or "").strip()
    ds_arg = str(payload.get("datasource") or "").strip()
    if ctx is not None and (site_arg or ds_arg):
        from backend.modules.mind.execution.sdk_tools.query_tools import _resolve_datasource_arg
        try:
            payload["datasource_id"] = int(_resolve_datasource_arg(payload, ctx) or 0)
        except (ValueError, PermissionError) as exc:
            return ({"error": str(exc),
                     "hint": "site 传「数据源拓扑」段里的站点业务名(如 '中国'/'日本'), "
                             "datasource 传 list_datasources 显示的业务名; 不确定就问用户, 不要猜。"}, True)
    elif ctx:
        payload["datasource_id"] = int(ctx.datasource_id or 0)
    if ctx:
        payload["workspace_id"] = int(ctx.workspace_id or 0)
        payload["user_id"] = int(ctx.user_id or 0)

    # 会话站点兼容：未点名站点/源且会话未锁定单一源（如中国站点挂了 mysql+SLS 多源）
    # 时，按会话站点再试一次（站点内唯一源才能定下来，多义不猜）。
    if not int(payload.get("datasource_id") or 0) and ctx is not None:
        extra = getattr(ctx, "extra", None) or {}
        session_site = str(extra.get("site_code") or "").strip()
        available = extra.get("available_datasource_ids") or []
        if session_site and available:
            from backend.core.site_service import session_source_for_site
            payload["datasource_id"] = int(session_source_for_site(session_site, available) or 0)

    # fail-loud: 业务会话未确定数据源时明确报错, 不下探 resolve_binding(datasource_id=0)
    # 把"未选源"误报成"对象未绑定"(系统域 ds=0 是合法系统域, 不拦)。
    from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
    if not int(payload.get("datasource_id") or 0) and ctx is not None and not system_scope(ctx):
        session_site = str((getattr(ctx, "extra", None) or {}).get("site_code") or "").strip()
        return ({"error": "当前会话未确定数据源，无法执行语义查询；"
                          + (f"会话站点「{session_site}」下有多个已授权数据源或为聚合态，"
                             f"请对本次查询带上 site 参数（可查站点见「数据源拓扑」段）。"
                             if session_site else "请先在会话中选择一个已授权数据源。")}, True)

    q, err, notes = parse_intent(payload)
    if err:
        # 关键护栏：LLM 只允许声明式意图；SQL 旁路在这里被拒
        return ({
            "error": err,
            "notes": notes,
            "hint": "只允许 {object, metrics[], dimensions[], filters[], order[], limit}；禁止传任何 SQL 字段。",
        }, True)

    # v0.5: 服务端跨站展开已退役——全球/多站点由 LLM 按站点编排多次单站查询
    # (各带 site 或 datasource 名, 见 GLOBAL_SEMANTIC_LAYER_DESIGN.md §7/§8);
    # 站点维度已在 parse_intent 拒收, 勿再引入服务端展开/合并(防旧机制回潮)。

    # 取数路径（承载）择路：单一实现在 `semantics.topology.decide_carrier`。
    # 未声明承载时 status=none，行为与改造前逐字节一致（渐进增强不阻塞）。
    # 只对“非 detail 的全局/日志/镜像路径”接管解析；detail 仍走现有站点展开。
    from backend.semantics import topology
    from backend.semantics.binding_resolver import resolve_binding_via_carrier

    if q.carrier and not topology.routing_enabled():
        # 灰度关闭时目录已不暴露 carriers，模型本不该再传 carrier；真传了不能静默忽略，
        # 否则“选了路径但实际走的另一条”会以正确答案的面目返回给用户（禁止降级掩盖）。
        return ({"error": "取数路径（全局汇总/日志源）选路当前未启用，本次不能按指定路径取数",
                 "hint": "请去掉 intent 里的 carrier 参数后重试（退为逐站分开呈现），"
                         "不要自行猜测哪条路径能给出全局口径；需要开启该能力请联系管理员。"}, True)

    decision = await asyncio.to_thread(
        topology.decide_carrier, q.object,
        carrier=q.carrier, metrics=q.metrics or [],
        user_id=int(getattr(q, "user_id", 0) or 0),
        workspace_id=int(getattr(q, "workspace_id", 0) or 0),
        need_realtime=_needs_realtime(q))
    dstatus = str(decision.get("status") or "none")
    if decision.get("carriers_filtered"):
        # 被权限裁剪的条数只进服务端日志，不向用户解释“为什么没有全局口径”（拍板）
        logger.info("[run_semantic_query] 对象 %s 有 %d 条取数路径因授权不可见（user=%s）",
                    q.object, decision["carriers_filtered"], getattr(q, "user_id", 0))
    if dstatus == "unavailable":
        return ({"error": decision.get("reason") or "该取数路径当前不可用",
                 "available_paths": decision.get("candidates") or [],
                 "hint": "不要猜路径标识，也不得拿另一条路径的结果冒充本口径；"
                         "可用路径已在 available_paths 里，或如实告知用户该口径暂不可得。"}, True)
    if dstatus == "ambiguous":
        return ({"needs_clarification": True,
                 "paths": decision.get("candidates") or [],
                 "reason": decision.get("note") or "",
                 "hint": "请用 ask_user 让用户选口径（把 paths 里的 name 逐个列出），"
                         "选定后在 intent 里带 carrier=<路径标识> 重新查询；不得自行挑一条。"}, True)

    binding = None
    bind_warnings: list = []
    if dstatus == "use":
        binding, bind_warnings = await asyncio.to_thread(
            resolve_binding_via_carrier, decision["carrier"],
            datasource_id=int(q.datasource_id or 0),
            reason=str(decision.get("path_reason") or ("declared" if q.carrier else "authority")))
        if binding is None and bind_warnings:
            # 承载声明了但解不到物理形状 = 建模缺失，fail-loud；
            # 不得静默退回业务契约行去查错库（那会拿业务库形状查数仓 → 错数）
            return ({"error": "声明的取数路径无法完成取数（目标准备未就绪）",
                     "reason": bind_warnings,
                     "hint": "请确认该数据源已有生效的源本体且对应对象已绑定物理表；"
                             "未完成前不要改用其他路径充数，可先如实告知用户。"}, True)
        if binding is None:
            bind_warnings = []   # 与已解析目标一致（无需重解）→ 回到常规路径
    if binding is None:
        binding, bind_warnings = await asyncio.to_thread(
            resolve_binding, q.object, datasource_id=q.datasource_id,
        )
    if binding is None:
        return ({
            "error": f"object '{q.object}' 未绑定到任何物理表",
            "warnings": bind_warnings,
            "hint": "先用 knowledge_search 或 select_tables 找一个已在本体里 bound 的对象。",
        }, True)

    # 择路溯源补齐：detail/none 两条常规解析路径也要落戳（单点实现在 topology，
    # 本模块不直接写 carrier 字段——下方静态护栏禁止这些字段出现在返体构造里）。
    topology.stamp_provenance(binding, decision, explicit=bool(q.carrier))

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
    # 治理串脱敏口径：物理列 -> 业务名（binding.column_map 反向）+ 物理表名 redact。
    # 不回修服务端值（审计/评测仍消原始策略串），只修对 LLM 下发的这一份。
    gov_kwargs = {
        "label_map": {v: k for k, v in (binding.column_map or {}).items()},
        "redact": [binding.physical_table, binding.dataset_ref],
    }

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
            **_safe_governance_notes(
                list((e.detail or {}).get("applied_rls") or []),
                list((e.detail or {}).get("masked_columns") or []),
                **gov_kwargs,
            ),
            "warnings": _safe_warnings(p.warnings, bind_warnings),
            "columns": [], "rows": [], "row_count": 0, "execution_ms": None,
            "error": _sanitize_execute_error(e),
            "blocked_at": e.blocked_at,
            "needs_approval": bool((e.detail or {}).get("needs_approval")),
        }
        if (e.detail or {}).get("proposed_edit"):
            payload_out["proposed_edit"] = e.detail["proposed_edit"]
        if getattr(binding, "as_of_note", ""):
            payload_out["caliber_note"] = binding.as_of_note
        _attach_degraded_hint(payload_out, decision)
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
    # 剥离 base_sql / secured_sql / datasource_id / catalog_ref / provenance / 原始报错，
    # 并把治理串里的物理列名换成业务名（护栏 §7）。
    payload_out = {
        "object": binding.object_key,
        "intent": intent_echo,
        "query_mode": binding.guardrail.query_mode,
        **_safe_governance_notes(result.applied_rls, result.masked_columns, **gov_kwargs),
        "warnings": _safe_warnings(p.warnings, bind_warnings),
        **{k: rows_result[k] for k in
           ("columns", "rows", "row_count", "execution_ms", "truncated") if k in rows_result},
    }
    # 口径时效提示（业务文案，如“T+1，不含今天”）：走了哪条取数路径必须让用户看得见，
    # 否则“汇总层数字”与“明细数字”混在一起无从判断。只给业务文案，不给 id/物理表。
    if getattr(binding, "as_of_note", ""):
        payload_out["caliber_note"] = binding.as_of_note
    if int(result.row_count) > _MAX_ROWS:
        payload_out["aggregation_warning"] = (
            "结果已被截断展示：不要对截断后的行做求和/计数并当作全量值，"
            "改为让语义层直接算（metrics + 聚合）后输出。")
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
            '"limit": 100, "time_grain": "day|week|month|null", "site": "<site business name or code, e.g. 中国/cn>", '
            '"datasource": "<source business name>", "carrier": "<data-path key from get_metrics carriers>", '
            '"dry_run": false, '
            '"time_window": "7d", "time_range": "last_week", "time_column": "<dim_name>", "params": {"<tpl_var>": ...}}. '
            "For multi-site/global questions: call this tool once per site with \"site\" "
            "(business name from the session '数据源拓扑' context; the server resolves it to that site's "
            "datasource within your authorized sources) and merge results yourself — sites are NOT a "
            "dimension and \"站点\" in dimensions is rejected. Never guess a site or source name. "
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
            "For multi-site/global questions: call this tool once per site with \"site\" "
            "(business name shown in the session '数据源拓扑' context) and present one row per site; sites are NOT a "
            "dimension — '站点' in dimensions is rejected with guidance. Do NOT sum per-site numbers into a "
            "'global' figure unless the object declares an authoritative global path — then call ONCE with \"carrier\". "
            "When an object lists several carriers (get_metrics → objects[].carriers) and the intended caliber is "
            "ambiguous, the tool returns needs_clarification with the candidate paths: ask the user via ask_user "
            "which caliber they want, then retry with carrier=<that key>. Never invent a carrier key and never "
            "silently substitute another path's result. "
            "If the object is bound to a governed dataset (bind_kind=dataset), pass its declared "
            "variables via params — run knowledge_search first to see the dataset's parameter list. "
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
