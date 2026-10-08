"""可信分析 M0/M1 回归：仅使用内存身份、策略与路由，不访问真实数据库。"""
import asyncio
from unittest.mock import Mock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from services.shared.common import api_permission as api
from services.shared.common import auth
from services.datamind.permission.enforcer import PermissionEnforcer


@pytest.fixture(autouse=True)
def isolated_metadata(monkeypatch):
    api._cache.clear()
    monkeypatch.setattr("services.shared.common.db.execute_query", lambda *a, **k: [])
    yield
    api._cache.clear()


def test_empty_permissions_deny():
    assert not api.check_api_permission("viewer", "GET", "/api/quality/results")


def test_registry_expands_patterns_and_methods(monkeypatch):
    def query(sql, params=(), **kwargs):
        assert "adh_role_apis" not in sql
        if "FROM adh_roles" in sql:
            return [{"id": 2}]
        if "adh_role_perms" in sql:
            return [{"perm_code": "quality:read"}]
        if "adh_perm_registry" in sql:
            return [{"perm_code": "quality:read", "api_pattern": "/api/quality/*,/api/lineage/*", "api_method": "GET,POST"}]
        return []
    monkeypatch.setattr("services.shared.common.db.execute_query", query)
    assert api.check_api_permission("viewer", "POST", "/api/lineage/preview")
    assert not api.check_api_permission("viewer", "DELETE", "/api/lineage/preview")


def test_policy_failure_deny(monkeypatch):
    monkeypatch.setattr("services.shared.common.db.execute_query", Mock(side_effect=RuntimeError("offline")))
    assert not api.check_api_permission("viewer", "GET", "/api/quality/results")


def test_anonymous_cannot_reach_unannotated_route():
    app = FastAPI()
    api.add_api_permission_middleware(app)

    @app.get("/api/private")
    def private():
        return {"secret": "不得返回"}

    assert TestClient(app).get("/api/private").status_code == 401


def test_forged_admin_token_rejected(monkeypatch):
    monkeypatch.setattr(auth, "ADH_SECRET_KEY", "trusted-key")
    token = jwt.encode({"user_id": 1, "role": "admin", "exp": 9999999999}, "forged-key", algorithm="HS256")
    req = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    assert api._extract_role_from_request(req) is None


def test_auth_uses_live_role_and_rejects_disabled(monkeypatch):
    monkeypatch.setattr(auth, "decode_token", lambda token: {"user_id": 7, "role": "admin"})
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid: {"id": uid, "username": "reader", "user_role": "viewer", "status": "active"})
    credentials = auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials="test")
    assert asyncio.run(auth.get_current_user(credentials))["role"] == "viewer"
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid: {"id": uid, "status": "disabled"})
    with pytest.raises(HTTPException):
        asyncio.run(auth.get_current_user(credentials))


def test_workspace_zero_not_global_access():
    with pytest.raises(HTTPException):
        auth.authorize_workspace({"user_id": 7, "role": "viewer"}, 0)


def test_workspace_access_requires_scoped_role(monkeypatch):
    from services.authservice.services.role_service import role_service
    # 工作空间属主制(个人工作站): 授权口径是 check_workspace_owner,
    # 旧成员体系的 check_user_workspace_access 已退役, 打桩须对准现行方法
    monkeypatch.setattr(role_service, "check_workspace_owner", lambda uid, ws: ws == 3)
    assert auth.authorize_workspace({"user_id": 7, "role": "viewer"}, 3) == 3
    with pytest.raises(HTTPException):
        auth.authorize_workspace({"user_id": 7, "role": "viewer"}, 4)


@pytest.mark.parametrize("sql, expected", [
    ("SELECT * FROM `db`.`orders` o JOIN `db`.`users` u ON o.uid=u.id", ["db.orders", "db.users"]),
    ("WITH orders AS (SELECT * FROM source_orders) SELECT * FROM orders", ["source_orders"]),
    ("WITH a AS (SELECT * FROM db.a) SELECT * FROM a JOIN db.b ON 1=1", ["db.a", "db.b"]),
])
def test_scoped_physical_tables(sql, expected):
    assert PermissionEnforcer()._extract_tables(sql) == expected


def test_rls_qualified_join_and_alias():
    enf = PermissionEnforcer()
    sql = "SELECT o.id, u.id FROM db.orders o JOIN db.users u ON o.uid=u.id"
    out = enf._inject_row_filter(sql, "db.users", "region = 'east'")
    assert "WHERE region = 'east'" in out
    assert "AS u" in out
    assert "u.id" in out


def test_rls_cte_does_not_filter_virtual_reference():
    enf = PermissionEnforcer()
    sql = "WITH users AS (SELECT * FROM db.users) SELECT * FROM users"
    out = enf._inject_row_filter(sql, "db.users", "region = 'east'")
    assert out.count("region = 'east'") == 1


def test_sensitive_policy_failure_is_closed(monkeypatch):
    monkeypatch.setattr("services.shared.common.db.execute_query", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(PermissionError):
        PermissionEnforcer()._get_sensitive_policies(3, 1, "orders")


@pytest.mark.parametrize("sql", ["DELETE FROM t", "SELECT 1; SELECT 2", "CREATE TABLE t AS SELECT 1"])
def test_table_parser_rejects_non_query(sql):
    with pytest.raises(PermissionError):
        PermissionEnforcer()._extract_tables(sql)
