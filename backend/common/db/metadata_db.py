"""MetadataDB — abstract base for metadata database connections.

Provides a unified interface for metadata storage (MySQL or Doris).
The actual implementation is selected based on METADATA_DB_TYPE config.

Usage:
    from backend.common.db.metadata_db import get_metadata_conn, get_metadata_connection

    # Direct connection (manual close)
    conn = get_metadata_conn()
    try:
        ...
    finally:
        conn.close()

    # Context manager (auto-close)
    with get_metadata_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ...")
"""

import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Optional

import pymysql
from dbutils.pooled_db import PooledDB

from backend.common.config import (
    METADATA_DB_TYPE,
    METADATA_DB_HOST, METADATA_DB_PORT,
    METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
    VECTOR_DB_TYPE,
    VECTOR_DB_HOST, VECTOR_DB_PORT,
    VECTOR_DB_USER, VECTOR_DB_PASSWORD, VECTOR_DB_DATABASE,
)

logger = logging.getLogger(__name__)

# ── Connection Pool Configuration ────────────────────────────────────

POOL_MIN_CACHED = 2
POOL_MAX_CACHED = 10
POOL_MAX_SHARED = 20
POOL_MAX_CONNECTIONS = 50
POOL_BLOCKING = True
POOL_TIMEOUT = 30
POOL_RECYCLE = 3600


class MetadataDB(ABC):
    """Abstract base class for metadata database connections."""

    @abstractmethod
    def get_conn(self):
        """Get a raw connection."""
        ...

    @abstractmethod
    def get_connection(self):
        """Get a context-managed connection."""
        ...

    @abstractmethod
    def close_pool(self):
        """Close the connection pool."""
        ...

    @abstractmethod
    def get_pool_stats(self) -> dict:
        """Get connection pool statistics."""
        ...


class MySQLMetadataDB(MetadataDB):
    """MySQL implementation of MetadataDB using DBUtils connection pool."""

    def __init__(self, host: str = None, port: int = None, user: str = None,
                 password: str = None, database: str = None):
        self._pool: Optional[PooledDB] = None
        self._pool_initialized = False
        # Allow overriding config per instance (used by vector DB pool)
        self._host = host or METADATA_DB_HOST
        self._port = port or METADATA_DB_PORT
        self._user = user or METADATA_DB_USER
        self._password = password or METADATA_DB_PASSWORD
        self._database = database or METADATA_DB_DATABASE

    def _init_pool(self):
        """Initialize the connection pool (lazy, called once)."""
        if self._pool_initialized:
            return

        try:
            self._pool = PooledDB(
                creator=pymysql,
                mincached=POOL_MIN_CACHED,
                maxcached=POOL_MAX_CACHED,
                maxshared=POOL_MAX_SHARED,
                maxconnections=POOL_MAX_CONNECTIONS,
                blocking=POOL_BLOCKING,
                maxusage=None,
                setsession=[],
                ping=1,
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
                autocommit=True,
            )
            self._pool_initialized = True
            logger.info(
                "MetadataDB pool initialized: host=%s, port=%s, db=%s, min=%d, max=%d",
                self._host, self._port, self._database,
                POOL_MIN_CACHED, POOL_MAX_CACHED,
            )
        except Exception as e:
            logger.error("Failed to initialize MetadataDB pool: %s", e)
            raise

    def get_conn(self):
        """Get a connection from the pool."""
        self._init_pool()
        return self._pool.connection()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = self.get_conn()
        try:
            yield conn
        finally:
            conn.close()

    def close_pool(self):
        """Close all connections in the pool."""
        if self._pool:
            self._pool = None
            self._pool_initialized = False
            logger.info("MetadataDB pool closed")

    def get_pool_stats(self) -> dict:
        """Get connection pool statistics."""
        if not self._pool_initialized or not self._pool:
            return {"initialized": False}
        return {
            "initialized": True,
            "type": "mysql",
            "host": self._host,
            "port": self._port,
            "database": self._database,
            "min_cached": POOL_MIN_CACHED,
            "max_cached": POOL_MAX_CACHED,
            "max_connections": POOL_MAX_CONNECTIONS,
        }


class DorisMetadataDB(MySQLMetadataDB):
    """Doris implementation — uses same pymysql protocol as MySQL.

    Doris supports MySQL wire protocol, so this is functionally identical
    to MySQLMetadataDB but with different default config.
    """

    def get_pool_stats(self) -> dict:
        stats = super().get_pool_stats()
        stats["type"] = "doris"
        return stats


class SQLiteMetadataDB(MetadataDB):
    """SQLite implementation of MetadataDB for lightweight deployments."""

    def __init__(self, db_path: str = None):
        import sqlite3
        from pathlib import Path

        self._db_path = db_path or "data/metadata.db"
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        logger.info("SQLiteMetadataDB initialized at %s", self._db_path)

    def _get_conn(self):
        import sqlite3
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def get_conn(self):
        """Get a SQLite connection (returns a wrapper with dict-like cursor)."""
        return _SQLiteConnectionWrapper(self._get_conn())

    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = self.get_conn()
        try:
            yield conn
        finally:
            pass  # SQLite connection is shared, don't close

    def close_pool(self):
        """Close the SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("SQLiteMetadataDB connection closed")

    def get_pool_stats(self) -> dict:
        return {"type": "sqlite", "db_path": self._db_path}


class _SQLiteConnectionWrapper:
    """Wrapper to make sqlite3 connection compatible with pymysql interface."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _SQLiteCursorWrapper(self._conn)

    def commit(self):
        self._conn.commit()

    def close(self):
        pass  # Shared connection, don't close

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _SQLiteCursorWrapper:
    """Wrapper to make sqlite3 cursor compatible with pymysql DictCursor."""

    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor()

    def execute(self, query, params=None):
        # Convert MySQL-style %s placeholders to SQLite ? placeholders
        import re
        query = re.sub(r'%s', '?', query)
        if params:
            self._cursor.execute(query, params)
        else:
            self._cursor.execute(query)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self):
        return [dict(row) for row in self._cursor.fetchall()]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._cursor.close()


# ── Global Singleton ─────────────────────────────────────────────────

_db: Optional[MetadataDB] = None


def _get_db() -> MetadataDB:
    """Get or create the global MetadataDB instance."""
    global _db
    if _db is None:
        if METADATA_DB_TYPE == "doris":
            _db = DorisMetadataDB()
        elif METADATA_DB_TYPE == "sqlite":
            _db = SQLiteMetadataDB()
        else:
            _db = MySQLMetadataDB()
    return _db


def get_metadata_conn():
    """Get a metadata database connection from the pool.

    Returns a pooled connection that should be closed after use.
    """
    return _get_db().get_conn()


@contextmanager
def get_metadata_connection():
    """Context manager for metadata database connections.

    Usage:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
    """
    with _get_db().get_connection() as conn:
        yield conn


def close_metadata_pool():
    """Close the metadata database connection pool."""
    global _db
    if _db:
        _db.close_pool()
        _db = None


def get_metadata_pool_stats() -> dict:
    """Get metadata database connection pool statistics."""
    return _get_db().get_pool_stats()


# ── Vector Database Connection Pool (Doris) ──────────────────────────
# Separate from metadata pool — vector search requires Doris with HNSW index.
# Metadata may be in MySQL; vectors are always in Doris.

_vec_db: Optional[MetadataDB] = None


def _get_vec_db() -> MetadataDB:
    """Get or create the global VectorDB instance (uses VECTOR_DB_* config)."""
    global _vec_db
    if _vec_db is None:
        if VECTOR_DB_TYPE == "doris":
            _vec_db = DorisMetadataDB(
                host=VECTOR_DB_HOST, port=VECTOR_DB_PORT,
                user=VECTOR_DB_USER, password=VECTOR_DB_PASSWORD,
                database=VECTOR_DB_DATABASE,
            )
        elif VECTOR_DB_TYPE == "default":
            # In-memory vector store doesn't need a DB connection
            # Return a dummy that will be handled by MemoryVectorStore
            return None
        else:
            _vec_db = MySQLMetadataDB(
                host=VECTOR_DB_HOST, port=VECTOR_DB_PORT,
                user=VECTOR_DB_USER, password=VECTOR_DB_PASSWORD,
                database=VECTOR_DB_DATABASE,
            )
    return _vec_db


def get_vector_conn():
    """Get a vector database connection from the pool."""
    db = _get_vec_db()
    if db is None:
        raise RuntimeError("Vector DB is in-memory mode, use get_vector_store() instead")
    return db.get_conn()


@contextmanager
def get_vector_connection():
    """Context manager for vector database connections."""
    db = _get_vec_db()
    if db is None:
        raise RuntimeError("Vector DB is in-memory mode, use get_vector_store() instead")
    with db.get_connection() as conn:
        yield conn


def close_vector_pool():
    """Close the vector database connection pool."""
    global _vec_db
    if _vec_db:
        _vec_db.close_pool()
        _vec_db = None
