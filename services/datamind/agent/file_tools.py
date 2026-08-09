"""Builtin Toolbox — 内置 Agent 工具集(工作空间沙箱).

为平台自研 Agent(ConfigurableAgent)提供与 opencode/qoder 对齐的
内置工具,命名与 opencode 保持一致:
    read / write / edit / glob / grep / bash / webfetch

安全约束:
- 文件类工具限制在工作空间根目录 data/workspaces/ws_{workspace_id} 内,
  路径解析后校验(realpath),禁止 .. / 符号链接逃逸
- bash 以工作空间目录为 cwd 执行,超时与输出长度有上限
- 读取/抓取内容有大小上限,避免超大输出撑爆上下文
"""

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_READ_CHARS = 100_000
MAX_WRITE_CHARS = 200_000
MAX_SEARCH_RESULTS = 100
BASH_TIMEOUT_CAP = 120
BASH_OUTPUT_CAP = 100_000

# 工作空间文件根目录:<项目根>/data/workspaces
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKSPACES_DIR = _PROJECT_ROOT / "data" / "workspaces"


def workspace_root(workspace_id: int) -> Path:
    """工作空间文件根目录(不存在则创建);workspace_id=0 用 global."""
    name = f"ws_{workspace_id}" if workspace_id else "global"
    root = WORKSPACES_DIR / name
    root.mkdir(parents=True, exist_ok=True)
    return root


class BuiltinToolbox:
    """工作空间沙箱内的内置工具集(命名对齐 opencode)."""

    def __init__(self, workspace_id: int = 0):
        self.workspace_id = workspace_id or 0
        self.root = workspace_root(self.workspace_id)

    def _resolve(self, path: str) -> Path:
        """将相对路径解析到沙箱内;逃逸沙箱时抛 ValueError."""
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        resolved = p.resolve()
        root_resolved = self.root.resolve()
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise ValueError(f"路径越出工作空间目录: {path}")
        return resolved

    # ── read ─────────────────────────────────────────────────────

    def read(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        """读取文本文件,按行返回(offset 起始行,limit 行数)."""
        p = self._resolve(path)
        if not p.is_file():
            return f"错误: 文件不存在: {path}"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"错误: 读取失败: {e}"
        lines = text.splitlines()
        start = max(0, offset)
        chunk = lines[start:start + max(1, limit)]
        out = "\n".join(chunk)
        if len(out) > MAX_READ_CHARS:
            out = out[:MAX_READ_CHARS] + "\n...(内容过长已截断)"
        header = f"[{path}] 共 {len(lines)} 行,显示第 {start + 1}-{start + len(chunk)} 行"
        return f"{header}\n{out}"

    # ── write ────────────────────────────────────────────────────

    def write(self, path: str, content: str) -> str:
        """创建或覆盖写入文本文件(自动创建父目录)."""
        if len(content) > MAX_WRITE_CHARS:
            return f"错误: 写入内容超过 {MAX_WRITE_CHARS} 字符上限"
        p = self._resolve(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            return f"错误: 写入失败: {e}"
        return f"已写入 {path}({len(content)} 字符,{content.count(chr(10)) + 1} 行)"

    # ── edit ─────────────────────────────────────────────────────

    def edit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """精确替换文件中的文本片段;默认要求 old_string 唯一匹配."""
        p = self._resolve(path)
        if not p.is_file():
            return f"错误: 文件不存在: {path}"
        if not old_string:
            return "错误: old_string 不能为空"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"错误: 读取失败: {e}"
        count = text.count(old_string)
        if count == 0:
            return "错误: 未找到匹配的文本片段"
        if count > 1 and not replace_all:
            return f"错误: 匹配到 {count} 处,请提供更精确的 old_string 或设置 replace_all=true"
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        if len(new_text) > MAX_WRITE_CHARS:
            return f"错误: 修改后内容超过 {MAX_WRITE_CHARS} 字符上限"
        try:
            p.write_text(new_text, encoding="utf-8")
        except Exception as e:
            return f"错误: 写入失败: {e}"
        replaced = count if replace_all else 1
        return f"已修改 {path}(替换 {replaced} 处)"

    # ── glob ─────────────────────────────────────────────────────

    def glob(self, path: str = ".", pattern: str = "") -> str:
        """列出目录内容;pattern 为通配符时匹配文件(如 *.csv、**/*.md)."""
        base = self._resolve(path)
        try:
            if pattern:
                matches = sorted(
                    str(m.relative_to(self.root.resolve()))
                    for m in base.glob(pattern) if m.is_file()
                )[:500]
                return "\n".join(matches) if matches else "(无匹配文件)"
            if not base.is_dir():
                return f"错误: 目录不存在: {path}"
            entries = []
            for item in sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name)):
                prefix = "📁 " if item.is_dir() else "   "
                size = "" if item.is_dir() else f" ({item.stat().st_size}B)"
                entries.append(f"{prefix}{item.name}{size}")
            return "\n".join(entries) if entries else "(空目录)"
        except Exception as e:
            return f"错误: 检索失败: {e}"

    # ── grep ─────────────────────────────────────────────────────

    def grep(self, pattern: str, path: str = ".", glob: str = "") -> str:
        """在文件内容中按正则搜索,返回 文件:行号:内容."""
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"错误: 非法正则: {e}"
        base = self._resolve(path)
        files = base.rglob(glob) if glob else base.rglob("*")
        results: list[str] = []
        try:
            for f in sorted(files):
                if not f.is_file() or f.stat().st_size > 2_000_000:
                    continue
                try:
                    for i, line in enumerate(
                        f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                    ):
                        if regex.search(line):
                            rel = f.relative_to(self.root.resolve())
                            results.append(f"{rel}:{i}: {line.strip()[:300]}")
                            if len(results) >= MAX_SEARCH_RESULTS:
                                results.append(f"...(超过 {MAX_SEARCH_RESULTS} 条,已截断)")
                                return "\n".join(results)
                except Exception:
                    continue
        except Exception as e:
            return f"错误: 搜索失败: {e}"
        return "\n".join(results) if results else "(无匹配)"

    # ── bash ─────────────────────────────────────────────────────

    def bash(self, command: str, timeout: int = 60) -> str:
        """在工作空间目录内执行 shell 命令(超时/输出长度受限)."""
        if not command.strip():
            return "错误: 命令不能为空"
        timeout = min(max(1, int(timeout or 60)), BASH_TIMEOUT_CAP)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.root),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"错误: 命令执行超时({timeout}s,上限 {BASH_TIMEOUT_CAP}s)"
        except Exception as e:
            return f"错误: 命令执行失败: {e}"
        parts = [f"exit code: {proc.returncode}"]
        if proc.stdout:
            parts.append(f"stdout:\n{proc.stdout[:BASH_OUTPUT_CAP]}")
        if proc.stderr:
            parts.append(f"stderr:\n{proc.stderr[:BASH_OUTPUT_CAP]}")
        return "\n\n".join(parts)

    # ── webfetch ─────────────────────────────────────────────────

    def webfetch(self, url: str) -> str:
        """抓取网页内容(HTML 粗提取正文文本,大小受限)."""
        import httpx

        if not url.startswith(("http://", "https://")):
            return "错误: 仅支持 http/https 链接"
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (AI-DataHub Agent)"})
            resp.raise_for_status()
        except Exception as e:
            return f"错误: 抓取失败: {e}"
        text = resp.text
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype:
            text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", "", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n...(内容过长已截断)"
        return f"[{url}] status={resp.status_code}\n{text}"

    # ── 工具定义(Anthropic tool_use 格式)与分发 ─────────────────

    @staticmethod
    def tool_definitions() -> list[dict]:
        return [
            {
                "name": "read",
                "description": "读取工作空间目录内的文本文件内容(按行返回)。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径(相对工作空间目录)"},
                        "offset": {"type": "integer", "description": "起始行号(从 0 开始,默认 0)"},
                        "limit": {"type": "integer", "description": "读取行数(默认 2000)"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write",
                "description": "在工作空间目录内创建或覆盖写入文本文件,可用于保存分析结果、报告等。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径(相对工作空间目录)"},
                        "content": {"type": "string", "description": "文件完整内容"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "edit",
                "description": "对工作空间目录内已有文件做精确文本替换(默认要求片段唯一匹配)。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "old_string": {"type": "string", "description": "要替换的原文本(需精确匹配)"},
                        "new_string": {"type": "string", "description": "替换后的新文本"},
                        "replace_all": {"type": "boolean", "description": "是否替换全部匹配(默认 false)"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
            {
                "name": "glob",
                "description": "列出工作空间目录内的文件与子目录;可用通配符匹配文件(如 *.csv、**/*.md)。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径(默认当前目录)"},
                        "pattern": {"type": "string", "description": "通配符(如 *.csv、**/*.md),提供时按模式匹配文件"},
                    },
                },
            },
            {
                "name": "grep",
                "description": "按正则在工作空间文件内容中搜索,返回匹配的文件、行号与内容。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "正则表达式"},
                        "path": {"type": "string", "description": "搜索起始目录(默认工作空间根目录)"},
                        "glob": {"type": "string", "description": "文件名过滤(如 *.md)"},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "bash",
                "description": "在工作空间目录内执行 shell 命令(如数据清洗、文件批处理),返回 stdout/stderr。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的 shell 命令"},
                        "timeout": {"type": "integer", "description": f"超时秒数(默认 60,上限 {BASH_TIMEOUT_CAP})"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "webfetch",
                "description": "抓取 http/https 网页内容并返回正文文本。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "网页 URL(http/https)"},
                    },
                    "required": ["url"],
                },
            },
        ]

    def dispatch(self, tool_name: str, arguments: dict) -> str:
        """分发内置工具调用;非工具箱工具返回 None."""
        a = arguments or {}
        handlers = {
            "read": lambda: self.read(a.get("path", ""), int(a.get("offset") or 0), int(a.get("limit") or 2000)),
            "write": lambda: self.write(a.get("path", ""), a.get("content", "")),
            "edit": lambda: self.edit(a.get("path", ""), a.get("old_string", ""), a.get("new_string", ""), bool(a.get("replace_all"))),
            "glob": lambda: self.glob(a.get("path") or ".", a.get("pattern") or ""),
            "grep": lambda: self.grep(a.get("pattern", ""), a.get("path") or ".", a.get("glob") or ""),
            "bash": lambda: self.bash(a.get("command", ""), int(a.get("timeout") or 60)),
            "webfetch": lambda: self.webfetch(a.get("url", "")),
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return None
        try:
            return handler()
        except ValueError as e:
            return f"错误: {e}"
        except Exception as e:
            logger.warning("[BuiltinToolbox] %s failed: %s", tool_name, e)
            return f"错误: {e}"


# 兼容旧引用
FileToolbox = BuiltinToolbox
