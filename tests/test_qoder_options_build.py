"""真实 SDK options 契约：空权限不产生原生工具、默认 MCP 或子代理。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.datamind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
from services.datamind.execution.models import ExecutionContext, ExecutionTask
from services.datamind.execution.session_workspace import SessionWorkspace
from services.datamind.execution.tool_policy import compile_policy


def make_task(tmp_path, tools=None):
    policy = compile_policy({"as_bot_key": "test", "name": "测试", "tools": tools or {}, "chart_enabled": False})
    runtime = SessionWorkspace("a" * 32, "b" * 32, tmp_path, policy, None, "trusted-session")
    ctx = ExecutionContext(user_id=1, workspace_id=2, user_role="admin",
                           extra={"secure_runtime": runtime, "session_id": "forged", "as_bot_key": "test"})
    return ExecutionTask(task_id="t", question="你好", context=ctx)


def build(tmp_path, monkeypatch, tools=None):
    pytest.importorskip("qoder_agent_sdk")
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "test-only-token")
    monkeypatch.setenv("TEST_PLATFORM_SECRET", "must-not-inherit")
    task = make_task(tmp_path, tools)
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder", "mode": "sdk"})
    with patch("services.datamind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("services.datamind.execution.as_bots.load_skills", return_value=[]):
        return adapter._build_options(task), task


def test_empty_tools_disable_native_mcp_agents(tmp_path, monkeypatch):
    options, _ = build(tmp_path, monkeypatch)
    assert options.tools == []
    assert options.allowed_tools == []
    assert options.mcp_servers == {}
    assert options.agents == {}
    assert options.permission_mode == "default"
    assert options.setting_sources == []
    assert options.strict_mcp_config
    assert options.resume == "trusted-session"
    assert options.env["TEST_PLATFORM_SECRET"] is None
    assert options.cwd == str(tmp_path / "workspace")
    assert options.add_dirs == []
    assert "当前未授权任何工具" in options.system_prompt


def test_sdk_serializes_empty_base_tools(tmp_path, monkeypatch):
    options, _ = build(tmp_path, monkeypatch)
    from qoder_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    transport = SubprocessCLITransport(prompt="hello", options=options)
    command = transport._build_command()
    assert command[command.index("--tools") + 1] == ""
    assert "--dangerously-skip-permissions" not in command


def test_selected_custom_tool_is_registered_and_described(tmp_path, monkeypatch):
    options, _ = build(tmp_path, monkeypatch, {"mcp": {"semantic": ["get_metrics"]}})
    assert options.allowed_tools == ["mcp__datahub_semantic__get_metrics"]
    assert set(options.mcp_servers) == {"datahub_semantic"}
    assert "mcp__datahub_semantic__get_metrics" in options.system_prompt
    assert options.tools == []


def test_standard_tools_are_session_proxies(tmp_path, monkeypatch):
    options, _ = build(tmp_path, monkeypatch, {"standard": ["read", "grep"]})
    assert set(options.allowed_tools) == {"mcp__datahub_workspace__read", "mcp__datahub_workspace__grep"}
    assert options.tools == []
    assert not options.agents


def test_permission_and_hook_reject_unknown_tool(tmp_path, monkeypatch):
    options, _ = build(tmp_path, monkeypatch)
    async def run():
        with patch("services.datamind.execution.secure_sdk.check_tool", side_effect=PermissionError("拒绝")):
            denied = await options.can_use_tool("Bash", {"command": "pwd"}, SimpleNamespace())
            hook = options.hooks["PreToolUse"][0].hooks[0]
            response = await hook({"tool_name": "Workflow"}, "id", {})
        assert denied.behavior == "deny"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    asyncio.run(run())


def test_no_trusted_session_cannot_build(monkeypatch):
    pytest.importorskip("qoder_agent_sdk")
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder"})
    with pytest.raises(PermissionError):
        adapter._build_options(ExecutionTask(task_id="t", question="hi"))
