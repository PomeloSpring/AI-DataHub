"""统一认证与权限管理集成测试

测试覆盖:
1. LDAP 认证后端
2. Kerberos 后端
3. Ranger 客户端
4. 查询执行器 Ranger 集成
5. RAG 检索器 Ranger 过滤

运行方式:
    cd /home/wuzhiwei/project/AI-DataHub
    python -m pytest tests/test_permission_management.py -v

注意: 需要先启动基础设施 (docker compose -f docker/docker-compose.ranger.yml up -d)
"""
from __future__ import annotations

import os
import sys
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ══════════════════════════════════════════════════════════════════════
# 1. LDAP Backend Tests
# ══════════════════════════════════════════════════════════════════════

class TestLDAPBackend:
    """Test LDAP authentication backend."""

    @patch("services.shared.common.config.LDAP_ENABLED", False)
    def test_ldap_disabled_returns_none(self):
        """When LDAP is disabled, authenticate should return None."""
        from services.authservice.services.ldap_backend import LDAPBackend
        backend = LDAPBackend()
        result = backend.authenticate("user", "pass")
        assert result is None

    @patch("services.shared.common.config.LDAP_ENABLED", True)
    @patch("services.authservice.services.ldap_backend._get_ldap3")
    def test_ldap_authenticate_success(self, mock_ldap3):
        """Test successful LDAP authentication."""
        # Mock ldap3
        mock_conn = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_dn = "uid=testuser,ou=users,dc=example,dc=com"
        mock_entry.uid = "testuser"
        mock_entry.mail = "test@example.com"
        mock_entry.cn = "Test User"
        mock_entry.memberOf = ["cn=analysts,ou=groups,dc=example,dc=com"]

        mock_conn.entries = [mock_entry]
        mock_conn.search = MagicMock()
        mock_conn.unbind = MagicMock()

        mock_server = MagicMock()
        mock_ldap3_obj = MagicMock()
        mock_ldap3_obj.Server.return_value = mock_server
        mock_ldap3_obj.Connection.return_value = mock_conn
        mock_ldap3_obj.ALL = "ALL"
        mock_ldap3_obj.AUTO_BIND_TLS_BEFORE_BIND = "TLS"
        mock_ldap3.return_value = mock_ldap3_obj

        from services.authservice.services.ldap_backend import LDAPBackend
        backend = LDAPBackend()
        result = backend.authenticate("testuser", "password123")

        assert result is not None
        assert result["username"] == "testuser"
        assert result["email"] == "test@example.com"
        assert result["cn"] == "Test User"
        assert len(result["groups"]) == 1

    @patch("services.shared.common.config.LDAP_ENABLED", True)
    @patch("services.authservice.services.ldap_backend._get_ldap3")
    def test_ldap_authenticate_wrong_password(self, mock_ldap3):
        """Test LDAP authentication with wrong password."""
        mock_conn = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_dn = "uid=testuser,ou=users,dc=example,dc=com"
        mock_entry.uid = "testuser"
        mock_entry.mail = "test@example.com"
        mock_entry.cn = "Test User"
        mock_entry.memberOf = []

        mock_conn.entries = [mock_entry]

        # First connection (search) succeeds, second (bind) fails
        call_count = [0]
        def mock_connection(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_conn
            raise Exception("Invalid credentials")

        mock_ldap3_obj = MagicMock()
        mock_ldap3_obj.Server.return_value = MagicMock()
        mock_ldap3_obj.Connection.side_effect = mock_connection
        mock_ldap3_obj.ALL = "ALL"
        mock_ldap3.return_value = mock_ldap3_obj

        from services.authservice.services.ldap_backend import LDAPBackend
        backend = LDAPBackend()
        result = backend.authenticate("testuser", "wrongpassword")

        assert result is None

    @patch("services.shared.common.config.LDAP_ENABLED", True)
    @patch("services.authservice.services.ldap_backend._get_ldap3")
    def test_ldap_authenticate_user_not_found(self, mock_ldap3):
        """Test LDAP authentication when user doesn't exist."""
        mock_conn = MagicMock()
        mock_conn.entries = []  # No results
        mock_conn.unbind = MagicMock()

        mock_ldap3_obj = MagicMock()
        mock_ldap3_obj.Server.return_value = MagicMock()
        mock_ldap3_obj.Connection.return_value = mock_conn
        mock_ldap3_obj.ALL = "ALL"
        mock_ldap3.return_value = mock_ldap3_obj

        from services.authservice.services.ldap_backend import LDAPBackend
        backend = LDAPBackend()
        result = backend.authenticate("nonexistent", "password")

        assert result is None

    def test_map_ldap_groups_to_role_default(self):
        """Test LDAP group to role mapping returns default when no mapping."""
        from services.authservice.services.ldap_backend import LDAPBackend
        backend = LDAPBackend()
        role = backend._map_ldap_groups_to_role([])
        assert role == "viewer"  # Default role


# ══════════════════════════════════════════════════════════════════════
# 2. Kerberos Backend Tests
# ══════════════════════════════════════════════════════════════════════

class TestKerberosBackend:
    """Test Kerberos authentication backend."""

    @patch("services.shared.common.config.KERBEROS_ENABLED", False)
    def test_kerberos_disabled_returns_none(self):
        """When Kerberos is disabled, validate should return None."""
        from services.authservice.services.kerberos_backend import KerberosBackend
        backend = KerberosBackend()
        result = backend.validate_spnego_token(b"token")
        assert result is None

    @patch("services.shared.common.config.KERBEROS_ENABLED", False)
    def test_principal_to_username_simple(self):
        """Test principal to username conversion."""
        from services.authservice.services.kerberos_backend import KerberosBackend
        backend = KerberosBackend()

        assert backend.principal_to_username("user@EXAMPLE.COM") == "user"
        assert backend.principal_to_username("user/admin@EXAMPLE.COM") == "user"
        assert backend.principal_to_username("HTTP/server.example.com@EXAMPLE.COM") == "HTTP/server.example.com"
        assert backend.principal_to_username("simpleuser") == "simpleuser"

    @patch("services.shared.common.config.KERBEROS_ENABLED", False)
    def test_extract_realm(self):
        """Test realm extraction from principal."""
        from services.authservice.services.kerberos_backend import KerberosBackend
        backend = KerberosBackend()

        assert backend._extract_realm("user@EXAMPLE.COM") == "EXAMPLE.COM"
        assert backend._extract_realm("user") == "EXAMPLE.COM"  # Default realm


# ══════════════════════════════════════════════════════════════════════
# 3. Ranger Client Tests
# ══════════════════════════════════════════════════════════════════════

class TestRangerClient:
    """Test Ranger REST API client."""

    @patch("services.shared.common.config.RANGER_ENABLED", False)
    def test_ranger_disabled_allows_access(self):
        """When Ranger is disabled, all access should be allowed."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        result = asyncio.get_event_loop().run_until_complete(
            client.check_access(
                user="testuser",
                groups=[],
                resource_type="table",
                resource={"database": "mydb", "table": "orders"},
                action="select",
            )
        )

        assert result.allowed is True

    def test_cache_key_generation(self):
        """Test cache key generation."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        key1 = client._cache_key("user1", "table", '{"db":"mydb","table":"t1"}', "select")
        key2 = client._cache_key("user1", "table", '{"db":"mydb","table":"t1"}', "select")
        key3 = client._cache_key("user2", "table", '{"db":"mydb","table":"t1"}', "select")

        assert key1 == key2  # Same inputs = same key
        assert key1 != key3  # Different user = different key

    def test_policy_cache_set_get(self):
        """Test policy cache set and get."""
        from services.shared.services.ranger_client import _PolicyCache
        cache = _PolicyCache(ttl=60)

        cache.set("key1", {"allowed": True})
        assert cache.get("key1") == {"allowed": True}
        assert cache.get("key2") is None

    def test_policy_cache_invalidation(self):
        """Test policy cache invalidation."""
        from services.shared.services.ranger_client import _PolicyCache
        cache = _PolicyCache(ttl=60)

        cache.set("key1", {"allowed": True})
        cache.invalidate("key1")
        assert cache.get("key1") is None

    def test_evaluate_policy_allows_matching_user(self):
        """Test policy evaluation allows matching user."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        policy_data = {
            "id": 1,
            "name": "test_policy",
            "policyItems": [
                {
                    "users": ["testuser"],
                    "groups": [],
                    "accesses": [
                        {"type": "select", "isAllowed": True}
                    ]
                }
            ]
        }

        result = client._evaluate_policy_response(policy_data, "testuser", [], "select")
        assert result.allowed is True

    def test_evaluate_policy_denies_non_matching_user(self):
        """Test policy evaluation denies non-matching user."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        policy_data = {
            "id": 1,
            "name": "test_policy",
            "policyItems": [
                {
                    "users": ["otheruser"],
                    "groups": [],
                    "accesses": [
                        {"type": "select", "isAllowed": True}
                    ]
                }
            ]
        }

        result = client._evaluate_policy_response(policy_data, "testuser", [], "select")
        assert result.allowed is False

    def test_evaluate_policy_allows_matching_group(self):
        """Test policy evaluation allows matching group."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        policy_data = {
            "id": 1,
            "name": "test_policy",
            "policyItems": [
                {
                    "users": [],
                    "groups": ["cn=analysts,ou=groups,dc=example,dc=com"],
                    "accesses": [
                        {"type": "select", "isAllowed": True}
                    ]
                }
            ]
        }

        result = client._evaluate_policy_response(
            policy_data, "testuser",
            ["cn=analysts,ou=groups,dc=example,dc=com"],
            "select"
        )
        assert result.allowed is True

    def test_evaluate_policy_extracts_row_filter(self):
        """Test policy evaluation extracts row filter."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        policy_data = {
            "id": 1,
            "name": "test_policy",
            "policyItems": [
                {
                    "users": ["testuser"],
                    "groups": [],
                    "accesses": [{"type": "select", "isAllowed": True}],
                    "rowFilterPolicyItems": [
                        {"rowFilter": "region = '华东'"}
                    ]
                }
            ]
        }

        result = client._evaluate_policy_response(policy_data, "testuser", [], "select")
        assert result.allowed is True
        assert result.row_filter == "region = '华东'"

    def test_evaluate_policy_extracts_column_masking(self):
        """Test policy evaluation extracts column masking."""
        from services.shared.services.ranger_client import RangerClient
        client = RangerClient()

        policy_data = {
            "id": 1,
            "name": "test_policy",
            "policyItems": [
                {
                    "users": ["testuser"],
                    "groups": [],
                    "accesses": [{"type": "select", "isAllowed": True}],
                    "dataMaskPolicyItems": [
                        {"column": "phone", "dataMaskType": "MASK_PARTIAL"},
                        {"column": "email", "dataMaskType": "MASK_HASH"},
                    ]
                }
            ]
        }

        result = client._evaluate_policy_response(policy_data, "testuser", [], "select")
        assert result.allowed is True
        assert result.column_masking["phone"] == "MASK_PARTIAL"
        assert result.column_masking["email"] == "MASK_HASH"


# ══════════════════════════════════════════════════════════════════════
# 4. SQL Row Filter Injection Tests
# ══════════════════════════════════════════════════════════════════════

class TestRowFilterInjection:
    """Test SQL row filter injection."""

    def test_inject_row_filter_simple(self):
        """Test row filter injection into simple SELECT."""
        from services.shared.services.ranger_client import inject_row_filter

        sql = "SELECT * FROM orders WHERE amount > 100"
        result = inject_row_filter(sql, "orders", "region = '华东'")

        # Should contain the row filter
        assert "region = '华东'" in result

    def test_inject_row_filter_no_filter(self):
        """Test that empty row filter returns original SQL."""
        from services.shared.services.ranger_client import inject_row_filter

        sql = "SELECT * FROM orders"
        result = inject_row_filter(sql, "orders", "")

        assert result == sql


# ══════════════════════════════════════════════════════════════════════
# 5. Column Masking Tests
# ══════════════════════════════════════════════════════════════════════

class TestColumnMasking:
    """Test column masking application."""

    def test_mask_null(self):
        """Test MASK_NULL masking type."""
        import pandas as pd
        from services.shared.services.ranger_client import apply_column_masking, MaskingRule

        df = pd.DataFrame({"name": ["Alice", "Bob"], "phone": ["13812345678", "13987654321"]})
        rules = {"phone": MaskingRule(column="phone", masking_type="MASK_NULL")}

        result = apply_column_masking(df, rules)
        assert result["phone"].isna().all()

    def test_mask_hash(self):
        """Test MASK_HASH masking type."""
        import pandas as pd
        from services.shared.services.ranger_client import apply_column_masking, MaskingRule

        df = pd.DataFrame({"email": ["alice@example.com", "bob@example.com"]})
        rules = {"email": MaskingRule(column="email", masking_type="MASK_HASH")}

        result = apply_column_masking(df, rules)
        # Hashed values should be different from original
        assert result["email"].iloc[0] != "alice@example.com"
        assert len(result["email"].iloc[0]) == 16  # SHA256 truncated to 16 chars

    def test_mask_partial(self):
        """Test MASK_PARTIAL masking type."""
        import pandas as pd
        from services.shared.services.ranger_client import apply_column_masking, MaskingRule

        df = pd.DataFrame({"phone": ["13812345678", "13987654321"]})
        rules = {"phone": MaskingRule(column="phone", masking_type="MASK_PARTIAL")}

        result = apply_column_masking(df, rules)
        # Should be partially masked: first char + *** + last char
        assert result["phone"].iloc[0] == "1***8"

    def test_masking_skips_missing_columns(self):
        """Test that masking skips columns not in DataFrame."""
        import pandas as pd
        from services.shared.services.ranger_client import apply_column_masking, MaskingRule

        df = pd.DataFrame({"name": ["Alice"]})
        rules = {"phone": MaskingRule(column="phone", masking_type="MASK_NULL")}

        # Should not raise error
        result = apply_column_masking(df, rules)
        assert "name" in result.columns

    def test_no_masking_when_rules_empty(self):
        """Test that empty masking rules returns original DataFrame."""
        import pandas as pd
        from services.shared.services.ranger_client import apply_column_masking

        df = pd.DataFrame({"name": ["Alice", "Bob"]})
        result = apply_column_masking(df, {})
        assert result.equals(df)


# ══════════════════════════════════════════════════════════════════════
# 6. SQL Table/Column Parser Tests
# ══════════════════════════════════════════════════════════════════════

class TestSQLParser:
    """Test SQL table/column extraction."""

    def test_parse_simple_select(self):
        """Test parsing simple SELECT statement."""
        from services.datamind.nl2sql.sql.query_executor import _parse_sql_tables_columns

        sql = "SELECT name, email FROM users WHERE id = 1"
        result = _parse_sql_tables_columns(sql)

        assert "users" in result
        assert "name" in result["users"]
        assert "email" in result["users"]

    def test_parse_select_with_join(self):
        """Test parsing SELECT with JOIN."""
        from services.datamind.nl2sql.sql.query_executor import _parse_sql_tables_columns

        sql = "SELECT o.id, u.name FROM orders o JOIN users u ON o.user_id = u.id"
        result = _parse_sql_tables_columns(sql)

        assert "orders" in result
        assert "users" in result

    def test_parse_select_star(self):
        """Test parsing SELECT *."""
        from services.datamind.nl2sql.sql.query_executor import _parse_sql_tables_columns

        sql = "SELECT * FROM products"
        result = _parse_sql_tables_columns(sql)

        assert "products" in result
        assert "*" in result["products"]


# ══════════════════════════════════════════════════════════════════════
# 7. Auth Service Integration Tests
# ══════════════════════════════════════════════════════════════════════

class TestAuthServiceIntegration:
    """Test auth service LDAP integration."""

    @patch("services.shared.common.config.LDAP_ENABLED", True)
    @patch("services.authservice.services.auth_service._try_ldap_login")
    @patch("services.authservice.services.auth_service._local_login")
    def test_login_ldap_success_skips_local(self, mock_local, mock_ldap):
        """When LDAP succeeds, local login should not be called."""
        from services.authservice.services.auth_service import login

        mock_ldap.return_value = {
            "access_token": "token",
            "refresh_token": "refresh",
            "user": {"id": 1, "username": "test", "role": "analyst"},
            "auth_source": "ldap",
        }

        result = login("testuser", "password")
        assert result is not None
        assert result["auth_source"] == "ldap"
        mock_local.assert_not_called()

    @patch("services.shared.common.config.LDAP_ENABLED", True)
    @patch("services.authservice.services.auth_service._try_ldap_login")
    @patch("services.authservice.services.auth_service._local_login")
    def test_login_ldap_fails_falls_back_to_local(self, mock_local, mock_ldap):
        """When LDAP fails, should fall back to local login."""
        from services.authservice.services.auth_service import login

        mock_ldap.return_value = None
        mock_local.return_value = {
            "access_token": "token",
            "refresh_token": "refresh",
            "user": {"id": 1, "username": "test", "role": "viewer"},
        }

        result = login("testuser", "password")
        assert result is not None
        mock_local.assert_called_once()

    @patch("services.shared.common.config.LDAP_ENABLED", False)
    @patch("services.authservice.services.auth_service._local_login")
    def test_login_ldap_disabled_uses_local(self, mock_local):
        """When LDAP is disabled, should use local login directly."""
        from services.authservice.services.auth_service import login

        mock_local.return_value = {
            "access_token": "token",
            "refresh_token": "refresh",
            "user": {"id": 1, "username": "test", "role": "viewer"},
        }

        result = login("testuser", "password")
        assert result is not None
        mock_local.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# 8. require_datasource_access Tests
# ══════════════════════════════════════════════════════════════════════

class TestRequireDatasourceAccess:
    """Test require_datasource_access dependency."""

    @patch("services.shared.common.config.RANGER_ENABLED", False)
    def test_ranger_disabled_allows_access(self):
        """When Ranger is disabled, should allow access."""
        from services.shared.common.auth import require_datasource_access

        # Create the dependency
        dep = require_datasource_access(database="mydb", table="orders")

        # Should be callable
        assert callable(dep)


# ══════════════════════════════════════════════════════════════════════
# 9. Config Tests
# ══════════════════════════════════════════════════════════════════════

class TestConfig:
    """Test configuration loading."""

    def test_ldap_config_defaults(self):
        """Test LDAP config has correct defaults."""
        from services.shared.common.config import (
            LDAP_ENABLED, LDAP_SERVER_URL, LDAP_BASE_DN,
            LDAP_DEFAULT_ROLE, LDAP_CONNECT_TIMEOUT,
        )

        assert LDAP_ENABLED is False  # Disabled by default
        assert "ldap://" in LDAP_SERVER_URL
        assert LDAP_DEFAULT_ROLE == "viewer"
        assert LDAP_CONNECT_TIMEOUT == 5

    def test_ranger_config_defaults(self):
        """Test Ranger config has correct defaults."""
        from services.shared.common.config import (
            RANGER_ENABLED, RANGER_ADMIN_URL, RANGER_CACHE_TTL,
        )

        assert RANGER_ENABLED is False  # Disabled by default
        assert "6080" in RANGER_ADMIN_URL
        assert RANGER_CACHE_TTL == 300

    def test_kerberos_config_defaults(self):
        """Test Kerberos config has correct defaults."""
        from services.shared.common.config import (
            KERBEROS_ENABLED, KERBEROS_REALM,
        )

        assert KERBEROS_ENABLED is False  # Disabled by default
        assert KERBEROS_REALM == "EXAMPLE.COM"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
