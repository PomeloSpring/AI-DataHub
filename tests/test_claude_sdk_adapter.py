"""Unit tests for ClaudeSDKAdapter — claude-agent-sdk 执行层适配器.

覆盖场景:
1. 正常流式(StreamEvent 增量 → token/thinking,ResultMessage → done)
2. resume 失败降级新会话重试
3. 工具调用可观测事件(ToolUseBlock/ToolResultBlock → tool_start/tool_result)
4. sdk_tools 工具异常(execute_sql handler 错误路径)
5. 双 SDK 回归(build_tool_servers qoder / claude)
"""

import asyncio
import os
import sys
import types
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.execution.adapters.claude_sdk_adapter import ClaudeSDKAdapter
from services.datamind.execution.models import ExecutionContext, ExecutionTask


# ── 伪造 SDK 消息类型(适配器按 type().__name__ 分发) ─────────────────

class StreamEvent:
    def __init__(self, event):
        self.event = event


class TextBlock:
    def __init__(self, text):
        self.text = text


class ToolUseBlock:
    def __init__(self, name, id="toolu_1", input=None):
        self.name = name
        self.id = id
        self.input = input or {}


class ToolResultBlock:
    def __init__(self, tool_use_id, content, is_error=False):
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class UserMessage:
    def __init__(self, content):
        self.content = content


class AssistantMessage:
    def __init__(self, content):
        self.content = content


class ResultMessage:
    def __init__(self, session_id="sess-1", is_error=False, result=""):
        self.session_id = session_id
        self.is_error = is_error
        self.result = result
        self.duration_ms = 100
        self.num_turns = 1
        self.subtype = "success"
        self.total_cost_usd = 0.01


def _adapter():
    ad = ClaudeSDKAdapter("claude", {"mode": "sdk", "cli_name": "claude", "sdk_tools": "all"})
    ad._build_options = lambda task: types.SimpleNamespace(
        resume=(task.context.extra or {}).get("session_id") or None
    )
    ad._prompt_with_attachments = lambda task, include_history=True: "PROMPT"
    return ad


def _task(extra=None):
    return ExecutionTask(
        task_id="t1",
        question="hello",
        context=ExecutionContext(workspace_id=1, user_id=2, username="tester", extra=extra or {}),
    )


def _collect(ad, task):
    async def run():
        events = []
        async for ev in ad.execute_stream(task):
            events.append(ev)
        return events

    return asyncio.run(run())


# ── 场景1:正常流式 ──────────────────────────────────────────────────

class TestStreamHappyPath:
    def test_stream_events_map_to_token_thinking_done(self):
        ad = _adapter()

        async def fake_query(prompt, options):
            yield StreamEvent({"delta": {"type": "text_delta", "text": "Hello "}})
            yield StreamEvent({"delta": {"type": "thinking_delta", "thinking": "hm"}})
            yield StreamEvent({"delta": {"type": "text_delta", "text": "world"}})
            yield AssistantMessage([TextBlock("Hello world")])
            yield ResultMessage(session_id="sess-abc")

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        tokens = [e["text"] for e in events if e["type"] == "token"]
        assert tokens == ["Hello ", "world"]  # 已流式,AssistantMessage 不重复发
        assert [e["text"] for e in events if e["type"] == "thinking"] == ["hm"]
        done = events[-1]
        assert done["type"] == "done" and done["result"].success
        assert done["result"].meta["session_id"] == "sess-abc"
        assert done["result"].output == "Hello world"

    def test_no_partial_messages_fallback_to_assistant_text(self):
        ad = _adapter()

        async def fake_query(prompt, options):
            yield AssistantMessage([TextBlock("full answer")])
            yield ResultMessage()

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        assert [e["text"] for e in events if e["type"] == "token"] == ["full answer"]
        assert events[-1]["result"].success

    def test_result_error_maps_to_failed_done(self):
        ad = _adapter()

        async def fake_query(prompt, options):
            yield ResultMessage(is_error=True, result="boom")

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        done = events[-1]
        assert done["type"] == "done"
        assert not done["result"].success and done["result"].error == "boom"


# ── 场景2:resume 失败降级 ──────────────────────────────────────────

class TestResumeFallback:
    def test_resume_failure_retries_as_new_session(self):
        ad = _adapter()
        calls = []

        async def failing_query(prompt, options):
            raise RuntimeError("session not found")
            yield  # pragma: no cover

        async def ok_query(prompt, options):
            yield AssistantMessage([TextBlock("recovered")])
            yield ResultMessage(session_id="sess-new")

        def fake_query(prompt, options):
            calls.append(options.resume)
            return failing_query(prompt, options) if len(calls) == 1 else ok_query(prompt, options)

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task(extra={"session_id": "sess-old"}))

        assert calls == ["sess-old", None]  # 第二次降级为新会话
        done = events[-1]
        assert done["result"].success
        assert done["result"].meta["session_id"] == "sess-new"

    def test_resume_failure_after_streamed_no_retry(self):
        ad = _adapter()
        calls = []

        async def half_query(prompt, options):
            calls.append(1)
            yield StreamEvent({"delta": {"type": "text_delta", "text": "partial"}})
            raise RuntimeError("stream broken mid-way")

        with patch("claude_agent_sdk.query", half_query):
            events = _collect(ad, _task(extra={"session_id": "sess-old"}))

        assert len(calls) == 1  # 已有流式输出,不重试
        done = events[-1]
        assert not done["result"].success
        assert "stream broken mid-way" in done["result"].error


# ── 场景3:工具调用可观测事件(tool_start/tool_result) ────────────

class TestToolEvents:
    def test_tool_use_and_result_emit_timeline_events(self):
        ad = _adapter()

        async def fake_query(prompt, options):
            yield AssistantMessage([
                ToolUseBlock("mcp__datahub_query__execute_sql", id="tu1", input={"sql": "SELECT 1"})
            ])
            yield UserMessage([ToolResultBlock("tu1", [{"text": "ok 1 row"}])])
            yield AssistantMessage([TextBlock("answer")])
            yield ResultMessage(session_id="s1")

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        types = [e["type"] for e in events]
        assert "tool_start" in types and "tool_result" in types
        assert types.index("tool_start") < types.index("tool_result")
        start = next(e for e in events if e["type"] == "tool_start")
        assert start["tool_call_id"] == "tu1"
        assert start["tool"] == "mcp__datahub_query__execute_sql"
        assert start["arguments"] == {"sql": "SELECT 1"}
        res = next(e for e in events if e["type"] == "tool_result")
        assert res["tool_call_id"] == "tu1"
        assert res["output"] == "ok 1 row" and res["error"] == ""
        # done 携带完整 tool_calls 与统计(持久化回放)
        done = events[-1]
        assert done["result"].meta["tool_call_count"] == 1
        calls = done["result"].meta["tool_calls"]
        assert len(calls) == 1
        assert calls[0]["result"] == "ok 1 row" and calls[0]["step"] == 1

    def test_tool_result_error_maps_to_error_field(self):
        ad = _adapter()

        async def fake_query(prompt, options):
            yield AssistantMessage([ToolUseBlock("Bash", id="tu2", input={"command": "ls"})])
            yield UserMessage([ToolResultBlock("tu2", "permission denied", is_error=True)])
            yield ResultMessage()

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        res = next(e for e in events if e["type"] == "tool_result")
        assert res["error"] == "permission denied" and res["output"] == "permission denied"
        call = events[-1]["result"].meta["tool_calls"][0]
        assert call["error"] == "permission denied" and call["result"] is None

    def test_long_tool_output_truncated(self):
        ad = _adapter()
        big = "x" * 5000  # 超过默认 2000 展示上限

        async def fake_query(prompt, options):
            yield AssistantMessage([ToolUseBlock("Read", id="tu3")])
            yield UserMessage([ToolResultBlock("tu3", big)])
            yield ResultMessage()

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        res = next(e for e in events if e["type"] == "tool_result")
        assert res["output"].endswith("(truncated)")
        assert len(res["output"]) < 2100

    def test_plain_text_user_message_ignored(self):
        ad = _adapter()

        async def fake_query(prompt, options):
            yield UserMessage("plain history text")  # 非工具结果的 UserMessage
            yield AssistantMessage([TextBlock("hi")])
            yield ResultMessage()

        with patch("claude_agent_sdk.query", fake_query):
            events = _collect(ad, _task())

        assert not any(e["type"] in ("tool_start", "tool_result") for e in events)
        assert events[-1]["result"].success


# ── 场景4:sdk_tools 工具异常路径 ────────────────────────────────────

class TestSdkToolErrors:
    def test_execute_sql_missing_sql(self):
        from services.datamind.execution.sdk_tools.query_tools import execute_sql

        res = asyncio.run(execute_sql({"sql": ""}))
        assert res["isError"] is True
        assert "sql is required" in res["content"][0]["text"]

    def test_execute_sql_invalid_statement(self):
        from services.datamind.execution.sdk_tools.query_tools import execute_sql

        res = asyncio.run(execute_sql({"sql": "DROP TABLE users"}))
        assert res["isError"] is True

    def test_execute_sql_executor_exception(self):
        from services.datamind.execution.sdk_tools import set_execution_context, ExecutionContextVar
        from services.datamind.execution.sdk_tools.query_tools import execute_sql

        token = set_execution_context(ExecutionContext(workspace_id=1, user_id=1, username="u"))
        try:
            with patch(
                "services.datamind.nl2sql.sql.query_executor.execute_query_with_permission",
                side_effect=PermissionError("denied"),
            ), patch(
                "services.datamind.nl2sql.sql.query_executor.validate_sql",
                return_value=(True, ""),
            ):
                res = asyncio.run(execute_sql({"sql": "SELECT 1"}))
        finally:
            ExecutionContextVar.reset(token)
        assert res["isError"] is True
        assert "denied" in res["content"][0]["text"]


# ── 场景5:双 SDK 回归 ──────────────────────────────────────────────

class TestDualSdkToolServers:
    def test_qoder_backend(self):
        from services.datamind.execution.sdk_tools import build_tool_servers

        res = build_tool_servers("qoder", "all")
        assert set(res["servers"]) == {"datahub_catalog", "datahub_query", "datahub_semantic"}
        assert "mcp__datahub_query__execute_sql" in res["allowed_tools"]

    def test_claude_backend(self):
        from services.datamind.execution.sdk_tools import build_tool_servers

        res = build_tool_servers("claude", "all")
        assert set(res["servers"]) == {"datahub_catalog", "datahub_query", "datahub_semantic"}
        assert len(res["allowed_tools"]) == 8
        assert all(t.startswith("mcp__datahub_") for t in res["allowed_tools"])

    def test_subset_enabled(self):
        from services.datamind.execution.sdk_tools import build_tool_servers

        res = build_tool_servers("claude", ["catalog", "query"])
        assert set(res["servers"]) == {"datahub_catalog", "datahub_query"}
        assert len(res["allowed_tools"]) == 4
