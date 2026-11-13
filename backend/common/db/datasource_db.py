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

import json
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

# ── 连接超时：所有拿数据源连接的地方共用一份优先级 ──────────────────────
# 过去这些值写死在各连接器与工厂里（connect 10s / read 30s、SLS 更是完全不看
# 调用侧预算），跨源体量与网络路径不同就只能改代码。现在按数据源可配
# （「数据源」页高级配置→ `adh_datasources.extra_config`），缺省等于旧写死值：
# 未配置的源行为逐字不变。
DEFAULT_CONNECT_TIMEOUT_SEC = 10
DEFAULT_READ_TIMEOUT_SEC = 30


def source_extra(row: dict) -> dict:
    """数据源行的 `extra_config` → dict（列是 JSON，不同驱动回 str/bytes/dict）。

    解析不了就报错不静默走默认值：库里存了脏 JSON 是配置缺陷，
    “默默按默认超时跑”会把“预算调不动”伪装成“配了没生效”。
    """
    extra = (row or {}).get("extra_config")
    if isinstance(extra, (bytes, bytearray)):
        extra = extra.decode("utf-8", errors="replace")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra or "{}")
        except ValueError as exc:
            raise ValueError(
                "数据源高级配置（extra_config）不是合法 JSON 对象，请在「数据源」页重新保存") from exc
    return dict(extra or {}) if isinstance(extra, dict) else {}


def resolve_timeouts(row: dict, *, timeout_sec=None) -> tuple[int, int]:
    """返回 (connect, read) 秒。

    优先级：调用侧显式预算（治理 Guardrail 下发）> 数据源高级配置 > 代码缺省。
    未配任何一项时与写死时代逐字一致（10/30），行为零变化。
    """
    extra = source_extra(row)
    connect = int(extra.get("connect_timeout_sec") or DEFAULT_CONNECT_TIMEOUT_SEC)
    read = int(timeout_sec or extra.get("read_timeout_sec") or DEFAULT_READ_TIMEOUT_SEC)
    return connect, read


def get_datasource_conn(db_type: str, host: str, port: int,
                        user: str, password: str, database: str = None,
                        ssl: bool = False, ssl_mode: str = None,
                        read_timeout: int = 30, connect_timeout: int = 10):
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
        connect_timeout: Handshake bound in seconds. Both budgets are now
            per-datasource configurable (``adh_datasources.extra_config``);
            the defaults here equal the previously hardcoded 10/30 so an
            unconfigured datasource behaves exactly as before.

    Returns:
        A pymysql/psycopg2 connection for SQL datasources.

    Raises:
        ValueError: If the required driver package is not installed.
    """
    db_type = (db_type or "mysql").lower()
    from backend.common.db_types import is_es_db_type  # L0 同源清单（分层契约：common 不得引 semantics）
    connect_timeout = int(connect_timeout or DEFAULT_CONNECT_TIMEOUT_SEC)
    read_timeout = int(read_timeout or DEFAULT_READ_TIMEOUT_SEC)

    if is_es_db_type(db_type):
        # ES 没有 DB-API 连接（既不是 MySQL 也不是 PG 协议），它走 REST `/_sql`。
        # 在这里显式拒，而不是“当 mysql 连一下看看”：那会拿 ES 的 host/port 去握手，
        # 报出一个完全误导向的连接错（护栏 §1：不得长出绕开 es_client 的裸执行通道）。
        raise ValueError(
            "Elasticsearch 型数据源没有 DB-API 连接：请用 `backend.core.es_client`，"
            "或经治理执行口 `backend.core.query_executor.execute_query`（已按 db_type 分派）")
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
        _rt = int(read_timeout or DEFAULT_READ_TIMEOUT_SEC)
        pg_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "dbname": database or "postgres",
            "cursor_factory": psycopg2.extras.RealDictCursor,
            "connect_timeout": connect_timeout,
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
            "connect_timeout": connect_timeout,
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


def datasource_db_type(datasource_id) -> str:
    """数据源的 db_type（单列轻查：不取凭据、不解密）。查不到回 ''。

    存心的窄接口：上面那些调用只要回答“这是什么引擎”，不需要（也不应）
    拿着一行带账密的配置在内存里流转。
    """
    ds_id = int(datasource_id or 0)
    if not ds_id:
        return ""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT db_type FROM adh_datasources WHERE id = %s", (ds_id,))
            row = cur.fetchone() or {}
            return str(row.get("db_type") or "")
    finally:
        conn.close()


def is_es_datasource(datasource_id) -> bool:
    """该数据源（或数据集行的执行源）是否 ES 型 —— 判据单一来自 `adh_datasources.db_type`。

    为何放在 L0：同一个问题三处要问 —— 取数分派（`dataset_service.execution_is_es`）、
    定义校验（`dataset_registry.es_source_row_of`）、语义层编译门（`semantics.planner`），
    而 L2(`modules/*`) 与 L3(`semantics/*`) 不得互相 import。各写一份就会出双口径：
    内置通道退役后 planner 拿 `source_type='es'/es_pattern` 当判据就恒为 false，
    那道门会静默失效（拿 ES SQL 按目标方言二次编译，结果是报错或错数）。

    查不到源行/查库异常回 False（=按非 ES 走通用链）：这不是把 ES 当普通源将就，而是
    “源不存在”属于另一类错，由通用链在执行前显式报出（护栏 §1），不得在这里猜。
    """
    from backend.common.db_types import is_es_db_type

    if not int(datasource_id or 0):
        return False
    try:
        return is_es_db_type(datasource_db_type(datasource_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("[datasource] db_type lookup failed for id=%s: %s", datasource_id, e)
        return False


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
