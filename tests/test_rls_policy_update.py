"""数据安全策略编辑保存防假成功契约.

回归背景: 编辑策略改「数据源/目标表名」时, RLSPolicyUpdate 模型与 update_policy
白名单都缺这两个字段, 前端提示"保存成功"但改动被静默丢弃; 且策略不存在/无有效
字段时 API 也照样返回 success。
"""
import pytest
from fastapi import HTTPException

from services.authservice.api import rls as rls_api
from services.authservice.services import rls_service as rls_svc_mod


class _FakeCursor:
    def __init__(self, rowcount=1, exists=True):
        self.rowcount = rowcount
        self.exists = exists
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return {"id": 1} if self.exists else None


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def close(self):
        pass


def test_update_model_keeps_datasource_and_table(monkeypatch):
    """编辑载荷里的 datasource_id/table_name 不得被模型静默丢弃。"""
    req = rls_api.RLSPolicyUpdate(
        name="p", datasource_id=7, table_name="t_orders", filter_expr="region='cn'")
    data = req.model_dump(exclude_unset=True)
    assert data["datasource_id"] == 7
    assert data["table_name"] == "t_orders"


def test_update_policy_persists_datasource_and_table(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    ok = rls_svc_mod.rls_service.update_policy(
        1, {"datasource_id": 7, "table_name": "t_orders", "name": "p"})
    assert ok is True
    sql, params = cur.executed[0]
    assert "datasource_id = %s" in sql and "table_name = %s" in sql
    assert "t_orders" in params and 7 in params


def test_update_policy_no_fields_returns_false(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    # 仅含白名单外字段(如 workspace_id) → 无有效更新, 必须 False
    assert rls_svc_mod.rls_service.update_policy(1, {"workspace_id": 0}) is False
    assert not any(s.startswith("UPDATE") for s, _ in cur.executed)


def test_update_policy_missing_record_returns_false(monkeypatch):
    """affected=0 且记录已不存在 → 更新未生效(False), 不得当成功。"""
    cur = _FakeCursor(rowcount=0, exists=False)
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    assert rls_svc_mod.rls_service.update_policy(1, {"name": "x"}) is False


def test_update_policy_unchanged_value_is_idempotent_success(monkeypatch):
    """affected=0 但记录仍在(值未变化) → 幂等保存成功(True)。"""
    cur = _FakeCursor(rowcount=0, exists=True)
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    assert rls_svc_mod.rls_service.update_policy(1, {"name": "x"}) is True


def _fake_service(monkeypatch, *, old={"id": 1, "name": "p", "workspace_id": 0, "table_name": "t"}, update_ok=True):
    class _S:
        def get_policy(self, pid):
            return old

        def update_policy(self, pid, data):
            return update_ok

        def log_audit(self, **kw):
            pass

    monkeypatch.setattr(rls_svc_mod, "rls_service", _S())


def test_api_update_missing_policy_is_404(monkeypatch):
    _fake_service(monkeypatch, old=None)
    with pytest.raises(HTTPException) as e:
        rls_api.update_rls_policy(999, rls_api.RLSPolicyUpdate(name="x"),
                                  admin={"user_id": 1})
    assert e.value.status_code == 404


def test_api_update_not_applied_is_400(monkeypatch):
    """更新未落库必须显式报错, 绝不返回 success(防假成功)。"""
    _fake_service(monkeypatch, update_ok=False)
    with pytest.raises(HTTPException) as e:
        rls_api.update_rls_policy(1, rls_api.RLSPolicyUpdate(name="x"),
                                  admin={"user_id": 1})
    assert e.value.status_code == 400
    assert "更新未生效" in e.value.detail


def test_api_update_success_when_persisted(monkeypatch):
    _fake_service(monkeypatch, update_ok=True)
    res = rls_api.update_rls_policy(1, rls_api.RLSPolicyUpdate(table_name="t2"),
                                    admin={"user_id": 1})
    assert res == {"success": True}
