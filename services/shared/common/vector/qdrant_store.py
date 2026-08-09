"""QdrantVectorStore — Qdrant implementation of VectorStore.

Uses Qdrant's native vector search for similarity retrieval.
Each metadata table maps to a Qdrant collection with prefix.

Usage:
    VECTOR_DB_TYPE=qdrant
    QDRANT_HOST=localhost
    QDRANT_PORT=6333
"""

import logging
import uuid
from typing import Any, Optional

from services.shared.common.vector.base import VectorStore

logger = logging.getLogger(__name__)

# Tables that need Qdrant collections (same as memory_store._VECTOR_TABLES)
_VECTOR_TABLES = [
    "adh_table_info",
    "adh_column_metadata",
    "adh_sql_templates",
    "adh_business_terms",
    "adh_table_relations",
    "adh_sql_corrections",
]


def _table_to_collection(prefix: str, table: str) -> str:
    """Convert table name to Qdrant collection name."""
    return f"{prefix}{table}"


def _id_to_point_id(table: str, id_value: Any) -> str:
    """Convert a table row ID to a deterministic Qdrant point UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{table}_{id_value}"))


class QdrantVectorStore(VectorStore):
    """Qdrant implementation of VectorStore."""

    def __init__(self):
        from services.shared.common.config import (
            QDRANT_HOST, QDRANT_PORT, QDRANT_API_KEY,
            QDRANT_COLLECTION_PREFIX, VECTOR_DIM,
        )

        self._host = QDRANT_HOST
        self._port = QDRANT_PORT
        self._api_key = QDRANT_API_KEY or None
        self._prefix = QDRANT_COLLECTION_PREFIX
        self._dim = VECTOR_DIM
        self._client = None
        self._initialized = False

    def _get_client(self):
        """Lazy-initialize Qdrant client."""
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(
                host=self._host,
                port=self._port,
                api_key=self._api_key,
                timeout=30,
            )
            logger.info("Qdrant client connected: %s:%s", self._host, self._port)
        return self._client

    def _ensure_collections(self):
        """Ensure all vector table collections exist in Qdrant."""
        if self._initialized:
            return

        from qdrant_client.models import Distance, VectorParams

        client = self._get_client()
        existing = {c.name for c in client.get_collections().collections}

        for table in _VECTOR_TABLES:
            name = _table_to_collection(self._prefix, table)
            if name not in existing:
                client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=self._dim,
                        distance=Distance.EUCLID,
                    ),
                )
                logger.info("Created Qdrant collection: %s", name)
            else:
                logger.debug("Qdrant collection already exists: %s", name)

        self._initialized = True

    def _build_filter(self, filters: Optional[dict] = None):
        """Convert a filter dict to Qdrant Filter object."""
        if not filters:
            return None

        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        conditions = []
        for key, value in filters.items():
            if key == "_raw":
                continue
            if isinstance(value, (list, tuple)):
                conditions.append(
                    FieldCondition(key=key, match=MatchAny(any=list(value)))
                )
            else:
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )

        return Filter(must=conditions) if conditions else None

    def search(
        self,
        table: str,
        query_embedding: list[float],
        limit: int = 20,
        filters: Optional[dict] = None,
        output_columns: Optional[list[str]] = None,
    ) -> list[dict]:
        """Vector similarity search using Qdrant."""
        try:
            self._ensure_collections()
            client = self._get_client()
            collection = _table_to_collection(self._prefix, table)

            query_filter = self._build_filter(filters)

            results = client.query_points(
                collection_name=collection,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )

            # Convert to list of dicts with distance
            rows = []
            for point in results.points:
                row = {"distance": point.score}
                if point.payload:
                    if output_columns:
                        for col in output_columns:
                            row[col] = point.payload.get(col)
                    else:
                        row.update(point.payload)
                rows.append(row)

            return rows

        except Exception as e:
            logger.error("Qdrant search failed on table %s: %s", table, e)
            return []

    def upsert(
        self,
        table: str,
        id_column: str,
        id_value: Any,
        data: dict,
    ) -> None:
        """Insert or update a single record."""
        try:
            self._ensure_collections()
            client = self._get_client()
            collection = _table_to_collection(self._prefix, table)

            embedding = data.get("embedding", [])
            payload = {k: v for k, v in data.items() if k != "embedding"}

            point_id = _id_to_point_id(table, id_value)

            from qdrant_client.models import PointStruct

            client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(id=point_id, vector=embedding, payload=payload)
                ],
            )

        except Exception as e:
            logger.error(
                "Qdrant upsert failed on table %s for %s=%s: %s",
                table, id_column, id_value, e,
            )
            raise

    def upsert_batch(
        self,
        table: str,
        id_column: str,
        records: list[dict],
    ) -> int:
        """Batch insert or update records."""
        if not records:
            return 0

        try:
            self._ensure_collections()
            client = self._get_client()
            collection = _table_to_collection(self._prefix, table)

            from qdrant_client.models import PointStruct

            points = []
            for record in records:
                id_value = record.get(id_column)
                embedding = record.get("embedding", [])
                payload = {k: v for k, v in record.items() if k != "embedding"}
                point_id = _id_to_point_id(table, id_value)
                points.append(
                    PointStruct(id=point_id, vector=embedding, payload=payload)
                )

            # Upsert in batches of 100
            batch_size = 100
            for i in range(0, len(points), batch_size):
                batch = points[i : i + batch_size]
                client.upsert(collection_name=collection, points=batch)

            return len(records)

        except Exception as e:
            logger.error("Qdrant batch upsert failed on table %s: %s", table, e)
            raise

    def delete(
        self,
        table: str,
        id_column: str,
        id_value: Any,
    ) -> None:
        """Delete a single record."""
        try:
            self._ensure_collections()
            client = self._get_client()
            collection = _table_to_collection(self._prefix, table)

            point_id = _id_to_point_id(table, id_value)
            client.delete(collection_name=collection, points_selector=[point_id])

        except Exception as e:
            logger.error(
                "Qdrant delete failed on table %s for %s=%s: %s",
                table, id_column, id_value, e,
            )
            raise

    def close(self):
        """Close the Qdrant client connection."""
        if self._client:
            self._client.close()
            self._client = None
            self._initialized = False
            logger.info("Qdrant client closed")
