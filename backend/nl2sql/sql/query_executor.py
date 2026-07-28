"""Doris query executor with SQL validation and audit logging."""

import re
import time
import logging
from contextlib import contextmanager

import pymysql
import pandas as pd

from backend.common.config import (
    DORIS_HOST,
    DORIS_PORT,
    DORIS_USER,
    DORIS_PASSWORD,
    DORIS_DATABASE,
    METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn
from backend.common.crypto import decrypt_password, is_encrypted
from backend.common.ttl_cache import datasource_cache

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
                        "SELECT id FROM adh_datasources WHERE is_default = 1 AND is_active != 0 LIMIT 1"
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
    """Context manager that yields a pymysql connection to the specified datasource."""
    params = _get_ds_conn_params(datasource_id)
    conn = pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
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
    if not sql or not sql.strip():
        return False, "SQL statement cannot be empty."

    # Strip leading explanation text (LLM sometimes puts prose before SQL)
    cleaned = _extract_sql_from_text(sql)

    cleaned = cleaned.rstrip(";").strip()

    # Reject multiple statements (ignore semicolons inside strings, identifiers, and comments)
    # Simple heuristic: remove quoted strings, backtick identifiers, and comments, then check for semicolons
    import re as _re
    no_strings = _re.sub(r"'[^']*'", "''", cleaned)  # Remove single-quoted strings
    no_strings = _re.sub(r'"[^"]*"', '""', no_strings)  # Remove double-quoted strings
    no_strings = _re.sub(r'`[^`]*`', '``', no_strings)  # Remove backtick-quoted identifiers
    no_strings = _re.sub(r'--[^\n]*', '', no_strings)  # Remove single-line comments
    no_strings = _re.sub(r'/\*.*?\*/', '', no_strings, flags=_re.DOTALL)  # Remove multi-line comments
    if ";" in no_strings:
        return False, "Multiple statements are not allowed."

    # Normalise for keyword checks
    upper = cleaned.upper().lstrip()

    # Must start with SELECT or WITH
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        preview = cleaned[:80].replace("\n", " ")
        return False, f"Only SELECT or WITH (CTE) queries are allowed. Got: '{preview}...'"

    # Block DDL keywords
    ddl_patterns = [
        r"\bCREATE\b", r"\bALTER\b", r"\bDROP\b", r"\bTRUNCATE\b",
        r"\bRENAME\b", r"\bGRANT\b", r"\bREVOKE\b",
    ]
    for pat in ddl_patterns:
        if re.search(pat, upper):
            return False, f"DDL statement detected ({pat.strip(chr(92)).strip('b')}). Only SELECT is allowed."

    # Block DML keywords (INSERT / UPDATE / DELETE / REPLACE in statement position)
    dml_patterns = [
        r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bREPLACE\b",
    ]
    for pat in dml_patterns:
        if re.search(pat, upper):
            return False, f"DML statement detected ({pat.strip(chr(92)).strip('b')}). Only SELECT is allowed."

    # Must contain LIMIT (unless skipped for count queries)
    if require_limit and not re.search(r"\bLIMIT\b", upper):
        return False, "Query must include a LIMIT clause."

    return True, ""


def _build_es_client(params: dict):
    """Build an Elasticsearch client from connection parameters."""
    try:
        from elasticsearch import Elasticsearch
    except ImportError:
        raise RuntimeError("Elasticsearch library not installed")

    protocol = "https" if params.get("ssl") else "http"
    es_url = f"{protocol}://{params['host']}:{params['port']}"
    es_kwargs = {"hosts": [es_url], "request_timeout": 30}
    if params.get("ssl"):
        es_kwargs["verify_certs"] = False
        es_kwargs["ssl_show_warn"] = False
    if params.get("user") and params.get("password"):
        es_kwargs["basic_auth"] = (params["user"], params["password"])
    elif params.get("user"):
        es_kwargs["basic_auth"] = (params["user"], "")
    return Elasticsearch(**es_kwargs)


def _preprocess_es_sql(sql: str) -> str:
    """Preprocess Elasticsearch SQL to fix common LLM generation issues.

    Fixes:
    1. Remove _id from SELECT clause (ES metadata field, not a regular column).
       If _id is the only column, convert to SELECT *.
    2. Lowercase double-quoted identifiers (ES field names are case-sensitive,
       but LLM often capitalizes them like "Application" instead of "application").
    """
    import re

    # Remove _id from SELECT clause: SELECT "_id", "col1" → SELECT "col1"
    # Also handle: SELECT "col1", "_id" → SELECT "col1"
    sql = re.sub(
        r'(?i)(SELECT\s+.*?)["\']_id["\']\s*,\s*',
        r'\1',
        sql,
    )
    sql = re.sub(
        r'(?i)(SELECT\s+.*?),\s*["\']_id["\']',
        r'\1',
        sql,
    )
    # Handle SELECT "_id" as the only column → convert to SELECT * (will get all fields)
    sql = re.sub(
        r'(?i)SELECT\s+["\']_id["\']\s+FROM',
        'SELECT * FROM',
        sql,
    )

    # Lowercase double-quoted identifiers (field names, not string literals in WHERE)
    # This targets "FieldName" patterns but not string values in WHERE clauses
    def _lower_identifiers(match):
        prefix = match.group(1)  # text before the quote
        name = match.group(2)    # the identifier
        # Don't lowercase string literals in WHERE conditions (after = ' or = ")
        # Check if preceded by = or IN or LIKE etc. — those are values, not identifiers
        if re.search(r'[=!<>]\s*$', prefix):
            return match.group(0)  # It's a value, keep original case
        if re.search(r'\bIN\s*\(\s*$', prefix, re.IGNORECASE):
            return match.group(0)
        if re.search(r'\bLIKE\s*$', prefix, re.IGNORECASE):
            return match.group(0)
        if re.search(r"\bIN\s*\(\s*'$", prefix, re.IGNORECASE):
            return match.group(0)
        # It's an identifier — lowercase it
        return f'{prefix}"{name.lower()}"'

    sql = re.sub(r'([^"]*)"([^"]+)"', _lower_identifiers, sql)

    return sql


def _execute_elasticsearch_query(sql: str, params: dict) -> tuple[pd.DataFrame, int, int]:
    """Execute an Elasticsearch SQL query.

    Uses the Elasticsearch SQL API to execute standard SQL statements.
    The LLM generates SQL (not JSON DSL) based on the Elasticsearch prompt template.

    Args:
        sql: SQL query string (e.g. SELECT * FROM "index" WHERE ...).
        params: Connection parameters including host, port, ssl, etc.

    Returns:
        (DataFrame, execution_time_ms, row_count)
    """
    # Preprocess SQL to fix common LLM issues
    sql = _preprocess_es_sql(sql)
    if not sql or not sql.strip():
        raise ValueError("Empty SQL query for Elasticsearch")
    logger.info("ES SQL after preprocessing: %s", sql)

    es = _build_es_client(params)

    start = time.time()
    try:
        # Use Elasticsearch SQL API
        result = es.sql.query(body={"query": sql})

        # Parse columnar response: columns + rows
        columns_info = result.get("columns", [])
        rows_data = result.get("rows", [])

        if not columns_info or not rows_data:
            elapsed_ms = int((time.time() - start) * 1000)
            return pd.DataFrame(), elapsed_ms, 0

        # Extract column names
        col_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns_info)]

        # Build DataFrame from rows (each row is a list of values)
        df = pd.DataFrame(rows_data, columns=col_names)
        elapsed_ms = int((time.time() - start) * 1000)
        return df, elapsed_ms, len(df)
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("Elasticsearch query failed (%d ms): %s", elapsed_ms, e)
        raise RuntimeError(f"Elasticsearch query failed: {e}") from e
    finally:
        es.close()


def _execute_es_rest(path: str, params: dict) -> tuple[pd.DataFrame, int, int]:
    """Execute an Elasticsearch REST API request.

    Args:
        path: REST path like "GET /index/_doc/id" or "POST /index/_search {...body...}".
        params: Connection parameters.

    Returns:
        (DataFrame, execution_time_ms, row_count)
    """
    import json as _json

    es = _build_es_client(params)
    start = time.time()

    try:
        # Parse method and path: "GET /index/_doc/id" or "POST /index/_search {body}"
        path = path.strip()
        # Split method, URL, and body — body may contain newlines
        first_space = path.find(" ")
        if first_space == -1:
            raise ValueError(f"Invalid REST path format: {path}. Expected: METHOD /path [body]")
        method = path[:first_space].upper()
        rest = path[first_space+1:].strip()

        # Find where body starts (first '{' after URL)
        body_start = rest.find("{")
        if body_start != -1:
            url = rest[:body_start].strip()
            body_str = rest[body_start:].strip()
            try:
                body = _json.loads(body_str)
            except _json.JSONDecodeError:
                # Try to extract first complete JSON object
                depth = 0
                for i, ch in enumerate(body_str):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            body = _json.loads(body_str[:i+1])
                            break
                else:
                    body = body_str
        else:
            url = rest
            body = None

        logger.info("ES REST: %s %s (body=%s)", method, url, bool(body))

        # Build headers — Content-Type required when body is present
        headers = {"Content-Type": "application/json"} if body else {}

        # Execute via low-level perform_request
        if method == "GET":
            resp = es.perform_request("GET", url, body=body, headers=headers)
        elif method == "POST":
            resp = es.perform_request("POST", url, body=body, headers=headers)
        elif method == "PUT":
            resp = es.perform_request("PUT", url, body=body, headers=headers)
        elif method == "DELETE":
            resp = es.perform_request("DELETE", url, body=body, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        elapsed_ms = int((time.time() - start) * 1000)

        # Parse response to DataFrame
        if isinstance(resp, dict):
            # Check for _search response with hits
            if "hits" in resp:
                hits = resp["hits"].get("hits", [])
                if not hits:
                    return pd.DataFrame(), elapsed_ms, 0
                rows = []
                for hit in hits:
                    row = {"_id": hit.get("_id"), "_index": hit.get("_index"), "_score": hit.get("_score")}
                    row.update(hit.get("_source", {}))
                    rows.append(row)
                df = pd.DataFrame(rows)
                return df, elapsed_ms, len(df)
            # Single document response (_doc)
            elif "_source" in resp:
                row = {"_id": resp.get("_id"), "_index": resp.get("_index")}
                row.update(resp["_source"])
                df = pd.DataFrame([row])
                return df, elapsed_ms, 1
            # Generic dict response
            else:
                df = pd.DataFrame([resp])
                return df, elapsed_ms, 1
        else:
            # Non-dict response (e.g., string acknowledgement)
            df = pd.DataFrame([{"result": str(resp)}])
            return df, elapsed_ms, 1

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("ES REST request failed (%d ms): %s", elapsed_ms, e)
        raise RuntimeError(f"ES REST request failed: {e}") from e
    finally:
        es.close()


def _execute_es_dsl(body: str, params: dict, index: str = None) -> tuple[pd.DataFrame, int, int]:
    """Execute an Elasticsearch DSL query.

    Args:
        body: JSON DSL body string, e.g. '{"query":{"term":{"_id":"abc123"}}}'.
        params: Connection parameters.
        index: Optional index name (extracted from body or tables field if not provided).

    Returns:
        (DataFrame, execution_time_ms, row_count)
    """
    import json as _json

    es = _build_es_client(params)
    start = time.time()

    try:
        # Parse DSL body — handle malformed JSON from LLM
        if isinstance(body, str):
            body = body.strip()
            # Try direct parse first
            try:
                dsl = _json.loads(body)
            except _json.JSONDecodeError:
                # Try to extract first complete JSON object by brace matching
                start = body.find("{")
                if start == -1:
                    raise ValueError(f"No JSON object found in DSL body: {body[:100]}")
                depth = 0
                end_pos = None
                for i, ch in enumerate(body[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_pos = i + 1
                            break
                if end_pos is None:
                    # Try closing the last brace
                    last_brace = body.rfind("}")
                    if last_brace > start:
                        end_pos = last_brace + 1
                    else:
                        raise ValueError(f"Unmatched braces in DSL body: {body[:100]}")
                dsl = _json.loads(body[start:end_pos])
        else:
            dsl = body

        # Extract index from body if present, otherwise use provided index
        target_index = dsl.pop("_index", None) or index or "*"

        logger.info("ES DSL: index=%s, body=%s", target_index, _json.dumps(dsl, ensure_ascii=False)[:200])

        # Execute search
        result = es.search(index=target_index, body=dsl)

        elapsed_ms = int((time.time() - start) * 1000)

        # Parse hits
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            return pd.DataFrame(), elapsed_ms, 0

        rows = []
        for hit in hits:
            row = {"_id": hit.get("_id"), "_index": hit.get("_index"), "_score": hit.get("_score")}
            row.update(hit.get("_source", {}))
            rows.append(row)

        df = pd.DataFrame(rows)
        return df, elapsed_ms, len(df)

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("ES DSL query failed (%d ms): %s", elapsed_ms, e)
        raise RuntimeError(f"ES DSL query failed: {e}") from e
    finally:
        es.close()


def execute_query(sql: str, datasource_id: int = None, query_type: str = "sql",
                  user_id: int = 0, workspace_id: int = 0,
                  table_names: list = None) -> tuple[pd.DataFrame, int, int]:
    """Execute a validated query against the specified datasource.

    Execution priority:
    1. DataFusion Gateway (if enabled and healthy) — RLS injected at engine level
    2. Direct connection + rls_filter.py (fallback) — RLS injected via regex

    For Elasticsearch: always uses direct connection (Gateway doesn't support ES).

    Args:
        sql: The SQL query string (or REST path / DSL JSON for ES).
        datasource_id: Optional datasource ID to execute against.
        query_type: Query type — "sql" (default), "rest", or "dsl".
        user_id: Current user ID for RLS enforcement.
        workspace_id: Current workspace ID for RLS enforcement.
        table_names: Pre-extracted table names from SQL (optional, for RLS).

    Returns:
        (DataFrame, execution_time_ms, row_count)

    Raises:
        ValueError: If SQL validation fails.
        RuntimeError: If query execution fails.
    """
    params = _get_ds_conn_params(datasource_id)
    db_type = params.get("db_type", "doris")

    # For Elasticsearch, route by query_type (Gateway doesn't support ES)
    if db_type == "elasticsearch":
        if query_type == "rest":
            return _execute_es_rest(sql, params)
        elif query_type == "dsl":
            return _execute_es_dsl(sql, params)
        else:
            return _execute_elasticsearch_query(sql, params)

    # Clean SQL: strip leading prose, markdown fences, etc.
    sql = _extract_sql_from_text(sql)

    # Ensure LIMIT after extraction
    from backend.nl2sql.sql.sql_validator import add_limit
    sql = add_limit(sql)

    # ── Try DataFusion Gateway first ──────────────────────────────
    try:
        from backend.services.gateway_client import gateway_client
        if gateway_client.is_enabled() and gateway_client.health():
            return _execute_via_gateway(sql, params, datasource_id, user_id, workspace_id, table_names)
    except Exception as e:
        logger.info("Gateway unavailable, falling back to direct execution: %s", e)

    # ── Fallback: Direct connection + rls_filter.py ───────────────
    return _execute_direct(sql, params, datasource_id, user_id, workspace_id, table_names)


def _execute_via_gateway(sql: str, params: dict, datasource_id: int,
                         user_id: int, workspace_id: int,
                         table_names: list = None) -> tuple[pd.DataFrame, int, int]:
    """Execute query via DataFusion Gateway with engine-level RLS."""
    from backend.services.gateway_client import gateway_client, GatewayError

    # Collect RLS policies
    rls_policies = _collect_rls_policies(user_id, workspace_id, datasource_id, table_names, sql)

    # Build datasource config for gateway
    datasource_config = {
        "db_type": params.get("db_type", "mysql"),
        "host": params["host"],
        "port": params["port"],
        "database": params["database"],
        "user": params["user"],
        "password": params["password"],
        "ssl": params.get("ssl", False),
    }

    # Call gateway
    result = gateway_client.execute(
        sql=sql,
        datasource_config=datasource_config,
        rls_policies=rls_policies,
    )

    # Check for errors
    if result.get("error"):
        raise RuntimeError(f"Gateway execution failed: {result['error']}")

    # Convert to DataFrame
    columns = [col["name"] for col in result.get("columns", [])]
    rows = result.get("rows", [])
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame()

    elapsed_ms = result.get("execution_time_ms", 0)
    row_count = result.get("row_count", 0)

    # Log RLS audit
    rls_applied = result.get("rls_applied", [])
    if rls_applied:
        logger.info("Gateway RLS applied: %s", "; ".join(rls_applied))

    return df, elapsed_ms, row_count


def _execute_direct(sql: str, params: dict, datasource_id: int,
                    user_id: int, workspace_id: int,
                    table_names: list = None) -> tuple[pd.DataFrame, int, int]:
    """Execute query directly via pymysql with rls_filter.py (fallback)."""
    # Apply RLS (Row-Level Security) filtering
    rls_warnings = []
    if user_id and workspace_id and datasource_id:
        try:
            from backend.services.rls_service import rls_service
            from backend.nl2sql.sql.rls_filter import apply_rls

            # Get effective policies for all tables in the query
            if not table_names:
                table_names = _extract_table_names(sql)

            all_policies = {
                "row_filter": "",
                "hidden_columns": [],
                "masked_columns": {},
                "policies_applied": [],
            }
            for tname in table_names:
                tp = rls_service.get_effective_policies(
                    user_id, workspace_id, datasource_id, tname
                )
                if tp["policies_applied"]:
                    # Combine row filters with AND
                    if tp["row_filter"]:
                        if all_policies["row_filter"]:
                            all_policies["row_filter"] += " AND " + tp["row_filter"]
                        else:
                            all_policies["row_filter"] = tp["row_filter"]
                    # Merge hidden/masked columns
                    for col in tp["hidden_columns"]:
                        if col not in all_policies["hidden_columns"]:
                            all_policies["hidden_columns"].append(col)
                    all_policies["masked_columns"].update(tp["masked_columns"])
                    all_policies["policies_applied"].extend(tp["policies_applied"])

            if all_policies["policies_applied"]:
                original_sql = sql
                sql, rls_warnings = apply_rls(sql, all_policies, user_id, workspace_id)
                # Log audit for each applied policy
                for pid in all_policies["policies_applied"]:
                    policy = rls_service.get_policy(pid)
                    if policy:
                        rls_service.log_audit(
                            user_id, workspace_id, pid, policy.get("name", ""),
                            ",".join(table_names), "rls_applied",
                            original_sql, sql
                        )
        except Exception as e:
            logger.warning("RLS enforcement failed (proceeding without filter): %s", e)

    # For MySQL/Doris, validate SQL
    valid, reason = validate_sql(sql)
    if not valid:
        raise ValueError(f"SQL validation failed: {reason}")

    start = time.time()
    try:
        with get_connection(datasource_id) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
        elapsed_ms = int((time.time() - start) * 1000)
        return df, elapsed_ms, len(df)
    except ValueError:
        raise
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("Query execution failed (%d ms): %s", elapsed_ms, e)
        raise RuntimeError(f"Query execution failed: {e}") from e


def _collect_rls_policies(user_id: int, workspace_id: int, datasource_id: int,
                          table_names: list = None, sql: str = "") -> list[dict]:
    """Collect RLS policies for the given tables."""
    if not user_id or not workspace_id or not datasource_id:
        return []

    try:
        from backend.services.rls_service import rls_service

        if not table_names:
            table_names = _extract_table_names(sql)

        policies = []
        for tname in table_names:
            tp = rls_service.get_effective_policies(
                user_id, workspace_id, datasource_id, tname
            )
            if tp["policies_applied"]:
                policies.append({
                    "tables": [tname],
                    "row_filter": tp["row_filter"],
                    "hidden_columns": tp["hidden_columns"],
                    "masked_columns": tp["masked_columns"],
                })
        return policies
    except Exception as e:
        logger.warning("Failed to collect RLS policies: %s", e)
        return []


def _extract_table_names(sql: str) -> list:
    """Extract table names from SQL for RLS policy lookup.

    Simple regex-based extraction — handles FROM and JOIN clauses.
    """
    import re
    tables = []
    # Match FROM table and JOIN table patterns
    pattern = r'\b(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?\w+)?'
    for match in re.finditer(pattern, sql, re.IGNORECASE):
        tname = match.group(1)
        # Skip subquery aliases and common keywords
        if tname.upper() not in ("SELECT", "WHERE", "AND", "OR", "ON", "AS"):
            tables.append(tname)
    return list(set(tables))


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
        sql:        The generated SQL (or REST/DSL for ES).
        status:     'success' or 'error'.
        row_count:  Number of rows returned (0 on error).
        time_ms:    Query execution time in milliseconds.
        error:      Error message (empty on success).
        datasource_id: ID of the datasource used.
        query_type: 'sql', 'rest', or 'dsl'.
    """
    import time as _time
    from datetime import datetime
    audit_id = int(_time.time() * 1000)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    insert_sql = """
        INSERT INTO adh_query_audit
            (id, datasource_id, user_id, username, user_role, question, generated_sql,
             query_type, execution_status, row_count, execution_time_ms, error_message, created_at)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(insert_sql, (
                    audit_id, datasource_id, user_id, username, role, question, sql,
                    query_type, status, row_count, time_ms, error, now,
                ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        # Audit logging should never crash the main flow
        logger.warning("Failed to write audit log: %s", e)
