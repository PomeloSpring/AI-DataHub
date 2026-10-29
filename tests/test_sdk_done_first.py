"""done 先行、收尾后置：SDK 执行流的收尾时序契约 + 同请求策略解析复用。

背景（用户报障）：答案已完整流式输出，前端「思考中」仍长时间转圈。
根因是 done 事件排在 `client.disconnect` + 在跑工具等待 + `confirm_stopped`
轮询 + `finish` 写库之后 —— CLI 子进程退出前 done 发不出去，前端 loading
无法落位。修复把收尾移交后台任务（secure_sdk._spawn_teardown），done 结果
就绪即发；收尾失败不静默：执行权留在 running，由下轮 preflight 显式 409 暴露。

本文件锁定：
① done 在 disconnect 完成之前送达消费者（转圈不再等收尾）；
② 收尾失败时 done 仍为成功结果，但执行权不被伪装为已释放（finish 不被调用），
   失败事实进日志（no-silent-degradation：暴露给下轮 preflight/管理侧）；
③ resolve_policy 带 inputs 时零 DB 重读（同请求 preflight→claim 的固定往返去重）；
④ _preflight_inputs 对身份/空间/AS-BOT 不匹配一律拒绝复用（退回全量解析）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from backend.modules.mind.execution import secure_sdk
from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask


# ── 测试替身 ─────────────────────────────────────────────────────

class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class AssistantMessage:
    """类名即 SDK 消息类型判据（_execute_stream 按 type(msg).__name__ 分派）。"""

    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class ResultMessage:
    is_error = False
    subtype = "success"
    num_turns = 1
    duration_ms = 5
    result = "答案正文"
    session_id = "sdk-sess-1"


class _FakeClient:
    """disconnect 故意慢：done 若仍被收尾阻塞，测试会等到 disconnect 才拿到事件。"""

    def __init__(self, events, disconnect_spy, slow=0.05):
        self._events = events
        self._spy = disconnect_spy
        self._slow = slow
        self._transport = SimpleNamespace(_process=SimpleNamespace(pid=4321, returncode=None))

    async def connect(self):
        pass

    async def query(self, prompt):
        pass

    async def receive_response(self):
        for ev in self._events:
            yield ev

    async def disconnect(self):
        await asyncio.sleep(self._slow)
        self._spy["disconnected"] = True


class _FakeRuntime:
    def __init__(self, finish_raises=False):
        from backend.modules.mind.execution.tool_policy import compile_policy
        self.policy = compile_policy({"as_bot_key": "analyst", "name": "n",
                                      "tools": {"groups": []}, "models": []})
        self.sdk_session_id = ""
        self.sdk_pid = 4321
        self.closing = False
        self.tool_tasks = set()
        self.finished = False
        self.finish_raises = finish_raises

    def record_client(self, client):
        pass

    def confirm_stopped(self):
        pass

    def finish(self, sdk_session_id="", success=False, initialized_only=False):
        if self.finish_raises:
            raise RuntimeError("状态提交冲突")
        self.finished = True


def _patch_startup(monkeypatch, runtime, client_events, disconnect_spy, client_factory=None):
    """把 _execute_stream 的启动依赖全部换成替身，只保留流协议与收尾逻辑本身。"""
    async def _fake_owned(function, *args):
        return function(*args)
    monkeypatch.setattr(secure_sdk, "run_owned_sync", _fake_owned)
    import backend.modules.mind.execution.session_workspace as sw
    monkeypatch.setattr(sw, "claim_session", lambda *a, **k: runtime)
    import backend.modules.mind.execution.resource_guard as rg
    monkeypatch.setattr(rg, "bind_resources", lambda *a, **k: set())
    import backend.modules.mind.execution.sdk_tools.compat as compat
    factory = client_factory or (lambda options=None: _FakeClient(client_events, disconnect_spy))
    fake_sdk = SimpleNamespace(
        QoderSDKClient=factory,
        QoderAgentOptions=lambda **kw: SimpleNamespace(**kw),
    )
    monkeypatch.setattr(compat, "load_sdk", lambda backend_: fake_sdk)
    monkeypatch.setattr(secure_sdk, "build_options", lambda adapter, task, backend_: SimpleNamespace(
        model="m", cli_path="cli"))


def _make_task():
    ctx = ExecutionContext(workspace_id=7, user_id=42, username="u", user_role="admin",
                           extra={"conversation_id": 100})
    return ExecutionTask(task_id="t1", question="q", history=[], context=ctx)


def _make_adapter():
    return SimpleNamespace(config={"_layer_id": 0}, model="m",
                           _prompt_with_attachments=lambda task, include_history=True: "prompt")


async def _drain(stream, events):
    async for ev in stream:
        events.append(ev)


# ── ① / ② done 先行 ─────────────────────────────────────────────

def test_done_arrives_before_slow_teardown(monkeypatch):
    runtime = _FakeRuntime()
    spy = {"disconnected": False}
    _patch_startup(monkeypatch, runtime,
                   [AssistantMessage("答案正文"), ResultMessage()], spy)
    events = []

    async def scenario():
        stream = secure_sdk._execute_stream(_make_adapter(), _make_task(), "qoder")
        await _drain(stream, events)
        # done 送达时 disconnect 尚未完成（收尾已移交后台任务，不再阻塞 done）
        assert spy["disconnected"] is False, "done 仍在等 CLI 断连收尾 → 前端转圈会复现"
        await asyncio.gather(*secure_sdk._teardown_tasks)

    asyncio.run(scenario())
    assert events[-1]["type"] == "done"
    assert events[-1]["result"].success is True
    assert events[-1]["result"].output == "答案正文"
    # 后台收尾照常完成：断连 + 释放执行权
    assert spy["disconnected"] is True
    assert runtime.finished is True
    assert runtime.closing is True


def test_teardown_failure_does_not_fake_release(monkeypatch):
    """finish 抛错（无法确认释放）：done 仍携带成功答案，但执行权不得被伪装为已释放。"""
    runtime = _FakeRuntime(finish_raises=True)
    spy = {"disconnected": False}
    _patch_startup(monkeypatch, runtime,
                   [AssistantMessage("答案正文"), ResultMessage()], spy)
    events = []

    async def scenario():
        stream = secure_sdk._execute_stream(_make_adapter(), _make_task(), "qoder")
        await _drain(stream, events)
        await asyncio.gather(*secure_sdk._teardown_tasks)

    asyncio.run(scenario())
    assert events[-1]["type"] == "done"
    assert events[-1]["result"].success is True
    assert runtime.finished is False, "finish 失败不得把状态标记为已释放（下轮 preflight 须显式 409）"
    assert spy["disconnected"] is True


def test_error_path_yields_single_done(monkeypatch):
    """异常路径（connect 失败）：仍恰好一个 done，且携带失败结果，收尾照常移交。"""
    runtime = _FakeRuntime()
    spy = {"disconnected": False}

    class _Boom:
        def __init__(self, options=None):
            raise ValueError("凭据缺失")

        async def disconnect(self):
            pass

    _patch_startup(monkeypatch, runtime, [ResultMessage()], spy, client_factory=_Boom)
    events = []

    async def scenario():
        stream = secure_sdk._execute_stream(_make_adapter(), _make_task(), "qoder")
        try:
            await _drain(stream, events)
        finally:
            await asyncio.gather(*secure_sdk._teardown_tasks)

    asyncio.run(scenario())
    dones = [e for e in events if e.get("type") == "done"]
    assert len(dones) == 1, "done 必须恰好一条（重复会让前端收尾逻辑跑两遍）"
    assert dones[0]["result"].success is False


# ── ③ / ④ 同请求策略解析复用 ─────────────────────────────────────

_AS_BOT = {"id": 1, "as_bot_key": "analyst", "name": "n", "display_name": "分析师",
           "tools": {"groups": []}, "models": [], "knowledge_base_ids": [],
           "mcp_server_ids": [], "is_default": 1}


def _fresh_ctx(user_id=42, workspace_id=7, as_bot_key=""):
    return ExecutionContext(user_id=user_id, workspace_id=workspace_id,
                            extra={"as_bot_key": as_bot_key})


def test_resolve_policy_with_inputs_skips_db(monkeypatch):
    from backend.modules.mind.execution import tool_policy

    def _boom(*a, **k):
        raise AssertionError("inputs 命中时不得重读 DB")

    import backend.common.auth as auth
    import backend.core.role_service as rs
    import backend.modules.mind.execution.as_bots as ab
    monkeypatch.setattr(auth, "resolve_execution_owner", _boom)
    monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", _boom)
    monkeypatch.setattr(ab, "resolve_as_bots", _boom)
    ctx = _fresh_ctx()
    inputs = {"owner": {"user_id": 42, "username": "u", "role": "admin", "workspace_id": 7},
              "as_bot": dict(_AS_BOT), "role_perms": {}}
    policy = tool_policy.resolve_policy(ctx, None, inputs=inputs)
    assert policy.as_bot["as_bot_key"] == "analyst"
    assert not policy.resolved_inputs, "复用路径不再记录 inputs（避免链式透传陈旧事实）"


def test_resolve_policy_full_records_inputs(monkeypatch):
    from backend.modules.mind.execution import tool_policy
    import backend.common.auth as auth
    import backend.core.role_service as rs
    import backend.modules.mind.execution.as_bots as ab
    monkeypatch.setattr(auth, "resolve_execution_owner",
                        lambda uid, ws: {"user_id": uid, "username": "u", "role": "admin"})
    monkeypatch.setattr(ab, "resolve_as_bots", lambda *a, **k: [dict(_AS_BOT)])
    monkeypatch.setattr(ab, "default_as_bot", lambda rows: rows[0] if rows else None)
    monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", lambda *a, **k: {})
    policy = tool_policy.resolve_policy(_fresh_ctx(), None)
    assert policy.resolved_inputs.get("as_bot") is not None
    assert policy.resolved_inputs["owner"]["user_id"] == 42


def test_preflight_inputs_rejects_identity_mismatch():
    from backend.modules.mind.execution.session_workspace import _preflight_inputs
    from backend.modules.mind.execution.tool_policy import compile_policy

    policy = compile_policy(dict(_AS_BOT))
    policy.resolved_inputs.update({"owner": {"user_id": 42, "workspace_id": 7},
                                   "as_bot": dict(_AS_BOT), "role_perms": {}})
    # 同用户同空间 → 复用
    assert _preflight_inputs(policy, _fresh_ctx()) is not None
    # 换用户 → 拒绝
    assert _preflight_inputs(policy, _fresh_ctx(user_id=99)) is None
    # 跨入口空间（会话归属空间与 preflight 不同）→ 拒绝
    assert _preflight_inputs(policy, _fresh_ctx(workspace_id=8)) is None
    # 会话已绑定另一个 AS-BOT → 拒绝
    assert _preflight_inputs(policy, _fresh_ctx(as_bot_key="other")) is None
    # 无预取事实（内部调用路径）→ 拒绝
    assert _preflight_inputs(compile_policy(dict(_AS_BOT)), _fresh_ctx()) is None
    assert _preflight_inputs(None, _fresh_ctx()) is None
