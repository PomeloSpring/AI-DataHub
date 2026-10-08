"""MDL Manifest Builder — converts AI-DataHub metadata to engine-server-rust manifest.

Builds a Model Definition Language (MDL) manifest from:
- adh_table_info → Model
- adh_column_metadata → Column
- adh_table_relations → Relationship
- adh_rls_policies → RowLevelAccessControl

Usage:
    from backend.modules.mind.nl2sql.sql.manifest_builder import build_manifest

    manifest = build_manifest(datasource_id=123)
    # Returns dict compatible with engine-server-rust API
"""

import logging
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn
from backend.common.type_mapping import (
    _get_columns,
    _get_relations,
    _get_rls_policies,
    _get_column_policies,
    _get_user_attributes,
    _map_column_type,
    _build_session_properties_for_rls,
    _map_join_type,
    _find_primary_key,
    _get_data_source_type,
)

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
