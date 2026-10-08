"""角色-数据安全策略绑定 —— 离线单测(不触碰真实数据库)。

覆盖:
  R1  GET/PUT /roles/{role_id}/rls-policies 往返、全量替换、去重;
  R2  404 未知角色;
  R3  get_effective_policies 仅应用「用户任一角色绑定的策略」(勾选才生效):
      未绑定的策略不施加, 即使 workspace/datasource/table 匹配;
  R4  无角色 / 无绑定 = 空限制(不施加任何 RLS 行/列限制);
  R5  workspace_id=0 的全局策略对实际工作空间同样生效;
  R6  :user_xxx 属性取值: 统一由角色属性供给(adh_rls_user_attributes 已退役, 残留数据不得被读取);
  R7  PUT 绑定后 invalidate_access_cache() 生效(enforcer 访问缓存清空)。

全部用假 execute_query/execute_write / 假 cursor 注入, 不落真库。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

import pytest
from fastapi import HTTPException

roles_api = importlib.import_module("backend.modules.auth.api.roles")
rls_svc_mod = importlib.import_module("backend.modules.auth.services.rls_service")
role_svc_mod = importlib.import_module("backend.modules.auth.services.role_service")

ADMIN = {"role": "admin", "user_id": 1}
VIEWER = {"role": "viewer", "user_id": 9}

# ── 假绑定/策略状态 ──────────────────────────────────────────────────

POLICIES = [
    {"id": 10, "name": "华东过滤", "workspace_id": 5, "datasource_id": 1, "table_name": "orders",
     "policy_type": "both", "filter_type": "user_attribute", "filter_expr": "region = :user_region",
     "user_attribute": "region", "is_active": 1},
    {"id": 11, "name": "未绑定策略", "workspace_id": 5, "datasource_id": 1, "table_name": "orders",
     "policy_type": "row", "filter_type": "condition", "filter_expr": "status = 'active'",
     "user_attribute": "", "is_active": 1},
    {"id": 12, "name": "全局策略", "workspace_id": 0, "datasource_id": 1, "table_name": "orders",
     "policy_type": "column", "filter_type": "condition", "filter_expr": "",
     "user_attribute": "", "is_active": 1},
]

COLUMN_POLICIES = {
    10: [{"column_name": "salary", "access_type": "hidden", "mask_pattern": ""}],
    12: [{"column_name": "phone", "access_type": "masked", "mask_pattern": "partial"}],
}


class _Store:
    def __init__(self):
        self.roles = {7: "viewer"}
        self.bindings: dict[int, set] = {7: set()}   # role_id → policy ids
        self.writes: list = []


@pytest.fixture
def store(monkeypatch):
    st = _Store()

    def fake_query(sql, params=None, fetchone=False):
        s = " ".join(sql.split())
        p = list(params or [])
        if "FROM adh_roles WHERE id=" in s:
            rid = p[0]
            rows = [{"id": rid}] if rid in st.roles else []
        elif "FROM adh_role_rls_policies" in s:
            rows = [{"policy_id": pid} for pid in sorted(st.bindings.get(p[0], set()))]
        else:
            rows = []
        if fetchone:
            return rows[0] if rows else None
        return rows

    def fake_write(sql, params=None):
        s = " ".join(sql.split())
        p = list(params or [])
        st.writes.append((s, p))
        if "DELETE FROM adh_role_rls_policies" in s:
            st.bindings[p[0]] = set()
        elif "INSERT IGNORE INTO adh_role_rls_policies" in s:
            st.bindings.setdefault(p[0], set()).add(p[1])

    monkeypatch.setattr(roles_api, "execute_query", fake_query)
    monkeypatch.setattr(roles_api, "execute_write", fake_write)
    return st


# ── R1 GET/PUT 往返、全量替换 ───────────────────────────────────────


def test_rls_policies_roundtrip_and_full_replace(store):
    req = roles_api.SetRoleRLSPoliciesRequest(policy_ids=[10, 12, 10])
    roles_api.set_role_rls_policies(7, req, admin=ADMIN)
    assert store.bindings[7] == {10, 12}
    # 写序列: 先全量 DELETE 再逐条 INSERT(全量替换语义)
    assert store.writes[0][0].startswith("DELETE FROM adh_role_rls_policies")
    assert all(s.startswith("INSERT IGNORE") for s, _ in store.writes[1:])

    got = roles_api.get_role_rls_policies(7, user=VIEWER)
    assert got == {"role_id": 7, "policy_ids": [10, 12]}

    # 再次保存 = 全量替换(不再包含 12)
    roles_api.set_role_rls_policies(7, roles_api.SetRoleRLSPoliciesRequest(policy_ids=[10]),
                                    admin=ADMIN)
    assert store.bindings[7] == {10}
    assert roles_api.get_role_rls_policies(7, user=VIEWER)["policy_ids"] == [10]


def test_set_rls_policies_empty_clears(store):
    store.bindings[7] = {10, 11}
    roles_api.set_role_rls_policies(7, roles_api.SetRoleRLSPoliciesRequest(policy_ids=[]),
                                    admin=ADMIN)
    assert store.bindings[7] == set()
    assert roles_api.get_role_rls_policies(7, user=VIEWER)["policy_ids"] == []


# ── R2 404 ──────────────────────────────────────────────────────────


def test_set_rls_policies_unknown_role_404(store):
    with pytest.raises(HTTPException) as exc:
        roles_api.set_role_rls_policies(99, roles_api.SetRoleRLSPoliciesRequest(policy_ids=[10]),
                                        admin=ADMIN)
    assert exc.value.status_code == 404


# ── 执行层: get_effective_policies 按绑定裁决 ────────────────────────


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = list(params or [])
        if "FROM adh_role_rls_policies" in s:
            # SELECT DISTINCT policy_id ... WHERE role_id IN (...)
            bound: set = set()
            for rid in p:
                bound |= self.db["bindings"].get(rid, set())
            self._rows = [{"policy_id": pid} for pid in sorted(bound)]
        elif "FROM adh_rls_policies" in s:
            # SELECT * ... WHERE id IN (...) AND (workspace_id=%s OR workspace_id=0)
            #                AND datasource_id=%s AND table_name=%s AND is_active=1
            # params = (*bound_ids, workspace_id, datasource_id, table_name) → 尾部 3 个固定参
            n_bound = len(p) - 3
            ids = set(p[:n_bound])
            ws, ds, table = p[n_bound], p[n_bound + 1], p[n_bound + 2]
            self._rows = [
                dict(pol) for pol in self.db["policies"]
                if pol["id"] in ids and pol["is_active"] == 1
                and pol["datasource_id"] == ds and pol["table_name"] == table
                and pol["workspace_id"] in (ws, 0)
            ]
        elif "FROM adh_rls_column_policies" in s:
            pid = p[0]
            self._rows = [dict(c) for c in self.db["column_policies"].get(pid, [])]
        elif "FROM adh_rls_user_attributes" in s:
            # 退役表残留数据: 仅作反证 —— 若代码回退读取该表会拿到"全局值/华东",
            # 相关断言会因出现这些值而失败
            uid, ws = p[0], p[1]
            rows = sorted(
                [dict(a) for a in self.db["user_attrs"]
                 if a["user_id"] == uid and a["workspace_id"] in (ws, 0)],
                key=lambda r: r["workspace_id"])
            self._rows = [{"attr_key": r["attr_key"], "attr_value": r["attr_value"]} for r in rows]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def close(self):
        pass

    def commit(self):
        pass


@pytest.fixture
def exec_db(monkeypatch):
    db = {
        "policies": [dict(p) for p in POLICIES],
        "column_policies": {k: [dict(c) for c in v] for k, v in COLUMN_POLICIES.items()},
        "bindings": {2: {10}},          # analyst(2) 绑定策略 10
        # 退役的用户属性表残留数据(仅作反证, 代码不得读取)
        "user_attrs": [
            {"user_id": 9, "workspace_id": 0, "attr_key": "region", "attr_value": "全局值"},
            {"user_id": 9, "workspace_id": 5, "attr_key": "region", "attr_value": "华东"},
        ],
        "role_attrs": {"region": "华南", "dept": "销售"},
    }
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(db))
    monkeypatch.setattr(role_svc_mod.role_service, "get_user_roles",
                        lambda user_id, workspace_id=None: [{"id": 2, "name": "analyst"}])
    monkeypatch.setattr(role_svc_mod.role_service, "get_user_effective_attributes",
                        lambda user_id, workspace_id: dict(db["role_attrs"]))
    return db


def test_effective_policies_only_bound_apply(exec_db):
    """R3: 策略 10/11 同表匹配, 但 11 未绑定 → 仅 10 生效。"""
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert res["policies_applied"] == [10]
    assert "region = '华南'" in res["row_filter"]
    assert "华东" not in res["row_filter"]   # 旧用户属性残留不生效
    assert "status = 'active'" not in res["row_filter"]   # 未绑定策略不施加
    assert res["hidden_columns"] == ["salary"]


def test_effective_policies_unbound_no_restriction(exec_db):
    """R4: 未绑定任何策略 = 空限制(勾选才生效)。"""
    exec_db["bindings"] = {2: set()}
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert res == {"row_filter": "", "hidden_columns": [], "masked_columns": {}, "policies_applied": []}


def test_effective_policies_no_roles_no_restriction(exec_db, monkeypatch):
    """R4: 无角色 = 无绑定 = 空限制。"""
    monkeypatch.setattr(role_svc_mod.role_service, "get_user_roles",
                        lambda user_id, workspace_id=None: [])
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert res["policies_applied"] == []


def test_effective_policies_ws0_global_policy(exec_db):
    """R5: workspace_id=0 的全局策略对实际工作空间生效。"""
    exec_db["bindings"] = {2: {12}}
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert res["policies_applied"] == [12]
    assert res["masked_columns"] == {"phone": "partial"}


def test_effective_policies_inactive_policy_skipped(exec_db):
    """is_active=0 的绑定策略不施加。"""
    for pol in exec_db["policies"]:
        if pol["id"] == 10:
            pol["is_active"] = 0
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert res["policies_applied"] == []


# ── R6 :user_xxx 属性取值(统一由角色属性供给) ──────────────────────


def test_user_attr_value_comes_from_role_attrs(exec_db):
    """用户属性已退役: :user_xxx 取值只来自角色属性, 库中残留旧数据不被读取。"""
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert "region = '华南'" in res["row_filter"]                       # 角色属性
    assert "华东" not in res["row_filter"] and "全局值" not in res["row_filter"]


def test_missing_role_attr_replaces_empty_not_legacy(exec_db):
    """角色无该属性 → 替换为空串; 不得回退读取旧用户属性。"""
    exec_db["role_attrs"] = {}
    res = rls_svc_mod.rls_service.get_effective_policies(9, 5, 1, "orders")
    assert "region = ''" in res["row_filter"]
    assert "华东" not in res["row_filter"] and "全局值" not in res["row_filter"]


# ── R7 PUT 后 enforcer 访问缓存失效 ──────────────────────────────────


def test_put_rls_policies_invalidates_access_cache(store):
    enforcer_mod = importlib.import_module("backend.modules.mind.permission.enforcer")
    enforcer_mod._access_cache["9:5:1:orders"] = (0.0, object())
    roles_api.set_role_rls_policies(7, roles_api.SetRoleRLSPoliciesRequest(policy_ids=[10]),
                                    admin=ADMIN)
    assert enforcer_mod._access_cache == {}
