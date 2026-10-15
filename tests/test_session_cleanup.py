"""会话删除回归：目录/存储卷缺失不阻断删除（目录已不存在即无残留，删除无妨碍）。"""
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.modules.mind.execution import session_workspace as sw


def _session(**over):
    key = uuid.uuid4().hex
    row = {
        "session_key": key,
        "workspace_id": 3,
        "user_id": 7,
        "status": "idle",
        "execution_token": None,
        "storage_node": "a" * 32,
        "relative_dir": f"7/workspace/3/sessions/{key}",
    }
    row.update(over)
    return row


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """把 ADH_WORKSPACES_DIR 指到临时目录；exists=False 模拟存储卷未挂载。"""
    def _set(exists=True):
        root = tmp_path / "workspaces"
        if exists:
            root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("backend.common.config.ADH_WORKSPACES_DIR", str(root))
        return root
    return _set


def test_cleanup_returns_none_when_volume_missing(workspaces):
    """存储卷目录整体不存在 → 不再报 503，视为无残留可清理(本次修复)。"""
    workspaces(exists=False)
    assert sw._session_cleanup_root(_session()) is None


def test_cleanup_returns_none_when_session_dir_missing(workspaces):
    root = workspaces(exists=True)
    assert sw._session_cleanup_root(_session()) is None
    assert not (root / "7").exists()  # 不创建任何目录/标记文件


def test_cleanup_rejects_foreign_storage_node(workspaces):
    """目录存在但属于其它存储节点 → 仍阻断(跨节点误删保护不变)。"""
    root = workspaces(exists=True)
    sess = _session()
    (root / sess["relative_dir"]).mkdir(parents=True)
    (root / ".storage-node").write_text("b" * 32)
    with pytest.raises(HTTPException) as exc:
        sw._session_cleanup_root(sess)
    assert exc.value.status_code == 409
    assert "存储节点" in exc.value.detail


def test_cleanup_returns_root_on_same_node(workspaces):
    root = workspaces(exists=True)
    sess = _session(storage_node="c" * 32)
    (root / sess["relative_dir"]).mkdir(parents=True)
    (root / ".storage-node").write_text("c" * 32)
    assert sw._session_cleanup_root(sess) == root / sess["relative_dir"]


def test_cleanup_rejects_running_session(workspaces):
    workspaces(exists=False)
    with pytest.raises(HTTPException) as exc:
        sw._session_cleanup_root(_session(status="running", execution_token="t"))
    assert exc.value.status_code == 409


# ── 执行权残留自愈（证据口径）─────────────────────────────────

def _reclaim_fixture(monkeypatch, *, owner_start=999, sdk_alive=False, proc_hits=()):
    """process_identity 恒回 999：仅当记录值==999 才算存活。"""
    monkeypatch.setattr(sw, "process_identity", lambda pid: 999)
    monkeypatch.setattr(sw, "_proc_holds_session", lambda key: list(proc_hits))
    monkeypatch.setattr(sw, "_remove_token_sandbox", lambda token: None)
    return _session(status="running", execution_token="a" * 32,
                    owner_pid=1, owner_start=owner_start,
                    sdk_pid=None, sdk_start=None)


def test_reclaim_dead_execution_recovers(monkeypatch):
    """owner/sdk 均退出且无进程证据: running 残留 → interrupted, 删除不再卡死。"""
    sess = _reclaim_fixture(monkeypatch, owner_start=1)   # 1 != 999 = owner 已死
    writes = []
    assert sw._reclaim_dead_execution(sess, write=lambda sql, p: writes.append((sql, p)) or 1)
    assert writes and "interrupted" in writes[0][0]


def test_reclaim_blocks_when_owner_alive(monkeypatch):
    """宿主仍存活: 绝不回收(不误杀活执行)。"""
    sess = _reclaim_fixture(monkeypatch, owner_start=999)  # 999 == 999 = 存活
    writes = []
    assert not sw._reclaim_dead_execution(sess, write=lambda sql, p: writes.append(1) or 1)
    assert not writes


def test_reclaim_blocks_when_process_holds(monkeypatch):
    """/proc 发现持有会话 key 的活进程(孤儿 SDK): 保持阻断。"""
    sess = _reclaim_fixture(monkeypatch, owner_start=1, proc_hits=[4242])
    writes = []
    assert not sw._reclaim_dead_execution(sess, write=lambda sql, p: writes.append(1) or 1)
    assert not writes


class _Cursor:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.log.append((sql, tuple(params or ())))

    def fetchone(self):
        sql = self.log[-1][0]
        for key, row in self.rows.items():
            if key in sql:
                return row
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows

    def begin(self):
        pass

    def commit(self):
        pass

    def cursor(self):
        return _Cursor(self.log, self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_delete_conversation_proceeds_without_volume(workspaces, monkeypatch):
    """端到端：存储卷缺失时删除会话不报错，映射与会话记录正常删除。"""
    workspaces(exists=False)
    sess = _session()
    conv = {"id": 11, "user_id": 7, "workspace_id": 3}
    log: list = []
    rows = {"FROM adh_conversations": conv, "FROM adh_agent_sessions": sess}
    monkeypatch.setattr(sw, "DBConnection", lambda: _Conn(log, rows))

    assert sw.delete_conversation_workspace(11, 7) == {"success": True}
    deleted = [sql for sql, _ in log if sql.startswith("DELETE")]
    assert any("adh_agent_sessions" in sql for sql in deleted)
    assert any("adh_conversations" in sql for sql in deleted)
