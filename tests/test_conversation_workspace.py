"""会话归属回归：未指定工作空间解析为用户默认工作空间，不再落全局(ws=0)。"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.modules.mind.api import chat
from backend.modules.mind.execution import tool_policy
from backend.common.db import metadata_db


class _FakeCursor:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows
        self.lastrowid = 999

    def execute(self, sql, params=None):
        self.log.append((sql, tuple(params) if params else ()))

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


class _FakeConn:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self.log, self.rows)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def make_env(monkeypatch):
    """假 DB(rows 按 SQL 关键词分派返回行) + 桩掉授权与策略解析。"""
    def _make(rows):
        log: list = []
        monkeypatch.setattr(metadata_db, "get_metadata_conn", lambda: _FakeConn(log, rows))
        monkeypatch.setattr(chat, "authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(
            tool_policy, "resolve_policy",
            lambda ctx, *a, **k: SimpleNamespace(as_bot={"as_bot_key": "bot"}))
        return log
    return _make


def test_create_conversation_resolves_default_workspace(make_env):
    """未指定工作空间 → 归属用户默认工作空间(个人工作站)，不再落全局 ws=0。"""
    log = make_env({"is_default=1": {"id": 42}})
    resp = chat.create_conversation(chat.CreateConversationRequest(), user={"user_id": 7})
    assert resp["workspace_id"] == 42
    insert_sql, params = log[-1]
    assert "INSERT INTO adh_conversations" in insert_sql
    assert params[3] == 42


def test_create_conversation_falls_back_to_owned_workstation(make_env):
    """无 is_default 标记时取属主最早的工作站(确定性保底口径)。"""
    log = make_env({"ORDER BY id": {"id": 8}})
    resp = chat.create_conversation(chat.CreateConversationRequest(workspace_id=0), user={"user_id": 7})
    assert resp["workspace_id"] == 8
    _, params = log[-1]
    assert params[3] == 8


def test_create_conversation_keeps_explicit_workspace(make_env):
    """显式指定工作空间时沿用，不触发默认解析。"""
    log = make_env({})
    resp = chat.create_conversation(chat.CreateConversationRequest(workspace_id=7), user={"user_id": 7})
    assert resp["workspace_id"] == 7
    assert not any("adh_workspaces" in sql for sql, _ in log)
    _, params = log[-1]
    assert params[3] == 7


def test_create_conversation_fails_loud_without_workspace(make_env):
    """用户无任何工作空间 → 显式报错(fail-loud)，绝不落回全局 ws=0。"""
    log = make_env({})
    with pytest.raises(HTTPException) as exc:
        chat.create_conversation(chat.CreateConversationRequest(), user={"user_id": 7})
    assert exc.value.status_code == 422
    assert not any("INSERT INTO adh_conversations" in sql for sql, _ in log)
