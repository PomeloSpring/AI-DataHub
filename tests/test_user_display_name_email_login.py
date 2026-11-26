"""用户姓名（display_name）与邮箱登录（email_hash 指纹）守卫。

背景：管理员添加/编辑用户时填不了「用户姓名」；登录只认用户名，填了邮箱的用户
无法用邮箱登录。且邮箱是 AES-GCM 随机 IV 密文 —— 同一邮箱两次密文不同，历史上
用密文等值做唯一性检查从未命中过（缺陷），邮箱等值查找也不可行。

口径：
  * display_name：非必填、展示用（列表「姓名」列 / 登录响应），不参与登录判定；
  * email_hash：HMAC-SHA256 盲索引（backend/common/crypto.fingerprint，归一化邮箱），
    等值可检索、不含明文；原值仍只存密文；
  * 登录标识含 ``@`` → 按 email_hash 命中，否则按用户名；请求字段仍叫 ``username``；
  * 邮箱唯一性（create/update）一律按 email_hash 判；
  * 存量 email_hash 由 _backfill_email_hashes 在邮箱登录时幂等回填；解不开的密文
    跳过并留 WARNING（不打断用户名登录主链路，但留痕可诊断）。

守卫内容：
  F1  crypto：fingerprint 确定性/区分性 + normalize_email 归一口径；
  F2  create_user：display_name/email_hash 落库，唯一性按指纹（且不再用密文等值）；
  F3  update_user：display_name 可改；email 变更重算指纹、清空邮箱清指纹；
  F4  邮箱登录：归一化指纹命中、返回 display_name；用户名登录不走指纹；邮箱不存在→None；
  F5  存量回填：解密成功算指纹落库、解密失败跳过留痕且不炸；
  F6  迁移幂等（information_schema 门 + 两列一索引齐全）；
  F7  API/前端契约：请求模型带 display_name；Admin 弹窗有姓名输入框与姓名列；
      登录页标注「用户名 / 邮箱」；源码级禁止密文等值唯一性检查复发。
  F8  角色绑定真值源：create_user/update_user 改角色必须同事务写 adh_user_roles
      （历史缺陷：只写 adh_users.user_role 显示列 →「用户分配」弹窗读真值源看不到
      该用户，还得再分配一次；且新用户零权限码被 API 门控 403）；角色不存在 fail-loud
      透出原因，不洗成「创建失败/更新失败」。断言打在 auth_service（真正挂 /api/users
      的实现）上 —— 修在无人调用的 backend/common/auth.py 上是假绿（历史教训）。
"""
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.common import crypto  # noqa: E402
from backend.modules.auth.services import auth_service  # noqa: E402

MIGRATION_SQL = ROOT / "docker" / "mysql" / "user_profile_email_login_migration.sql"
USERS_API = ROOT / "backend" / "modules" / "auth" / "api" / "users.py"
AUTH_API = ROOT / "backend" / "modules" / "auth" / "api" / "auth.py"
ADMIN_TSX = ROOT / "frontend" / "src" / "pages" / "Admin.tsx"
LOGIN_TSX = ROOT / "frontend" / "src" / "pages" / "Login.tsx"
UI_I18N = ROOT / "frontend" / "src" / "i18n" / "locales" / "zh-CN" / "ui.json"
AUTH_STORE = ROOT / "frontend" / "src" / "stores" / "authStore.ts"
SERVICE_PY = ROOT / "backend" / "modules" / "auth" / "services" / "auth_service.py"

EMAIL = "Xiaolong.Kang@Allied-Star.com"


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch):
    """指纹/加密密钥固定，保证断言可复现（与生产真密钥解耦）。"""
    monkeypatch.setattr(crypto, "ADH_SECRET_KEY", "test-secret-for-fingerprint")


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
        self.began = False
        self.committed = False
        self.rolled_back = False

    def begin(self):
        # 元库 autocommit=True，服务侧靠显式 BEGIN 才有真事务（半态防护）
        self.began = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 对齐 backend.common.db.DBConnection.__exit__ 的分派（半态防护靠它兑现）
        if exc_type:
            self.rollback()
        else:
            self.commit()
        return False


def _user_row(**over):
    row = {
        "id": 7, "username": "kangxiaolong", "password_hash": "bcrypt-hash",
        "user_role": "analyst", "status": "active", "login_attempts": 0,
        "locked_until": None, "display_name": "康小龙",
    }
    row.update(over)
    return row


# ── F1 crypto 指纹 ──────────────────────────────────────────────────────────
def test_fingerprint_deterministic_and_discriminating():
    a1 = crypto.fingerprint("user@example.com")
    a2 = crypto.fingerprint("user@example.com")
    assert a1 == a2 and len(a1) == 64, "指纹必须确定性且 SHA-256 hex"
    assert crypto.fingerprint("other@example.com") != a1
    # 不含明文、可安全入库（CHAR(64)）
    assert EMAIL[:8].lower() not in crypto.fingerprint(EMAIL)


def test_normalize_email_and_fingerprint_alignment():
    fp = crypto.fingerprint(crypto.normalize_email(EMAIL))
    # 同一邮箱不同写法：归一化后指纹一致；未归一化的字面指纹不参与查找（调用方必须先归一）
    assert fp == crypto.fingerprint(crypto.normalize_email("  xiaolong.kang@allied-star.com  "))
    assert fp != crypto.fingerprint("  xiaolong.kang@allied-star.com  ")
    assert crypto.normalize_email("") == "" and crypto.fingerprint("") == ""


# ── F2 create_user ──────────────────────────────────────────────────────────
def test_create_user_writes_display_name_and_email_hash(monkeypatch):
    conn = _Conn([
        (("FROM adh_users WHERE username = %s",), []),      # 用户名未占用
        (("WHERE email_hash = %s AND email_hash != ''",), []),  # 邮箱未占用
        (("FROM adh_roles WHERE name = %s",), [{"id": 31}]),    # 角色解析（绑定真值源）
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg, uid = auth_service.create_user(
        "kangxiaolong", "Aa123456!", "analyst", EMAIL, "13800000000", display_name="康小龙")

    assert ok, msg
    insert_sql, params = next(
        (s, p) for s, p in conn.cur.calls if s.startswith("INSERT INTO adh_users"))
    col_sql = _norm(re.search(r"INSERT INTO adh_users\s+\((.*?)\)", insert_sql, re.S).group(1))
    assert "display_name" in col_sql and "email_hash" in col_sql
    assert params[2] == "康小龙", "display_name 必须落库"
    assert params[5] == crypto.fingerprint(crypto.normalize_email(EMAIL)), "指纹按归一化邮箱算"


def test_create_user_email_uniqueness_by_fingerprint_not_ciphertext(monkeypatch):
    conn = _Conn([
        (("FROM adh_users WHERE username = %s",), []),
        (("WHERE email_hash = %s AND email_hash != ''",), [{"id": 2}]),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg, uid = auth_service.create_user("other", "Aa123456!", email=EMAIL)
    assert not ok and msg == "邮箱已被使用" and uid == 0
    dup_sql, dup_params = next(
        (s, p) for s, p in conn.cur.calls if "email_hash = %s AND email_hash != ''" in s)
    assert dup_params == [crypto.fingerprint(crypto.normalize_email(EMAIL))]


def test_create_user_no_ciphertext_equality_check_left():
    """源码级：随机 IV 密文等值检查是永不命中的假护栏，不得复发。"""
    text = SERVICE_PY.read_text(encoding="utf-8")
    assert not re.search(r"WHERE email = %s", text), "密文等值检查（永不命中）不得回流"


# ── F8 角色绑定真值源（添加用户选了角色 = 创建即已分配）────────────────────
def _role_lookup_response(role_id=31):
    return (("FROM adh_roles WHERE name = %s",), [{"id": role_id}])


def test_create_user_writes_user_role_binding_truth_source(monkeypatch):
    """create_user 必须把角色写进真值源 adh_user_roles（同事务）。

    否则「用户分配」弹窗（读 adh_user_roles）看不到新用户，管理员还得再去
    分配一次 —— 正是本用例要锁死的交互缺陷。
    """
    conn = _Conn([
        (("FROM adh_users WHERE username = %s",), []),
        (("WHERE email_hash = %s AND email_hash != ''",), []),
        _role_lookup_response(31),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg, uid = auth_service.create_user("newbie", "Aa123456!", role="analyst")
    assert ok, msg
    assert conn.began and conn.committed, (
        "必须显式 BEGIN 后提交（元库 autocommit=True，不开事务则 rollback 空转）")

    del_sql, del_params = next(
        (s, p) for s, p in conn.cur.calls if "DELETE FROM adh_user_roles" in s)
    assert del_params == [uid], "先清该用户旧绑定（幂等）"
    ins_sql, ins_params = next(
        (s, p) for s, p in conn.cur.calls if "INSERT INTO adh_user_roles" in s)
    assert ins_params[1] == uid and ins_params[2] == 31, "绑定行落真值源且带解析出的 role_id"
    # 名缓存/绑定缓存同事务同步（登录 JWT 与角色页显示一致）
    upd_sql, upd_params = next(
        (s, p) for s, p in conn.cur.calls
        if s.startswith("UPDATE adh_users SET role_id = %s"))
    assert "user_role = %s" in upd_sql and "analyst" in upd_params


def test_create_user_unknown_role_fails_loud_not_generic(monkeypatch):
    """角色名不存在：错因原样透出（HTTP 400 detail → 前端 toast），不得洗成「创建失败」。"""
    conn = _Conn([
        (("FROM adh_users WHERE username = %s",), []),
        (("WHERE email_hash = %s AND email_hash != ''",), []),
        (("FROM adh_roles WHERE name = %s",), []),   # 角色不存在
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg, uid = auth_service.create_user("ghost", "Aa123456!", role="不存在的角色")
    assert not ok and uid == 0
    assert "角色不存在" in msg, f"错因被通用文案吞掉了：{msg}"
    assert msg != "创建失败"
    # 半态防护：失败必须回滚（用户行不得残留，否则下次重试报「用户名已存在」）
    assert conn.began and conn.rolled_back, "失败未回滚 = 留下半态用户行"
    assert not conn.committed


def test_update_user_role_change_writes_truth_source(monkeypatch):
    """改角色同样走绑定唯一写入口，不得只 UPDATE user_role 显示列。"""
    conn = _Conn([
        (("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}]),
        _role_lookup_response(33),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.update_user(7, role="viewer")
    assert ok, msg
    assert any("INSERT INTO adh_user_roles" in s for s, _ in conn.cur.calls), (
        "改角色必须写真值源 adh_user_roles")
    # 仅改角色时不发空 UPDATE（其余字段未变）
    assert not any(
        s.startswith("UPDATE adh_users SET updated_at") for s, _ in conn.cur.calls)


def test_update_user_unknown_role_fails_loud_not_generic(monkeypatch):
    conn = _Conn([
        (("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}]),
        (("FROM adh_roles WHERE name = %s",), []),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.update_user(7, role="不存在的角色")
    assert not ok and "角色不存在" in msg and msg != "更新失败"


def test_delete_user_clears_role_binding_truth_source(monkeypatch):
    """删用户必须同事务清 adh_user_roles，否则留悬空绑定（死数据永存）。"""
    conn = _Conn([
        (("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}]),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.delete_user(7)
    assert ok, msg
    del_binding = next(
        (s, p) for s, p in conn.cur.calls if "DELETE FROM adh_user_roles" in s)
    del_user = next(
        (s, p) for s, p in conn.cur.calls if s == "DELETE FROM adh_users WHERE id = %s")
    assert del_binding[1] == [7] and del_user[1] == [7]
    # 先清绑定再删用户（同一事务，顺序不颠倒）
    assert conn.cur.calls.index(del_binding) < conn.cur.calls.index(del_user)


# ── F3 update_user ──────────────────────────────────────────────────────────
def test_update_user_changes_display_name(monkeypatch):
    conn = _Conn([(("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}])])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.update_user(7, display_name="  康小龙  ")
    assert ok, msg
    upd_sql, params = next(
        (s, p) for s, p in conn.cur.calls if s.startswith("UPDATE adh_users SET"))
    assert "display_name = %s" in upd_sql
    assert params[params.index("康小龙") if "康小龙" in params else -1] == "康小龙", "姓名去首尾空白落库"


def test_update_user_email_change_recomputes_fingerprint(monkeypatch):
    conn = _Conn([(("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}])])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.update_user(7, email="new@example.com")
    assert ok, msg
    upd_sql, params = next(
        (s, p) for s, p in conn.cur.calls if s.startswith("UPDATE adh_users SET"))
    assert "email_hash = %s" in upd_sql
    assert crypto.fingerprint("new@example.com") in params, "邮箱变更指纹同步重算"


def test_update_user_clearing_email_clears_fingerprint(monkeypatch):
    conn = _Conn([(("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}])])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.update_user(7, email="")
    assert ok, msg
    upd_sql, params = next(
        (s, p) for s, p in conn.cur.calls if s.startswith("UPDATE adh_users SET"))
    assert "email_hash = %s" in upd_sql
    assert params[params.index("") if "" in params else -1] == "" or "" in params, "清空邮箱指纹一并清空"


def test_update_user_email_uniqueness_by_fingerprint(monkeypatch):
    conn = _Conn([
        (("FROM adh_users WHERE id = %s",), [{"id": 7, "username": "kangxiaolong"}]),
        (("WHERE email_hash = %s AND id != %s AND email_hash != ''",), [{"id": 9}]),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    ok, msg = auth_service.update_user(7, email="taken@example.com")
    assert not ok and msg == "邮箱已被使用"


# ── F4 登录 ─────────────────────────────────────────────────────────────────
def test_login_by_email_hits_fingerprint_and_returns_display_name(monkeypatch):
    conn = _Conn([
        (("email_hash IS NULL OR email_hash = ''",), []),   # 回填：无待回填行
        (("WHERE email_hash = %s ORDER BY id",), [_user_row()]),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)
    monkeypatch.setattr(auth_service, "verify_password", lambda p, h: True)

    result = auth_service.login(EMAIL, "Aa123456!")

    assert result and result["user"]["display_name"] == "康小龙"
    assert result["user"]["username"] == "kangxiaolong"
    lookup_sql, lookup_params = next(
        (s, p) for s, p in conn.cur.calls if "WHERE email_hash = %s ORDER BY id" in s)
    assert lookup_params == [crypto.fingerprint(crypto.normalize_email(EMAIL))]
    # 审计主体是账号本身，不是邮箱
    audit = next(p for s, p in conn.cur.calls if "INSERT INTO adh_audit_logs" in s)
    assert audit[1] == "kangxiaolong"


def test_login_by_email_is_whitespace_case_insensitive(monkeypatch):
    conn = _Conn([
        (("email_hash IS NULL OR email_hash = ''",), []),
        (("WHERE email_hash = %s ORDER BY id",), [_user_row()]),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)
    monkeypatch.setattr(auth_service, "verify_password", lambda p, h: True)

    result = auth_service.login(f"  {EMAIL.lower()}  ", "Aa123456!")
    assert result, "归一化后同一邮箱必须命中同一行"


def test_login_by_username_does_not_touch_email_hash(monkeypatch):
    conn = _Conn([(("WHERE username = %s",), [_user_row()])])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)
    monkeypatch.setattr(auth_service, "verify_password", lambda p, h: True)

    result = auth_service.login("kangxiaolong", "Aa123456!")
    assert result and result["user"]["display_name"] == "康小龙"
    assert all("email_hash" not in s for s, _ in conn.cur.calls if "SELECT" in s), \
        "用户名登录不得触发邮箱指纹查询（回填只属于邮箱路径）"


def test_login_unknown_email_returns_none(monkeypatch):
    conn = _Conn([
        (("email_hash IS NULL OR email_hash = ''",), []),
        (("WHERE email_hash = %s ORDER BY id",), []),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)
    assert auth_service.login("nobody@example.com", "Aa123456!") is None


# ── F5 存量回填 ─────────────────────────────────────────────────────────────
def test_backfill_computes_fingerprint_for_existing_rows(monkeypatch):
    enc = crypto.encrypt_field(EMAIL)
    conn = _Conn([
        (("AND (email_hash IS NULL OR email_hash = '')",),
         [{"id": 5, "email": enc}, {"id": 6, "email": ""}]),  # 空邮箱行不回填
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    auth_service._backfill_email_hashes()

    updates = [(s, p) for s, p in conn.cur.calls if s.startswith("UPDATE adh_users SET email_hash")]
    assert len(updates) == 1, "只回填有邮箱的行"
    assert updates[0][1] == [crypto.fingerprint(crypto.normalize_email(EMAIL)), 5]


def test_backfill_skips_undecryptable_rows_without_breaking(monkeypatch):
    enc = crypto.encrypt_field(EMAIL)
    # 损坏密文：base64 形状合法（is_encrypted=True）但 GCM 解密失败（密钥不匹配/密文损坏）
    corrupted = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    conn = _Conn([
        (("AND (email_hash IS NULL OR email_hash = '')",),
         [{"id": 5, "email": enc}, {"id": 6, "email": corrupted}]),
    ])
    monkeypatch.setattr(auth_service, "DBConnection", lambda: conn)

    auth_service._backfill_email_hashes()  # 不得抛异常

    updates = [(s, p) for s, p in conn.cur.calls if s.startswith("UPDATE adh_users SET email_hash")]
    assert len(updates) == 1 and updates[0][1][1] == 5, "解不开的行跳过，解得开的照常回填"


# ── F6 迁移 ─────────────────────────────────────────────────────────────────
def test_migration_is_idempotent_and_complete():
    text = MIGRATION_SQL.read_text(encoding="utf-8")
    assert text.count("information_schema") >= 3, "列/索引必须先查 information_schema（幂等）"
    assert "PREPARE s1 FROM @ddl" in text and "idx_email_hash" in text
    assert "ADD COLUMN display_name VARCHAR(64)" in text
    assert "ADD COLUMN email_hash CHAR(64)" in text
    assert "ADD INDEX idx_email_hash (email_hash)" in text
    assert "CONCAT(" in text, "MySQL 默认 || 是逻辑 OR，必须用 CONCAT 拼接 DDL"


# ── F7 API / 前端契约 ───────────────────────────────────────────────────────
def test_api_models_accept_display_name():
    sys.path.insert(0, str(ROOT / "backend"))
    from backend.modules.auth.api.users import CreateUserRequest, UpdateUserRequest
    assert CreateUserRequest(username="u", password="Aa123456!", display_name="康小龙").display_name == "康小龙"
    assert UpdateUserRequest(display_name="康小龙").display_name == "康小龙"
    assert UpdateUserRequest().display_name is None, "不传 = 不改该项"


def test_login_request_keeps_username_field():
    text = AUTH_API.read_text(encoding="utf-8")
    assert "username: str" in text, "请求字段仍叫 username（前端不改请求体，语义扩为账号标识）"
    assert "邮箱" in text, "登录请求需注明邮箱标识语义"


def _i18n_text(key: str) -> str:
    """按 key 取中文文案：本仓前端一律走 i18n（`t("k_xxx")`），源码里不写死中文标签。"""
    return json.loads(UI_I18N.read_text(encoding="utf-8"))[key]


def test_admin_dialog_has_display_name_and_table_column():
    text = ADMIN_TSX.read_text(encoding="utf-8")
    # 添加 + 编辑两个弹窗都要有姓名输入框
    assert text.count("createForm.display_name") >= 1, "添加用户弹窗缺用户姓名输入"
    assert text.count("editForm.display_name") >= 1, "编辑弹窗缺用户姓名输入"
    assert "display_name: user.display_name" in text, "openEdit 必须回填现有姓名"
    # 表头走 i18n key（k_be4c2616b1 = 「姓名」），断言挂在 key + 文案两侧
    assert 't("k_be4c2616b1")' in text, "用户列表缺「姓名」列"
    assert _i18n_text("k_be4c2616b1") == "姓名"
    assert "user.display_name || '-'" in text, "姓名列按可读名显示（空=占位）"


def test_login_page_offers_email_identifier():
    text = LOGIN_TSX.read_text(encoding="utf-8")
    # 文案走 i18n：k_3f27a643d4 = 「用户名 / 邮箱」、k_0600e9654d = 「请输入用户名或邮箱」
    assert 't("k_3f27a643d4")' in text and 't("k_0600e9654d")' in text
    assert _i18n_text("k_3f27a643d4") == "用户名 / 邮箱"
    assert _i18n_text("k_0600e9654d") == "请输入用户名或邮箱"
