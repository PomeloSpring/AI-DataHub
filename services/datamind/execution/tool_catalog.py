"""标准工具目录 — 工作空间执行层 tools 权限白名单的通用抽象.

命名与 opencode 内置工具保持一致(bash/edit/glob/grep/read/write/webfetch/task),
经 TOOL_NAME_MAP 映射到各执行层后端的实际工具名(qoder 为 Pascal 命名,
builtin 为内置 Agent 的实现名).

opencode 的 `list`(目录列表)能力已并入 `glob`,不单独列目录项.
"""

import json

# 标准工具目录(name 与 opencode 工具名一致),供前端渲染勾选 chips
TOOL_CATALOG = [
    {"name": "read", "label": "读取文件", "description": "读取文本文件内容"},
    {"name": "write", "label": "写入文件", "description": "创建或覆盖写入文件"},
    {"name": "edit", "label": "编辑文件", "description": "对已有文件做精确文本替换"},
    {"name": "glob", "label": "文件检索", "description": "按通配符匹配文件/列出目录"},
    {"name": "grep", "label": "内容搜索", "description": "按正则在文件内容中搜索"},
    {"name": "bash", "label": "命令执行", "description": "执行 shell 命令/代码运行"},
    {"name": "webfetch", "label": "网页抓取", "description": "抓取网页内容"},
    {"name": "task", "label": "子任务", "description": "派发子代理/子任务"},
]

# 标准名 → 各后端实际工具名;None 表示该后端无此工具
TOOL_NAME_MAP = {
    # opencode 内置工具名(与标准名一致)
    "opencode": {
        "read": "read", "write": "write", "edit": "edit", "glob": "glob",
        "grep": "grep", "bash": "bash", "webfetch": "webfetch", "task": "task",
    },
    # qoder CLI / qoder-agent-sdk 工具名(Claude Code 风格)
    "qoder": {
        "read": "Read", "write": "Write", "edit": "Edit", "glob": "Glob",
        "grep": "Grep", "bash": "Bash", "webfetch": "WebFetch", "task": "Task",
    },
    # claude-agent-sdk 工具名(Claude Code 原生,Pascal 命名)
    "claude": {
        "read": "Read", "write": "Write", "edit": "Edit", "glob": "Glob",
        "grep": "Grep", "bash": "Bash", "webfetch": "WebFetch", "task": "Task",
    },
    # 内置 Agent(ConfigurableAgent)的实现名(BuiltinToolbox)
    "builtin": {
        "read": "read", "write": "write", "edit": "edit", "glob": "glob",
        "grep": "grep", "bash": "bash", "webfetch": "webfetch", "task": None,
    },
}

# 历史命名兼容别名(旧版目录名 list/search 与内置旧实现名)
LEGACY_ALIASES = {
    "list": "glob", "search": "grep",
    "read_file": "read", "write_file": "write",
    "list_directory": "glob", "search_files": "grep",
}

# 标准名有序列表(按目录顺序,保证各后端输出顺序稳定)
CANONICAL_NAMES = [t["name"] for t in TOOL_CATALOG]


def _canonicalize(names) -> list[str]:
    """白名单条目 → (标准名, 原始具体名) 列表.

    历史命名(list/search 等)归一到标准名;其余名称原样作为
    具体工具名保留(如 mcp__srv__tool、run_code).
    """
    out = []
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        canonical = LEGACY_ALIASES.get(n)
        if canonical is None and n in CANONICAL_NAMES:
            canonical = n
        out.append((canonical, n))
    return out


def parse_allowed_tools(raw) -> list[str]:
    """把 DB JSON 列/原始值解析为 list(兼容空值与字符串)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    return []


def expand_allowed_tools(allowed_tools, flavor: str) -> set:
    """白名单 → 该后端允许的具体工具名集合.

    标准名按 TOOL_NAME_MAP 映射;具体工具名(如 MCP 工具)原样保留.
    某后端不存在的标准工具(映射为 None)自动跳过.
    """
    allowed = parse_allowed_tools(allowed_tools)
    if not allowed:
        return set()
    mapping = TOOL_NAME_MAP.get(flavor, {})
    result = set()
    for canonical, raw in _canonicalize(allowed):
        if canonical:
            concrete = mapping.get(canonical)
            if concrete:
                result.add(concrete)
        else:
            result.add(raw)
    return result


def disallowed_tools(allowed_tools, flavor: str) -> list:
    """白名单 → 该后端目录内未被允许的工具列表(deny-list).

    用于 qoder/opencode:只禁用目录内未勾选的标准工具,
    不影响 MCP/自定义等目录外工具.
    """
    allowed = parse_allowed_tools(allowed_tools)
    if not allowed:
        return []
    allowed_set = expand_allowed_tools(allowed, flavor)
    mapping = TOOL_NAME_MAP.get(flavor, {})
    return [
        concrete for name in CANONICAL_NAMES
        if (concrete := mapping.get(name)) and concrete not in allowed_set
    ]
