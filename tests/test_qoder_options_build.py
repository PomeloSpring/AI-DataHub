"""QoderSDKAdapter._build_options 注入测试 — 对应计划 test_qoder_options_build.

给定 Waker 绑定,断言适配器把:
- MCP(仅 Waker 引用的共享服务)→ options.mcp_servers
- 标准工具白名单 → options.disallowed_tools(目录内未勾选项)
- 每个 Waker → 一个 AgentDefinition → options.agents
- 组合后的系统提示词 → options.system_prompt
- 前端回传的会话 → options.resume

并覆盖无 Waker 时的旧版(adh_agents / ctx.system_prompt)回退路径。

qoder_agent_sdk 通过伪造模块注入 sys.modules;工作空间资源加载、
MCP 加载、工具 server 构建、cwd 解析均以替身隔离,不触真实 DB。
"""

import os
import sys
import types
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
from services.datamind.execution.models import ExecutionContext, ExecutionTask
from services.datamind.execution import wakers as wakers_mod


# ── 伪造 qoder_agent_sdk ─────────────────────────────────────────

class _AgentDefinition:
    def __init__(self, description=None, prompt=None, tools=None, mcpServers=None):
        self.description = description
        self.prompt = prompt
        self.tools = tools
        self.mcpServers = mcpServers


class _QoderAgentOptions:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        # 适配器会 extend/append 以下字段,预置为列表
        self.allowed_tools = list(kwargs.get("allowed_tools") or [])
        self.disallowed_tools = list(kwargs.get("disallowed_tools") or [])
        self.mcp_servers = dict(kwargs.get("mcp_servers") or {})
        self.agents = kwargs.get("agents")


class _AccessTokenAuthOptions:
    def __init__(self, access_token=None):
        self.access_token = access_token


class _AccessTokenEnvVar:
    def __init__(self, env_var=None):
        self.env_var = env_var


def _install_fake_sdk():
    mod = types.ModuleType("qoder_agent_sdk")
    mod.AgentDefinition = _AgentDefinition
    mod.QoderAgentOptions = _QoderAgentOptions
    mod.query = lambda *a, **k: None
    auth = types.ModuleType("qoder_agent_sdk.auth")
    auth.AccessTokenAuthOptions = _AccessTokenAuthOptions
    auth.AccessTokenEnvVar = _AccessTokenEnvVar
    mod.auth = auth
    return {"qoder_agent_sdk": mod, "qoder_agent_sdk.auth": auth}


_FAKE_SDK = _install_fake_sdk()


# ── 测试辅助 ──────────────────────────────────────────────────────

def _adapter():
    ad = QoderSDKAdapter("qoder", {"mode": "sdk", "cli_name": "qoder", "sdk_tools": "all"})
    # 隔离外部依赖:cwd / 工具 server / 工作空间资源 / MCP 加载
    ad._resolve_cwd = lambda workspace_id: (None, [])
    ad._build_tool_servers = lambda groups=None: {"servers": {}, "allowed_tools": []}
    ad._load_workspace_resources = lambda workspace_id: {
        "mcp_servers": {}, "agents": {}, "skills": [],
    }
    return ad


def _task(ctx_extra=None, system_prompt=""):
    return ExecutionTask(
        task_id="t1",
        question="hello",
        context=ExecutionContext(
            workspace_id=5, user_id=2, username="alice",
            user_role="analyst", system_prompt=system_prompt,
            extra=ctx_extra or {},
        ),
    )


def _norm(id_, name, **overrides):
    row = {
        "id": id_, "waker_key": name, "name": name,
        "display_name": overrides.pop("display_name", name),
        "description": overrides.pop("description", ""),
        "system_prompt": overrides.pop("system_prompt", ""),
        "persona": overrides.pop("persona", {}),
        "tools": overrides.pop("tools", {}),
        "mcp_server_ids": overrides.pop("mcp_server_ids", []),
        "datasource_ids": [], "skills": overrides.pop("skills", []),
        "chart_enabled": overrides.pop("chart_enabled", True),
        "permission_mode": "inherit", "workspace_id": 5,
        "is_default": overrides.pop("is_default", 0),
        "category": "custom",
    }
    return row


# ── Waker 生效路径 ───────────────────────────────────────────────

class TestWakerInjection:
    def _run(self, waker_list, mcp_ids_result, compose_result="COMPOSED"):
        ad = _adapter()
        ad._load_mcp_by_ids = lambda ids: mcp_ids_result
        with patch.dict(sys.modules, _FAKE_SDK), \
            patch.object(wakers_mod, "resolve_wakers", return_value=waker_list), \
            patch("services.datamind.execution.prompt_composer.compose_system_prompt",
                  return_value=compose_result):
            options = ad._build_options(_task(ctx_extra={"session_id": "sess-42"}))
        return options

    def test_resume_session(self):
        opts = self._run([_norm(1, "analyst")], ({}, {}))
        assert opts.resume == "sess-42"

    def test_system_prompt_from_composer(self):
        opts = self._run([_norm(1, "analyst")], ({}, {}), compose_result="HELLO ROLE")
        assert opts.system_prompt == "HELLO ROLE"

    def test_mcp_servers_from_waker_reference(self):
        cfg = {"type": "stdio", "command": "x"}
        opts = self._run(
            [_norm(1, "analyst", mcp_server_ids=[101])],
            ({"datahub_catalog": cfg}, {101: "datahub_catalog"}),
        )
        assert "datahub_catalog" in opts.mcp_servers
        assert "datahub_catalog" in opts.allowed_mcp_server_names

    def test_each_waker_becomes_agent(self):
        opts = self._run(
            [
                _norm(1, "analyst", system_prompt="你是数据分析师", persona={"responsibility": "查询数据"}),
                _norm(2, "reporter", display_name="报告助手"),
            ],
            ({}, {}),
        )
        assert set(opts.agents.keys()) == {"analyst", "reporter"}
        assert "你是数据分析师" in opts.agents["analyst"].prompt
        assert "职责: 查询数据" in opts.agents["analyst"].prompt
        assert opts.agents["reporter"].description == "报告助手"

    def test_agent_mcp_servers_mapped_by_id(self):
        opts = self._run(
            [_norm(1, "analyst", mcp_server_ids=[101])],
            ({"datahub_catalog": {"type": "stdio", "command": "x"}}, {101: "datahub_catalog"}),
        )
        assert opts.agents["analyst"].mcpServers == ["datahub_catalog"]

    def test_standard_tools_drive_disallowed(self):
        # 仅允许 read/grep → 目录内其余标准工具进 deny-list(Pascal 名)
        opts = self._run(
            [_norm(1, "analyst", tools={"groups": ["query"], "standard": ["read", "grep"]})],
            ({}, {}),
        )
        assert "Read" not in opts.disallowed_tools
        assert "Grep" not in opts.disallowed_tools
        assert "Bash" in opts.disallowed_tools
        assert "Write" in opts.disallowed_tools


# ── 回退(无 Waker)路径 ──────────────────────────────────────────

class TestLegacyFallback:
    def _run(self, res_resources, ctx):
        ad = _adapter()
        ad._load_workspace_resources = lambda workspace_id: res_resources
        task = ExecutionTask(task_id="t1", question="hi", context=ctx)
        with patch.dict(sys.modules, _FAKE_SDK), \
            patch.object(wakers_mod, "resolve_wakers", return_value=[]):
            options = ad._build_options(task)
        return options

    def test_falls_back_to_adh_agents(self):
        res = {
            "mcp_servers": {"legacy_mcp": {"type": "sse", "url": "http://x"}},
            "agents": {"legacy_agent": {"description": "旧代理", "prompt": "OLD", "mcpServers": ["legacy_mcp"]}},
            "skills": [],
        }
        ctx = ExecutionContext(workspace_id=5, username="alice", user_role="analyst",
                               system_prompt="CTX PROMPT")
        opts = self._run(res, ctx)
        assert "legacy_mcp" in opts.mcp_servers
        assert set(opts.agents.keys()) == {"legacy_agent"}
        assert opts.agents["legacy_agent"].prompt == "OLD"
        assert opts.system_prompt == "CTX PROMPT"

    def test_no_session_no_resume(self):
        res = {"mcp_servers": {}, "agents": {}, "skills": []}
        ctx = ExecutionContext(workspace_id=5, username="a", user_role="analyst")
        opts = self._run(res, ctx)
        assert getattr(opts, "resume", None) is None
        # 无 Waker 且无 agents → options.agents 未设置(保持 None)
        assert opts.agents is None
