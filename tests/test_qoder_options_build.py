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


def build(tmp_path, monkeypatch, tools=None, platform_default=""):
    """组装一轮 options。

    `platform_default` 把模型中心「设为默认」那一档钉成固定值（不连真实库），
    否则优先级断言会跟着管理员今天设了哪台模型一起飘。
    """
    pytest.importorskip("qodercn_agent_sdk")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "test-only-token")
    monkeypatch.setenv("TEST_PLATFORM_SECRET", "must-not-inherit")
    task = make_task(tmp_path, tools)
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder", "mode": "sdk"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]), \
         patch("backend.modules.mind.execution.llm_resources.platform_default_model_ref",
               return_value=platform_default):
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
    """模型四级优先级：model_ref > adapter.model > 模型中心默认 > .env QODER_MODEL > None(SDK Auto)。

    历史缺陷有两个：① .env 中配的 QODER_MODEL 因 secure_sdk 剥离子进程 env 且
    build_options 不读 config，造成 AS-BOT 未绑模型时直接落到 SDK Auto；
    ② 模型中心「设为默认」在链上没消费点，设与不设都命中不到，实际跑的是 .env 那台。
    """
    import backend.common.config as _cfg
    monkeypatch.setattr(_cfg, "QODER_MODEL", "Qwen3.7-Plus", raising=False)

    # 1) 三层都空（未设平台默认）→ QODER_MODEL 兜底
    options, _ = build(tmp_path, monkeypatch)
    assert options.model == "Qwen3.7-Plus"

    # 2) 模型中心设了默认 → 优先于 QODER_MODEL（页面承诺的“不指定模型时的选型”现在为真）
    options, _ = build(tmp_path, monkeypatch, platform_default="openai/deepseek-v4.1-flash")
    assert options.model == "openai/deepseek-v4.1-flash"

    # 3) adapter.model 存在 → 优先于平台默认与 QODER_MODEL
    pytest.importorskip("qodercn_agent_sdk")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "test-only-token")
    task = make_task(tmp_path)
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder", "mode": "sdk", "model": "gpt-4o"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]), \
         patch("backend.modules.mind.execution.llm_resources.platform_default_model_ref",
               return_value="openai/deepseek-v4.1-flash"):
        opts2 = adapter._build_options(task)
    assert opts2.model == "gpt-4o"

    # 4) ctx.extra.model_ref 存在 → 优先于 adapter.model
    task3 = make_task(tmp_path)
    task3.context.extra["model_ref"] = "qoder/Qwen3.7-Max"
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]), \
         patch("backend.modules.mind.execution.llm_resources.platform_default_model_ref",
               return_value="openai/deepseek-v4.1-flash"):
        opts3 = adapter._build_options(task3)
    assert opts3.model == "qoder/Qwen3.7-Max"


def test_model_env_empty_allows_sdk_auto(tmp_path, monkeypatch):
    """四级全空时仍保留 SDK Auto 通道(options.model is None)。"""
    import backend.common.config as _cfg
    monkeypatch.setattr(_cfg, "QODER_MODEL", "", raising=False)
    options, _ = build(tmp_path, monkeypatch)
    assert options.model is None


def test_resolve_effective_model_chain_is_single_implementation(monkeypatch):
    """四级链的唯一实现逐档验证（问答/压缩/无人值守共用，不许再各写一份）。

    不依赖 SDK，所以没装 qodercn_agent_sdk 的环境也能跑。
    """
    import backend.common.config as _cfg
    import backend.modules.mind.execution.llm_resources as lr

    monkeypatch.setattr(_cfg, "QODER_MODEL", "Qwen3.8-Flash", raising=False)
    monkeypatch.setattr(lr, "platform_default_model_ref", lambda: "openai/deepseek-v4.1-flash")
    # 逐档取第一个非空：回合级 > Harness 级 > 平台默认 > 部署兜底
    assert lr.resolve_effective_model("openai/qwen3.8-max", "openai/kimi-k3") == "openai/qwen3.8-max"
    assert lr.resolve_effective_model("", "openai/kimi-k3") == "openai/kimi-k3"
    assert lr.resolve_effective_model("", "") == "openai/deepseek-v4.1-flash"
    # 未设平台默认 → 继续往下降一级，不会停在空值
    monkeypatch.setattr(lr, "platform_default_model_ref", lambda: "")
    assert lr.resolve_effective_model("", "") == "Qwen3.8-Flash"
    monkeypatch.setattr(_cfg, "QODER_MODEL", "", raising=False)
    assert lr.resolve_effective_model("", "") == ""


def test_platform_default_requires_explicit_is_default_row(monkeypatch):
    """未显式设默认时返回空串，**不得**拿表里第一行充数。

    回落“第一行”会让一个没人勾选的模型永久架空 .env 兜底，把“管理员还没设默认”
    这个事实藏起来（no-silent-degradation）。
    """
    import backend.common.db as db
    import backend.modules.mind.execution.llm_resources as lr

    seen = []

    def _none(sql, *a, **k):
        seen.append(sql)
        return None

    monkeypatch.setattr(db, "execute_query", _none)
    assert lr.platform_default_model_ref() == ""
    assert "is_default=1" in seen[0]

    monkeypatch.setattr(db, "execute_query",
                        lambda *a, **k: {"provider": "openai", "model_name": "deepseek-v4.1-flash"})
    assert lr.platform_default_model_ref() == "openai/deepseek-v4.1-flash"

    # 查询异常（没连上库等）按“未设默认”处理，不抛到取数主链路
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "execute_query", _boom)
    assert lr.platform_default_model_ref() == ""


def build_with_skills(tmp_path, monkeypatch, skills, extra=None):
    """带技能正文的 options 组装：用于验聊天 `/` 选择一路流到 system_prompt。

    `build()` 把 load_skills 钉成空清单（其它用例只关心工具契约），这里换成可控清单。
    """
    pytest.importorskip("qodercn_agent_sdk")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "test-only-token")
    task = make_task(tmp_path)
    task.context.extra.update(extra or {})
    adapter = QoderSDKAdapter("test", {"cli_name": "qoder", "mode": "sdk"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=skills):
        return adapter._build_options(task)


_TWO_SKILLS = [
    {"name": "data-analysis", "display_name": "数据分析", "description": "语义层取数剧本",
     "system_prompt": "先确认口径再取数（数据分析作业细则）"},
    {"name": "report-delivery", "display_name": "报表交付", "description": "报表产出规范",
     "system_prompt": "报表标题按内容归纳（报表交付作业细则）"},
]


def test_slash_pinned_skill_body_and_directive_reach_prompt(tmp_path, monkeypatch):
    """`/` 选中的技能正文 + 指令必须进本轮提示词。

    多技能绑定时正文按触发词收窄（MAX_SKILL_BODIES），本轮问题「你好」谁都不命中；
    用户显式点名的技能要**越过收窄**被展开，否则前端能选、后端不认=空承诺。
    """
    options = build_with_skills(
        tmp_path, monkeypatch, _TWO_SKILLS,
        {"selected_skills": ["data-analysis"],
         "slash_directive": "用户通过 `/` 显式指定：本轮结束后把该分析需求创建为定时任务。"})
    assert "### 技能:数据分析" in options.system_prompt
    assert "数据分析作业细则" in options.system_prompt
    assert "### 技能:报表交付" not in options.system_prompt
    assert "## 本轮用户指定操作" in options.system_prompt
    assert "把该分析需求创建为定时任务" in options.system_prompt


def test_slash_unpinned_question_keeps_narrowing(tmp_path, monkeypatch):
    """未用 `/` 点名时行为不变：正文按触发词收窄，清单行仍带未展开原因。"""
    options = build_with_skills(tmp_path, monkeypatch, _TWO_SKILLS)
    assert "### 技能:" not in options.system_prompt
    assert "## 已绑定技能" in options.system_prompt
    assert "## 本轮用户指定操作" not in options.system_prompt


def test_slash_unbound_skill_is_announced_not_dropped(tmp_path, monkeypatch):
    """点名的技能没绑到当前 AS-BOT：提示词必须显式告知“本轮没按它执行”。

    静默换个剧本把答案交出去 = 让用户以为指定生效了（no-silent-degradation）。
    """
    options = build_with_skills(tmp_path, monkeypatch, _TWO_SKILLS,
                                {"selected_skills": ["ontology-builder"]})
    assert "## 用户指定技能未生效" in options.system_prompt
    assert "ontology-builder" in options.system_prompt
