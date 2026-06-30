"""MemoryVectorStore — In-memory implementation of VectorStore.

Lightweight fallback when no vector database is available.
Embeddings stored in memory, L2 distance computed via numpy.

Usage:
    VECTOR_DB_TYPE=default

Auto-loads metadata from METADATA_DB on first search if empty.
"""

import logging
from typing import Any, Optional

import numpy as np

from backend.common.vector.base import VectorStore

logger = logging.getLogger(__name__)

# Tables that need vector search
_VECTOR_TABLES = [
    "adh_table_info",
    "adh_column_metadata",
    "adh_sql_templates",
    "adh_business_terms",
    "adh_table_relations",
    "adh_sql_corrections",
]


class MemoryVectorStore(VectorStore):
    """In-memory vector store with numpy L2 distance computation."""

    def __init__(self):
        # {table_name: [{id, embedding, data}, ...]}
        self._store: dict[str, list[dict]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Load metadata from METADATA_DB if not already loaded."""
        if self._loaded:
            return

        try:
            from backend.common.config import VECTOR_DB_TYPE
            if VECTOR_DB_TYPE != "default":
                self._loaded = True
                return

            logger.info("Loading metadata into memory vector store...")
            self._load_from_metadata_db()
            self._loaded = True
            logger.info("Memory vector store loaded: %s",
                        {k: len(v) for k, v in self._store.items()})
        except Exception as e:
            logger.warning("Failed to auto-load metadata: %s", e)
            self._loaded = True  # Don't retry on every search

    def _load_from_metadata_db(self):
        """Load all vector table data from METADATA_DB."""
        from backend.common.db.metadata_db import get_metadata_connection

        for table in _VECTOR_TABLES:
            try:
                with get_metadata_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT * FROM {table} WHERE is_active = 1 OR is_active IS NULL")
                        rows = cur.fetchall()

                        records = []
                        for row in rows:
                            # Parse embedding from JSON string
                            embedding = row.get("embedding")
                            if isinstance(embedding, str):
                                try:
                                    import json
                                    embedding = json.loads(embedding)
                                except:
                                    embedding = []

                            if not embedding:
                                continue

                            # Get ID column
                            id_value = row.get("id")
                            data = {k: v for k, v in row.items() if k != "embedding"}

                            records.append({
                                "id": id_value,
                                "embedding": embedding,
                                "data": data,
                            })

                        self._store[table] = records
                        logger.debug("Loaded %d records from %s", len(records), table)

            except Exception as e:
                logger.warning("Failed to load %s: %s", table, e)
                self._store[table] = []

    def search(
        self,
        table: str,
        query_embedding: list[float],
        limit: int = 20,
        filters: Optional[dict] = None,
        output_columns: Optional[list[str]] = None,
    ) -> list[dict]:
        """Vector similarity search using numpy L2 distance."""
        self._ensure_loaded()

        try:
            records = self._store.get(table, [])
            if not records:
                return []

            # Compute L2 distances
            query_vec = np.array(query_embedding, dtype=np.float32)
            results = []

            for record in records:
                data = record["data"]

                # Apply filters
                if filters:
                    skip = False
                    for key, value in filters.items():
                        if key == "_raw":
                            continue
                        if isinstance(value, (list, tuple)):
                            if data.get(key) not in value:
                                skip = True
                                break
                        elif data.get(key) != value:
                            skip = True
                            break
                    if skip:
                        continue

                # Compute L2 distance
                emb = np.array(record["embedding"], dtype=np.float32)
                distance = float(np.linalg.norm(query_vec - emb))

                # Build result
                result = {"distance": distance}
                if output_columns:
                    for col in output_columns:
                        result[col] = data.get(col)
                else:
                    result.update(data)

                results.append(result)

            # Sort by distance and limit
            results.sort(key=lambda x: x["distance"])
            return results[:limit]

        except Exception as e:
            logger.error("Memory vector search failed on table %s: %s", table, e)
            return []

    def upsert(
        self,
        table: str,
        id_column: str,
        id_value: Any,
        data: dict,
    ) -> None:
        """Insert or update a single record."""
        self._ensure_loaded()

        if table not in self._store:
            self._store[table] = []

        embedding = data.get("embedding", [])
        data_without_emb = {k: v for k, v in data.items() if k != "embedding"}

        # Find and update existing record
        for record in self._store[table]:
            if record["data"].get(id_column) == id_value:
                record["embedding"] = embedding
                record["data"] = data_without_emb
                return

        # Insert new record
        self._store[table].append({
            "id": id_value,
            "embedding": embedding,
            "data": data_without_emb,
        })

    def upsert_batch(
        self,
        table: str,
        id_column: str,
        records: list[dict],
    ) -> int:
        """Batch insert or update records."""
        if not records:
            return 0

        for record in records:
            id_value = record.get(id_column)
            self.upsert(table, id_column, id_value, record)

        return len(records)

    def delete(
        self,
        table: str,
        id_column: str,
        id_value: Any,
    ) -> None:
        """Delete a single record."""
        self._ensure_loaded()

        if table not in self._store:
            return

        self._store[table] = [
            r for r in self._store[table]
            if r["data"].get(id_column) != id_value
        ]

    def reload(self):
        """Force reload from METADATA_DB."""
        self._loaded = False
        self._store.clear()
        self._ensure_loaded()

    def close(self):
        """Clear all data."""
        self._store.clear()
        self._loaded = False
