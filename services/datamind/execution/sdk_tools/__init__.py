"""SDK 进程内自定义工具 — 通过 qoder-agent-sdk / claude-agent-sdk @tool 注册.

工具 handler 运行在 datamind 进程内,直接调用现有 service 层,
不走网络;工作空间/用户上下文由 SDK 适配器派发时经
ExecutionContextVar 注入(见 context.py)。

工具组(执行层 config.sdk_tools 控制启用):
- catalog: search_metadata / get_table_schema / list_datasources
- query:   execute_sql(带校验 + 权限 + 审计)
- semantic: get_metrics / get_glossary / query_by_tags / knowledge_search

handler 只写一份(SDK 无关),经 compat.py 用指定后端
(qoder / claude)的 @tool 包装;见 build_tool_servers()。
"""

import logging

from services.datamind.execution.sdk_tools.catalog_tools import build_catalog_server
from services.datamind.execution.sdk_tools.context import (
    ExecutionContextVar,
    get_execution_context,
    set_execution_context,
)
from services.datamind.execution.sdk_tools.query_tools import build_query_server
from services.datamind.execution.sdk_tools.semantic_tools import build_semantic_server

logger = logging.getLogger(__name__)

# 工具组名 → 构建函数(backend 参数选择 SDK,默认 qoder)
TOOL_SERVER_BUILDERS = {
    "catalog": build_catalog_server,
    "query": build_query_server,
    "semantic": build_semantic_server,
}

# 工具组名 → server 名与工具名(用于 allowed_tools 精确预授权)
TOOL_SERVER_TOOLS = {
    "catalog": ("datahub_catalog", ["search_metadata", "get_table_schema", "list_datasources"]),
    "query": ("datahub_query", ["execute_sql"]),
    "semantic": ("datahub_semantic", ["get_metrics", "get_glossary", "query_by_tags", "knowledge_search"]),
}


def build_tool_servers(backend: str, enabled) -> dict:
    """按执行层 config.sdk_tools 构建进程内自定义工具 server.

    Args:
        backend: SDK 后端("qoder" / "claude")
        enabled: 工具组名列表(["catalog", "query"] 或 "all"/None 全部启用)

    Returns:
        {"servers": {server 名: McpSdkServerConfig},
         "allowed_tools": [mcp__server__tool 精确名...]}
    """
    if enabled in (None, "", "all"):
        groups = list(TOOL_SERVER_BUILDERS.keys())
    else:
        groups = [g for g in enabled if g in TOOL_SERVER_BUILDERS]
    servers: dict = {}
    allowed_tools: list[str] = []
    for g in groups:
        try:
            cfg = TOOL_SERVER_BUILDERS[g](backend)
            servers[cfg["name"]] = cfg
            srv_name, tool_names = TOOL_SERVER_TOOLS[g]
            allowed_tools.extend(f"mcp__{srv_name}__{t}" for t in tool_names)
        except Exception as e:
            logger.warning("[sdk_tools] Build tool group '%s' (%s) failed: %s", g, backend, e)
    return {"servers": servers, "allowed_tools": allowed_tools}


__all__ = [
    "TOOL_SERVER_BUILDERS",
    "TOOL_SERVER_TOOLS",
    "build_tool_servers",
    "build_catalog_server",
    "build_query_server",
    "build_semantic_server",
    "ExecutionContextVar",
    "get_execution_context",
    "set_execution_context",
]
