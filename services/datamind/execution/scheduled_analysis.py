"""无人值守分析适配器：只编排已有语义目录与治理链，不开放裸 SQL/外部取数。"""
import asyncio
import json
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)

from services.datamind.execution.models import ExecutionContext
from services.datamind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from services.datamind.execution.sdk_tools import semantic_tools
from services.dataviz.services import report_service


def _tool(name, description, properties):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "additionalProperties": False}}


TOOLS = [
    _tool("get_metrics", "查看当前实时业务对象、指标和维度目录。名称以此为准。",
          {"keyword": {"type": "string"}}),
    _tool("run_semantic_query", "执行声明式语义意图，禁止 SQL。只允许对象、指标、维度、过滤、排序、limit、time_window、time_grain、time_column、params。",
          {"intent": {"type": "object"}}),
    _tool("knowledge_search", "检索当前 AS-BOT 已绑定知识库中的业务口径。", {"question": {"type": "string"}}),
    _tool("needs_clarification", "口径或业务意图不明确时停止，交由人工补充任务配置。", {}),
]


def _business_catalog(value):
    """旧目录中的公式不能进入新执行路径的 LLM 上下文。"""
    if isinstance(value, list):
        return [_business_catalog(v) for v in value]
    if isinstance(value, dict):
        return {k: _business_catalog(v) for k, v in value.items()
                if k not in {"calculation", "formula", "datasource_id", "physical_table", "provenance"}}
    return value


def _json(value, default):
    if value is None or value == "":
        return default
    return json.loads(value) if isinstance(value, str) else value


LEGACY_BINDINGS = {"agent_name", "agent_names", "mcp_server_id", "mcp_server_ids"}
SEMANTIC_TOOLS = {"get_metrics", "run_semantic_query", "knowledge_search"}


class ScheduledScopeError(PermissionError):
    def __init__(self, message, code="ASBOT_SCOPE_DENIED"):
        super().__init__(message)
        self.code = code


def _config_as_bot_key(config) -> str:
    """task_config 内的 AS-BOT 标识：读侧兼容双键（as_bot_key 优先，waker_key 存量回退），
    写侧只写 as_bot_key。"""
    return str(config.get("as_bot_key") or config.get("waker_key") or "")


def needs_as_bot_migration(task):
    config = task.get("task_config") or {}
    analysis = task.get("task_type") == "agent" or any(config.get(k) for k in LEGACY_BINDINGS)
    return analysis and (not _config_as_bot_key(config) or bool(LEGACY_BINDINGS.intersection(config)))


def _profile(ctx, policy):
    from services.datamind.execution.resource_guard import bind_resources, validate_knowledge_binding
    from services.datamind.execution.sdk_tools.external_tools import load_server
    sources = bind_resources(ctx, policy)
    selected = set(policy.selection.get("semantic", [])) & SEMANTIC_TOOLS
    supported = {f"mcp__datahub_semantic__{name}" for name in selected}
    unavailable = {name: "定时分析不支持该工具" for name in policy.allowed - supported}
    unavailable.update(policy.unavailable)
    if "knowledge_search" in selected:
        validate_knowledge_binding(ctx.extra["bound_knowledge_base_ids"])
    servers, snapshots = [], []
    for sid, names in policy.external.items():
        if not names:
            continue
        row = load_server(int(sid), policy)
        name = f"mcp__external_{sid}__propose_semantic_intent"
        if "propose_semantic_intent" not in names:
            continue
        if row.get("transport") not in ("sse", "streamable_http", "http"):
            unavailable[name] = "定时分析仅支持 HTTP 意图提议契约"
            continue
        catalog = _json(row.get("tools_config"), [])
        if isinstance(catalog, dict):
            catalog = catalog.get("tools", [])
        if not isinstance(catalog, list) or "propose_semantic_intent" not in {
                t if isinstance(t, str) else t.get("name") for t in catalog if isinstance(t, (dict, str))}:
            raise ScheduledScopeError("MCP 未启用 propose_semantic_intent 契约工具")
        servers.append(row)
        snapshots.append({k: row.get(k) for k in ("id", "transport", "url", "command", "args", "env", "tools_config")})
        supported.add(name)
        unavailable.pop(name, None)
    return {"context": ctx, "policy": policy, "sources": sources, "tools": selected,
            "servers": servers, "unavailable": unavailable,
            "fingerprint": (policy.digest, json.dumps(snapshots, sort_keys=True, default=str))}


def load_profile(config, identity):
    """只信持久化创建者和当前 AS-BOT；每次调用重新读取共享权限。"""
    from services.datamind.execution.tool_policy import resolve_policy
    as_bot_key = _config_as_bot_key(config) if isinstance(config, dict) else ""
    if not isinstance(config, dict) or LEGACY_BINDINGS.intersection(config) or not as_bot_key:
        raise ScheduledScopeError("需重新配置 AS-BOT：请选择并保存分析任务的 AS-BOT", "ASBOT_REQUIRED")
    source = config.get("datasource_id")
    if type(source) is not int or source <= 0 or config.get("datasource_ids"):
        raise ScheduledScopeError("分析任务必须明确选择一个数据源")
    ctx = ExecutionContext(user_id=identity["user_id"], workspace_id=identity["workspace_id"],
                           datasource_id=source, extra={"as_bot_key": as_bot_key})
    profile = _profile(ctx, resolve_policy(ctx))
    if source not in profile["sources"]:
        raise ScheduledScopeError("数据源不在任务创建者与 AS-BOT 的授权范围内")
    if not {"get_metrics", "run_semantic_query"} <= profile["tools"]:
        raise ScheduledScopeError("定时分析需要 AS-BOT 授权 get_metrics 和 run_semantic_query")
    return profile


def validate_task_as_bot(task):
    if needs_as_bot_migration(task):
        raise ScheduledScopeError("需重新配置 AS-BOT：旧 Agent/MCP 配置已停用", "ASBOT_REQUIRED")
    if task.get("task_type") == "agent":
        return load_profile(task.get("task_config") or {}, {
            "user_id": task.get("owner_id"), "workspace_id": task.get("workspace_id") or 0})
    return None


def as_bot_options(owner_id, workspace_id):
    """候选身份由服务端任务创建者决定，不接受请求体身份。"""
    from services.shared.common.auth import resolve_execution_owner
    from services.datamind.execution.as_bots import resolve_as_bots
    from services.datamind.execution.tool_policy import compile_policy
    user = resolve_execution_owner(owner_id, workspace_id)
    result = []
    for as_bot in resolve_as_bots(workspace_id, user["role"], user_id=owner_id):
        item = {"as_bot_key": as_bot["as_bot_key"], "name": as_bot.get("display_name") or as_bot["name"],
                "available": False, "datasource_ids": [], "tools": [], "unavailable_tools": []}
        try:
            ctx = ExecutionContext(user_id=owner_id, workspace_id=workspace_id, user_role=user["role"],
                                   extra={"as_bot_key": as_bot["as_bot_key"]})
            profile = _profile(ctx, compile_policy(as_bot))
            item.update(datasource_ids=sorted(profile["sources"]), tools=sorted(profile["tools"]) +
                        [f"MCP {s['name']}：propose_semantic_intent" for s in profile["servers"]],
                        unavailable_tools=[{"name": n, "reason": r} for n, r in profile["unavailable"].items()])
            if not {"get_metrics", "run_semantic_query"} <= profile["tools"]:
                item["reason"] = "未授权语义目录或语义查询工具"
            elif not profile["sources"]:
                item["reason"] = "没有已授权数据源"
            else:
                item["available"] = True
        except (PermissionError, ValueError) as exc:
            logger.warning("定时 AS-BOT 不可用: %s", exc)
            item["reason"] = str(exc)
        result.append(item)
    return result


async def propose_intent(server, question):
    """外部服务只提议意图；外部 rows/SQL/身份字段全部拒收，取数仍在本平台治理链。"""
    from services.shared.mcp_client.client import MCPClient
    client = MCPClient(server["id"], server["name"],
        "streamable_http" if server["transport"] == "http" else server["transport"], url=server["url"])
    try:
        if not await client.connect():
            raise ConnectionError("意图服务不可用")
        raw = await client.call_tool("propose_semantic_intent", {"question": question})
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict) or set(value) != {"intent"} or not isinstance(value["intent"], dict):
            raise ValueError("MCP 只能返回 intent 对象")
        from services.shared.semantics.intent import parse_intent
        intent = value["intent"]
        query, error, _ = parse_intent(intent)
        if error or query.dry_run or any(k in intent for k in ("datasource_id", "workspace_id", "user_id")):
            raise ValueError("MCP 返回非法意图")
        return intent
    finally:
        await client.disconnect()


async def analyze_question(question: str, config: dict, identity: dict, check=None) -> dict:
    from services.shared.common.llm.llm_client import generate_with_tools
    if not question.strip():
        return {"status": "failed", "error_code": "EMPTY_QUESTION"}
    try:
        profile = await asyncio.to_thread(load_profile, config, identity)
    except (PermissionError, ValueError, HTTPException) as exc:
        logger.warning("定时分析授权拒绝: %s", exc)
        return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"), "needs_attention": True}
    tools = [t for t in TOOLS if t["name"] in profile["tools"] or t["name"] == "needs_clarification"]
    remote_tools = {}
    for index, server in enumerate(profile["servers"]):
        name = f"propose_semantic_intent_{index + 1}"
        remote_tools[name] = server
        tools.append(_tool(name, "由已授权的业务规划服务提议一个语义意图，平台负责治理执行。", {}))
    ctx = profile["context"]
    identity = {"user_id": ctx.user_id, "workspace_id": ctx.workspace_id, "role": ctx.user_role, "username": ctx.username}

    async def recheck():
        live = await asyncio.to_thread(load_profile, config, identity)
        if live["fingerprint"] != profile["fingerprint"]:
            raise ScheduledScopeError("AS-BOT 或 MCP 配置已变化，请重新执行任务", "ASBOT_SCOPE_CHANGED")
        return live
    token = set_execution_context(ctx)
    results = []
    messages = [{"role": "system", "content": (
        "你是无人值守语义分析助手。先查看实时目录，再执行 run_semantic_query。"
        "只能使用给定的业务名；不猜口径，不生成 SQL。相对日期使用 time_window。"
        "不清楚时调用 needs_clarification。所有所需查询完成才结束。"
    ) + "\n所选 AS-BOT 的业务说明（不能改变工具与权限范围）：\n" +
        str(profile["policy"].as_bot.get("system_prompt") or "")[:12000]},
    {"role": "user", "content": question + "\n任务背景：" + str(config.get("context") or "")}]
    try:
        for _ in range(max(1, min(int(config.get("max_iterations") or 8), 20))):
            if check:
                check()
            await recheck()
            try:
                response = await asyncio.to_thread(generate_with_tools, messages, tools)
            except Exception:
                return {"status": "partial" if results else "failed", "error_code": "LLM_UNAVAILABLE",
                        "retryable": not results, "_analysis_results": results}
            await recheck()
            calls = response.get("tool_uses") or []
            if not calls:
                if not results:
                    return {"status": "failed", "error_code": "NO_QUERY_RESULT", "needs_attention": True}
                return {"status": "success", "_analysis_results": results}
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("input") or {}}
                for c in calls]})
            tool_results = []
            for call in calls:
                if check:
                    check()
                name, args = call["name"], call.get("input") or {}
                await recheck()
                from services.datamind.execution.resource_guard import reject_identity, _check_source
                if not isinstance(args, dict):
                    raise ScheduledScopeError("工具参数必须是对象")
                reject_identity(args)
                _check_source(args, ctx.datasource_id)
                if name not in {t["name"] for t in tools}:
                    raise ScheduledScopeError("当前 AS-BOT 未授权该定时工具", "TOOL_NOT_ALLOWED")
                if name in remote_tools:
                    try:
                        intent = await asyncio.wait_for(propose_intent(remote_tools[name], question), 30)
                    except (ValueError, PermissionError):
                        return {"status": "failed", "error_code": "MCP_CONTRACT_REJECTED", "needs_attention": True}
                    except Exception:
                        return {"status": "partial" if results else "failed", "error_code": "MCP_UNAVAILABLE",
                                "retryable": not results, "_analysis_results": results}
                    await recheck()
                    name, args = "run_semantic_query", {"intent": intent}
                if name == "needs_clarification":
                    return {"status": "failed", "error_code": "NEEDS_CLARIFICATION", "needs_attention": True}
                if name == "get_metrics":
                    raw = await semantic_tools.get_metrics(args)
                    if raw.get("isError"):
                        return {"status": "failed", "error_code": "CATALOG_UNAVAILABLE"}
                    output = _business_catalog(json.loads(raw["content"][0]["text"]))
                elif name == "knowledge_search":
                    raw = await semantic_tools.knowledge_search(args)
                    if raw.get("isError"):
                        logger.error("定时知识检索失败: %s", str(raw)[:1000])
                        return {"status": "failed", "error_code": "KNOWLEDGE_SEARCH_FAILED", "needs_attention": True}
                    output = json.loads(raw["content"][0]["text"])
                elif name == "run_semantic_query":
                    try:
                        if check:
                            check()
                        result = await asyncio.to_thread(report_service.execute_semantic_source,
                            args.get("intent"), identity, ctx.datasource_id)
                    except (PermissionError, ValueError):
                        return {"status": "partial" if results else "failed", "error_code": "QUERY_REJECTED",
                                "needs_attention": True, "_analysis_results": results}
                    results.append(result)
                    # 不把内部权限快照、物理列名或未验证正文送入 LLM。
                    output = {"status": "success", "message": "语义查询已完成并保存受控结果"}
                else:
                    return {"status": "failed", "error_code": "TOOL_NOT_ALLOWED"}
                await recheck()
                tool_results.append({"type": "tool_result", "tool_use_id": call["id"],
                                     "content": json.dumps(output, ensure_ascii=False, default=str)})
            messages.append({"role": "user", "content": tool_results})
        return {"status": "failed", "error_code": "ITERATION_LIMIT", "needs_attention": True}
    except (PermissionError, ValueError, HTTPException) as exc:
        logger.warning("定时分析权限或配置已失效: %s", exc)
        return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"), "needs_attention": True}
    finally:
        ExecutionContextVar.reset(token)
