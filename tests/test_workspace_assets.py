"""工作空间资产清单(OSS 托管) —— 离线单测(不触碰真实数据库/对象存储)。

覆盖:
  W1  归档: 会话产物 → 对象存储 + 清单落库; delete_source 清理本地产物;
  W2  两步确认: asset_archive/cleanup_execute confirm=false 仅预览不落盘;
  W3  清理候选: 已归档来源文件 + 已关闭会话目录(活跃会话不入候选);
  W4  磁盘配额拦截: 用量达配额拒绝新建会话(409);
  W5  磁盘用量统计: 递归求和(不计符号链接);
  W6  LLM 工具组: assets 六工具注册 + 无上下文 fail-closed。
  W7  下载响应头: 中文文件名按 RFC 5987 编码(不抛 latin-1 编码错)。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib
from pathlib import Path

import pytest

wa_api = importlib.import_module("services.datamind.api.workspace_assets")
sw = importlib.import_module("services.datamind.execution.session_workspace")
asset_tools = importlib.import_module("services.datamind.execution.sdk_tools.asset_tools")


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

    def delete(self, key):
        self.deleted.append(key)
        return self.blobs.pop(key, None) is not None


class FakeCursor:
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
        if "FROM adh_agent_sessions" in s and "conversation_id=%s" in s:
            self._rows = [dict(r) for r in self.db["sessions"]
                          if r.get("conversation_id") == p[0] and r.get("user_id") == p[1]
                          and r.get("workspace_id") == p[2]]
        elif "FROM adh_agent_sessions" in s:
            self._rows = [dict(r) for r in self.db["sessions"] if r.get("workspace_id") == p[0]]
        elif "INSERT INTO adh_workspace_assets" in s:
            self.db["assets"].append({"id": p[0], "workspace_id": p[1], "name": p[2], "filename": p[3],
                                      "category": p[4], "object_key": p[5], "storage_type": p[6],
                                      "size": p[7], "source_conversation_id": p[8], "created_by": p[9]})
            self._rows = []
        elif "FROM adh_workspace_assets WHERE workspace_id=" in s and "source_conversation_id" in s:
            self._rows = [dict(a) for a in self.db["assets"]
                          if a["workspace_id"] == p[0] and a["source_conversation_id"] > 0]
        elif "FROM adh_workspace_assets WHERE workspace_id=" in s:
            self._rows = [dict(a) for a in self.db["assets"] if a["workspace_id"] == p[0]]
        elif "FROM adh_workspace_assets WHERE id=" in s:
            self._rows = [dict(a) for a in self.db["assets"]
                          if a["id"] == p[0] and a["workspace_id"] == p[1]]
        elif "SELECT created_by FROM adh_workspace_assets" in s:
            self._rows = [{"created_by": 9} for _ in range(1)]
        elif "DELETE FROM adh_workspace_assets" in s:
            self.db["assets"] = [a for a in self.db["assets"] if a["id"] != p[0]]
            self._rows = []
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
        "sessions": [{"conversation_id": 5, "user_id": 9, "workspace_id": 3,
                      "session_key": "a" * 32, "status": "idle", "created_at": ""}],
        "assets": [],
        "quota": [{"disk_quota_bytes": 1024}],
    }
    storage = FakeStorage()
    monkeypatch.setattr("services.shared.common.config.ADH_WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setattr("services.shared.common.db.metadata_db.get_metadata_conn", lambda: FakeConn(db))
    monkeypatch.setattr(wa_api, "get_metadata_conn", lambda: FakeConn(db))
    monkeypatch.setattr(wa_api, "get_object_storage", lambda: storage)
    monkeypatch.setattr("services.shared.common.object_storage.get_object_storage", lambda: storage)
    # 建会话目录并放一个产物文件
    root = sw.session_paths(tmp_path, "a" * 32, 3, create=True)
    (root / "workspace" / "report.txt").write_text("分析结果", encoding="utf-8")
    return {"db": db, "storage": storage, "root": root, "tmp": tmp_path}


# ── W1 归档 ────────────────────────────────────────────────────────


def test_archive_upload_and_register(env):
    asset = wa_api.archive_session_file(3, 5, "report.txt", "周报", 9)
    assert asset["filename"] == "report.txt"
    key = asset["object_key"]
    assert key in env["storage"].blobs
    assert env["storage"].blobs[key] == "分析结果".encode("utf-8")
    assert env["db"]["assets"][0]["name"] == "周报"
    assert env["db"]["assets"][0]["source_conversation_id"] == 5
    # 未指定 delete_source 时本地产物保留
    assert (env["root"] / "workspace" / "report.txt").is_file()


def test_archive_delete_source(env):
    wa_api.archive_session_file(3, 5, "report.txt", "", 9, delete_source=True)
    assert not (env["root"] / "workspace" / "report.txt").exists()
    assert env["db"]["assets"], "资产清单已落库(不受本地删除影响)"


def test_archive_rejects_path_escape(env):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        wa_api.archive_session_file(3, 5, "../../etc/passwd", "", 9)
    assert exc.value.status_code in (403, 404)


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
    assert env["db"]["assets"]


def test_cleanup_execute_requires_confirm(env, monkeypatch):
    monkeypatch.setattr(asset_tools, "_ctx_ids", lambda: (9, 3, 5))
    out = asyncio.run(asset_tools.cleanup_execute_tool({"confirm": False}))
    text = _text_of(out)
    assert "confirm_required" in text


# ── W3 清理候选 ────────────────────────────────────────────────────


def test_cleanup_candidates_archived_source_and_closed_sessions(env):
    # 归档后本地仍在 → 来源文件入候选
    wa_api.archive_session_file(3, 5, "report.txt", "周报", 9)
    # closed 会话目录入候选; idle 会话不入
    env["db"]["sessions"].append({"conversation_id": 6, "user_id": 9, "workspace_id": 3,
                                  "session_key": "b" * 32, "status": "closed", "created_at": ""})
    sw.session_paths(env["tmp"], "b" * 32, 3, create=True)
    candidates = wa_api._cleanup_candidates(3)
    kinds = sorted(c["kind"] for c in candidates)
    assert kinds == ["file", "session"]
    file_c = next(c for c in candidates if c["kind"] == "file")
    assert file_c["filename"] == "report.txt"
    session_c = next(c for c in candidates if c["kind"] == "session")
    assert session_c["session_key"] == "b" * 32


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
    from services.datamind.execution.sdk_tools import TOOL_SERVER_BUILDERS, TOOL_SERVER_TOOLS
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


# ── W7 下载响应头(中文文件名) ─────────────────────────────────────


def test_download_asset_chinese_filename_header(env):
    """中文文件名下载: Content-Disposition 须 RFC 5987 编码, 不得 latin-1 编码失败(500)。"""
    from urllib.parse import unquote
    (env["root"] / "workspace" / "季度报告.txt").write_text("内容", encoding="utf-8")
    asset = wa_api.archive_session_file(3, 5, "季度报告.txt", "季度报告", 9)
    resp = wa_api.download_asset_endpoint(3, asset["id"], user={"user_id": 9, "role": "admin"})
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")  # 响应头不得抛 UnicodeEncodeError
    assert "季度报告" not in cd, "头部不得裸含非 latin-1 字符"
    assert "filename*=UTF-8''" in cd
    assert unquote(cd.split("filename*=UTF-8''", 1)[1]) == "季度报告.txt"
    assert resp.body == "内容".encode("utf-8")


def test_download_asset_ascii_filename_header(env):
    asset = wa_api.archive_session_file(3, 5, "report.txt", "周报", 9)
    resp = wa_api.download_asset_endpoint(3, asset["id"], user={"user_id": 9, "role": "admin"})
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")
    assert 'filename="report.txt"' in cd
    assert "filename*=UTF-8''report.txt" in cd
