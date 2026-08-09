"""Full Table Strategy — current default approach.

Table-level vector search → return ALL columns from matched tables.
Pros: comprehensive, higher accuracy. Cons: noisy context, many irrelevant columns.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from services.datamind.rag.strategies.base import RetrievalStrategy

logger = logging.getLogger(__name__)


class FullTableStrategy(RetrievalStrategy):
    """Retrieve columns using BM25 + vector hybrid search, filtered by matched tables."""

    name = "full_table"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from services.datamind.rag.rag_retriever import (
            _get_table_info_for_names,
            _fallback_from_information_schema,
            retrieve_table_info,
            select_columns,
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
        )
        from services.datamind.rag.table_selector import _extract_keywords, _expand_synonyms
        from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        # Generate embedding once
        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # Extract keywords if not provided
        if keywords is None:
            keywords = _extract_keywords(question)
            keywords = _expand_synonyms(keywords)

        # Step 1: Get table info
        if selected_tables:
            logger.info("[full_table] using pre-selected tables: %s", selected_tables)
            table_info = _get_table_info_for_names(selected_tables, datasource_id)
            rag_source = "full_table:keyword_selected"
        else:
            table_info = retrieve_table_info(question, 20, target_tables, vec_literal, datasource_id)
            rag_source = "full_table:vector_search"

        # Step 2: Get relevant columns using BM25 + vector hybrid search
        top_table_names = [t["table_name"] for t in table_info]
        column_metadata = select_columns(
            question, keywords=keywords, top_k=50,
            vector_literal=vec_literal, datasource_id=datasource_id,
        )

        # Filter: only keep columns from matched tables
        if top_table_names:
            matched_set = set(top_table_names)
            column_metadata = [c for c in column_metadata if c["table_name"] in matched_set]

        # Step 3: Parallel searches for templates, terms, relations, datasets
        sql_templates, business_terms, table_relations, saved_datasets = _parallel_search(
            question, keywords, top_table_names, vec_literal, datasource_id
        )

        # Fallback: if RAG returned nothing, try information_schema
        if not table_info and not column_metadata:
            logger.warning("[full_table] RAG empty, falling back to information_schema")
            fallback_tables = selected_tables or target_tables
            fallback = _fallback_from_information_schema(target_tables=fallback_tables)
            table_info = fallback["table_info"]
            column_metadata = fallback["column_metadata"]
            rag_source = "full_table:information_schema_fallback"

        return {
            "table_info": table_info,
            "column_metadata": column_metadata,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": rag_source,
        }


def _parallel_search(
    question: str,
    keywords: list[str],
    table_names: list[str],
    vec_literal: str,
    datasource_id: int,
) -> tuple:
    """Run templates, terms, relations, datasets searches in parallel."""
    from services.datamind.rag.rag_retriever import (
        retrieve_sql_templates,
        retrieve_business_terms,
        retrieve_table_relations,
        retrieve_saved_datasets,
    )

    sql_templates = []
    business_terms = []
    table_relations = []
    saved_datasets = []

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
