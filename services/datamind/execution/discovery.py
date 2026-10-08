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
    os.path.expanduser("~/.local/bin"),
    "/usr/local/bin",
]


# 已知 CLI 白名单:binary / version_cmd / 默认命令模板 / 能力标签
# model_flag: 指定模型的命令行参数;models_cmd: 列出可用模型的命令
KNOWN_CLIS = {
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
