"""OntologyTraversalStrategy — 一次性确定性本体检索（无向量、无 LLM 循环）。

以 Palantir 本体（经 ontology_yaml_import 归一化并入 Oxigraph）为语义标准：
    ① 入口：问题分词 + 同义词 → 词法 CONTAINS 命中 owl:Class 对象（seed）
    ② 闭包：一条 SPARQL 属性路径让图引擎跑完关联遍历（≤2 跳，无往返）
    ③ 映射：对象 → 物理表（primary_table + mapsColumn 前缀）
    ④ 装填：复用 GraphRagStrategy 的确定性 hydration 输出标准 RAG dict

与默认 `graphrag` 的区别：graphrag 用 AgenticSparqlRetriever（LLM 最多 6 轮写
SPARQL 探图）；本策略因本体规模小、schema 全已知，查询形状可预先手写，故单趟完成。
"""

import logging
import re

from services.datamind.rag.strategies.base import RetrievalStrategy, empty_result
from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore
from services.shared.common.rdf.sparql_client import get_sparql_client, OxigraphClient

logger = logging.getLogger(__name__)

_MAX_SEEDS = 5       # 入口对象上限
_MAX_HOPS = 2        # 关联遍历跳数
_MAX_TABLES = 8      # 送入 prompt 的物理表上限
_RESULT_LIMIT = 200

# 只保留常见文字/下划线，剔除会破坏 SPARQL 字面量的字符
_TOKEN_SANITIZE = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff]+")


def _clean_token(tok: str) -> str:
    return _TOKEN_SANITIZE.sub("", tok or "")[:32]


class OntologyTraversalStrategy(RetrievalStrategy):
    """Ground a question on the ontology graph via one-shot SPARQL traversal."""

    name = "ontology_traversal"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from services.datamind.rag.rag_retriever import _search_tokens

        client = get_sparql_client()
        if not client.health():
            logger.warning("[ontology_traversal] Oxigraph unreachable; returning empty")
            return empty_result("ontology_traversal:store_unavailable")

        graph = OxigraphStore.graph_uri(datasource_id)

        # ① 入口 seed（纯词法匹配，无 LLM）
        tokens = [_clean_token(t) for t in _search_tokens(question, keywords)]
        tokens = [t for t in tokens if t]
        seeds = self._find_seeds(client, graph, tokens) if tokens else []
        if not seeds:
            # 词法无命中：若有候选表提示则交下游按名 hydration，否则空
            candidate = selected_tables or target_tables or []
            res = empty_result("ontology_traversal:no_seed")
            if candidate:
                res = self._hydrate(candidate, [], res, datasource_id)
            return res

        seed_iris = [s["iri"] for s in seeds[:_MAX_SEEDS]]

        # ② 关联闭包（一条属性路径，引擎单趟遍历）
        closure = self._closure(client, graph, seed_iris)   # iri -> {label, primary_table}
        col_tables = self._maps_columns(client, graph, set(closure))  # iri -> {table}
        links = self._links(client, graph, set(closure))    # [{from,to,join,cardinality}]

        # ③ 对象 → 物理表（seed 优先，按跳序，截断）
        tables = self._closure_tables(seed_iris, closure, col_tables)

        # ④ 装填（复用 graphrag 确定性 hydration）
        result = self._hydrate(tables, links, empty_result("ontology_traversal"), datasource_id)
        result["ontology_context"] = {
            "strategy": self.name,
            "seeds": [s["label"] for s in seeds[:_MAX_SEEDS]],
            "objects": sorted({v["label"] for v in closure.values()}),
            "links": links[:30],
            "tables": tables,
            # Phase 2：回传每个对象的绑定徒章，供语义层/LLM 直接取用
            # (目标源 / queryMode / sizeClass / guardrail / 同步状态)。
            "bindings": self._bindings_view(closure),
        }
        logger.info(
            "[ontology_traversal] seeds=%d closure=%d tables=%d links=%d",
            len(seed_iris), len(closure), len(tables), len(links),
        )
        return result

    # ── SPARQL steps ────────────────────────────────────────────────

    def _find_seeds(self, client: OxigraphClient, graph: str, tokens: list[str]) -> list[dict]:
        conds = []
        for tok in tokens:
            low = tok.lower()
            conds.append(
                f'CONTAINS(LCASE(STR(?label)), "{low}") '
                f'|| CONTAINS(LCASE(STR(COALESCE(?desc, ""))), "{low}") '
                f'|| CONTAINS(LCASE(STR(COALESCE(?alias, ""))), "{low}")'
            )
        filter_expr = " || ".join(conds) if conds else "false"
        sparql = f"""
SELECT DISTINCT ?c ?label WHERE {{
  GRAPH <{graph}> {{
    ?c a owl:Class ; rdfs:label ?label .
    OPTIONAL {{ ?c rdfs:comment ?desc }}
    OPTIONAL {{ ?c skos:altLabel ?alias }}
  }}
  FILTER ( {filter_expr} )
}} LIMIT {_RESULT_LIMIT}
"""
        rows = client.query(sparql)
        return [{"iri": r["c"], "label": r.get("label", "")} for r in rows if r.get("c")]

    def _closure(self, client: OxigraphClient, graph: str, seed_iris: list[str]) -> dict:
        """Bounded frontier expansion over ``adh:linkedTo`` (both directions).

        This Oxigraph build rejects cardinality on a path group
        (``(p|^p){1,2}`` fails to parse), so we run ``_MAX_HOPS`` one-hop
        round-trips instead — still fully deterministic (no LLM), the graph
        engine does the walking, and the hop count stays capped.
        """
        closure: dict[str, dict] = {iri: {"label": "", "primary_table": ""} for iri in seed_iris}
        frontier = list(seed_iris)
        for _ in range(_MAX_HOPS):
            if not frontier:
                break
            values = " ".join(f"<{iri}>" for iri in frontier)
            sparql = f"""
SELECT DISTINCT ?n WHERE {{
  GRAPH <{graph}> {{
    VALUES ?s {{ {values} }}
    ?s (adh:linkedTo|^adh:linkedTo) ?n .
    FILTER(?n != ?s)
  }}
}} LIMIT {_RESULT_LIMIT}
"""
            nxt = [r["n"] for r in client.query(sparql)
                   if r.get("n") and r["n"] not in closure]
            for iri in nxt:
                closure[iri] = {"label": "", "primary_table": ""}
            frontier = nxt

        # Resolve label / primary_table for every node in the closure (seeds + hops).
        if closure:
            values = " ".join(f"<{iri}>" for iri in closure)
            label_sparql = f"""
SELECT ?c ?label ?pt ?qm ?sc ?cr ?afs ?st WHERE {{
  GRAPH <{graph}> {{
    VALUES ?c {{ {values} }}
    ?c rdfs:label ?label .
    OPTIONAL {{ ?c adh:primaryTable ?pt }}
    OPTIONAL {{ ?c adh:queryMode    ?qm }}
    OPTIONAL {{ ?c adh:sizeClass    ?sc }}
    OPTIONAL {{ ?c adh:catalogRef   ?cr }}
    OPTIONAL {{ ?c adh:allowFullScan ?afs }}
    OPTIONAL {{ ?c adh:syncState    ?st }}
  }}
}}
"""
            for r in client.query(label_sparql):
                iri = r.get("c")
                if iri not in closure:
                    continue
                closure[iri]["label"] = r.get("label", "") or closure[iri]["label"]
                closure[iri]["primary_table"] = r.get("pt", "") or ""
                binding = {}
                if r.get("qm"):
                    binding["query_mode"] = r["qm"]
                if r.get("sc"):
                    binding["size_class"] = r["sc"]
                if r.get("cr"):
                    binding["catalog_ref"] = r["cr"]
                if r.get("afs") is not None:
                    binding["allow_full_scan"] = str(r["afs"]) not in ("false", "0")
                if r.get("st"):
                    binding["sync_state"] = r["st"]
                if r.get("pt"):
                    binding["physical_table"] = r["pt"]
                if binding:
                    closure[iri]["binding"] = binding
        return closure

    def _maps_columns(self, client: OxigraphClient, graph: str, iris: set) -> dict:
        result: dict[str, set] = {}
        if not iris:
            return result
        values = " ".join(f"<{iri}>" for iri in iris)
        sparql = f"""
SELECT ?o ?col WHERE {{
  GRAPH <{graph}> {{ VALUES ?o {{ {values} }} ?o adh:mapsColumn ?col }}
}} LIMIT {_RESULT_LIMIT}
"""
        for r in client.query(sparql):
            iri, col = r.get("o"), r.get("col", "")
            if iri and "." in str(col):
                result.setdefault(iri, set()).add(str(col).split(".", 1)[0])
        return result

    def _links(self, client: OxigraphClient, graph: str, iris: set) -> list[dict]:
        if not iris:
            return []
        values = " ".join(f"<{iri}>" for iri in iris)
        sparql = f"""
SELECT DISTINCT ?from ?to ?join ?card WHERE {{
  GRAPH <{graph}> {{
    VALUES ?from {{ {values} }}
    ?l a adh:Link ; adh:linkFrom ?from ; adh:linkTo ?to ; adh:joinExpr ?join .
    OPTIONAL {{ ?l adh:cardinality ?card }}
  }}
}} LIMIT {_RESULT_LIMIT}
"""
        out = []
        for r in client.query(sparql):
            join = (r.get("join") or "").strip()
            out.append({
                "from": (r.get("from") or "").split("obj:")[-1],
                "to": (r.get("to") or "").split("obj:")[-1],
                "join": join,
                "cardinality": r.get("card", "") or "",
            })
        # 只保留含有效 join 的边（供下游 JOIN 提示，去重）
        seen = set()
        deduped = []
        for lk in out:
            sig = (lk["from"], lk["to"], lk["join"])
            if lk["join"] and sig not in seen:
                seen.add(sig)
                deduped.append(lk)
        return deduped

    # ── table ordering ──────────────────────────────────────────────

    def _closure_tables(self, seed_iris: list[str], closure: dict,
                        col_tables: dict) -> list[str]:
        ordered_iris = list(seed_iris) + [i for i in closure if i not in seed_iris]
        tables: list[str] = []
        seen = set()
        for iri in ordered_iris:
            for t in self._object_tables(closure.get(iri, {}), col_tables.get(iri, set())):
                if t and t not in seen:
                    seen.add(t)
                    tables.append(t)
                if len(tables) >= _MAX_TABLES:
                    return tables
        return tables

    @staticmethod
    def _object_tables(meta: dict, extra_tables: set) -> list[str]:
        out = []
        pt = (meta.get("primary_table") or "").strip()
        if pt:
            out.append(pt)
        out.extend(sorted(extra_tables or []))
        return out

    @staticmethod
    def _bindings_view(closure: dict) -> dict:
        """closure → {label -> binding}，方便下游以中文名直接取到绑定。"""
        view: dict[str, dict] = {}
        for meta in closure.values():
            label = meta.get("label") or ""
            binding = meta.get("binding") or {}
            if label and binding:
                view[label] = binding
        return view

    # ── hydration (reuse graphrag deterministic lookups) ─────────────

    def _hydrate(self, tables: list[str], links: list[dict], result: dict,
                 datasource_id: int) -> dict:
        from services.datamind.rag.strategies.graphrag import GraphRagStrategy
        g = GraphRagStrategy()
        result["table_info"] = g._hydrate_tables(tables, datasource_id)
        result["column_metadata"] = g._hydrate_columns(tables, datasource_id)
        result["table_relations"] = g._hydrate_relations(tables, datasource_id)
        result["business_terms"] = g._hydrate_terms([], tables, datasource_id)
        result["sql_templates"] = g._hydrate_templates([], tables, datasource_id)
        # 用本体 join 补充/合成缺失的关联（physical relations 未覆盖时）
        result["table_relations"] = self._merge_ontology_relations(
            result["table_relations"], links,
        )
        return result

    @staticmethod
    def _merge_ontology_relations(physical: list[dict], links: list[dict]) -> list[dict]:
        covered = {
            (str(r.get("source_table", "")), str(r.get("target_table", "")))
            for r in physical
        }
        extra = []
        for lk in links:
            a, b = lk["from"], lk["to"]
            if not a or not b or (a, b) in covered or (b, a) in covered:
                continue
            extra.append({
                "source_table": a, "target_table": b,
                "relation_type": lk.get("cardinality", ""),
                "join_type": "INNER",
                "description": f"ontology join: {lk.get('join', '')}",
            })
        return physical + extra
