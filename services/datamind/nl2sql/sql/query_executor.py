"""Doris query executor with SQL validation and audit logging."""

import re
import time
import logging
from contextlib import contextmanager

import pandas as pd

from services.shared.common.config import (
    DORIS_HOST,
    DORIS_PORT,
    DORIS_USER,
    DORIS_PASSWORD,
    DORIS_DATABASE,
    METADATA_DB_DATABASE,
)
from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.crypto import decrypt_password, is_encrypted
from services.shared.common.ttl_cache import datasource_cache

logger = logging.getLogger(__name__)

# 默认数据源配置（不缓存，直接返回）
_DEFAULT_DS_CONFIG = {
    "host": DORIS_HOST, "port": DORIS_PORT,
    "user": DORIS_USER, "password": DORIS_PASSWORD,
    "database": DORIS_DATABASE,
    "db_type": "doris",
    "ssl": False,
}


def _get_ds_conn_params(datasource_id: int = None) -> dict:
    """Get connection parameters for a datasource.

    If datasource_id is not provided, uses the default datasource from database.
    Falls back to env config only if database query fails.
    结果会缓存 5 分钟，数据源更新时需调用 invalidate_datasource_cache() 清除。
    """
    if not datasource_id:
        # Try to get default datasource from database
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM adh_datasources WHERE is_default = 1 LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row:
                        datasource_id = row["id"]
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Failed to get default datasource: %s", e)

        # If still no datasource_id, fallback to env config
        if not datasource_id:
            return _DEFAULT_DS_CONFIG

    cache_key = f"ds:{datasource_id}"

    def _query_from_db():
        try:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT host, port, username, password, database_name, db_type, "
                            "`ssl`, ssl_mode "
                            "FROM adh_datasources WHERE id = %s",
                            (datasource_id,),
                        )
                    except Exception:
                        # ssl_mode 列为迁移新增(未迁移时不阻断): 回落旧列查询
                        conn.rollback()
                        cur.execute(
                            "SELECT host, port, username, password, database_name, db_type, `ssl` "
                            "FROM adh_datasources WHERE id = %s",
                            (datasource_id,),
                        )
                    row = cur.fetchone()
                    if row:
                        password = row["password"] or ""
                        if password and is_encrypted(password):
                            try:
                                password = decrypt_password(password)
                            except ValueError as e:
                                logger.warning("Failed to decrypt password for datasource %s: %s", datasource_id, e)
                        return {
                            "host": row["host"], "port": row["port"],
                            "user": row["username"], "password": password,
                            "database": row.get("database_name") or DORIS_DATABASE,
                            "db_type": row.get("db_type", "doris"),
                            "ssl": bool(row.get("ssl", 0)),
                            "ssl_mode": row.get("ssl_mode") or None,
                        }
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Failed to get datasource %s config: %s", datasource_id, e)
        return _DEFAULT_DS_CONFIG

    return datasource_cache.get_or_set(cache_key, _query_from_db)


def invalidate_datasource_cache(datasource_id: int = None):
    """清除数据源配置缓存。datasource_id=None 清除全部。"""
    if datasource_id is None:
        datasource_cache.invalidate()
    else:
        datasource_cache.invalidate(f"ds:{datasource_id}")


@contextmanager
def get_connection(datasource_id: int = None):
    """Context manager that yields a driver connection to the specified datasource.

    按 db_type 分发: mysql/doris → pymysql, postgres/sls → psycopg2(共享工厂
    datasource_db.get_datasource_conn), 作为 DataEngine 不可用时的直连兜底。
    """
    from services.shared.common.db.datasource_db import get_datasource_conn
    params = _get_ds_conn_params(datasource_id)
    conn = get_datasource_conn(
        db_type=params.get("db_type", "doris"),
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        ssl=bool(params.get("ssl", False)),
        ssl_mode=params.get("ssl_mode"),
        read_timeout=60,
    )
    try:
        yield conn
    finally:
        conn.close()


def _extract_sql_from_text(text: str) -> str:
    """Extract actual SQL from LLM output that may contain leading explanation text.

    Strips markdown fences, leading prose, trailing semicolons, and finds the first SQL keyword.
    Stops at non-SQL content (markdown tables, explanations, etc.).
    """
    import re
    text = text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first fence line
        lines = lines[1:]
        # Remove trailing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find the first SQL keyword (SELECT or WITH) — everything before it is prose
    m = re.search(r'\b(SELECT|WITH)\b', text, re.IGNORECASE)
    if m:
        text = text[m.start():].strip()
    else:
        # No SQL keyword found — the LLM returned pure explanation, not SQL
        return ""

    # Stop at non-SQL content: markdown tables, headers, bold text, explanations
    # SQL typically ends at LIMIT clause, semicolon, or closing parenthesis
    lines = text.split("\n")
    sql_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip empty lines within SQL
        if not stripped:
            sql_lines.append(line)
            continue
        # Stop at markdown content markers
        if stripped.startswith("|") or stripped.startswith("#") or stripped.startswith("**"):
            break
        # Stop at markdown code fences
        if stripped.startswith("```"):
            break
        # Stop at explanation lines (Chinese text with colons that's not SQL)
        # Pattern: lines starting with Chinese characters followed by colon
        if re.match(r'^[一-鿿].*?[：:]', stripped):
            break
        # Stop at table separator lines (e.g., | --- | --- |)
        if re.match(r'^[\|\-\s:]+$', stripped) and '|' in stripped:
            break
        sql_lines.append(line)

    text = "\n".join(sql_lines).strip()

    # Strip trailing semicolons (and optional comment after)
    # Handles: "SELECT ... ;" or "SELECT ... ; -- comment"
    text = re.sub(r';\s*(--.*)?\s*$', '', text).strip()

    # Strip all SQL comments (-- line comments and /* block comments */)
    # LLM may include template comments that break SQL execution
    text = re.sub(r'--[^\n]*', '', text)  # Remove single-line comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)  # Remove multi-line comments
    text = re.sub(r'\n\s*\n', '\n', text)  # Collapse multiple blank lines
    text = text.strip()

    return text


def validate_sql(sql: str, require_limit: bool = True) -> tuple[bool, str]:
    """Validate that a SQL statement is safe to execute.

    Rules:
    - Must be a SELECT or WITH (CTE) statement only
    - No DDL (CREATE, ALTER, DROP, TRUNCATE, RENAME)
    - No DML (INSERT, UPDATE, DELETE, REPLACE)
    - No multiple statements (no semicolons in content)
    - Must include a LIMIT clause (unless require_limit=False)
    - No system commands or admin operations

    Returns:
        (True, "") if valid, (False, error_message) if invalid.
    """
    from services.shared.semantics.sql_guard import parse_query
    try:
        tree = parse_query(sql)
        if require_limit and tree.args.get("limit") is None:
            return False, "Query must include a LIMIT clause."
        return True, ""
    except (PermissionError, ValueError, TypeError):
        return False, "仅允许单条 SELECT/WITH 只读查询"


def execute_query(
    sql: str,
    datasource_id: int = None,
    user_id: int = None,
    workspace_id: int = None,
    user_role: str = None,
    federated: list = None,
) -> tuple[pd.DataFrame, int, int]:
    """Execute a validated query against the specified datasource.

    Execution priority:
    1. DataEngine (Rust DataFusion) — if ENGINE_ENABLED=true and health OK
       - Applies RLS policies (row filtering, column hiding, column masking)
    2. Direct execution (pymysql) — fallback (no RLS)

    Args:
        sql: The SQL query string.
        datasource_id: Optional datasource ID to execute against.
        user_id: Optional user ID for RLS policy loading.
        workspace_id: Optional workspace ID for RLS policy loading.
        user_role: Optional user role ("admin" bypasses RLS).
        federated: 跨源联邦附加数据源 [{"name": 数据源名, "config": {连接配置}}]，
            SQL 中以 `name.db.table` 三段式引用；非空时禁止直连回退
            （直连无法跨源执行，必须走 DataFusion 联邦）。

    Returns:
        (DataFrame, execution_time_ms, row_count)

    Raises:
        ValueError: If SQL validation fails.
        RuntimeError: If query execution fails.
    """
    params = _get_ds_conn_params(datasource_id)

    # Clean SQL: strip leading prose, markdown fences, etc.
    sql = _extract_sql_from_text(sql)

    # Ensure LIMIT after extraction — _extract_sql_from_text may strip
    # trailing Chinese explanation that contained the LIMIT added by validate_and_fix
    from services.datamind.nl2sql.sql.sql_validator import add_limit
    sql = add_limit(sql)

    # For MySQL/Doris, validate SQL
    valid, reason = validate_sql(sql)
    if not valid:
        raise ValueError(f"SQL validation failed: {reason}")

    # DataEngine 是唯一执行通道（禁止降级 pymysql 直连）
    from services.shared.common.engine_client import engine_client, ENGINE_ENABLED
    if not ENGINE_ENABLED:
        raise RuntimeError("DataEngine 未启用，拒绝执行（禁止降级直连）")
    if not engine_client.health():
        raise RuntimeError("DataEngine 不可用，拒绝执行（禁止降级直连）")
    try:
        # Get or create datasource in DataEngine
        ds_name = f"adh-{datasource_id}" if datasource_id else "adh-default"
        engine_ds_id = engine_client.get_or_create_datasource(
            name=ds_name,
            db_type=params.get("db_type", "doris"),
            host=str(params.get("host", "")),
            port=int(params.get("port", 3306)),
            username=str(params.get("user", "")),
            password=str(params.get("password", "")),
            database=str(params.get("database", "")),
            ssl_mode=params.get("ssl_mode"),
        )

        # Load RLS policies for DataEngine
        # 策略加载失败 = fail-closed 拒绝（rls_loader 内部已 raise），
        # 不得降级为无 RLS 执行、也不得回落直连绕过拒绝决定
        rls_policies = []
        if user_id and workspace_id and datasource_id:
            from services.shared.common.rls_loader import load_rls_policies_for_query
            # Extract table names from SQL for policy lookup
            tables = _extract_table_names(sql)
            rls_policies = load_rls_policies_for_query(
                user_id=user_id,
                workspace_id=workspace_id,
                datasource_id=datasource_id,
                tables=tables,
                user_role=user_role or "user",
            )
            if rls_policies:
                logger.info("Loaded %d RLS policies for tables: %s", len(rls_policies), tables)

        start = time.time()
        try:
            result = engine_client.query(
                sql=sql, datasource_id=engine_ds_id, rls_policies=rls_policies,
                federated=federated,
            )
        except Exception as exc:  # noqa: BLE001 — 仅瞬时故障重试，其余原样抛
            # 只读查询幂等：上游瞬时抖动（连接/发现挂死）在超时取消后立即重试一次可自愈
            # （实测取消后重试 <1s 成功）；语法/权限类错误不重试。
            if not _is_transient_engine_error(exc):
                raise
            logger.warning("DataEngine 瞬时故障，重试一次: %s", exc)
            result = engine_client.query(
                sql=sql, datasource_id=engine_ds_id, rls_policies=rls_policies,
                federated=federated,
            )
        elapsed_ms = int((time.time() - start) * 1000)

        df = result.to_dataframe()
        logger.info(
            "DataEngine query: %d rows, %d ms, ds=%s",
            len(df), elapsed_ms, datasource_id,
        )
        return df, elapsed_ms, len(df)
    except PermissionError:
        # 安全拒绝决定不得被任何回退路径吞掉（fail-loud）
        raise
    except Exception as e:
        if federated:
            # 跨源联邦查询只有 DataFusion 能执行
            raise RuntimeError(f"跨源联邦查询执行失败: {e}") from e
        # 不允许降级直连（用户指令 + no-silent-degradation）：DataEngine 失败必须显式暴露
        raise RuntimeError(f"DataEngine 查询失败: {e}") from e


def _is_transient_engine_error(exc: Exception) -> bool:
    """引擎瞬时故障（上游超时/网关 5xx/连接不可用）可安全重试；语法/校验类不重试。"""
    status = getattr(exc, "status_code", None)
    if status in (502, 503, 504):
        return True
    msg = str(exc)
    return "timed out" in msg or "Cannot connect to engine" in msg


def _extract_table_names(sql: str) -> list[str]:
    """Extract table references from SQL（含限定名 db.table / ds.db.table）。

    用 sqlglot 结构化解析（与 enforcer/RLS 匹配键同源），跨源 SQL 的
    限定名一并返回，供 RLS 策略按 (源, 表) 归一匹配（护栏 §10）。
    解析失败直接拒绝，不降级为宽松正则（no-silent-degradation）。
    """
    from services.shared.semantics.sql_guard import extract_tables
    return extract_tables(sql)



def execute_query_with_permission(
    sql: str,
    datasource_id: int = None,
    user_context: dict = None,
    workspace_id: int = 0,
    database: str = "",
    federated_names: list = None,
) -> tuple[pd.DataFrame, int, int]:
    """Execute a query with lightweight permission enforcement.

    Uses role_service + rls_service for data access control (no Ranger/LDAP/Kerberos).

    Flow:
    1. Extract tables from SQL
    2. Check datasource/table access via role_service
    3. Get row-level filters from rls_service → inject into SQL
    4. Execute modified query
    5. Apply column hiding/masking post-execution
    6. Log audit

    Args:
        sql: The SQL query string.
        datasource_id: Datasource ID.
        user_context: dict with "user_id" and "username".
        workspace_id: Workspace context for permission scoping.
        database: Database name (unused, kept for API compat).
        federated_names: 跨源联邦附加数据源名列表（= adh_datasources.name）。
            服务端解析为连接配置随请求下发 dataengine（凭据不进 LLM/前端）；
            解析不到的名字直接拒绝（不猜源）。

    Returns:
        (DataFrame, execution_time_ms, row_count)

    Raises:
        PermissionError: If access denied.
        ValueError: If SQL validation fails.
        RuntimeError: If query execution fails.
    """
    from services.datamind.permission.enforcer import permission_enforcer

    # 跨源联邦源：服务端按 name 解析（含解密凭据），仅在内部通道下发给 dataengine
    federated = None
    if federated_names:
        from services.shared.common.db import get_datasource_by_name
        federated = []
        for name in dict.fromkeys(federated_names):
            source = get_datasource_by_name(name)
            if not source:
                raise PermissionError(f"联邦数据源 '{name}' 无法解析，已拒绝执行")
            federated.append({
                "name": name,
                "config": {
                    "db_type": source.get("db_type") or "mysql",
                    "host": source.get("host") or "",
                    "port": int(source.get("port") or 3306),
                    "database": source.get("database_name") or "",
                    "user": source.get("username") or "",
                    "password": source.get("password") or "",
                    "ssl_mode": source.get("ssl_mode") or None,
                },
            })

    # 无 user_context 不再整体旁路: 敏感列屏蔽基线对所有调用(含系统/内部)强制生效。
    # enforce_sql 对 user_id=0 走 sensitive_only(仅治理基线, 不做 RBAC/RLS 行级过滤)。
    user_id = (user_context or {}).get("user_id") or 0
    has_user = bool(user_id)

    try:
        # Step 1-3: Check permissions and rewrite SQL
        modified_sql, perm_result = permission_enforcer.enforce_sql(
            sql=sql,
            user_id=user_id,
            workspace_id=workspace_id,
            datasource_id=datasource_id or 0,
        )

        # Step 4: Execute the modified query
        df, elapsed_ms, row_count = execute_query(
            modified_sql, datasource_id,
            user_id=user_id, workspace_id=workspace_id, federated=federated)

        # Step 5: Apply column hiding/masking (含 block 屏蔽列, 始终执行)
        if perm_result.hidden_columns or perm_result.masked_columns:
            df = permission_enforcer.apply_post_processing(df, perm_result)

        # Step 6: Log audit (success) — 仅真实用户调用落审计
        if has_user:
            _log_permission_audit(
                user_context=user_context,
                datasource_id=datasource_id,
                tables=permission_enforcer._extract_tables(sql),
                original_sql=sql,
                filtered_sql=modified_sql,
                allowed=True,
                policies=perm_result.policies_applied,
                workspace_id=workspace_id,
            )

        return df, elapsed_ms, row_count

    except PermissionError as exc:
        if has_user:
            try:
                denied_tables = permission_enforcer._extract_tables(sql)
            except Exception:
                denied_tables = []
            _log_permission_audit(
                user_context=user_context or {}, datasource_id=datasource_id,
                tables=denied_tables, original_sql=sql, filtered_sql="",
                allowed=False, deny_reason=str(exc), workspace_id=workspace_id,
            )
        raise


def explain_query_with_permission(
    sql: str,
    datasource_id: int,
    user_context: dict = None,
    workspace_id: int = 0,
    federated_names: list = None,
) -> list:
    """治理后 EXPLAIN：返回执行计划文本行（不含数据行），供同步/DAG 执行诊断。

    安全口径：先走 enforce_sql 治理改写（敏感基线/RBAC/RLS）与只读校验，
    再下发 dataengine EXPLAIN；输出仅为计划文本，不返回任何业务数据行。
    """
    from services.datamind.permission.enforcer import permission_enforcer
    from services.shared.semantics.sql_guard import parse_query
    parse_query(sql)  # 只读校验（EXPLAIN 前置，拒绝非 SELECT/写操作）
    user_id = (user_context or {}).get("user_id") or 0
    modified_sql, _perm = permission_enforcer.enforce_sql(
        sql=sql, user_id=user_id, workspace_id=workspace_id,
        datasource_id=datasource_id or 0)

    federated = None
    if federated_names:
        from services.shared.common.db import get_datasource_by_name
        federated = []
        for name in dict.fromkeys(federated_names):
            source = get_datasource_by_name(name)
            if not source:
                raise PermissionError(f"联邦数据源 '{name}' 无法解析，已拒绝执行")
            federated.append({
                "name": name,
                "config": {
                    "db_type": source.get("db_type") or "mysql",
                    "host": source.get("host") or "",
                    "port": int(source.get("port") or 3306),
                    "database": source.get("database_name") or "",
                    "user": source.get("username") or "",
                    "password": source.get("password") or "",
                    "ssl_mode": source.get("ssl_mode") or None,
                },
            })

    params = _get_ds_conn_params(datasource_id)
    from services.shared.common.engine_client import engine_client, ENGINE_ENABLED
    if not ENGINE_ENABLED or not engine_client.health():
        raise RuntimeError("DataEngine 不可用，拒绝执行（禁止降级直连）")
    # 与 execute_query 同口径：引擎侧注册数据源 + RLS 计划反映治理后形态
    ds_name = f"adh-{datasource_id}" if datasource_id else "adh-default"
    engine_ds_id = engine_client.get_or_create_datasource(
        name=ds_name,
        db_type=params.get("db_type", "doris"),
        host=str(params.get("host", "")),
        port=int(params.get("port", 3306)),
        username=str(params.get("user", "")),
        password=str(params.get("password", "")),
        database=str(params.get("database", "")),
        ssl_mode=params.get("ssl_mode"),
    )
    rls_policies = []
    if user_id and workspace_id and datasource_id:
        from services.shared.common.rls_loader import load_rls_policies_for_query
        rls_policies = load_rls_policies_for_query(
            user_id=user_id, workspace_id=workspace_id,
            datasource_id=datasource_id,
            tables=_extract_table_names(sql),
            user_role=(user_context or {}).get("user_role") or "user")
    return engine_client.explain(
        modified_sql, engine_ds_id, rls_policies=rls_policies or None,
        federated=federated)


def _log_permission_audit(
    user_context: dict,
    datasource_id: int,
    tables: list,
    original_sql: str,
    filtered_sql: str,
    allowed: bool,
    deny_reason: str = "",
    policies: list = None,
    workspace_id: int = 0,
):
    """Log permission enforcement audit to adh_rls_audit_logs."""
    try:
        import time as _time
        audit_id = int(_time.time() * 1000)

        # policies_applied 混合了 RLS 数字策略 id 与字符串治理标记(如
        # "sensitive_block:..."/"sensitive_fields:..."), 而 policy_id 列为整型 ——
        # 只取首个数字入 policy_id, 字符串标记归入 policy_name, 避免类型错。
        numeric_pid = None
        markers: list = []
        for p in (policies or []):
            if isinstance(p, bool):
                continue
            if isinstance(p, int):
                if numeric_pid is None:
                    numeric_pid = p
            else:
                s = str(p)
                if s and s != "permission_enforcer":
                    markers.append(s)
        # adh_rls_audit_logs.policy_name 为 varchar(128)，按列宽截断防写入失败
        # （审计行不得因标记过长而丢失；截断保留可诊断前缀）
        policy_name = (",".join(markers) or "permission_enforcer")[:128]

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_rls_audit_logs
                       (id, user_id, workspace_id, policy_id, policy_name, table_name,
                        action, original_sql, filtered_sql, deny_reason)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        audit_id,
                        user_context.get("user_id", 0),
                        workspace_id,
                        numeric_pid,
                        policy_name,
                        ", ".join(tables),
                        "allow" if allowed else "deny",
                        original_sql[:1000],
                        filtered_sql[:1000],
                        # 拒绝原因必须持久化：审计页"拒绝访问"记录的查看详情
                        # 依赖它下钻（此前丢弃导致拒绝行无详情可查）
                        deny_reason[:512] if deny_reason else None,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to log permission audit: %s", e)


def log_audit(
    user_id: int,
    username: str,
    role: str,
    question: str,
    sql: str,
    status: str,
    row_count: int = 0,
    time_ms: int = 0,
    error: str = "",
    datasource_id: int = 0,
    query_type: str = "sql",
) -> None:
    """Write a query audit record to adh_query_audit in Doris.

    Args:
        user_id:    ID of the user who executed the query.
        username:   Username string.
        role:       User role (admin / analyst / viewer).
        question:   The natural-language question from the user.
        sql:        The generated SQL.
        status:     'success' or 'error'.
        row_count:  Number of rows returned (0 on error).
        time_ms:    Query execution time in milliseconds.
        error:      Error message (empty on success).
        datasource_id: ID of the datasource used.
        query_type: 'sql' 或 'semantic'.
    """
    import time as _time
    audit_id = int(_time.time() * 1000)

    insert_sql = """
        INSERT INTO adh_query_audit
            (id, datasource_id, user_id, username, user_role, question, generated_sql,
             query_type, execution_status, row_count, execution_time_ms, error_message, created_at)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    """
    try:
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(insert_sql, (
                    audit_id, datasource_id, user_id, username, role, question, sql,
                    query_type, status, row_count, time_ms, error,
                ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        # Audit logging should never crash the main flow
        logger.warning("Failed to write audit log: %s", e)


# ══════════════════════════════════════════════════════════════════════
# Engine Server Integration — SQL execution via engine-server-rust
# ══════════════════════════════════════════════════════════════════════

def execute_query_via_engine(
    sql: str,
    datasource_id: int = None,
    user_context: dict = None,
    table_names: list[str] = None,
    workspace_id: int = 0,
) -> tuple[pd.DataFrame, int, int]:
    """Execute SQL through DataEngine (Rust DataFusion Gateway).

    This is an alternative to execute_query() that routes SQL through
    the Rust DataFusion engine for:
    - SQL execution against MySQL/Doris
    - Query result caching
    - Datasource management

    Args:
        sql: The SQL query string.
        datasource_id: Datasource ID.
        user_context: User context dict for RLS (user_id, username, role, workspace_id).
        table_names: Optional list of table names to include in manifest.
        workspace_id: Workspace ID for RLS policy filtering.

    Returns:
        (DataFrame, execution_time_ms, row_count)

    Raises:
        RuntimeError: If engine execution fails.
    """
    from services.shared.common.engine_client import engine_client, EngineError, ENGINE_ENABLED

    # Check if engine is enabled — 禁止降级: DataEngine 是唯一执行通道
    if not ENGINE_ENABLED:
        raise RuntimeError("DataEngine 未启用，拒绝执行（禁止降级直连）")

    # Check engine health — 不健康即显式拒绝，不回落直连
    if not engine_client.health():
        raise RuntimeError("DataEngine 不可用，拒绝执行（禁止降级直连）")

    # Get connection info
    params = _get_ds_conn_params(datasource_id)
    db_type = params.get("db_type", "doris")

    # Clean SQL
    sql = _extract_sql_from_text(sql)
    from services.datamind.nl2sql.sql.sql_validator import add_limit
    sql = add_limit(sql)

    # Validate SQL
    valid, reason = validate_sql(sql)
    if not valid:
        raise ValueError(f"SQL validation failed: {reason}")

    # Get or create datasource in DataEngine
    try:
        ds_name = f"adh-{datasource_id}" if datasource_id else "adh-default"
        engine_ds_id = engine_client.get_or_create_datasource(
            name=ds_name,
            db_type=db_type,
            host=str(params.get("host", "")),
            port=int(params.get("port", 3306)),
            username=str(params.get("user", "")),
            password=str(params.get("password", "")),
            database=str(params.get("database", "")),
            ssl_mode=params.get("ssl_mode"),
        )
    except Exception as e:
        logger.warning("Failed to get/create engine datasource: %s", e)
        return execute_query(sql, datasource_id)

    # Load RLS policies for DataEngine
    rls_policies = []
    if user_context and datasource_id:
        try:
            from services.shared.common.rls_loader import load_rls_policies_for_query
            from services.datamind.nl2sql.sql.query_executor import _extract_table_names
            tables = _extract_table_names(sql)
            rls_policies = load_rls_policies_for_query(
                user_id=user_context.get("user_id", 0),
                workspace_id=workspace_id or user_context.get("workspace_id", 0),
                datasource_id=datasource_id,
                tables=tables,
                user_role=user_context.get("role", "user"),
            )
            if rls_policies:
                logger.info("Loaded %d RLS policies for tables: %s", len(rls_policies), tables)
        except Exception as e:
            logger.warning("Failed to load RLS policies: %s", e)

    start = time.time()
    try:
        result = engine_client.query(
            sql=sql,
            datasource_id=engine_ds_id,
            rls_policies=rls_policies,
        )

        elapsed_ms = int((time.time() - start) * 1000)

        # Convert to DataFrame
        df = result.to_dataframe()
        row_count = len(df)

        logger.info(
            "Engine query completed: %d rows, %d ms, columns=%s",
            row_count, elapsed_ms, result.columns,
        )

        return df, elapsed_ms, row_count

    except EngineError as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("Engine query failed (%d ms): %s", elapsed_ms, e)

        # Fallback to direct execution on engine error
        logger.info("Falling back to direct execution")
        return execute_query(sql, datasource_id)

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("Engine query error (%d ms): %s", elapsed_ms, e)
        raise RuntimeError(f"Engine query failed: {e}") from e


def dry_plan_sql(
    sql: str,
    datasource_id: int = None,
    user_context: dict = None,
    table_names: list[str] = None,
    workspace_id: int = 0,
) -> str:
    """Rewrite SQL through DataEngine without executing.

    Note: Current DataEngine implementation executes SQL directly.
    This function is kept for API compatibility but returns the original SQL.

    Args:
        sql: The SQL query string.
        datasource_id: Datasource ID.
        user_context: User context dict for RLS.
        table_names: Optional table names for manifest.
        workspace_id: Workspace ID for RLS.

    Returns:
        Original SQL string (DataEngine executes directly).
    """
    # DataEngine executes SQL directly, no dry-plan mode available
    return sql
