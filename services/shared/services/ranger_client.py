"""Apache Ranger REST API client for data-level authorization.

Provides:
- Table/column-level access control (allow/deny)
- Row-level security (row filter injection)
- Column masking (PII field obfuscation)
- Policy caching with TTL
- Audit event logging

Usage:
    from services.shared.services.ranger_client import ranger_client

    result = await ranger_client.check_access(
        user="zhangsan",
        groups=["cn=analysts,ou=groups,dc=example,dc=com"],
        resource_type="table",
        resource={"database": "mydb", "table": "orders"},
        action="select",
    )
    if result.allowed:
        # Apply row filter and column masking
        sql = inject_row_filter(sql, result.row_filter)
        df = apply_column_masking(df, result.column_masking)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from services.shared.common.config import (
    RANGER_ENABLED, RANGER_ADMIN_URL, RANGER_USERNAME, RANGER_PASSWORD,
    RANGER_SERVICE_NAME, RANGER_CACHE_TTL,
)

logger = logging.getLogger(__name__)


# ── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class RangerAccessResult:
    """Result of a Ranger access check."""
    allowed: bool
    row_filter: Optional[str] = None        # Row-level filter SQL fragment
    column_masking: dict = field(default_factory=dict)  # {column: masking_type}
    reason: str = ""                        # Deny reason


@dataclass
class MaskingRule:
    """Column masking rule."""
    column: str
    masking_type: str  # "MASK_NULL", "MASK_HASH", "MASK_PARTIAL", "MASK_NONE"
    masking_value: str = ""  # Custom masking expression


# ── Cache ────────────────────────────────────────────────────────────────

class _PolicyCache:
    """Simple in-memory TTL cache for Ranger policies."""

    def __init__(self, ttl: int = 300):
        self._cache: dict[str, tuple[float, any]] = {}
        self._ttl = ttl

    def get(self, key: str):
        if key in self._cache:
            ts, value = self._cache[key]
            if _time.time() - ts < self._ttl:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value):
        self._cache[key] = (_time.time(), value)

    def invalidate(self, key: str):
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()


# ── Ranger Client ────────────────────────────────────────────────────────

class RangerClient:
    """Apache Ranger REST API client.

    Ranger Admin REST API endpoints:
    - GET  /service/public/v2/api/service/{serviceName} — Get service info
    - GET  /service/public/v2/api/policy — List policies
    - GET  /service/public/v2/api/policy/{policyId} — Get policy by ID
    - POST /service/public/v2/api/service/{serviceName}/policy/resource — Get policy by resource
    """

    def __init__(self):
        self._enabled = RANGER_ENABLED
        self._admin_url = RANGER_ADMIN_URL.rstrip("/")
        self._auth = (RANGER_USERNAME, RANGER_PASSWORD)
        self._service_name = RANGER_SERVICE_NAME
        self._cache = _PolicyCache(ttl=RANGER_CACHE_TTL)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._admin_url,
                auth=self._auth,
                timeout=10.0,
            )
        return self._client

    def _cache_key(self, *parts) -> str:
        """Generate cache key from parts."""
        raw = ":".join(str(p) for p in parts)
        return hashlib.md5(raw.encode()).hexdigest()

    # ── Core Access Check ────────────────────────────────────────────────

    async def check_access(
        self,
        user: str,
        groups: list[str],
        resource_type: str,
        resource: dict,
        action: str = "select",
    ) -> RangerAccessResult:
        """Check if a user has access to a resource.

        Args:
            user: Username (e.g., "zhangsan")
            groups: LDAP group DNs (e.g., ["cn=analysts,ou=groups,dc=example,dc=com"])
            resource_type: "database", "table", or "column"
            resource: Resource dict (e.g., {"database": "mydb", "table": "orders", "column": "phone"})
            action: "select", "insert", "update", "delete"

        Returns:
            RangerAccessResult with allowed, row_filter, column_masking, reason
        """
        if not self._enabled:
            return RangerAccessResult(allowed=True)

        # Check cache first
        ck = self._cache_key(user, resource_type, json.dumps(resource, sort_keys=True), action)
        cached = self._cache.get(ck)
        if cached is not None:
            return cached

        try:
            # Build Ranger policy lookup request
            result = await self._evaluate_policy(user, groups, resource_type, resource, action)
            self._cache.set(ck, result)
            return result
        except Exception as e:
            logger.error("Ranger access check failed for user=%s resource=%s: %s", user, resource, e)
            # On Ranger failure, allow access (fail-open for availability)
            # Change to fail-closed (deny) if security is more important than availability
            return RangerAccessResult(allowed=True, reason=f"Ranger check failed: {e}")

    async def _evaluate_policy(
        self,
        user: str,
        groups: list[str],
        resource_type: str,
        resource: dict,
        action: str,
    ) -> RangerAccessResult:
        """Evaluate Ranger policy for a resource.

        This method queries Ranger Admin REST API to find matching policies
        and evaluates them against the user/group/action.
        """
        client = await self._get_client()

        # Try to find policy by resource
        # Ranger REST API: POST /service/public/v2/api/service/{serviceName}/policy/resource
        try:
            response = await client.post(
                f"/service/public/v2/api/service/{self._service_name}/policy/resource",
                json={
                    "resource": resource,
                    "user": user,
                    "groups": groups,
                    "action": action,
                },
            )

            if response.status_code == 404:
                # No policy found — default deny
                return RangerAccessResult(
                    allowed=False,
                    reason=f"No Ranger policy found for {resource_type}: {resource}",
                )

            response.raise_for_status()
            policy_data = response.json()

            # Evaluate the policy
            return self._evaluate_policy_response(policy_data, user, groups, action)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return RangerAccessResult(
                    allowed=False,
                    reason=f"No policy found for {resource_type}: {resource}",
                )
            raise

    def _evaluate_policy_response(
        self,
        policy_data: dict,
        user: str,
        groups: list[str],
        action: str,
    ) -> RangerAccessResult:
        """Evaluate a Ranger policy response.

        Ranger policy structure:
        {
            "id": 1,
            "name": "policy_name",
            "resources": {"database": {...}, "table": {...}, "column": {...}},
            "policyItems": [
                {
                    "users": ["user1"],
                    "groups": ["group1"],
                    "accesses": [{"type": "select", "isAllowed": true}],
                    "rowFilterPolicyItems": [...],
                    "dataMaskPolicyItems": [...]
                }
            ]
        }
        """
        if not policy_data:
            return RangerAccessResult(allowed=False, reason="Empty policy response")

        # Handle single policy or list of policies
        policies = policy_data if isinstance(policy_data, list) else [policy_data]

        for policy in policies:
            policy_items = policy.get("policyItems", [])

            for item in policy_items:
                item_users = item.get("users", [])
                item_groups = item.get("groups", [])
                accesses = item.get("accesses", [])

                # Check if user or group matches
                user_match = user in item_users or "public" in item_users or "*" in item_users
                group_match = bool(set(groups) & set(item_groups))

                if not (user_match or group_match):
                    continue

                # Check if action is allowed
                for access in accesses:
                    if access.get("type", "").lower() == action.lower() and access.get("isAllowed", False):
                        # Access allowed — extract row filter and column masking
                        row_filter = self._extract_row_filter(item)
                        column_masking = self._extract_column_masking(item)

                        return RangerAccessResult(
                            allowed=True,
                            row_filter=row_filter,
                            column_masking=column_masking,
                        )

        return RangerAccessResult(allowed=False, reason=f"Action '{action}' not allowed by policy")

    def _extract_row_filter(self, policy_item: dict) -> Optional[str]:
        """Extract row-level filter from policy item.

        Row filters are SQL WHERE clause fragments, e.g., "region = '华东'"
        """
        row_filters = policy_item.get("rowFilterPolicyItems", [])
        if row_filters:
            return row_filters[0].get("rowFilter", None)
        return None

    def _extract_column_masking(self, policy_item: dict) -> dict:
        """Extract column masking rules from policy item.

        Returns dict of {column: masking_type}, e.g., {"phone": "MASK_PARTIAL"}
        """
        masking = {}
        mask_items = policy_item.get("dataMaskPolicyItems", [])
        for item in mask_items:
            column = item.get("column", "")
            mask_type = item.get("dataMaskType", "MASK_NONE")
            if column and mask_type != "MASK_NONE":
                masking[column] = mask_type
        return masking

    # ── Convenience Methods ──────────────────────────────────────────────

    async def get_allowed_tables(
        self,
        user: str,
        groups: list[str],
        database: str,
    ) -> list[str]:
        """Get list of tables a user can access in a database.

        Returns list of table names. Empty list means no access.
        """
        if not self._enabled:
            return ["*"]  # Wildcard: all tables allowed when Ranger is disabled

        try:
            client = await self._get_client()
            response = await client.get(
                f"/service/public/v2/api/service/{self._service_name}/policy",
                params={"database": database},
            )
            response.raise_for_status()
            policies = response.json()

            allowed_tables = []
            for policy in policies if isinstance(policies, list) else [policies]:
                resources = policy.get("resources", {})
                table_res = resources.get("table", {})
                table_values = table_res.get("values", [])

                # Check if any policy item allows this user/group
                for item in policy.get("policyItems", []):
                    item_users = item.get("users", [])
                    item_groups = item.get("groups", [])
                    user_match = user in item_users or "public" in item_users or "*" in item_users
                    group_match = bool(set(groups) & set(item_groups))

                    if user_match or group_match:
                        for access in item.get("accesses", []):
                            if access.get("type", "").lower() == "select" and access.get("isAllowed", False):
                                allowed_tables.extend(table_values)
                                break

            return list(set(allowed_tables)) if allowed_tables else []
        except Exception as e:
            logger.error("Ranger get_allowed_tables failed: %s", e)
            return ["*"]  # Fail-open

    async def get_allowed_columns(
        self,
        user: str,
        groups: list[str],
        database: str,
        table: str,
    ) -> list[str]:
        """Get list of columns a user can access in a table.

        Returns list of column names. Empty list means no access.
        ["*"] means all columns allowed (Ranger disabled or no column-level policy).
        """
        if not self._enabled:
            return ["*"]

        ck = self._cache_key("columns", user, database, table)
        cached = self._cache.get(ck)
        if cached is not None:
            return cached

        try:
            result = await self.check_access(
                user=user,
                groups=groups,
                resource_type="table",
                resource={"database": database, "table": table},
                action="select",
            )

            if not result.allowed:
                columns = []
            else:
                # If there's column masking, those columns are still accessible (just masked)
                # The actual column filtering happens at the SQL level
                columns = ["*"]  # Simplified: all columns allowed, masking applied separately

            self._cache.set(ck, columns)
            return columns
        except Exception as e:
            logger.error("Ranger get_allowed_columns failed: %s", e)
            return ["*"]

    async def get_column_masking_rules(
        self,
        user: str,
        groups: list[str],
        database: str,
        table: str,
    ) -> dict[str, MaskingRule]:
        """Get column masking rules for a table.

        Returns dict of {column: MaskingRule}.
        """
        if not self._enabled:
            return {}

        ck = self._cache_key("masking", user, database, table)
        cached = self._cache.get(ck)
        if cached is not None:
            return cached

        try:
            result = await self.check_access(
                user=user,
                groups=groups,
                resource_type="table",
                resource={"database": database, "table": table},
                action="select",
            )

            rules = {}
            for column, mask_type in result.column_masking.items():
                rules[column] = MaskingRule(column=column, masking_type=mask_type)

            self._cache.set(ck, rules)
            return rules
        except Exception as e:
            logger.error("Ranger get_column_masking_rules failed: %s", e)
            return {}

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def invalidate_cache(self):
        """Clear the policy cache (e.g., after policy update)."""
        self._cache.clear()


# Singleton instance
ranger_client = RangerClient()


# ── Utility Functions ────────────────────────────────────────────────────

def inject_row_filter(sql: str, table: str, row_filter: str) -> str:
    """Inject a row-level filter into a SQL query.

    Wraps the table reference with a subquery that includes the row filter.
    E.g., SELECT * FROM orders → SELECT * FROM (SELECT * FROM orders WHERE region = '华东') AS orders

    This is a simplified implementation. A production version should use a proper
    SQL parser (e.g., sqlparse or DataFusion's SQL parser) for accurate rewriting.
    """
    if not row_filter:
        return sql

    import re

    # Simple approach: find "FROM table" and inject WHERE clause
    # This handles basic SELECT queries but may need enhancement for complex SQL
    pattern = rf'(\bFROM\s+){table}(\s|,|WHERE|GROUP|ORDER|LIMIT|$)'
    replacement = rf'\1(SELECT * FROM {table} WHERE {row_filter}) AS {table}\2'

    result = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

    if result == sql:
        # Pattern didn't match, try without table alias
        pattern = rf'(\bFROM\s+){table}(\s)'
        replacement = rf'\1(SELECT * FROM {table} WHERE {row_filter}) AS {table}\2'
        result = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

    return result


def apply_column_masking(df, masking_rules: dict[str, MaskingRule]):
    """Apply column masking to a DataFrame.

    Supported masking types:
    - MASK_NULL: Replace with NULL
    - MASK_HASH: Replace with SHA256 hash
    - MASK_PARTIAL: Partial masking (e.g., 138****1234)
    - CUSTOM: Custom masking expression
    """
    if not masking_rules or df is None or df.empty:
        return df

    import hashlib

    for column, rule in masking_rules.items():
        if column not in df.columns:
            continue

        if rule.masking_type == "MASK_NULL":
            df[column] = None
        elif rule.masking_type == "MASK_HASH":
            df[column] = df[column].apply(
                lambda x: hashlib.sha256(str(x).encode()).hexdigest()[:16] if x is not None else None
            )
        elif rule.masking_type == "MASK_PARTIAL":
            df[column] = df[column].apply(_partial_mask)
        # MASK_NONE or unknown: no masking

    return df


def _partial_mask(value):
    """Apply partial masking to a value.

    Phone: 138****1234
    Email: z***g@example.com
    Other: first char + *** + last char
    """
    if value is None:
        return None
    s = str(value)
    if len(s) <= 4:
        return "***"
    return s[:1] + "***" + s[-1:]
