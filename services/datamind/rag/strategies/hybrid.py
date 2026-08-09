"""Hybrid Strategy — BM25 sparse + Vector dense with RRF fusion + template fast path.

Unified retrieval strategy that replaces full_table, column_first, two_stage, bidirectional.

Pipeline:
  Step 0: Template fast path — high-confidence template match → skip RAG + LLM
  Step 1: BM25 sparse + Vector dense → RRF fusion for tables, columns, terms, templates
  Step 2: Column handling based on sub_mode (full_table / two_stage / graph)
  Step 3: Parallel auxiliary retrieval (relations, datasets)

Sub-modes:
  - full_table: selected tables → ALL columns (simple, fast)
  - two_stage: selected tables → BM25+Vector filtered columns (precise)
  - graph: entry nodes → graph traversal → discover related entities
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from services.datamind.rag.strategies.base import RetrievalStrategy, empty_result

logger = logging.getLogger(__name__)

# Template fast-path confidence threshold (vector distance)
# Lower = more confident. Doris L2 distance, typical range 0.5-2.0
_TEMPLATE_DISTANCE_THRESHOLD = 0.8


class HybridStrategy(RetrievalStrategy):
    """BM25+Vector hybrid retrieval with template fast path."""

    name = "hybrid"

    def __init__(self, sub_mode: str = "two_stage"):
        """
        Args:
            sub_mode: Column handling mode — "full_table", "two_stage", or "graph".
        """
        self.sub_mode = sub_mode

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from services.datamind.rag.rag_retriever import (
            retrieve_table_info,
            retrieve_column_metadata,
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
            _get_table_info_for_names,
            _get_columns_for_tables,
            _fallback_from_information_schema,
        )
        from services.datamind.rag.table_selector import select_tables, _bm25_search_tables
        from services.datamind.rag.bm25 import rrf_merge
        from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        # Generate embedding once
        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # ── Step 0: Template fast path ──
        template_result = self._try_template_fast_path(
            question, vec_literal, datasource_id
        )
        if template_result:
            logger.info("[hybrid] template fast path hit, skipping RAG + LLM")
            return template_result

        # ── Step 1: Table retrieval (BM25 + Vector + RRF) ──
        table_info, rag_source = self._retrieve_tables(
            question, selected_tables, target_tables,
            keywords, vec_literal, datasource_id,
        )

        if not table_info:
            logger.warning("[hybrid] no tables found, falling back to information_schema")
            fallback_tables = selected_tables or target_tables
            fallback = _fallback_from_information_schema(target_tables=fallback_tables)
            return {
                **fallback,
                "sql_templates": [],
                "business_terms": [],
                "table_relations": [],
                "saved_datasets": [],
                "rag_source": "hybrid:information_schema_fallback",
            }

        top_table_names = [t["table_name"] for t in table_info]

        # ── Step 2: Column retrieval (sub-mode dependent) ──
        # Graph mode also returns relations from graph traversal
        column_metadata, graph_relations = self._retrieve_columns(
            question, top_table_names, selected_tables,
            vec_literal, datasource_id,
        )

        # ── Step 3: Parallel auxiliary retrieval ──
        # For graph mode, relations come from graph traversal, not vector search
        if self.sub_mode == "graph" and graph_relations:
            sql_templates, business_terms, _, saved_datasets = (
                self._retrieve_auxiliary(
                    question, keywords, top_table_names, vec_literal, datasource_id
                )
            )
            table_relations = graph_relations
        else:
            sql_templates, business_terms, table_relations, saved_datasets = (
                self._retrieve_auxiliary(
                    question, keywords, top_table_names, vec_literal, datasource_id
                )
            )

        return {
            "table_info": table_info,
            "column_metadata": column_metadata,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": f"hybrid:{self.sub_mode}",
        }

    # ── Template fast path ──────────────────────────────────────────

    def _try_template_fast_path(
        self,
        question: str,
        vec_literal: str,
        datasource_id: int,
    ) -> Optional[dict]:
        """Check if a high-confidence SQL template match exists.

        Previously returned early with empty metadata, causing LLM to fail.
        Now always returns None — the template will be included naturally
        through the normal RAG vector search flow.
        """
        return None

    # ── Table retrieval ─────────────────────────────────────────────

    def _retrieve_tables(
        self,
        question: str,
        selected_tables: list[str],
        target_tables: list[str],
        keywords: list[str],
        vec_literal: str,
        datasource_id: int,
    ) -> tuple[list[dict], str]:
        """Retrieve tables using BM25 + Vector + RRF fusion."""
        from services.datamind.rag.rag_retriever import (
            retrieve_table_info, _get_table_info_for_names,
        )
        from services.datamind.rag.table_selector import select_tables

        if selected_tables:
            logger.info("[hybrid] using pre-selected tables: %s", selected_tables)
            table_info = _get_table_info_for_names(selected_tables, datasource_id)
            return table_info, "hybrid:pre_selected"

        # Use table_selector which already has BM25+Vector+RRF
        merged_tables = select_tables(
            question, top_k=10, vector_literal=vec_literal, datasource_id=datasource_id,
        )

        if merged_tables:
            table_info = _get_table_info_for_names(merged_tables, datasource_id)
            return table_info, "hybrid:bm25_vector_rrf"

        # Fallback: pure vector search
        table_info = retrieve_table_info(
            question, 20, target_tables, vec_literal, datasource_id,
        )
        return table_info, "hybrid:vector_fallback"

    # ── Column retrieval ────────────────────────────────────────────

    def _retrieve_columns(
        self,
        question: str,
        table_names: list[str],
        selected_tables: list[str],
        vec_literal: str,
        datasource_id: int,
    ) -> tuple[list[dict], list[dict]]:
        """Retrieve columns based on sub_mode.

        Returns:
            (column_metadata, table_relations) — table_relations is only
            populated for graph mode, empty list for others.
        """
        if self.sub_mode == "full_table":
            return self._columns_full_table(table_names, datasource_id), []
        elif self.sub_mode == "two_stage":
            return self._columns_two_stage(
                question, table_names, vec_literal, datasource_id,
            ), []
        elif self.sub_mode == "graph":
            return self._columns_graph(
                question, table_names, selected_tables, vec_literal, datasource_id,
            )
        else:
            logger.warning("[hybrid] unknown sub_mode '%s', falling back to full_table", self.sub_mode)
            return self._columns_full_table(table_names, datasource_id), []

    def _columns_full_table(
        self, table_names: list[str], datasource_id: int,
    ) -> list[dict]:
        """full_table: return ALL columns from matched tables."""
        from services.datamind.rag.rag_retriever import _get_columns_for_tables
        columns = _get_columns_for_tables(table_names, datasource_id)
        logger.info("[hybrid:full_table] %d columns from %d tables", len(columns), len(table_names))
        return columns

    def _columns_two_stage(
        self,
        question: str,
        table_names: list[str],
        vec_literal: str,
        datasource_id: int,
    ) -> list[dict]:
        """two_stage: BM25 + Vector column filtering."""
        from services.datamind.rag.rag_retriever import (
            retrieve_column_metadata, _get_columns_for_tables,
        )
        from services.datamind.rag.table_selector import _bm25_search_tables, _tokenize_text, _extract_keywords, _expand_synonyms
        from services.datamind.rag.bm25 import BM25, rrf_merge

        # Get ALL columns for the tables
        all_columns = _get_columns_for_tables(table_names, datasource_id)

        # Vector search columns
        vector_columns = retrieve_column_metadata(
            question, limit=100, target_tables=table_names,
            vec_literal=vec_literal, datasource_id=datasource_id,
        )
        vector_col_keys = {(c["table_name"], c["column_name"]) for c in vector_columns}

        # BM25 search columns
        bm25_col_keys = self._bm25_search_columns(question, all_columns)

        # Keyword match (direct string match in question)
        question_lower = question.lower()
        keyword_col_keys = set()
        for col in all_columns:
            col_name = col["column_name"].lower()
            col_comment = (col.get("column_comment") or "").lower()
            if col_name in question_lower or col_comment in question_lower:
                keyword_col_keys.add((col["table_name"], col["column_name"]))

        # Merge: keep columns hit by any channel + key columns
        keep_keys = vector_col_keys | bm25_col_keys | keyword_col_keys
        filtered = [
            c for c in all_columns
            if (c["table_name"], c["column_name"]) in keep_keys
            or c.get("is_key") == "true"
        ]

        # Ensure minimum coverage per table
        tables_with_cols = {c["table_name"] for c in filtered}
        for c in all_columns:
            if c["table_name"] not in tables_with_cols and c.get("is_key") == "true":
                filtered.append(c)

        # If filter is too aggressive, fall back to all
        if len(filtered) < len(all_columns) * 0.3:
            logger.info("[hybrid:two_stage] filter too aggressive (%d/%d), using all",
                        len(filtered), len(all_columns))
            filtered = all_columns

        logger.info("[hybrid:two_stage] columns: vector=%d, bm25=%d, keyword=%d → %d (from %d)",
                    len(vector_col_keys), len(bm25_col_keys), len(keyword_col_keys),
                    len(filtered), len(all_columns))
        return filtered

    def _bm25_search_columns(
        self, question: str, all_columns: list[dict],
    ) -> set[tuple[str, str]]:
        """BM25 search over column metadata. Returns set of (table_name, column_name)."""
        from services.datamind.rag.bm25 import BM25
        from services.datamind.rag.table_selector import _tokenize_text, _extract_keywords, _expand_synonyms

        if not all_columns:
            return set()

        # Build documents: tokenize column_name + column_comment + business_desc
        documents = []
        for col in all_columns:
            text = " ".join(filter(None, [
                col.get("column_name", ""),
                col.get("column_comment", ""),
                col.get("business_desc", ""),
            ]))
            documents.append(_tokenize_text(text))

        bm25 = BM25()
        bm25.index(documents)

        # Query tokens
        keywords = _extract_keywords(question)
        expanded = _expand_synonyms(keywords)
        query_tokens = list(set(kw.lower() for kw in expanded))
        query_tokens.extend(_tokenize_text(" ".join(expanded)))
        query_tokens = list(set(query_tokens))

        results = bm25.search(query_tokens, top_k=50)
        return {(all_columns[idx]["table_name"], all_columns[idx]["column_name"]) for idx, _ in results}

    def _columns_graph(
        self,
        question: str,
        table_names: list[str],
        selected_tables: list[str],
        vec_literal: str,
        datasource_id: int,
    ) -> tuple[list[dict], list[dict]]:
        """graph: use NetworkX graph traversal to discover columns and relations.

        Returns:
            (column_metadata, table_relations)
        """
        from services.datamind.rag.strategies.graph import _get_graph, _find_entry_nodes
        import networkx as nx

        G = _get_graph(datasource_id)
        entry_node_ids = _find_entry_nodes(G, question, selected_tables, datasource_id)

        if not entry_node_ids:
            logger.warning("[hybrid:graph] no entry nodes, falling back to full_table")
            return self._columns_full_table(table_names, datasource_id), []

        # ego_graph traversal
        discovered_ids: set[str] = set()
        for nid in entry_node_ids:
            ego = nx.ego_graph(G.to_undirected(), nid, radius=1)
            discovered_ids.update(ego.nodes())

        # Extract discovered columns and tables
        discovered_columns: set[tuple[str, str]] = set()
        discovered_tables: set[str] = set()
        for nid in discovered_ids:
            data = G.nodes[nid]
            if data.get("type") == "column":
                discovered_columns.add((data["table"], data["name"]))
            elif data.get("type") == "table":
                discovered_tables.add(data["name"])

        # JOIN-connected tables
        for nid in list(entry_node_ids):
            for successor in G.successors(nid):
                sdata = G.nodes[successor]
                if sdata.get("type") == "table":
                    discovered_tables.add(sdata["name"])
                    for col_succ in G.successors(successor):
                        cdata = G.nodes[col_succ]
                        if cdata.get("type") == "column":
                            discovered_columns.add((cdata["table"], cdata["name"]))

        # Filter columns
        from services.datamind.rag.rag_retriever import _get_columns_for_tables
        all_columns = _get_columns_for_tables(list(discovered_tables), datasource_id)
        column_metadata = [
            c for c in all_columns
            if (c["table_name"], c["column_name"]) in discovered_columns
            or c.get("is_key") == "true"
        ]

        # Minimum coverage
        tables_with_cols = {c["table_name"] for c in column_metadata}
        for c in all_columns:
            if c["table_name"] not in tables_with_cols and c.get("is_key") == "true":
                column_metadata.append(c)

        # Extract relations from graph edges
        table_relations = []
        seen_rels = set()
        for src_table in discovered_tables:
            src_id = f"table:{src_table}"
            if src_id not in G:
                continue
            for _, tgt_id, edata in G.out_edges(src_id, data=True):
                if edata.get("type") != "join":
                    continue
                tgt_data = G.nodes[tgt_id]
                if tgt_data.get("type") != "table":
                    continue
                tgt_table = tgt_data["name"]
                if tgt_table not in discovered_tables:
                    continue
                rel_key = tuple(sorted([src_table, tgt_table]))
                if rel_key not in seen_rels:
                    seen_rels.add(rel_key)
                    table_relations.append({
                        "source_table": src_table,
                        "source_column": edata.get("source_column", ""),
                        "target_table": tgt_table,
                        "target_column": edata.get("target_column", ""),
                        "relation_type": edata.get("relation_type", "1:N"),
                        "join_type": edata.get("join_type", "INNER"),
                        "description": edata.get("description", ""),
                    })

        logger.info("[hybrid:graph] entry=%s, tables=%s, columns=%d, relations=%d",
                    [G.nodes[n]["name"] for n in entry_node_ids],
                    list(discovered_tables), len(column_metadata), len(table_relations))
        return column_metadata, table_relations

    # ── Auxiliary retrieval ──────────────────────────────────────────

    def _retrieve_auxiliary(
        self,
        question: str,
        keywords: list[str],
        table_names: list[str],
        vec_literal: str,
        datasource_id: int,
    ) -> tuple:
        """Parallel retrieval of templates, terms, relations, datasets."""
        from services.datamind.rag.rag_retriever import (
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
        )

        sql_templates, business_terms, table_relations, saved_datasets = [], [], [], []

        with ThreadPoolExecutor(max_workers=4) as pool:
            f_sql = pool.submit(retrieve_sql_templates, question, 5, vec_literal, datasource_id)
            f_terms = pool.submit(retrieve_business_terms, question, 20, keywords, vec_literal, datasource_id)
            f_rels = pool.submit(retrieve_table_relations, question, 20, table_names, vec_literal, datasource_id)
            f_ds = pool.submit(retrieve_saved_datasets, question)

            for name, future in [
                ("sql_templates", f_sql),
                ("business_terms", f_terms),
                ("table_relations", f_rels),
                ("saved_datasets", f_ds),
            ]:
                try:
                    result = future.result()
                    if name == "sql_templates":
                        sql_templates = result
                    elif name == "business_terms":
                        business_terms = result
                    elif name == "table_relations":
                        table_relations = result
                    else:
                        saved_datasets = result
                except Exception as e:
                    logger.warning("%s failed: %s", name, e)

        return sql_templates, business_terms, table_relations, saved_datasets
