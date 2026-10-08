"""RLS Policy Loader — loads row-level and column-level security policies from DB.

Converts database policies (adh_rls_policies, adh_rls_column_policies,
adh_role_column_access) into DataEngine-compatible format.

Usage:
    from backend.common.rls_loader import load_rls_policies_for_query

    policies = load_rls_policies_for_query(
        user_id=123, workspace_id=1, datasource_id=456, tables=["orders", "users"]
    )
    result = engine_client.query(sql, datasource_id, rls_policies=policies)
"""

import hashlib
import logging
import time
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

# ── RLS policy cache (30s TTL) ────────────────────────────────────────
# Key: (user_id, workspace_id, datasource_id, sorted_tables, user_role)
# Avoids repeated DB queries for the same user/table combination.
_RLS_CACHE_TTL = 30  # seconds
_rls_cache: dict[str, tuple[float, list[dict]]] = {}


def _cache_key(user_id, workspace_id, datasource_id, tables, user_role) -> str:
    raw = f"{user_id}:{workspace_id}:{datasource_id}:{','.join(sorted(tables))}:{user_role}"
    return hashlib.md5(raw.encode()).hexdigest()


def invalidate_rls_cache():
    """Clear the RLS policy cache (call after policy changes)."""
    _rls_cache.clear()


def _resolve_table_refs(tables: list[str], datasource_id: int) -> list[tuple[str, int, str]]:
    """限定名归一 → [(匹配键(与 SQL 引用一致), 所属源 id, 策略裸表名)]。

    三段 `ds.db.t`（跨源联邦）按 adh_datasources.name 解析所属源，未命中
    fail-closed 拒绝（不猜源）；双段/裸表归当前源。匹配键保留原始引用，
    供 DataEngine RLS 施加时对齐（护栏 §10：匹配键按解析后的 (源, 表) 归一）。
    """
    from backend.common.db.datasource_db import get_datasource_by_name
    resolved: list[tuple[str, int, str]] = []
    for t in tables:
        parts = t.split(".")
        if len(parts) == 1:
            resolved.append((t, datasource_id, t))
        elif len(parts) in (2, 3):
            ds_id = datasource_id
            if len(parts) == 3:
                source = get_datasource_by_name(parts[0])
                if not source:
                    raise PermissionError("限定表引用的数据源无法解析，已拒绝执行")
                ds_id = int(source.get("id") or 0)
            resolved.append((t, ds_id, parts[-1]))
        else:
            raise PermissionError("限定表引用格式不受支持，已拒绝执行")
    return resolved


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
        user_role: User role (unused — RLS follows DB policy config, no role bypass).

    Returns:
        List of DataEngine RLSPolicy dicts:
        [{"tables": [...], "row_filter": "...", "hidden_columns": [...],
          "masked_columns": {"col": "pattern"}}]
    """
    # RLS 策略完全按数据库配置生效，不做角色旁路
    if not tables:
        return []

    # 限定名归一：跨源表按所属源加载，匹配键保留原始引用（不串味）
    resolved = _resolve_table_refs(tables, datasource_id)

    # Check cache first (30s TTL)
    key = _cache_key(user_id, workspace_id, datasource_id, tables, user_role)
    now = time.time()
    if key in _rls_cache:
        cached_at, cached_result = _rls_cache[key]
        if now - cached_at < _RLS_CACHE_TTL:
            return cached_result

    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 1. Load user attributes for row-level filtering
                user_attrs = _load_user_attributes(cur, user_id, workspace_id)

                # 2/3. 按源分组加载行级/列级策略（跨源 SQL 每张表取所属源的策略）
                row_policies: list[dict] = []
                column_policies: list[dict] = []
                for ds_id in dict.fromkeys(item[1] for item in resolved):
                    ds_tables = list(dict.fromkeys(
                        bare for _key, rid, bare in resolved if rid == ds_id))
                    for row in _load_row_policies(
                            cur, workspace_id, ds_id, ds_tables, user_id, user_attrs):
                        row["_ds_id"] = ds_id
                        row_policies.append(row)
                    for col in _load_column_policies(
                            cur, workspace_id, ds_id, ds_tables, user_id):
                        col["_ds_id"] = ds_id
                        column_policies.append(col)

                # 4. Merge into DataEngine format
                result = _merge_policies(row_policies, column_policies, resolved)

            # Cache the result
            _rls_cache[key] = (now, result)
            return result
        finally:
            conn.close()
    except PermissionError:
        raise
    except Exception as e:
        logger.error("Failed to load RLS policies: %s", e)
        # fail-closed（护栏 §2/§3）：策略加载失败必须显式拒绝，
        # 不得静默降级为无 RLS 执行（no-silent-degradation）
        raise PermissionError("行级安全策略暂不可用，已拒绝执行") from e


def _load_user_attributes(cur, user_id: int, workspace_id: int) -> dict:
    """Load :user_xxx 属性值 — 统一由用户角色权限管理(角色属性)。

    旧版按用户单独配置的 adh_rls_user_attributes 已退役(表已下线, 不再读取);
    取值失败不得静默吞掉 —— 错值替换会让行过滤产生错数(fail-closed,
    异常交由外层统一拒绝执行)。
    """
    if not user_id:
        return {}
    cur.execute(
        "SELECT DISTINCT ra.attr_key AS attribute_name, ra.attr_value AS attribute_value "
        "FROM adh_role_attributes ra "
        "JOIN adh_user_roles ur ON ra.role_id = ur.role_id "
        "WHERE ur.user_id = %s AND (ra.workspace_id = %s OR ra.workspace_id = 0)",
        (user_id, workspace_id),
    )
    return {row["attribute_name"]: row["attribute_value"] for row in cur.fetchall()}


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
    row_policies: list[dict], column_policies: list[dict],
    resolved: list[tuple[str, int, str]],
) -> list[dict]:
    """Merge row and column policies into DataEngine format.

    resolved: [(匹配键, 所属源 id, 裸表名)] —— 按 (源, 裸表) 匹配策略，
    输出的 tables 用匹配键（与 SQL 引用一致），跨库同名表不串味。
    Returns list of RLSPolicy dicts grouped by table ref.
    """
    # Group row filters by (source, bare table)
    row_filters: dict[tuple, list[str]] = {}
    for rp in row_policies:
        row_filters.setdefault((rp.get("_ds_id"), rp["table_name"]), []).append(rp["filter_expr"])

    # Group column policies by (source, bare table)
    hidden: dict[tuple, list[str]] = {}
    masked: dict[tuple, dict[str, str]] = {}
    for cp in column_policies:
        scope = (cp.get("_ds_id"), cp["table_name"])
        col = cp["column_name"]
        access = cp["access_type"]

        if access == "hidden":
            hidden.setdefault(scope, []).append(col)
        elif access in ("masked", "mask"):
            masked.setdefault(scope, {})[col] = cp.get("mask_pattern") or "default"

    # Build combined policy per table reference
    result = []
    for match_key, ds_id, bare in resolved:
        scope = (ds_id, bare)
        filters = row_filters.get(scope, [])
        hide_cols = hidden.get(scope, [])
        mask_cols = masked.get(scope, {})

        # Only include if there's something to apply
        if not filters and not hide_cols and not mask_cols:
            continue

        # Combine multiple row filters with AND
        combined_filter = " AND ".join(f"({f})" for f in filters) if filters else ""

        result.append({
            "tables": [match_key],
            "row_filter": combined_filter,
            "hidden_columns": hide_cols,
            "masked_columns": mask_cols,
        })

    return result
