"""配置变更审计的回归门禁。

锁定中间件层集中式审计的五条硬约束：
  1) 所有配置变更都落审计，**不依赖各端点自觉补记**（此前漏成"什么都没记"）；
  2) 被权限拒绝（403）的变更同样要记（security-guardrails §9：成功与拒绝均落审计）；
  3) 取数/会话链路不重复记（另有 adh_query_audit / 取数审计）；
  4) **请求体的值一律不入审计**（可能含密码/密钥/连接串，守 §7）；
  5) 审计写失败不得影响主链路（best-effort，只 log 不抛）。
"""
import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.common import api_permission as ap


class _Recorder:
    """捕获 log_audit 的调用，避免真写库。"""

    def __init__(self):
        self.rows = []

    def __call__(self, user_id, username, action="", target_type="", target_id=0,
                 detail="", ip_address="", module=""):
        self.rows.append({"user_id": user_id, "username": username, "action": action,
                          "target_type": target_type, "target_id": target_id,
                          "detail": detail, "ip_address": ip_address, "module": module})


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    import backend.common.auth as auth_mod
    monkeypatch.setattr(auth_mod, "log_audit", rec)

    # 身份解析：固定返回 admin，权限放行
    monkeypatch.setattr(auth_mod, "resolve_current_user",
                        lambda token: {"user_id": 7, "username": "admin", "role": "admin"})
    monkeypatch.setattr(ap, "check_api_permission", lambda *a, **k: True)
    return rec


def _app():
    app = FastAPI()

    @app.post("/api/admin/as_bots/123")
    def update_as_bot(payload: dict):
        return {"ok": True}

    @app.post("/api/roles/5/permissions")
    def set_perms(payload: dict):
        return {"ok": True}

    @app.post("/api/eval/cases")
    def create_case(payload: dict):
        return {"ok": True}

    @app.post("/api/chat/conversations")
    def chat(payload: dict):
        return {"ok": True}

    @app.get("/api/admin/as_bots")
    def list_as_bots():
        return {"ok": True}

    @app.post("/api/denied/thing")
    def denied(payload: dict):
        return {"ok": True}

    ap.add_api_permission_middleware(app)
    return app


def _client():
    return TestClient(_app(), raise_server_exceptions=False)


HDR = {"Authorization": "Bearer x"}


# ═══════════════════════════════════════════════════════════════════════════
# 1. 配置变更必须落审计
# ═══════════════════════════════════════════════════════════════════════════

def test_config_change_is_audited(recorder):
    _client().post("/api/admin/as_bots/123", json={"name": "w1"}, headers=HDR)
    assert recorder.rows, "配置变更没有落审计"
    row = recorder.rows[-1]
    assert row["user_id"] == 7 and row["username"] == "admin"
    assert row["action"] == "update_as_bot"
    assert row["target_type"] == "as_bot" and row["target_id"] == 123
    assert "POST /api/admin/as_bots/123" in row["detail"]


def test_nested_config_path_is_audited(recorder):
    _client().post("/api/roles/5/permissions", json={"permissions": []}, headers=HDR)
    row = recorder.rows[-1]
    assert row["target_type"] == "role_permission"
    assert row["target_id"] == 5


def test_eval_case_create_is_audited(recorder):
    _client().post("/api/eval/cases", json={"case_key": "k"}, headers=HDR)
    row = recorder.rows[-1]
    assert row["action"] == "create_eval_case"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 拒绝也要记 / 只读不记 / 取数链路不记
# ═══════════════════════════════════════════════════════════════════════════

def test_denied_change_is_also_audited(recorder, monkeypatch):
    monkeypatch.setattr(ap, "check_api_permission", lambda *a, **k: False)
    resp = _client().post("/api/denied/thing", json={"a": 1}, headers=HDR)
    assert resp.status_code == 403
    assert recorder.rows, "被拒的变更没有落审计（护栏 §9 要求成功与拒绝均落）"
    assert "权限拒绝" in recorder.rows[-1]["detail"]


def test_read_only_request_not_audited(recorder):
    _client().get("/api/admin/as_bots", headers=HDR)
    assert recorder.rows == []


def test_query_and_session_paths_not_audited(recorder):
    """取数/会话链路另有审计，这里不重复记。"""
    _client().post("/api/chat/conversations", json={}, headers=HDR)
    assert recorder.rows == []


def test_auth_paths_not_audited(recorder):
    _client().post("/api/auth/login", json={}, headers=HDR)
    assert recorder.rows == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. 值不入审计（守 security-guardrails §7）
# ═══════════════════════════════════════════════════════════════════════════

def test_body_values_never_leak_into_audit(recorder):
    secret = "S3cr3tP@ssw0rd-should-never-be-logged"
    _client().post("/api/admin/as_bots/123",
                   json={"name": "w1", "password": secret, "api_key": secret,
                         "connection_string": "jdbc:mysql://10.0.0.1:3306/db"},
                   headers=HDR)
    row = recorder.rows[-1]
    blob = f"{row['detail']}"
    assert secret not in blob, "审计里出现了请求体的值（密码/密钥）"
    assert "jdbc:mysql" not in blob, "审计里出现了连接串"
    assert "10.0.0.1" not in blob, "审计里出现了 IP"
    # 但字段名要能看见，否则审计回答不了"改了什么"
    for key in ("api_key", "connection_string", "name", "password"):
        assert key in blob


# ═══════════════════════════════════════════════════════════════════════════
# 4. 目标推导
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path,expected", [
    ("/api/admin/as_bots/123", ("as_bot", 123)),
    ("/api/datasources/", ("datasource", 0)),
    ("/api/roles/5/permissions", ("role_permission", 5)),
    ("/api/eval/cases/12", ("eval_case", 12)),
    ("/api/admin/rls-policies/7004", ("rls_policy", 7004)),
])
def test_derive_audit_target(path, expected):
    target, tid, _module = ap._derive_audit_target(path)
    assert (target, tid) == expected


def test_module_mapping_covers_admin_config():
    _t, _i, module = ap._derive_audit_target("/api/admin/as_bots/1")
    assert module == "model"
    _t, _i, module = ap._derive_audit_target("/api/datasources/1")
    assert module == "datasource"


# ═══════════════════════════════════════════════════════════════════════════
# 5. 审计失败不影响主链路
# ═══════════════════════════════════════════════════════════════════════════

def test_audit_failure_does_not_break_request(recorder, monkeypatch):
    import backend.common.auth as auth_mod

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(auth_mod, "log_audit", _boom)
    resp = _client().post("/api/admin/as_bots/123", json={"name": "w"}, headers=HDR)
    assert resp.status_code == 200, "审计写失败不应影响主链路（护栏 §9）"


def test_should_audit_blacklist_is_denylist_not_allowlist():
    """黑名单口径：未知的新配置路径默认**要记**，避免新增端点产生审计盲区。"""
    assert ap._should_audit("POST", "/api/some/brand/new/config") is True
    assert ap._should_audit("POST", "/api/chat/conversations") is False
    assert ap._should_audit("GET", "/api/some/brand/new/config") is False


# ═══════════════════════════════════════════════════════════════════════════
# 6. 写语句不得报假失败（曾把“删成功”报成 deleted:false）
# ═══════════════════════════════════════════════════════════════════════════

def test_write_statement_returns_affected_rows_not_none():
    """store._exec 对写语句必须回受影响行数；回 None 会让 bool() 恒 False，
    接口把成功报成失败（假失败，比报错更骗人）。"""
    import inspect
    from backend.eval import store
    src = inspect.getsource(store._exec)
    assert "cur.rowcount" in src, "写语句应回受影响行数，不能回 None"


def test_delete_case_returns_true_on_success(monkeypatch):
    """删成功必须回 True（实测曾恒回 False）。"""
    from backend.eval import store
    monkeypatch.setattr(store, "_exec", lambda *a, **k: 1)
    assert store.delete_case(1) is True
    monkeypatch.setattr(store, "_exec", lambda *a, **k: 0)
    assert store.delete_case(999) is False
