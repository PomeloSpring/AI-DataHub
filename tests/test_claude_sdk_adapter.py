"""双 SDK 安全执行生命周期：失败不新建重试、会话标识仅由服务端维护。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.datamind.execution import secure_sdk
from services.datamind.execution.models import ExecutionTask, ExecutionContext
from services.datamind.execution.tool_policy import compile_policy


def message(kind, **values):
    return type(kind, (), values)()


@pytest.fixture
def harness(monkeypatch):
    from services.datamind.execution import session_workspace, resource_guard
    from services.datamind.execution.sdk_tools import compat
    policy = compile_policy({"waker_key": "empty", "tools": {}})
    runtime = SimpleNamespace(policy=policy, sdk_session_id="server-session", finish=Mock(), record_client=Mock(),
                              tool_tasks=set(), sdk_pid=0, confirm_stopped=Mock())
    task = ExecutionTask(task_id="t", question="hi", context=ExecutionContext(user_id=1))
    calls = []
    messages = []

    class Client:
        def __init__(self, options):
            self.options = options
        async def connect(self):
            calls.append("connect")
        async def query(self, prompt):
            calls.append("query")
        async def receive_response(self):
            for msg in messages:
                if isinstance(msg, Exception):
                    raise msg
                yield msg
        async def disconnect(self):
            calls.append("disconnect")

    monkeypatch.setattr(session_workspace, "claim_session", lambda *a: runtime)
    monkeypatch.setattr(resource_guard, "bind_resources", lambda *a: None)
    monkeypatch.setattr(secure_sdk, "materialize_attachments", lambda *a: None)
    monkeypatch.setattr(secure_sdk, "build_options", lambda *a: SimpleNamespace(resume="server-session"))
    monkeypatch.setattr(compat, "load_sdk", lambda *a: SimpleNamespace(QoderSDKClient=Client, ClaudeSDKClient=Client))
    adapter = SimpleNamespace(config={}, _prompt_with_attachments=lambda *a, **k: "hello")

    def run(backend):
        async def collect():
            return [event async for event in secure_sdk.execute_stream(adapter, task, backend)]
        return asyncio.run(collect())
    return run, messages, calls, runtime


@pytest.mark.parametrize("backend", ["qoder", "claude"])
def test_stream_success_without_exposing_sdk_session(harness, backend):
    run, messages, calls, runtime = harness
    messages.extend([
        message("StreamEvent", event={"delta": {"type": "text_delta", "text": "你好"}}),
        message("AssistantMessage", content=[message("TextBlock", text="你好")]),
        message("ResultMessage", session_id="new-server-session", result="你好", is_error=False),
    ])
    events = run(backend)
    assert [e["text"] for e in events if e["type"] == "token"] == ["你好"]
    assert events[0]["type"] == "capabilities"
    assert events[-1]["result"].success
    assert "session_id" not in events[-1]["result"].meta
    runtime.finish.assert_called_once_with("new-server-session", True, False)
    runtime.confirm_stopped.assert_called_once()
    assert calls == ["connect", "query", "disconnect"]


@pytest.mark.parametrize("backend", ["qoder", "claude"])
def test_resume_failure_is_not_retried(harness, backend):
    run, messages, calls, runtime = harness
    messages.append(RuntimeError("private host and raw error"))
    events = run(backend)
    assert not events[-1]["result"].success
    assert "private host" not in events[-1]["result"].error
    assert calls == ["connect", "query", "disconnect"]
    runtime.finish.assert_called_once_with("", False, False)


@pytest.mark.parametrize("backend", ["qoder", "claude"])
def test_missing_final_result_is_failure(harness, backend):
    run, _, _, runtime = harness
    assert not run(backend)[-1]["result"].success
    runtime.finish.assert_called_once_with("", False, False)


@pytest.mark.parametrize("backend", ["qoder", "claude"])
def test_stop_confirmation_failure_keeps_claim(harness, backend):
    run, messages, _, runtime = harness
    messages.append(message("ResultMessage", session_id="sdk", result="ok", is_error=False))
    runtime.confirm_stopped.side_effect = RuntimeError("process still alive")
    result = run(backend)[-1]["result"]
    assert not result.success and "阻断" in result.error
    runtime.finish.assert_not_called()


@pytest.mark.parametrize("backend", ["qoder", "claude"])
def test_cancel_during_claim_waits_for_thread_and_releases_initialization(harness, monkeypatch, backend):
    import threading
    import time
    from services.datamind.execution import session_workspace
    _, _, calls, runtime = harness
    entered, release = threading.Event(), threading.Event()
    task = ExecutionTask(task_id="cancel", question="hi", context=ExecutionContext(user_id=1))

    def claim(*args):
        entered.set()
        assert release.wait(5)
        task.context.extra["secure_runtime"] = runtime
        return runtime

    monkeypatch.setattr(session_workspace, "claim_session", claim)
    adapter = SimpleNamespace(config={})

    async def run():
        from contextlib import aclosing
        async def consume():
            async with aclosing(secure_sdk.execute_stream(adapter, task, backend)) as stream:
                return [event async for event in stream]
        consumer = asyncio.create_task(consume())
        while not entered.is_set():
            await asyncio.sleep(0.01)
        consumer.cancel()
        await asyncio.sleep(0.03)
        assert not consumer.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer
    asyncio.run(run())
    assert not calls
    runtime.finish.assert_called_once_with("", False, True)


def test_tool_servers_empty_is_empty():
    from services.datamind.execution.sdk_tools import build_tool_servers
    for backend in ("qoder", "claude"):
        for enabled in (None, []):
            assert build_tool_servers(backend, enabled) == {"servers": {}, "allowed_tools": []}
        assert build_tool_servers(backend, ["semantic"], selection={}) == {"servers": {}, "allowed_tools": []}
        for enabled in ("all", ["query"], ["unknown"]):
            with pytest.raises(ValueError):
                build_tool_servers(backend, enabled)


def test_actual_claude_empty_options_contract():
    sdk = pytest.importorskip("claude_agent_sdk", reason="当前虚拟环境未安装 Claude SDK，不能宣称通过真实契约")
    options = sdk.ClaudeAgentOptions(tools=[], permission_mode="default", mcp_servers={}, agents={})
    assert options.tools == [] and options.permission_mode == "default"


@pytest.mark.skipif(__import__("os").getenv("ADH_TEST_AGENT_SDK") != "1", reason="需显式启用真实 Qoder/MySQL/Docker 联调")
def test_real_qoder_empty_tools_resume_and_custom_tools(tmp_path, monkeypatch):
    import uuid
    from services.datamind.execution import session_workspace, service, tool_policy
    from services.datamind.execution.manager import get_execution_layer_manager
    from services.shared.common import auth
    from services.shared.common.db import execute_insert, execute_write, execute_query
    uid = 2**60 + uuid.uuid4().int % 10**10
    key = "sdk-security-test"
    cid = execute_insert("INSERT INTO adh_conversations (user_id,title,workspace_id,waker_key,messages) "
                         "VALUES (%s,%s,0,%s,'[]')", (uid, "__sdk_security_test__", key))
    monkeypatch.setattr(session_workspace, "workspace_base", lambda: tmp_path)
    monkeypatch.setattr(auth, "authorize_workspace", lambda *a: 0)
    policy = compile_policy({"waker_key": key, "tools": {}})
    monkeypatch.setattr(tool_policy, "resolve_policy", lambda *a: policy)
    row = service.get_layer_by_name("cli-qoder")
    assert row and (row["config"].get("env") or {}).get("QODER_PERSONAL_ACCESS_TOKEN"), "测试要求已配置 Qoder 凭据"
    adapter = get_execution_layer_manager().build_adapter(row)

    async def ask(question):
        task = ExecutionTask(task_id=uuid.uuid4().hex, question=question, timeout=120,
                             context=ExecutionContext(user_id=uid, user_role="admin", workspace_id=0,
                             extra={"conversation_id": cid, "waker_key": key}))
        events = [e async for e in adapter.execute_stream(task)]
        result = events[-1]["result"]
        assert result.success, result.error
        return result, task.context.extra["secure_runtime"]

    try:
        first, runtime = __import__("asyncio").run(ask(
            "请尝试用文件写入工具创建 denied.txt。如果没有授权工具，明确说不可用，不要伪造执行成功。"))
        assert first.meta["capabilities"]["empty"]
        assert not (runtime.workspace / "denied.txt").exists()
        saved = execute_query("SELECT * FROM adh_agent_sessions WHERE conversation_id=%s", (cid,), fetchone=True)
        assert saved["status"] == "idle" and saved["sdk_session_id"]
        policy = compile_policy({"waker_key": key, "tools": {"standard": ["read", "write"]}})
        second, resumed = __import__("asyncio").run(ask(
            "请使用 mcp__datahub_workspace__write 写入 proof.txt，内容严格为 SESSION_PROOF，"
            "然后使用 mcp__datahub_workspace__read 读取它。必须实际调用已授权工具。"))
        assert resumed.key == runtime.key and resumed.sdk_session_id == saved["sdk_session_id"]
        assert (runtime.workspace / "proof.txt").read_text() == "SESSION_PROOF"
        calls = [c["tool"] for c in second.meta.get("tool_calls", [])]
        assert "mcp__datahub_workspace__write" in calls and "mcp__datahub_workspace__read" in calls
        policy = compile_policy({"waker_key": key, "tools": {"standard": ["read"]}})
        third, _ = __import__("asyncio").run(ask(
            "尝试把 proof.txt 改成 CHANGED；如果写入权限已经撤回就明确拒绝，不得伪造成功。"))
        assert (runtime.workspace / "proof.txt").read_text() == "SESSION_PROOF"
        assert all(t["name"] != "mcp__datahub_workspace__write" for t in third.meta["capabilities"]["tools"])
    finally:
        active = execute_query("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (cid,), fetchone=True)
        if not active or active["status"] != "running":
            execute_write("DELETE FROM adh_agent_sessions WHERE conversation_id=%s AND user_id=%s", (cid, uid))
            execute_write("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (cid, uid))
