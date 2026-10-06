"""角色「菜单与功能」权限码配置 —— 离线单测(不触碰真实数据库)。

覆盖:
  P1  GET /roles/perm-registry: menu_groups(菜单→功能) / standalone 分组
      + 每个权限点附 api_pattern/api_method 只读字段;
  P2  GET/PUT /roles/{role_id}/permissions 往返、全量替换、去重、空白剔除;
  P3  清空权限码后 fail-closed: GET /roles/current/permissions
      → unrestricted=False, menus 空(不解释为全量);
  P4  由权限码反查可访问菜单(含模块通配 module:*);
  P5  PUT 后 invalidate_perm_cache() 生效(中间件角色规则缓存清空)。

全部用假 execute_query/execute_write 注入, 不落真库。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

import pytest
from fastapi import HTTPException

roles_api = importlib.import_module("services.authservice.api.roles")
api_permission = importlib.import_module("services.shared.common.api_permission")

ADMIN = {"role": "admin", "user_id": 1}
VIEWER = {"role": "viewer", "user_id": 9}

# ── 假注册表/菜单/授权状态 ─────────────────────────────────────────────

REGISTRY = [
    {"perm_code": "ontology:save", "label": "保存本体", "module": "data", "module_label": "数据中台",
     "description": "保存本体模型", "menu_key": "data:ontology", "sort": 1,
     "api_pattern": "/api/ontology/*", "api_method": "POST"},
    {"perm_code": "report:view", "label": "查看报表", "module": "data", "module_label": "数据中台",
     "description": "", "menu_key": "data:reports", "sort": 2,
     "api_pattern": "/api/reports/*,/api/scheduled/*", "api_method": "GET"},
    # menu_key 指向不存在的菜单 → standalone
    {"perm_code": "orphan:code", "label": "孤儿权限", "module": "system", "module_label": "系统配置",
     "description": "", "menu_key": "ghost:menu", "sort": 3, "api_pattern": "", "api_method": ""},
    # menu_key 为空 → standalone
    {"perm_code": "legacy:code", "label": "无菜单权限", "module": "workspace", "module_label": "工作空间",
     "description": "", "menu_key": "", "sort": 4, "api_pattern": "/api/legacy", "api_method": "*"},
]

MENUS = [
    {"menu_key": "data:ontology", "label": "本体管理", "section": "数据资产", "module": "data",
     "sort": 1, "perm_code": "ontology:save"},
    {"menu_key": "data:reports", "label": "报表中心", "section": "数据资产", "module": "data",
     "sort": 2, "perm_code": "report:view"},
]


class _Store:
    def __init__(self):
        self.roles = {7: "viewer"}     # role_id → name
        self.perms: dict[int, set] = {7: set()}   # role_id → perm codes
        self.writes: list = []         # [(sql, params)]


@pytest.fixture
def store(monkeypatch):
    st = _Store()

    def fake_query(sql, params=None, fetchone=False):
        s = " ".join(sql.split())
        p = list(params or [])
        if "FROM adh_perm_registry" in s:
            rows = [dict(r) for r in REGISTRY]
        elif "FROM adh_menu_registry" in s and "SELECT menu_key, label" in s:
            rows = [{k: m[k] for k in ("menu_key", "label", "section", "module", "sort")} for m in MENUS]
        elif "FROM adh_menu_registry" in s and "perm_code" in s:
            rows = [dict(m) for m in MENUS]
        elif "FROM adh_menu_registry" in s:
            rows = [{"menu_key": m["menu_key"]} for m in MENUS]
        elif "FROM adh_roles WHERE name=" in s:
            rows = [{"id": rid} for rid, name in st.roles.items() if name == p[0]]
        elif "FROM adh_roles WHERE id=" in s:
            rid = p[0]
            rows = [{"id": rid}] if rid in st.roles else []
        elif "FROM adh_role_perms" in s:
            rows = [{"perm_code": pc} for pc in sorted(st.perms.get(p[0], set()))]
        else:
            rows = []
        if fetchone:
            return rows[0] if rows else None
        return rows

    def fake_write(sql, params=None):
        s = " ".join(sql.split())
        p = list(params or [])
        st.writes.append((s, p))
        if "DELETE FROM adh_role_perms" in s:
            st.perms[p[0]] = set()
        elif "INSERT IGNORE INTO adh_role_perms" in s:
            st.perms.setdefault(p[0], set()).add(p[1])

    monkeypatch.setattr(roles_api, "execute_query", fake_query)
    monkeypatch.setattr(roles_api, "execute_write", fake_write)
    return st


# ── P1 perm-registry 分组 + api 只读字段 ──────────────────────────────


def test_perm_registry_menu_groups_and_standalone(store):
    res = roles_api.list_perm_registry(user=ADMIN)
    groups = res["menu_groups"]
    # 菜单→功能分组, 按模块/排序组织
    assert [g["menu_key"] for g in groups] == ["data:ontology", "data:reports"]
    first = groups[0]
    assert first["label"] == "本体管理" and first["section"] == "数据资产" and first["module"] == "data"
    assert [p["perm_code"] for p in first["permissions"]] == ["ontology:save"]
    # menu_key 指向不存在菜单 / 为空 → standalone
    assert sorted(p["perm_code"] for p in res["standalone"]) == ["legacy:code", "orphan:code"]
    # 兼容保留的按模块分组
    assert {g["module"] for g in res["groups"]} == {"data", "system", "workspace"}


def test_perm_registry_api_fields_readonly_defaults(store):
    res = roles_api.list_perm_registry(user=ADMIN)
    items = {p["perm_code"]: p for g in res["menu_groups"] for p in g["permissions"]}
    items.update({p["perm_code"]: p for p in res["standalone"]})
    # 只读接口绑定字段随权限点返回(角色侧不可编辑)
    assert items["ontology:save"]["api_pattern"] == "/api/ontology/*"
    assert items["ontology:save"]["api_method"] == "POST"
    assert items["report:view"]["api_pattern"] == "/api/reports/*,/api/scheduled/*"
    # 缺省值: 空 pattern, method '*'
    assert items["orphan:code"]["api_pattern"] == ""
    assert items["orphan:code"]["api_method"] == "*"


# ── P2 GET/PUT 往返、全量替换 ─────────────────────────────────────────


def test_permissions_roundtrip_and_full_replace(store):
    req = roles_api.SetPermissionsRequest(
        permissions=["ontology:save", " report:view ", "", "  ", "ontology:save"])
    roles_api.set_role_permissions(7, req, admin=ADMIN)
    # 去重 + 空白剔除
    assert store.perms[7] == {"ontology:save", "report:view"}
    # 写序列: 先全量 DELETE 再逐条 INSERT(全量替换语义)
    assert store.writes[0][0].startswith("DELETE FROM adh_role_perms")
    assert all(s.startswith("INSERT IGNORE") for s, _ in store.writes[1:])

    got = roles_api.get_role_permissions(7, user=VIEWER)
    assert got == {"role_id": 7, "permissions": ["ontology:save", "report:view"]}

    # 再次保存 = 全量替换(不再包含 ontology:save)
    roles_api.set_role_permissions(7, roles_api.SetPermissionsRequest(permissions=["report:view"]),
                                  admin=ADMIN)
    assert store.perms[7] == {"report:view"}
    assert roles_api.get_role_permissions(7, user=VIEWER)["permissions"] == ["report:view"]


def test_set_permissions_unknown_role_404(store):
    with pytest.raises(HTTPException) as exc:
        roles_api.set_role_permissions(99, roles_api.SetPermissionsRequest(permissions=["x:y"]),
                                       admin=ADMIN)
    assert exc.value.status_code == 404


# ── P3 清空后 fail-closed ─────────────────────────────────────────────


def test_clear_permissions_fail_closed(store):
    store.perms[7] = {"ontology:save"}
    roles_api.set_role_permissions(7, roles_api.SetPermissionsRequest(permissions=[]), admin=ADMIN)
    assert store.perms[7] == set()
    res = roles_api.get_current_user_permissions(user=VIEWER)
    # 未配置任何功能 = 不可用(fail-closed), 不解释为全量
    assert res == {"permissions": [], "menus": [], "unrestricted": False}


def test_role_without_perms_fail_closed(store):
    res = roles_api.get_current_user_permissions(user=VIEWER)
    assert res["unrestricted"] is False
    assert res["menus"] == []


def test_unknown_role_fail_closed(store):
    res = roles_api.get_current_user_permissions(user={"role": "ghost", "user_id": 9})
    assert res == {"permissions": [], "menus": [], "unrestricted": False}


# ── P4 由权限码反查可访问菜单 ─────────────────────────────────────────


def test_menus_derived_from_perm_codes(store):
    store.perms[7] = {"report:view"}
    res = roles_api.get_current_user_permissions(user=VIEWER)
    assert res["permissions"] == ["report:view"]
    assert res["menus"] == ["data:reports"]   # 本体菜单未授权 → 不可见
    assert res["unrestricted"] is False


def test_module_wildcard_unlocks_menu(store):
    store.perms[7] = {"ontology:*"}
    res = roles_api.get_current_user_permissions(user=VIEWER)
    assert res["menus"] == ["data:ontology"]


def test_admin_unrestricted_sees_all_menus(store):
    res = roles_api.get_current_user_permissions(user=ADMIN)
    assert res == {"permissions": ["*"], "menus": ["data:ontology", "data:reports"],
                   "unrestricted": True}


# ── P5 PUT 后中间件缓存失效 ───────────────────────────────────────────


def test_invalidate_perm_cache_exists_and_clears_on_put(store, monkeypatch):
    assert callable(getattr(api_permission, "invalidate_perm_cache", None))
    api_permission._cache["viewer"] = (0.0, [{"pattern": "/api/old/*", "method": "GET", "allowed": True}])

    roles_api.set_role_permissions(7, roles_api.SetPermissionsRequest(permissions=["report:view"]),
                                  admin=ADMIN)
    # PUT 后缓存被清空, 下一次判定重新从注册表取规则(旧规则不再放行)
    assert api_permission._cache == {}

    import services.shared.common.db as db_mod

    def fake_db_query(sql, params=None, fetchone=False):
        s = " ".join(sql.split())
        if "FROM adh_roles WHERE name" in s:
            return [{"id": 7}]
        if "FROM adh_role_perms" in s:
            return [{"perm_code": pc} for pc in sorted(store.perms[7])]
        if "FROM adh_perm_registry" in s:
            return [dict(r) for r in REGISTRY]
        return []

    monkeypatch.setattr(db_mod, "execute_query", fake_db_query)
    assert api_permission.check_api_permission("viewer", "GET", "/api/reports/1") is True
    assert api_permission.check_api_permission("viewer", "GET", "/api/old/x") is False
