"""Bidirectional Strategy — table-first + column-first, take union.

Runs both table-level and column-level vector search in parallel,
then merges results: tables from both paths, columns from both paths.
Pros: best recall (不容易漏). Cons: more context than two_stage.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from backend.rag.strategies.base import RetrievalStrategy

logger = logging.getLogger(__name__)


class BidirectionalStrategy(RetrievalStrategy):
    """Run table-first and column-first in parallel, merge results."""

    name = "bidirectional"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from backend.rag.rag_retriever import (
            retrieve_table_info,
            retrieve_column_metadata,
            _get_table_info_for_names,
            _get_columns_for_tables,
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
        )
        from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # ── Path A: Table-first (same as full_table) ──
        if selected_tables:
            table_info_a = _get_table_info_for_names(selected_tables, datasource_id)
        else:
            table_info_a = retrieve_table_info(question, 20, target_tables, vec_literal, datasource_id)

        table_names_a = {t["table_name"] for t in table_info_a}

        # ── Path B: Column-first (find tables from columns) ──
        columns_b = retrieve_column_metadata(
            question, limit=50, vec_literal=vec_literal, datasource_id=datasource_id,
        )
        table_names_b = {c["table_name"] for c in columns_b}

        # ── Merge: union of table names ──
        all_table_names = list(dict.fromkeys(list(table_names_a) + list(table_names_b)))
        logger.info("[bidirectional] table-first=%s, column-first=%s, merged=%s",
                    list(table_names_a), list(table_names_b), all_table_names)

        # Get table info for all merged tables
        table_info = _get_table_info_for_names(all_table_names, datasource_id)

        # Get all columns, then filter to relevant ones
        all_columns = _get_columns_for_tables(all_table_names, datasource_id)

        # Build set of (table, column) from column-first vector search
        column_keys_b = {(c["table_name"], c["column_name"]) for c in columns_b}

        # Also match columns by keyword in question
        question_lower = question.lower()
        keyword_matched = set()
        for c in all_columns:
            col_comment = (c.get("column_comment") or "").lower()
            col_biz = (c.get("business_desc") or "").lower()
            if col_comment in question_lower or col_biz in question_lower:
                keyword_matched.add((c["table_name"], c["column_name"]))

        # Keep: vector-matched (path B) + keyword-matched + key columns
        keep_keys = column_keys_b | keyword_matched
        filtered_columns = [
            c for c in all_columns
            if (c["table_name"], c["column_name"]) in keep_keys
            or c.get("is_key") == "true"
        ]

        # Ensure minimum coverage
        tables_with_cols = {c["table_name"] for c in filtered_columns}
        for c in all_columns:
            if c["table_name"] not in tables_with_cols and c.get("is_key") == "true":
                filtered_columns.append(c)

        logger.info("[bidirectional] columns: %d vector-matched, %d keyword-matched → %d returned (from %d total)",
                    len(column_keys_b), len(keyword_matched), len(filtered_columns), len(all_columns))

        # Parallel searches for templates, terms, relations, datasets
        sql_templates, business_terms, table_relations, saved_datasets = _parallel_search(
            question, keywords, all_table_names, vec_literal, datasource_id
        )

        return {
            "table_info": table_info,
            "column_metadata": filtered_columns,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": "bidirectional",
        }


def _parallel_search(question, keywords, table_names, vec_literal, datasource_id):
    from backend.rag.rag_retriever import (
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
