"""SDK 进程内自定义工具 — 通过 qoder-agent-sdk / claude-agent-sdk @tool 注册.

工具 handler 运行在 datamind 进程内,直接调用现有 service 层,
不走网络;工作空间/用户上下文由 SDK 适配器派发时经
ExecutionContextVar 注入(见 context.py)。

工具组(由 waker 能力按"逐个工具"粒度勾选控制启用):
- catalog:  search_metadata / get_table_schema / list_datasources
- semantic: get_metrics / get_glossary / query_by_tags / knowledge_search / run_semantic_query
- query:    execute_sql(受控旁路;统一语义层下默认不勾选,由 waker 显式授权)

设计理念:代码不再硬删任何工具组,工具的去留完全下放到 waker 的"工具权限"粒度配置
(waker.tools.mcp 逐工具勾选)。未选中的工具不会被注册进 MCP server,LLM 无从调用。
统一语义层仍是主路:prompt_composer 的 SEMANTIC_QUERY_RULES 引导 LLM 优先用
run_semantic_query;execute_sql 仅在 waker 显式勾选后才出现。

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
from services.datamind.execution.sdk_tools.ontology_tools import build_ontology_server
from services.datamind.execution.sdk_tools.query_tools import build_query_server
from services.datamind.execution.sdk_tools.screen_tools import build_screen_server
from services.datamind.execution.sdk_tools.semantic_tools import build_semantic_server

logger = logging.getLogger(__name__)

# 工具组名 → 构建函数(backend 参数选择 SDK,默认 qoder)
# 全部三组均可被 waker 逐工具粒度启用;是否注册由 waker.tools 决定。
TOOL_SERVER_BUILDERS = {
    "catalog": build_catalog_server,
    "semantic": build_semantic_server,
    "query": build_query_server,
    "ontology": build_ontology_server,
    "screen": build_screen_server,
}

# 工具组名 → server 名与工具名(用于 allowed_tools 精确预授权)
TOOL_SERVER_TOOLS = {
    "catalog": ("datahub_catalog", ["search_metadata", "get_table_schema", "list_datasources"]),
    "query": ("datahub_query", ["execute_sql"]),
    "semantic": ("datahub_semantic", [
        "get_metrics", "get_glossary", "query_by_tags", "knowledge_search",
        # Phase 3: 声明式语义查询工具（与 retrieval 同层，LLM 主路）
        "run_semantic_query",
    ]),
    "ontology": ("datahub_ontology", [
        "search_ontology", "get_ontology_model", "list_ontology_models",
        "get_metadata_summary", "generate_ontology_draft",
        "save_ontology_model", "activate_ontology_model", "import_ontology_yaml",
    ]),
    "screen": ("datahub_screen", [
        "create_data_screen", "get_data_screen", "update_data_screen_chart",
        "list_vis_components", "get_vis_component", "save_vis_component",
    ]),
}


def build_tool_servers(backend: str, enabled, selection=None) -> dict:
    """构建进程内自定义工具 server.

    Args:
        backend: SDK 后端("qoder" / "claude")
        enabled: 粗粒度工具组名列表(["catalog","semantic"] 或 "all"/None 全部启用);
                 selection 为空时按组整组注册(向后兼容)。
        selection: 细粒度 {group: [tool_name, ...]} 选择;非空时按"逐工具"注册,
                 只把被勾选的工具塞进对应 server,并从 allowed_tools 精确生成。

    Returns:
        {"servers": {server 名: McpSdkServerConfig},
         "allowed_tools": [mcp__server__tool 精确名...]}
    """
    servers: dict = {}
    allowed_tools: list[str] = []

    if selection:
        # 细粒度:waker 逐工具勾选。仅构建有≥一个被选工具的组。
        for group, tools in selection.items():
            names = [t for t in (tools or []) if t]
            builder = TOOL_SERVER_BUILDERS.get(group)
            if not builder or not names:
                continue
            try:
                cfg = builder(backend, tool_names=names)
                servers[cfg["name"]] = cfg
                srv_name, _ = TOOL_SERVER_TOOLS[group]
                allowed_tools.extend(f"mcp__{srv_name}__{t}" for t in names)
            except Exception as e:
                logger.warning("[sdk_tools] Build granular group '%s' failed: %s", group, e)
        return {"servers": servers, "allowed_tools": allowed_tools}

    # 向后兼容:按工具组整组注册
    if enabled in (None, "", "all"):
        groups = list(TOOL_SERVER_BUILDERS.keys())
    else:
        groups = [g for g in enabled if g in TOOL_SERVER_BUILDERS]
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
    "build_ontology_server",
    "build_screen_server",
    "ExecutionContextVar",
    "get_execution_context",
    "set_execution_context",
]
