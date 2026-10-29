"""semantic 工具组 — 业务语义增强(get_metrics / get_glossary / query_by_tags / knowledge_search).

handler 为 SDK 无关的纯函数,由 build_semantic_server(backend) 包装。
"""

import asyncio
import json
import logging
import time
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

#: 多问法并发上限：每路检索会 spawn 一个 qmind CLI(node) 子进程，不封顶会在
#: 共享宿主上把内存打满；4 路已足以把“N 问串行”压成“~N/4 轮”。
_MAX_CONCURRENT_RETRIEVE = 4


# ── 工具 handler(SDK 无关) ─────────────────────────────────────

async def get_metrics(args):
    """对象级语义目录: 每个本体对象可用的指标/维度(含别名与枚举业务标签)。

    只暴露字典层信息(name/aliases/unit/枚举标签), 不回显 target_table 等物理细节。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.semantics.mdl_compiler import (
            _get_dimensions, _get_metrics, active_binding_tables, binding_scope_for, dict_unavailable_reason,
        )

        ds_id = (ctx.datasource_id if ctx else 0) or 0
        # fail-loud: 业务会话未确定数据源时不得回空目录假成功(系统域 ds=0 是合法系统域, 不拦)。
        # 但聚合态（全球）会话 ds=0 是合法口径：指标/维度字典全局共享（adh_metrics/dimensions
        # 均 datasource_id=0），按 ds=0 加载正是全球语义层的正确目录，不得回"请先选会话源"
        # 死胡同（历史缺陷：全球站点下连合法指标名都查不出，逐站编排无从开始）。
        from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
        if not ds_id and ctx is not None and not system_scope(ctx):
            from backend.core.site_service import is_aggregate_site
            if not is_aggregate_site((getattr(ctx, "extra", None) or {}).get("site_code") or ""):
                return _text({"error": "当前会话未确定数据源，无法加载语义目录；请先在会话中选择一个已授权数据源后重试。"},
                             is_error=True)
        keyword = (args.get("keyword") or "").strip().lower()
        metrics = await asyncio.to_thread(_get_metrics, ds_id)
        dims = await asyncio.to_thread(_get_dimensions, ds_id)
        # 目录层（bound_object_key）与编译层（target_table=物理表）必须同一口径：
        # 拿不到绑定表集合时只做空值裁决，不拿“查不到”当“不可用”
        try:
            all_tables, tables_by_object = await asyncio.to_thread(active_binding_tables)
        except Exception as bind_err:  # noqa: BLE001
            logger.warning("[get_metrics] 部署绑定表集合加载失败（仅做空值裁决）: %s", bind_err)
            all_tables, tables_by_object = set(), {}
        blocked: list[dict] = []

        def _match(row):
            if not keyword:
                return True
            hay = {str(row.get("name") or ""), str(row.get("name_en") or "")}
            hay |= {str(a) for a in (row.get("aliases") or [])}
            return any(keyword in h.lower() for h in hay if h)

        objects: dict[str, dict] = {}

        def bucket(key: str) -> dict:
            return objects.setdefault(key or "(未绑定对象)",
                                      {"object": key or "", "metrics": [], "dimensions": []})

        for m in metrics:
            if not _match(m):
                continue
            reason = dict_unavailable_reason(
                m, binding_scope_for(m, all_tables, tables_by_object), kind="metric")
            if reason:
                blocked.append({"name": m["name"], "kind": "metric", "reason": reason})
                continue
            row = {"name": m["name"], "aliases": m.get("aliases") or [],
                   "unit": m.get("unit") or "", "description": m.get("description") or ""}
            bucket(m.get("bound_object_key")).setdefault("metrics", []).append(row)
        for d in dims:
            if not _match(d):
                continue
            reason = dict_unavailable_reason(
                d, binding_scope_for(d, all_tables, tables_by_object), kind="dimension")
            if reason:
                blocked.append({"name": d["name"], "kind": "dimension", "reason": reason})
                continue
            bucket(d.get("bound_object_key")).setdefault("dimensions", []).append({
                "name": d["name"], "aliases": d.get("aliases") or [],
                "is_time": (d.get("category") or "") == "时间",
                "enum": d.get("value_labels") or {},
                "description": d.get("description") or "",
            })
        # 排掉 keyword 未命中的空桶
        items = [v for v in objects.values() if v["metrics"] or v["dimensions"]]
        # C2: 对象关系上下文（Oxigraph/SPARQL）——让 LLM 看到对象间真实关系；
        # 物理 join 表达式不外泄（护栏 §7）；图不可用显式降级标注，不静默。
        relations: list[dict] = []
        relations_note = ""
        obj_keys = [it["object"] for it in items if it.get("object")]
        if obj_keys:
            try:
                from backend.semantics.graph_rag.link_graph import object_links
                relations = [
                    {k: v for k, v in r.items() if k != "join"}
                    for r in await asyncio.to_thread(object_links, obj_keys)
                ]
            except Exception as rel_err:  # noqa: BLE001
                relations_note = f"关系上下文不可用(知识图谱): {rel_err}"
        # 部署清单(v0.6): 对象 × 站点部署事实(事实源=模型级 deploy_sites)——LLM 逐站编排知识;
        # 非独立调用(get_metrics 顺风车字段, 职责三分见 GLOBAL_SEMANTIC_LAYER_DESIGN.md §9)。
        deployments: list[dict] = []
        try:
            from backend.semantics.cross_site import list_deployments
            deployments = await asyncio.to_thread(list_deployments)
        except Exception as dep_err:  # noqa: BLE001
            relations_note = relations_note or f"部署清单不可用: {dep_err}"
        # 取数路径（carrier）已下线：同一概念的跨源取数统一由 LLM 按站点逐站编排
        # （见下方 deployments + usage）；跨源去重合计口径不在目录层提供，需由知识库文档定义。
        out = {"objects": items, "total": len(items), "relations": relations,
               "deployments": deployments,
               "usage": "dimensions/metrics 直接复制此处 name 或其 aliases; 按天/月趋势选 is_time=true 的维度并配 time_grain; "
                        "enum 字段列出码值→业务名, filter 可传业务名也可传码值, 结果自动显示业务名; "
                        "relations 列出对象间关联(跨对象字段用 '对象名.业务名' 限定引用); "
                        "**全球/多站点问题：按 deployments 对每个可查站点分别调用 run_semantic_query（site 参数）逐站查询，"
                        "站点不是维度；会话默认站点与可查站点清单见上下文「数据源拓扑」段**; "
                        "跨站去重合计口径不在取数目录提供：默认分站呈现、不得把各站数字相加当合计值；"
                        "如需一个合计数字，其口径需由知识库文档定义后向用户说明；"
                        "若报错'无法解析', 从返回的可用清单改名重试。"}
        if relations_note:
            out["relations_degraded"] = relations_note
        if blocked:
            # 显式告知：这些口径已登记但今天编不出数，不得向用户承诺可查（也不得转成
            # “换个指标试试”的瞎猜）；需要时告知用户该口径待建模修复
            out["unavailable"] = blocked
            out["unavailable_note"] = (
                "unavailable 内的指标/维度在字典中已登记但当前无法取数（原因见 reason）："
                "不得当作可查口径向用户承诺，也不得自行替换成别的指标作答；"
                "用户问到时应说明该口径尚未完成物理绑定/待修复。")
        return _text(out)
    except Exception as e:
        logger.error("get_metrics error: %s", e)
        return _text({"error": str(e)}, is_error=True)


def _split_multi(value) -> list[str]:
    """把入参归一为多个关键词/问题: 支持 JSON 数组、逗号/换行分隔字符串。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(v).strip() for v in arr if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in s.replace("\n", ",").split(",") if p.strip()]


def _dedupe(items: list[str]) -> list[str]:
    """保留先后次序的去重（LLM 常把同一个词/问法写两遍，不拦就是白烧一次远程查询）。"""
    seen: set = set()
    out: list[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


async def get_glossary(args):
    """业务术语查询; 支持 keywords 一次传多个(逗号分隔/JSON数组), 合并去重返回。

    多关键词走 **单条合并查询**（TermService.search_terms：一次建连 + 一条 SQL，
    词间 OR）。之前的逐词 list_terms 已实测不占优（N 词 = N 次建连 + 2N 条 SQL，
    并发只把等待换成线程/连接开销），而单次往返直接把这部分从耗时里拿掉。
    未命中词显式列在 unmatched_keywords，不让模型拿“空结果”当“再试一次”的理由。
    """
    try:
        from backend.modules.catalog.services.term_service import TermService

        words = _dedupe(_split_multi(args.get("keywords")) or _split_multi(args.get("keyword")))
        if not words:
            result = await asyncio.to_thread(TermService.list_terms, 1, 50, "")
            return _text(result)
        words = words[:10]
        result = await asyncio.to_thread(TermService.search_terms, words)
        items = result.get("items") or []
        seen: set = set()
        merged: list = []
        for item in items:
            key = item.get("id") or item.get("term_cn")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        matched = sorted({w for it in merged for w in (it.get("matched_keywords") or [])})
        return _text({"items": merged, "total": len(merged),
                      "queried_keywords": words,
                      "matched_keywords": matched,
                      "unmatched_keywords": [w for w in words if w not in matched],
                      "usage": "多术语已在一次调用内全部查完, 无需逐个关键词重复调用；"
                               "unmatched_keywords 里的词术语表确实没登记, 换 knowledge_search 或直接告知用户。"})
    except Exception as e:
        logger.error("get_glossary error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def query_by_tags(args):
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.catalog.services import tags_service
        params = json.loads(args.get("tag_conditions") or "{}")
        conditions = params.get("conditions", [])
        if not conditions:
            return _text({"error": "At least one tag condition required"}, is_error=True)
        # 本体资源域隔离: 授权数据源集由服务端守卫注入 ctx.extra, 不进 LLM 视野、不可篡改。
        # None(未过守卫/程序化直调) → 不裸查全库; 空集 → service 层 fail-closed 不返回。
        ds_ids = (getattr(ctx, "extra", None) or {}).get("tag_authorized_datasource_ids")
        if ds_ids is None:
            return _text({"error": "标签查询需在已授权数据源范围内执行；当前会话未确定可用数据源。"}, is_error=True)
        result = await asyncio.to_thread(
            tags_service.query_entities_by_tags,
            conditions,
            params.get("operator", "AND"),
            ctx.workspace_id,
            list(ds_ids),
        )
        return _text({"items": result, "total": len(result)})
    except json.JSONDecodeError:
        return _text({"error": "Invalid JSON in tag_conditions"}, is_error=True)
    except Exception as e:
        logger.error("query_by_tags error: %s", e)
        return _text({"error": str(e)}, is_error=True)


def _classify_retrieval_source(rag_source: str, count: int) -> str:
    """把 qmind_retrieve 的 rag_source 归一为 eval 可分桶的来源标签。

    qmind_hit=云端知识库命中 / local_hybrid_fallback=回退本地 graphrag+BM25 / none=无命中。
    """
    rs = (rag_source or "").lower()
    if count and "qmind" in rs and "fallback" not in rs:
        return "qmind_hit"
    if count:
        return "local_hybrid_fallback"
    return "none"


def _attach_routes(result: dict) -> dict:
    """把命中对象 key 解析为业务本体路由并附到结果（canonical 确定性读取）。

    路由解析失败时 routes 置空并显式标注 route_resolution=failed ——
    routes=[]（真无路由）与 failed（解析降级）必须可区分（no-silent-degradation）。
    """
    try:
        from backend.modules.mind.rag.business_route import resolve_business_routes
        result["routes"] = resolve_business_routes(result.get("hit_object_keys") or [])
        result["route_resolution"] = "ok"
    except Exception as e:  # noqa: BLE001 — 路由是增强信息，不毁掉检索主结果
        logger.warning("[knowledge_search] 业务本体路由解析失败: %s", e)
        result["routes"] = []
        result["route_resolution"] = f"failed: {e}"
    return result


def _attach_paths(result: dict, keywords: list | None = None) -> dict:
    """把命中结果解析为行为路径口径（关键路径/漏斗/用户指定路径）并附到结果。

    路径不是 SQL 维度而是"有序步骤口径"：它告诉 LLM 该查哪个对象、能传哪些参数，
    避免把漏斗拆成 N 个独立步骤指标（无顺序/无窗口⇒转化率系统性偏高）。
    解析失败时 paths 置空并显式标注 path_resolution=failed（与 routes 同口径）。
    """
    try:
        from backend.modules.mind.rag.behavior_path import resolve_behavior_paths
        paths = resolve_behavior_paths(
            object_keys=result.get("hit_object_keys") or [], keywords=keywords or [])
        result["paths"] = paths
        result["path_resolution"] = "ok"
    except Exception as e:  # noqa: BLE001 — 路径是增强信息，不毁掉检索主结果，但必须可诊断
        logger.warning("[knowledge_search] 行为路径解析失败: %s", e)
        result["paths"] = []
        result["path_resolution"] = f"failed: {e}"
    return result


def _attach_graph_relations(result: dict) -> dict:
    """C3: 检索命中对象的 Oxigraph 关系展开（1 跳邻接）；图不可用显式降级标注。"""
    keys = result.get("hit_object_keys") or []
    if not keys:
        return result
    try:
        from backend.semantics.graph_rag.link_graph import object_links
        rels = [{k: v for k, v in r.items() if k != "join"} for r in object_links(keys)]
        return {**result, "graph_relations": rels, "graph_source": "oxigraph"}
    except Exception as e:  # noqa: BLE001 — 不静默：降级必须可诊断
        return {**result, "graph_relations": [],
                "graph_degraded": f"关系展开不可用(知识图谱): {e}"}


def _retrieve_and_enrich(question: str, ds_id: int, bound_kb_ids, system_scope_flag: bool) -> dict:
    """单问题：qmind 检索 + 增强链（业务本体路由 / 图关系 / 行为路径）整体**同步**执行。

    必须是同步函数并由调用方丢进线程：增强链里有 DB/图查询，留在事件循环上会
    ① 把并发检索退化成串行（attach 期间占住循环），② 阻塞流式消息解析。

    `retrieval_ms` 只计检索段（不含 attach / CLI→MCP 分派 / 工具治理开销），
    使 trace 能区分“知识库真慢”与“工具包装层慢”。
    """
    from backend.modules.mind.rag.qmind_retriever import qmind_retrieve

    t0 = time.perf_counter()
    result = qmind_retrieve(question, ds_id, bound_kb_ids, system_scope=system_scope_flag)
    retrieval_ms = int((time.perf_counter() - t0) * 1000)
    source = _classify_retrieval_source(result.get("rag_source"), result.get("count") or 0)
    return _attach_paths(
        _attach_graph_relations(_attach_routes(
            {**result, "retrieval_source": source, "retrieval_ms": retrieval_ms})),
        keywords=_split_multi(question))


async def knowledge_search(args):
    """知识库检索; 支持 questions 一次传多个问题, 批量**并发**检索减少调用轮次。

    附带检索来源归因(记录到 observability, 未开启即 no-op), 供 eval 按来源分桶统计。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.observability import record_span

    ctx = get_execution_context()
    try:
        # 仅消费服务端 AS-BOT 显式绑定；缺少上下文不能查询全库。
        bound_kb_ids = (ctx.extra or {}).get("bound_knowledge_base_ids")
        # 系统能力形态（能力叠加）：系统 KB 照常检索，无命中同样回退业务元数据并标注来源；
        # “系统运营问题不拿业务知识充数”由 prompt 引导 + 来源分桶承担，不再检索硬限定。
        from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
        system_scope_flag = system_scope(ctx)
        if not bound_kb_ids:
            return _text({"error": "当前 AS-BOT 未绑定知识库"}, is_error=True)
        from backend.modules.mind.execution.resource_guard import validate_knowledge_binding
        await asyncio.to_thread(validate_knowledge_binding, bound_kb_ids)
        # 会话已选源由服务端上下文提供；不把 datasource_id 暴露给 LLM——
        # 护栏 _check_source 本就拒绝非会话源，暴露可选参数只会诱导模型猜 id 而被反复拒。
        ds_id = (ctx.datasource_id if ctx else 0) or 0
        questions = _split_multi(args.get("questions")) or _split_multi(args.get("question"))
        questions = _dedupe(questions)[:8]
        if not questions:
            return _text({"error": "请传 question 或 questions(多个用逗号/JSON数组)"}, is_error=True)
        if len(questions) == 1:
            result = await asyncio.to_thread(
                _retrieve_and_enrich, questions[0], ds_id, bound_kb_ids, system_scope_flag)
            # 归因不写敏感 SQL/PII, 仅来源标签 + 耗时(护栏 §9)
            record_span(kind="retrieval", name="knowledge_search", status="success",
                        duration_ms=int(result.get("retrieval_ms") or 0),
                        input_text=questions[0][:200],
                        output_text=f"source={result.get('retrieval_source')} "
                                    f"count={result.get('count') or 0}")
            return _text(result)
        # 多问法**并发**检索（历史缺陷：for 循环串行，2 问=2 段检索时间直接叠加）。
        t0 = time.perf_counter()
        sem = asyncio.Semaphore(_MAX_CONCURRENT_RETRIEVE)

        async def _one(qtext: str) -> dict:
            async with sem:
                return await asyncio.to_thread(
                    _retrieve_and_enrich, qtext, ds_id, bound_kb_ids, system_scope_flag)

        fetched = await asyncio.gather(*(_one(q) for q in questions))
        wall_ms = int((time.perf_counter() - t0) * 1000)
        results = [{"question": qtext, **r} for qtext, r in zip(questions, fetched)]
        hit = sum(1 for x in results if x["retrieval_source"] == "qmind_hit")
        fallback = sum(1 for x in results if x["retrieval_source"] == "local_hybrid_fallback")
        record_span(kind="retrieval", name="knowledge_search", status="success",
                    duration_ms=wall_ms, input_text=f"{len(questions)} questions",
                    output_text=f"qmind_hit={hit} fallback={fallback}")
        return _text({"results": results, "total": len(results),
                      "attribution": {"qmind_hit": hit, "local_hybrid_fallback": fallback},
                      "usage": "多问题已在一次调用内并发检索, 无需逐个重复调用。"})
    except Exception:
        logger.exception("知识检索失败")
        return _text({"error": "知识检索未完成，请检查 AS-BOT 知识库绑定或联系管理员"}, is_error=True)


async def list_datasets(args):
    """列出当前用户可见的 BI 数据集(治理建模层)."""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.viz.services import dataset_service
        identity = {
            "user_id": (ctx.user_id if ctx else 0) or 0,
            "username": (ctx.username if ctx else "") or "",
            "role": (ctx.user_role if ctx else "") or "",
            "workspace_id": (ctx.workspace_id if ctx else 0) or 0,
        }
        items = await asyncio.to_thread(
            dataset_service.list_datasets, identity, args.get("keyword") or "")
        return _text({
            "datasets": [
                {"id": d["id"], "name": d["name"], "source_type": d["source_type"],
                 "object_key": d.get("object_key") or "", "description": d.get("description") or "",
                 "field_count": d.get("field_count"),
                 # 可用数据源范围只回业务名（护栏 §7）：多源数据集靠 site/datasource 逐查询定源
                 "datasource": d.get("datasource_name") or "",
                 "available_datasources": d.get("available_source_names") or [],
                 "usable_datasources": d.get("usable_now") or []}
                for d in items
            ],
            "total": len(items),
            "usage": ("用 query_dataset(name=...) 取数; 已内置行级范围/RLS/敏感脱敏, 与看板同口径。"
                      "available_datasources 多于 1 个源的数据集可跨源执行：用 site（站点业务名）或 "
                      "datasource（数据源业务名）指定本站；不传则由服务端按会话源/执行源定源。"),
        })
    except Exception as e:
        logger.error("list_datasets error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def query_dataset(args):
    """按数据集取数(治理建模层): 权限/RLS/scopes/审计服务端施加."""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.viz.services import dataset_service
        identity = {
            "user_id": (ctx.user_id if ctx else 0) or 0,
            "username": (ctx.username if ctx else "") or "",
            "role": (ctx.user_role if ctx else "") or "",
            "workspace_id": (ctx.workspace_id if ctx else 0) or 0,
        }
        ds = None
        ds_id = int(args.get("dataset_id") or 0)
        if ds_id:
            ds = await asyncio.to_thread(dataset_service.get_dataset, ds_id)
        elif args.get("name"):
            ds = await asyncio.to_thread(
                dataset_service.get_dataset_by_name, str(args["name"]).strip())
        if not ds:
            return _text({"error": "数据集不存在, 先用 list_datasets 查看可用数据集"},
                         is_error=True)
        params = {
            "dimensions": args.get("dimensions") or [],
            "measures": args.get("measures") or [],
            "filters": args.get("filters") or [],
            "order": args.get("order") or [],
            "limit": args.get("limit") or 200,
            "args": args.get("args") or args.get("variables") or {},
            # 选源只接业务名（站点名/源名），服务端在“可用范围 ∩ 角色授权集”内定源
            "site": args.get("site") or "",
            "datasource": args.get("datasource") or "",
            "preferred_datasource_id": int((ctx.datasource_id if ctx else 0) or 0),
        }
        result = await asyncio.to_thread(
            dataset_service.query_dataset, int(ds["id"]), params, identity)
        # 数据源黑盒: 剥离可能出现的 SQL/物理细节
        for k in ("sql", "base_sql", "secured_sql", "datasource_id", "catalog_ref"):
            result.pop(k, None)
        return _text(result)
    except Exception as e:
        logger.error("query_dataset error: %s", e)
        return _text({"error": str(e)}, is_error=True)


# 工具元信息(name / description / schema / annotations)
TOOL_SPECS = [
    {
        "name": "get_metrics",
        "description": (
            "List the semantic catalog grouped by ontology object: for each object the usable "
            "metrics (name/aliases/unit/calculation) and dimensions (name/aliases/is_time/enum "
            "value labels). ALWAYS copy metric/dimension names (or their aliases) from this "
            "catalog into run_semantic_query instead of inventing names; use is_time dimensions "
            "with time_grain for trends; enum maps stored codes to business labels "
            "(filters accept either). Optional keyword narrows by name/alias."
        ),
        "schema": {"keyword": Annotated[Optional[str], "Metric/dimension name keyword; empty means list all"]},
        "handler": get_metrics,
    },
    {
        "name": "get_glossary",
        "description": (
            "Search the business glossary for term definitions and field mappings "
            "(e.g. what '华东' or '活跃用户' means in this business). "
            "Pass ALL terms you need in ONE call via `keywords` (comma-separated or JSON array) "
            "instead of calling repeatedly — 多关键词已在服务端合成一次查询。 "
            "Use this to translate business terms before writing SQL filters. "
            "仅限业务黑话/枚举口径的翻译；入口地址、联系方式、组织分工这类事实问题不在术语表里，"
            "不要用本工具去补 knowledge_search 的未命中。"
        ),
        "schema": {
            "keyword": Annotated[Optional[str], "Single term keyword in Chinese or English; empty means list all"],
            "keywords": Annotated[Optional[str], "Multiple terms in ONE call, comma-separated or JSON array, e.g. '华东,活跃用户' — merged & deduped"],
        },
        "handler": get_glossary,
    },
    {
        "name": "query_by_tags",
        "description": (
            "Find data entities (tables etc.) by tag conditions, e.g. entities tagged '财务' AND '核心'. "
            "tag_conditions is a JSON string: {\"conditions\": [{\"tag_id\": 1}], \"operator\": \"AND\"}."
        ),
        "schema": {"tag_conditions": Annotated[str, 'JSON string, e.g. {"conditions": [{"tag_id": 1}], "operator": "AND"}']},
        "handler": query_by_tags,
    },
    {
        "name": "knowledge_search",
        "description": (
            "Retrieve authoritative business context for natural-language questions. "
            "The bound QMind knowledge base contains the LATEST ontology model docs "
            "(objects/metrics/dimensions/SQL-template variables) — query it FIRST: if it "
            "answers the question, proceed directly without extra discovery tools. "
            "Pass ALL questions in ONE call via `questions` (comma-separated or JSON array); "
            "多个问法在一次调用内**并发**检索，因此不要把候选问法拆成多次调用逐个试探——"
            "一次就把所有同义措辞（如“官网域名/中国站网址/访问入口”）全传进去。 "
            "When hit_object_keys is returned, prefer copying those keys verbatim as the "
            "`object` of run_semantic_query (they are the KB-recognized ontology objects; verify "
            "names still exist in get_metrics if doc_stale=true). "
            "Falls back to hybrid metadata retrieval (BM25 + graph) when no QMind hit. "
            "仅用于业务本体/口径建模上下文；系统运营/用量/活跃类问题（如“近N天有多少人用 chat”）"
            "请用 system_usage / system_overview，不要用本工具。"
        ),
        "schema": {
            "question": Annotated[Optional[str], "Single natural-language question, e.g. '上个月的销售额'"],
            "questions": Annotated[Optional[str], "Multiple questions in ONE call, comma-separated or JSON array (max 8) — results aligned by order"],
        },
        "handler": knowledge_search,
    },
    {
        "name": "list_datasets",
        "description": (
            "List published BI datasets visible to the current user (governed modeling layer on "
            "top of the ontology). Each dataset has a stable name, fields (dimensions/measures) "
            "and row-scope policy. Prefer querying a published dataset over ad-hoc "
            "run_semantic_query when one matches the user's question — same numbers as dashboards."
        ),
        "schema": {"keyword": Annotated[Optional[str], "Optional name/description keyword"]},
        "handler": list_datasets,
    },
    {
        "name": "query_dataset",
        "description": (
            "Fetch rows from a published BI dataset by name (or dataset_id). Server applies "
            "permissions/RLS/sensitive masking/row-scope/audit — identical governance as "
            "dashboards. dimensions/measures must be copied from the dataset field list "
            "(see list_datasets or dataset detail). filters: [{field, op(eq|ne|gt|gte|lt|lte|in|like), value}]. "
            "A dataset may declare multiple usable datasources (available_datasources): pick one per "
            "query with site (site business name) or datasource (datasource business name) — never an id; "
            "if it stays ambiguous the server returns the candidate business names instead of guessing."
        ),
        "schema": {
            "name": Annotated[Optional[str], "Exact dataset name from list_datasets"],
            "dataset_id": Annotated[Optional[int], "Alternative: numeric dataset id"],
            "site": Annotated[Optional[str], "Business site name to run against, e.g. '中国' / '国际' (multi-site datasets)"],
            "datasource": Annotated[Optional[str], "Business datasource name to run against, e.g. 'prd-cn' (takes precedence over site when both)"],
            "dimensions": Annotated[Optional[list], "Dimension field names to group by"],
            "measures": Annotated[Optional[list], "Measure field names to aggregate"],
            "filters": Annotated[Optional[list], "[{field, op, value}] conditions"],
            "order": Annotated[Optional[list], "[{by, desc}] ordering"],
            "limit": Annotated[Optional[int], "Max rows (default 200)"],
            "args": Annotated[Optional[dict], "Declared dataset variables {name: value} (from dataset variables)"],
        },
        "handler": query_dataset,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_semantic_server(backend: str = "qoder", tool_names=None):
    """构建 semantic 进程内 MCP server(qoder / claude).

    Phase 3 将 run_semantic_query 归入本组（与 retrieval 类工具同层），
    保证 agent 只需开启 “semantic” 即可拿到完整“发现对象 + 下意”工具集。
    tool_names 给定时只注册被选中的工具(AS-BOT 粒度逐个控制)。
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool
    from backend.modules.mind.execution.sdk_tools.semantic_query import (
        TOOL_SPECS as SEMANTIC_QUERY_SPECS,
    )

    specs = list(TOOL_SPECS) + list(SEMANTIC_QUERY_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_semantic", tools)
