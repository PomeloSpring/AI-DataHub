"""
SQL Validator — client-side safety checks before execution.

This is a second layer of validation (the first being query_executor.validate_sql).
Focuses on business-level rules: time range, result size, and sensitive fields.
"""

import re


# Fields that may contain PII
_SENSITIVE_PATTERNS = [
    r"\bphone\b", r"\bmobile\b", r"\bemail\b", r"\bmail\b",
    r"\bid_card\b", r"\bidcard\b", r"\bidentity\b",
    r"\b手机号\b", r"\b邮箱\b", r"\b身份证\b",
]


def check_time_range(sql: str) -> tuple[bool, str]:
    """Check that the query includes a time range condition.

    Returns (ok, message). If not ok, message explains what to add.
    """
    upper = sql.upper()
    # Look for common time column patterns in WHERE
    time_patterns = [
        r"\bCREATE_TIME\b", r"\bUPDATE_TIME\b", r"\bSCAN_TIME\b",
        r"\bDT\b", r"\bDATE\b", r"\bDATETIME\b", r"\bTIMESTAMP\b",
    ]
    has_time_col = any(re.search(p, upper) for p in time_patterns)
    has_where = "WHERE" in upper

    if has_where and has_time_col:
        return True, ""

    # If there's a WHERE but no time column, warn
    if has_where:
        return False, "建议在 WHERE 子句中添加时间范围条件（如 create_time >= '2025-01-01'），避免全表扫描。"

    # No WHERE at all
    return False, "查询缺少 WHERE 子句，建议添加时间范围条件。"


def check_sensitive_fields(sql: str) -> list[str]:
    """Detect potentially sensitive fields in SELECT clause.

    Returns list of field names that may need masking.
    """
    found = []
    upper = sql.upper()
    for pattern in _SENSITIVE_PATTERNS:
        if re.search(pattern, upper):
            found.append(pattern.strip("\\b"))
    return found


def add_limit(sql: str, default_limit: int = 1000) -> str:
    """Ensure the SQL has a LIMIT clause. If not, append the default."""
    if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        return sql
    return sql.rstrip(";") + f" LIMIT {default_limit}"


def check_window_function_distinct(sql: str) -> list[str]:
    """Check for window functions with DISTINCT which is not supported.

    Returns list of warnings.
    """
    warnings = []
    # Pattern: COUNT(DISTINCT ...) OVER(), SUM(DISTINCT ...) OVER(), etc.
    pattern = r'\b(COUNT|SUM|AVG|MAX|MIN)\s*\(\s*DISTINCT\s+.+?\)\s*OVER\s*\('
    matches = re.findall(pattern, sql, re.IGNORECASE)
    if matches:
        warnings.append(
            "窗口函数中使用了 DISTINCT，这在 MySQL/Doris 中不支持。"
            "建议先用子查询去重，再使用窗口函数。"
        )
    return warnings




def check_select_star(sql: str) -> tuple[bool, str]:
    """Detect SELECT * in SQL queries.

    Returns (is_select_star, message). If is_select_star is True,
    message explains why it's blocked.

    Detection handles:
    - Simple: SELECT * FROM table
    - With alias: SELECT u.* FROM table u
    - With subquery: SELECT * FROM (SELECT ...)
    - With CTE: WITH ... SELECT * FROM ...
    - With comments: SELECT /* comment */ * FROM
    """
    # Normalize: remove comments and extra whitespace
    normalized = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.DOTALL)
    normalized = re.sub(r'--.*$', ' ', normalized, flags=re.MULTILINE)
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    # Remove aggregate function calls with * (COUNT(*), SUM(*) etc.)
    cleaned = re.sub(r'\b(COUNT|SUM|AVG|MAX|MIN)\s*\(\s*\*\s*\)', 'AGG_FUNC', normalized, flags=re.IGNORECASE)

    # Pattern 1: SELECT [DISTINCT] * FROM
    pattern1 = r'\bSELECT\s+(DISTINCT\s+)?\*\s+FROM\b'
    if re.search(pattern1, cleaned, re.IGNORECASE):
        return True, "不支持 SELECT *，请明确指定查询字段。SELECT * 可能意外获取敏感列，且影响查询性能。"

    # Pattern 2: SELECT alias.* FROM (e.g., SELECT u.* FROM users u)
    pattern2 = r'\bSELECT\s+(DISTINCT\s+)?\w+\.\*\s+FROM\b'
    if re.search(pattern2, cleaned, re.IGNORECASE):
        return True, "不支持 SELECT alias.*，请明确指定查询字段。"

    # Pattern 3: SELECT * without FROM (edge case)
    pattern3 = r'\bSELECT\s+(DISTINCT\s+)?\*\s*$'
    if re.search(pattern3, cleaned, re.IGNORECASE | re.MULTILINE):
        return True, "不支持 SELECT *，请明确指定查询字段。"

    return False, ""

def validate_and_fix(sql: str, query_type: str = "sql") -> tuple[str, list[str]]:
    """Run all validations and return (fixed_sql, warnings).

    This does NOT reject the query — it fixes what it can and returns
    warnings for the user to review.

    Args:
        sql: The SQL/query string.
        query_type: "sql", "rest", or "dsl". REST/DSL skip SQL-specific checks.
    """
    warnings = []

    # REST/DSL queries don't need SQL validation
    if query_type in ("rest", "dsl"):
        return sql, warnings

    # Step 1: Clean SQL — strip markdown fences, leading prose, trailing semicolons
    sql = sql.strip()
    if sql.startswith("```"):
        lines = sql.split("\n")
        lines = lines[1:]  # Remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # Remove closing fence
        sql = "\n".join(lines).strip()

    # Find first SQL keyword
    m = re.search(r'\b(SELECT|WITH)\b', sql, re.IGNORECASE)
    if m:
        sql = sql[m.start():].strip()
    else:
        # No SQL keyword found — not a valid SQL statement
        return "", ["未找到有效的 SQL 语句（缺少 SELECT 或 WITH 关键字）"]

    # Strip trailing semicolons and comments
    sql = re.sub(r';\s*(--.*)?\s*$', '', sql).strip()

    # Strip all SQL comments (-- line comments and /* block comments */)
    # LLM may include template comments that break SQL execution
    sql = re.sub(r'--[^\n]*', '', sql)  # Remove single-line comments
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)  # Remove multi-line comments
    sql = re.sub(r'\n\s*\n', '\n', sql)  # Collapse multiple blank lines
    sql = sql.strip()

    # Check SELECT * (blocking check — returns error, not warning)
    is_select_star, star_msg = check_select_star(sql)
    if is_select_star:
        return "", [star_msg]

    # Check sensitive fields
    sensitive = check_sensitive_fields(sql)
    if sensitive:
        warnings.append(f"查询可能涉及敏感字段: {', '.join(sensitive)}，结果将自动脱敏。")

    # Check window functions with DISTINCT
    window_warnings = check_window_function_distinct(sql)
    warnings.extend(window_warnings)

    # Ensure LIMIT
    sql = add_limit(sql)

    return sql, warnings
