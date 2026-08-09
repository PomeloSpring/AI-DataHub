"""Datasource connection management.

Provides functions to create connections to external datasources (MySQL, Doris, Elasticsearch)
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

# Try to import elasticsearch
try:
    from elasticsearch import Elasticsearch
    HAS_ELASTICSEARCH = True
except ImportError:
    HAS_ELASTICSEARCH = False


def get_datasource_conn(db_type: str, host: str, port: int,
                        user: str, password: str, database: str = None):
    """Create a connection to a datasource based on db_type.

    Args:
        db_type: Database type ("mysql", "doris", "elasticsearch").
        host: Database host.
        port: Database port.
        user: Database username.
        password: Database password.
        database: Database name (optional for ES).

    Returns:
        A pymysql connection for MySQL/Doris, or an Elasticsearch client for ES.

    Raises:
        ValueError: If elasticsearch package is not installed for ES connections.
    """
    if db_type == "elasticsearch":
        if not HAS_ELASTICSEARCH:
            raise ValueError("Elasticsearch library not installed. Run: pip install elasticsearch")
        # Return Elasticsearch client - use https if ssl is enabled
        es_url = f"http://{host}:{port}"
        es_kwargs = {"hosts": [es_url], "request_timeout": 30, "meta_header": False}
        if user and password:
            es_kwargs["basic_auth"] = (user, password)
        elif user:
            es_kwargs["basic_auth"] = (user, "")
        return Elasticsearch(**es_kwargs)
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
            "read_timeout": 30,
        }
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
