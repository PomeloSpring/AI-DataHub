"""云空间会话产物上云 —— 离线单测（假对象存储 + 临时工作区，不触碰真实库/桶）。

覆盖：
  C1  推送：新增/改动文件上传、本地已删的对象跟随删除、增量清单落 runtime；
  C2  开关：用户关闭自动上云后不再推送（默认开启的口径由偏好读取函数自身负责）；
  C3  未配置对象存储：不上传，且显式给出原因（不往本地回退目录假装“已同步”）；
  C4  单文件超限：跳过上传并留可诊断原因，不静默截断；
  C5  回落：本地缺失 + 云端有副本 → 标准布局重建、文件回位、执行映射换节点并丢弃旧 resume；
  C6  回落不可得：云端确实没有副本时返回 False 且不建空目录（让调用方给出可操作提示）；
  C7  同步标记：以云端实际清单为准（大小不符=未同步），云端不可读时一律未同步并带原因；
  C8  删除会话/空间：云端副本一并清除；
  C9  路径安全：云端对象名里的 `..`/绝对路径一律拒绝（防回落时被构造路径逃逸）；
  C10 回落后的清单：下一轮推送不重复上传已一致的文件。
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

sc = importlib.import_module("backend.modules.mind.execution.session_cloud")
sw = importlib.import_module("backend.modules.mind.execution.session_workspace")
object_storage = importlib.import_module("backend.common.object_storage")

USER_ID = 7
WS_ID = 3
KEY = "a" * 32
PREFIX = f"ai-datahub/{USER_ID}/sessions/{KEY}/workspace"


class FakeStorage:
    """内存对象存储：key → bytes（is_object_storage 可按用例切换）。"""

    def __init__(self, enabled=True):
        self.objects = {}
        self.is_object_storage = enabled
        self.list_raises = False
        self.upload_raises = False
        self.deleted_prefixes = []

    def list_keys(self, prefix):
        if self.list_raises:
            raise RuntimeError("bucket unreachable")
        return [{"key": k, "size": len(v), "etag": ""}
                for k, v in sorted(self.objects.items()) if k.startswith(prefix)]

    def upload_file(self, key, path, content_type="application/octet-stream"):
        if self.upload_raises:
            raise RuntimeError("put failed")
        self.objects[key] = open(path, "rb").read()

    def upload_bytes(self, key, data, content_type=""):
        self.objects[key] = data

    def download_to_file(self, key, path):
        if key not in self.objects:
            return False
        p = __import__("pathlib").Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.objects[key])
        return True

    def delete(self, key):
        return self.objects.pop(key, None) is not None

    def delete_prefix(self, prefix):
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            self.objects.pop(k)
        self.deleted_prefixes.append(prefix)
        return len(keys)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时持久卷 + 假对象存储 + 默认开启的自动上云。"""
    monkeypatch.setattr("backend.common.config.ADH_WORKSPACES_DIR", str(tmp_path))
    fake = FakeStorage()
    monkeypatch.setattr(object_storage, "get_object_storage", lambda: fake)
    monkeypatch.setattr(sc, "cloud_sync_enabled", lambda user_id: True)
    root = sw.session_paths(tmp_path, KEY, WS_ID, USER_ID, create=True)
    return {"base": tmp_path, "root": root, "ws": root / "workspace", "storage": fake}


def _put(env, rel, data):
    target = env["ws"] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


# ── C1 推送：增量上传 + 删除跟随 + 清单落盘 ─────────────────────────────────
def test_sync_uploads_new_and_deletes_removed(env):
    _put(env, "uploads/原始表.xlsx", b"A")
    _put(env, "report.html", b"<html>")

    first = sc.sync_session(env["root"], USER_ID, KEY)
    assert first["uploaded"] == 2 and first["skipped"] == 0
    assert set(env["storage"].objects) == {f"{PREFIX}/uploads/原始表.xlsx", f"{PREFIX}/report.html"}
    assert (env["root"] / "runtime" / sc.MANIFEST_NAME).is_file()

    # 未改动的第二轮：没有新上传（清单按 size+mtime 判增量）
    again = sc.sync_session(env["root"], USER_ID, KEY)
    assert again["uploaded"] == 0

    # 改动 → 重传；本地删除 → 云端对象跟随删除（镜像语义）
    (env["ws"] / "report.html").write_bytes(b"<html>2")
    (env["ws"] / "uploads/原始表.xlsx").unlink()
    third = sc.sync_session(env["root"], USER_ID, KEY)
    assert third["uploaded"] == 1 and third["deleted"] == 1
    assert list(env["storage"].objects) == [f"{PREFIX}/report.html"]


# ── C2 开关：关闭后不推送 ────────────────────────────────────────────────────
def test_sync_skipped_when_user_disabled(env, monkeypatch):
    monkeypatch.setattr(sc, "cloud_sync_enabled", lambda user_id: False)
    _put(env, "a.csv", b"1,2")
    result = sc.sync_session(env["root"], USER_ID, KEY)
    assert result["uploaded"] == 0 and "关闭" in result["note"]
    assert env["storage"].objects == {}


# ── C3 未配置对象存储：不上云并说明原因 ──────────────────────────────────────
def test_sync_reports_unconfigured_object_storage(env):
    env["storage"].is_object_storage = False
    _put(env, "a.csv", b"1,2")
    result = sc.sync_session(env["root"], USER_ID, KEY)
    assert result["uploaded"] == 0 and "对象存储未配置" in result["note"]


# ── C4 单文件超限：跳过并留原因（不静默截断）────────────────────────────────
def test_sync_skips_oversized_file(env, monkeypatch):
    monkeypatch.setattr(sc, "_max_file_bytes", lambda: 4)
    _put(env, "big.parquet", b"123456789")
    _put(env, "small.txt", b"ok")
    result = sc.sync_session(env["root"], USER_ID, KEY)
    assert result["uploaded"] == 1 and result["skipped"] == 1
    assert list(env["storage"].objects) == [f"{PREFIX}/small.txt"]
    assert "未上云" in result["note"]


# ── C5 回落：本地缺失 + 云端有副本 ───────────────────────────────────────────
def test_ensure_local_restores_from_cloud(env):
    _put(env, "uploads/数据.xlsx", b"WORK")
    sc.sync_session(env["root"], USER_ID, KEY)

    # 模拟“换节点/卷被清理”：库里有会话记录，本地目录整体消失
    import shutil
    shutil.rmtree(env["root"])
    updates = []

    def fake_write(sql, params):
        updates.append((sql, params))
        return 1

    assert sc.ensure_local_workspace(
        {"session_key": KEY, "workspace_id": WS_ID, "user_id": USER_ID}, write=fake_write)
    assert (env["ws"] / "uploads/数据.xlsx").read_bytes() == b"WORK"
    # 标准布局齐备（runtime/ 也要有，否则后续校验判“会话工作区不可用”）
    assert (env["root"] / "runtime").is_dir() and (env["root"] / "runtime" / "tmp").is_dir()
    sql, params = updates[0]
    assert "storage_node=%s" in sql and "sdk_session_id=''" in sql
    assert params[1:] == (KEY, USER_ID)     # 换到当前存储节点 + 丢弃不在这台机器上的 resume 会话


# ── C6 云端没有副本：不建空目录假装恢复成功 ─────────────────────────────────
def test_ensure_local_false_without_cloud_copy(env):
    import shutil
    shutil.rmtree(env["root"])
    assert sc.ensure_local_workspace(
        {"session_key": KEY, "workspace_id": WS_ID, "user_id": USER_ID},
        write=lambda sql, params: 1) is False
    assert not env["root"].exists()


def test_ensure_local_false_when_object_storage_absent(env):
    import shutil
    env["storage"].is_object_storage = False
    shutil.rmtree(env["root"])
    assert sc.ensure_local_workspace(
        {"session_key": KEY, "workspace_id": WS_ID, "user_id": USER_ID},
        write=lambda sql, params: 1) is False


def test_ensure_local_true_when_directory_present(env):
    _put(env, "a.txt", b"x")
    writes = []
    assert sc.ensure_local_workspace(
        {"session_key": KEY, "workspace_id": WS_ID, "user_id": USER_ID},
        write=lambda sql, params: writes.append(sql) or 1)
    assert writes == []   # 本地已可用 → 不动登记


# ── C7 同步标记：按云端实际清单 ─────────────────────────────────────────────
def test_sync_status_marks_per_file_synced(env):
    _put(env, "a.csv", b"1,2")
    _put(env, "b.csv", b"9")
    sc.sync_session(env["root"], USER_ID, KEY)
    (env["ws"] / "b.csv").write_bytes(b"999")           # 本地改动未推
    files = [{"path": "a.csv", "size": 3}, {"path": "b.csv", "size": 3}]

    status = sc.sync_status(USER_ID, KEY, files)
    assert status["configured"] and status["enabled"]
    assert {f["path"]: f["synced"] for f in files} == {"a.csv": True, "b.csv": False}


def test_sync_status_unreadable_cloud_marks_all_unsynced(env):
    _put(env, "a.csv", b"1,2")
    env["storage"].list_raises = True
    files = [{"path": "a.csv", "size": 3}]
    status = sc.sync_status(USER_ID, KEY, files)
    assert files[0]["synced"] is False and "不可读" in status["reason"]


def test_sync_status_unconfigured(env):
    env["storage"].is_object_storage = False
    files = [{"path": "a.csv", "size": 3}]
    status = sc.sync_status(USER_ID, KEY, files)
    assert status["configured"] is False and files[0]["synced"] is False


# ── C8 云端副本随会话/空间删除 ──────────────────────────────────────────────
def test_purge_session_cloud_deletes_prefix(env):
    _put(env, "a.csv", b"1,2")
    sc.sync_session(env["root"], USER_ID, KEY)
    assert sc.purge_session_cloud(USER_ID, KEY) == 1
    assert env["storage"].deleted_prefixes == [PREFIX + "/"]
    assert env["storage"].objects == {}


def test_purge_session_cloud_noop_without_object_storage(env):
    env["storage"].is_object_storage = False
    assert sc.purge_session_cloud(USER_ID, KEY) == 0


# ── C9 路径安全 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["../escape.txt", "a/../../b.txt", "..\\escape.txt/../x"])
def test_safe_rel_path_rejects_traversal(raw):
    with pytest.raises(ValueError):
        sc.safe_rel_path(raw)


def test_safe_rel_path_normalizes():
    assert sc.safe_rel_path("./uploads/月报.xlsx") == "uploads/月报.xlsx"
    assert sc.safe_rel_path("/workspace/uploads/a.txt") == "workspace/uploads/a.txt"
    # 前导 / 只是被当相对路径落回工作区内（对象名本就带自己的前缀），不会逃到绝对路径
    assert sc.safe_rel_path("/etc/passwd") == "etc/passwd"


def test_build_session_prefix_requires_valid_ids():
    with pytest.raises(ValueError):
        sc.build_session_prefix(0, KEY)
    with pytest.raises(ValueError):
        sc.build_session_prefix(USER_ID, "NOT-A-KEY")


# ── C10 回落写入的清单让下一轮不重复上传 ────────────────────────────────────
def test_restore_writes_manifest_so_next_push_is_noop(env):
    _put(env, "a.csv", b"1,2")
    sc.sync_session(env["root"], USER_ID, KEY)
    import shutil
    shutil.rmtree(env["root"])
    sc.ensure_local_workspace({"session_key": KEY, "workspace_id": WS_ID, "user_id": USER_ID},
                              write=lambda sql, params: 1)
    before = dict(env["storage"].objects)
    assert sc.sync_session(env["root"], USER_ID, KEY)["uploaded"] == 0
    assert env["storage"].objects == before


# ── 接线：读路径与回合收尾都走云空间 ────────────────────────────────────────
def test_teardown_syncs_artifacts_after_turn(monkeypatch):
    """回合结束在后台镜像产物；上云失败不得连带卡住执行权释放（只留日志）。"""
    import inspect
    from backend.modules.mind.execution import secure_sdk

    assert "_sync_turn_files" in inspect.getsource(secure_sdk._teardown_client)

    logged = []
    monkeypatch.setattr(secure_sdk.logger, "exception",
                        lambda *a, **k: logged.append(a))
    monkeypatch.setattr(sc, "sync_session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bucket down")))
    runtime = type("R", (), {"root": "/tmp/x", "key": KEY})()
    secure_sdk._sync_turn_files(runtime, USER_ID)      # 不得抛出
    assert logged, "上云失败必须落服务端日志"
