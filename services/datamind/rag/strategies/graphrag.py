"""GraphRagStrategy — single-route retrieval: agentic text→SPARQL grounding.

Replaces the vector + BM25 retrieval legs. The only *search* step is the LLM
driving read-only SPARQL over the Oxigraph knowledge graph to ground the
question onto concrete entities (see ``agentic_sparql.AgenticSparqlRetriever``).
Grounded entity names are then hydrated into full prompt context via plain,
deterministic metadata lookups (exact-name/IN queries — no embeddings, no BM25).

Returns the uniform strategy dict consumed by ``prompt_builder`` (see base.py):
    table_info, column_metadata, business_terms, table_relations,
    sql_templates, saved_datasets, rag_source.
"""

import logging

from services.datamind.rag.strategies.base import RetrievalStrategy, empty_result

logger = logging.getLogger(__name__)

# Cap grounded tables so the prompt stays focused (SPARQL grounding returns
# importance-ordered names from the model).
_MAX_TABLES = 8


class GraphRagStrategy(RetrievalStrategy):
    """Ground a question on the knowledge graph via agentic SPARQL, then hydrate."""

    name = "graphrag"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from services.datamind.rag.graph_rag.agentic_sparql import AgenticSparqlRetriever
        from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore

        store = OxigraphStore()
        if not store.health():
            logger.warning("[graphrag] Oxigraph unreachable; returning empty")
            return empty_result("graphrag:store_unavailable")

        candidate = selected_tables or target_tables

        # ── Grounding: the single (SPARQL) retrieval route ──
        try:
            grounding = AgenticSparqlRetriever(store=store).ground(
                question, datasource_id=datasource_id, keywords=keywords,
                candidate_tables=candidate,
            )
        except Exception as e:
            logger.error("[graphrag] grounding failed: %s", e, exc_info=True)
            grounding = {"tables": [], "sql_templates": [], "business_terms": [], "metrics": []}

        tables = [t for t in (grounding.get("tables") or []) if t][:_MAX_TABLES]
        if not tables and candidate:
            # Graph had nothing to ground on — degrade to by-name hydration of
            # any pre-selected tables (still no vector/BM25 search here).
            tables = list(candidate)[:_MAX_TABLES]
            logger.info("[graphrag] no grounded tables, using %d candidate tables", len(tables))

        result = empty_result("graphrag")
        result["table_info"] = self._hydrate_tables(tables, datasource_id)
        result["column_metadata"] = self._hydrate_columns(tables, datasource_id)
        result["sql_templates"] = self._hydrate_templates(
            grounding.get("sql_templates") or [], tables, datasource_id,
        )
        result["business_terms"] = self._hydrate_terms(
            grounding.get("business_terms") or [], tables, datasource_id,
        )
        result["table_relations"] = self._hydrate_relations(tables, datasource_id)
        # Keep grounding for downstream logging/debugging (extra key is harmless).
        result["ontology_context"] = {
            "grounding": {
                "tables": tables,
                "sql_templates": grounding.get("sql_templates", []),
                "business_terms": grounding.get("business_terms", []),
                "metrics": grounding.get("metrics", []),
                "reasoning": grounding.get("reasoning", ""),
                "submitted": grounding.get("submitted", False),
                "turns": grounding.get("turns", 0),
            },
        }
        logger.info(
            "[graphrag] hydrated tables=%d cols=%d templates=%d terms=%d relations=%d",
            len(result["table_info"]), len(result["column_metadata"]),
            len(result["sql_templates"]), len(result["business_terms"]),
            len(result["table_relations"]),
        )
        return result

    # ── Hydration (deterministic metadata lookups, no embedding/BM25) ──

    def _hydrate_tables(self, tables: list[str], datasource_id: int) -> list[dict]:
        if not tables:
            return []
        from services.datamind.rag.rag_retriever import _get_table_info_for_names
        return _get_table_info_for_names(tables, datasource_id)

    def _hydrate_columns(self, tables: list[str], datasource_id: int) -> list[dict]:
        if not tables:
            return []
        from services.datamind.rag.rag_retriever import _get_columns_for_tables
        return _get_columns_for_tables(tables, datasource_id)

    def _hydrate_templates(self, grounded: list[str], tables: list[str],
                           datasource_id: int) -> list[dict]:
        """Match templates by grounded id/name, or by touching a grounded table."""
        if not grounded and not tables:
            return []
        from services.shared.common.db.metadata_db import get_metadata_conn
        gset = {str(x).strip() for x in grounded}
        tset = {str(x).strip().lower() for x in tables if x}
        conn = get_metadata_conn()
        out: list[dict] = []
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT template_id, template_name, category, intent_keywords,
                           sql_template, variables, rules, description, usage_count
                    FROM adh_sql_templates WHERE is_active = 1 {ds_filter}
                """, params)
                rows = cur.fetchall()
            for r in rows:
                tid = str(r.get("template_id", ""))
                name = str(r.get("template_name", ""))
                hit = tid in gset or name in gset
                if not hit and tset:
                    low = (r.get("sql_template") or "").lower()
                    hit = any(tn in low for tn in tset)
                if hit:
                    out.append(r)
        except Exception as e:
            logger.warning("[graphrag] template hydration failed: %s", e)
            return []
        finally:
            conn.close()
        # Grounded matches first (preserve model's ordering intent), then table-derived.
        def _rank(t):
            key = str(t.get("template_id", "")), str(t.get("template_name", ""))
            order = [g for g in grounded]
            for i, g in enumerate(order):
                if g in key:
                    return (0, i)
            return (1, 0)
        out.sort(key=_rank)
        return out[:8]

    def _hydrate_terms(self, grounded: list[str], tables: list[str],
                       datasource_id: int) -> list[dict]:
        """Match business terms by grounded name, or mapped to a grounded table."""
        if not grounded and not tables:
            return []
        from services.shared.common.db.metadata_db import get_metadata_conn
        gset = {str(x).strip() for x in grounded}
        tset = {str(x).strip().lower() for x in tables if x}
        conn = get_metadata_conn()
        out: list[dict] = []
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT term_cn, term_en, description, calculation,
                           target_table, target_column
                    FROM adh_business_terms WHERE is_active = 1 {ds_filter}
                """, params)
                for r in cur.fetchall():
                    hit = str(r.get("term_cn", "")) in gset or str(r.get("term_en", "")) in gset
                    if not hit and tset and (r.get("target_table") or "").lower() in tset:
                        hit = True
                    if hit:
                        out.append(r)
        except Exception as e:
            logger.warning("[graphrag] term hydration failed: %s", e)
            return []
        finally:
            conn.close()
        return out[:20]

    def _hydrate_relations(self, tables: list[str], datasource_id: int) -> list[dict]:
        """Table relations (with join columns) involving any grounded table."""
        if not tables:
            return []
        from services.shared.common.db.metadata_db import get_metadata_conn
        tset = {str(x).strip() for x in tables}
        conn = get_metadata_conn()
        out: list[dict] = []
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT source_table, source_column, target_table, target_column,
                           relation_type, join_type, description
                    FROM adh_table_relations WHERE is_active = 1 {ds_filter}
                """, params)
                for r in cur.fetchall():
                    if str(r.get("source_column", "")).strip() and str(r.get("target_column", "")).strip() \
                            and (r.get("source_table") in tset or r.get("target_table") in tset):
                        out.append(r)
        except Exception as e:
            logger.warning("[graphrag] relation hydration failed: %s", e)
            return []
        finally:
            conn.close()
        return out[:30]
