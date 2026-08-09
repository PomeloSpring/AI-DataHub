"""CLIDiscovery — 自动发现物理机上的已知 CLI 工具.

仅扫描白名单内的 CLI(KNOWN_CLIS),不执行任意命令。
每个 CLI 携带默认命令模板({question} 为任务问题占位符),
管理员可在执行层配置中覆盖。
"""

import asyncio
import logging
import os
import shutil

from services.datamind.execution.models import DiscoveredCLI

logger = logging.getLogger(__name__)

# PATH 之外额外扫描的常见安装目录
EXTRA_SEARCH_DIRS = [
    os.path.expanduser("~/.qodersec/bin"),
    os.path.expanduser("~/.opencode/bin"),   # opencode 官方安装脚本默认路径
    os.path.expanduser("~/.local/bin"),
    "/usr/local/bin",
]


def claude_bundled_cli() -> str:
    """claude-agent-sdk 内置运行时二进制路径(自包含原生二进制,无需 Node).

    未安装 SDK 或运行时缺失时返回空串。
    """
    try:
        import claude_agent_sdk
        p = os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude")
        return p if os.path.isfile(p) and os.access(p, os.X_OK) else ""
    except Exception:
        return ""


# claude 执行层无外部 CLI,运行时随 pip 包内置;将其目录纳入扫描以便发现页展示
_bundled_cli = claude_bundled_cli()
if _bundled_cli:
    _bundled_dir = os.path.dirname(_bundled_cli)
    if _bundled_dir not in EXTRA_SEARCH_DIRS:
        EXTRA_SEARCH_DIRS.append(_bundled_dir)

# 已知 CLI 白名单:binary / version_cmd / 默认命令模板 / 能力标签
# model_flag: 指定模型的命令行参数;models_cmd: 列出可用模型的命令
KNOWN_CLIS = {
    "opencode": {
        "binary": "opencode",
        "version_cmd": ["opencode", "--version"],
        "command": ["opencode", "run", "{question}"],
        "model_flag": ["--model"],
        "models_cmd": [],
        "capabilities": ["code", "search", "read", "write", "mcp"],
        "display_name": "OpenCode",
    },
    "qoder": {
        "binary": "qodercli",
        "aliases": ["qoder"],
        "version_cmd": ["qodercli", "--version"],
        "command": ["qodercli", "-p", "{question}"],
        "model_flag": ["-m"],
        "models_cmd": ["qodercli", "--list-models"],
        "capabilities": ["code", "search", "read", "write", "mcp"],
        "display_name": "Qoder CLI",
    },
    # claude-agent-sdk:无子进程命令模板(仅 mode=sdk),运行时随 SDK 内置
    "claude": {
        "binary": "claude",
        "version_cmd": ["claude", "--version"],
        "command": [],
        "model_flag": [],
        "models_cmd": [],
        "capabilities": ["code", "search", "read", "write", "mcp", "sdk"],
        "display_name": "Claude Agent SDK",
    },
}


class CLIDiscovery:
    """扫描 PATH 中的已知 CLI 工具."""

    async def discover(self) -> list[DiscoveredCLI]:
        discovered = []
        for name, info in KNOWN_CLIS.items():
            candidates = [info["binary"]] + info.get("aliases", [])
            path = None
            for c in candidates:
                path = shutil.which(c)
                if path:
                    break
            if not path:
                # 扫描 PATH 之外的常见安装目录
                for d in EXTRA_SEARCH_DIRS:
                    for c in candidates:
                        p = os.path.join(d, c)
                        if os.path.isfile(p) and os.access(p, os.X_OK):
                            path = p
                            break
                    if path:
                        break
            if not path:
                continue

            # 以实际找到的可执行文件路径校准版本命令与命令模板
            version_cmd = [path] + list(info.get("version_cmd", ["--version"]))[1:]
            command = [path if part == info["binary"] else part for part in info.get("command", [])]

            version = await self._get_version(version_cmd)
            discovered.append(DiscoveredCLI(
                name=name,
                path=path,
                version=version or "",
                capabilities=info.get("capabilities", []),
                default_command=command,
            ))
            logger.info("[CLIDiscovery] Found %s at %s (version: %s)", name, path, version)
        return discovered

    async def _get_version(self, version_cmd: list, timeout: int = 10) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *version_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = out_b.decode(errors="replace").strip()
            return out.splitlines()[0] if out else ""
        except Exception as e:
            logger.warning("[CLIDiscovery] Version check failed for %s: %s", version_cmd, e)
            return ""
