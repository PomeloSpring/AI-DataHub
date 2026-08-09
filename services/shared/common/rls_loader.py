"""RLS Policy Loader — loads row-level and column-level security policies from DB.

Converts database policies (adh_rls_policies, adh_rls_column_policies,
adh_role_column_access) into DataEngine-compatible format.

Usage:
    from services.shared.common.rls_loader import load_rls_policies_for_query

    policies = load_rls_policies_for_query(
        user_id=123, workspace_id=1, datasource_id=456, tables=["orders", "users"]
    )
    result = engine_client.query(sql, datasource_id, rls_policies=policies)
"""

import logging
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def load_rls_policies_for_query(
    user_id: int,
    workspace_id: int,
    datasource_id: int,
    tables: list[str],
    user_role: str = "user",
) -> list[dict]:
    """Load RLS policies applicable to the given user and tables.

    Args:
        user_id: Current user ID.
        workspace_id: Current workspace ID.
        datasource_id: Datasource ID.
        tables: List of table names referenced in the query.
        user_role: User role ("admin" bypasses all policies).

    Returns:
        List of DataEngine RLSPolicy dicts:
        [{"tables": [...], "row_filter": "...", "hidden_columns": [...],
          "masked_columns": {"col": "pattern"}}]
    """
    # Admin users bypass all RLS
    if user_role == "admin":
        return []

    if not tables:
        return []

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 1. Load user attributes for row-level filtering
            user_attrs = _load_user_attributes(cur, user_id)

            # 2. Load row-level policies
            row_policies = _load_row_policies(
                cur, workspace_id, datasource_id, tables, user_id, user_attrs
            )

            # 3. Load column-level policies (from rls_column_policies + role_column_access)
            column_policies = _load_column_policies(
                cur, workspace_id, datasource_id, tables, user_id
            )

            # 4. Merge into DataEngine format
            return _merge_policies(row_policies, column_policies, tables)

    except Exception as e:
        logger.error("Failed to load RLS policies: %s", e)
        return []  # Fail open — no policies applied
    finally:
        conn.close()


def _load_user_attributes(cur, user_id: int) -> dict:
    """Load user attributes for dynamic policy filtering."""
    attrs = {}
    try:
        cur.execute(
            "SELECT attribute_name, attribute_value "
            "FROM adh_rls_user_attributes WHERE user_id = %s",
            (user_id,),
        )
        for row in cur.fetchall():
            attrs[row["attribute_name"]] = row["attribute_value"]
    except Exception as e:
        logger.debug("No user attributes found: %s", e)
    return attrs


def _load_row_policies(
    cur, workspace_id: int, datasource_id: int, tables: list[str],
    user_id: int, user_attrs: dict,
) -> list[dict]:
    """Load active row-level policies for the given tables."""
    if not tables:
        return []

    placeholders = ", ".join(["%s"] * len(tables))
    cur.execute(
        "SELECT id, table_name, filter_type, filter_expr, user_attribute, policy_type "
        "FROM adh_rls_policies "
        "WHERE workspace_id = %s AND datasource_id = %s "
        "AND table_name IN ({placeholders}) AND is_active = 1 "
        "AND policy_type IN ('row', 'both')".format(placeholders=placeholders),
        [workspace_id, datasource_id] + tables,
    )

    policies = []
    for row in cur.fetchall():
        filter_expr = row.get("filter_expr", "")

        # Dynamic substitution: replace {attr} with user attribute values
        if row.get("user_attribute") and row["user_attribute"] in user_attrs:
            attr_val = user_attrs[row["user_attribute"]]
            filter_expr = filter_expr.replace(
                "{" + row["user_attribute"] + "}", str(attr_val)
            )

        if filter_expr:
            policies.append({
                "table_name": row["table_name"],
                "filter_expr": filter_expr,
            })

    return policies


def _load_column_policies(
    cur, workspace_id: int, datasource_id: int, tables: list[str],
    user_id: int,
) -> list[dict]:
    """Load column-level policies from both policy table and role-based access."""
    if not tables:
        return []

    placeholders = ", ".join(["%s"] * len(tables))
    columns = []

    # 1. From adh_rls_column_policies (linked to RLS policies)
    try:
        cur.execute(
            "SELECT cp.column_name, cp.access_type, cp.mask_pattern, p.table_name "
            "FROM adh_rls_column_policies cp "
            "JOIN adh_rls_policies p ON cp.policy_id = p.id "
            "WHERE p.workspace_id = %s AND p.datasource_id = %s "
            "AND p.table_name IN ({placeholders}) AND p.is_active = 1 "
            "AND p.policy_type IN ('column', 'both')".format(placeholders=placeholders),
            [workspace_id, datasource_id] + tables,
        )
        for row in cur.fetchall():
            columns.append({
                "table_name": row["table_name"],
                "column_name": row["column_name"],
                "access_type": row["access_type"],
                "mask_pattern": row.get("mask_pattern"),
            })
    except Exception as e:
        logger.debug("No rls_column_policies found: %s", e)

    # 2. From adh_role_column_access (role-based)
    try:
        # Get user's roles
        cur.execute(
            "SELECT r.id FROM adh_user_roles ur "
            "JOIN adh_roles r ON ur.role_id = r.id "
            "WHERE ur.user_id = %s",
            (user_id,),
        )
        role_ids = [r["id"] for r in cur.fetchall()]

        if role_ids:
            role_placeholders = ", ".join(["%s"] * len(role_ids))
            cur.execute(
                "SELECT table_name, column_name, access_type, mask_pattern "
                "FROM adh_role_column_access "
                "WHERE datasource_id = %s "
                "AND table_name IN ({tp}) "
                "AND role_id IN ({rp})".format(tp=placeholders, rp=role_placeholders),
                [datasource_id] + tables + role_ids,
            )
            for row in cur.fetchall():
                columns.append({
                    "table_name": row["table_name"],
                    "column_name": row["column_name"],
                    "access_type": row["access_type"],
                    "mask_pattern": row.get("mask_pattern"),
                })
    except Exception as e:
        logger.debug("No role_column_access found: %s", e)

    return columns


def _merge_policies(
    row_policies: list[dict], column_policies: list[dict], tables: list[str]
) -> list[dict]:
    """Merge row and column policies into DataEngine format.

    Returns list of RLSPolicy dicts grouped by table set.
    """
    # Group row filters by table
    row_filters: dict[str, list[str]] = {}
    for rp in row_policies:
        tbl = rp["table_name"]
        row_filters.setdefault(tbl, []).append(rp["filter_expr"])

    # Group column policies by table
    hidden: dict[str, list[str]] = {}
    masked: dict[str, dict[str, str]] = {}
    for cp in column_policies:
        tbl = cp["table_name"]
        col = cp["column_name"]
        access = cp["access_type"]

        if access == "hidden":
            hidden.setdefault(tbl, []).append(col)
        elif access in ("masked", "mask"):
            pattern = cp.get("mask_pattern") or "default"
            masked.setdefault(tbl, {})[col] = pattern

    # Build combined policy per table
    result = []
    for table in tables:
        filters = row_filters.get(table, [])
        hide_cols = hidden.get(table, [])
        mask_cols = masked.get(table, {})

        # Only include if there's something to apply
        if not filters and not hide_cols and not mask_cols:
            continue

        # Combine multiple row filters with AND
        combined_filter = " AND ".join(f"({f})" for f in filters) if filters else ""

        result.append({
            "tables": [table],
            "row_filter": combined_filter,
            "hidden_columns": hide_cols,
            "masked_columns": mask_cols,
        })

    return result
