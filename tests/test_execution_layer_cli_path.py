"""执行层 cli_path 解析契约: 相对项目根/~/​$VAR/命令名, 保证换目录·换机器·容器部署可移植.

回归背景: DB 曾把 cli-qoder 的 cli_path 写死成机器绝对路径
(/home/.../runtime/qodercli/bin/qodercli), 项目一挪位置就失效。
修复后 cli_path 支持相对**项目根**解析, SDK 模式下命令名回退内置 bundled runtime。
"""
import os

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


def test_sdk_cli_path_command_name_becomes_none():
    """SDK 模式: 命令名/空 → None, 让 SDK 走内置 bundled runtime(子进程 env 无 PATH)."""
    assert secure_sdk._sdk_cli_path(_adapter("qodercli-does-not-exist-xyz")) is None
    assert secure_sdk._sdk_cli_path(_adapter(None)) is None or os.path.isabs(
        secure_sdk._sdk_cli_path(_adapter(None))
    )
