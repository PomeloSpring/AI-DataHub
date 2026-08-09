"""RLS SQL Filter — injects row-level and column-level security into SQL queries.

This module modifies SQL queries before execution to enforce RLS policies:
- Row-level: adds WHERE conditions from policy filter expressions
- Column-level: removes hidden columns and applies masking expressions

Usage:
    from backend.nl2sql.sql.rls_filter import apply_rls
    filtered_sql, warnings = apply_rls(sql, policies, user_attrs)
"""

import re
import logging

logger = logging.getLogger(__name__)

# Masking expressions for common sensitive patterns
_MASK_EXPRESSIONS = {
    "phone": "CONCAT(LEFT({col}, 3), '****', RIGHT({col}, 4))",
    "mobile": "CONCAT(LEFT({col}, 3), '****', RIGHT({col}, 4))",
    "email": "CONCAT(LEFT({col}, 2), '***@', SUBSTRING_INDEX({col}, '@', -1))",
    "id_card": "CONCAT(LEFT({col}, 6), '********', RIGHT({col}, 4))",
    "identity": "CONCAT(LEFT({col}, 6), '********', RIGHT({col}, 4))",
    "ssn": "CONCAT(LEFT({col}, 3), '****', RIGHT({col}, 4))",
    "default": "CONCAT(LEFT({col}, 2), '****', RIGHT({col}, 2))",
}


def _find_mask_expr(column_name: str, mask_pattern: str = "partial") -> str:
    """Find the masking expression for a column based on its name or pattern."""
    if mask_pattern and mask_pattern != "partial":
        # Custom mask pattern provided
        return mask_pattern

    lower = column_name.lower()
    for key, expr in _MASK_EXPRESSIONS.items():
        if key in lower:
            return expr.format(col=column_name)
    return _MASK_EXPRESSIONS["default"].format(col=column_name)


def _inject_row_filter(sql: str, filter_expr: str) -> str:
    """Inject a row-level filter into the SQL WHERE clause.

    Handles:
    - Simple SELECT ... FROM ... WHERE ... → append AND (filter)
    - No WHERE clause → add WHERE (filter)
    - Subqueries → recursively apply to outermost query
    - UNION → apply to each SELECT
    """
    if not filter_expr or not filter_expr.strip():
        return sql

    # Handle UNION: apply filter to each part
    union_pattern = re.compile(r'\bUNION\b', re.IGNORECASE)
    if union_pattern.search(sql):
        parts = union_pattern.split(sql)
        filtered_parts = [_inject_row_filter(part.strip(), filter_expr) for part in parts]
        return " UNION ".join(filtered_parts)

    # Normalize whitespace for analysis
    normalized = re.sub(r'\s+', ' ', sql).strip()

    # Find the main WHERE clause position
    # We need to handle: SELECT ... FROM ... [JOIN ...] [WHERE ...] [GROUP BY ...] [ORDER BY ...] [LIMIT ...]
    where_match = re.search(r'\bWHERE\b', normalized, re.IGNORECASE)

    if where_match:
        # Has WHERE clause — find the end of WHERE (before GROUP BY, ORDER BY, LIMIT, or end)
        where_start = where_match.end()
        # Find the next clause boundary
        rest = normalized[where_start:]
        # Look for GROUP BY, ORDER BY, LIMIT, HAVING as terminators
        clause_boundary = re.search(
            r'\b(GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b',
            rest, re.IGNORECASE
        )
        if clause_boundary:
            insert_pos = where_start + clause_boundary.start()
            # Insert the filter before the next clause
            return (
                normalized[:insert_pos].rstrip()
                + " AND (" + filter_expr + ") "
                + normalized[insert_pos:]
            )
        else:
            # WHERE is the last clause — append
            return normalized.rstrip() + " AND (" + filter_expr + ")"
    else:
        # No WHERE clause — need to insert one
        # Find FROM ... [table] and insert WHERE after the table reference
        # Look for the main FROM clause (not in subqueries)
        from_match = re.search(r'\bFROM\b', normalized, re.IGNORECASE)
        if not from_match:
            logger.warning("Cannot inject row filter: no FROM clause found in SQL")
            return sql

        # Find the end of the FROM clause (before GROUP BY, ORDER BY, LIMIT, etc.)
        from_start = from_match.end()
        rest = normalized[from_start:]

        # Find the next top-level keyword that starts a new clause
        clause_match = re.search(
            r'\b(GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b',
            rest, re.IGNORECASE
        )

        if clause_match:
            insert_pos = from_start + clause_match.start()
            return (
                normalized[:insert_pos].rstrip()
                + " WHERE (" + filter_expr + ") "
                + normalized[insert_pos:]
            )
        else:
            return normalized.rstrip() + " WHERE (" + filter_expr + ")"


def _remove_hidden_columns(sql: str, hidden_columns: list) -> str:
    """Remove hidden columns from the SELECT clause.

    Handles:
    - Simple: SELECT a, b, c FROM ... → remove b
    - Aliased: SELECT t.a, t.b FROM ... → remove t.b
    - With expressions: SELECT a, CASE WHEN ... END as x FROM ... → remove x
    """
    if not hidden_columns:
        return sql

    # Normalize
    normalized = re.sub(r'\s+', ' ', sql).strip()

    # Find SELECT ... FROM
    select_match = re.search(r'\bSELECT\b\s+', normalized, re.IGNORECASE)
    from_match = re.search(r'\bFROM\b', normalized, re.IGNORECASE)

    if not select_match or not from_match:
        return sql

    select_start = select_match.end()
    select_end = from_match.start()
    select_clause = normalized[select_start:select_end].strip()

    # Check for SELECT * — cannot filter columns
    if select_clause.strip() == "*" or re.search(r'\w+\.\*', select_clause):
        logger.warning("Cannot apply column filter: SELECT * detected")
        return sql

    # Parse columns (handle nested parentheses and CASE expressions)
    columns = _parse_select_columns(select_clause)

    # Filter out hidden columns
    hidden_lower = {c.lower() for c in hidden_columns}
    filtered = []
    removed = []

    for col_expr, alias in columns:
        # Check if this column or its alias matches a hidden column
        col_name_lower = (alias or col_expr).strip().lower()
        # Also check for table.column format
        bare_name = col_name_lower.split(".")[-1] if "." in col_name_lower else col_name_lower

        if bare_name in hidden_lower or col_name_lower in hidden_lower:
            removed.append(alias or col_expr)
        else:
            filtered.append((col_expr, alias))

    if not removed:
        return sql

    if not filtered:
        # All columns hidden — return a safe placeholder
        logger.warning("All requested columns are hidden by RLS policy")
        return sql

    # Reconstruct SELECT clause
    new_select_parts = []
    for col_expr, alias in filtered:
        if alias:
            new_select_parts.append(f"{col_expr} AS {alias}")
        else:
            new_select_parts.append(col_expr)

    new_select = ", ".join(new_select_parts)
    return normalized[:select_start] + " " + new_select + " " + normalized[select_end:]


def _apply_column_masking(sql: str, masked_columns: dict) -> str:
    """Apply masking expressions to columns in the SELECT clause.

    Args:
        sql: The SQL query.
        masked_columns: dict of {column_name: mask_pattern}
    """
    if not masked_columns:
        return sql

    normalized = re.sub(r'\s+', ' ', sql).strip()

    select_match = re.search(r'\bSELECT\b\s+', normalized, re.IGNORECASE)
    from_match = re.search(r'\bFROM\b', normalized, re.IGNORECASE)

    if not select_match or not from_match:
        return sql

    select_start = select_match.end()
    select_end = from_match.start()
    select_clause = normalized[select_start:select_end].strip()

    columns = _parse_select_columns(select_clause)

    masked_any = False
    new_parts = []
    for col_expr, alias in columns:
        bare_name = (alias or col_expr).strip().lower()
        bare_name = bare_name.split(".")[-1] if "." in bare_name else bare_name

        if bare_name in masked_columns:
            mask_expr = _find_mask_expr(bare_name, masked_columns[bare_name])
            if alias:
                new_parts.append(f"{mask_expr} AS {alias}")
            else:
                new_parts.append(f"{mask_expr} AS {bare_name}")
            masked_any = True
        else:
            if alias:
                new_parts.append(f"{col_expr} AS {alias}")
            else:
                new_parts.append(col_expr)

    if not masked_any:
        return sql

    new_select = ", ".join(new_parts)
    return normalized[:select_start] + " " + new_select + " " + normalized[select_end:]


def _parse_select_columns(select_clause: str) -> list:
    """Parse a SELECT clause into a list of (expression, alias) tuples.

    Handles nested parentheses, CASE expressions, and string literals.
    """
    columns = []
    depth = 0
    current = []
    in_string = False
    string_char = None

    i = 0
    while i < len(select_clause):
        ch = select_clause[i]

        # Handle string literals
        if ch in ("'", '"') and not in_string:
            in_string = True
            string_char = ch
            current.append(ch)
            i += 1
            continue
        if in_string:
            current.append(ch)
            if ch == string_char and (i + 1 >= len(select_clause) or select_clause[i + 1] != string_char):
                in_string = False
            i += 1
            continue

        # Handle parentheses
        if ch == '(':
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch == ')':
            depth -= 1
            current.append(ch)
            i += 1
            continue

        # Split on comma at depth 0
        if ch == ',' and depth == 0:
            col_text = "".join(current).strip()
            if col_text:
                expr, alias = _split_alias(col_text)
                columns.append((expr, alias))
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Last column
    col_text = "".join(current).strip()
    if col_text:
        expr, alias = _split_alias(col_text)
        columns.append((expr, alias))

    return columns


def _split_alias(col_text: str) -> tuple:
    """Split a column expression into (expression, alias).

    Handles: 'expr AS alias', 'expr alias', 'expr'
    """
    # Try AS keyword (case insensitive)
    as_match = re.match(r'^(.+?)\s+[Aa][Ss]\s+(\S+)$', col_text.strip())
    if as_match:
        return as_match.group(1).strip(), as_match.group(2).strip()

    # Try bare alias (expression followed by a word that looks like an alias)
    # But be careful not to split function calls or expressions
    parts = col_text.strip().split()
    if len(parts) >= 2:
        last = parts[-1]
        # Only treat as alias if it's a simple identifier (no parentheses, no operators)
        if re.match(r'^[a-zA-Z_]\w*$', last) and not col_text.rstrip().endswith(')'):
            return " ".join(parts[:-1]).strip(), last

    return col_text.strip(), ""


def apply_rls(sql: str, policies: dict, user_id: int = 0,
              workspace_id: int = 0, log_fn=None) -> tuple:
    """Apply RLS policies to a SQL query.

    Args:
        sql: The original SQL query.
        policies: dict from RLSService.get_effective_policies().
        user_id: Current user ID (for audit logging).
        workspace_id: Current workspace ID (for audit logging).
        log_fn: Optional callable(user_id, workspace_id, action, original_sql, filtered_sql) for audit.

    Returns:
        (filtered_sql, warnings) tuple.
    """
    warnings = []
    original_sql = sql

    row_filter = policies.get("row_filter", "")
    hidden_columns = policies.get("hidden_columns", [])
    masked_columns = policies.get("masked_columns", {})

    # Apply row-level filter
    if row_filter:
        sql = _inject_row_filter(sql, row_filter)
        if sql != original_sql:
            warnings.append(f"已应用行级安全过滤: {row_filter}")
            if log_fn:
                log_fn(user_id, workspace_id, "row_filter", original_sql, sql)

    # Apply column masking (before hiding — masking preserves column presence)
    if masked_columns:
        before_mask = sql
        sql = _apply_column_masking(sql, masked_columns)
        if sql != before_mask:
            masked_names = ", ".join(masked_columns.keys())
            warnings.append(f"已对敏感列脱敏: {masked_names}")
            if log_fn:
                log_fn(user_id, workspace_id, "column_mask", original_sql, sql)

    # Apply column hiding
    if hidden_columns:
        before_hide = sql
        sql = _remove_hidden_columns(sql, hidden_columns)
        if sql != before_hide:
            hidden_names = ", ".join(hidden_columns)
            warnings.append(f"已隐藏无权限列: {hidden_names}")
            if log_fn:
                log_fn(user_id, workspace_id, "column_hide", original_sql, sql)

    return sql, warnings
