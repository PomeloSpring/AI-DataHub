"""报告正文转对象存储的回归：key 构建、直链寻址、写路径、存量搬迁、泄露面。

口径（docker/mysql/report_content_oss_migration.sql + report_service 正文存取段）：
* 正文唯一事实源 = ``adh_reports.content_object_key`` 指向的对象；``content`` 列退役为恒空串；
* 读侧只回「域名 + 签名」的直链（对象存储模式）或同源代理路径（本地回退模式），
  对象 key 本身不外泄给客户端；
* 上传失败一律显式外抛，**绝不**回退写库正文（双真值源迟早飘）；
* 存量正文由 backend/scripts/migrate_report_content_to_oss.py 搬运（dry-run 默认、幂等）。

本文件全程用内存桶 + 假连接，不碰真对象存储、不碰真库。
"""
import io
import json
from contextlib import contextmanager

import pytest

from backend.common.object_storage import build_report_content_key
from backend.modules.viz.services import report_access as access
from backend.modules.viz.services import report_service as reports

OWNER = {"user_id": 7, "role": "analyst"}
SIGNED_URL = ("https://bucket.oss-test.example.com/ai-datahub/7/reports/5"
              "?X-Amz-Credential=LTAIfake&X-Amz-Signature=deadbeef&Expires=300")


class MemStorage:
    """内存桶：行为对齐 ObjectStorage 公开面（含本地回退模式无直链这个事实）。"""

    def __init__(self, is_object_storage=True, presigned_ok=True):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.is_object_storage = is_object_storage
        self.presigned_ok = presigned_ok
        self.fail_upload = False

    def upload_bytes(self, key, data, content_type="application/octet-stream"):
        if self.fail_upload:
            raise RuntimeError("simulated outage")
        self.objects[key] = data

    def download_bytes(self, key):
        return self.objects.get(key)

    def exists(self, key):
        return key in self.objects

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)
        return True

    def get_presigned_url(self, key, expires=3600, download_filename=""):
        if not self.presigned_ok or key not in self.objects:
            return None
        return (f"https://bucket.oss-test.example.com/{key}"
                f"?X-Amz-Credential=LTAIfake&X-Amz-Signature=deadbeef&Expires={expires}")


@pytest.fixture(autouse=True)
def storage(monkeypatch):
    store = MemStorage()
    monkeypatch.setattr(reports, "content_storage", lambda: store)
    return store


@pytest.fixture(autouse=True)
def authorize_ok(monkeypatch):
    """属主自检路径里 authorize_workspace 只做工作空间归属，测试里放行即可
    （放行后跑的仍是 report_access 的真实可见性判据，不是把判断整个换掉）。"""
    monkeypatch.setattr(access, "authorize_workspace", lambda user, ws: ws)


class _Cur:
    """假 cursor：把语句收进 log，SELECT 一律回 None（写路径的捕获测试不读结果）。"""

    def __init__(self, log):
        self.log = log

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        return 1

    def fetchone(self): return None
    def fetchall(self): return []


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _Cur(self.log)


def _capture_conn(log):
    return lambda: _Conn(log)


# ── 1. key 唯一构建点 ────────────────────────────────────────────────────────

def test_key_is_owner_prefixed_and_body_format_independent():
    """首段是 user_id（报告跟随属主）；不带扩展名，所以 md↔html 互转不需要搬对象。"""
    assert build_report_content_key(7, 123) == "ai-datahub/7/reports/123"
    assert build_report_content_key(7, 123) == build_report_content_key("7", "123")


# ── 2. 读侧寻址 ─────────────────────────────────────────────────────────────

def test_object_storage_mode_returns_presigned_url_never_the_key(storage):
    key = "ai-datahub/7/reports/5"
    storage.objects[key] = b"body"
    row = {"id": 5, "content_object_key": key, "content_size": 9}
    out = reports.content_access(row)
    assert out["content_url"].startswith("https://") and "X-Amz-Signature=" in out["content_url"]
    assert out["content_expires_at"] and not out["content_warning"]
    # 对象 key 属内部存储标识：对外投影里不得出现（客户端只拿现拼的短期直链）
    assert key not in json.dumps(access.public_report(row), ensure_ascii=False)


def test_local_fallback_mode_returns_same_origin_proxy_path(monkeypatch):
    """本地回退无签名可拼 → 给同源代理路径，前端 fetch 逻辑不变（不是降级，是寻址形式）。"""
    local = MemStorage(is_object_storage=False)
    local.objects["ai-datahub/7/reports/6"] = b"x"
    monkeypatch.setattr(reports, "content_storage", lambda: local)
    out = reports.content_access({"id": 6, "content_object_key": "ai-datahub/7/reports/6",
                                  "content_size": 1})
    assert out["content_url"] == "/api/reports/6/content"
    assert out["content_expires_at"] is None and not out["content_warning"]


def test_unmigrated_row_is_explicit_not_db_fallback(storage):
    """key 空 + 库里有正文 = 还没搬：显式指向迁移脚本，绝不回退读库正文（双真值源）。"""
    out = reports.content_access({"id": 7, "content_object_key": "", "content": "# 旧正文"})
    assert out["content_url"] == ""
    assert "migrate_report_content_to_oss" in out["content_warning"]


def test_empty_body_row_says_body_is_empty(storage):
    out = reports.content_access({"id": 8, "content_object_key": "", "content": ""})
    assert "正文为空" in out["content_warning"]


def test_presign_failure_is_visible_not_blank(storage):
    storage.presigned_ok = False
    storage.objects["ai-datahub/7/reports/9"] = b"x"
    out = reports.content_access({"id": 9, "content_object_key": "ai-datahub/7/reports/9",
                                  "content_size": 3})
    assert out["content_url"] == "" and "签名" in out["content_warning"]


def test_get_report_carries_url_and_drops_body(monkeypatch, storage):
    row = {"id": 11, "owner_id": 7, "workspace_id": 3, "source_type": "manual",
           "generation_status": "ready", "content": "", "content_size": 5,
           "content_object_key": "ai-datahub/7/reports/11", "format": "markdown"}
    storage.objects[row["content_object_key"]] = b"hello"
    monkeypatch.setattr(reports, "load_report", lambda rid: dict(row))
    monkeypatch.setattr(reports, "DBConnection", _capture_conn([]))
    out = reports.get_report(11, user=OWNER)
    assert out["content_url"].startswith("https://")
    assert "content" not in out and out["content_size"] == 5
    assert "access_warning" not in out


def test_get_report_surfaces_missing_body_reason(monkeypatch, storage):
    """正文没搬/丢了：warning 必须落到前端已在消费的 access_warning 通道上。"""
    row = {"id": 12, "owner_id": 7, "workspace_id": 3, "source_type": "manual",
           "generation_status": "ready", "content": "# 旧", "content_object_key": "",
           "format": "markdown"}
    monkeypatch.setattr(reports, "load_report", lambda rid: dict(row))
    out = reports.get_report(12, user=OWNER)
    assert "migrate_report_content_to_oss" in out["access_warning"]


# ── 3. 写路径 ───────────────────────────────────────────────────────────────

def _route_queries(responses):
    """按 SQL 片段路由 execute_query 的结果（finish_report 里两类 SELECT）。"""
    def fake(sql, params=(), fetchone=False):
        for needle, value in responses.items():
            if needle in sql:
                return dict(value) if isinstance(value, dict) else value
        return None
    return fake


def test_finish_report_writes_object_and_keeps_db_column_empty(monkeypatch, storage):
    written = []
    key = build_report_content_key(7, 21)
    monkeypatch.setattr("backend.common.db.execute_query", _route_queries({
        "SELECT owner_id, format": {"owner_id": 7, "format": "markdown"},
        "SELECT content_object_key": {"content_object_key": key},   # 正文被采纳
    }))
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)

    assert reports.finish_report(21, generation_status="degraded", content="# 事实表") is True
    assert storage.objects[key] == "# 事实表".encode()
    sql, params = written[0]
    assert "`content_object_key`=%s" in sql and key in params
    assert "content_object_key=IF(lease_expires_at<=NOW(),'',content_object_key)" in sql
    # content 列写的是空串：正文本身不作为参数进库
    assert not any(isinstance(p, str) and "# 事实表" in p for p in params)
    assert not storage.deleted


def test_finish_report_lease_race_does_not_leave_orphan(monkeypatch, storage):
    """租约在上传途中过期 → SQL 的 IF 把 key 清了；刚上传的对象必须收走，不留孤儿。"""
    monkeypatch.setattr("backend.common.db.execute_query", _route_queries({
        "SELECT owner_id, format": {"owner_id": 7, "format": "markdown"},
        "SELECT content_object_key": {"content_object_key": ""},     # 被 IF 清掉了
    }))
    monkeypatch.setattr("backend.common.db.execute_write", lambda sql, params=(): 1)
    reports.finish_report(22, generation_status="ready", content="迟到的正文")
    assert storage.deleted == [build_report_content_key(7, 22)]


def test_finish_report_without_content_touches_no_object(monkeypatch, storage):
    """失败终态不带正文：不该多一次对象读写。"""
    seen = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): seen.append(sql) or 1)
    reports.finish_report(24, generation_status="failed", stage_error_code="QUERY_FAILED")
    assert seen and "SELECT" not in seen[0]
    assert storage.objects == {}


def test_finish_report_rejects_unknown_terminal_state(monkeypatch):
    with pytest.raises(ValueError):
        reports.finish_report(23, generation_status="published", content="x")


def test_finish_report_missing_row_is_explicit(monkeypatch, storage):
    monkeypatch.setattr("backend.common.db.execute_query", lambda *a, **k: None)
    with pytest.raises(ValueError) as exc:
        reports.finish_report(25, generation_status="ready", content="x")
    assert "不存在" in str(exc.value)


def test_create_content_report_uploads_before_insert(monkeypatch, storage):
    written = []
    monkeypatch.setattr(reports, "DBConnection", _capture_conn(written))
    out = reports.create_content_report(title="报告", content="# 结论", workspace_id=3,
                                       owner_id=7, source_type="manual")
    key = build_report_content_key(7, out["report_id"])
    assert storage.objects[key] == "# 结论".encode()
    sql, params = written[0]
    assert "content_object_key" in sql and key in params
    assert not any(isinstance(p, str) and "# 结论" in p for p in params)


def test_create_content_report_upload_failure_writes_nothing(monkeypatch, storage):
    written = []
    storage.fail_upload = True
    monkeypatch.setattr(reports, "DBConnection", _capture_conn(written))
    with pytest.raises(ValueError) as exc:
        reports.create_content_report(title="报告", content="# 结论", workspace_id=3,
                                      owner_id=7, source_type="manual")
    assert "对象存储" in str(exc.value)
    assert written == []


def test_create_report_run_key_reuse_produces_no_second_object(monkeypatch, storage):
    """同一 run 的重投递：先判复用再写对象，顺序反了就会多出一份没人引用的正文。"""
    monkeypatch.setattr("backend.common.db.execute_query", lambda sql, params=(), fetchone=False:
                        {"id": 55, "owner_id": 7, "workspace_id": 3, "generation_status": "ready"})
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append(sql) or 1)
    out = reports.create_report(title="t", content="# 正文", workspace_id=3, owner_id=7,
                                run_key="rk-1")
    assert out["id"] == 55 and storage.objects == {} and written == []


def test_create_report_run_key_owner_mismatch_still_refused(monkeypatch, storage):
    monkeypatch.setattr("backend.common.db.execute_query", lambda sql, params=(), fetchone=False:
                        {"id": 55, "owner_id": 8, "workspace_id": 3, "generation_status": "ready"})
    with pytest.raises(PermissionError):
        reports.create_report(title="t", content="x", workspace_id=3, owner_id=7, run_key="rk-2")


def test_create_report_empty_body_creates_no_object(monkeypatch, storage):
    """排队中的报告（content="")：不产空对象，key 留空由 finish_report 补。"""
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    out = reports.create_report(title="t", content="", workspace_id=3, owner_id=7)
    assert storage.objects == {}
    # 默认不再预设"降级"（那是没人复核的陷阱默认）；排队/终态由调用方显式传
    assert out["generation_status"] == "ready"
    key_pos = written[0][0].index("content_object_key")   # 列已写入，但值是空串
    assert key_pos and "''" in written[0][0]


def test_delete_report_removes_object(monkeypatch, storage):
    row = {"id": 31, "owner_id": 7, "source_type": "manual", "content_object_key": "k/31"}
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: dict(row))
    monkeypatch.setattr("backend.common.db.execute_write", lambda sql, params=(): 1)
    # 无同归集键的历史版本（execute_query 回空→只删目标行本身）
    monkeypatch.setattr("backend.common.db.execute_query", lambda sql, params=(): [])
    reports.delete_report(31, OWNER)
    assert storage.deleted == ["k/31"]


def test_update_content_report_reuses_same_key(monkeypatch, storage):
    """key 不带版本 → 就地覆盖同一对象：不新增对象、不删旧对象，也读不到旧正文。"""
    key = build_report_content_key(7, 41)
    row = {"id": 41, "owner_id": 7, "source_type": "manual", "format": "markdown",
           "content_object_key": key}
    storage.objects[key] = "旧正文".encode()
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: dict(row))
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    reports.update_content_report(41, OWNER, content="新正文", format="html")
    assert storage.objects == {key: "新正文".encode()}
    assert not storage.deleted
    sql, params = written[0]
    assert "content=''" in sql and "content_object_key=%s" in sql and key in params


def test_read_content_separates_missing_key_from_missing_object(storage):
    """两种"读不到"必须分开：key 空的问题在库（跑迁移），对象缺的问题在桶（重生成）。"""
    with pytest.raises(ValueError) as obj:
        reports.read_report_content_bytes({"content_object_key": "k/missing"})
    assert "取不到" in str(obj.value)
    with pytest.raises(LookupError) as key:
        reports.read_report_content_bytes({"content_object_key": "", "content": "# 旧"})
    assert "migrate_report_content_to_oss" in str(key.value)
    with pytest.raises(LookupError) as empty:
        reports.read_report_content_bytes({"content_object_key": "", "content": ""})
    assert "正文为空" in str(empty.value)


# ── 4. 存量搬迁脚本 ─────────────────────────────────────────────────────────

class _MigCur:
    def __init__(self, log, state):
        self.log, self.state = log, state
        self._last = ""

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self._last = sql
        self.log.append((sql, params))
        if sql.lstrip().upper().startswith("UPDATE"):
            # 条件写：pending 里的行搬完就消失（模拟 content_object_key='' 的守卫）
            self.state["pending"] = [r for r in self.state["pending"] if r["id"] != params[-1]]
            return 1
        return 1

    def fetchall(self):
        if "LENGTH(content)" in self._last:
            return [dict(r) for r in self.state["pending"]]
        return [dict(r) for r in self.state["pending"] if "content_object_key<>''" in self._last]

    def fetchone(self):
        if "content FROM adh_reports" in self._last:
            row = self.state["pending"][0] if self.state["pending"] else None
            return {"content": row["content"]} if row else None
        return None


@contextmanager
def _fake_conn(log, state):
    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _MigCur(log, state)
        def commit(self): log.append(("COMMIT", None))
    yield C()


@pytest.fixture
def migrate_env(monkeypatch, storage):
    from backend.scripts import migrate_report_content_to_oss as mig
    log: list = []
    state = {"pending": [{"id": 101, "owner_id": 7, "format": "markdown", "bytes": 6,
                          "content": "# 旧"}]}
    monkeypatch.setattr(mig, "get_object_storage", lambda: storage)
    monkeypatch.setattr(mig, "get_metadata_conn", lambda: _fake_conn(log, state))
    return mig, log, state


def _updates(log):
    return [(s, p) for s, p in log if s.lstrip().upper().startswith("UPDATE")]


def test_migration_dry_run_writes_nothing(migrate_env, storage):
    mig, log, state = migrate_env
    assert mig.migrate(apply=False) == 1              # dry-run 后仍待搬迁
    assert storage.objects == {}
    assert _updates(log) == []


def test_migration_apply_moves_body_and_clears_db_column(migrate_env, storage):
    mig, log, state = migrate_env
    assert mig.migrate(apply=True) == 0
    key = build_report_content_key(7, 101)
    assert storage.objects[key] == "# 旧".encode()
    sql, params = _updates(log)[0]
    assert params[0] == key and params[-1] == 101
    assert "content=''" in sql and "AND content_object_key=''" in sql


def test_migration_skips_rows_without_owner(migrate_env, storage):
    """没有属主就定不了 key（首段是 user_id）—— 不猜值，显式失败并留在 pending 里。"""
    mig, log, state = migrate_env
    state["pending"][0]["owner_id"] = 0
    assert mig.migrate(apply=True) != 0
    assert storage.objects == {}
    assert _updates(log) == []


def test_migration_is_idempotent(migrate_env, storage):
    mig, log, state = migrate_env
    assert mig.migrate(apply=True) == 0               # 第一轮搬完，pending 已空
    log.clear()
    assert mig.migrate(apply=True) == 0
    assert _updates(log) == []
    assert len(storage.objects) == 1                  # 没有第二份对象


def test_migration_upload_failure_leaves_row_pending(migrate_env, storage):
    mig, log, state = migrate_env
    storage.fail_upload = True
    assert mig.migrate(apply=True) != 0
    assert _updates(log) == []
    assert len(state["pending"]) == 1                 # 没搬走，重跑还会处理它


# ── 5. REST 面：本地模式代理端点 ────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch, storage):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.modules.viz.api import report as report_api
    from backend.common.auth import get_current_user
    app = FastAPI()
    app.include_router(report_api.router, prefix="/api/reports")
    app.dependency_overrides[get_current_user] = lambda: dict(OWNER)
    monkeypatch.setattr(report_api.report_service, "content_storage", lambda: storage)
    return TestClient(app), report_api, storage


def _row(content_object_key="ai-datahub/7/reports/77", content=""):
    return {"id": 77, "owner_id": 7, "workspace_id": 3, "source_type": "manual",
            "generation_status": "ready", "format": "markdown",
            "content_object_key": content_object_key, "content": content, "content_size": 5}


def test_content_proxy_refused_when_object_storage_is_available(api, monkeypatch):
    """直链与代理不并存：对象存储模式下代理显式 400，避免漂出第二套寻址口径。"""
    client, report_api, store = api
    store.objects["ai-datahub/7/reports/77"] = "# 正文".encode()
    monkeypatch.setattr(report_api.report_service, "load_report", lambda rid: _row())
    res = client.get("/api/reports/77/content")
    assert res.status_code == 400 and "content_url" in res.json()["detail"]


def test_content_proxy_serves_text_plain_attachment_in_local_mode(api, monkeypatch):
    """本地回退模式回正文；必须是 text/plain + attachment —— HTML 正文以本站源
    inline 下发等于把存储型 XSS 挂在自己域名下。"""
    client, report_api, store = api
    monkeypatch.setattr(store, "is_object_storage", False)
    store.objects["ai-datahub/7/reports/77"] = "<h1>正文</h1><script>alert(1)</script>".encode()
    monkeypatch.setattr(report_api.report_service, "load_report", lambda rid: _row())
    res = client.get("/api/reports/77/content")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert "attachment" in res.headers["content-disposition"]
    assert b"script" in res.content          # 原样字节：净化在渲染侧，不在这里改内容


def test_content_proxy_missing_object_is_410_not_404(api, monkeypatch):
    """行在、对象没了：说清是正文不可用，不伪装成报告不存在（排查方向完全不同）。"""
    client, report_api, store = api
    monkeypatch.setattr(store, "is_object_storage", False)
    monkeypatch.setattr(report_api.report_service, "load_report", lambda rid: _row())
    res = client.get("/api/reports/77/content")
    assert res.status_code == 410 and "重新生成" in res.json()["detail"]


def test_content_proxy_requires_the_same_access_as_detail(api, monkeypatch):
    """访问判据与详情同一（can_access_report）：非属主拿不到正文，匿名也不行。"""
    client, report_api, store = api
    monkeypatch.setattr(report_api.report_service, "load_report",
                        lambda rid: _row(content_object_key=""))
    assert client.get("/api/reports/77/content").status_code == 404


# ── 6. 超时兜底清理 ─────────────────────────────────────────────────────────

def test_cleanup_stale_reports_clears_key_and_reclaims_object(monkeypatch, storage):
    """正文列与对象 key 必须一起清、对象一起收：只清 content 会留下状态与正文不一致的半截行。"""
    key = "ai-datahub/7/reports/90"
    storage.objects[key] = "没跑完的正文".encode()
    written = []
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), fetchone=False: [{"id": 90, "content_object_key": key}])
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append(sql) or 1)
    reports.cleanup_stale_reports()
    assert storage.deleted == [key] and storage.objects == {}
    assert "content_object_key=''" in written[0] and "content=''" in written[0]


# ── 7. 泄露面 ───────────────────────────────────────────────────────────────

def test_signed_url_would_be_blocked_if_it_reached_the_llm():
    """直链含 X-Amz-Credential=，若误入工具返回会被出站守卫拦下（纵深兜底，fail-loud）。"""
    from backend.modules.mind.execution import outbound_guard as guard
    assert "sensitive_field" in guard.find_sensitive_hits(SIGNED_URL)


def test_ai_report_list_projection_has_no_body_or_key():
    """AI 通道的报告清单是显式列白名单：不得因为正文出库就顺手把 key/直链递进去。"""
    from backend.modules.mind.execution import function_tools as ft
    src = io.open(ft.__file__, encoding="utf-8").read()
    stmt = src[src.index("def _list_reports"):]
    select = stmt[stmt.index("SELECT"):stmt.index("FROM adh_reports")]
    for forbidden in ("content", "share_token_hash", "analysis_source"):
        assert forbidden not in select
