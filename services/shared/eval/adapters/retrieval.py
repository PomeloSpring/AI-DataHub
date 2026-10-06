"""本体层检索适配器：跑真实检索链路，产出命中集合与**检索来源**分桶。

**这是补 pitfall 缺口的关键**：早期的 tests/eval/runner.py 完全不经过
``datamind/rag/strategies/graphrag.py`` 与 ``graph_rag/agentic_sparql.py``，
所以"eval 全绿"从未覆盖 GraphRAG/SPARQL 路径。本适配器显式走
``rag_retriever.retrieve_with_strategy``（策略总入口），``rag_source`` 即策略名，
分桶语义与 compile 适配器的"解析档位"完全不同，报告里分开呈现。

缓存旁路：``rag_retriever`` 有进程内 ``_RAG_CACHE``。评测必须清掉它——
热缓存会让检索结果与真实召回能力脱钩，造成假阳性。
"""

from __future__ import annotations

from services.shared.eval.contract import Case

#: 检索来源分桶的说明文案（报告呈现用，与 compile 的档位区分）
SOURCES_LABEL = "检索来源（retrieval strategy / rag_source，非解析档位）"


def _clear_rag_cache() -> None:
    """清空进程内检索缓存，确保评测测的是真实召回而非缓存命中。"""
    try:
        from services.datamind.rag import rag_retriever as rr
        cache = getattr(rr, "_RAG_CACHE", None)
        if cache is not None and hasattr(cache, "clear"):
            cache.clear()
    except Exception:  # noqa: BLE001 —— 清缓存失败不该阻断评测，但要能诊断
        import logging
        logging.getLogger("eval.retrieval").warning("[eval] 清理检索缓存失败，结果可能受缓存影响", exc_info=True)


def _flatten(result: dict) -> list[dict]:
    """把检索结果摊成扁平命中清单，便于 contains/forbidden/no_physical 断言。"""
    items: list[dict] = []

    def _add(kind, name, text):
        items.append({"kind": kind, "name": str(name or ""), "text": str(text or "")})

    for t in result.get("table_info") or []:
        _add("table", t.get("table_name"), t.get("table_comment") or t.get("table_business_desc"))
    for c in result.get("column_metadata") or []:
        _add("column", c.get("column_name"), c.get("column_comment") or c.get("business_desc"))
    for term in result.get("business_terms") or []:
        _add("term", term.get("term_cn") or term.get("name"), term.get("description") or term.get("term_aliases"))
    for tpl in result.get("sql_templates") or []:
        # 只取模板名/说明，**不取 SQL 文本**（SQL 属 §7 黑盒，不应进评测命中集）
        _add("template", tpl.get("template_name"), tpl.get("description"))
    for rel in result.get("table_relations") or []:
        _add("relation", rel.get("relation_type"), "")
    for ds in result.get("saved_datasets") or []:
        _add("dataset", ds.get("name"), ds.get("description"))
    return items


def run_case(case: Case) -> dict:
    """跑一条检索用例，返回 ``observed`` 供 contract.check_retrieval 比对。"""
    from services.datamind.rag.rag_retriever import retrieve_with_strategy

    payload = case.payload or {}
    observed: dict = {"items": [], "sources": {}, "raw_keys": []}

    _clear_rag_cache()   # 必须旁路缓存，否则测的是缓存不是召回

    result = retrieve_with_strategy(
        question=case.question or "",
        keywords=payload.get("keywords"),
        target_tables=payload.get("target_tables"),
        selected_tables=payload.get("selected_tables"),
        datasource_id=int(payload.get("datasource_id") or 0),
        strategy_name=payload.get("strategy") or None,
    )
    if not isinstance(result, dict):
        observed["error"] = f"检索返回了非对象结果: {type(result).__name__}"
        return observed

    observed["items"] = _flatten(result)
    observed["raw_keys"] = sorted(k for k in result.keys())
    
    # ── 业务本体路由观察（路由索引层）──
    # 云端 qmind 命中→hit_object_keys 在离线不可测；此处以 payload.hit_object_keys
    # 显式声明命中契约，评测确定性路由解析（canonical 读取）与分桶：
    # route_resolved=唯一目标源 / route_ambiguous=多源歧义 / route_none=无路由。
    observed["routes"] = []
    route_bucket = "route_none"
    hit_keys = payload.get("hit_object_keys")
    if hit_keys is not None:
        try:
            from services.datamind.rag.business_route import resolve_business_routes
            routes = resolve_business_routes([str(k) for k in (hit_keys or [])])
            observed["routes"] = routes
            src_names = {r.get("source_ontology") for r in routes if r.get("source_ontology")}
            if len(src_names) > 1:
                route_bucket = "route_ambiguous"
            elif routes:
                route_bucket = "route_resolved"
        except Exception as e:  # noqa: BLE001 — 路由解析失败显式入桶，不冒充无路由
            route_bucket = "route_failed"
            observed["routes_error"] = str(e)
    
    observed["sources"] = {
        "retrieval_source": result.get("rag_source") or "",
        "route_bucket": route_bucket,
        "strategy": payload.get("strategy") or "(config default)",
    }
    return observed
