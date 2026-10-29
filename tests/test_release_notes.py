"""版本公告 + 新手引导状态 API 的离线回归：不连库、不连 HTTP 服务。

覆盖口径：
- 公告读域只回 ``is_published=1``（草稿不得出现在普通用户面板），且 ``sections``
  出参归一成数组（DB JSON 列可能回字符串，前端 map 会按字符炸开）；
- 引导状态写入**只认服务端身份**，请求体里塞 user_id 不生效；key 走白名单，
  脏 key 会把状态表写成垃圾场并让"已处理"判定永远命中；
- 管理域（内容发布）对非 admin 一律 403，个人域对无权限码角色保持可读 ——
  这是 ``_SELF_ROUTE_PATTERNS`` 放行的边界，放行过头就等于开了个公共写入口。
"""
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.common import api_permission as ap
from backend.common import auth
from backend.modules.platform.api import release_notes as rn


def _row(**over):
    base = {
        "id": 1, "version": "1.1.0", "title": "新手引导与版本公告", "summary": "",
        # 故意给字符串形态: pymysql 对 JSON 列的返回类型依版本而异
        "sections": '[{"kind":"feature","items":["新增新手引导"]}]',
        "guide_key": "welcome", "is_major": 1, "is_published": 1,
        "published_at": None, "updated_by": "admin", "updated_at": None,
    }
    base.update(over)
    return base


# ── 公告读域 ────────────────────────────────────────────────────────


def test_list_only_returns_published(monkeypatch):
    sqls = []
    monkeypatch.setattr(rn, "execute_query",
                        lambda sql, params=None, **kw: sqls.append(sql) or [_row()])
    out = rn.list_release_notes(user={"user_id": 5, "role": "analyst"})
    assert "is_published = 1" in sqls[0]
    assert out["latest_version"] == "1.1.0"
    item = out["items"][0]
    # 出参形状: 前端直接 map，不需要再 JSON.parse
    assert isinstance(item["sections"], list) and item["sections"][0]["items"] == ["新增新手引导"]
    assert item["sections"][0]["kind"] == "feature"
    # 个人域响应不带管理字段(发布人/草稿标记不外泄)
    assert "updated_by" not in item and "is_published" not in item


def test_get_state_scopes_by_server_identity(monkeypatch):
    seen = []
    monkeypatch.setattr(rn, "execute_query",
                        lambda sql, params=None, **kw: seen.append(params) or [])
    rn.get_guide_state(user={"user_id": 12, "role": "viewer"})
    assert seen == [(12,)]


# ── sections 归一 ─────────────────────────────────────────────────────


def test_sections_unknown_kind_kept_as_other():
    """未知类型不静默丢: 归到 other 并保留 raw_kind 供前端标注原类型。"""
    out = rn._normalize_sections([{"kind": "security", "items": ["行级策略调整"]}])
    assert out == [{"kind": "other", "raw_kind": "security", "items": ["行级策略调整"]}]


def test_sections_bad_json_and_blank_items_dropped():
    assert rn._normalize_sections("not-json") == []
    assert rn._normalize_sections([{"kind": "fix", "items": ["   "]}]) == []
    assert rn._normalize_sections(["oops"]) == []


# ── 状态写入 ──────────────────────────────────────────────────────────


def test_put_state_uses_server_uid_and_upserts(monkeypatch):
    writes = []
    monkeypatch.setattr(rn, "execute_write",
                        lambda sql, params=None: writes.append((sql, params)) or 1)
    req = rn.GuideStateUpdate(keys=["guide:home", "note:1.1.0"], status="completed")
    # body 里伪造他人身份: 模型无该字段, 不参与写入
    req_dict = req.model_dump()
    assert "user_id" not in req_dict
    out = rn.put_guide_state(req, user={"user_id": 42, "role": "analyst"})
    assert out["saved"] == 2
    assert {w[1][0] for w in writes} == {42}
    assert {w[1][1] for w in writes} == {"guide:home", "note:1.1.0"}
    assert "ON DUPLICATE KEY UPDATE" in writes[0][0]


def test_put_state_allows_special_guide_ids(monkeypatch):
    """guide:all(全局静音) / guide:welcome(欢迎总览) 必须可写, 否则跳过语义落不了地。"""
    monkeypatch.setattr(rn, "execute_write", lambda sql, params=None: 1)
    for key in ("guide:all", "guide:welcome", "guide:workspace", "note:1.1.0", "note:1.2.0-rc.1"):
        assert rn.put_guide_state(
            rn.GuideStateUpdate(key=key, status="skipped"), user={"user_id": 7})["success"]


@pytest.mark.parametrize("bad_key", [
    "admin:all", "guide:", "note:", "notes:1.1.0", "guide:ALL",
    "guide:home; DROP TABLE", "note:abc", "", "GUIDE:home",
])
def test_put_state_rejects_dirty_keys(monkeypatch, bad_key):
    """白名单外的 key 一律拒: 脏 key 会让自动弹出判定永远命中"已处理"。"""
    called = []
    monkeypatch.setattr(rn, "execute_write", lambda sql, params=None: called.append(sql) or 1)
    with pytest.raises(HTTPException) as ei:
        rn.put_guide_state(rn.GuideStateUpdate(key=bad_key, status="read"), user={"user_id": 7})
    assert ei.value.status_code == 400
    assert not called


def test_put_state_rejects_unknown_status_and_empty_keys(monkeypatch):
    monkeypatch.setattr(rn, "execute_write", lambda sql, params=None: 1)
    with pytest.raises(HTTPException) as ei:
        rn.put_guide_state(rn.GuideStateUpdate(key="guide:home", status="done"), user={"user_id": 7})
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        rn.put_guide_state(rn.GuideStateUpdate(keys=[], status="read"), user={"user_id": 7})
    assert ei.value.status_code == 400


@pytest.mark.parametrize("user", [None, {}, {"user_id": 0}, {"user_id": "abc"}])
def test_put_state_fails_closed_without_identity(user):
    """无可信 uid 一律 401, 不回退成"按 0 号用户/按所有人"写。"""
    with pytest.raises(HTTPException) as ei:
        rn.put_guide_state(rn.GuideStateUpdate(key="guide:home", status="read"), user=user)
    assert ei.value.status_code == 401


# ── 门控边界(个人域自助 vs 管理域 admin) ──────────────────────────────


def test_self_route_pattern_covers_personal_domain_only():
    for p in ("/api/release-notes", "/api/release-notes/", "/api/release-notes/state"):
        assert ap._SELF_ROUTE_PATTERNS.fullmatch(p.rstrip("/")), p
    for p in ("/api/admin/release-notes", "/api/admin/release-notes/3"):
        assert not ap._SELF_ROUTE_PATTERNS.fullmatch(p.rstrip("/")), p


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(rn.router, prefix="/api/release-notes")
    app.include_router(rn.admin_router, prefix="/api/admin/release-notes")
    return app


def test_admin_domain_denies_non_admin(monkeypatch):
    async def analyst():
        return {"user_id": 3, "username": "bob", "role": "analyst"}

    app = _app()
    app.dependency_overrides[auth.get_current_user] = analyst
    monkeypatch.setattr(rn, "execute_query", lambda sql, params=None, **kw: [])
    monkeypatch.setattr(rn, "execute_write", lambda sql, params=None: 1)
    monkeypatch.setattr(rn, "execute_insert", lambda sql, params=None: 11)
    client = TestClient(app)

    body = {"version": "1.2.0", "title": "新公告",
            "sections": [{"kind": "feature", "items": ["新增了点什么"]}]}
    assert client.get("/api/admin/release-notes").status_code == 403
    assert client.post("/api/admin/release-notes", json=body).status_code == 403
    assert client.put("/api/admin/release-notes/1", json=body).status_code == 403
    assert client.delete("/api/admin/release-notes/1").status_code == 403


def test_personal_domain_open_to_role_without_perm_codes(monkeypatch):
    """无权限码角色也要能读公告/存自己的跳过状态(否则跳过语义只对 admin 生效)。"""
    async def nocodes():
        return {"user_id": 9, "username": "u", "role": "nocodes"}

    app = _app()
    app.dependency_overrides[auth.get_current_user] = nocodes
    monkeypatch.setattr(rn, "execute_query",
                        lambda sql, params=None, **kw: [_row()] if "FROM adh_release_notes" in sql else [])
    monkeypatch.setattr(rn, "execute_write", lambda sql, params=None: 1)
    client = TestClient(app)
    # 属主由路由层 user_id 过滤保证: 写入只带 key/status, 不带身份字段
    assert client.get("/api/release-notes").status_code == 200
    assert client.get("/api/release-notes/state").status_code == 200
    assert client.put("/api/release-notes/state",
                      json={"keys": ["guide:all"], "status": "skipped"}).status_code == 200
    assert client.put("/api/release-notes/state",
                      json={"keys": ["whoami:all"], "status": "skipped"}).status_code == 400


def test_admin_domain_allows_admin(monkeypatch):
    async def admin():
        return {"user_id": 1, "username": "admin", "role": "admin"}

    app = _app()
    app.dependency_overrides[auth.get_current_user] = admin
    monkeypatch.setattr(rn, "execute_query", lambda sql, params=None, **kw: [])
    monkeypatch.setattr(rn, "execute_insert", lambda sql, params=None: 11)
    client = TestClient(app)
    body = {"version": "1.2.0", "title": "新公告", "guide_key": "workspace",
            "sections": [{"kind": "feature", "items": ["新增了点什么"]}]}
    assert client.post("/api/admin/release-notes", json=body).status_code == 200
    # 管理页列表要能看到草稿与发布人(个人域不给这些字段)
    out = client.get("/api/admin/release-notes").json()
    assert "items" in out


# ── 管理域内容校验与终态 ─────────────────────────────────────────────


def test_create_rejects_duplicate_version(monkeypatch):
    monkeypatch.setattr(rn, "execute_query",
                        lambda sql, params=None, **kw: [{"id": 9}] if "WHERE version" in sql else [])
    with pytest.raises(HTTPException) as ei:
        rn.admin_create_note(rn.ReleaseNoteUpsert(
            version="1.1.0", title="重复版本",
            sections=[{"kind": "feature", "items": ["x"]}]), admin={"user_id": 1, "username": "admin"})
    assert ei.value.status_code == 409


@pytest.mark.parametrize("payload", [
    {"version": "v1.1", "title": "非法版本号", "sections": [{"kind": "feature", "items": ["x"]}]},
    {"version": "1.2.0", "title": "  ", "sections": [{"kind": "feature", "items": ["x"]}]},
    {"version": "1.2.0", "title": "缺更新项", "sections": [{"kind": "feature", "items": []}]},
    {"version": "1.2.0", "title": "类型非法", "sections": [{"kind": "whatever", "items": ["x"]}]},
    {"version": "1.2.0", "title": "引导标识非法", "guide_key": "Workspace 2",
     "sections": [{"kind": "feature", "items": ["x"]}]},
])
def test_content_validation_rejects_bad_input(monkeypatch, payload):
    """发布入口的每条拒绝都要显式报错, 不能"存了个半成品"再让用户猜。"""
    written = []
    monkeypatch.setattr(rn, "execute_query", lambda sql, params=None, **kw: [])
    monkeypatch.setattr(rn, "execute_insert", lambda sql, params=None: written.append(sql) or 1)
    with pytest.raises(HTTPException) as ei:
        rn.admin_create_note(rn.ReleaseNoteUpsert(**payload), admin={"user_id": 1, "username": "admin"})
    assert ei.value.status_code == 400
    assert not written


def test_update_and_delete_missing_note_returns_404(monkeypatch):
    monkeypatch.setattr(rn, "execute_query", lambda sql, params=None, **kw: None)
    body = rn.ReleaseNoteUpsert(version="1.2.0", title="改一改",
                                sections=[{"kind": "fix", "items": ["修好了"]}])
    with pytest.raises(HTTPException) as ei:
        rn.admin_update_note(99, body, admin={"user_id": 1, "username": "admin"})
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        rn.admin_delete_note(99, admin={"user_id": 1, "username": "admin"})
    assert ei.value.status_code == 404


def test_draft_created_without_publish_stamp(monkeypatch):
    """草稿落库不能给自己打上发布时间: published_at 由 is_published 同一个入参兼带。"""
    inserts = []
    monkeypatch.setattr(rn, "execute_query", lambda sql, params=None, **kw: [])
    monkeypatch.setattr(rn, "execute_insert", lambda sql, params=None: inserts.append((sql, params)) or 5)
    out = rn.admin_create_note(rn.ReleaseNoteUpsert(
        version="1.2.0", title="草稿", is_published=0,
        sections=[{"kind": "feature", "items": ["x"]}]), admin={"user_id": 1, "username": "admin"})
    assert out["id"] == 5
    sql, params = inserts[0]
    assert "CASE WHEN %s = 1 THEN UTC_TIMESTAMP(6) ELSE NULL END" in sql
    assert params[6] == 0 and params[7] == 0      # is_published 与其发布时间判定同一口径
    assert json.loads(params[3]) == [{"kind": "feature", "items": ["x"]}]


def test_routes_registered_under_expected_prefixes():
    """前端调的路径改名必须同步登记, 否则中间件放行与页面调用双双错位。"""
    personal = {r.path for r in rn.router.routes}
    admin = {r.path for r in rn.admin_router.routes}
    assert {"", "/", "/state"} <= personal
    assert {"", "/", "/{note_id}"} <= admin


def test_collection_routes_accept_both_slash_forms():
    """集合路径的 GET/POST 都要支持带与不带尾斜杠。

    只注册 "/" 会让前端 POST /api/admin/release-notes 撞 405(同路径上 GET 先局部
    命中就不走 redirect_slashes), admin 页新建公告直接不可用。
    """
    by_path: dict[str, set[str]] = {}
    for r in rn.admin_router.routes:
        by_path.setdefault(r.path, set()).update(r.methods or set())
    for path in ("", "/"):
        assert {"GET", "POST"} <= by_path[path], (path, by_path[path])
