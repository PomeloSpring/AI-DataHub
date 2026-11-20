"""会话文件清单：新建会话首轮尚未 claim 时不是 404（离线单测，假 DB，不触碰真实库）。

缺陷由来：会话行（`adh_conversations`）在建会话时就落库，但执行映射行
（`adh_agent_sessions`，工作区/产物都挂在它下面）要等首轮 claim 才写入。
右侧会话侧栏在“建会话后、执行前”就已挂载并拉文件清单，旧实现直接抛
404「会话不存在或无权访问」并把红框端给用户 —— 实际只是“还没有工作区/还没有文件”。

口径：
  P1 会话存在但无执行映射 → 空清单（不是 404）；
  P2 会话不存在 / 不属本人 → 仍 404（不泄露他人会话存在性）；
  P3 写路径（删除文件）保持严格：没有工作区就没有可操作目标，不做“pending 放行”。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

from backend.modules.mind.api import user_assets

WORKSPACE = 3
CONV = 55
USER = 7


class _FakeCursor:
    def __init__(self, log, session_row, conversation_row):
        self.log = log
        self.session_row = session_row
        self.conversation_row = conversation_row
        self._sql = ""

    def execute(self, sql, params=None):
        self._sql = str(sql)
        self.log.append((self._sql, params))

    def fetchone(self):
        if "adh_agent_sessions" in self._sql:
            return self.session_row
        if "adh_conversations" in self._sql:
            return self.conversation_row
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, log, session_row, conversation_row):
        self.log = log
        self.session_row = session_row
        self.conversation_row = conversation_row

    def cursor(self):
        return _FakeCursor(self.log, self.session_row, self.conversation_row)

    def close(self):
        pass


def _patch_db(monkeypatch, session_row, conversation_row):
    log: list = []
    monkeypatch.setattr(user_assets, "get_metadata_conn",
                        lambda: _FakeConn(log, session_row, conversation_row))
    return log


def test_pending_conversation_lists_empty_instead_of_404(monkeypatch):
    """P1：会话已建、执行尚未 claim → 空清单（侧栏显示“还没有文件”，不是“会话不存在”）。"""
    _patch_db(monkeypatch, session_row=None, conversation_row={"id": CONV})
    assert user_assets.list_session_files(WORKSPACE, CONV, USER) == []


def test_unknown_or_foreign_conversation_still_404(monkeypatch):
    """P2：会话行也查不到（不存在/他人/已删） → 仍然 404，不因上面那条例外而放行。"""
    _patch_db(monkeypatch, session_row=None, conversation_row=None)
    with pytest.raises(HTTPException) as exc:
        user_assets.list_session_files(WORKSPACE, CONV, USER)
    assert exc.value.status_code == 404


def test_delete_path_stays_strict_without_session(monkeypatch):
    """P3：写路径不享受 pending 例外 —— 没有工作区就拒绝，不假装删成功。"""
    _patch_db(monkeypatch, session_row=None, conversation_row={"id": CONV})
    with pytest.raises(HTTPException) as exc:
        user_assets._locate_session_file_root(WORKSPACE, CONV, USER)
    assert exc.value.status_code == 404
