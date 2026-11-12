"""个人设置「角色」显示口径守卫（角色可读名由服务端按真值源下发）。

背景（缺陷）：个人设置页的角色一格渲染成空白徽标。根因是字段名错配 ——
`/api/users/me` 返回的账号列叫 `user_role`，而 `Profile.tsx` 读的是 `profile.role`，
取不到值就渲染成空。顺带暴露两个口径问题：

  * 角色是资源（`adh_roles`），显示必须用 `display_name`（ui-resource-display §1）。
    前端硬编码的三档文案接不住自定义角色（如 name=`Operations Manager` /
    display_name=`运营管理`），会把内部英文名当名称显示；
  * 用户→角色的唯一真值源是 `adh_user_roles`（`adh_users.user_role` 只是登录/JWT 的
    显示缓存，见 `core/role_service.write_user_role_binding`）。显示按真值源取，
    才是「这个账号实际生效的角色」。

守卫内容：
  R1  服务层 `get_user_profile` 按真值源解析 role/role_name/roles（行为用例）；
  R2  无生效绑定 → 两字段为空（不回退缓存列把配置缺口伪装成正常），页面如实标注；
  R3  角色表查询失败必须上抛，不被吞成「没有角色」；
  R4  `/users/me` 读写两侧都走 `get_user_profile`（只改 GET 会让保存后角色又变空白）；
  R5  前端消费 `role_name`，不再读不存在的 `profile.role` 当显示值；
  R6  身份 store 与首页同源消费 `role_name`（自定义角色不露内部英文名）。
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.modules.auth.services import auth_service  # noqa: E402

USERS_API = ROOT / "backend" / "modules" / "auth" / "api" / "users.py"
PROFILE_TSX = ROOT / "frontend" / "src" / "pages" / "Profile.tsx"
AUTH_STORE = ROOT / "frontend" / "src" / "stores" / "authStore.ts"
HOME_TSX = ROOT / "frontend" / "src" / "pages" / "Home.tsx"


def _norm(sql):
    return " ".join(str(sql).split())


class _Cur:
    """按 SQL 形状回放预设行：responses = [(需同时命中的子串元组, rows)]，首匹配生效。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.rows = []

    def execute(self, sql, params=None):
        s = _norm(sql)
        self.calls.append((s, list(params or [])))
        for subs, rows in self.responses:
            need = subs if isinstance(subs, (tuple, list)) else (subs,)
            if all(x in s for x in need):
                self.rows = rows
                return
        self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, responses):
        self.cur = _Cur(responses)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _conn(role_rows, user_rows=None):
    return _Conn([
        (("FROM adh_users WHERE id",), user_rows if user_rows is not None else [{
            "id": 1, "username": "lisi", "email": "", "phone": "", "avatar": "",
            "user_role": "Operations Manager", "status": "active", "last_login": None,
            "login_attempts": 0, "locked_until": None,
            "created_at": "2026-10-09 13:38:33", "updated_at": "2026-10-09 13:38:33"}]),
        (("FROM adh_user_roles ur", "JOIN adh_roles r"), role_rows),
    ])


# ── R1/R2 服务层行为 ─────────────────────────────────────────────────────────
def test_profile_role_resolved_from_truth_source(monkeypatch):
    conn = _conn([{"name": "Operations Manager", "display_name": "运营管理"}])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    row = auth_service.get_user_profile(1)

    assert row["role"] == "Operations Manager", "role 供既有 role==='admin' 判定消费"
    assert row["role_name"] == "运营管理", "显示名必须取 adh_roles.display_name"
    assert row["roles"] == [{"name": "Operations Manager", "display_name": "运营管理"}]
    # 真值源口径：绑定行来自 adh_user_roles ⋈ adh_roles，且只取生效角色
    binding_sql = next(s for s, _ in conn.cur.calls if "FROM adh_user_roles ur" in s)
    assert "JOIN adh_roles r ON r.id = ur.role_id" in binding_sql
    assert "r.is_active = 1" in binding_sql, "停用角色不得继续按生效角色显示"


def test_profile_role_name_falls_back_to_name_when_display_missing(monkeypatch):
    """display_name 空串 → 回 name（不给空白显示，但也不编造文案）。"""
    monkeypatch.setattr(auth_service, "DBConnection",
                        lambda: _conn([{"name": "auditor", "display_name": None}]))
    assert auth_service.get_user_profile(1)["role_name"] == "auditor"


def test_profile_without_binding_is_explicitly_empty(monkeypatch):
    """无生效绑定 → role/role_name 为空串，由页面显示「未绑定角色」。

    不回退 `adh_users.user_role` 缓存列：列有值真值源无行 = 该账号零权限码，
    把缓存值当生效角色显示就是拿空承诺掩盖配置缺口（no-silent-degradation §1）。
    """
    monkeypatch.setattr(auth_service, "DBConnection", lambda: _conn([]))

    row = auth_service.get_user_profile(1)

    assert row["role"] == "" and row["role_name"] == "" and row["roles"] == []
    assert row["user_role"] == "Operations Manager", "缓存列原样保留供排查，不参与显示裁决"


def test_missing_user_returns_none(monkeypatch):
    monkeypatch.setattr(auth_service, "DBConnection", lambda: _conn([], user_rows=[]))
    assert auth_service.get_user_profile(1) is None


# ── R3 取数失败不得伪装成「没有角色」──────────────────────────────────────────
def test_role_query_failure_propagates(monkeypatch):
    class _Boom:
        def cursor(self):
            raise RuntimeError("connection reset")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seq = iter([_Conn([(("FROM adh_users WHERE id",), [{"id": 1, "username": "admin",
                                                       "email": "", "phone": "",
                                                       "user_role": "admin",
                                                       "status": "active"}])]), _Boom()])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: next(seq))

    with pytest.raises(RuntimeError):
        auth_service.get_user_profile(1)


# ── R4 路由接线（读写两侧同源）───────────────────────────────────────────────
def test_me_routes_use_profile_assembler():
    api = USERS_API.read_text(encoding="utf-8")
    me_block = api.split('@router.get("/me")', 1)[1].split('@router.put("/me")', 1)[0]
    put_block = api.split('@router.put("/me")', 1)[1].split('@router.put("/me/password")', 1)[0]
    assert "get_user_profile(" in me_block, "GET /users/me 仍读裸账号列 → 角色显示缺字段"
    assert "get_user_profile(" in put_block, "PUT /users/me 保存后回读少了角色 → 角色又变空白"
    assert "get_user_by_id" not in me_block + put_block


# ── R5/R6 前端消费口径 ───────────────────────────────────────────────────────
def test_profile_page_consumes_role_name():
    tsx = PROFILE_TSX.read_text(encoding="utf-8")
    assert "role_name" in tsx, "个人设置未消费服务端下发的角色可读名"
    assert "ROLE_LABELS[profile?.role]" not in tsx, "仍在读 /users/me 不存在的 profile.role"
    assert "未绑定角色" in tsx, "无生效角色必须可见标注，不得渲染空白徽标"


def test_identity_store_and_home_consume_role_name():
    """首页问候语的角色徽标同源：自定义角色不该露内部英文名。"""
    assert "role_name" in AUTH_STORE.read_text(encoding="utf-8")
    home = HOME_TSX.read_text(encoding="utf-8")
    assert "user?.role_name" in home
