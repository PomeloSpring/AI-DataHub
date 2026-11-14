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
"""
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

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
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


def test_admin_dialog_has_display_name_and_table_column():
    text = ADMIN_TSX.read_text(encoding="utf-8")
    # 添加 + 编辑两个弹窗都要有姓名输入框
    assert text.count("createForm.display_name") >= 1, "添加用户弹窗缺用户姓名输入"
    assert text.count("editForm.display_name") >= 1, "编辑弹窗缺用户姓名输入"
    assert "display_name: user.display_name" in text, "openEdit 必须回填现有姓名"
    assert '>姓名</th>' in text, "用户列表缺「姓名」列"
    assert "user.display_name || '-'" in text, "姓名列按可读名显示（空=占位）"


def test_login_page_offers_email_identifier():
    text = LOGIN_TSX.read_text(encoding="utf-8")
    assert "用户名 / 邮箱" in text and "请输入用户名或邮箱" in text
