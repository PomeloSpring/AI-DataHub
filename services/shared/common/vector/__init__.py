"""Vector Store Layer — VectorStore abstraction for RAG retrieval.

Usage:
    from services.shared.common.vector import get_vector_store

    store = get_vector_store()
    results = store.search(
        table="adh_table_info",
        query_embedding=embedding,
        limit=20,
        filters={"is_active": 1},
        output_columns=["table_name", "table_comment"],
    )

VECTOR_DB_TYPE options:
    - "default": In-memory vector store (numpy L2 distance)
    - "doris":   Doris HNSW vector index
    - "qdrant":  Qdrant vector database
"""

from services.shared.common.vector.base import VectorStore
from services.shared.common.vector.memory_store import MemoryVectorStore
from services.shared.common.vector.doris_store import DorisVectorStore
from services.shared.common.vector.qdrant_store import QdrantVectorStore

_store: VectorStore = None


def get_vector_store() -> VectorStore:
    """Get or create the global VectorStore instance."""
    global _store
    if _store is None:
        from services.shared.common.config import VECTOR_DB_TYPE
        if VECTOR_DB_TYPE == "doris":
            _store = DorisVectorStore()
        elif VECTOR_DB_TYPE == "qdrant":
            _store = QdrantVectorStore()
        else:
            # Default: in-memory vector store
            _store = MemoryVectorStore()
    return _store


def close_vector_store():
    """Close the vector store connection."""
    global _store
    if _store:
        _store.close()
        _store = None


__all__ = [
    "VectorStore", "MemoryVectorStore", "DorisVectorStore", "QdrantVectorStore",
    "get_vector_store", "close_vector_store",
]
