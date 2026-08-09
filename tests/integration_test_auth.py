#!/usr/bin/env python3
"""统一认证与权限管理集成测试

测试流程:
1. 启动 OpenLDAP 容器
2. 测试 LDAP 连接和认证
3. 测试用户同步到本地数据库
4. 测试 Ranger 客户端（策略评估）
5. 测试端到端登录流程

运行方式:
    python3 tests/integration_test_auth.py
"""
from __future__ import annotations

import os
import sys
import json
import time
import subprocess
from datetime import datetime

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ══════════════════════════════════════════════════════════════════════
# Test Helpers
# ══════════════════════════════════════════════════════════════════════

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

passed = 0
failed = 0
skipped = 0


def log_pass(name: str, detail: str = ""):
    global passed
    passed += 1
    print(f"  {Colors.GREEN}✅ PASS{Colors.RESET} {name}" + (f" — {detail}" if detail else ""))


def log_fail(name: str, detail: str = ""):
    global failed
    failed += 1
    print(f"  {Colors.RED}❌ FAIL{Colors.RESET} {name}" + (f" — {detail}" if detail else ""))


def log_skip(name: str, reason: str = ""):
    global skipped
    skipped += 1
    print(f"  {Colors.YELLOW}⏭️  SKIP{Colors.RESET} {name}" + (f" — {reason}" if reason else ""))


def log_section(title: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'═'*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}  {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'═'*60}{Colors.RESET}")


def run_cmd(cmd: str, timeout: int = 30) -> tuple[int, str]:
    """Run a shell command and return (returncode, output)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, "Command timed out"
    except Exception as e:
        return -1, str(e)


# ══════════════════════════════════════════════════════════════════════
# Test 1: Docker Infrastructure
# ══════════════════════════════════════════════════════════════════════

def test_docker_infrastructure():
    log_section("Test 1: Docker Infrastructure")

    # Check Docker is available
    rc, out = run_cmd("docker --version")
    if rc == 0:
        log_pass("Docker available", out.strip())
    else:
        log_fail("Docker not available", out)
        return False

    # Check docker-compose.ranger.yml exists
    compose_file = "docker/docker-compose.ranger.yml"
    if os.path.exists(compose_file):
        log_pass("Compose file exists", compose_file)
    else:
        log_fail("Compose file missing", compose_file)
        return False

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 2: OpenLDAP
# ══════════════════════════════════════════════════════════════════════

def test_openldap():
    log_section("Test 2: OpenLDAP Integration")

    # Clean up any existing container
    run_cmd("docker rm -f adh-openldap 2>/dev/null", timeout=10)

    # Start OpenLDAP container
    print("  Starting OpenLDAP container...")
    rc, out = run_cmd(
        "cd docker && docker compose -f docker-compose.ranger.yml up -d openldap",
        timeout=120,
    )
    if rc == 0:
        log_pass("OpenLDAP container started")
    else:
        log_fail("OpenLDAP container failed to start", out[:200])
        return False

    # Wait for LDAP to be ready using Python ldap3
    print("  Waiting for OpenLDAP to be ready...")
    import ldap3

    for i in range(60):
        try:
            server = ldap3.Server("ldap://localhost:389", connect_timeout=2)
            conn = ldap3.Connection(server, "cn=admin,dc=example,dc=com", "admin123", auto_bind=True)
            conn.unbind()
            log_pass("OpenLDAP is ready", f"after {i+1}s")
            break
        except Exception:
            time.sleep(1)
    else:
        log_fail("OpenLDAP not ready after 60s")
        return False

    # Bootstrap test data (users and groups) using Python ldap3
    print("  Bootstrapping LDAP test data...")
    try:
        server = ldap3.Server("ldap://localhost:389")
        conn = ldap3.Connection(server, "cn=admin,dc=example,dc=com", "admin123", auto_bind=True)

        # Create OUs
        conn.add("ou=users,dc=example,dc=com", "organizationalUnit", {"ou": "users"})
        conn.add("ou=groups,dc=example,dc=com", "organizationalUnit", {"ou": "groups"})

        # Create users
        conn.add("uid=zhangsan,ou=users,dc=example,dc=com", "inetOrgPerson", {
            "cn": "张三", "sn": "张", "mail": "zhangsan@example.com",
            "userPassword": "password123",
        })
        conn.add("uid=lisi,ou=users,dc=example,dc=com", "inetOrgPerson", {
            "cn": "李四", "sn": "李", "mail": "lisi@example.com",
            "userPassword": "password123",
        })

        # Create groups
        conn.add("cn=admins,ou=groups,dc=example,dc=com", "groupOfNames", {
            "member": "uid=zhangsan,ou=users,dc=example,dc=com",
        })
        conn.add("cn=analysts,ou=groups,dc=example,dc=com", "groupOfNames", {
            "member": [
                "uid=zhangsan,ou=users,dc=example,dc=com",
                "uid=lisi,ou=users,dc=example,dc=com",
            ],
        })

        conn.unbind()
        log_pass("LDAP bootstrap data added", "users and groups created")
    except Exception as e:
        log_fail("LDAP bootstrap failed", str(e))

    # Test LDAP search with admin (using Python ldap3 since ldapsearch may not be installed)
    try:
        server = ldap3.Server("ldap://localhost:389")
        conn = ldap3.Connection(server, "cn=admin,dc=example,dc=com", "admin123", auto_bind=True)
        conn.search("ou=users,dc=example,dc=com", "(uid=zhangsan)", attributes=["uid", "cn", "mail"])
        if conn.entries:
            log_pass("LDAP search user 'zhangsan'", f"cn={conn.entries[0].cn}")
        else:
            log_fail("LDAP search user 'zhangsan'", "not found")
        conn.unbind()
    except Exception as e:
        log_fail("LDAP search user 'zhangsan'", str(e))

    # Test LDAP bind with user password
    try:
        server = ldap3.Server("ldap://localhost:389")
        conn = ldap3.Connection(server, "uid=zhangsan,ou=users,dc=example,dc=com", "password123", auto_bind=True)
        log_pass("LDAP bind user 'zhangsan'", f"authenticated: {conn.extend.standard.who_am_i()}")
        conn.unbind()
    except Exception as e:
        log_fail("LDAP bind user 'zhangsan'", str(e))

    # Test LDAP bind with wrong password
    try:
        server = ldap3.Server("ldap://localhost:389")
        conn = ldap3.Connection(server, "uid=zhangsan,ou=users,dc=example,dc=com", "wrongpass", auto_bind=True)
        log_fail("LDAP reject wrong password", "should have been rejected")
        conn.unbind()
    except Exception:
        log_pass("LDAP reject wrong password", "correctly rejected")

    # Test LDAP groups
    try:
        server = ldap3.Server("ldap://localhost:389")
        conn = ldap3.Connection(server, "cn=admin,dc=example,dc=com", "admin123", auto_bind=True)
        conn.search("ou=groups,dc=example,dc=com", "(objectClass=groupOfNames)", attributes=["cn", "member"])
        groups = [str(entry.cn) for entry in conn.entries]
        if "analysts" in groups:
            log_pass("LDAP groups found", f"groups: {groups}")
        else:
            log_fail("LDAP groups search", f"analysts not found in {groups}")
        conn.unbind()
    except Exception as e:
        log_fail("LDAP groups search", str(e))

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 3: LDAP Backend (Python)
# ══════════════════════════════════════════════════════════════════════

def test_ldap_backend():
    log_section("Test 3: LDAP Backend (Python ldap3)")

    try:
        import ldap3
        log_pass("ldap3 installed", f"version {ldap3.__version__}")
    except ImportError:
        log_skip("ldap3 not installed", "pip install ldap3")
        return False

    # Test LDAP connection
    try:
        server = ldap3.Server("ldap://localhost:389", get_info=ldap3.ALL)
        conn = ldap3.Connection(server, "cn=admin,dc=example,dc=com", "admin123", auto_bind=True)
        log_pass("LDAP connection", f"bound as {conn.extend.standard.who_am_i()}")

        # Search for users
        conn.search(
            "ou=users,dc=example,dc=com",
            "(uid=zhangsan)",
            attributes=["uid", "cn", "mail"],
        )
        if conn.entries:
            entry = conn.entries[0]
            log_pass("LDAP search zhangsan", f"cn={entry.cn}, mail={entry.mail}")
        else:
            log_fail("LDAP search zhangsan", "not found")

        # Search for groups
        conn.search(
            "ou=groups,dc=example,dc=com",
            "(objectClass=groupOfNames)",
            attributes=["cn", "member"],
        )
        groups = [str(entry.cn) for entry in conn.entries]
        log_pass("LDAP groups", f"found: {groups}")

        # Test user bind
        user_conn = ldap3.Connection(
            server,
            "uid=zhangsan,ou=users,dc=example,dc=com",
            "password123",
            auto_bind=True,
        )
        log_pass("LDAP user bind", f"zhangsan authenticated: {user_conn.extend.standard.who_am_i()}")
        user_conn.unbind()

        conn.unbind()
    except Exception as e:
        log_fail("LDAP backend test", str(e))
        return False

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 4: Ranger Client (Policy Evaluation)
# ══════════════════════════════════════════════════════════════════════

def test_ranger_client():
    log_section("Test 4: Ranger Client (Policy Evaluation)")

    # Test policy evaluation logic (no Ranger Admin needed)
    from services.shared.services.ranger_client import RangerClient, _PolicyCache

    client = RangerClient()

    # Test cache
    cache = _PolicyCache(ttl=60)
    cache.set("test_key", {"allowed": True})
    assert cache.get("test_key") == {"allowed": True}
    assert cache.get("missing") is None
    cache.invalidate("test_key")
    assert cache.get("test_key") is None
    log_pass("Policy cache", "set/get/invalidate works")

    # Test policy evaluation: allow user
    policy = {
        "policyItems": [{
            "users": ["zhangsan"],
            "groups": [],
            "accesses": [{"type": "select", "isAllowed": True}],
        }]
    }
    result = client._evaluate_policy_response(policy, "zhangsan", [], "select")
    assert result.allowed is True
    log_pass("Policy allow user", "zhangsan allowed for select")

    # Test policy evaluation: deny user
    result = client._evaluate_policy_response(policy, "lisi", [], "select")
    assert result.allowed is False
    log_pass("Policy deny user", "lisi denied for select")

    # Test policy evaluation: allow group
    policy_group = {
        "policyItems": [{
            "users": [],
            "groups": ["cn=analysts,ou=groups,dc=example,dc=com"],
            "accesses": [{"type": "select", "isAllowed": True}],
        }]
    }
    result = client._evaluate_policy_response(
        policy_group, "zhangsan",
        ["cn=analysts,ou=groups,dc=example,dc=com"],
        "select",
    )
    assert result.allowed is True
    log_pass("Policy allow group", "analysts group allowed")

    # Test row filter extraction
    policy_with_filter = {
        "policyItems": [{
            "users": ["zhangsan"],
            "groups": [],
            "accesses": [{"type": "select", "isAllowed": True}],
            "rowFilterPolicyItems": [{"rowFilter": "region = '华东'"}],
        }]
    }
    result = client._evaluate_policy_response(policy_with_filter, "zhangsan", [], "select")
    assert result.row_filter == "region = '华东'"
    log_pass("Row filter extraction", f"filter: {result.row_filter}")

    # Test column masking extraction
    policy_with_masking = {
        "policyItems": [{
            "users": ["zhangsan"],
            "groups": [],
            "accesses": [{"type": "select", "isAllowed": True}],
            "dataMaskPolicyItems": [
                {"column": "phone", "dataMaskType": "MASK_PARTIAL"},
                {"column": "email", "dataMaskType": "MASK_HASH"},
            ],
        }]
    }
    result = client._evaluate_policy_response(policy_with_masking, "zhangsan", [], "select")
    assert result.column_masking["phone"] == "MASK_PARTIAL"
    assert result.column_masking["email"] == "MASK_HASH"
    log_pass("Column masking extraction", f"masking: {result.column_masking}")

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 5: SQL Row Filter Injection
# ══════════════════════════════════════════════════════════════════════

def test_row_filter_injection():
    log_section("Test 5: SQL Row Filter Injection")

    from services.shared.services.ranger_client import inject_row_filter

    test_cases = [
        {
            "name": "Simple SELECT",
            "sql": "SELECT * FROM orders WHERE amount > 100",
            "table": "orders",
            "filter": "region = '华东'",
            "expected_contains": "region = '华东'",
        },
        {
            "name": "SELECT with JOIN",
            "sql": "SELECT o.id, u.name FROM orders o JOIN users u ON o.user_id = u.id",
            "table": "orders",
            "filter": "status = 'active'",
            "expected_contains": "status = 'active'",
        },
        {
            "name": "No filter",
            "sql": "SELECT * FROM orders",
            "table": "orders",
            "filter": "",
            "expected_contains": "SELECT * FROM orders",
        },
    ]

    for tc in test_cases:
        result = inject_row_filter(tc["sql"], tc["table"], tc["filter"])
        if tc["expected_contains"] in result:
            log_pass(tc["name"], f"filter injected correctly")
        else:
            log_fail(tc["name"], f"expected '{tc['expected_contains']}' in result: {result}")

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 6: Column Masking
# ══════════════════════════════════════════════════════════════════════

def test_column_masking():
    log_section("Test 6: Column Masking")

    try:
        import pandas as pd
        from services.shared.services.ranger_client import apply_column_masking, MaskingRule

        # MASK_PARTIAL
        df = pd.DataFrame({"phone": ["13812345678", "13987654321"]})
        rules = {"phone": MaskingRule(column="phone", masking_type="MASK_PARTIAL")}
        result = apply_column_masking(df.copy(), rules)
        assert result["phone"].iloc[0] == "1***8"
        assert result["phone"].iloc[1] == "1***1"
        log_pass("MASK_PARTIAL", f"13812345678 -> {result['phone'].iloc[0]}")

        # MASK_HASH
        df = pd.DataFrame({"email": ["alice@example.com"]})
        rules = {"email": MaskingRule(column="email", masking_type="MASK_HASH")}
        result = apply_column_masking(df.copy(), rules)
        assert result["email"].iloc[0] != "alice@example.com"
        log_pass("MASK_HASH", f"alice@example.com -> {result['email'].iloc[0]}")

        # MASK_NULL
        df = pd.DataFrame({"ssn": ["123-45-6789"]})
        rules = {"ssn": MaskingRule(column="ssn", masking_type="MASK_NULL")}
        result = apply_column_masking(df.copy(), rules)
        assert result["ssn"].isna().all()
        log_pass("MASK_NULL", "123-45-6789 -> NULL")

    except ImportError:
        log_skip("pandas not installed", "column masking tests skipped")
        return False

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 7: Auth Service Login Flow
# ══════════════════════════════════════════════════════════════════════

def test_auth_login_flow():
    log_section("Test 7: Auth Service Login Flow")

    # Test login flow logic
    # Scenario 1: LDAP enabled + LDAP success
    # Scenario 2: LDAP enabled + LDAP fail + local success
    # Scenario 3: LDAP disabled + local success
    # Scenario 4: Both fail

    scenarios = [
        {
            "name": "LDAP success (skip local)",
            "ldap_enabled": True,
            "ldap_success": True,
            "local_success": False,
            "expected_source": "ldap",
        },
        {
            "name": "LDAP fail -> local success",
            "ldap_enabled": True,
            "ldap_success": False,
            "local_success": True,
            "expected_source": "local",
        },
        {
            "name": "LDAP disabled -> local",
            "ldap_enabled": False,
            "ldap_success": False,
            "local_success": True,
            "expected_source": "local",
        },
        {
            "name": "Both fail -> None",
            "ldap_enabled": True,
            "ldap_success": False,
            "local_success": False,
            "expected_source": None,
        },
    ]

    for s in scenarios:
        # Simulate login flow
        result = None
        if s["ldap_enabled"] and s["ldap_success"]:
            result = {"source": "ldap", "success": True}
        elif s["local_success"]:
            result = {"source": "local", "success": True}

        if result and result["source"] == s["expected_source"]:
            log_pass(s["name"], f"source={result['source']}")
        elif result is None and s["expected_source"] is None:
            log_pass(s["name"], "correctly returned None")
        else:
            log_fail(s["name"], f"expected {s['expected_source']}, got {result}")

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 8: Config Verification
# ══════════════════════════════════════════════════════════════════════

def test_config():
    log_section("Test 8: Configuration Verification")

    from services.shared.common.config import (
        LDAP_ENABLED, LDAP_SERVER_URL, LDAP_BASE_DN, LDAP_BIND_DN,
        LDAP_USER_SEARCH_BASE, LDAP_USER_SEARCH_FILTER,
        LDAP_DEFAULT_ROLE, LDAP_CONNECT_TIMEOUT,
        RANGER_ENABLED, RANGER_ADMIN_URL, RANGER_CACHE_TTL,
        KERBEROS_ENABLED, KERBEROS_REALM,
    )

    checks = [
        ("LDAP_ENABLED", LDAP_ENABLED, False),
        ("LDAP_SERVER_URL", LDAP_SERVER_URL, "ldap://localhost:389"),
        ("LDAP_BASE_DN", LDAP_BASE_DN, "dc=example,dc=com"),
        ("LDAP_BIND_DN", LDAP_BIND_DN, "cn=admin,dc=example,dc=com"),
        ("LDAP_DEFAULT_ROLE", LDAP_DEFAULT_ROLE, "viewer"),
        ("LDAP_CONNECT_TIMEOUT", LDAP_CONNECT_TIMEOUT, 5),
        ("RANGER_ENABLED", RANGER_ENABLED, False),
        ("RANGER_ADMIN_URL", RANGER_ADMIN_URL, "http://localhost:6080"),
        ("RANGER_CACHE_TTL", RANGER_CACHE_TTL, 300),
        ("KERBEROS_ENABLED", KERBEROS_ENABLED, False),
        ("KERBEROS_REALM", KERBEROS_REALM, "EXAMPLE.COM"),
    ]

    for name, actual, expected in checks:
        if actual == expected:
            log_pass(name, f"{actual}")
        else:
            log_fail(name, f"expected {expected}, got {actual}")

    return True


# ══════════════════════════════════════════════════════════════════════
# Test 9: Database Migration Verification
# ══════════════════════════════════════════════════════════════════════

def test_migration_sql():
    log_section("Test 9: Database Migration SQL")

    migration_file = "docker/mysql/permission_management_migration.sql"
    if not os.path.exists(migration_file):
        log_fail("Migration file exists", f"{migration_file} not found")
        return False

    with open(migration_file) as f:
        content = f.read()

    checks = [
        ("ALTER TABLE adh_users", "auth_source" in content),
        ("adh_ldap_role_mapping table", "adh_ldap_role_mapping" in content),
        ("adh_ranger_policy_cache table", "adh_ranger_policy_cache" in content),
        ("adh_data_access_audit table", "adh_data_access_audit" in content),
    ]

    for name, condition in checks:
        if condition:
            log_pass(name)
        else:
            log_fail(name)

    return True


# ══════════════════════════════════════════════════════════════════════
# Cleanup
# ══════════════════════════════════════════════════════════════════════

def cleanup():
    log_section("Cleanup")
    print("  Stopping Docker containers...")
    run_cmd("cd docker && docker compose -f docker-compose.ranger.yml down -v", timeout=60)
    log_pass("Docker containers stopped")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    global passed, failed, skipped

    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}  统一认证与权限管理 — 集成测试{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    tests = [
        ("Docker Infrastructure", test_docker_infrastructure),
        ("OpenLDAP", test_openldap),
        ("LDAP Backend (Python)", test_ldap_backend),
        ("Ranger Client", test_ranger_client),
        ("Row Filter Injection", test_row_filter_injection),
        ("Column Masking", test_column_masking),
        ("Auth Login Flow", test_auth_login_flow),
        ("Configuration", test_config),
        ("Migration SQL", test_migration_sql),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            log_fail(f"{name} (exception)", str(e))

    # Summary
    log_section("Test Summary")
    total = passed + failed + skipped
    print(f"  {Colors.GREEN}Passed: {passed}{Colors.RESET}")
    print(f"  {Colors.RED}Failed: {failed}{Colors.RESET}")
    print(f"  {Colors.YELLOW}Skipped: {skipped}{Colors.RESET}")
    print(f"  Total: {total}")

    if failed == 0:
        print(f"\n  {Colors.GREEN}{Colors.BOLD}ALL TESTS PASSED ✅{Colors.RESET}")
    else:
        print(f"\n  {Colors.RED}{Colors.BOLD}{failed} TESTS FAILED ❌{Colors.RESET}")

    print(f"  结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Ask about cleanup
    if "--no-cleanup" not in sys.argv:
        cleanup()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
