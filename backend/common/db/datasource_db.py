"""Datasource connection management.

Provides functions to create connections to external datasources
(MySQL/Doris/PostgreSQL(SLS))
and to look up datasource configuration from the metadata database.

Usage:
    from backend.common.db.datasource_db import get_datasource_conn, get_datasource_by_id

    # Look up datasource by ID
    ds = get_datasource_by_id(1)

    # Create a connection from datasource config
    conn = get_datasource_conn(ds["db_type"], ds["host"], ds["port"],
                               ds["username"], ds["password"], ds.get("database_name"))
"""

import logging

import pymysql

from backend.common.db.metadata_db import get_metadata_conn
from backend.common.crypto import decrypt_password, is_encrypted

logger = logging.getLogger(__name__)

# Try to import psycopg2 (PostgreSQL / SLS-PG 协议)
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# 走 PG 协议/语法的数据源类型(SLS 以 PG 兼容协议接入)
POSTGRES_DB_TYPES = ("postgres", "postgresql", "pg", "sls")


def get_datasource_conn(db_type: str, host: str, port: int,
                        user: str, password: str, database: str = None,
                        ssl: bool = False, ssl_mode: str = None,
                        read_timeout: int = 30):
    """Create a connection to a datasource based on db_type.

    Args:
        db_type: Database type ("mysql", "doris", "postgres", "postgresql",
                 "pg", "sls").
        host: Database host.
        port: Database port.
        user: Database username.
        password: Database password.
        database: Database name.
        ssl: Legacy SSL flag (still honored when ssl_mode is unset).
        ssl_mode: "disabled" | "preferred" | "required"; overrides ssl.
        read_timeout: Query bound in seconds. For MySQL/Doris it is the socket
            read timeout; for PostgreSQL/SLS it is pushed down as a server-side
            ``statement_timeout`` (psycopg2 has no socket read timeout).

    Returns:
        A pymysql/psycopg2 connection for SQL datasources.

    Raises:
        ValueError: If the required driver package is not installed.
    """
    db_type = (db_type or "mysql").lower()
    # 归一化有效 ssl_mode(MySQL 列可能回传 bytes)
    if isinstance(ssl_mode, bytes):
        ssl_mode = ssl_mode.decode("utf-8", errors="replace")
    effective_ssl_mode = ssl_mode or "disabled"
    if effective_ssl_mode == "disabled" and ssl:
        effective_ssl_mode = "required"

    if db_type in POSTGRES_DB_TYPES:
        if not HAS_PSYCOPG2:
            raise ValueError("psycopg2 not installed. Run: pip install psycopg2-binary")
        # psycopg2 没有 MySQL 那样的 socket read_timeout 参数；旧写法只设 connect_timeout，
        # 于是**握手完成之后的查询阶段完全无上界**。跨境源(如新加坡 arms-prd-en)一旦抖动，
        # 调用会无限挂在读上，前端超时 abort 也不会释放这条连接，重试只会越堆越多(看板"查询超时"根因)。
        # 这里用两个对 PG 协议真实有效的上界把它收口(已实机探测 SLS 端点接受这些 libpq 参数)：
        #   - statement_timeout：服务端在 read_timeout 秒主动取消失控查询 → psycopg2 抛 QueryCanceled，
        #     沿既有 fail-loud 通道回可读错误(不静默换源、不假成功，符合 no-silent-degradation)；
        #   - keepalives：探测半死/被链路(NAT/防火墙)静默断开的跨境 socket，避免复用死连接时阻塞在读上。
        _rt = int(read_timeout or 30)
        pg_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "dbname": database or "postgres",
            "cursor_factory": psycopg2.extras.RealDictCursor,
            "connect_timeout": 10,
            "options": f"-c statement_timeout={_rt * 1000}",
            "keepalives": 1,
            "keepalives_idle": max(5, _rt // 3),
            "keepalives_interval": 5,
            "keepalives_count": 3,
        }
        if effective_ssl_mode == "required":
            pg_kwargs["sslmode"] = "require"
        elif effective_ssl_mode == "preferred":
            pg_kwargs["sslmode"] = "prefer"
        return psycopg2.connect(**pg_kwargs)
    else:
        # MySQL/Doris - return pymysql connection
        conn_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": 10,
            "read_timeout": read_timeout,
        }
        if effective_ssl_mode == "required":
            conn_kwargs["ssl"] = {"ssl_mode": "REQUIRED"}
            conn_kwargs["ssl_disabled"] = False
        elif effective_ssl_mode == "preferred":
            conn_kwargs["ssl"] = {"ssl_mode": "PREFERRED"}
            conn_kwargs["ssl_disabled"] = False
        return pymysql.connect(**conn_kwargs)


def get_datasource_by_id(ds_id: int) -> dict:
    """Look up a datasource configuration by ID.

    Queries the adh_datasources table and decrypts the password if encrypted.

    Args:
        ds_id: The datasource ID to look up.

    Returns:
        A dict with datasource configuration, or None if not found.
    """
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_datasources WHERE id = %s", (ds_id,))
            row = cur.fetchone()
            if row and row.get("password"):
                password = row["password"]
                if is_encrypted(password):
                    try:
                        row["password"] = decrypt_password(password)
                    except ValueError as e:
                        # Log but continue - will fail at connection time
                        logger.warning(
                            "Failed to decrypt password for datasource %s: %s", ds_id, e
                        )
            return row
    finally:
        conn.close()


def get_datasource_by_name(name: str) -> dict:
    """Look up a datasource by global-unique name (uk_datasource_name).

    三段式限定名 `ds.db.table` 的 catalog 段解析用；数据源 name 全局唯一、
    删除重建后 id 会变而 name 不变（ontology-modeling §2），故跨源引用以名为准。
    未命中返回 None（调用方必须 fail-closed 拒绝，不得猜源）。
    """
    if not name:
        return None
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_datasources WHERE name = %s", (name,))
            row = cur.fetchone()
            if row and row.get("password"):
                password = row["password"]
                if is_encrypted(password):
                    try:
                        row["password"] = decrypt_password(password)
                    except ValueError as e:
                        logger.warning(
                            "Failed to decrypt password for datasource %s: %s", name, e
                        )
            return row
    finally:
        conn.close()
