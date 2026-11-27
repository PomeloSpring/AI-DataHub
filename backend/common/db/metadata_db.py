"""MetadataDB -- abstract base for metadata database connections.

Provides a unified interface for metadata storage (MySQL or Doris).
The actual implementation is selected based on METADATA_DB_TYPE config.

Supported METADATA_DB_TYPE values:
    - "mysql" (default): MySQL 8.0 via pymysql + DBUtils pool
    - "doris": Apache Doris (MySQL wire protocol compatible)

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
import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Optional

import pymysql
from dbutils.pooled_db import PooledDB, TooManyConnectionsError

from backend.common.config import (
    METADATA_DB_TYPE,
    METADATA_DB_HOST, METADATA_DB_PORT,
    METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
)

logger = logging.getLogger(__name__)

# -- Connection Pool Configuration ------------------------------------------
# 池大小与全部超时均可由 backend/.env 覆盖（键名见 _pool_settings 内注释）；
# 下列为缺省值。解析/校验统一走 _pool_settings()（每次建池时调用），
# 配置非法直接抛 ValueError（fail-loud），绝不静默回退默认值。
POOL_MIN_CACHED = 1
POOL_MAX_CACHED = 3
# maxshared 仅当驱动 threadsafety>1 才生效；pymysql=1 时 DBUtils 会忽略它，
# 故保留常量但不开配置口（开一个不生效的旋钮 = 空承诺）。
POOL_MAX_SHARED = 6
POOL_MAX_CONNECTIONS = 8
POOL_BLOCKING = True
# 池等待预算（秒）：池满时最多等这么久，超时抛 TooManyConnectionsError（显式失败，
# 不再无限挂起——历史事故见 core/viz_query_pool 模块说明）；0 = 不限时等待（旧行为）。
POOL_TIMEOUT = 10.0


def _env_int(name: str, default: int) -> int:
    """读整型环境变量；未配置用默认值，配错显式报错（不静默回退）。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是整数，当前值: {raw!r}") from None


def _env_float(name: str, default: float) -> float:
    """读浮点型环境变量；未配置用默认值，配错显式报错（不静默回退）。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是数字，当前值: {raw!r}") from None


def _pool_settings() -> dict:
    """解析连接池与超时配置（backend/.env），并做交叉校验。

    可配键（均在 backend/.env）：
        METADATA_DB_POOL_MIN_CACHED      池初始/最小空闲连接（默认 1）
        METADATA_DB_POOL_MAX_CACHED      最大空闲连接（默认 3）
        METADATA_DB_POOL_MAX_CONNECTIONS 池连接总数上限（默认 8）
        METADATA_DB_POOL_TIMEOUT         等连接预算秒数（默认 10，0=不限时）
        METADATA_DB_CONNECT_TIMEOUT      建连超时秒数（默认 5）
        METADATA_DB_READ_TIMEOUT         读超时秒数（默认 5）
        METADATA_DB_WRITE_TIMEOUT        写超时秒数（默认 15）

    网络超时默认值收紧的原因（历史缺陷）：元数据库是跨公网远程实例，空闲连接易被
    链路(防火墙/NAT)静默断成“半开”状态；复用时 DBUtils 的 ping=1 健康检查会在死
    socket 上阻塞读直到 read_timeout 才失败——曾把单个卡住的鉴权查询连带成整页
    15~30s pending。收紧后死连接秒级快速失败，由池自动重建(仅 ~100ms)透明重试。
    read_timeout 覆盖 ping 与查询读；元数据读结果集都很小(实测 <150ms)，留足余量。

    交叉校验（DBUtils 对越界值会静默改写，如 maxconnections 被抬到 maxcached，
    配置与实际生效值分叉正是要杜绝的）：这里一律显式拒绝。
    """
    cfg = {
        "mincached": _env_int("METADATA_DB_POOL_MIN_CACHED", POOL_MIN_CACHED),
        "maxcached": _env_int("METADATA_DB_POOL_MAX_CACHED", POOL_MAX_CACHED),
        "maxshared": POOL_MAX_SHARED,
        "maxconnections": _env_int("METADATA_DB_POOL_MAX_CONNECTIONS", POOL_MAX_CONNECTIONS),
        "blocking": POOL_BLOCKING,
        "pool_timeout": _env_float("METADATA_DB_POOL_TIMEOUT", POOL_TIMEOUT),
        "connect_timeout": _env_float("METADATA_DB_CONNECT_TIMEOUT", 5.0),
        "read_timeout": _env_float("METADATA_DB_READ_TIMEOUT", 5.0),
        "write_timeout": _env_float("METADATA_DB_WRITE_TIMEOUT", 15.0),
    }
    if cfg["mincached"] < 0:
        raise ValueError(f"METADATA_DB_POOL_MIN_CACHED 不能为负数: {cfg['mincached']}")
    if cfg["maxcached"] < 0:
        raise ValueError(f"METADATA_DB_POOL_MAX_CACHED 不能为负数: {cfg['maxcached']}")
    if cfg["maxconnections"] < 1:
        raise ValueError(f"METADATA_DB_POOL_MAX_CONNECTIONS 必须 >= 1: {cfg['maxconnections']}")
    # mincached 在建池时会真的开连接，超过总数上限会直接卡死在建池阶段；
    # maxcached 超上限则 DBUtils 会静默抬高 maxconnections——两者都必须显式拒绝。
    if cfg["mincached"] > cfg["maxconnections"]:
        raise ValueError(
            f"METADATA_DB_POOL_MIN_CACHED({cfg['mincached']}) 不能大于 "
            f"METADATA_DB_POOL_MAX_CONNECTIONS({cfg['maxconnections']})"
        )
    if cfg["maxcached"] > cfg["maxconnections"]:
        raise ValueError(
            f"METADATA_DB_POOL_MAX_CACHED({cfg['maxcached']}) 不能大于 "
            f"METADATA_DB_POOL_MAX_CONNECTIONS({cfg['maxconnections']})"
        )
    if cfg["pool_timeout"] < 0:
        raise ValueError(f"METADATA_DB_POOL_TIMEOUT 不能为负数: {cfg['pool_timeout']}")
    for env_key, cfg_key in (
        ("METADATA_DB_CONNECT_TIMEOUT", "connect_timeout"),
        ("METADATA_DB_READ_TIMEOUT", "read_timeout"),
        ("METADATA_DB_WRITE_TIMEOUT", "write_timeout"),
    ):
        if cfg[cfg_key] <= 0:
            raise ValueError(f"{env_key} 必须为正数: {cfg[cfg_key]}")
    return cfg


# 启动即校验（fail-fast）：配置写错在导入时报错，而不是等第一个请求才暴露。
_pool_settings()


class _TimeoutPooledDB(PooledDB):
    """带等待预算的连接池。

    DBUtils 的 blocking 等待是 Condition.wait() **无限期**等待——池被占满时请求
    无限挂起（历史事故：8 路并发刷新互相等连接，15 分钟不返回，见 viz_query_pool）。
    这里把 METADATA_DB_POOL_TIMEOUT 接到等待上：超时抛 TooManyConnectionsError，
    错误文案带上限与调优入口，属显式失败而非静默降级。

    注意 pool_timeout 必须在此拦截，不能透传给 super().__init__ —— PooledDB 会把
    未消费的 kwargs 转交给 pymysql.connect，pymysql 没有该参数会直接 TypeError。
    """

    def __init__(self, *args, pool_timeout: float = 0.0, **kwargs):
        self._pool_timeout = float(pool_timeout)
        super().__init__(*args, **kwargs)

    def _wait_lock(self) -> None:
        if not self._blocking:
            raise TooManyConnectionsError("元数据库连接池已满（blocking 已关闭）")
        if self._pool_timeout <= 0:
            self._lock.wait()  # 显式配置 0 = 保留不限时等待的旧行为
            return
        if not self._lock.wait(self._pool_timeout):
            raise TooManyConnectionsError(
                f"等空闲连接超过 {self._pool_timeout:g}s 仍无连接"
                f"（上限 {self._maxconnections}）：可调大 METADATA_DB_POOL_MAX_CONNECTIONS，"
                "或排查是否有连接未归还（conn.close() 漏调）"
            )


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
            cfg = _pool_settings()
            self._pool_cfg = cfg
            self._pool = _TimeoutPooledDB(
                creator=pymysql,
                mincached=cfg["mincached"],
                maxcached=cfg["maxcached"],
                maxshared=cfg["maxshared"],
                maxconnections=cfg["maxconnections"],
                blocking=cfg["blocking"],
                pool_timeout=cfg["pool_timeout"],
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
                connect_timeout=cfg["connect_timeout"],
                read_timeout=cfg["read_timeout"],
                write_timeout=cfg["write_timeout"],
                autocommit=True,
            )
            self._pool_initialized = True
            logger.info(
                "MetadataDB pool initialized: host=%s, port=%s, db=%s, min=%d, max=%d, "
                "max_conn=%d, wait_timeout=%gs, conn_timeout=%gs/%gs/%gs",
                self._host, self._port, self._database,
                cfg["mincached"], cfg["maxcached"], cfg["maxconnections"],
                cfg["pool_timeout"],
                cfg["connect_timeout"], cfg["read_timeout"], cfg["write_timeout"],
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
        cfg = getattr(self, "_pool_cfg", {})
        return {
            "initialized": True,
            "type": "mysql",
            "host": self._host,
            "port": self._port,
            "database": self._database,
            "min_cached": cfg.get("mincached", POOL_MIN_CACHED),
            "max_cached": cfg.get("maxcached", POOL_MAX_CACHED),
            "max_connections": cfg.get("maxconnections", POOL_MAX_CONNECTIONS),
            "wait_timeout": cfg.get("pool_timeout", POOL_TIMEOUT),
            "connect_timeout": cfg.get("connect_timeout"),
            "read_timeout": cfg.get("read_timeout"),
            "write_timeout": cfg.get("write_timeout"),
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
