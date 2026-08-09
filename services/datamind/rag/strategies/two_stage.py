"""Two Stage Strategy — table-level coarse filter → column-level fine filter.

Stage 1: Vector search tables (same as full_table).
Stage 2: For each matched table, vector-search its columns and keep only relevant ones.
Pros: balanced noise/relevance. Cons: second stage adds latency.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from services.datamind.rag.strategies.base import RetrievalStrategy, empty_result

logger = logging.getLogger(__name__)

# Column relevance score threshold — columns below this are excluded
_COLUMN_SCORE_THRESHOLD = 0.0


class TwoStageStrategy(RetrievalStrategy):
    """Stage 1: table search. Stage 2: per-table column filtering."""

    name = "two_stage"

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
            _get_table_info_for_names,
            _get_columns_for_tables,
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
        )
        from services.datamind.rag.table_selector import select_tables
        from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # ── Stage 1: Table selection ──
        if selected_tables:
            logger.info("[two_stage] using pre-selected tables: %s", selected_tables)
            table_info = _get_table_info_for_names(selected_tables, datasource_id)
        else:
            # Use table_selector for keyword+vector dual-channel
            selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)
            if selected_tables:
                table_info = _get_table_info_for_names(selected_tables, datasource_id)
            else:
                table_info = retrieve_table_info(question, 20, target_tables, vec_literal, datasource_id)

        if not table_info:
            logger.warning("[two_stage] no tables found")
            return empty_result("two_stage:no_tables")

        top_table_names = [t["table_name"] for t in table_info]
        logger.info("[two_stage] stage1 tables: %s", top_table_names)

        # ── Stage 2: Column filtering ──
        # Get ALL columns for matched tables first
        all_columns = _get_columns_for_tables(top_table_names, datasource_id)

        # Vector search columns to get relevance scores
        vector_columns = retrieve_column_metadata(
            question, limit=100, target_tables=top_table_names,
            vec_literal=vec_literal, datasource_id=datasource_id,
        )

        # Build a set of (table, column) pairs from vector search (these are relevant)
        relevant_keys = set()
        for vc in vector_columns:
            relevant_keys.add((vc["table_name"], vc["column_name"]))

        # Also keep columns that match keywords in the question
        question_lower = question.lower()
        keyword_matched_keys = set()
        for col in all_columns:
            col_name = col["column_name"].lower()
            col_comment = (col.get("column_comment") or "").lower()
            col_biz = (col.get("business_desc") or "").lower()
            if (col_name in question_lower or
                col_comment in question_lower or
                any(kw in col_comment for kw in question_lower.split()) or
                any(kw in col_biz for kw in question_lower.split())):
                keyword_matched_keys.add((col["table_name"], col["column_name"]))

        # Merge: vector-matched + keyword-matched, preserve full rows
        keep_keys = relevant_keys | keyword_matched_keys
        filtered_columns = [c for c in all_columns if (c["table_name"], c["column_name"]) in keep_keys]

        # Ensure at least some columns per table (fallback to key columns)
        tables_with_cols = {c["table_name"] for c in filtered_columns}
        for col in all_columns:
            if col["table_name"] not in tables_with_cols and col.get("is_key") == "true":
                filtered_columns.append(col)

        # If filtering removed too much, fall back to all columns
        if len(filtered_columns) < len(all_columns) * 0.3:
            logger.info("[two_stage] column filter too aggressive (%d/%d), using all",
                        len(filtered_columns), len(all_columns))
            filtered_columns = all_columns

        logger.info("[two_stage] stage2 columns: %d → %d (from %d total)",
                    len(all_columns), len(filtered_columns), len(all_columns))

        # Step 3: Parallel searches for templates, terms, relations, datasets
        sql_templates, business_terms, table_relations, saved_datasets = _parallel_search(
            question, keywords, top_table_names, vec_literal, datasource_id
        )

        return {
            "table_info": table_info,
            "column_metadata": filtered_columns,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": "two_stage",
        }


def _parallel_search(question, keywords, table_names, vec_literal, datasource_id):
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

        for name, future in [("sql_templates", f_sql), ("business_terms", f_terms),
                             ("table_relations", f_rels), ("saved_datasets", f_ds)]:
            try:
                result = future.result()
                if name == "sql_templates": sql_templates = result
                elif name == "business_terms": business_terms = result
                elif name == "table_relations": table_relations = result
                else: saved_datasets = result
            except Exception as e:
                logger.warning("%s failed: %s", name, e)

    return sql_templates, business_terms, table_relations, saved_datasets
