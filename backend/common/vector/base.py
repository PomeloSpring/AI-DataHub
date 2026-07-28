"""VectorStore — abstract base class for vector similarity search.

Defines the interface for vector storage backends (Doris HNSW, pgvector, ES, etc.).
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class VectorStore(ABC):
    """Abstract base class for vector similarity search."""

    @abstractmethod
    def search(
        self,
        table: str,
        query_embedding: list[float],
        limit: int = 20,
        filters: Optional[dict] = None,
        output_columns: Optional[list[str]] = None,
    ) -> list[dict]:
        """Vector similarity search.

        Args:
            table: Logical table name (e.g., "adh_table_info")
            query_embedding: Query vector (list of floats)
            limit: Maximum number of results
            filters: Optional filters (e.g., {"is_active": 1, "datasource_id": 123})
                     Special key "_raw" for raw SQL filter string
            output_columns: Columns to return (None = all)

        Returns:
            List of dicts, sorted by distance ASC
        """
        ...

    @abstractmethod
    def upsert(
        self,
        table: str,
        id_column: str,
        id_value: Any,
        data: dict,
    ) -> None:
        """Insert or update a single record with vector.

        Args:
            table: Logical table name
            id_column: Primary key column name
            id_value: Primary key value
            data: Column values to insert/update (must include embedding column)
        """
        ...

    @abstractmethod
    def upsert_batch(
        self,
        table: str,
        id_column: str,
        records: list[dict],
    ) -> int:
        """Batch insert or update records with vectors.

        Args:
            table: Logical table name
            id_column: Primary key column name
            records: List of records to upsert

        Returns:
            Number of records upserted
        """
        ...

    @abstractmethod
    def delete(
        self,
        table: str,
        id_column: str,
        id_value: Any,
    ) -> None:
        """Delete a single record.

        Args:
            table: Logical table name
            id_column: Primary key column name
            id_value: Primary key value
        """
        ...

    def hybrid_search(
        self,
        table: str,
        query_embedding: list[float],
        keyword: Optional[str] = None,
        limit: int = 20,
        filters: Optional[dict] = None,
        output_columns: Optional[list[str]] = None,
    ) -> list[dict]:
        """Hybrid search (vector + BM25).

        Default implementation falls back to vector-only search.
        Subclasses (e.g., Elasticsearch) can override for true hybrid search.

        Args:
            table: Logical table name
            query_embedding: Query vector
            keyword: Optional keyword for BM25 matching
            limit: Maximum number of results
            filters: Optional filters
            output_columns: Columns to return

        Returns:
            List of dicts, sorted by relevance
        """
        return self.search(table, query_embedding, limit, filters, output_columns)

    def close(self):
        """Close the vector store connection. Override if needed."""
        pass
