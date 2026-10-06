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
import os
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
POOL_TIMEOUT = 10
POOL_RECYCLE = 3600

# -- Network / socket timeouts (seconds) ------------------------------------
# 元数据库是跨公网远程实例(47.103.50.8)，空闲连接易被链路(防火墙/NAT)静默断开成
# “半开”状态。复用该连接时 DBUtils 的 ping=1 健康检查会在死 socket 上阻塞读，直到
# read_timeout 才失败——历史缺陷：单个卡住的鉴权查询(resolve_current_user→get_user_by_id)
# 连带把整页请求拖成 15~30s pending。这里把超时收紧，让死连接在秒级快速失败，
# 由池自动重建新连接(重建仅 ~100ms)透明重试，而不是长时间挂起。
# read_timeout 覆盖 ping 与查询读；元数据读结果集都很小(实测 <150ms)，留足余量。
# 可按环境用 METADATA_DB_*_TIMEOUT 覆盖，便于对超慢读场景调优。
CONN_CONNECT_TIMEOUT = float(os.getenv("METADATA_DB_CONNECT_TIMEOUT", "5"))
CONN_READ_TIMEOUT = float(os.getenv("METADATA_DB_READ_TIMEOUT", "5"))
CONN_WRITE_TIMEOUT = float(os.getenv("METADATA_DB_WRITE_TIMEOUT", "15"))


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
                connect_timeout=CONN_CONNECT_TIMEOUT,
                read_timeout=CONN_READ_TIMEOUT,
                write_timeout=CONN_WRITE_TIMEOUT,
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
