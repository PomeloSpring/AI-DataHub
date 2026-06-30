"""Database Connection Layer — MetadataDB + VectorDB abstraction.

Usage:
    from backend.common.db import get_metadata_conn, get_vector_conn

    # Metadata connection (MySQL)
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ...")

    # Vector connection (Doris with HNSW)
    with get_vector_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ... l2_distance_approximate(...)")
"""

from backend.common.db.metadata_db import (
    get_metadata_conn, get_metadata_connection, close_metadata_pool,
    get_vector_conn, get_vector_connection, close_vector_pool,
)

__all__ = [
    "get_metadata_conn", "get_metadata_connection", "close_metadata_pool",
    "get_vector_conn", "get_vector_connection", "close_vector_pool",
]
