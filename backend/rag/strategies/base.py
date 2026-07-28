"""Base class for RAG retrieval strategies.

All strategies implement the same interface and return a uniform dict,
so downstream code (prompt_builder, LLM) is strategy-agnostic.
"""

from abc import ABC, abstractmethod


class RetrievalStrategy(ABC):
    """Abstract base for metadata retrieval strategies."""

    name: str = "base"

    @abstractmethod
    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        """Retrieve metadata for NL2SQL prompt construction.

        Args:
            question: User's natural language question.
            selected_tables: Pre-selected tables from table_selector (keyword matching).
            target_tables: Tables from intent classifier (legacy, for boost).
            keywords: Business keywords for term filtering.
            datasource_id: Filter metadata by this datasource.

        Returns:
            Dict with keys:
                table_info: list[dict]      — table-level metadata
                column_metadata: list[dict] — column-level metadata
                business_terms: list[dict]  — matched business terms
                table_relations: list[dict] — table JOIN relations
                sql_templates: list[dict]   — matched SQL templates
                saved_datasets: list[dict]  — matched saved datasets
                rag_source: str             — strategy name for logging
        """
        ...


# Standard empty result for fallback
_EMPTY_RESULT = {
    "table_info": [],
    "column_metadata": [],
    "business_terms": [],
    "table_relations": [],
    "sql_templates": [],
    "saved_datasets": [],
    "rag_source": "empty",
}


def empty_result(rag_source: str = "empty") -> dict:
    """Return an empty result dict with the given rag_source label."""
    return {**_EMPTY_RESULT, "rag_source": rag_source}
