"""SDK 进程内自定义工具 — 通过 qodercn-agent-sdk @tool 注册.

工具 handler 运行在 datamind 进程内,直接调用现有 service 层,
不走网络;工作空间/用户上下文由 SDK 适配器派发时经
ExecutionContextVar 注入(见 context.py)。

工具组(由 AS-BOT 能力按"逐个工具"粒度勾选控制启用):
- catalog:  search_metadata / get_table_schema / list_datasources
- semantic: get_metrics / get_glossary / query_by_tags / knowledge_search / run_semantic_query
- query:    check_sql(治理预检) / execute_sql(受治理只读执行;由 AS-BOT 显式授权)

设计理念:代码不再硬删任何工具组,工具的去留完全下放到 AS-BOT 的"工具权限"粒度配置
(as_bot.tools.mcp 逐工具勾选)。未选中的工具不会被注册进 MCP server,LLM 无从调用。
统一语义层仍是主路:prompt_composer 的 SEMANTIC_QUERY_RULES 引导 LLM 优先用
run_semantic_query;execute_sql 仅在 AS-BOT 显式勾选后才出现。

handler 只写一份(SDK 无关),经 compat.py 用指定后端
(qoder / claude)的 @tool 包装;见 build_tool_servers()。
"""

import logging

from services.datamind.execution.sdk_tools.catalog_tools import build_catalog_server
from services.datamind.execution.sdk_tools.asset_tools import build_assets_server
from services.datamind.execution.sdk_tools.context import (
    ExecutionContextVar,
    get_execution_context,
    set_execution_context,
)
from services.datamind.execution.sdk_tools.ontology_tools import build_ontology_server
from services.datamind.execution.sdk_tools.query_tools import build_query_server
from services.datamind.execution.sdk_tools.screen_tools import build_screen_server
from services.datamind.execution.sdk_tools.semantic_tools import build_semantic_server
from services.datamind.execution.sdk_tools.system_tools import build_system_server

logger = logging.getLogger(__name__)

# 工具组名 → 构建函数(backend 参数选择 SDK,默认 qoder)
# 全部三组均可被 AS-BOT 逐工具粒度启用;是否注册由 as_bot.tools 决定。
TOOL_SERVER_BUILDERS = {
    "catalog": build_catalog_server,
    "semantic": build_semantic_server,
    "query": build_query_server,
    "ontology": build_ontology_server,
    "screen": build_screen_server,
    "system": build_system_server,
    "assets": build_assets_server,
}

# 工具组名 → server 名与工具名(用于 allowed_tools 精确预授权)
TOOL_SERVER_TOOLS = {
    "catalog": ("datahub_catalog", ["search_metadata", "get_table_schema", "list_datasources"]),
    "query": ("datahub_query", ["check_sql", "execute_sql"]),
    "semantic": ("datahub_semantic", [
        "get_metrics", "get_glossary", "query_by_tags", "knowledge_search",
        # Phase 3: 声明式语义查询工具（与 retrieval 同层，LLM 主路）
        "run_semantic_query", "get_business_semantics", "search_business_knowledge",
    ]),
    "ontology": ("datahub_ontology", [
        "search_ontology", "get_ontology_model", "list_ontology_models",
        "get_metadata_summary", "generate_ontology_draft", "save_ontology_draft",
        "save_ontology_model", "activate_ontology_model", "import_ontology_yaml",
    ]),
    "screen": ("datahub_screen", [
        "create_data_screen", "get_data_screen", "update_data_screen_chart",
        "request_dashboard_design", "get_dashboard_design", "prepare_dashboard_design",
        "list_vis_components", "get_vis_component", "save_vis_component",
    ]),
    "system": ("datahub_system", ["system_usage", "system_overview"]),
    "assets": ("datahub_assets", [
        "asset_list", "asset_get", "asset_archive", "disk_status",
        "cleanup_candidates", "cleanup_execute",
    ]),
}


def build_tool_servers(backend: str, enabled, selection=None, function_names=None) -> dict:
    """构建进程内自定义工具 server.

    Args:
        backend: SDK 后端("qoder" / "claude")
        enabled: 粗粒度工具组名列表(["catalog","semantic"] 或 "all"/None 全部启用);
                 selection 为空时按组整组注册(向后兼容)。
        selection: 细粒度 {group: [tool_name, ...]} 选择;非空时按"逐工具"注册,
                 只把被勾选的工具塞进对应 server, 并从 allowed_tools 精确生成。
                 这是 **B 维度(系统数据工具)**, 授权来自 adh_as_bots.tools.mcp。
        function_names: **A 维度(功能能力)** 的工具名清单, 授权来自角色权限码
                 自动继承(tool_policy.compile_policy 经 perm_link 裁定后传入),
                 **不走** as_bot.tools 配置 —— AS-BOT 仅能做减法。

    Returns:
        {"servers": {server 名: McpSdkServerConfig},
         "allowed_tools": [mcp__server__tool 精确名...]}
    """
    servers: dict = {}
    allowed_tools: list[str] = []

    # A 维度：功能能力（角色权限码自动继承）。与 B 维度的数据工具**分开注册**，
    # 互不干扰 —— 故意不把它塞进 TOOL_SERVER_TOOLS，避免被 as_bot.tools.mcp 勾选
    # （那就变成“每个 AS-BOT 自己开功能”而不是“跟角色权限走”了）。
    if function_names:
        from services.datamind.execution.function_tools import SERVER_NAME, build_function_server
        try:
            cfg = build_function_server(backend, tool_names=list(function_names))
            if cfg:
                servers[cfg["name"]] = cfg
                allowed_tools.extend(f"mcp__{SERVER_NAME}__{t}" for t in function_names)
        except Exception as e:
            logger.exception("[sdk_tools] 功能能力工具初始化失败")
            raise RuntimeError("已授权功能工具初始化失败") from e

    if selection is not None:
        # 显式空选择不得回退到整组注册。
        for group, tools in selection.items():
            names = [t for t in (tools or []) if t]
            builder = TOOL_SERVER_BUILDERS.get(group)
            if not builder or set(names) - set(TOOL_SERVER_TOOLS[group][1]):
                raise ValueError("不允许的 AS-BOT 工具配置")
            if not names:
                continue
            try:
                cfg = builder(backend, tool_names=names)
                servers[cfg["name"]] = cfg
                srv_name, _ = TOOL_SERVER_TOOLS[group]
                allowed_tools.extend(f"mcp__{srv_name}__{t}" for t in names)
            except Exception as e:
                logger.exception("[sdk_tools] 工具初始化失败: %s", group)
                raise RuntimeError("已授权工具初始化失败") from e
        return {"servers": servers, "allowed_tools": allowed_tools}

    # 整组隐式注册已停用：工具一律按"逐工具"显式授权（selection）。
    # 整组注册表达不了"组里只开一个工具"，很容易被误配成整组放开；
    # 且它在生产已无调用方（secure_sdk 恒传 selection）。保留空入参向后兼容。
    if enabled not in (None, "", []):
        raise ValueError("禁止整组注册工具，请使用逐工具授权（selection）")
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
    "build_system_server",
    "build_assets_server",
    "ExecutionContextVar",
    "get_execution_context",
    "set_execution_context",
]
