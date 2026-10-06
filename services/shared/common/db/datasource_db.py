"""Datasource connection management.

Provides functions to create connections to external datasources
(MySQL/Doris/PostgreSQL(SLS))
and to look up datasource configuration from the metadata database.

Usage:
    from services.shared.common.db.datasource_db import get_datasource_conn, get_datasource_by_id

    # Look up datasource by ID
    ds = get_datasource_by_id(1)

    # Create a connection from datasource config
    conn = get_datasource_conn(ds["db_type"], ds["host"], ds["port"],
                               ds["username"], ds["password"], ds.get("database_name"))
"""

import logging

import pymysql

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.crypto import decrypt_password, is_encrypted

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
        read_timeout: MySQL/Doris socket read timeout (seconds).

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
        pg_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "dbname": database or "postgres",
            "cursor_factory": psycopg2.extras.RealDictCursor,
            "connect_timeout": 10,
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
