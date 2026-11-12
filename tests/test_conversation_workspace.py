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
    """未指定工作空间 → 归属用户默认工作空间(个人空间)，不再落全局 ws=0。"""
    log = make_env({"is_default=1": {"id": 42}})
    resp = chat.create_conversation(chat.CreateConversationRequest(), user={"user_id": 7})
    assert resp["workspace_id"] == 42
    insert_sql, params = log[-1]
    assert "INSERT INTO adh_conversations" in insert_sql
    assert params[3] == 42


def test_create_conversation_falls_back_to_owned_workstation(make_env):
    """无 is_default 标记时取属主最早的工作空间(确定性保底口径)。"""
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


# ── 服务端兜底落库（Phase E）──────────────────────────────────────
# 会话 1783069459150：回合跑满 126s、烧掉 12 次模型请求，用户中途断连后前端那次
# PUT 永不到达，`messages=[]` —— 产出只在服务端内存里存在过。落库不再只押注前端，
# 服务端收尾时必须自己兜住这份用户资产；但兜底不能变成"重复写/覆盖用户编辑"。

from split_table_fake import SplitTable, new_state, seed  # noqa: E402


class _FallbackCursor:
    """拆行消息表替身的 cursor 壳：语句处理走共用 :class:`SplitTable`，本层只记 log。

    旧替身按退役列 ``adh_conversations.messages`` 建模：写路径改走拆行表后，
    它既不报错也不落东西，断言全退化成"什么都没发生"的假绿。
    """

    def __init__(self, state, log):
        self.log = log
        self.table = SplitTable(state)

    def execute(self, sql, params=None):
        self.log.append((sql, tuple(params or ())))
        if not self.table.handle(sql, params):
            raise AssertionError(f"替身不认识这条 SQL: {sql}")

    @property
    def rowcount(self):
        return self.table.rowcount

    def fetchone(self):
        return self.table.fetchone()

    def fetchall(self):
        return self.table.fetchall()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FallbackConn:
    def __init__(self, state, log):
        self.state = state
        self._cur = _FallbackCursor(state, log)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def begin(self):
        pass

    def cursor(self):
        return self._cur

    def commit(self):
        self.state["commits"] = self.state.get("commits", 0) + 1

    def rollback(self):
        self.state["rollbacks"] = self.state.get("rollbacks", 0) + 1

    def close(self):
        pass


@pytest.fixture
def fallback_env(monkeypatch):
    """把兜底落库用的事务连接换成拆行替身；顺带 spy 缓存失效（漏失效=前端读到旧会话）。

    返还的 ``state["messages"]`` 是替身维护的"读回来长什么样"（前端契约），
    建行与投影同源，断言不面对裸列。
    """
    def _make(messages, owner_ok=True):
        state = new_state(owner_ok=owner_ok)
        seed(state, messages)
        log: list = []
        import backend.common.db as dbpkg

        monkeypatch.setattr(dbpkg, "DBConnection",
                            lambda: _FallbackConn(state, log))
        invalidations: list = []
        monkeypatch.setattr(chat, "_invalidate_conv_cache",
                            lambda uid, cid=0: invalidations.append((uid, cid)))
        return state, log, invalidations
    return _make


class TestPersistTurnFallback:
    def test_appends_assistant_and_missing_question(self, fallback_env):
        """断连时前端连用户问题都没落库：兜底必须成对补齐，回放不能是没头没尾的答案。"""
        store, log, invalidated = fallback_env([])
        out = chat.persist_turn_fallback(
            conversation_id=150, user_id=7,
            message={"role": "assistant", "content": "已产出的分析正文",
                     "tool_calls": [{"step": 1, "tool": "execute_sql"}]},
            message_uuid="mu-1", question="近一年 app 使用情况")
        assert out == {"persisted": True, "seq": 1, "count": 2}, "补问题(0)+补答案(1)，seq 递增不重"
        assert [m["role"] for m in store["messages"]] == ["user", "assistant"]
        assert store["messages"][0]["content"] == "近一年 app 使用情况"
        asst = store["messages"][1]
        assert asst["content"] == "已产出的分析正文"
        assert asst["message_uuid"] == "mu-1"
        assert asst["fallback_persisted"] is True, "兜底写入必须可区分来源"
        assert asst["tool_calls"], "已执行的工具清单是用户资产的一部分"
        assert invalidated == [(7, 150)], "漏失效缓存会让前端读到旧会话"

    def test_skips_when_client_already_persisted_by_uuid(self, fallback_env):
        """幂等：前端 PUT 已带这条 message_uuid 时不得重复追加，也不得覆盖用户后续编辑。"""
        store, log, _ = fallback_env(
            [{"role": "user", "content": "q"},
             {"role": "assistant", "content": "已产出的分析正文", "message_uuid": "mu-1"}])
        out = chat.persist_turn_fallback(
            conversation_id=150, user_id=7,
            message={"role": "assistant", "content": "已产出的分析正文"}, message_uuid="mu-1")
        assert out == {"persisted": False, "reason": "already_persisted_by_client"}
        assert not any("UPDATE adh_conversations SET messages" in sql for sql, _ in log)

    def test_tail_content_dedup_for_entries_without_uuid(self, fallback_env):
        """AS-BOT 面板落库不带 message_uuid：按正文尾部判重，避免每轮重复一条。"""
        store, log, _ = fallback_env(
            [{"role": "user", "content": "q"},
             {"role": "assistant", "content": "同样的终稿"}])
        out = chat.persist_turn_fallback(
            conversation_id=150, user_id=7,
            message={"role": "assistant", "content": "同样的终稿"})
        assert out["reason"] == "already_persisted_by_client"
        assert len(store["messages"]) == 2
        assert not any("UPDATE adh_conversations SET messages" in sql for sql, _ in log)

    def test_empty_turn_writes_nothing(self, fallback_env):
        """空回合（无正文/无工具/无错误）不落库，也不该碰数据库：不制造空气泡。"""
        store, log, _ = fallback_env([])
        out = chat.persist_turn_fallback(conversation_id=150, user_id=7,
                                         message={"role": "assistant", "content": "  "})
        assert out == {"persisted": False, "reason": "empty_turn"}
        assert log == []

    def test_requires_identity_and_conversation(self, fallback_env):
        """身份/会话缺失直接跳过（身份只信服务端传入，护栏 §2）。"""
        _, log, _ = fallback_env([])
        assert chat.persist_turn_fallback(conversation_id=0, user_id=7,
                                          message={"content": "x"})["reason"] \
            == "no_conversation_or_identity"
        assert chat.persist_turn_fallback(conversation_id=150, user_id=0,
                                          message={"content": "x"})["reason"] \
            == "no_conversation_or_identity"
        assert log == []

    def test_foreign_conversation_is_not_written(self, fallback_env):
        """属主校验走 SQL 条件（WHERE id AND user_id）：别人的会话一律不写。"""
        store, log, _ = fallback_env([], owner_ok=False)
        out = chat.persist_turn_fallback(conversation_id=150, user_id=7,
                                         message={"role": "assistant", "content": "x"},
                                         message_uuid="mu-9")
        assert out == {"persisted": False, "reason": "conversation_not_found_or_not_owner"}
        assert not any("UPDATE adh_conversations SET" in sql for sql, _ in log)

    def test_error_turn_persists_partial_body_and_error(self, fallback_env):
        """中止回合：正文（用户已看到的部分）与错误原因一起落库，刷新后仍是可见的事实。"""
        store, _, _ = fallback_env([{"role": "user", "content": "q"}])
        out = chat.persist_turn_fallback(
            conversation_id=150, user_id=7,
            message={"role": "assistant", "content": "已查得两站结果",
                     "error": "执行超时或中断，请检查会话状态；不会自动重放请求"},
            message_uuid="mu-2")
        assert out["persisted"] is True
        asst = store["messages"][-1]
        assert asst["content"] == "已查得两站结果"
        assert asst["error"].startswith("执行超时或中断")


# ── 会话批量删除：逐条结果可见，且与单条共用同一删除实现 ──────────────────────
#
# 批量入口最容易出的两类问题都在这里钉住：
# 1) 把“根本没删掉”报成“已删除”（delete_conversation_workspace 对不存在/非本人的行
#    rowcount=0 也返回 success），所以必须做归属预检；
# 2) 悄悄新建第二套删除语义（绕过工作区两阶段清理），所以必须断言它逐条调用同一实现。

@pytest.fixture
def batch_env(monkeypatch):
    """返回 (calls, set_backend)：calls 记录逐条删除动作，set_backend 配置每条结果。"""
    from backend.common import db as dbpkg
    from backend.modules.mind.execution import session_workspace

    calls: list = []
    state = {"owned": {}, "outcome": {}}

    def fake_execute_query(sql, params=None, **kw):
        if "FROM adh_conversations WHERE id IN" in sql:
            return [{"id": i, "title": state["owned"][i]} for i in params if i in state["owned"]]
        return []

    def fake_delete(cid, uid):
        from fastapi import HTTPException as _HE
        calls.append((cid, uid))
        err = state["outcome"].get(cid)
        if err:
            raise _HE(409, err)
        return {"success": True}

    monkeypatch.setattr(dbpkg, "execute_query", fake_execute_query)
    monkeypatch.setattr(session_workspace, "delete_conversation_workspace", fake_delete)
    monkeypatch.setattr(chat, "_invalidate_conv_cache", lambda *a, **k: None)
    return calls, state


def _batch(ids, user_id=7):
    req = chat.BatchDeleteConversationsRequest(conversation_ids=ids)
    return chat.batch_delete_conversations(req, user={"user_id": user_id, "username": "u"})


def test_batch_delete_reports_per_item_outcome(batch_env):
    calls, state = batch_env
    state["owned"] = {1: "近7天案例趋势", 2: "站点口径确认"}
    state["outcome"] = {2: "会话仍在执行，请先停止后再删除"}

    result = _batch([1, 2, 3])

    assert result["deleted"] == [1]
    reasons = {f["id"]: f for f in result["failed"]}
    # 执行中的会话：原因原样转述，并带标题供 UI 点名
    assert reasons[2]["reason"] == "会话仍在执行，请先停止后再删除"
    assert reasons[2]["title"] == "站点口径确认"
    # 不在本人清单里的 id：绝不能出现在 deleted（否则用户以为删了，库里还在）
    assert 3 not in result["deleted"] and "不属于当前用户" in reasons[3]["reason"]
    assert result["deleted_count"] == 1 and result["failed_count"] == 2


def test_batch_delete_reuses_the_single_conversation_delete_path(batch_env):
    """逐条走同一实现（含工作区两阶段清理），不新建第二套删除语义。"""
    calls, state = batch_env
    state["owned"] = {1: "a", 2: "b", 3: "c"}
    result = _batch([1, 2, 3, 3])   # 重复 id 只处理一次
    assert [c for c, _u in calls] == [1, 2, 3]
    assert all(u == 7 for _c, u in calls)         # 身份只取服务端解析的用户，不信请求体
    assert result["deleted"] == [1, 2, 3]


def test_batch_delete_rejects_empty_and_oversized_selection(batch_env):
    calls, state = batch_env
    with pytest.raises(HTTPException) as exc:
        _batch([])
    assert exc.value.status_code == 400 and "勾选" in exc.value.detail

    state["owned"] = {i: f"会话{i}" for i in range(1, 61)}
    with pytest.raises(HTTPException) as exc:
        _batch(list(range(1, 61)))
    assert exc.value.status_code == 400 and "最多删除" in exc.value.detail
    assert calls == []                            # 超限不得先删一部分


def test_single_delete_endpoint_shape_is_unchanged(batch_env):
    """重构为共用 helper 后，单条端点响应体保持 {"success": True}（既有前端与用例依赖它）。"""
    _calls, state = batch_env
    state["owned"] = {5: "e"}
    assert chat.delete_conversation(5, user={"user_id": 7, "username": "u"}) == {"success": True}
