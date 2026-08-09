"""MDL Manifest Builder — converts AI-DataHub metadata to engine-server-rust manifest.

Builds a Model Definition Language (MDL) manifest from:
- adh_table_info → Model
- adh_column_metadata → Column
- adh_table_relations → Relationship
- adh_rls_policies → RowLevelAccessControl

Usage:
    from services.datamind.nl2sql.sql.manifest_builder import build_manifest

    manifest = build_manifest(datasource_id=123)
    # Returns dict compatible with engine-server-rust API
"""

import logging
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _get_tables(datasource_id: int) -> list[dict]:
    """Get table metadata for a datasource."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, table_comment, db_name "
                "FROM adh_table_info "
                "WHERE datasource_id = %s AND is_active = 1",
                (datasource_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _get_columns(datasource_id: int) -> list[dict]:
    """Get column metadata for a datasource."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.id, c.table_name, c.column_name, c.column_type, "
                "c.column_comment, c.is_primary_key, c.is_nullable "
                "FROM adh_column_metadata c "
                "JOIN adh_table_info t ON c.table_name = t.table_name "
                "  AND c.datasource_id = t.datasource_id "
                "WHERE c.datasource_id = %s AND t.is_active = 1",
                (datasource_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _get_relations(datasource_id: int) -> list[dict]:
    """Get table relations for a datasource."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_table, source_column, target_table, target_column, "
                "relation_type, relation_name "
                "FROM adh_table_relations "
                "WHERE datasource_id = %s",
                (datasource_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _get_rls_policies(datasource_id: int, workspace_id: int = 0) -> list[dict]:
    """Get RLS policies for a datasource.

    Returns policies with normalized field names:
    - policy_name: from adh_rls_policies.name
    - row_filter: from adh_rls_policies.filter_expr
    - user_attribute: for dynamic filtering based on user attributes
    """
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, table_name, policy_type, "
                "filter_type, filter_expr, user_attribute, is_active "
                "FROM adh_rls_policies "
                "WHERE datasource_id = %s AND workspace_id = %s AND is_active = 1",
                (datasource_id, workspace_id),
            )
            policies = cur.fetchall()

            # Normalize field names for manifest builder
            result = []
            for p in policies:
                result.append({
                    "id": p["id"],
                    "table_name": p["table_name"],
                    "policy_name": p["name"],
                    "row_filter": p.get("filter_expr", ""),
                    "description": p.get("description", ""),
                    "filter_type": p.get("filter_type", "condition"),
                    "user_attribute": p.get("user_attribute", ""),
                    "policy_type": p.get("policy_type", "both"),
                })
            return result
    finally:
        conn.close()


def _get_column_policies(datasource_id: int, workspace_id: int = 0) -> list[dict]:
    """Get column-level RLS policies for a datasource.

    Returns column policies with:
    - table_name, column_name, access_type (visible/hidden/masked), mask_pattern
    """
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cp.column_name, cp.access_type, cp.mask_pattern, "
                "p.table_name, p.id as policy_id "
                "FROM adh_rls_column_policies cp "
                "JOIN adh_rls_policies p ON cp.policy_id = p.id "
                "WHERE p.datasource_id = %s AND p.workspace_id = %s AND p.is_active = 1",
                (datasource_id, workspace_id),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _get_user_attributes(user_id: int, workspace_id: int) -> dict:
    """Get user attributes for dynamic RLS filtering.

    Checks role-based attributes first, then legacy per-user attributes.
    """
    if not user_id:
        return {}

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Role-based attributes (from adh_role_attributes via adh_user_roles)
            cur.execute(
                "SELECT DISTINCT ra.attr_key, ra.attr_value "
                "FROM adh_role_attributes ra "
                "JOIN adh_user_roles ur ON ra.role_id = ur.role_id "
                "WHERE ur.user_id = %s AND (ra.workspace_id = %s OR ra.workspace_id = 0)",
                (user_id, workspace_id),
            )
            attrs = {r["attr_key"]: r["attr_value"] for r in cur.fetchall()}

            # Legacy per-user attributes (fill gaps)
            cur.execute(
                "SELECT attr_key, attr_value FROM adh_rls_user_attributes "
                "WHERE user_id = %s AND workspace_id = %s",
                (user_id, workspace_id),
            )
            for r in cur.fetchall():
                if r["attr_key"] not in attrs:
                    attrs[r["attr_key"]] = r["attr_value"]

            return attrs
    finally:
        conn.close()


def _map_column_type(mysql_type: str) -> str:
    """Map MySQL column type to engine-server-rust type string."""
    if not mysql_type:
        return "varchar"

    type_lower = mysql_type.lower().strip()

    # Integer types
    if "bigint" in type_lower:
        return "int64"
    if "int" in type_lower or "mediumint" in type_lower:
        return "int32"
    if "smallint" in type_lower:
        return "int16"
    if "tinyint" in type_lower:
        return "int8"

    # Float types
    if "double" in type_lower:
        return "float64"
    if "float" in type_lower:
        return "float32"
    if "decimal" in type_lower or "numeric" in type_lower:
        return "decimal"

    # Date/Time types
    if "datetime" in type_lower or "timestamp" in type_lower:
        return "datetime"
    if "date" in type_lower:
        return "date"
    if "time" in type_lower:
        return "time"

    # Binary types
    if "blob" in type_lower or "binary" in type_lower or "varbinary" in type_lower:
        return "binary"

    # JSON type
    if "json" in type_lower:
        return "json"

    # Boolean type
    if "bool" in type_lower:
        return "bool"

    # Default: string
    return "string"


def _build_session_properties_for_rls(policies: list[dict]) -> list[dict]:
    """Build session property definitions from RLS policies.

    Each RLS policy's row_filter may contain placeholders like @session_user_id.
    We extract these and create SessionProperty definitions.
    """
    import re

    properties = []
    seen = set()

    for policy in policies:
        row_filter = policy.get("row_filter", "")
        # Find @variable references in the filter
        matches = re.findall(r"@(\w+)", row_filter)
        for var_name in matches:
            if var_name not in seen:
                seen.add(var_name)
                properties.append({
                    "name": var_name,
                    "required": True,
                    "defaultExpr": None,
                })

    return properties


def build_manifest(
    datasource_id: int,
    workspace_id: int = 0,
    table_names: list[str] = None,
    user_id: int = 0,
) -> dict:
    """Build MDL manifest from AI-DataHub metadata.

    Args:
        datasource_id: The datasource ID to build manifest for.
        workspace_id: Workspace ID for RLS policy filtering.
        table_names: Optional list of table names to include.
                     If None, includes all active tables.
        user_id: User ID for user-attribute-based RLS filtering.

    Returns:
        Dict compatible with engine-server-rust Manifest format.
    """
    # Get metadata
    tables = _get_tables(datasource_id)
    columns = _get_columns(datasource_id)
    relations = _get_relations(datasource_id)
    rls_policies = _get_rls_policies(datasource_id, workspace_id)
    column_policies = _get_column_policies(datasource_id, workspace_id)

    # Get user attributes for dynamic RLS
    user_attrs = _get_user_attributes(user_id, workspace_id) if user_id else {}

    # Filter tables if specified
    if table_names:
        table_name_set = set(table_names)
        tables = [t for t in tables if t["table_name"] in table_name_set]

    # Build table name set for column filtering
    active_tables = {t["table_name"] for t in tables}

    # Group columns by table
    columns_by_table: dict[str, list[dict]] = {}
    for col in columns:
        tbl = col["table_name"]
        if tbl in active_tables:
            columns_by_table.setdefault(tbl, []).append(col)

    # Group RLS policies by table
    rls_by_table: dict[str, list[dict]] = {}
    for policy in rls_policies:
        tbl = policy["table_name"]
        rls_by_table.setdefault(tbl, []).append(policy)

    # Group column policies by table
    col_policies_by_table: dict[str, dict] = {}  # {table: {col: {access_type, mask_pattern}}}
    for cp in column_policies:
        tbl = cp["table_name"]
        col = cp["column_name"]
        if tbl not in col_policies_by_table:
            col_policies_by_table[tbl] = {}
        col_policies_by_table[tbl][col] = {
            "access_type": cp["access_type"],
            "mask_pattern": cp.get("mask_pattern", ""),
        }

    # Build relationships
    relationships = []
    for rel in relations:
        src_table = rel["source_table"]
        tgt_table = rel["target_table"]

        # Only include relationships where both tables are active
        if src_table not in active_tables or tgt_table not in active_tables:
            continue

        rel_name = rel.get("relation_name") or f"{src_table}_{tgt_table}"
        join_type = _map_join_type(rel.get("relation_type", "many_to_one"))

        relationships.append({
            "name": rel_name,
            "models": [src_table, tgt_table],
            "joinType": join_type,
            "condition": f"{src_table}.{rel['source_column']} = {tgt_table}.{rel['target_column']}",
        })

    # Build models
    models = []
    for table in tables:
        tbl_name = table["table_name"]
        tbl_cols = columns_by_table.get(tbl_name, [])

        # Get column policies for this table
        tbl_col_policies = col_policies_by_table.get(tbl_name, {})

        # Build columns
        mdl_columns = []
        for col in tbl_cols:
            col_name = col["column_name"]
            col_type = _map_column_type(col.get("column_type", ""))

            # Check column-level RLS
            col_policy = tbl_col_policies.get(col_name, {})
            is_hidden = col_policy.get("access_type") == "hidden"

            mdl_col = {
                "name": col_name,
                "type": col_type,
                "isCalculated": False,
                "notNull": col.get("is_primary_key", 0) == 1 or col.get("is_nullable", "YES") == "NO",
                "isHidden": is_hidden,
            }

            # Add masking info if column is masked
            if col_policy.get("access_type") == "masked":
                mdl_col["masking"] = {
                    "type": col_policy.get("mask_pattern", "partial"),
                }

            mdl_columns.append(mdl_col)

        # Add relationship columns for related tables
        for rel in relationships:
            if tbl_name in rel["models"]:
                other_model = [m for m in rel["models"] if m != tbl_name][0]
                mdl_columns.append({
                    "name": other_model,
                    "type": "object",
                    "relationship": rel["name"],
                    "isCalculated": False,
                    "notNull": False,
                    "isHidden": False,
                })

        # Build RLS access controls with user attribute substitution
        rls_controls = []
        for policy in rls_by_table.get(tbl_name, []):
            session_props = _build_session_properties_for_rls([policy])

            # Get the row filter condition
            condition = policy.get("row_filter", "")

            # If filter_type is user_attribute, substitute :user_xxx with actual value
            if policy.get("filter_type") == "user_attribute" and policy.get("user_attribute"):
                attr_key = policy["user_attribute"]
                attr_val = user_attrs.get(attr_key, "")
                # Replace :user_region with actual value like '华东'
                condition = condition.replace(f":user_{attr_key}", f"'{attr_val}'")

            if condition:
                rls_controls.append({
                    "name": policy.get("policy_name", f"rls_{policy['id']}"),
                    "requiredProperties": session_props,
                    "condition": condition,
                })

        # Build table reference (catalog.schema.table)
        db_name = table.get("db_name", "")
        table_ref = f"{db_name}.{tbl_name}" if db_name else tbl_name

        model = {
            "name": tbl_name,
            "tableReference": table_ref,
            "columns": mdl_columns,
            "primaryKey": _find_primary_key(tbl_cols),
            "cached": False,
        }

        if rls_controls:
            model["rowLevelAccessControls"] = rls_controls

        models.append(model)

    # Determine data source type
    data_source = _get_data_source_type(datasource_id)

    # Build manifest
    manifest = {
        "catalog": "adh",
        "schema": "public",
        "models": models,
        "relationships": relationships,
        "views": [],
        "dataSource": data_source,
    }

    logger.info(
        "Built manifest for ds=%d: %d models, %d relationships, user_id=%d, attrs=%s",
        datasource_id, len(models), len(relationships), user_id, user_attrs,
    )

    return manifest


def _map_join_type(relation_type: str) -> str:
    """Map AI-DataHub relation type to MDL JoinType."""
    type_map = {
        "one_to_one": "ONE_TO_ONE",
        "one_to_many": "ONE_TO_MANY",
        "many_to_one": "MANY_TO_ONE",
        "many_to_many": "MANY_TO_MANY",
        "1:1": "ONE_TO_ONE",
        "1:n": "ONE_TO_MANY",
        "n:1": "MANY_TO_ONE",
        "n:m": "MANY_TO_MANY",
    }
    return type_map.get(relation_type.lower() if relation_type else "", "MANY_TO_ONE")


def _find_primary_key(columns: list[dict]) -> Optional[str]:
    """Find primary key column name from column list."""
    for col in columns:
        if col.get("is_primary_key", 0) == 1:
            return col["column_name"]
    return None


def _get_data_source_type(datasource_id: int) -> str:
    """Get the data source type string for engine-server-rust."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT db_type FROM adh_datasources WHERE id = %s",
                (datasource_id,),
            )
            row = cur.fetchone()
            if row:
                db_type = row.get("db_type", "mysql").lower()
                type_map = {
                    "mysql": "MYSQL",
                    "doris": "DORIS",
                    "postgresql": "POSTGRES",
                    "postgres": "POSTGRES",
                    "clickhouse": "CLICKHOUSE",
                }
                return type_map.get(db_type, "MYSQL")
            return "MYSQL"
    finally:
        conn.close()
