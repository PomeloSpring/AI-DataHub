"""数据安全策略编辑保存防假成功契约.

回归背景: 编辑策略改「数据源/目标表名」时, RLSPolicyUpdate 模型与 update_policy
白名单都缺这两个字段, 前端提示"保存成功"但改动被静默丢弃; 且策略不存在/无有效
字段时 API 也照样返回 success。
"""
import pytest
from fastapi import HTTPException

from backend.modules.auth.api import rls as rls_api
from backend.core import rls_service as rls_svc_mod


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


# ── 列策略纯黑名单契约 (access_type 只允许 hidden/masked) ────────────


class _RoutingCursor(_FakeCursor):
    """按 SQL 关键字路由 fetchone/fetchall 结果的假游标。"""

    def __init__(self, *, one_by_table=None, all_by_table=None, rowcount=1):
        super().__init__(rowcount=rowcount)
        self._one_by_table = one_by_table or {}
        self._all_by_table = all_by_table or {}

    def fetchone(self):
        last = self.executed[-1][0] if self.executed else ""
        for key, row in self._one_by_table.items():
            if key in last:
                return row
        return None

    def fetchall(self):
        last = self.executed[-1][0] if self.executed else ""
        for key, rows in self._all_by_table.items():
            if key in last:
                return rows
        return []


def test_column_policy_item_defaults_hidden():
    """写入口载荷默认 hidden(不传即屏蔽), 不得默认 visible 把白名单语义落库。"""
    item = rls_api.ColumnPolicyItem(column_name="salary")
    assert item.access_type == "hidden"


def test_set_column_policies_rejects_visible_row(monkeypatch):
    """黑名单行 access_type='visible' 必须整批拒绝(执行层会静默忽略它)。"""
    cur = _RoutingCursor(one_by_table={"adh_rls_column_policies": {"policy_id": 1}})
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    with pytest.raises(ValueError) as e:
        rls_svc_mod.rls_service.set_column_policies(1, [{"column_name": "salary", "access_type": "visible"}])
    assert "visible" in str(e.value)
    assert not any("INSERT" in sql for sql, _ in cur.executed)


def test_set_column_policies_accepts_blacklist_rows(monkeypatch):
    cur = _RoutingCursor(one_by_table={"adh_rls_column_policies": {"policy_id": 1}})
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    ok = rls_svc_mod.rls_service.set_column_policies(1, [
        {"column_name": "salary", "access_type": "hidden"},
        {"column_name": "phone", "access_type": "masked", "mask_pattern": "partial"},
    ])
    assert ok is True
    inserts = [(s, p) for s, p in cur.executed if "INSERT" in s]
    assert [p[3] for _, p in inserts] == ["hidden", "masked"]


def test_set_column_policies_requires_baseline_when_policy_exists(monkeypatch):
    """策略存在但列策略本体无基线行 → 拒绝"新建空壳落黑名单"，防双写入口漂移。"""
    cur = _RoutingCursor(one_by_table={
        "adh_rls_column_policies": None,
        "adh_rls_policies": {"id": 1},
    })
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    with pytest.raises(ValueError) as e:
        rls_svc_mod.rls_service.set_column_policies(1, [{"column_name": "a", "access_type": "hidden"}])
    assert "基线" in str(e.value)


def test_roles_api_column_policies_value_error_is_400(monkeypatch):
    """roles.py 列策略端点把校验 ValueError 转 400 带原因, 不冒 500 不假成功。"""
    from backend.modules.auth.api import roles as roles_api

    class _S:
        def set_column_policies(self, pid, cols):
            raise ValueError("access_type='visible' 非法")

    monkeypatch.setattr(roles_api, "rls_service", _S())
    req = roles_api.SetRLSColumnPoliciesRequest(columns=[{"column_name": "a", "access_type": "visible"}])
    with pytest.raises(HTTPException) as e:
        roles_api.set_rls_column_policies(1, req, admin={"user_id": 1})
    assert e.value.status_code == 400
    assert "非法" in e.value.detail


# ── 行策略属性名清单 (选择搜索框候选, 不手填) ────────────────────────


def test_list_role_attr_keys_returns_deduped_sorted(monkeypatch):
    cur = _FakeCursor()
    cur.fetchall = lambda: [{"attr_key": "region"}, {"attr_key": "dept"}, {"attr_key": None}]
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    keys = rls_svc_mod.rls_service.list_role_attr_keys()
    assert keys == ["region", "dept"]
    assert "DISTINCT attr_key" in cur.executed[0][0]


def test_api_role_attr_keys_endpoint(monkeypatch):
    class _S:
        def list_role_attr_keys(self):
            return ["region"]

    monkeypatch.setattr(rls_svc_mod, "rls_service", _S())
    assert rls_api.list_role_attr_keys() == {"keys": ["region"]}


# ── 表策略 policy_type='table' 白名单 ───────────────────────────────


def _patch_user_roles(monkeypatch, role_ids):
    from backend.core import role_service as rs_mod

    class _RS:
        def get_user_roles(self, uid, ws):
            return [{"id": rid} for rid in role_ids]

    monkeypatch.setattr(rs_mod, "role_service", _RS())


def test_table_whitelist_none_when_no_table_policy_bound(monkeypatch):
    """角色只绑行/列策略 → 表级不限制(None), 不得误启用空白名单全拒。"""
    _patch_user_roles(monkeypatch, [5])
    cur = _RoutingCursor(all_by_table={
        "adh_role_rls_policies": [{"policy_id": 1}],
        "adh_rls_policies": [],
    })
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    assert rls_svc_mod.rls_service.get_role_table_whitelist(1, 0, 1) is None


def test_table_whitelist_returns_allowed_tables(monkeypatch):
    _patch_user_roles(monkeypatch, [5])
    cur = _RoutingCursor(all_by_table={
        "adh_role_rls_policies": [{"policy_id": 7}],
        "adh_rls_policies": [{"table_name": "orders"}, {"table_name": None}],
    })
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    wl = rls_svc_mod.rls_service.get_role_table_whitelist(1, 0, 1)
    assert wl == {"orders"}
    sql = cur.executed[-1][0]
    assert "policy_type = 'table'" in sql


def test_enforcer_denies_table_outside_whitelist(monkeypatch):
    """表策略绑定后, 清单外表在 check_access 表访问步即拒(无需触达数据源)。"""
    from backend.core import enforcer as enf
    from backend.core import role_service as rs_mod

    class _RS:
        def get_user_allowed_datasources(self, uid, ws):
            return [1]

        def get_user_allowed_tables(self, uid, ds, ws):
            return []

        def get_user_column_restrictions(self, uid, ds, t, ws):
            return {}

    monkeypatch.setattr(rs_mod, "role_service", _RS())
    monkeypatch.setattr(rls_svc_mod.rls_service, "get_role_table_whitelist",
                        lambda uid, ws, ds: {"orders"})
    monkeypatch.setattr(rls_svc_mod.rls_service, "get_effective_policies",
                        lambda *a, **kw: {"row_filter": "", "hidden_columns": [],
                                          "masked_columns": {}, "policies_applied": []})
    e = enf.PermissionEnforcer()
    monkeypatch.setattr(e, "_get_sensitive_policies", lambda ws, ds, t: ({}, []))
    enf._access_cache.clear()
    denied = e.check_access(1, 0, 1, table_name="secret_tbl")
    assert denied.allowed is False and "白名单" in denied.reason
    allowed = e.check_access(1, 0, 1, table_name="orders")
    assert allowed.allowed is True
    enf._access_cache.clear()
