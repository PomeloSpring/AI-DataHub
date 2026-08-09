"""End-to-end tests for the permission system.

Tests the complete flow:
1. User authentication → JWT token
2. Permission check via enforcer
3. SQL rewriting (row-level filters)
4. Column hiding/masking
5. Workspace isolation

Prerequisites:
- Database running with permission_demo_migration.sql applied
- Backend services running (or mock DB connection)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from services.datamind.permission.enforcer import PermissionEnforcer, PermissionResult


@pytest.fixture
def enforcer():
    return PermissionEnforcer()


# ============================================================================
# Test Scenarios Based on Demo Data
# ============================================================================
#
# User       | Role           | Workspace | Data Permissions
# -----------|----------------|-----------|----------------------------------
# admin      | admin          | all       | Full access, no restrictions
# zhangsan   | region_analyst | 华东(100) | Only 华东 data, phone masked
# zhangsan   | full_analyst   | 全国(300) | All regions, no restrictions
# lisi       | data_viewer    | 华南(200) | Only 华南 orders, salary hidden, phone masked
# wangwu     | full_analyst   | 全国(300) | All regions, no restrictions
# wangwu     | region_analyst | 华东(100) | Only 华东 data, phone masked
# zhaoliu    | data_viewer    | 华东(100) | Only 华东 orders, amount hidden
# ============================================================================


class TestAdminAccess:
    """Admin should have full access everywhere."""

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_admin_bypass_all_checks(self, mock_rls, mock_role, enforcer):
        """Admin role bypasses all permission checks."""
        mock_role.get_user_roles.return_value = [{"id": 1, "name": "admin"}]

        # Admin can access any datasource
        result = enforcer.check_access(user_id=1, workspace_id=0, datasource_id=999)
        assert result.allowed is True
        assert result.hidden_columns == []
        assert result.masked_columns == {}

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_admin_sees_all_columns(self, mock_rls, mock_role, enforcer):
        """Admin sees all columns without masking."""
        mock_role.get_user_roles.return_value = [{"id": 1, "name": "admin"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {}}
        mock_rls.get_effective_policies.return_value = {"row_filter": "", "hidden_columns": [], "masked_columns": {}, "policies_applied": []}

        result = enforcer.check_access(
            user_id=1, workspace_id=0, datasource_id=1, table_name="users"
        )
        assert result.allowed is True
        assert "salary" not in result.hidden_columns
        assert "phone" not in result.masked_columns


class TestZhangsanEastChina:
    """zhangsan as region_analyst in 华东 workspace."""

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_zhangsan_can_access_east_datasource(self, mock_rls, mock_role, enforcer):
        """zhangsan can access datasource 1 in 华东 workspace."""
        mock_role.get_user_roles.return_value = [{"id": 100, "name": "region_analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []  # No restriction
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {"phone": "partial"}}
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华东'",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [7001],
        }

        result = enforcer.check_access(
            user_id=10, workspace_id=100, datasource_id=1, table_name="orders"
        )
        assert result.allowed is True
        assert result.row_filter == "region = '华东'"
        assert "phone" in result.masked_columns

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_zhangsan_sql_gets_row_filter(self, mock_rls, mock_role, enforcer):
        """zhangsan's SQL should have row filter injected."""
        mock_role.get_user_roles.return_value = [{"id": 100, "name": "region_analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {}}
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华东'",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [7001],
        }

        sql = "SELECT * FROM orders WHERE amount > 100 LIMIT 100"
        modified_sql, result = enforcer.enforce_sql(
            sql=sql, user_id=10, workspace_id=100, datasource_id=1
        )
        assert "region = '华东'" in modified_sql
        assert modified_sql != sql  # SQL was modified


class TestLisiSouthChina:
    """lisi as data_viewer in 华南 workspace."""

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_lisi_can_only_see_orders(self, mock_rls, mock_role, enforcer):
        """lisi can only access orders table, not users or products."""
        mock_role.get_user_roles.return_value = [{"id": 200, "name": "data_viewer"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = ["orders"]  # Only orders

        # Can access orders
        result = enforcer.check_access(
            user_id=11, workspace_id=200, datasource_id=1, table_name="orders"
        )
        assert result.allowed is True

        # Cannot access users
        mock_role.get_user_allowed_tables.return_value = ["orders"]
        result = enforcer.check_access(
            user_id=11, workspace_id=200, datasource_id=1, table_name="users"
        )
        assert result.allowed is False
        assert "无权访问表" in result.reason

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_lisi_salary_hidden_phone_masked(self, mock_rls, mock_role, enforcer):
        """lisi should have salary hidden and phone masked."""
        mock_role.get_user_roles.return_value = [{"id": 200, "name": "data_viewer"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {
            "hidden_columns": ["amount"],
            "masked_columns": {},
        }
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华南'",
            "hidden_columns": ["salary"],
            "masked_columns": {"phone": "partial", "email": "partial"},
            "policies_applied": [7003, 7004],
        }

        result = enforcer.check_access(
            user_id=11, workspace_id=200, datasource_id=1, table_name="orders"
        )
        assert result.allowed is True
        assert "amount" in result.hidden_columns  # From role column access
        assert "salary" in result.hidden_columns  # From RLS column policy
        assert result.masked_columns.get("phone") == "partial"
        assert result.masked_columns.get("email") == "partial"

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_lisi_post_processing(self, mock_rls, mock_role, enforcer):
        """lisi's query results should have hidden columns removed and masked values."""
        df = pd.DataFrame({
            "id": [1, 2],
            "amount": [100, 200],
            "salary": [10000, 20000],
            "phone": ["13800001111", "13900002222"],
            "region": ["华南", "华南"],
        })

        result = PermissionResult(
            hidden_columns=["amount", "salary"],
            masked_columns={"phone": "partial"},
        )

        processed = enforcer.apply_post_processing(df.copy(), result)

        # Hidden columns removed
        assert "amount" not in processed.columns
        assert "salary" not in processed.columns

        # Phone masked
        assert "phone" in processed.columns
        for val in processed["phone"]:
            assert "*" in str(val)

        # Other columns unchanged
        assert "id" in processed.columns
        assert "region" in processed.columns


class TestWorkspaceIsolation:
    """Test that workspaces properly isolate data."""

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_east_workspace_filters_to_east(self, mock_rls, mock_role, enforcer):
        """华东 workspace should filter to 华东 data only."""
        mock_role.get_user_roles.return_value = [{"id": 100, "name": "region_analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {}}
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华东'",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [7001],
        }

        sql = "SELECT * FROM orders LIMIT 100"
        modified_sql, _ = enforcer.enforce_sql(
            sql=sql, user_id=10, workspace_id=100, datasource_id=1
        )
        assert "region = '华东'" in modified_sql

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_south_workspace_filters_to_south(self, mock_rls, mock_role, enforcer):
        """华南 workspace should filter to 华南 data only."""
        mock_role.get_user_roles.return_value = [{"id": 200, "name": "data_viewer"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {}}
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华南'",
            "hidden_columns": ["salary"],
            "masked_columns": {"phone": "partial"},
            "policies_applied": [7003],
        }

        sql = "SELECT * FROM orders LIMIT 100"
        modified_sql, result = enforcer.enforce_sql(
            sql=sql, user_id=11, workspace_id=200, datasource_id=1
        )
        assert "region = '华南'" in modified_sql
        assert "salary" in result.hidden_columns

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_national_workspace_no_filter(self, mock_rls, mock_role, enforcer):
        """全国 workspace should have no row filter."""
        mock_role.get_user_roles.return_value = [{"id": 300, "name": "full_analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {}}
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [7005],
        }

        sql = "SELECT * FROM orders LIMIT 100"
        modified_sql, result = enforcer.enforce_sql(
            sql=sql, user_id=10, workspace_id=300, datasource_id=1
        )
        # No row filter should be injected
        assert modified_sql == sql
        assert result.hidden_columns == []


class TestColumnMasking:
    """Test column masking behavior."""

    def test_partial_masking_preserves_structure(self, enforcer):
        """Partial masking should keep first 2 and last 2 chars."""
        assert enforcer._mask_value("13800001111", "partial") == "13*******11"
        assert enforcer._mask_value("test@email.com", "partial") == "te**********om"

    def test_hash_masking_is_deterministic(self, enforcer):
        """Same input should produce same hash."""
        hash1 = enforcer._mask_value("sensitive", "hash")
        hash2 = enforcer._mask_value("sensitive", "hash")
        assert hash1 == hash2

    def test_null_masking_returns_none(self, enforcer):
        """Null masking should return None."""
        assert enforcer._mask_value("anything", "null") is None
        assert enforcer._mask_value(12345, "null") is None

    def test_none_value_stays_none(self, enforcer):
        """None values should stay None regardless of mask type."""
        assert enforcer._mask_value(None, "partial") is None
        assert enforcer._mask_value(None, "hash") is None
        assert enforcer._mask_value(None, "null") is None


class TestSQLRewrite:
    """Test SQL rewriting for row-level security."""

    def test_simple_where_injection(self, enforcer):
        """Should inject WHERE clause into FROM clause."""
        sql = "SELECT * FROM orders WHERE amount > 100 LIMIT 100"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        assert "region = '华东'" in result
        assert "SELECT * FROM" in result

    def test_join_with_filter(self, enforcer):
        """Should handle JOIN queries."""
        sql = "SELECT * FROM orders JOIN users ON orders.uid = users.id LIMIT 100"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        # Should filter orders table
        assert "region = '华东'" in result

    def test_no_filter_returns_original(self, enforcer):
        """Empty filter should return original SQL."""
        sql = "SELECT * FROM orders LIMIT 100"
        result = enforcer._inject_row_filter(sql, "orders", "")
        assert result == sql

    def test_table_not_in_sql(self, enforcer):
        """If table not in SQL, return original."""
        sql = "SELECT * FROM users LIMIT 100"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        assert result == sql


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
