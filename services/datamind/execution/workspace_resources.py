"""工作空间资源加载 — 各 SDK 适配器共享.

按 workspace_id(含全局 workspace_id=0)从 adh_mcp_servers / adh_agents
加载原始行;目标格式因 CLI 而异(qoder SDK options vs opencode.json),
由各适配器自行映射。
"""

import logging

logger = logging.getLogger(__name__)


def load_workspace_resources(workspace_id: int) -> dict:
    """加载工作空间的 MCP servers / agents 原始行.

    Returns:
        {"mcp_rows": [...], "agent_rows": [...]}
    """
    from services.shared.common.db import execute_query

    res: dict = {"mcp_rows": [], "agent_rows": []}
    if not workspace_id:
        return res
    try:
        res["mcp_rows"] = execute_query(
            "SELECT name, transport, url, command, args, env FROM adh_mcp_servers "
            "WHERE is_active=1 AND workspace_id IN (%s, 0)",
            (workspace_id,),
        )
        res["agent_rows"] = execute_query(
            "SELECT name, display_name, description, system_prompt, tools, mcp_server_ids "
            "FROM adh_agents WHERE is_active=1 AND workspace_id IN (%s, 0)",
            (workspace_id,),
        )
    except Exception as e:
        logger.warning("Load workspace resources failed (ws=%s): %s", workspace_id, e)
    return res


def parse_json_field(value, default):
    """DB JSON 字段兼容解析(可能已是 dict/list,也可能是字符串)."""
    import json

    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
