"""SDK 兼容层 — 统一加载 qoder-agent-sdk / claude-agent-sdk 的工具构造器.

两家 SDK 的 @tool / create_sdk_mcp_server 接口基本一致(
claude-agent-sdk 为原型,qoder-agent-sdk 与其对称),
本模块屏蔽 import 差异,使工具 handler 只写一份。
"""

import logging

logger = logging.getLogger(__name__)

SDK_BACKENDS = ("qoder", "claude")

# 可写 SQL 的 Waker（授权了 execute_sql）需要真实表/列元数据来拼 SQL；
# 其 search_metadata / get_table_schema 走原始 handler（物理元数据，已剥除 datasource_id），
# 不走本体业务投影。语义层 Waker（无 execute_sql）仍只回业务本体视图。
PHYSICAL_METADATA_TOOLS = {"search_metadata", "get_table_schema"}


def _waker_can_write_sql(runtime) -> bool:
    policy = getattr(runtime, "policy", None)
    allowed = getattr(policy, "allowed", None) or ()
    return any(str(t).endswith("__execute_sql") for t in allowed)


def load_sdk(backend: str):
    """按后端加载 SDK 模块;未安装时抛 ImportError."""
    if backend == "claude":
        import claude_agent_sdk as sdk
    else:
        import qoder_agent_sdk as sdk
    return sdk


def make_tool(backend: str, name: str, description: str, input_schema: dict,
              handler, annotations: dict = None, qualified_name: str = ""):
    """用指定 SDK 的 @tool 包装 handler.

    annotations(如 readOnlyHint)在个别 SDK 不支持时自动降级忽略。
    """
    import asyncio
    from services.dataviz.services.dashboard_design_service import DesignError
    from services.datamind.execution.resource_guard import ResourceScopeError
    from services.datamind.execution.sdk_tools.context import get_execution_context
    from services.datamind.execution.tool_policy import check_tool
    from services.datamind.execution.session_workspace import run_owned_sync
    from services.datamind.execution.sdk_tools import TOOL_SERVER_TOOLS
    if not qualified_name:
        qualified_name = next((f"mcp__{server}__{name}" for server, names in TOOL_SERVER_TOOLS.values() if name in names), "")
    if not qualified_name:
        raise ValueError("工具未登记授权名称")

    async def guarded(args):
        ctx = get_execution_context()
        runtime = ctx.extra.get("secure_runtime")
        current = asyncio.current_task()
        if runtime is not None:
            runtime.tool_tasks.add(current)
        try:
            await run_owned_sync(check_tool, ctx, qualified_name)
            from services.datamind.execution.resource_guard import validate_tool_resources
            args = await run_owned_sync(validate_tool_resources, ctx, qualified_name, args)
            from services.datamind.execution.sdk_tools import scoped_metadata
            use_projection = (name in scoped_metadata.NAMES
                              and not (name in PHYSICAL_METADATA_TOOLS and _waker_can_write_sql(runtime)))
            if qualified_name.startswith(("mcp__datahub_catalog__", "mcp__datahub_ontology__", "mcp__datahub_semantic__")) and use_projection:
                import json
                data = await run_owned_sync(scoped_metadata.execute, name, args, ctx)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}
            else:
                result = await handler(args)
            await run_owned_sync(check_tool, ctx, qualified_name)
            if result.get("isError") and qualified_name not in (
                    "mcp__datahub_semantic__run_semantic_query", "mcp__datahub_workspace__bash"):
                logger.error("工具返回执行错误: %s: %s", qualified_name, str(result)[:2000])
                return {"isError": True, "content": [{"type": "text", "text": "工具执行失败，请联系管理员查看服务端日志。"}]}
            return result
        except ResourceScopeError as exc:
            logger.warning("工具资源范围校验拒绝: %s: %s", qualified_name, exc)
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except DesignError as exc:
            import json
            logger.warning("设计工具拒绝: %s code=%s", qualified_name, exc.code)
            return {"isError": True, "content": [{"type": "text", "text": json.dumps(
                {"error": str(exc), "code": exc.code}, ensure_ascii=False)}]}
        except Exception:
            logger.exception("Waker 工具被拒绝或执行失败: %s", qualified_name)
            return {"isError": True, "content": [{"type": "text", "text": "工具执行未完成：权限、资源或执行环境不满足要求，请查看会话能力或联系管理员。"}]}
        finally:
            if runtime is not None:
                runtime.tool_tasks.discard(current)

    sdk = load_sdk(backend)
    return sdk.tool(name, description, input_schema)(guarded)


def make_server(backend: str, name: str, tools: list):
    """用指定 SDK 构建进程内 MCP server."""
    return load_sdk(backend).create_sdk_mcp_server(name=name, tools=tools)
