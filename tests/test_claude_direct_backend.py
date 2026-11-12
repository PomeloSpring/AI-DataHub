"""claude 后端（Anthropic 协议直连供应商）契约：地址归一、通道判定、options 构造、密钥不外泄。

背景口径（见 backend/modules/mind/execution/anthropic_direct.py 模块头）：
Qoder CLI 的 BYOK 由企业组织策略裁决（可整体禁用），claude-agent-sdk 走 Anthropic 兼容
协议直连供应商，凭据取自模型中心，**不依赖 Qoder 账号**。本文件锁定四件事：
1. 地址归一规则确定、可复述，猜不出就报错（不拼一个"看起来对"的 URL）；
2. 未登记模型 = 保持网关（零变化）；登记不全 = 显式报错（不静默改道）；
3. 直连链路**不要求** QODERCN_PERSONAL_ACCESS_TOKEN；
4. 供应商密钥只进子进程 env，不进通道摘要/审计结构。
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.modules.mind.execution import anthropic_direct as ad
from backend.modules.mind.execution.adapters.claude_sdk_adapter import ClaudeSDKAdapter
from backend.modules.mind.execution.adapters.qoder_sdk_adapter import QoderSDKAdapter
from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask
from backend.modules.mind.execution.session_workspace import SessionWorkspace
from backend.modules.mind.execution.tool_policy import compile_policy

_HOST = "llm-example.cn-beijing.maas.aliyuncs.com"
_OPENAI_URL = f"https://{_HOST}/compatible-mode/v1"
_ANTHROPIC_URL = f"https://{_HOST}/apps/anthropic"
_FAKE_KEY = "sk-fake-key-for-tests-only"
# 本系统会话 id 形态（32 位无横线 hex）；claude 侧必须归一为带横线 UUID
_HEX_SESSION = "0f1e2d3c4b5a49688776655443322110"
_DASHED_SESSION = "0f1e2d3c-4b5a-4968-8776-655443322110"


def _fake_row(**over):
    row = {"id": 1, "name": "百炼直连 · qwen-test", "provider": "openai",
           "base_url": _OPENAI_URL, "api_key": _FAKE_KEY, "model_name": "qwen-test",
           "context_window": 200000, "ref": "openai/qwen-test"}
    row.update(over)
    return row


# ── 1. 地址归一 ────────────────────────────────────────────────────
@pytest.mark.parametrize("given,expected", [
    (_OPENAI_URL, _ANTHROPIC_URL),
    (f"https://{_HOST}/compatible-mode/v1/", _ANTHROPIC_URL),
    (_ANTHROPIC_URL, _ANTHROPIC_URL),
    (f"https://{_HOST}/v1", _ANTHROPIC_URL),
    (f"https://{_HOST}/anything", None),          # 猜不出 → 必须报错
    ("dashscope.aliyuncs.com/compatible-mode/v1", None),  # 缺 scheme → 必须报错
])
def test_anthropic_base_url_normalization(given, expected):
    if expected is None:
        with pytest.raises(ad.DirectChannelError):
            ad.anthropic_base_url(given)
    else:
        assert ad.anthropic_base_url(given) == expected


# ── 2. 通道判定：未登记/空 ref 不改道，登记不全必须报错 ─────────────
def test_unregistered_model_keeps_gateway_no_silent_switch():
    """未登记的模型引用 → None（调用方继续走 Qoder 网关），行为零变化。"""
    with patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value={}) as resolver:
        assert ad.resolve_direct_channel("Qwen3.8-Flash") is None
        resolver.assert_called_once_with("Qwen3.8-Flash")


def test_empty_ref_does_not_fall_back_to_default_row():
    """空引用一律不查库：否则「模型中心默认行」会变成全局静默改道开关。"""
    with patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model") as resolver:
        assert ad.resolve_direct_channel("") is None
        resolver.assert_not_called()


@pytest.mark.parametrize("missing,label", [("api_key", "API Key"), ("base_url", "API 地址"),
                                  ("model_name", "模型")])
def test_incomplete_registration_raises(missing, label):
    row = _fake_row(**{missing: ""})
    with patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value=row):
        with pytest.raises(ad.DirectChannelError) as exc:
            ad.resolve_direct_channel(row["ref"])
    # 文案必须点名缺哪一项（可诊断），不能只说“失败”
    assert label in str(exc.value)


def test_channel_env_maps_every_model_slot():
    """haiku/sonnet/opus/子代理都要映射到供应商模型：漏一个就会去请求未开放的 Anthropic 模型名。"""
    with patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value=_fake_row()):
        ch = ad.resolve_direct_channel("openai/qwen-test")
    assert ch["base_url"] == _ANTHROPIC_URL
    assert ch["env"]["ANTHROPIC_BASE_URL"] == _ANTHROPIC_URL
    assert ch["env"]["ANTHROPIC_AUTH_TOKEN"] == _FAKE_KEY
    for key in ad._MODEL_ENV_KEYS:
        assert ch["env"][key] == "qwen-test", key
    assert ch["env"]["DISABLE_AUTOUPDATER"] == "1"


# ── 3. build_options：claude 后端构造真实 ClaudeAgentOptions ────────
def make_task(tmp_path, model_ref):
    policy = compile_policy({"as_bot_key": "test", "name": "测试", "tools": {}, "chart_enabled": False})
    runtime = SessionWorkspace("a" * 32, "b" * 32, tmp_path, policy, None, _HEX_SESSION)
    ctx = ExecutionContext(user_id=1, workspace_id=2, user_role="admin",
                           extra={"secure_runtime": runtime, "session_id": "forged",
                                  "as_bot_key": "test", "model_ref": model_ref})
    return ExecutionTask(task_id="t", question="你好", context=ctx)


def build_claude(tmp_path, monkeypatch, model_ref="openai/qwen-test", existing_transcript=False):
    pytest.importorskip("claude_agent_sdk")
    # 关键：故意**不配** Qoder 凭据，证明直连链路不依赖企业账号
    monkeypatch.delenv("QODERCN_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("QODER_PERSONAL_ACCESS_TOKEN", raising=False)
    task = make_task(tmp_path, model_ref)
    adapter = ClaudeSDKAdapter("test", {"cli_name": "claude", "mode": "sdk"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]), \
         patch("backend.modules.mind.execution.anthropic_direct.cli_session_exists",
               return_value=existing_transcript), \
         patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value=_fake_row()):
        return adapter._build_options(task), task


def test_claude_options_need_no_qoder_token(tmp_path, monkeypatch):
    options, task = build_claude(tmp_path, monkeypatch)
    import claude_agent_sdk
    assert isinstance(options, claude_agent_sdk.ClaudeAgentOptions)
    assert options.model == "qwen-test"
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == _FAKE_KEY
    assert options.env["ANTHROPIC_BASE_URL"] == _ANTHROPIC_URL
    assert options.env["CLAUDE_CONFIG_DIR"] == str(task.context.extra["secure_runtime"].runtime_dir)
    # 安全执行链与 qoder 后端同源：空权限仍然是零工具、会话恢复仍指向可信会话
    assert options.tools == [] and options.allowed_tools == []
    assert options.strict_mcp_config
    # 会话参数由 claude 自己定（首轮 session_id / 续会话 resume），不再是 qoder 那套 hex id
    assert options.session_id == _DASHED_SESSION
    assert options.resume is None


def test_claude_env_isolation_no_none_and_no_leak(tmp_path, monkeypatch):
    """claude SDK 会继承 os.environ，所以隔离子进程只能靠“显式置空”。

    两个必须同时成立的方向：
    - env 里不能有 None（claude transport 会把 None 当路径用，spawn 直接挂）；
    - 宿主真实凭据不得原样下传（这里用带真实值的变量做探针）。
    """
    monkeypatch.setenv("TEST_PLATFORM_SECRET", "must-not-inherit")
    monkeypatch.setenv("METADATA_DB_PASSWORD", "meta-db-must-not-inherit")
    options, _ = build_claude(tmp_path, monkeypatch)
    assert None not in options.env.values()
    assert options.env["TEST_PLATFORM_SECRET"] == ""
    assert options.env["METADATA_DB_PASSWORD"] == ""
    # 但直连必需的变量必须是真的
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == _FAKE_KEY


def test_claude_channel_summary_has_no_secret(tmp_path, monkeypatch):
    """通道摘要会进 ctx.extra（审计/诊断可达），密钥必须在它之外。"""
    _, task = build_claude(tmp_path, monkeypatch)
    summary = task.context.extra["llm_channel"]
    assert _FAKE_KEY not in repr(summary)
    assert summary["channel"] == "anthropic-direct" and summary["model"] == "qwen-test"


def test_claude_backend_unregistered_model_fails_loud(tmp_path, monkeypatch):
    """模型没登记成直连配置时报错，不回退网关（否则用户看到 Credits 却以为在用百炼）。"""
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.delenv("QODERCN_PERSONAL_ACCESS_TOKEN", raising=False)
    task = make_task(tmp_path, "openai/not-registered")
    adapter = ClaudeSDKAdapter("test", {"cli_name": "claude", "mode": "sdk"})
    with patch("backend.modules.mind.execution.prompt_composer._permission_summary", return_value="权限"), \
         patch("backend.modules.mind.execution.as_bots.load_skills", return_value=[]), \
         patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value={}):
        with pytest.raises(ad.DirectChannelError) as exc:
            adapter._build_options(task)
    assert "模型中心" in str(exc.value)


def test_claude_options_field_drops_are_guarded(tmp_path, monkeypatch):
    """common 里出现 claude 不支持且未登记的字段时必须报错，不静默少传。"""
    from backend.modules.mind.execution.secure_sdk import _build_claude_options
    import claude_agent_sdk as sdk
    with patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value=_fake_row()):
        with pytest.raises(ValueError, match="不支持这些字段"):
            _build_claude_options(sdk, ClaudeSDKAdapter("t", {"cli_name": "claude"}),
                                  {"model_ref_nonsense": 1}, {}, str(tmp_path), "openai/qwen-test",
                                  SimpleNamespace(extra={}))


# ── 4. 会话参数：首轮 session_id、已有转录则 resume ─────────────────
def test_cli_session_id_normalizes_hex_to_dashed_uuid():
    import uuid

    hex32 = uuid.uuid4().hex
    dashed = str(uuid.UUID(hex32))
    assert ad.cli_session_id(hex32) == dashed
    assert ad.cli_session_id(dashed) == dashed
    assert ad.cli_session_id("") == ""
    assert ad.cli_session_id("trusted-session") == ""   # 不是 UUID 形态就不猜


def test_cli_session_kwargs_first_turn_vs_followup():
    """无转录→ session_id 建会话；已有转录→ resume 续会话。"""
    with patch.object(ad, "cli_session_exists", return_value=False):
        first = ad.cli_session_kwargs("a" * 32, "/tmp/x")
        assert first["session_id"] and "-" in first["session_id"]
        assert "resume" not in first and first["_adh_new_cli_session"] is True
    with patch.object(ad, "cli_session_exists", return_value=True):
        later = ad.cli_session_kwargs("a" * 32, "/tmp/x")
        assert later["resume"] and "session_id" not in later
        # 续会话不新建 → 调用方不该把历史再带进提示词（否则历史在转录与提示词里双份）
        assert not later.get("_adh_new_cli_session")


def test_claude_options_first_turn_uses_session_id(tmp_path, monkeypatch):
    """首轮必须用 session_id：实测 Claude CLI 对不存在的会话做 --resume 会直接报错。"""
    options, task = build_claude(tmp_path, monkeypatch)
    assert getattr(options, "session_id", None)
    assert getattr(options, "resume", None) is None
    assert task.context.extra["_adh_new_cli_session"] is True


def test_claude_options_followup_uses_resume(tmp_path, monkeypatch):
    options, task = build_claude(tmp_path, monkeypatch, existing_transcript=True)
    assert getattr(options, "resume", None)
    assert getattr(options, "session_id", None) is None
    assert task.context.extra["_adh_new_cli_session"] is False


def test_new_cli_session_forces_history_into_prompt(tmp_path, monkeypatch):
    """新建 CLI 会话时提示词必须自带历史：否则多轮上下文静默丢失。"""
    from backend.modules.mind.execution.adapters.cli_adapter import CLIProcessAdapter

    _, task = build_claude(tmp_path, monkeypatch)
    task.history = [{"role": "user", "content": "上一轮问题"}]
    prompt_new = CLIProcessAdapter._prompt_with_attachments(None, task, include_history=True)
    prompt_resume = CLIProcessAdapter._prompt_with_attachments(None, task, include_history=False)
    assert "上一轮问题" in prompt_new
    assert "上一轮问题" not in prompt_resume


# ── 5. 选层与后端映射：qoder 行为零变化 ─────────────────────────────
def test_manager_maps_backend_by_cli_name():
    from backend.modules.mind.execution.manager import ExecutionLayerManager

    mgr = ExecutionLayerManager()
    claude = mgr.build_adapter({"id": 8, "name": "cli-claude-direct", "layer_type": "cli",
                                "config": {"mode": "sdk", "cli_name": "claude"}})
    qoder = mgr.build_adapter({"id": 7, "name": "cli-qoder", "layer_type": "cli",
                               "config": {"mode": "sdk", "cli_name": "qoder"}})
    assert isinstance(claude, ClaudeSDKAdapter)
    assert isinstance(qoder, QoderSDKAdapter)
    with pytest.raises(ValueError):
        mgr.build_adapter({"id": 9, "name": "x", "layer_type": "cli",
                           "config": {"mode": "process", "cli_name": "claude"}})
    with pytest.raises(ValueError):
        mgr.build_adapter({"id": 10, "name": "y", "layer_type": "http", "config": {}})


def test_sdk_backend_guard_rejects_unknown():
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk
    with pytest.raises(ValueError):
        load_sdk("openai-agents")


# ── 5. 密钥不外泄（与出站守卫同口径）────────────────────────────────
def test_channel_summary_passes_outbound_guard():
    """通道摘要会随审计/诊断结构流动，必须过得了出站守卫（护栏 §7）。"""
    import json

    from backend.modules.mind.execution.outbound_guard import find_sensitive_hits

    with patch("backend.modules.mind.execution.llm_resources.resolve_system_llm_model",
               return_value=_fake_row()):
        ch = ad.resolve_direct_channel("openai/qwen-test")
    summary = {"channel": ch["channel"], "provider": ch["provider"],
               "model": ch["model"], "base_url": ch["base_url"]}
    assert find_sensitive_hits(json.dumps(summary, ensure_ascii=False)) == []
    # 反证：把 env 整包序列化进对外结构一定会被拦（这就是不把 env 放进 ctx.extra 的原因）
    assert find_sensitive_hits(json.dumps(ad.redact_secrets({"env": ch["env"]}))) == []
    assert _FAKE_KEY not in json.dumps(ad.redact_secrets({"env": ch["env"]}))
