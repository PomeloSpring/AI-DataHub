"""Vector Database connection pool (Doris).

Provides a separate connection pool for the Doris vector database,
distinct from the metadata MySQL pool.

Usage:
    from vector_db import get_vector_connection, VectorDBConnection

    # Context manager
    with get_vector_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ...")

    # Direct pool connection
    conn = get_vector_pool().connection()
    try:
        ...
    finally:
        conn.close()
"""

import logging
from contextlib import contextmanager
from typing import Optional

import pymysql
from dbutils.pooled_db import PooledDB

from services.shared.common.config import (
    VECTOR_DB_TYPE,
    VECTOR_DB_HOST,
    VECTOR_DB_PORT,
    VECTOR_DB_USER,
    VECTOR_DB_PASSWORD,
    VECTOR_DB_DATABASE,
)

logger = logging.getLogger(__name__)

# ── Pool Configuration ────────────────────────────────────────────────

POOL_MIN_CACHED = 2
POOL_MAX_CACHED = 10
POOL_MAX_SHARED = 20
POOL_MAX_CONNECTIONS = 50
POOL_BLOCKING = True
POOL_TIMEOUT = 30
POOL_RECYCLE = 3600

# ── Global Pool ───────────────────────────────────────────────────────

_pool: Optional[PooledDB] = None
_pool_initialized = False


def _init_pool() -> PooledDB:
    """Initialize the Doris vector connection pool (lazy)."""
    global _pool, _pool_initialized

    if _pool_initialized and _pool is not None:
        return _pool

    try:
        _pool = PooledDB(
            creator=pymysql,
            mincached=POOL_MIN_CACHED,
            maxcached=POOL_MAX_CACHED,
            maxshared=POOL_MAX_SHARED,
            maxconnections=POOL_MAX_CONNECTIONS,
            blocking=POOL_BLOCKING,
            maxusage=None,
            setsession=[],
            ping=1,
            host=VECTOR_DB_HOST,
            port=VECTOR_DB_PORT,
            user=VECTOR_DB_USER,
            password=VECTOR_DB_PASSWORD,
            database=VECTOR_DB_DATABASE,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
            autocommit=True,
        )
        _pool_initialized = True
        logger.info(
            "Vector DB pool initialized: host=%s, port=%s, db=%s, min=%d, max=%d",
            VECTOR_DB_HOST, VECTOR_DB_PORT, VECTOR_DB_DATABASE,
            POOL_MIN_CACHED, POOL_MAX_CACHED,
        )
    except Exception as e:
        logger.error("Failed to initialize Vector DB pool: %s", e)
        raise

    return _pool


def get_vector_pool() -> PooledDB:
    """Get the global Doris vector connection pool."""
    return _init_pool()


@contextmanager
def get_vector_connection():
    """Context manager for vector database connections.

    Usage:
        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
    """
    pool = get_vector_pool()
    conn = pool.connection()
    try:
        yield conn
    finally:
        conn.close()


def close_vector_pool():
    """Close the vector database connection pool."""
    global _pool, _pool_initialized
    if _pool:
        _pool.close()
        _pool = None
        _pool_initialized = False
        logger.info("Vector DB pool closed")


def get_vector_pool_stats() -> dict:
    """Get vector database connection pool statistics."""
    if not _pool_initialized or _pool is None:
        return {"initialized": False}
    return {
        "initialized": True,
        "type": VECTOR_DB_TYPE,
        "host": VECTOR_DB_HOST,
        "port": VECTOR_DB_PORT,
        "database": VECTOR_DB_DATABASE,
        "min_cached": POOL_MIN_CACHED,
        "max_cached": POOL_MAX_CACHED,
        "max_connections": POOL_MAX_CONNECTIONS,
    }
