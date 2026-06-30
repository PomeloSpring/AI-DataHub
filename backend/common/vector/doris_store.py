"""DorisVectorStore — Doris HNSW implementation of VectorStore.

Uses Doris's native l2_distance_approximate() for vector similarity search.
Replaces raw SQL in rag_retriever.py with a clean abstraction.

Usage:
    from backend.common.vector import get_vector_store

    store = get_vector_store()
    results = store.search(
        table="adh_table_info",
        query_embedding=[0.1, 0.2, ...],
        limit=20,
        filters={"is_active": 1},
    )
"""

import logging
from typing import Any, Optional

from backend.common.vector.base import VectorStore
from backend.common.db.metadata_db import get_vector_connection

logger = logging.getLogger(__name__)


def _embedding_to_sql_literal(embedding: list[float]) -> str:
    """Convert embedding list to SQL array literal for Doris HNSW."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


class DorisVectorStore(VectorStore):
    """Doris HNSW implementation of VectorStore."""

    def search(
        self,
        table: str,
        query_embedding: list[float],
        limit: int = 20,
        filters: Optional[dict] = None,
        output_columns: Optional[list[str]] = None,
    ) -> list[dict]:
        """Vector similarity search using Doris HNSW index."""
        try:
            vec_literal = _embedding_to_sql_literal(query_embedding)

            # Build SELECT clause
            if output_columns:
                select_cols = ", ".join(f"`{c}`" for c in output_columns)
            else:
                select_cols = "*"

            # Build WHERE clause
            where_parts = []
            params = []
            if filters:
                for key, value in filters.items():
                    if key == "_raw":
                        # Raw SQL filter (for complex expressions)
                        where_parts.append(value)
                    elif isinstance(value, (list, tuple)):
                        placeholders = ", ".join(["%s"] * len(value))
                        where_parts.append(f"`{key}` IN ({placeholders})")
                        params.extend(value)
                    else:
                        where_parts.append(f"`{key}` = %s")
                        params.append(value)

            where_clause = " AND ".join(where_parts) if where_parts else "1=1"

            sql = f"""
                SELECT {select_cols},
                       l2_distance_approximate(embedding, {vec_literal}) AS distance
                FROM {table}
                WHERE {where_clause}
                ORDER BY distance ASC
                LIMIT %s
            """
            params.append(limit)

            with get_vector_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()

        except Exception as e:
            logger.error("Vector search failed on table %s: %s", table, e)
            return []

    def upsert(
        self,
        table: str,
        id_column: str,
        id_value: Any,
        data: dict,
    ) -> None:
        """Insert or update a single record.

        Note: Doris DUPLICATE KEY tables don't support UPDATE directly.
        Use DELETE + INSERT pattern.
        """
        try:
            with get_vector_connection() as conn:
                with conn.cursor() as cur:
                    # Delete existing record
                    cur.execute(
                        f"DELETE FROM {table} WHERE `{id_column}` = %s",
                        (id_value,)
                    )

                    # Insert new record
                    cols = ", ".join(f"`{k}`" for k in data.keys())
                    placeholders = ", ".join(["%s"] * len(data))
                    cur.execute(
                        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                        list(data.values())
                    )

        except Exception as e:
            logger.error("Upsert failed on table %s for %s=%s: %s", table, id_column, id_value, e)
            raise

    def upsert_batch(
        self,
        table: str,
        id_column: str,
        records: list[dict],
    ) -> int:
        """Batch insert or update records.

        Uses DELETE + INSERT pattern for Doris compatibility.
        """
        if not records:
            return 0

        try:
            with get_vector_connection() as conn:
                with conn.cursor() as cur:
                    # Delete existing records
                    id_values = [r[id_column] for r in records]
                    placeholders = ", ".join(["%s"] * len(id_values))
                    cur.execute(
                        f"DELETE FROM {table} WHERE `{id_column}` IN ({placeholders})",
                        id_values
                    )

                    # Insert new records
                    cols = ", ".join(f"`{k}`" for k in records[0].keys())
                    col_placeholders = ", ".join(["%s"] * len(records[0]))
                    for record in records:
                        cur.execute(
                            f"INSERT INTO {table} ({cols}) VALUES ({col_placeholders})",
                            list(record.values())
                        )

                    return len(records)

        except Exception as e:
            logger.error("Batch upsert failed on table %s: %s", table, e)
            raise

    def delete(
        self,
        table: str,
        id_column: str,
        id_value: Any,
    ) -> None:
        """Delete a single record."""
        try:
            with get_vector_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {table} WHERE `{id_column}` = %s",
                        (id_value,)
                    )
        except Exception as e:
            logger.error("Delete failed on table %s for %s=%s: %s", table, id_column, id_value, e)
            raise
