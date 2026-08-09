"""Database Connection Layer -- MetadataDB + VectorDB + Datasource abstraction.

Usage:
    from services.shared.common.db import get_metadata_conn, get_vector_conn
    from services.shared.common.db import get_datasource_conn, get_datasource_by_id

    # Metadata connection (MySQL)
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ...")

    # Vector connection (Doris with HNSW)
    with get_vector_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ... l2_distance_approximate(...)")

    # Datasource connection
    ds = get_datasource_by_id(1)
    conn = get_datasource_conn(ds["db_type"], ds["host"], ds["port"],
                               ds["username"], ds["password"], ds.get("database_name"))
"""

from services.shared.common.db.metadata_db import (
    # Metadata pool
    get_metadata_conn, get_metadata_connection, close_metadata_pool, get_metadata_pool_stats,
    # Vector pool
    get_vector_conn, get_vector_connection, close_vector_pool,
)

from services.shared.common.db.datasource_db import (
    get_datasource_conn, get_datasource_by_id,
)

# Backward-compatible aliases for legacy code that imports from db.py
# Reuse the metadata connection pool instead of creating a separate one.

from contextlib import contextmanager


class DBConnection:
    """Context manager for database connections (legacy compatibility).

    Reuses the metadata pool from metadata_db to avoid duplicate connections.

    Usage:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_users")
                rows = cur.fetchall()
    """

    def __enter__(self):
        self.conn = get_metadata_conn()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
            self.conn.close()
        return False


def execute_query(sql: str, params=None, fetchone: bool = False):
    """Execute a SELECT query and return results (legacy compatibility)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetchone:
                return cur.fetchone()
            return cur.fetchall()


def execute_write(sql: str, params=None) -> int:
    """Execute an INSERT/UPDATE/DELETE and return affected rows (legacy compatibility)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def execute_insert(sql: str, params=None) -> int:
    """Execute an INSERT and return the last insert ID (legacy compatibility)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid


__all__ = [
    # Metadata pool
    "get_metadata_conn", "get_metadata_connection", "close_metadata_pool", "get_metadata_pool_stats",
    # Vector pool
    "get_vector_conn", "get_vector_connection", "close_vector_pool",
    # Datasource
    "get_datasource_conn", "get_datasource_by_id",
    # Legacy compatibility
    "DBConnection", "execute_query", "execute_write", "execute_insert",
]
