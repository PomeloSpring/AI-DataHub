"""MetadataDB -- abstract base for metadata database connections.

Provides a unified interface for metadata storage (MySQL or Doris).
The actual implementation is selected based on METADATA_DB_TYPE config.

Supported METADATA_DB_TYPE values:
    - "mysql" (default): MySQL 8.0 via pymysql + DBUtils pool
    - "doris": Apache Doris (MySQL wire protocol compatible)

Usage:
    from services.shared.common.db.metadata_db import get_metadata_conn, get_metadata_connection

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

from services.shared.common.config import (
    METADATA_DB_TYPE,
    METADATA_DB_HOST, METADATA_DB_PORT,
    METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
)

logger = logging.getLogger(__name__)

# -- Connection Pool Configuration ------------------------------------------

POOL_MIN_CACHED = 1
POOL_MAX_CACHED = 3
POOL_MAX_SHARED = 6
POOL_MAX_CONNECTIONS = 8
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
    """Doris implementation -- uses same pymysql protocol as MySQL.

    Doris supports MySQL wire protocol, so this is functionally identical
    to MySQLMetadataDB but with different default config.
    """

    def get_pool_stats(self) -> dict:
        stats = super().get_pool_stats()
        stats["type"] = "doris"
        return stats


# -- Global Singleton -------------------------------------------------------

_db: Optional[MetadataDB] = None


def _get_db() -> MetadataDB:
    """Get or create the global MetadataDB instance."""
    global _db
    if _db is None:
        if METADATA_DB_TYPE == "doris":
            _db = DorisMetadataDB()
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
