"""Unit tests for PermissionEnforcer — lightweight permission enforcement layer."""

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.core.enforcer import PermissionEnforcer, PermissionResult


@pytest.fixture
def enforcer():
    from backend.core.enforcer import invalidate_access_cache
    invalidate_access_cache()  # 清空 TTL 缓存, 避免跨测试泄漏
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

    def test_qualified_db_table_join(self, enforcer):
        """限定名 db.table 提取保留限定段，JOIN 两侧都在（护栏 §10）。"""
        sql = "SELECT * FROM shop.orders o JOIN shop.users u ON o.uid = u.id"
        tables = set(enforcer._extract_tables(sql))
        assert tables == {"shop.orders", "shop.users"}

    def test_qualified_three_segment(self, enforcer):
        """三段 catalog.db.table 同样保留全限定名，不得丢段。"""
        sql = "SELECT * FROM src1.shop.orders"
        assert enforcer._extract_tables(sql) == ["src1.shop.orders"]

    def test_cte_name_not_extracted(self, enforcer):
        """CTE 名不是物理表（Scope 解析，不按名字全局排除/误报）。"""
        sql = ("WITH recent AS (SELECT * FROM orders) "
               "SELECT * FROM recent JOIN users ON recent.uid = users.id")
        tables = enforcer._extract_tables(sql)
        assert "recent" not in tables
        assert "orders" in tables and "users" in tables


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

    def test_alias_preserved_no_double_alias(self, enforcer):
        """别名保留，不产生非法双重别名（护栏 §4）。"""
        from backend.semantics.sql_guard import parse_query
        sql = "SELECT o.id FROM orders o JOIN users u ON o.uid = u.id"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        assert "AS o" in result                # 原别名保留
        assert "AS orders o" not in result     # 无非法双重别名
        parse_query(result, "mysql")           # 可再解析 = 语法合法

    def test_join_side_table_injected(self, enforcer):
        """JOIN 侧表同样被包子查询，杜绝关联查询旁路（护栏 §4）。"""
        sql = "SELECT * FROM orders o JOIN users u ON o.uid = u.id"
        result = enforcer._inject_row_filter(sql, "users", "dept = 'a'")
        assert result != sql
        assert "dept = 'a'" in result
        assert "AS u" in result

    def test_both_sides_injected(self):
        """两张表都有行策略时 FROM 与 JOIN 两侧同时注入。"""
        from backend.semantics.sql_guard import inject_filters, parse_query
        sql = "SELECT * FROM orders o JOIN users u ON o.uid = u.id"
        out, applied = inject_filters(
            sql, {"orders": "region = '华东'", "users": "dept = 1"})
        assert set(applied) == {"orders", "users"}
        # 外层 1 + 两表内层各 1 = 3 处 SELECT，说明 FROM 与 JOIN 两侧都包子查询
        assert out.count("SELECT * FROM") == 3
        parse_query(out, "mysql")

    def test_same_table_two_refs_both_injected(self):
        """同一张表 FROM 与 JOIN 各出现一次时两处都要注入（历史旁路形态）。"""
        from backend.semantics.sql_guard import inject_filters
        sql = "SELECT * FROM orders a JOIN orders b ON a.id = b.parent_id"
        out, applied = inject_filters(sql, {"orders": "region = '华东'"})
        assert applied == ["orders"]
        assert out.count("region = '华东'") == 2   # 两处引用都注入

    def test_reserved_word_after_table_not_alias(self, enforcer):
        """FROM 后跟保留字（ORDER）不得误当别名吞掉排序（边界覆盖）。"""
        sql = "SELECT * FROM orders ORDER BY id"
        result = enforcer._inject_row_filter(sql, "orders", "region = '华东'")
        assert "AS orders" in result    # 无别名时回退表名作别名
        assert "ORDER BY id" in result  # ORDER 未被当别名，排序保留

    def test_filter_with_subquery_rejected(self, enforcer):
        """行策略含子查询必须拒收，不得注入（防止行策略扩权）。"""
        with pytest.raises(PermissionError):
            enforcer._inject_row_filter(
                "SELECT * FROM orders", "orders",
                "region IN (SELECT region FROM allowed_regions)")

    def test_qualified_table_injection(self, enforcer):
        """限定名引用按全限定键注入，不丢限定段。"""
        sql = "SELECT * FROM shop.orders"
        result = enforcer._inject_row_filter(sql, "shop.orders", "region = '华东'")
        assert result != sql
        assert "region = '华东'" in result


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
    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_admin_follows_role_permissions(self, mock_rls, mock_role, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        """Admin role follows role-based permission checks (no automatic bypass)."""
        mock_role.get_user_roles.return_value = [{"id": 1, "name": "admin"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # 显式授权 DS1（fail-closed：空=拒绝，不再是 No restriction）
        mock_role.get_user_allowed_tables.return_value = []  # No restriction
        mock_role.get_user_column_restrictions.return_value = {"hidden_columns": [], "masked_columns": {}}
        mock_rls.get_effective_policies.return_value = {"row_filter": "", "hidden_columns": [], "masked_columns": {}, "policies_applied": []}
        result = enforcer.check_access(user_id=1, workspace_id=0, datasource_id=1)
        assert result.allowed is True

    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_datasource_denied(self, mock_rls, mock_role, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "viewer"}]
        mock_role.get_user_allowed_datasources.return_value = [1, 2]  # Only DS 1, 2
        result = enforcer.check_access(user_id=1, workspace_id=0, datasource_id=99)
        assert result.allowed is False
        assert "数据源" in result.reason

    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_table_denied(self, mock_rls, mock_role, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "viewer"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # 显式授权 DS1（fail-closed：空=拒绝，不再是 No restriction）
        mock_role.get_user_allowed_tables.return_value = ["orders", "products"]  # No users table
        result = enforcer.check_access(
            user_id=1, workspace_id=0, datasource_id=1, table_name="users"
        )
        assert result.allowed is False
        assert "表" in result.reason

    @patch.object(PermissionEnforcer, "_get_sensitive_policies", return_value=({}, []))
    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_allowed_with_restrictions(self, mock_rls, mock_role, mock_sens, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "analyst"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # fail-closed：空=拒绝，须显式授权
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

    @patch.object(PermissionEnforcer, "_get_sensitive_policies", return_value=({}, []))
    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_no_restrictions_empty_result(self, mock_rls, mock_role, mock_sens, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "analyst"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # fail-closed：空=拒绝，须显式授权
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
    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_sql_rewrite_with_row_filter(self, mock_rls, mock_role, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "analyst"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # fail-closed：空=拒绝，须显式授权
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

    @patch("backend.core.role_service.role_service")
    @patch("backend.core.rls_service.rls_service")
    def test_permission_denied_raises(self, mock_rls, mock_role, enforcer):
        mock_rls.get_role_table_whitelist.return_value = None  # 未绑表策略 → 表级不限制
        mock_role.get_user_roles.return_value = [{"id": 2, "name": "viewer"}]
        mock_role.get_user_allowed_datasources.return_value = [1]  # Only DS 1

        with pytest.raises(PermissionError):
            enforcer.enforce_sql(
                sql="SELECT * FROM orders",
                user_id=1,
                workspace_id=0,
                datasource_id=99,
            )


class TestProtectProjection:
    """_protect_projection 对“位置重命名”的保守拒绝不得误拦表生成函数（LATERAL VIEW / UNNEST）
    的输出列命名；同时不得弱化对 hidden 列的引用拦截。"""

    def _res(self, **kw):
        return PermissionResult(**kw)

    def test_positional_rename_still_rejected(self, enforcer):
        sql = "SELECT a, b FROM (SELECT phone, salary FROM t_emp) AS derived(a, b)"
        res = self._res(masked_columns={"phone": "partial"})
        with pytest.raises(PermissionError, match="位置重命名"):
            enforcer._protect_projection(sql, res)

    def test_lateral_view_explode_allowed(self, enforcer):
        sql = ("SELECT pcid, t.level_index FROM user_funnel "
               "LATERAL VIEW EXPLODE(ARRAY_RANGE(1, level + 1)) t AS level_index")
        enforcer._protect_projection(sql, self._res(masked_columns={"phone": "partial"}))

    def test_presto_unnest_column_alias_allowed(self, enforcer):
        # Presto/Trino `CROSS JOIN UNNEST(expr) AS t(c)`：给展开的虚拟列命名，不得当位置重命名拒（SLS 漏斗常见）
        sql = ("SELECT pcid, level, t.level_index FROM user_funnel "
               "CROSS JOIN UNNEST(sequence(1, level)) AS t(level_index)")
        enforcer._protect_projection(sql, self._res(masked_columns={"phone": "partial"}))

    def test_unnest_still_checks_hidden_refs(self, enforcer):
        sql = ("SELECT `usercode`, t.level_index FROM user_funnel "
               "CROSS JOIN UNNEST(sequence(1, level)) AS t(level_index)")
        with pytest.raises(PermissionError, match="禁止访问"):
            enforcer._protect_projection(sql, self._res(hidden_columns=["usercode"]))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
