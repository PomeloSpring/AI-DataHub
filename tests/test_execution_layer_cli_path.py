"""执行层 cli_path 解析契约: 相对项目根/~/​$VAR/命令名, 保证换目录·换机器·容器部署可移植.

回归背景: DB 曾把 cli-qoder 的 cli_path 写死成机器绝对路径
(/home/.../runtime/qodercli/bin/qodercli), 项目一挪位置就失效。
修复后 cli_path 支持相对**项目根**解析, SDK 模式下命令名/空配置钉死项目内置 runtime wrapper。
"""
import os
import re
import subprocess
from types import SimpleNamespace

import pytest

from backend.modules.mind.execution import secure_sdk
from backend.modules.mind.execution.adapters.cli_adapter import CLIProcessAdapter
from backend.common.config import PROJECT_ROOT


def _adapter(cli_path=None, cli_name="qoder"):
    cfg = {"cli_name": cli_name, "mode": "sdk"}
    if cli_path is not None:
        cfg["cli_path"] = cli_path
    return CLIProcessAdapter("test-layer", cfg)


def test_relative_path_anchors_project_root():
    """相对路径锚定项目根 → 绝对路径(随代码库迁移, 不写死机器路径)."""
    a = _adapter("runtime/qodercli/bin/qodercli")
    expected = str((PROJECT_ROOT / "runtime/qodercli/bin/qodercli").resolve())
    assert a.cli_path == expected
    assert os.path.isabs(a.cli_path)


def test_project_runtime_wrapper_resolves_to_existing_file():
    """DB 实际配置的 runtime wrapper 解析后确实存在(tracked 启动脚本).

    缺失说明部署机未跑 runtime/qodercli/setup.sh —— fail-loud 提示, 不静默回退。
    """
    a = _adapter("runtime/qodercli/bin/qodercli")
    assert os.path.isfile(a.cli_path), (
        f"runtime wrapper 缺失, 需先执行 bash runtime/qodercli/setup.sh: {a.cli_path}"
    )


def test_absolute_path_preserved():
    """显式绝对路径原样使用(尊重管理员配置)."""
    a = _adapter("/opt/qoder/qodercli")
    assert a.cli_path == "/opt/qoder/qodercli"


def test_home_expansion():
    a = _adapter("~/custom/qodercli")
    assert a.cli_path == os.path.join(os.path.expanduser("~"), "custom/qodercli")


def test_env_var_expansion(monkeypatch):
    monkeypatch.setenv("QODERCLI_HOME", "/tmp/qcli-home")
    a = _adapter("$QODERCLI_HOME/bin/qodercli")
    assert a.cli_path == "/tmp/qcli-home/bin/qodercli"


def test_bare_command_name_not_anchored():
    """纯命令名(无分隔符)不被误当相对路径拼项目根; PATH 找不到则保留命令名."""
    a = _adapter("qodercli-does-not-exist-xyz")
    assert a.cli_path == "qodercli-does-not-exist-xyz"
    assert not os.path.isabs(a.cli_path)


def test_empty_falls_back_to_binary_lookup():
    """空配置 → PATH 查找 binary; 命中为绝对路径, 否则保留命令名 'qodercli'."""
    a = _adapter(None)
    assert a.cli_path
    assert a.cli_path == "qodercli" or os.path.isabs(a.cli_path)


def test_sdk_cli_path_absolute_passthrough():
    """SDK 模式: 解析后的绝对路径原样交给 SDK 直接 spawn."""
    a = _adapter("runtime/qodercli/bin/qodercli")
    resolved = secure_sdk._sdk_cli_path(a)
    assert resolved == a.cli_path
    assert os.path.isabs(resolved)


def test_sdk_cli_path_command_name_pins_runtime_wrapper():
    """SDK 模式: 命令名/空 → 钉死项目内置 runtime wrapper(qodercn 体系)。

    绝不返回 None 交给 SDK 自由查找 —— 查找链(QODERCLI_PATH → bundled →
    PATH → ~/.npm-global → ~/.local/bin)会短路到宿主机残留的旧 Global CLI，
    与 qodercn SDK 协议不匹配导致 initialize 卡死(历史缺陷根因)。
    """
    for cfg in ("qodercli-does-not-exist-xyz", None, "qodercli"):
        resolved = secure_sdk._sdk_cli_path(_adapter(cfg))
        assert resolved == str(secure_sdk._RUNTIME_CLI), f"cfg={cfg!r} 须钉死 runtime wrapper"
        assert os.path.isabs(resolved)
        assert "runtime" in resolved and "qodercli" in resolved


def test_sdk_cli_path_missing_wrapper_fails_loud(monkeypatch, tmp_path):
    """runtime wrapper 缺失 → 显式报错(提示跑 setup.sh), 不静默回退 PATH 查找."""
    monkeypatch.setattr(secure_sdk, "_RUNTIME_CLI", tmp_path / "no-such-qodercli")
    with pytest.raises(FileNotFoundError, match="runtime/qodercli/setup.sh"):
        secure_sdk._sdk_cli_path(_adapter("qodercli"))


def test_resolve_cli_path_pins_qoder_commands_to_runtime_wrapper():
    """CLIProcessAdapter: qoder 系命令名/空配置也钉死 runtime wrapper.

    PATH 查找会短路到宿主机旧 Global CLI(~/.local/bin 等)，模型列表探测
    (list_models)会拿空/错网关模型(历史缺陷根因)。
    """
    expected = str(PROJECT_ROOT / "runtime" / "qodercli" / "bin" / "qodercli")
    for cfg in ("qodercli", "qoderclicn", "qodercn", None, ""):
        assert _adapter(cfg).cli_path == expected, f"cfg={cfg!r} 须钉死 runtime wrapper"
    # 非 qoder 系命令名不受影响(保留原行为)
    assert _adapter("qodercli-does-not-exist-xyz").cli_path == "qodercli-does-not-exist-xyz"


def test_build_env_maps_legacy_pat_to_cn_var(monkeypatch):
    """_build_env 把旧变量名 PAT 映射到 QODERCN_ 前缀(qodercn CLI 只认该名)."""
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pt-TESTONLY0001")
    monkeypatch.delenv("QODERCN_PERSONAL_ACCESS_TOKEN", raising=False)
    env = _adapter("qodercli")._build_env(None)
    assert env["QODERCN_PERSONAL_ACCESS_TOKEN"] == "pt-TESTONLY0001"


# ── 启动期核验/预热（一次代替每轮 `qodercli -v`）─────────────────────────────
# 背景：qodercn SDK 每次 connect 都额外 spawn 一次 `<cli> -v`（实测 1.3~1.8s、~320MB
# RSS），而结果只用于 logger.warning 且整段被 `except Exception: pass` 吞掉。
# 前置到启动期一次性核验的护栏：只有**核验通过**才置 QODER_SKIP_VERSION_CHECK，
# 任何失败都保持与改动前一致的每轮探测，并把原因显式回给调用方（不做静默降级）。


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_run(result=None, exc=None):
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        if exc is not None:
            raise exc
        return result
    _run.calls = calls
    return _run


@pytest.fixture
def _skip_flag(monkeypatch):
    monkeypatch.delenv("QODER_SKIP_VERSION_CHECK", raising=False)
    return lambda: os.environ.get("QODER_SKIP_VERSION_CHECK")


def test_verify_runtime_cli_success_sets_skip_flag(tmp_path, monkeypatch, _skip_flag):
    cli = tmp_path / "qodercli"
    cli.write_text("#!/bin/sh\n")
    monkeypatch.setattr(secure_sdk, "_RUNTIME_CLI", cli)
    monkeypatch.setattr(secure_sdk, "_sdk_minimum_runtime_version", lambda: (0, 2, 0))
    runner = _fake_run(result=_proc(stdout="qodercli 1.4.2"))
    monkeypatch.setattr(subprocess, "run", runner)

    assert secure_sdk.verify_runtime_cli() == {
        "ok": True, "cli_path": str(cli), "version": "1.4.2", "reason": ""}
    assert runner.calls == [[str(cli), "-v"]], "只探测内置 wrapper，不交给 PATH 查找"
    assert _skip_flag() == "1"


def test_verify_runtime_cli_missing_cli_does_not_skip_check(tmp_path, monkeypatch, _skip_flag):
    """未跑 setup.sh：显式提示可操作修复，绝不把跳过位置上去当作“没问题”。"""
    monkeypatch.setattr(secure_sdk, "_RUNTIME_CLI", tmp_path / "qodercli")
    result = secure_sdk.verify_runtime_cli()
    assert result["ok"] is False and "setup.sh" in result["reason"]
    assert _skip_flag() is None


@pytest.mark.parametrize("result, exc, minimum, expect", [
    (None, OSError("执行格式错误"), None, "无法执行"),
    (None, subprocess.TimeoutExpired("qodercli -v", 30), None, "超时"),
    (_proc(returncode=1, stderr="boom"), None, None, "退出码 1"),
    (_proc(stdout="not-a-version"), None, None, "无法解析"),
    (_proc(stdout="0.1.9"), None, (0, 2, 0), "低于 SDK 要求"),
])
def test_verify_runtime_cli_failures_keep_per_turn_check(
        tmp_path, monkeypatch, _skip_flag, result, exc, minimum, expect):
    cli = tmp_path / "qodercli"
    cli.write_text("#!/bin/sh\n")
    monkeypatch.setattr(secure_sdk, "_RUNTIME_CLI", cli)
    monkeypatch.setattr(secure_sdk, "_sdk_minimum_runtime_version", lambda: minimum)
    monkeypatch.setattr(subprocess, "run", _fake_run(result=result, exc=exc))

    checked = secure_sdk.verify_runtime_cli()
    assert checked["ok"] is False and expect in checked["reason"]
    assert _skip_flag() is None, "核验不通过必须保持每轮探测（失败可见，不假装成功）"


def test_verify_runtime_cli_reads_minimum_from_sdk_not_hardcoded(monkeypatch):
    """版本下限取 SDK 自身常量（升级 SDK 自动跟随），读不到时返回 None 而不编造。"""
    from qodercn_agent_sdk._internal.transport.subprocess_cli import MINIMUM_RUNTIME_VERSION
    parsed = secure_sdk._sdk_minimum_runtime_version()
    assert parsed == tuple(int(x) for x in str(MINIMUM_RUNTIME_VERSION).split(".")[:3])
    # 解析不出版本号（SDK 改了输出格式/常量）→ 跳过下限比对，不拿伪造版本拒启动
    monkeypatch.setattr(secure_sdk, "_CLI_VERSION_RE", re.compile(r"(?!x)x"))
    assert secure_sdk._sdk_minimum_runtime_version() is None


def test_startup_hooks_wired_in_both_resident_processes():
    """跳过位是**进程环境变量**：web 与 celery worker 必须各自核验。

    漏一个则该进程（如定时无人值守分析）仍每轮付 node 冷启动，且没人知道。
    """
    web = (PROJECT_ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    worker = (PROJECT_ROOT / "backend/modules/flow/tasks/celery_app.py").read_text(encoding="utf-8")
    assert "verify_runtime_cli" in web and "asyncio.create_task" in web
    assert "verify_runtime_cli" in worker and "worker_process_init" in worker


def test_build_env_keeps_explicit_cn_pat(monkeypatch):
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pt-OLD0002")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "pt-CN0002")
    env = _adapter("qodercli")._build_env(None)
    assert env["QODERCN_PERSONAL_ACCESS_TOKEN"] == "pt-CN0002", "显式 CN 变量优先"
