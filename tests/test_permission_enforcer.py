"""Unit tests for PermissionEnforcer — lightweight permission enforcement layer."""

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.permission.enforcer import PermissionEnforcer, PermissionResult


@pytest.fixture
def enforcer():
    return PermissionEnforcer()


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "name": ["Alice", "Bob", "Charlie"],
        "salary": [10000, 20000, 30000],
        "phone": ["13800001111", "13900002222", "13700003333"],
        "region": ["华东", "华南", "华北"],
    })


# ── Table extraction ────────────────────────────────────────────────

class TestExtractTables:
    def test_simple_from(self, enforcer):
        sql = "SELECT * FROM orders WHERE id = 1"
        assert enforcer._extract_tables(sql) == ["orders"]

    def test_from_join(self, enforcer):
        sql = "SELECT * FROM orders JOIN users ON orders.user_id = users.id"
        tables = enforcer._extract_tables(sql)
        assert "orders" in tables
        assert "users" in tables

    def test_subquery(self, enforcer):
        sql = "SELECT * FROM (SELECT * FROM orders) t1 JOIN users ON t1.uid = users.id"
        tables = enforcer._extract_tables(sql)
        assert "orders" in tables
        assert "users" in tables

    def test_no_tables(self, enforcer):
        sql = "SELECT 1 + 1"
        assert enforcer._extract_tables(sql) == []

    def test_case_insensitive(self, enforcer):
        sql = "select * FROM Orders join Users on 1=1"
        tables = enforcer._extract_tables(sql)
        assert "Orders" in tables
        assert "Users" in tables


# ── Row filter injection ────────────────────────────────────────────

class TestInjectRowFilter:
    def test_simple_injection(self, enforcer):
        sql = "SELECT * FROM orders WHERE amount > 100"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        assert "WHERE region = '华东'" in result
        assert "SELECT * FROM orders" in result

    def test_no_filter(self, enforcer):
        sql = "SELECT * FROM orders"
        result = enforcer._inject_row_filter(sql, "orders", "")
        assert result == sql

    def test_table_not_found(self, enforcer):
        sql = "SELECT * FROM users"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        assert result == sql  # Unchanged


# ── Value masking ───────────────────────────────────────────────────

class TestMaskValue:
    def test_null_masking(self, enforcer):
        assert enforcer._mask_value("test", "null") is None
        assert enforcer._mask_value(None, "null") is None

    def test_hash_masking(self, enforcer):
        result = enforcer._mask_value("sensitive", "hash")
        assert result is not None
        assert len(result) == 16
        assert result != "sensitive"

    def test_partial_masking(self, enforcer):
        result = enforcer._mask_value("13800001111", "partial")
        assert result.startswith("13")
        assert result.endswith("11")
        assert "*" in result

    def test_partial_short_value(self, enforcer):
        result = enforcer._mask_value("abc", "partial")
        assert result == "***"

    def test_partial_4char_value(self, enforcer):
        result = enforcer._mask_value("abcd", "partial")
        assert result == "****"


# ── Post processing ─────────────────────────────────────────────────

class TestPostProcessing:
    def test_hide_columns(self, enforcer, sample_df):
        result = PermissionResult(hidden_columns=["salary", "phone"])
        df = enforcer.apply_post_processing(sample_df.copy(), result)
        assert "salary" not in df.columns
        assert "phone" not in df.columns
        assert "name" in df.columns

    def test_mask_columns(self, enforcer, sample_df):
        result = PermissionResult(masked_columns={"phone": "partial"})
        df = enforcer.apply_post_processing(sample_df.copy(), result)
        assert "phone" in df.columns
        # Check that values are masked
        for val in df["phone"]:
            assert "*" in str(val)

    def test_hide_and_mask(self, enforcer, sample_df):
        result = PermissionResult(
            hidden_columns=["salary"],
            masked_columns={"phone": "partial"},
        )
        df = enforcer.apply_post_processing(sample_df.copy(), result)
        assert "salary" not in df.columns
        assert "phone" in df.columns

    def test_empty_df(self, enforcer):
        df = pd.DataFrame()
        result = PermissionResult(hidden_columns=["col1"])
        result_df = enforcer.apply_post_processing(df, result)
        assert result_df.empty

    def test_none_df(self, enforcer):
        result = PermissionResult(hidden_columns=["col1"])
        result_df = enforcer.apply_post_processing(None, result)
        assert result_df is None


# ── Access check with mocked services ───────────────────────────────

class TestCheckAccess:
    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_admin_bypass(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 1, "name": "admin"}]
        result = enforcer.check_access(user_id=1, workspace_id=0, datasource_id=1)
        assert result.allowed is True

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_datasource_denied(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "viewer"}]
        mock_role.get_user_allowed_datasources.return_value = [1, 2]  # Only DS 1, 2
        result = enforcer.check_access(user_id=1, workspace_id=0, datasource_id=99)
        assert result.allowed is False
        assert "数据源" in result.reason

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_table_denied(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "viewer"}]
        mock_role.get_user_allowed_datasources.return_value = []  # No restriction
        mock_role.get_user_allowed_tables.return_value = ["orders", "products"]  # No users table
        result = enforcer.check_access(
            user_id=1, workspace_id=0, datasource_id=1, table_name="users"
        )
        assert result.allowed is False
        assert "表" in result.reason

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_allowed_with_restrictions(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {
            "hidden_columns": ["salary"],
            "masked_columns": {"phone": "partial"},
        }
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华东'",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [123],
        }
        result = enforcer.check_access(
            user_id=1, workspace_id=0, datasource_id=1, table_name="orders"
        )
        assert result.allowed is True
        assert result.hidden_columns == ["salary"]
        assert result.masked_columns == {"phone": "partial"}
        assert result.row_filter == "region = '华东'"
        assert 123 in result.policies_applied

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_no_restrictions_empty_result(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {
            "hidden_columns": [],
            "masked_columns": {},
        }
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [],
        }
        result = enforcer.check_access(
            user_id=1, workspace_id=0, datasource_id=1, table_name="orders"
        )
        assert result.allowed is True
        assert result.hidden_columns == []
        assert result.masked_columns == {}
        assert result.row_filter == ""


# ── Enforce SQL ─────────────────────────────────────────────────────

class TestEnforceSQL:
    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_sql_rewrite_with_row_filter(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "analyst"}]
        mock_role.get_user_allowed_datasources.return_value = []
        mock_role.get_user_allowed_tables.return_value = []
        mock_role.get_user_column_restrictions.return_value = {
            "hidden_columns": [],
            "masked_columns": {},
        }
        mock_rls.get_effective_policies.return_value = {
            "row_filter": "region = '华东'",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [1],
        }

        sql = "SELECT * FROM orders WHERE amount > 100"
        modified_sql, result = enforcer.enforce_sql(
            sql=sql, user_id=1, workspace_id=0, datasource_id=1
        )
        assert "region = '华东'" in modified_sql
        assert result.allowed is True

    @patch("services.authservice.services.role_service.role_service")
    @patch("services.authservice.services.rls_service.rls_service")
    def test_permission_denied_raises(self, mock_rls, mock_role, enforcer):
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "viewer"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # Only DS 1

        with pytest.raises(PermissionError):
            enforcer.enforce_sql(
                sql="SELECT * FROM orders",
                user_id=1,
                workspace_id=0,
                datasource_id=99,
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
