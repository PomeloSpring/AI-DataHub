"""真实 SDK options 契约：空权限不产生原生工具、默认 MCP 或子代理。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.modules.mind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask
from backend.modules.mind.execution.session_workspace import SessionWorkspace
from backend.modules.mind.execution.tool_policy import compile_policy


def make_task(tmp_path, tools=None):
    policy = compile_policy({"as_bot_key": "test", "name": "测试", "tools": tools or {}, "chart_enabled": False})
    runtime = SessionWorkspace("a" * 32, "b" * 32, tmp_path, policy, None, "trusted-session")
    ctx = ExecutionContext(user_id=1, workspace_id=2, user_role="admin",
                           extra={"secure_runtime": runtime, "session_id": "forged", "as_bot_key": "test"})
    return ExecutionTask(task_id="t", question="你好", context=ctx)


def build(tmp_path, monkeypatch, tools=None):
    pytest.importorskip("qodercn_agent_sdk")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "test-only-token")
    monkeypatch.setenv("TEST_PLATFORM_SECRET", "must-not-inherit")
    task = make_task(tmp_path, tools)
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder", "mode": "sdk"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]):
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
    from qodercn_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
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
        with patch("backend.modules.mind.execution.secure_sdk.check_tool", side_effect=PermissionError("拒绝")):
            denied = await options.can_use_tool("Bash", {"command": "pwd"}, SimpleNamespace())
            hook = options.hooks["PreToolUse"][0].hooks[0]
            response = await hook({"tool_name": "Workflow"}, "id", {})
        assert denied.behavior == "deny"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    asyncio.run(run())


def test_no_trusted_session_cannot_build(monkeypatch):
    pytest.importorskip("qodercn_agent_sdk")
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder"})
    with pytest.raises(PermissionError):
        adapter._build_options(ExecutionTask(task_id="t", question="hi"))


def test_model_resolution_priority(tmp_path, monkeypatch):
    """模型优先级：model_ref > adapter.model > .env QODER_MODEL > None(SDK Auto)。

    历史缺陷：.env 中配的 QODER_MODEL 因 secure_sdk 剥离子进程 env 且
    build_options 不读 config，造成 AS-BOT 未绑模型时直接落到 SDK Auto。
    """
    import backend.common.config as _cfg
    monkeypatch.setattr(_cfg, "QODER_MODEL", "Qwen3.7-Plus", raising=False)

    # 1) 三层都空 → QODER_MODEL 兜底
    options, _ = build(tmp_path, monkeypatch)
    assert options.model == "Qwen3.7-Plus"

    # 2) adapter.model 存在 → 优先于 QODER_MODEL
    pytest.importorskip("qodercn_agent_sdk")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "test-only-token")
    task = make_task(tmp_path)
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder", "mode": "sdk", "model": "gpt-4o"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]):
        opts2 = adapter._build_options(task)
    assert opts2.model == "gpt-4o"

    # 3) ctx.extra.model_ref 存在 → 优先于 adapter.model
    task3 = make_task(tmp_path)
    task3.context.extra["model_ref"] = "qoder/Qwen3.7-Max"
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]):
        opts3 = adapter._build_options(task3)
    assert opts3.model == "qoder/Qwen3.7-Max"


def test_model_env_empty_allows_sdk_auto(tmp_path, monkeypatch):
    """QODER_MODEL 为空字符串时仍保留 SDK Auto 通道(options.model is None)。"""
    import backend.common.config as _cfg
    monkeypatch.setattr(_cfg, "QODER_MODEL", "", raising=False)
    options, _ = build(tmp_path, monkeypatch)
    assert options.model is None
