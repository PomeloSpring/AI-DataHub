"""用户资产清单(OSS 托管, 资产跟随用户跨工作空间) —— 离线单测(不触碰真实数据库/对象存储)。

覆盖:
  W1  归档: 会话产物 → 对象存储 + 清单落库(user_id 归属); delete_source 清理本地产物;
  W2  两步确认: asset_archive/cleanup_execute confirm=false 仅预览不落盘;
  W3  清理候选: 已归档来源文件(按 source_path) + 已关闭会话目录(活跃会话不入候选);
  W4  磁盘配额拦截: 用量达配额拒绝新建会话(409);
  W5  磁盘用量统计: 递归求和(不计符号链接);
  W6  LLM 工具组: assets 六工具注册 + 无上下文 fail-closed;
  W7  下载响应头: 中文文件名按 RFC 5987 编码(不抛 latin-1 编码错);
  W8  资产跟随用户: 跨工作空间可见/可下载, 他人资产 fail-closed 404;
  W9  存量迁移: 旧 workspaces/ key 对象搬迁到 ai-datahub/{user_id}/assets/(幂等)。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

import pytest

ua_api = importlib.import_module("backend.modules.mind.api.user_assets")
sw = importlib.import_module("backend.modules.mind.execution.session_workspace")
asset_tools = importlib.import_module("backend.modules.mind.execution.sdk_tools.asset_tools")
mig = importlib.import_module("scripts.migrate_user_assets_objects")


# ── 假对象存储 / 假 DB ──────────────────────────────────────────────

class FakeStorage:
    is_object_storage = True

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_bytes(self, key, data, content_type="application/octet-stream"):
        self.blobs[key] = data

    def download_bytes(self, key):
        return self.blobs.get(key)

    def exists(self, key):
        return key in self.blobs

    def delete(self, key):
        self.deleted.append(key)
        return self.blobs.pop(key, None) is not None


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._rows: list = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = list(params or [])
        self.rowcount = 0
        if "FROM adh_agent_sessions" in s and "conversation_id=%s" in s:
            rows = [r for r in self.db["sessions"]
                    if r.get("conversation_id") == p[0] and r.get("user_id") == p[1]]
            if "AND workspace_id=%s" in s:
                rows = [r for r in rows if r.get("workspace_id") == p[2]]
            self._rows = [dict(r) for r in rows]
        elif "FROM adh_agent_sessions" in s:
            self._rows = [dict(r) for r in self.db["sessions"] if r.get("workspace_id") == p[0]]
        elif "INSERT INTO adh_user_assets" in s:
            self.db["assets"].append({"id": p[0], "user_id": p[1], "origin_workspace_id": p[2],
                                      "name": p[3], "filename": p[4], "category": p[5],
                                      "object_key": p[6], "storage_type": p[7], "size": p[8],
                                      "source_conversation_id": p[9], "source_path": p[10],
                                      "created_by": p[11]})
            self._rows = []
            self.rowcount = 1
        elif "UPDATE adh_user_assets SET object_key=" in s:
            rows = [a for a in self.db["assets"] if a["id"] == p[1] and a["object_key"] == p[2]]
            for a in rows:
                a["object_key"] = p[0]
            self._rows = []
            self.rowcount = len(rows)
        elif "DELETE FROM adh_user_assets" in s:
            self.db["assets"] = [a for a in self.db["assets"] if not (a["id"] == p[0] and a["user_id"] == p[1])]
            self._rows = []
            self.rowcount = 1
        elif "LEFT JOIN adh_workspaces" in s:
            ws_names = {w["id"]: w["name"] for w in self.db.get("workspaces", [])}
            rows = []
            for a in self.db["assets"]:
                if a["user_id"] != p[0]:
                    continue
                d = {k: a.get(k) for k in ("id", "name", "filename", "category", "size",
                                           "source_conversation_id", "origin_workspace_id", "created_at")}
                d["origin_workspace_name"] = ws_names.get(a.get("origin_workspace_id"))
                rows.append(d)
            self._rows = rows
        elif "FROM adh_user_assets WHERE id=" in s:
            self._rows = [dict(a) for a in self.db["assets"]
                          if a["id"] == p[0] and a["user_id"] == p[1]]
        elif "FROM adh_user_assets WHERE origin_workspace_id=" in s and "source_conversation_id" in s:
            self._rows = [dict(a) for a in self.db["assets"]
                          if a["origin_workspace_id"] == p[0] and a["source_conversation_id"] > 0]
        elif "SELECT id, user_id, filename, object_key FROM adh_user_assets" in s:
            self._rows = [dict(a) for a in self.db["assets"]
                          if str(a.get("object_key") or "").startswith("workspaces/")]
        elif "FROM adh_user_workspace_quota" in s:
            self._rows = [dict(r) for r in self.db["quota"]]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def close(self):
        pass

    def commit(self):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = {
        "sessions": [
            {"conversation_id": 5, "user_id": 9, "workspace_id": 3,
             "session_key": "a" * 32, "status": "idle", "created_at": ""},
            {"conversation_id": 7, "user_id": 9, "workspace_id": 8,
             "session_key": "c" * 32, "status": "idle", "created_at": ""},
        ],
        "assets": [],
        "quota": [{"disk_quota_bytes": 1024}],
        "workspaces": [{"id": 3, "name": "分析工作站"}, {"id": 8, "name": "实验空间"}],
    }
    storage = FakeStorage()
    monkeypatch.setattr("backend.common.config.ADH_WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setattr("backend.common.config.OBJECT_STORAGE_PREFIX", "ai-datahub")
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn", lambda: FakeConn(db))
    monkeypatch.setattr(ua_api, "get_metadata_conn", lambda: FakeConn(db))
    monkeypatch.setattr(ua_api, "get_object_storage", lambda: storage)
    monkeypatch.setattr("backend.common.object_storage.get_object_storage", lambda: storage)
    # 建会话目录(两个工作空间)并放产物文件
    root = sw.session_paths(tmp_path, "a" * 32, 3, create=True)
    (root / "workspace" / "report.txt").write_text("分析结果", encoding="utf-8")
    (root / "workspace" / "sub").mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "sub" / "明细.txt").write_text("明细", encoding="utf-8")
    root8 = sw.session_paths(tmp_path, "c" * 32, 8, create=True)
    (root8 / "workspace" / "实验.txt").write_text("实验", encoding="utf-8")
    return {"db": db, "storage": storage, "root": root, "root8": root8, "tmp": tmp_path}


# ── W1 归档 ────────────────────────────────────────────────────────


def test_archive_upload_and_register(env):
    asset = ua_api.archive_session_file(9, 5, "report.txt", "周报")
    assert asset["filename"] == "report.txt"
    assert "object_key" not in asset, "object_key 是内部存储标识, 不得外露"
    row = env["db"]["assets"][0]
    key = row["object_key"]
    assert key.startswith("ai-datahub/9/assets/"), "资产对象 key 按用户分目录"
    assert key in env["storage"].blobs
    assert env["storage"].blobs[key] == "分析结果".encode("utf-8")
    assert row["name"] == "周报"
    assert row["user_id"] == 9
    assert row["origin_workspace_id"] == 3
    assert row["source_conversation_id"] == 5
    assert row["source_path"] == "report.txt"
    # 未指定 delete_source 时本地产物保留
    assert (env["root"] / "workspace" / "report.txt").is_file()


def test_archive_nested_source_path(env):
    ua_api.archive_session_file(9, 5, "sub/明细.txt", "明细")
    assert env["db"]["assets"][0]["source_path"] == "sub/明细.txt"
    assert env["db"]["assets"][0]["filename"] == "明细.txt"


def test_archive_delete_source(env):
    ua_api.archive_session_file(9, 5, "report.txt", "", delete_source=True)
    assert not (env["root"] / "workspace" / "report.txt").exists()
    assert env["db"]["assets"], "资产清单已落库(不受本地删除影响)"


def test_archive_rejects_path_escape(env):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ua_api.archive_session_file(9, 5, "../../etc/passwd", "")
    assert exc.value.status_code in (403, 404)


def test_archive_rejects_other_users_conversation(env):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ua_api.archive_session_file(10, 5, "report.txt", "")
    assert exc.value.status_code == 404, "他人会话 fail-closed"


# ── W2 两步确认(LLM 交互契约) ──────────────────────────────────────


def _text_of(out) -> str:
    return out["content"][0]["text"]


def test_asset_archive_two_step_confirm(env, monkeypatch):
    monkeypatch.setattr(asset_tools, "_ctx_ids", lambda: (9, 3, 5))
    out = asyncio.run(asset_tools.asset_archive({"path": "report.txt", "confirm": False}))
    text = _text_of(out)
    assert "confirm_required" in text
    assert not env["db"]["assets"], "confirm=false 不得落盘"
    out2 = asyncio.run(asset_tools.asset_archive({"path": "report.txt", "confirm": True, "delete_source": True}))
    text2 = _text_of(out2)
    assert "archived" in text2
    assert "object_key" not in text2, "工具返回不得含对象存储内部 key"
    assert env["db"]["assets"]


def test_cleanup_execute_requires_confirm(env, monkeypatch):
    monkeypatch.setattr(asset_tools, "_ctx_ids", lambda: (9, 3, 5))
    out = asyncio.run(asset_tools.cleanup_execute_tool({"confirm": False}))
    text = _text_of(out)
    assert "confirm_required" in text


# ── W3 清理候选 ────────────────────────────────────────────────────


def test_cleanup_candidates_archived_source_and_closed_sessions(env):
    # 归档后本地仍在 → 来源文件入候选
    ua_api.archive_session_file(9, 5, "report.txt", "周报")
    # closed 会话目录入候选; idle 会话不入
    env["db"]["sessions"].append({"conversation_id": 6, "user_id": 9, "workspace_id": 3,
                                  "session_key": "b" * 32, "status": "closed", "created_at": ""})
    sw.session_paths(env["tmp"], "b" * 32, 3, create=True)
    candidates = ua_api._cleanup_candidates(3)
    kinds = sorted(c["kind"] for c in candidates)
    assert kinds == ["file", "session"]
    file_c = next(c for c in candidates if c["kind"] == "file")
    assert file_c["filename"] == "report.txt"
    session_c = next(c for c in candidates if c["kind"] == "session")
    assert session_c["session_key"] == "b" * 32


def test_cleanup_candidates_locate_nested_source_by_source_path(env):
    ua_api.archive_session_file(9, 5, "sub/明细.txt", "明细")
    candidates = ua_api._cleanup_candidates(3)
    file_c = next(c for c in candidates if c["kind"] == "file")
    assert file_c["path"].endswith("sub/明细.txt"), "按 source_path 定位嵌套来源文件"


# ── W4 磁盘配额拦截 ────────────────────────────────────────────────


def test_disk_quota_blocks_new_session(env, monkeypatch):
    # 用量(mock)达到配额 1024 → 拒绝
    monkeypatch.setattr(sw, "workspace_disk_usage", lambda ws: 1024)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        sw.assert_workspace_disk_quota(3)
    assert exc.value.status_code == 409
    assert "配额" in exc.value.detail


def test_disk_quota_under_limit_passes(env, monkeypatch):
    monkeypatch.setattr(sw, "workspace_disk_usage", lambda ws: 10)
    sw.assert_workspace_disk_quota(3)  # 不抛


# ── W5 磁盘用量统计 ────────────────────────────────────────────────


def test_workspace_disk_usage_recursive(env):
    (env["root"] / "workspace" / "big.bin").write_bytes(b"x" * 500)
    usage = sw.workspace_disk_usage(3)
    assert usage >= 500 + len("分析结果".encode("utf-8"))
    assert sw.workspace_disk_usage(999) == 0


# ── W6 工具组注册与 fail-closed ────────────────────────────────────


def test_assets_tool_group_registered():
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_BUILDERS, TOOL_SERVER_TOOLS
    assert "assets" in TOOL_SERVER_BUILDERS
    assert TOOL_SERVER_TOOLS["assets"][0] == "datahub_assets"
    assert set(TOOL_SERVER_TOOLS["assets"][1]) == {
        "asset_list", "asset_get", "asset_archive", "disk_status",
        "cleanup_candidates", "cleanup_execute",
    }


def test_asset_tools_fail_closed_without_context(monkeypatch):
    monkeypatch.setattr(asset_tools, "_ctx_ids", lambda: (0, 0, 0))
    for fn in (asset_tools.asset_list, asset_tools.disk_status_tool, asset_tools.cleanup_candidates_tool):
        out = asyncio.run(fn({}))
        text = _text_of(out)
        assert "error" in text


def test_asset_list_works_without_workspace_context(env, monkeypatch):
    """资产跟随用户: 无工作空间上下文也能列清单(磁盘/清理仍需工作空间上下文)。"""
    ua_api.archive_session_file(9, 5, "report.txt", "周报")
    monkeypatch.setattr(asset_tools, "_ctx_ids", lambda: (9, 0, 0))
    out = asyncio.run(asset_tools.asset_list({}))
    text = _text_of(out)
    assert "周报" in text
    out2 = asyncio.run(asset_tools.disk_status_tool({}))
    assert "error" in _text_of(out2)


# ── W7 下载响应头(中文文件名) ─────────────────────────────────────


def test_download_asset_chinese_filename_header(env):
    """中文文件名下载: Content-Disposition 须 RFC 5987 编码, 不得 latin-1 编码失败(500)。"""
    from urllib.parse import unquote
    (env["root"] / "workspace" / "季度报告.txt").write_text("内容", encoding="utf-8")
    asset = ua_api.archive_session_file(9, 5, "季度报告.txt", "季度报告")
    resp = ua_api.download_asset_endpoint(asset["id"], user={"user_id": 9, "role": "admin"})
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")  # 响应头不得抛 UnicodeEncodeError
    assert "季度报告" not in cd, "头部不得裸含非 latin-1 字符"
    assert "filename*=UTF-8''" in cd
    assert unquote(cd.split("filename*=UTF-8''", 1)[1]) == "季度报告.txt"
    assert resp.body == "内容".encode("utf-8")


def test_download_asset_ascii_filename_header(env):
    asset = ua_api.archive_session_file(9, 5, "report.txt", "周报")
    resp = ua_api.download_asset_endpoint(asset["id"], user={"user_id": 9, "role": "admin"})
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")
    assert 'filename="report.txt"' in cd
    assert "filename*=UTF-8''report.txt" in cd


# ── W8 资产跟随用户(跨工作空间/越权隔离) ──────────────────────────


def test_assets_follow_user_across_workspaces(env):
    """同一用户在 A 工作空间归档的资产, B 工作空间上下文同样可见可下载。"""
    a1 = ua_api.archive_session_file(9, 5, "report.txt", "周报")     # ws 3
    a2 = ua_api.archive_session_file(9, 7, "实验.txt", "实验记录")   # ws 8
    items = ua_api.list_user_assets(9)
    assert {i["id"] for i in items} == {a1["id"], a2["id"]}
    by_id = {i["id"]: i for i in items}
    assert by_id[a1["id"]]["origin_workspace_name"] == "分析工作站"
    assert by_id[a2["id"]]["origin_workspace_name"] == "实验空间"
    # 下载端点不带工作空间维度, 归属只看 user_id
    resp = ua_api.download_asset_endpoint(a2["id"], user={"user_id": 9, "role": "admin"})
    assert resp.body == "实验".encode("utf-8")


def test_assets_isolated_between_users(env):
    """他人资产 fail-closed: 列表为空, 下载/删除 404。"""
    from fastapi import HTTPException
    asset = ua_api.archive_session_file(9, 5, "report.txt", "周报")
    assert ua_api.list_user_assets(10) == []
    with pytest.raises(HTTPException) as exc:
        ua_api.download_asset_endpoint(asset["id"], user={"user_id": 10, "role": "admin"})
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        ua_api.delete_asset_endpoint(asset["id"], user={"user_id": 10, "role": "admin"})
    assert exc.value.status_code == 404
    assert env["db"]["assets"], "他人不得删除"


def test_delete_asset_removes_object(env):
    asset = ua_api.archive_session_file(9, 5, "report.txt", "周报")
    key = env["db"]["assets"][0]["object_key"]
    ua_api.delete_asset_endpoint(asset["id"], user={"user_id": 9, "role": "admin"})
    assert not env["db"]["assets"]
    assert key in env["storage"].deleted


# ── W9 存量迁移: 旧 key 对象搬迁(幂等) ────────────────────────────


def test_migrate_legacy_objects_to_user_prefix(env, monkeypatch):
    old_key = f"workspaces/3/assets/{'a' * 32}_报告.txt"
    env["db"]["assets"].append({"id": "a" * 32, "user_id": 9, "origin_workspace_id": 3,
                                "name": "报告", "filename": "报告.txt", "category": "report",
                                "object_key": old_key, "storage_type": "object", "size": 3,
                                "source_conversation_id": 5, "source_path": "报告.txt", "created_by": 9})
    env["storage"].blobs[old_key] = "旧对象".encode("utf-8")
    monkeypatch.setattr(mig, "get_metadata_conn", lambda: FakeConn(env["db"]))
    monkeypatch.setattr(mig, "get_object_storage", lambda: env["storage"])

    assert mig.migrate() == 0
    row = env["db"]["assets"][0]
    new_key = f"ai-datahub/9/assets/{'a' * 32}_报告.txt"
    assert row["object_key"] == new_key
    assert env["storage"].blobs[new_key] == "旧对象".encode("utf-8")
    assert old_key not in env["storage"].blobs
    # 幂等: 重跑无剩余旧 key, 不再搬运
    assert mig.migrate() == 0
    assert env["storage"].blobs[new_key] == "旧对象".encode("utf-8")


def test_migrate_reports_missing_object_as_failure(env, monkeypatch):
    """旧对象不可读必须显式失败(退出码 1), 不静默跳过(no-silent-degradation)。"""
    old_key = f"workspaces/3/assets/{'b' * 32}_丢失.txt"
    env["db"]["assets"].append({"id": "b" * 32, "user_id": 9, "origin_workspace_id": 3,
                                "name": "丢失", "filename": "丢失.txt", "category": "file",
                                "object_key": old_key, "storage_type": "object", "size": 0,
                                "source_conversation_id": 0, "source_path": "", "created_by": 9})
    monkeypatch.setattr(mig, "get_metadata_conn", lambda: FakeConn(env["db"]))
    monkeypatch.setattr(mig, "get_object_storage", lambda: env["storage"])
    assert mig.migrate() == 1
    assert env["db"]["assets"][0]["object_key"] == old_key, "失败行保留旧 key 待重跑"
