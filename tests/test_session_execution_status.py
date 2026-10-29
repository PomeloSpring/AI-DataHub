"""会话执行权状态回归：取消=断连，释放要等后端收尾核验，期间必须可查、不可重发。

背景（用户报障）：点「取消」后立刻发下一条 → `409 上一轮仍在执行中，请等待完成（或停止后再发送）`。
事实是取消只做了 `abortController.abort()`（前端断开 SSE），后端靠断连被动收尾
（`client.disconnect` + 等在跑的工具任务 + 核验 SDK 子进程已停）才把 running 释放为
interrupted；这段窗口里 `preflight_request` 仍按 running 拦。旧文案还写"或停止后再发送"，
而用户刚停止过 —— 只能被理解成"停止没生效"。

本文件锁定：① execution_status 的状态机口径与 preflight 拦截口径一致；
② 只读状态按 user_id 隔离且不外泄内部标识；③ 409 文案可操作、不自相矛盾。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.modules.mind.api.pipeline import send_status
from backend.modules.mind.execution import session_workspace as sw


def _patch_status(monkeypatch, status):
    """拦截状态查询，返回 (捕获到的 sql/params, 伪造行)。"""
    seen = {}

    def fake_execute_query(sql, params=None, fetchone=False, **kw):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return None if status is None else {"status": status}
    monkeypatch.setattr(sw, "execute_query", fake_execute_query)
    return seen


@pytest.mark.parametrize("status, busy, can_send", [
    ("running", True, False),        # 仍在执行 / 已断连但正在收尾核验
    ("idle", False, True),
    ("interrupted", False, True),    # 中断轮已释放执行权，不拦下一条
    ("closed", False, False),        # 已清空会话：不 busy 但也不能发
    ("deleting", False, False),
    (None, False, True),             # 无会话行（新会话）
])
def test_execution_status_matches_preflight_semantics(monkeypatch, status, busy, can_send):
    _patch_status(monkeypatch, status)
    out = sw.execution_status(123, 7)
    assert out["busy"] is busy
    assert out["can_send"] is can_send


def test_execution_status_scoped_by_user_and_leaks_nothing(monkeypatch):
    """身份只能来自服务端；返回值不得携带 session_key/execution_token/pid。"""
    seen = _patch_status(monkeypatch, "running")
    out = send_status(conversation_id=123, user={"user_id": 7})
    assert "user_id=%s" in seen["sql"], "状态查询未按 user_id 过滤 → 可探测他人会话"
    assert seen["params"] == (123, 7)
    assert set(out) == {"status", "busy", "can_send"}


def test_running_rejects_next_message_with_actionable_hint(monkeypatch):
    """running 时仍必须拦（宁阻断勿双跑），但文案要说明"正在停止"并给出可操作下一步。"""
    _patch_status(monkeypatch, "running")
    req = SimpleNamespace(workspace_id=0, question="再查一次", history=[],
                          attachments=[], pipeline_mode="agent", as_bot_key="",
                          conversation_id=123, model_ref="", session_id="",
                          datasource_id=0, model_id=None, retrieval_strategy=None)
    with pytest.raises(HTTPException) as exc:
        sw.preflight_request(req, {"user_id": 7, "role": "admin"})
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert "正在停止" in detail and "请稍候" in detail
    # 旧文案"（或停止后再发送）"是自相矛盾的误导：用户刚点过停止
    assert "或停止后再发送" not in detail


def test_released_session_does_not_block_next_message(monkeypatch):
    """interrupted（取消后已释放）不得再拦 —— 这正是"停止后能不能继续聊"的答案。"""
    _patch_status(monkeypatch, "interrupted")
    req = SimpleNamespace(workspace_id=0, question="再查一次", history=[],
                          attachments=[], pipeline_mode="agent", as_bot_key="",
                          conversation_id=123, model_ref="", session_id="",
                          datasource_id=0, model_id=None, retrieval_strategy=None)
    # 放行到权限校验段：resolve_policy 需要真实 AS-BOT 配置，这里只断言"没抛 409"
    resolved = {}

    def _fake_resolve_policy(ctx, ceiling=None, inputs=None, *, use_cache=False):
        resolved["use_cache"] = use_cache
        return object()
    monkeypatch.setattr("backend.modules.mind.execution.tool_policy.resolve_policy", _fake_resolve_policy)
    sw.preflight_request(req, {"user_id": 7, "role": "admin"})
    # 轮次起点显式走配置事实缓存（工具级裁决 check_tool 恒不传，拦截语义不变）
    assert resolved["use_cache"] is True
