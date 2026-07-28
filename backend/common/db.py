"""Database Connection Pool — centralized connection management.

Provides connection pooling for MySQL/Doris databases using DBUtils.
Replaces scattered pymysql.connect() calls with pooled connections.

Usage:
    from backend.common.db.metadata_db import get_metadata_conn, get_pooled_conn

    # Context manager (auto-close)
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ...")

    # Direct connection (manual close)
    conn = get_metadata_conn()
    try:
        ...
    finally:
        conn.close()
"""

import logging
import os
from contextlib import contextmanager
from typing import Optional

import pymysql
from dbutils.pooled_db import PooledDB

from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)

logger = logging.getLogger(__name__)

# ── Connection Pool Configuration ────────────────────────────────────

POOL_MIN_CACHED = int(os.getenv("DB_POOL_MIN_CACHED", "2"))
POOL_MAX_CACHED = int(os.getenv("DB_POOL_MAX_CACHED", "10"))
POOL_MAX_SHARED = int(os.getenv("DB_POOL_MAX_SHARED", "20"))
POOL_MAX_CONNECTIONS = int(os.getenv("DB_POOL_MAX_CONNECTIONS", "50"))
POOL_BLOCKING = os.getenv("DB_POOL_BLOCKING", "true").lower() == "true"
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))  # Recycle connections after 1 hour

# ── Global Pool Instance ─────────────────────────────────────────────

_pool: Optional[PooledDB] = None
_pool_initialized = False


def _init_pool():
    """Initialize the connection pool (lazy, called once)."""
    global _pool, _pool_initialized
    if _pool_initialized:
        return

    try:
        _pool = PooledDB(
            creator=pymysql,
            mincached=POOL_MIN_CACHED,
            maxcached=POOL_MAX_CACHED,
            maxshared=POOL_MAX_SHARED,
            maxconnections=POOL_MAX_CONNECTIONS,
            blocking=POOL_BLOCKING,
            maxusage=None,  # No limit on connection reuse
            setsession=[],  # No session commands
            ping=1,  # Ping connection on every checkout
            host=DORIS_HOST,
            port=DORIS_PORT,
            user=DORIS_USER,
            password=DORIS_PASSWORD,
            database=METADATA_DB_DATABASE,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
            autocommit=True,
        )
        _pool_initialized = True
        logger.info(
            "DB pool initialized: host=%s, port=%s, db=%s, min=%d, max=%d",
            DORIS_HOST, DORIS_PORT, METADATA_DB_DATABASE,
            POOL_MIN_CACHED, POOL_MAX_CACHED,
        )
    except Exception as e:
        logger.error("Failed to initialize DB pool: %s", e)
        raise


def get_metadata_conn():
    """Get a connection from the pool.

    Returns a pooled connection that should be closed after use
    (returns to pool, not actually closed).

    Usage:
        conn = get_metadata_conn()
        try:
            ...
        finally:
            conn.close()
    """
    _init_pool()
    return _pool.connection()


@contextmanager
def get_connection():
    """Context manager for database connections.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
    """
    conn = get_metadata_conn()
    try:
        yield conn
    finally:
        conn.close()


def get_pool_stats() -> dict:
    """Get connection pool statistics."""
    if not _pool_initialized or not _pool:
        return {"initialized": False}

    return {
        "initialized": True,
        "min_cached": POOL_MIN_CACHED,
        "max_cached": POOL_MAX_CACHED,
        "max_shared": POOL_MAX_SHARED,
        "max_connections": POOL_MAX_CONNECTIONS,
        "blocking": POOL_BLOCKING,
        "timeout": POOL_TIMEOUT,
        "recycle": POOL_RECYCLE,
    }


def close_pool():
    """Close all connections in the pool."""
    global _pool, _pool_initialized
    if _pool:
        try:
            # PooledDB doesn't have an explicit close method
            # Connections will be closed when the pool is garbage collected
            _pool = None
            _pool_initialized = False
            logger.info("DB pool closed")
        except Exception as e:
            logger.warning("Error closing DB pool: %s", e)
