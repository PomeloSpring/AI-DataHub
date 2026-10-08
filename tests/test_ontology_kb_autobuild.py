"""知识库 notebook 自动重建(ensure_notebook)行为契约.

场景: 切换 Qoder 账号后, 绑定的 notebook 对当前凭据不可用(403/不存在),
需在当前账号自动重建知识库并回写绑定 —— 知识库内容是本项目同步的派生视图, 重建无损。

锁死的边界:
1. 仅「无权/不存在」类错误触发重建; 网络抖动/5xx 不触发(防误建);
2. 重建走 MySQL GET_LOCK 分布式互斥 + 锁内复查(并发只建一个, 不用进程内防重);
3. create 失败必须显式报错(fail-loud), 不静默吞掉;
4. probe 可用时走快路径, 绝不创建。
"""
import json

import pytest

from backend.modules.catalog.services import ontology_kb_sync as oks
from backend.modules.mind.rag import qmind_retriever as qr


class _FakeCursor:
    def __init__(self, row=None, lock_ok=True):
        self.row = row or {}
        self.lock_ok = lock_ok
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        sql = self.executed[-1][0]
        if "GET_LOCK" in sql:
            return {"GET_LOCK(x)": 1 if self.lock_ok else 0}
        return self.row


class _FakeConn:
    def __init__(self, cursor):
        self._cur = cursor
        self.committed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _patch(monkeypatch, *, probe=(True, ""), create="nb-new", row=None, lock_ok=True):
    calls = {"create": 0, "probe": 0}
    cur = _FakeCursor(row=row, lock_ok=lock_ok)
    probe_seq = list(probe) if isinstance(probe, list) else None

    def fake_probe(nb):
        calls["probe"] += 1
        if probe_seq is not None:
            return probe_seq.pop(0) if probe_seq else (True, "")
        return probe

    def fake_create(title, description=""):
        calls["create"] += 1
        return create

    monkeypatch.setattr(qr, "probe_notebook", fake_probe)
    monkeypatch.setattr(qr, "create_notebook", fake_create)
    import backend.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _FakeConn(cur))
    return calls, cur


KB = {"id": 5, "name": "AI-DataHub 系统知识库",
      "cfg": {"notebook_id": "nb-old", "sync_ontology": True}}
ROW = {"name": "AI-DataHub 系统知识库",
       "source_config": json.dumps({"notebook_id": "nb-old", "sync_ontology": True})}


def test_probe_ok_keeps_binding(monkeypatch):
    """快路径: notebook 可达 → 原样返回, 绝不创建。"""
    calls, cur = _patch(monkeypatch, probe=(True, ""))
    nb, err = oks.ensure_notebook(KB)
    assert nb == "nb-old" and err == ""
    assert calls["create"] == 0
    assert not any("UPDATE" in s for s, _ in cur.executed)


def test_forbidden_triggers_rebuild(monkeypatch):
    """403/无权 → 当前账号重建 + 回写绑定(source_config 可审计)。"""
    calls, cur = _patch(
        monkeypatch,
        probe=(False, 'error: server returned 403: {"errorCode":"Forbidden",'
                      '"errorMessage":"insufficient notebook permission"}'),
        create="nb-new", row=ROW,
    )
    nb, err = oks.ensure_notebook(KB)
    assert nb == "nb-new" and err == ""
    assert calls["create"] == 1
    updates = [(s, p) for s, p in cur.executed if s.startswith("UPDATE")]
    assert len(updates) == 1
    cfg = json.loads(updates[0][1][0])
    assert cfg["notebook_id"] == "nb-new"
    assert cfg["previous_notebook_id"] == "nb-old"   # 重建事实留痕
    assert cfg["rebuilt_at"] and cfg["sync_ontology"] is True


def test_network_error_does_not_rebuild(monkeypatch):
    """网络/服务端异常不触发重建(防故障期误建), 维持原绑定并透出错误。"""
    calls, cur = _patch(monkeypatch, probe=(False, "timeout"), create="nb-new", row=ROW)
    nb, err = oks.ensure_notebook(KB)
    assert nb == "nb-old" and err == "timeout"
    assert calls["create"] == 0
    assert not any("UPDATE" in s for s, _ in cur.executed)


def test_lock_inner_recheck_avoids_duplicate(monkeypatch):
    """锁内复查: 并发已重建(库中绑定已变且可达) → 返回新值, 不重复创建。"""
    calls, _cur = _patch(
        monkeypatch,
        probe=[(False, "insufficient notebook permission"), (True, "")],  # 第二次probe=复查
        create="nb-should-not-be-used",
        row={"name": "kb", "source_config": json.dumps({"notebook_id": "nb-by-other"})},
    )
    # probe 顺序调用: 首次探测旧 nb 失败 → 锁内复查新 nb 成功
    nb, err = oks.ensure_notebook(KB)
    assert nb == "nb-by-other" and err == ""
    assert calls["create"] == 0


def test_create_failure_is_loud(monkeypatch):
    """创建失败必须显式返回错误, 不得静默当成功。"""
    calls, _cur = _patch(monkeypatch,
                         probe=(False, "insufficient notebook permission"),
                         create=None, row=ROW)
    nb, err = oks.ensure_notebook(KB)
    assert nb is None
    assert "自动重建知识库失败" in err
    assert calls["create"] == 1


def test_unavailable_error_classification():
    assert qr.is_unavailable_error("403 insufficient notebook permission")
    assert qr.is_unavailable_error('{"errorCode":"Forbidden"}')
    assert qr.is_unavailable_error("notebook not found")
    assert not qr.is_unavailable_error("timeout")
    assert not qr.is_unavailable_error("server returned 500")
    assert not qr.is_unavailable_error("")


def test_unbound_notebook_fails_loud(monkeypatch):
    calls, _cur = _patch(monkeypatch)
    nb, err = oks.ensure_notebook({"id": 9, "name": "x", "cfg": {}})
    assert nb is None and "未绑定" in err
    assert calls["create"] == 0
