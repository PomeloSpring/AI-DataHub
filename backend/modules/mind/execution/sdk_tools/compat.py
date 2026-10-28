"""SDK 兼容层 — 统一加载 qodercn-agent-sdk 的工具构造器.

qoder-agent-sdk(Global)与 qodercn-agent-sdk(qoder.cn)的 @tool /
create_sdk_mcp_server 接口对称, 项目已统一切换 qodercn 体系(2026-10),
本模块屏蔽 import 细节, 使工具 handler 只写一份。
"""

import logging
import types
import typing

logger = logging.getLogger(__name__)

SDK_BACKENDS = ("qoder",)

# 可写 SQL 的 AS-BOT（授权了 execute_sql）需要真实表/列元数据来拼 SQL；
# 其 search_metadata / get_table_schema 走原始 handler（物理元数据，已剥除 datasource_id），
# 不走本体业务投影。语义层 AS-BOT（无 execute_sql）仍只回业务本体视图。
PHYSICAL_METADATA_TOOLS = {"search_metadata", "get_table_schema"}


def _as_bot_can_write_sql(runtime) -> bool:
    policy = getattr(runtime, "policy", None)
    allowed = getattr(policy, "allowed", None) or ()
    return any(str(t).endswith("__execute_sql") for t in allowed)


def load_sdk(backend: str):
    """加载 qodercn-agent-sdk 模块;未安装时抛 ImportError.

    执行层唯一后端为 qoder(claude-agent-sdk 已退役),其他 backend 显式拒绝。
    """
    if backend != "qoder":
        raise ValueError(f"不支持的 SDK 后端: {backend}（仅允许 qoder）")
    import qodercn_agent_sdk as sdk
    return sdk


def _tool_error_text(result) -> str:
    """从工具结果里取人可读的错因文本（handler 已加工成业务文案）。"""
    content = (result or {}).get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                return text
    return ""


def _is_optional(py_type) -> bool:
    """Optional[X] / X | None 判定 —— 决定该参数是否可不传。"""
    origin = typing.get_origin(py_type)
    if origin is typing.Annotated:
        args = typing.get_args(py_type)
        return bool(args) and _is_optional(args[0])
    # NotRequired[X] 也算可选
    if getattr(origin, "_name", None) == "NotRequired":
        return True
    if origin is typing.Union or isinstance(py_type, types.UnionType):
        return any(a is type(None) for a in typing.get_args(py_type))
    return False


def _prop_schema(py_type) -> dict:
    """单个参数的 JSON Schema（描述取 Annotated 元数据）。"""
    description = ""
    origin = typing.get_origin(py_type)
    while origin is typing.Annotated:
        args = typing.get_args(py_type)
        for meta in args[1:]:
            if isinstance(meta, str) and meta:
                description = meta
                break
        py_type, origin = args[0], typing.get_origin(args[0])
    # NotRequired[X] / Required[X] 解包
    if getattr(origin, "_name", None) in ("NotRequired", "Required", "ReadOnly"):
        inner = _prop_schema(typing.get_args(py_type)[0])
        return {**inner, **({"description": description} if description else {})}
    if origin is typing.Union or isinstance(py_type, types.UnionType):
        non_none = [a for a in typing.get_args(py_type) if a is not type(None)]
        node = _prop_schema(non_none[0]) if len(non_none) == 1 else {
            "anyOf": [_prop_schema(a) for a in non_none]}
        return {**node, **({"description": description} if description else {})}
    if origin in (list, typing.List):
        item = typing.get_args(py_type)
        node = {"type": "array", "items": _prop_schema(item[0])} if item else {"type": "array"}
        return {**node, **({"description": description} if description else {})}
    if origin in (dict, typing.Dict):
        node = {"type": "object"}
        return {**node, **({"description": description} if description else {})}
    node = {"type": {str: "string", int: "integer", float: "number", bool: "boolean",
                     list: "array", dict: "object"}.get(py_type, "string")}
    return {**node, **({"description": description} if description else {})}


def to_json_schema(spec: dict) -> dict:
    """把 {参数名: Annotated[类型, 描述]} 转成带**正确 required** 的 JSON Schema。

    背景（历史缺陷）：qodercn_agent_sdk 对 dict 形态 schema 直接
    `"required": list(properties.keys())`，把所有键一律当必填，完全忽略 Optional ——
    导致 save_dimension 的可选 `id` 被判为必填，**新建维度/指标永远失败**
    （报 "params must have required property 'id'"）。SDK 对已是 JSON Schema 的输入
    会原样透传，所以在这里先转好再交给它，不必改 vendored 依赖。
    """
    if not isinstance(spec, dict):
        return {"type": "object", "properties": {}}
    # 已是 JSON Schema：原样透传（调用方自己声明了 required）
    if "type" in spec and "properties" in spec and isinstance(spec.get("type"), str):
        return spec
    properties = {name: _prop_schema(t) for name, t in spec.items()}
    required = [name for name, t in spec.items() if not _is_optional(t)]
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def make_tool(backend: str, name: str, description: str, input_schema: dict,
              handler, annotations: dict = None, qualified_name: str = ""):
    """用指定 SDK 的 @tool 包装 handler.

    annotations(如 readOnlyHint)在个别 SDK 不支持时自动降级忽略。
    """
    import asyncio
    from backend.modules.viz.services.dashboard_design_service import DesignError
    from backend.modules.mind.execution.resource_guard import ResourceScopeError
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.mind.execution.outbound_guard import assert_no_sensitive
    from backend.modules.mind.execution.tool_policy import check_tool
    from backend.modules.mind.execution.session_workspace import run_owned_sync
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
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
            from backend.modules.mind.execution.resource_guard import validate_tool_resources
            args = await run_owned_sync(validate_tool_resources, ctx, qualified_name, args)
            from backend.modules.mind.execution.sdk_tools import scoped_metadata
            use_projection = (name in scoped_metadata.NAMES
                              and not (name in PHYSICAL_METADATA_TOOLS and _as_bot_can_write_sql(runtime)))
            if qualified_name.startswith(("mcp__datahub_catalog__", "mcp__datahub_ontology__", "mcp__datahub_semantic__")) and use_projection:
                import json
                data = await run_owned_sync(scoped_metadata.execute, name, args, ctx)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}
            else:
                import inspect
                if inspect.iscoroutinefunction(handler):
                    result = await handler(args)
                else:
                    # function_tools 等同步 handler：跑线程池，防阻塞事件循环（兼容双形态）
                    result = await run_owned_sync(handler, args)
            await run_owned_sync(check_tool, ctx, qualified_name)
            if result.get("isError") and qualified_name not in (
                    "mcp__datahub_semantic__run_semantic_query", "mcp__datahub_workspace__bash"):
                logger.error("工具返回执行错误: %s: %s", qualified_name, str(result)[:2000])
                # 透传工具自己回的错因，**不得**换成通用文案：handler 给的是可操作的业务提示
                # （如「数据源 X 无有效表元数据，请先执行元数据同步」），换成“联系管理员”
                # 就把可诊断信息丢了（no-silent-degradation §1）。敏感收口由下方 assert_no_sensitive 负责。
                detail = _tool_error_text(result) or "工具执行失败，未返回具体原因。"
                return {"isError": True, "content": [{"type": "text", "text": detail}]}
            # 出站收口：任何工具结果下发 LLM 前过敏感信息守卫。
            # 放在 isError 归一化之后，否则守卫给出的具体命中类别会被通用文案盖掉。
            # 命中即拒发（fail-loud），不静默清空字段（no-silent-degradation §1）。
            return assert_no_sensitive(result, qualified_name)
        except ResourceScopeError as exc:
            logger.warning("工具资源范围校验拒绝: %s: %s", qualified_name, exc)
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except DesignError as exc:
            import json
            logger.warning("设计工具拒绝: %s code=%s", qualified_name, exc.code)
            # 异常文案也会进 LLM，同样过出站守卫。
            return assert_no_sensitive({"isError": True, "content": [{"type": "text", "text": json.dumps(
                {"error": str(exc), "code": exc.code}, ensure_ascii=False)}]}, qualified_name)
        except PermissionError as exc:
            # 授权/域拒绝的 PermissionError 必须透传真因（当前 AS-BOT 未授权该工具 /
            # 执行层已停用 等），不得坠入下方兜底换成“联系管理员”——那会把可诊断信息丢光
            # （no-silent-degradation §1）。敏感收口仍由 assert_no_sensitive 负责。
            logger.warning("AS-BOT 工具权限/域拒绝: %s: %s", qualified_name, exc)
            return assert_no_sensitive(
                {"isError": True, "content": [{"type": "text", "text": str(exc)}]}, qualified_name)
        except Exception:
            logger.exception("AS-BOT 工具被拒绝或执行失败: %s", qualified_name)
            return {"isError": True, "content": [{"type": "text", "text": "工具执行未完成：权限、资源或执行环境不满足要求，请查看会话能力或联系管理员。"}]}
        finally:
            if runtime is not None:
                runtime.tool_tasks.discard(current)

    sdk = load_sdk(backend)
    # 必须先转成带正确 required 的 JSON Schema：SDK 对 dict 形态会把所有键当必填
    return sdk.tool(name, description, to_json_schema(input_schema))(guarded)


def make_server(backend: str, name: str, tools: list):
    """用指定 SDK 构建进程内 MCP server."""
    return load_sdk(backend).create_sdk_mcp_server(name=name, tools=tools)
