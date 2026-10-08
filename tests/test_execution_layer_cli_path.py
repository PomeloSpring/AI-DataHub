"""执行层 cli_path 解析契约: 相对项目根/~/​$VAR/命令名, 保证换目录·换机器·容器部署可移植.

回归背景: DB 曾把 cli-qoder 的 cli_path 写死成机器绝对路径
(/home/.../runtime/qodercli/bin/qodercli), 项目一挪位置就失效。
修复后 cli_path 支持相对**项目根**解析, SDK 模式下命令名/空配置钉死项目内置 runtime wrapper。
"""
import os

import pytest

from services.datamind.execution import secure_sdk
from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.shared.common.config import PROJECT_ROOT


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


def test_build_env_keeps_explicit_cn_pat(monkeypatch):
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pt-OLD0002")
    monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "pt-CN0002")
    env = _adapter("qodercli")._build_env(None)
    assert env["QODERCN_PERSONAL_ACCESS_TOKEN"] == "pt-CN0002", "显式 CN 变量优先"
