"""SDK 兼容层 — 统一加载 qoder-agent-sdk / claude-agent-sdk 的工具构造器.

两家 SDK 的 @tool / create_sdk_mcp_server 接口基本一致(
claude-agent-sdk 为原型,qoder-agent-sdk 与其对称),
本模块屏蔽 import 差异,使工具 handler 只写一份。
"""

import logging

logger = logging.getLogger(__name__)

SDK_BACKENDS = ("qoder", "claude")


def load_sdk(backend: str):
    """按后端加载 SDK 模块;未安装时抛 ImportError."""
    if backend == "claude":
        import claude_agent_sdk as sdk
    else:
        import qoder_agent_sdk as sdk
    return sdk


def make_tool(backend: str, name: str, description: str, input_schema: dict,
              handler, annotations: dict = None):
    """用指定 SDK 的 @tool 包装 handler.

    annotations(如 readOnlyHint)在个别 SDK 不支持时自动降级忽略。
    """
    sdk = load_sdk(backend)
    if annotations:
        try:
            return sdk.tool(name, description, input_schema, annotations=annotations)(handler)
        except TypeError:
            logger.warning("[sdk_tools] %s SDK tool() 不支持 annotations,已忽略", backend)
    return sdk.tool(name, description, input_schema)(handler)


def make_server(backend: str, name: str, tools: list):
    """用指定 SDK 构建进程内 MCP server."""
    return load_sdk(backend).create_sdk_mcp_server(name=name, tools=tools)
