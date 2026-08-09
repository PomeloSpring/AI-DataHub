"""RLS (Row-Level Security) Service — policy management and enforcement.

Provides CRUD for RLS policies, column policies, user attributes,
and SQL filtering logic for row-level and column-level security.
"""

import logging
import time
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gen_id():
    return int(time.time() * 1000000)


class RLSService:
    """RLS policy management service."""

    # ── Policy CRUD ────────────────────────────────────────────────

    def list_policies(self, workspace_id: int, datasource_id: int = None,
                      table_name: str = None, page: int = 1, size: int = 20) -> dict:
        """List RLS policies with optional filters."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s"]
                params = [workspace_id]
                if datasource_id:
                    where.append("datasource_id = %s")
                    params.append(datasource_id)
                if table_name:
                    where.append("table_name = %s")
                    params.append(table_name)

                where_clause = " AND ".join(where)

                cur.execute(f"SELECT COUNT(*) as total FROM adh_rls_policies WHERE {where_clause}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_rls_policies WHERE {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset]
                )
                items = cur.fetchall()
                return {"total": total, "items": items}
        finally:
            conn.close()

    def get_policy(self, policy_id: int) -> Optional[dict]:
        """Get a single policy by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_rls_policies WHERE id = %s", (policy_id,))
                return cur.fetchone()
        finally:
            conn.close()

    def create_policy(self, data: dict) -> int:
        """Create a new RLS policy."""
        policy_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_rls_policies
                       (id, name, description, workspace_id, datasource_id, table_name,
                        policy_type, filter_type, filter_expr, user_attribute, is_active, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (policy_id, data["name"], data.get("description", ""),
                     data["workspace_id"], data["datasource_id"], data["table_name"],
                     data.get("policy_type", "both"),
                     data.get("filter_type", "condition"),
                     data.get("filter_expr", ""),
                     data.get("user_attribute", ""),
                     data.get("is_active", 1),
                     data.get("created_by"))
                )
                conn.commit()
                return policy_id
        finally:
            conn.close()

    def update_policy(self, policy_id: int, data: dict) -> bool:
        """Update an existing RLS policy."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["name", "description", "policy_type", "filter_type",
                            "filter_expr", "user_attribute", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(policy_id)
                cur.execute(
                    f"UPDATE adh_rls_policies SET {', '.join(fields)} WHERE id = %s",
                    params
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_policy(self, policy_id: int) -> bool:
        """Delete a RLS policy and its column policies."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_rls_column_policies WHERE policy_id = %s", (policy_id,))
                cur.execute("DELETE FROM adh_rls_policies WHERE id = %s", (policy_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Column Policy CRUD ─────────────────────────────────────────

    def get_column_policies(self, policy_id: int) -> list:
        """Get all column policies for a given RLS policy."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_rls_column_policies WHERE policy_id = %s ORDER BY column_name",
                    (policy_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_column_policies(self, policy_id: int, columns: list) -> bool:
        """Replace all column policies for a policy.

        Each column dict should have: column_name, access_type, mask_pattern (optional), description (optional).
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Delete existing
                cur.execute("DELETE FROM adh_rls_column_policies WHERE policy_id = %s", (policy_id,))
                # Insert new
                for col in columns:
                    cur.execute(
                        """INSERT INTO adh_rls_column_policies
                           (id, policy_id, column_name, access_type, mask_pattern, description)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (_gen_id(), policy_id, col["column_name"],
                         col.get("access_type", "visible"),
                         col.get("mask_pattern", ""),
                         col.get("description", ""))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    # ── User Attributes ────────────────────────────────────────────

    def get_user_attributes(self, user_id: int, workspace_id: int) -> dict:
        """Get all RLS attributes for a user in a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attr_key, attr_value FROM adh_rls_user_attributes WHERE user_id = %s AND workspace_id = %s",
                    (user_id, workspace_id)
                )
                rows = cur.fetchall()
                return {r["attr_key"]: r["attr_value"] for r in rows}
        finally:
            conn.close()

    def set_user_attributes(self, user_id: int, workspace_id: int, attrs: dict) -> bool:
        """Set RLS attributes for a user in a workspace (replace all)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Delete existing
                cur.execute(
                    "DELETE FROM adh_rls_user_attributes WHERE user_id = %s AND workspace_id = %s",
                    (user_id, workspace_id)
                )
                # Insert new
                for key, value in attrs.items():
                    cur.execute(
                        """INSERT INTO adh_rls_user_attributes
                           (id, user_id, workspace_id, attr_key, attr_value)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (_gen_id(), user_id, workspace_id, key, str(value))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Effective Policies ─────────────────────────────────────────

    def get_effective_policies(self, user_id: int, workspace_id: int,
                               datasource_id: int, table_name: str) -> dict:
        """Get the effective RLS policies for a user/table combination.

        Returns:
            {
                "row_filter": "region = 'cn'",           # SQL WHERE fragment or ""
                "hidden_columns": ["salary", "ssn"],     # columns to remove from SELECT
                "masked_columns": {"phone": "partial"},   # columns to mask
                "policies_applied": [policy_id, ...]      # which policies were applied
            }
        """
        result = {
            "row_filter": "",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [],
        }

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Find matching policies
                cur.execute(
                    """SELECT * FROM adh_rls_policies
                       WHERE workspace_id = %s AND datasource_id = %s AND table_name = %s
                         AND is_active = 1""",
                    (workspace_id, datasource_id, table_name)
                )
                policies = cur.fetchall()

                if not policies:
                    return result

                # Get user attributes from roles (role-based, not per-user)
                from services.authservice.services.role_service import role_service
                user_attrs = role_service.get_user_effective_attributes(user_id, workspace_id)

                # Fallback: also check legacy per-user attributes
                legacy_attrs = self.get_user_attributes(user_id, workspace_id)
                # Role attributes take precedence, legacy fills gaps
                for k, v in legacy_attrs.items():
                    if k not in user_attrs:
                        user_attrs[k] = v

                for policy in policies:
                    pid = policy["id"]
                    result["policies_applied"].append(pid)

                    # Row-level filter
                    if policy["policy_type"] in ("row", "both") and policy.get("filter_expr"):
                        filter_expr = policy["filter_expr"]
                        if policy["filter_type"] == "user_attribute" and policy.get("user_attribute"):
                            attr_key = policy["user_attribute"]
                            attr_val = user_attrs.get(attr_key, "")
                            # Replace :user_xxx with actual value
                            filter_expr = filter_expr.replace(f":user_{attr_key}", f"'{attr_val}'")
                        if result["row_filter"]:
                            result["row_filter"] += " AND " + filter_expr
                        else:
                            result["row_filter"] = filter_expr

                    # Column-level policies
                    if policy["policy_type"] in ("column", "both"):
                        cur.execute(
                            "SELECT column_name, access_type, mask_pattern FROM adh_rls_column_policies WHERE policy_id = %s",
                            (pid,)
                        )
                        col_policies = cur.fetchall()
                        for cp in col_policies:
                            col = cp["column_name"]
                            if cp["access_type"] == "hidden":
                                if col not in result["hidden_columns"]:
                                    result["hidden_columns"].append(col)
                            elif cp["access_type"] == "masked":
                                result["masked_columns"][col] = cp.get("mask_pattern") or "partial"

                return result
        finally:
            conn.close()

    # ── Audit Logging ──────────────────────────────────────────────

    def log_audit(self, user_id: int, workspace_id: int, policy_id: int = None,
                  policy_name: str = "", table_name: str = "", action: str = "",
                  original_sql: str = "", filtered_sql: str = "") -> None:
        """Log an RLS enforcement event."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_rls_audit_logs
                       (id, user_id, workspace_id, policy_id, policy_name, table_name,
                        action, original_sql, filtered_sql)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (_gen_id(), user_id, workspace_id, policy_id, policy_name,
                     table_name, action, original_sql, filtered_sql)
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to log RLS audit: %s", e)
        finally:
            conn.close()

    def list_audit_logs(self, workspace_id: int, user_id: int = None,
                        page: int = 1, size: int = 20) -> dict:
        """List RLS audit logs."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s"]
                params = [workspace_id]
                if user_id:
                    where.append("user_id = %s")
                    params.append(user_id)

                where_clause = " AND ".join(where)
                cur.execute(f"SELECT COUNT(*) as total FROM adh_rls_audit_logs WHERE {where_clause}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_rls_audit_logs WHERE {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset]
                )
                return {"total": total, "items": cur.fetchall()}
        finally:
            conn.close()


# Singleton instance
rls_service = RLSService()
