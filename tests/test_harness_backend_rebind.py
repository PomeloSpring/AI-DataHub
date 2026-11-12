"""换 Harness（执行后端）后续聊的契约：会话不作废、指针按后端各存一份、切换事实对用户可见。

背景缺陷：`adh_agent_sessions.sdk_session_id` 只有一个槽位，而它属于某一个 CLI 后端
（qoder 与 claude 的转录目录布局、id 规则、"转录不存在"的行为都不同）。默认执行层一切换，
旧会话的指针就成了新后端读不到的 id，`claim_session` 只能回 403
「执行会话身份或后端不匹配，请新建会话」——换一次 Harness 等于把切换前建的所有会话作废。

本文件锁定修复后的四条口径：
1. 后端不是身份：backend 不匹配改为**重绑**（问答文本与工作区产物保留），属主/AS-BOT 仍硬拒；
2. 转录指针按后端记账（`backend_sessions`），切回原后端能接回它自己的转录；
3. 新后端没有指针 → 不带 resume，由提示词带问答记录重建上下文（不猜、不拿别后端的 id）；
4. 切换**不向用户弹提示**（产品决策，2026-11-10），但事实必须可审计：服务端日志 + 本回合
   trace span + 执行映射落库，评测/排障能分辨"重绑轮"与"正常续聊轮"（§2 可区分要求）。
"""
import inspect
import os
import uuid
from unittest.mock import Mock
import pytest
from fastapi import HTTPException

from backend.modules.mind.execution import session_workspace as sessions


@pytest.mark.parametrize("raw,expected", [
    (None, {}),                                            # 旧环境列缺失/未回填
    ("", {}),
    ("{}", {}),
    ("bad-json", {}),                                      # 脏值不猜指针
    ({"qoder": "sid-q"}, {"qoder": "sid-q"}),
    ('{"claude": "sid-c"}', {"claude": "sid-c"}),          # pymysql 读 JSON 列返回字符串
    ('{"qoder": null, "claude": "sid-c"}', {"claude": "sid-c"}),
])
def test_backend_session_pointers_normalize(raw, expected):
    """指针表必须归一为 {backend: sdk_session_id}：JSON 列以字符串返回，不归一会被按字符展开。"""
    assert sessions.normalize_backend_sessions(raw) == expected


def test_rebind_switches_backend_and_takes_that_backends_pointer():
    """重绑只取**目标后端**的指针：没有就置空（本轮不带 resume），绝不拿别后端的 id。"""
    calls = []
    cur = Mock()
    cur.execute.side_effect = lambda sql, params=None: calls.append((sql, params))
    row = {"session_key": "k" * 32, "backend": "qoder", "sdk_session_id": "sid-q",
           "backend_sessions": '{"qoder": "sid-q"}'}

    # 切到从没跑过的新后端：无指针 → 空 id（由调用方按问答记录重建上下文）
    new_row, fact = sessions._rebind_backend(cur, row, "claude")
    assert fact == {"from": "qoder", "to": "claude", "resumed": False}
    assert new_row["backend"] == "claude" and new_row["sdk_session_id"] == ""
    assert "backend=claude" in calls[-1][0].replace(" ", "") or "%s" in calls[-1][0]
    assert calls[-1][1] == ("claude", None, "k" * 32)      # 空指针写 NULL，不写空串冒充

    # 切回原后端：map 里的指针还在 → 接回它自己的转录（当前登记已是 claude）
    calls.clear()
    on_claude = {**row, "backend": "claude", "sdk_session_id": "sid-c",
                 "backend_sessions": '{"qoder": "sid-q", "claude": "sid-c"}'}
    back, fact2 = sessions._rebind_backend(cur, on_claude, "qoder")
    assert fact2 == {"from": "claude", "to": "qoder", "resumed": True}
    assert back["sdk_session_id"] == "sid-q"
    assert calls[-1][1] == ("qoder", "sid-q", "k" * 32)


def test_rebind_is_noop_when_backend_unchanged():
    cur = Mock()
    row = {"session_key": "k", "backend": "qoder", "sdk_session_id": "sid-q"}
    same, fact = sessions._rebind_backend(cur, row, "qoder")
    assert fact == {} and same is row
    cur.execute.assert_not_called()


def test_finish_records_pointer_per_backend(monkeypatch):
    """回合结束同时写 map：只更新单槽位会让切回原后端时接不回（历史不可逆损失）。"""
    written = []
    def _capture(sql, params=()):
        written.append((sql, params))
        return 1        # 条件更新命中 1 行 = 释放成功（0 行会被 finish 判为状态冲突）

    monkeypatch.setattr(sessions, "execute_write", _capture)
    policy = Mock()
    runtime = sessions.SessionWorkspace("k" * 32, "t" * 32, Mock(), policy, None,
                                        "sid-c", backend="claude")
    runtime.finish("sid-c-new", success=True)
    sql, params = written[-1]
    assert "backend_sessions=JSON_SET" in sql
    assert params[:4] == ("idle", "sid-c-new", "$.claude", "sid-c-new")

    # 未登记后端的旧构造（旁路调用/历史写法）不写 map，行为与改前一致
    written.clear()
    legacy = sessions.SessionWorkspace("k" * 32, "t" * 32, Mock(), policy, None, "sid")
    legacy.finish("sid-2", success=True)
    assert "backend_sessions" not in written[-1][0]

    # 后端名异常（会进 JSON path）一律不记账，不拼出可疑 SQL
    written.clear()
    weird = sessions.SessionWorkspace("k" * 32, "t" * 32, Mock(), policy, None, "", backend="a; DROP")
    weird.finish("sid-3", success=True)
    assert "backend_sessions" not in written[-1][0]


def test_claim_session_keeps_hard_reject_on_identity_but_not_on_backend():
    """源码级护栏：身份（属主 + AS-BOT 形态）仍硬拒；后端不得再进这条判断。

    claim_session 需要真实 MySQL 才能整链路跑通（见下方 gated 用例），这里锁住"不得回到
    三元组一刀切"的写法，防止有人把 backend 又塞回身份校验里。
    """
    src = inspect.getsource(sessions.claim_session)
    assert 'raise HTTPException(403, "执行会话身份不匹配，请新建会话")' in src
    assert "执行会话身份或后端不匹配" not in src
    guard = src.split("raise HTTPException(403, \"执行会话身份不匹配")[0]
    assert 'row["backend"]' not in guard.split("if row:")[-1]
    assert "_rebind_backend(cur, row, backend)" in src


def test_switch_is_auditable_without_user_notice(monkeypatch):
    """切换不进 warnings（用户拍板不弹提示），但必须落 trace span + meta 结构化事实。"""
    from backend.modules.mind.execution import secure_sdk

    src = inspect.getsource(secure_sdk._execute_stream)
    tail = src.split("_harness_rebound")[-1]
    assert 'meta["harness_rebound"]' in tail and "_obs_rebind_span" in tail
    assert "rebind_notice" not in src                       # 不得再面向用户造文案
    assert 'meta["warnings"]' not in tail                   # 不进用户可见告警通道

    # span 名要能区分切换方向（管理端 trace 里"重绑轮"必须与"正常续聊轮"可分辨）
    recorded = []
    monkeypatch.setattr("backend.observability.record_span", lambda **kw: recorded.append(kw))
    secure_sdk._obs_rebind_span({"from": "qoder", "to": "claude"})
    assert recorded[0]["kind"] == "phase" and recorded[0]["name"] == "harness_rebind:qoder→claude"

    # 观测未开启/记录异常时不得影响主链路（护栏 §9）
    monkeypatch.setattr("backend.observability.record_span",
                        Mock(side_effect=RuntimeError("观测未开启")))
    secure_sdk._obs_rebind_span({"from": "claude", "to": "qoder"})


@pytest.mark.skipif(os.getenv("ADH_TEST_AGENT_MYSQL") != "1", reason="需显式启用隔离测试记录的真实 MySQL 回归")
def test_mysql_switch_harness_continues_conversation_and_restores_pointer(tmp_path, monkeypatch):
    """真实库全链路：换后端续聊不再 403，切回原后端接回原转录，属主不符仍拒。"""
    from backend.common import auth
    from backend.common.db import execute_insert, execute_write, execute_query
    from backend.modules.mind.execution import tool_policy as policies
    from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask

    uid = 2**60 + uuid.uuid4().int % 10**10
    cid = execute_insert("INSERT INTO adh_conversations (user_id,title,workspace_id,as_bot_key,messages) "
                         "VALUES (%s,%s,0,%s,'[]')", (uid, "__backend_rebind_test__", "rebind-test"))
    policy = policies.compile_policy({"as_bot_key": "rebind-test", "tools": {}})
    monkeypatch.setattr(sessions, "workspace_base", lambda *a, **k: tmp_path)
    monkeypatch.setattr(policies, "resolve_policy", lambda *a, **k: policy)
    monkeypatch.setattr(auth, "authorize_workspace", lambda *a, **k: 0)

    def task():
        return ExecutionTask(task_id=uuid.uuid4().hex, question="换后端后继续问", context=ExecutionContext(
            user_id=uid, user_role="admin", workspace_id=0,
            extra={"conversation_id": cid, "as_bot_key": "rebind-test"}))

    try:
        first = task()
        rt = sessions.claim_session(first, "qoder", {})
        rt.finish("sid-qoder", success=True)

        # 换 Harness：同一会话继续可执行，且切换事实可见
        second = task()
        rt2 = sessions.claim_session(second, "claude", {})
        assert rt2.key == rt.key and rt2.root == rt.root          # 工作区与产物原地保留
        assert rt2.sdk_session_id == ""                            # 不拿 qoder 的 id 去 resume
        assert rt2.rebound_from == "qoder"
        assert second.context.extra["_harness_rebound"] == {"from": "qoder", "to": "claude",
                                                            "resumed": False}
        rt2.finish("sid-claude", success=True)

        row = execute_query("SELECT backend, sdk_session_id, backend_sessions FROM adh_agent_sessions "
                            "WHERE conversation_id=%s", (cid,), fetchone=True)
        assert row["backend"] == "claude" and row["sdk_session_id"] == "sid-claude"
        assert sessions.normalize_backend_sessions(row["backend_sessions"]) == {
            "qoder": "sid-qoder", "claude": "sid-claude"}

        # 切回原 Harness：接回它自己的转录（旧单槽位写法在这里会把指针丢掉）
        third = task()
        rt3 = sessions.claim_session(third, "qoder", {})
        assert rt3.sdk_session_id == "sid-qoder"
        assert third.context.extra["_harness_rebound"]["resumed"] is True
        rt3.finish("sid-qoder", success=True)

        # 身份仍硬拒：属主不符不得因为"只是换后端"被放行
        forged = ExecutionTask(task_id=uuid.uuid4().hex, question="冒用", context=ExecutionContext(
            user_id=uid + 1, user_role="admin", workspace_id=0,
            extra={"conversation_id": cid, "as_bot_key": "rebind-test"}))
        with pytest.raises(HTTPException) as denied:
            sessions.claim_session(forged, "qoder", {})
        assert denied.value.status_code in (403, 404)
    finally:
        execute_write("DELETE FROM adh_agent_sessions WHERE conversation_id=%s", (cid,))
        execute_write("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (cid, uid))
