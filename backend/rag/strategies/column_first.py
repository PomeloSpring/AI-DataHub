"""Column First Strategy — column-level BM25 + vector hybrid search.

Directly search columns using BM25 + vector + RRF, then find their parent tables.
Pros: precise, less noise, keyword-aware. Cons: may miss related columns if embedding doesn't match.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from backend.rag.strategies.base import RetrievalStrategy, empty_result

logger = logging.getLogger(__name__)


class ColumnFirstStrategy(RetrievalStrategy):
    """BM25 + vector hybrid search columns first, then derive tables from matched columns."""

    name = "column_first"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from backend.rag.rag_retriever import (
            select_columns,
            _get_table_info_for_names,
            _get_columns_for_tables,
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
        )
        from backend.rag.table_selector import _extract_keywords, _expand_synonyms
        from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # Extract keywords if not provided
        if keywords is None:
            keywords = _extract_keywords(question)
            keywords = _expand_synonyms(keywords)

        # Step 1: BM25 + vector hybrid search columns directly
        columns = select_columns(
            question, keywords=keywords, top_k=50,
            vector_literal=vec_literal, datasource_id=datasource_id,
        )

        if not columns:
            logger.warning("[column_first] no columns found, returning empty")
            return empty_result("column_first:empty")

        # Step 2: Derive unique table names from matched columns
        matched_table_names = list(dict.fromkeys(c["table_name"] for c in columns))
        logger.info("[column_first] matched tables from columns: %s", matched_table_names[:10])

        # Step 3: Get table info for derived tables
        table_info = _get_table_info_for_names(matched_table_names, datasource_id)

        # Step 4: Build column_metadata from hybrid-matched columns + key columns
        # Get all columns for key-column fallback
        all_columns = _get_columns_for_tables(matched_table_names, datasource_id)

        # Set of (table, column) from hybrid search
        matched_keys = {(c["table_name"], c["column_name"]) for c in columns}

        # Keep: hybrid-matched columns + key columns (for JOIN context)
        filtered_columns = [
            c for c in all_columns
            if (c["table_name"], c["column_name"]) in matched_keys
            or c.get("is_key") == "true"
        ]

        # Ensure at least some columns per table
        tables_with_cols = {c["table_name"] for c in filtered_columns}
        for c in all_columns:
            if c["table_name"] not in tables_with_cols and c.get("is_key") == "true":
                filtered_columns.append(c)

        logger.info("[column_first] columns: %d matched → %d returned (from %d total)",
                    len(columns), len(filtered_columns), len(all_columns))

        # Step 5: Parallel searches for templates, terms, relations, datasets
        sql_templates, business_terms, table_relations, saved_datasets = _parallel_search(
            question, keywords, matched_table_names, vec_literal, datasource_id
        )

        return {
            "table_info": table_info,
            "column_metadata": filtered_columns,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": "column_first",
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
