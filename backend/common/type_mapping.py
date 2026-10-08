"""Type & manifest mapping helpers — 自 manifest_builder 下沉的纯映射/装载函数。

Phase 2 分层治理: semantics/mdl_compiler 原经 manifest_builder(L2) 取这些 helper,
构成 L1 → L2 反向依赖; 下沉到 L0 common 后, manifest_builder 与 mdl_compiler 共用一份,
语义零改动(manifest_builder 仅剩 _get_tables/build_manifest 编排)。

函数来源: backend/modules/mind/nl2sql/sql/manifest_builder.py(逐字搬移)。
"""

import logging
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

def _get_columns(datasource_id: int) -> list[dict]:
    """Get column metadata for a datasource."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                # 真实列为 data_type/is_key(非 column_type/is_primary_key), 用别名保持下游字段名不变
                "SELECT c.id, c.table_name, c.column_name, c.data_type AS column_type, "
                "COALESCE(NULLIF(c.business_desc, ''), c.column_comment) AS column_comment, "
                "c.is_key AS is_primary_key, c.is_nullable "
                "FROM adh_column_metadata c "
                "JOIN adh_table_info t ON c.table_name = t.table_name "
                "  AND c.datasource_id = t.datasource_id "
                "WHERE c.datasource_id = %s AND c.is_active = 1 AND t.is_active = 1",
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
                # 真实表无 relation_name 列(下游会自动回退 源表_目标表 命名)
                "SELECT source_table, source_column, target_table, target_column, "
                "relation_type, join_type, description "
                "FROM adh_table_relations "
                "WHERE datasource_id = %s AND is_active = 1",
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

    统一由用户角色权限管理: 取用户全部角色的属性(adh_role_attributes via adh_user_roles)。
    旧版按用户单独配置的 adh_rls_user_attributes 已退役(不再读取)。
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
            return {r["attr_key"]: r["attr_value"] for r in cur.fetchall()}
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
