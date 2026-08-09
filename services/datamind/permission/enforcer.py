"""Permission Enforcement Layer — unified data access control.

Integrates role_service (RBAC) and rls_service (row/column security)
into the query execution pipeline WITHOUT external dependencies (no Ranger/LDAP/Kerberos).

Permission model:
  1. Datasource access — role_service.get_user_allowed_datasources()
  2. Table access — role_service.get_user_allowed_tables()
  3. Column restrictions — role_service.get_user_column_restrictions()
  4. Row-level security — rls_service.get_effective_policies()
  5. Sensitive field marks — datagov adh_sensitive_fields（对所有用户生效，含管理员）
"""

import re
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

import pandas as pd

logger = logging.getLogger(__name__)

# datagov adh_sensitive_fields.mask_type → enforcer 脱敏方式
_SENSITIVE_MASK_MAP = {
    "full": "null",      # 完全遮蔽 → 置空
    "partial": "partial",
    "hash": "hash",
    # "none" → 不脱敏
}


@dataclass
class PermissionResult:
    """Result of permission check."""
    allowed: bool = True
    reason: str = ""
    row_filter: str = ""
    hidden_columns: list = field(default_factory=list)
    masked_columns: dict = field(default_factory=dict)  # {col: mask_type}
    policies_applied: list = field(default_factory=list)


class PermissionEnforcer:
    """Unified permission enforcement for query execution.

    Uses role_service and rls_service for policy evaluation.
    No external dependencies — all policies stored in MySQL.
    """

    def check_access(
        self,
        user_id: int,
        workspace_id: int,
        datasource_id: int,
        table_name: str = "",
        columns: list = None,
    ) -> PermissionResult:
        """Check user access to a datasource/table/columns.

        Args:
            user_id: User ID from JWT token.
            workspace_id: Workspace context.
            datasource_id: Target datasource.
            table_name: Target table (empty = datasource-level check only).
            columns: Columns being accessed (empty = all).

        Returns:
            PermissionResult with allowed, row_filter, hidden/masked columns.
        """
        from services.authservice.services.role_service import role_service
        from services.authservice.services.rls_service import rls_service

        result = PermissionResult()

        # 敏感字段标记（datagov）— 治理基线，对所有用户生效（含管理员）
        sensitive_masked = {}
        if table_name:
            sensitive_masked = self._get_sensitive_masks(
                workspace_id, datasource_id, table_name
            )
            if sensitive_masked:
                result.masked_columns.update(sensitive_masked)
                result.policies_applied.append(
                    f"sensitive_fields:{','.join(sorted(sensitive_masked))}"
                )

        # Admin bypass — admins have full access (敏感字段脱敏除外，已在上方生效)
        user_roles = role_service.get_user_roles(user_id, workspace_id)
        is_admin = any(r.get("name") == "admin" for r in user_roles)
        if is_admin:
            return result

        # Step 1: Check datasource access
        allowed_ds = role_service.get_user_allowed_datasources(user_id, workspace_id)
        if allowed_ds and datasource_id not in allowed_ds:
            result.allowed = False
            result.reason = f"无权访问数据源 {datasource_id}"
            return result

        # Step 2: Check table access (if table specified)
        if table_name:
            allowed_tables = role_service.get_user_allowed_tables(
                user_id, datasource_id, workspace_id
            )
            if allowed_tables and table_name not in allowed_tables:
                result.allowed = False
                result.reason = f"无权访问表 {table_name}"
                return result

            # Step 3: Get column restrictions
            col_restrictions = role_service.get_user_column_restrictions(
                user_id, datasource_id, table_name, workspace_id
            )
            result.hidden_columns = col_restrictions.get("hidden_columns", [])
            # 角色策略不得弱化敏感基线（不覆盖敏感字段的脱敏方式）
            for col, mask in col_restrictions.get("masked_columns", {}).items():
                if col not in sensitive_masked:
                    result.masked_columns[col] = mask

            # Step 4: Get RLS row filter
            rls_policies = rls_service.get_effective_policies(
                user_id, workspace_id, datasource_id, table_name
            )
            result.row_filter = rls_policies.get("row_filter", "")
            result.policies_applied = rls_policies.get("policies_applied", [])

            # Merge RLS column policies (RLS overrides role-level, but never weakens sensitive baseline)
            rls_hidden = rls_policies.get("hidden_columns", [])
            rls_masked = rls_policies.get("masked_columns", {})
            for col in rls_hidden:
                if col not in result.hidden_columns:
                    result.hidden_columns.append(col)
            for col, mask in rls_masked.items():
                if col not in sensitive_masked:
                    result.masked_columns[col] = mask

        return result

    def enforce_sql(
        self,
        sql: str,
        user_id: int,
        workspace_id: int,
        datasource_id: int,
        tables: list = None,
    ) -> tuple[str, PermissionResult]:
        """Enforce permissions on a SQL query.

        Modifies SQL to inject row-level filters and returns post-execution
        column restrictions.

        Args:
            sql: Original SQL query.
            user_id: User ID.
            workspace_id: Workspace context.
            datasource_id: Target datasource.
            tables: Table names extracted from SQL (auto-detected if None).

        Returns:
            (modified_sql, PermissionResult)
        """
        # Auto-detect tables if not provided
        if tables is None:
            tables = self._extract_tables(sql)

        if not tables:
            # No tables found, check datasource-level only
            result = self.check_access(user_id, workspace_id, datasource_id)
            if not result.allowed:
                raise PermissionError(result.reason)
            return sql, result

        # Check access for each table and merge results
        combined_result = PermissionResult()
        modified_sql = sql

        for table in tables:
            result = self.check_access(
                user_id, workspace_id, datasource_id, table
            )
            if not result.allowed:
                raise PermissionError(result.reason)

            # Inject row filter
            if result.row_filter:
                modified_sql = self._inject_row_filter(modified_sql, table, result.row_filter)

            # Merge column restrictions
            for col in result.hidden_columns:
                if col not in combined_result.hidden_columns:
                    combined_result.hidden_columns.append(col)
            combined_result.masked_columns.update(result.masked_columns)
            combined_result.policies_applied.extend(result.policies_applied)

        combined_result.row_filter = modified_sql != sql
        return modified_sql, combined_result

    def apply_post_processing(
        self,
        df: pd.DataFrame,
        result: PermissionResult,
    ) -> pd.DataFrame:
        """Apply column hiding and masking after query execution.

        Args:
            df: Query result DataFrame.
            result: PermissionResult from enforce_sql().

        Returns:
            Processed DataFrame with hidden columns removed and masked columns obfuscated.
        """
        if df is None or df.empty:
            return df

        # Remove hidden columns
        for col in result.hidden_columns:
            if col in df.columns:
                df = df.drop(columns=[col])
                logger.debug("Hidden column removed: %s", col)

        # Apply masking
        for col, mask_type in result.masked_columns.items():
            if col not in df.columns:
                continue
            df[col] = df[col].apply(lambda v: self._mask_value(v, mask_type))
            logger.debug("Column masked: %s (%s)", col, mask_type)

        return df

    # ── Internal helpers ───────────────────────────────────────────

    def _get_sensitive_masks(
        self, workspace_id: int, datasource_id: int, table_name: str
    ) -> dict:
        """从 datagov 敏感字段标记加载脱敏规则 {column: mask_type}.

        匹配当前工作空间或全局（workspace_id=0）标记；失败不阻断查询。
        """
        try:
            from services.shared.common.db import execute_query
            rows = execute_query(
                """SELECT column_name, mask_type FROM adh_sensitive_fields
                   WHERE datasource_id = %s AND table_name = %s
                     AND workspace_id IN (%s, 0)""",
                (datasource_id, table_name, workspace_id),
            )
            masks = {}
            for row in rows:
                mask = _SENSITIVE_MASK_MAP.get(row.get("mask_type") or "")
                if mask:
                    masks[row["column_name"]] = mask
            return masks
        except Exception as e:
            logger.warning("Load sensitive masks failed for %s: %s", table_name, e)
            return {}

    def _extract_tables(self, sql: str) -> list:
        """Extract table names from SQL FROM/JOIN clauses."""
        pattern = r'\b(?:FROM|JOIN)\s+(\w+)'
        tables = re.findall(pattern, sql, re.IGNORECASE)
        # Deduplicate while preserving order
        seen = set()
        result = []
        for t in tables:
            t_lower = t.lower()
            if t_lower not in seen and t_lower not in ('select', 'where', 'and', 'or', 'set'):
                seen.add(t_lower)
                result.append(t)
        return result

    def _inject_row_filter(self, sql: str, table: str, row_filter: str) -> str:
        """Inject row-level filter into SQL by wrapping table with subquery.

        SELECT * FROM orders → SELECT * FROM (SELECT * FROM orders WHERE region='cn') AS orders
        """
        if not row_filter:
            return sql

        # Find FROM table and wrap with filtered subquery
        pattern = rf'(\bFROM\s+){re.escape(table)}(\s|,|WHERE|GROUP|ORDER|LIMIT|$)'
        replacement = rf'\1(SELECT * FROM {table} WHERE {row_filter}) AS {table}\2'
        result = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

        if result == sql:
            # Try without trailing context
            pattern = rf'(\bFROM\s+){re.escape(table)}(\s)'
            replacement = rf'\1(SELECT * FROM {table} WHERE {row_filter}) AS {table}\2'
            result = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

        return result

    def _mask_value(self, value, mask_type: str):
        """Apply masking to a single value."""
        if value is None:
            return None

        value_str = str(value)

        if mask_type == "null":
            return None
        elif mask_type == "hash":
            return hashlib.sha256(value_str.encode()).hexdigest()[:16]
        elif mask_type == "partial":
            return self._partial_mask(value_str)
        else:
            # Default: partial masking
            return self._partial_mask(value_str)

    def _partial_mask(self, value: str) -> str:
        """Partial mask: keep first 2 and last 2 chars, mask middle."""
        if len(value) <= 4:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]


# Singleton instance
permission_enforcer = PermissionEnforcer()
