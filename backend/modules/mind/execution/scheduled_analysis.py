"""无人值守分析适配器：只编排已有语义目录与治理链，不开放裸 SQL/外部取数。

运行经 qodercli -q 一次性任务模式（qodercn_agent_sdk.query 单发），无会话状态。
"""
import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

logger = logging.getLogger(__name__)

from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from backend.modules.mind.execution.sdk_tools import semantic_tools
from backend.modules.mind.execution.stream_utils import ToolEventTracker
from backend.core.task_runtime import RunInterrupted


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
    from backend.modules.mind.execution.resource_guard import bind_resources, validate_knowledge_binding
    from backend.modules.mind.execution.sdk_tools.external_tools import load_server
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
    from backend.modules.mind.execution.tool_policy import resolve_policy
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
    from backend.common.auth import resolve_execution_owner
    from backend.modules.mind.execution.as_bots import resolve_as_bots
    from backend.modules.mind.execution.tool_policy import compile_policy
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
    from backend.mcp_client.client import MCPClient
    client = MCPClient(server["id"], server["name"],
        "streamable_http" if server["transport"] == "http" else server["transport"], url=server["url"])
    try:
        if not await client.connect():
            raise ConnectionError("意图服务不可用")
        raw = await client.call_tool("propose_semantic_intent", {"question": question})
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict) or set(value) != {"intent"} or not isinstance(value["intent"], dict):
            raise ValueError("MCP 只能返回 intent 对象")
        from backend.semantics.intent import parse_intent
        intent = value["intent"]
        query, error, _ = parse_intent(intent)
        if error or query.dry_run or any(k in intent for k in ("datasource_id", "workspace_id", "user_id")):
            raise ValueError("MCP 返回非法意图")
        return intent
    finally:
        await client.disconnect()


SCHEDULED_GUIDANCE = (
    "你是无人值守语义分析助手。先查看实时目录，再执行 run_semantic_query。"
    "只能使用给定的业务名；不猜口径，不生成 SQL。相对日期使用 time_window。"
    "不清楚时调用 needs_clarification。所有所需查询完成才结束。"
)


class _ScheduledRun:
    """qodercli -q 一次性任务模式的单次运行（sdk.query 单发，无会话状态）。

    工具循环在 qodercli 运行时内完成，本类负责：运行内工具 handler（治理取数、
    错误码映射、run 级结果收集器）、一次性消息流消费（stream_utils 转译与
    _obs_record_* 埋点口径）、终稿判读与错误码归一（_finalize）。

    原逐轮 recheck() 指纹校验由 authorize() 的实时授权校验替代——语义等价：
    每次工具调用重新 load_profile 解析共享权限（resource_guard 每调用重解析），
    配置/授权变化在工具边界立即生效，不留已撤权的执行窗口。
    """

    def __init__(self, question, config, identity, check, profile):
        self.question = question
        self.config = config
        self.check = check
        self.ctx = profile["context"]
        self.policy = profile["policy"]
        # 取数与守卫只用服务端任务创建者身份（含角色/用户名），不接受 LLM 传参。
        self.identity = {"user_id": self.ctx.user_id, "workspace_id": self.ctx.workspace_id,
                         "role": self.ctx.user_role, "username": self.ctx.username}
        self.prompt = question + "\n任务背景：" + str(config.get("context") or "")
        # 运行标识随 ctx.extra 注入，LLM 不可见；run 级收集器以本次运行为界。
        self.ctx.extra["task_binding"] = {"kind": "scheduled_analysis", "run_id": uuid4().hex}
        self.tools = [t for t in TOOLS if t["name"] in profile["tools"] or t["name"] == "needs_clarification"]
        self.remote = {}
        for index, server in enumerate(profile["servers"]):
            name = f"propose_semantic_intent_{index + 1}"
            self.remote[name] = server
            self.tools.append(_tool(name, "由已授权的业务规划服务提议一个语义意图，平台负责治理执行。", {}))
        self.allowlist = {t["name"] for t in self.tools}
        self.results = []
        self.fatal = None
        self.abort_exc = None
        self.final = None
        self.tracker = ToolEventTracker()
        self.max_turns = max(1, min(int(config.get("max_iterations") or 8), 20))
        self.timeout = min(int(config.get("timeout_seconds") or 300), 900)
        self.run_dir = None

    def _failure(self, code, retryable):
        return {"status": "partial" if self.results else "failed", "error_code": code,
                "retryable": retryable, "_analysis_results": list(self.results)}

    def _fail(self, response):
        """首个治理失败即终局（与旧链路的立即返回语义一致）。"""
        if self.fatal is None:
            self.fatal = response

    async def authorize(self, name):
        """每次工具调用实时重解析授权（resource_guard 每调用重解析）。"""
        if str(name).rsplit("__", 1)[-1] not in self.allowlist:
            raise ScheduledScopeError("当前 AS-BOT 未授权该定时工具", "TOOL_NOT_ALLOWED")
        await asyncio.to_thread(load_profile, self.config, self.identity)

    async def handle(self, name, args):
        """运行内工具统一入口：治理拒绝记 fatal，不向 SDK 运行时抛治理异常。"""
        from backend.modules.mind.execution.sdk_tools.catalog_tools import _text
        if self.fatal is not None or self.abort_exc is not None:
            return _text("任务已终止，工具不再执行", is_error=True)
        token = set_execution_context(self.ctx)
        try:
            if self.check:
                self.check()
            if not isinstance(args, dict):
                self._fail({"status": "failed", "error_code": "ASBOT_SCOPE_DENIED", "needs_attention": True})
                return _text("工具参数必须是对象", is_error=True)
            from backend.modules.mind.execution.resource_guard import reject_identity, _check_source
            try:
                reject_identity(args)
                _check_source(args, {self.ctx.datasource_id})
                await self.authorize(name)
            except (PermissionError, ValueError, HTTPException) as exc:
                self._fail({"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                            "needs_attention": True})
                return _text(str(exc) or "授权已失效", is_error=True)
            if name == "needs_clarification":
                self._fail({"status": "failed", "error_code": "NEEDS_CLARIFICATION", "needs_attention": True})
                return _text("口径或业务意图不明确，交由人工补充任务配置", is_error=True)
            if name == "get_metrics":
                raw = await semantic_tools.get_metrics(args)
                if raw.get("isError"):
                    self._fail({"status": "failed", "error_code": "CATALOG_UNAVAILABLE"})
                    return raw
                return _text(_business_catalog(json.loads(raw["content"][0]["text"])))
            if name == "knowledge_search":
                raw = await semantic_tools.knowledge_search(args)
                if raw.get("isError"):
                    logger.error("定时知识检索失败: %s", str(raw)[:1000])
                    self._fail({"status": "failed", "error_code": "KNOWLEDGE_SEARCH_FAILED", "needs_attention": True})
                    return raw
                return _text(json.loads(raw["content"][0]["text"]))
            if name == "run_semantic_query":
                return await self._execute_query(args.get("intent"))
            if name in self.remote:
                try:
                    intent = await asyncio.wait_for(propose_intent(self.remote[name], self.question), 30)
                except (ValueError, PermissionError):
                    self._fail({"status": "failed", "error_code": "MCP_CONTRACT_REJECTED", "needs_attention": True})
                    return _text("MCP 只能返回合法 intent 对象", is_error=True)
                except Exception:
                    self._fail(self._failure("MCP_UNAVAILABLE", retryable=not self.results))
                    return _text("意图服务不可用", is_error=True)
                return await self._execute_query(intent)
            self._fail({"status": "failed", "error_code": "TOOL_NOT_ALLOWED", "needs_attention": True})
            return _text("当前 AS-BOT 未授权该定时工具", is_error=True)
        except (PermissionError, ValueError, HTTPException) as exc:
            logger.warning("定时分析权限或配置已失效: %s", exc)
            self._fail({"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                        "needs_attention": True})
            return _text(str(exc), is_error=True)
        except Exception as exc:
            logger.exception("定时分析工具执行异常: %s", name)
            self.abort_exc = exc
            return _text("工具执行未完成，请查看服务端日志", is_error=True)
        finally:
            ExecutionContextVar.reset(token)

    async def _execute_query(self, intent):
        """受控取数：结果（含 _security_context）只进 run 级收集器，行数据不进 LLM。"""
        from backend.modules.viz.services import report_service
        from backend.modules.mind.execution.sdk_tools.catalog_tools import _text
        try:
            if self.check:
                self.check()
            result = await asyncio.to_thread(report_service.execute_semantic_source,
                                            intent, self.identity, self.ctx.datasource_id)
        except (PermissionError, ValueError):
            self._fail({"status": "partial" if self.results else "failed", "error_code": "QUERY_REJECTED",
                        "needs_attention": True, "_analysis_results": list(self.results)})
            return _text("语义查询被治理链拒绝", is_error=True)
        self.results.append(result)
        # 不把内部权限快照、物理列名或未验证正文送入 LLM。
        return _text({"status": "success", "message": "语义查询已完成并保存受控结果"})
    def oneshot(self, prompt, options):
        """qodercli -q 一次性单发通道（sdk.query：无会话、不落 resume）；测试缝。"""
        from backend.modules.mind.execution.sdk_tools.compat import load_sdk
        return load_sdk("qoder").query(prompt=prompt, options=options)

    async def _consume(self, options):
        """一次性消息流消费：复用 stream_utils 转译与 _obs_record_* 埋点口径，直至终稿。"""
        from contextlib import aclosing
        from backend.modules.mind.execution.adapters.qoder_sdk_adapter import (
            _obs_record_assistant, _obs_record_tool_result, _obs_record_result)
        async with aclosing(self.oneshot(self.prompt, options)) as stream:
            async for msg in stream:
                kind = type(msg).__name__
                if kind == "AssistantMessage":
                    _obs_record_assistant(msg)
                    for block in getattr(msg, "content", []) or []:
                        if type(block).__name__ == "ToolUseBlock":
                            self.tracker.on_tool_use(block)
                elif kind == "UserMessage":
                    for block in (msg.content if isinstance(msg.content, list) else []):
                        if type(block).__name__ == "ToolResultBlock":
                            event = self.tracker.on_tool_result(block)
                            if event:
                                _obs_record_tool_result(event, self.tracker.arguments_of(event["tool_call_id"]))
                elif kind == "ResultMessage":
                    self.final = msg
                    _obs_record_result(msg)
                if self.fatal is not None or self.abort_exc is not None:
                    break

    def _finalize(self) -> dict:
        if self.abort_exc is not None:
            raise self.abort_exc
        if self.fatal is not None:
            return self.fatal
        final = self.final
        if final is None:
            # 流在终稿前结束（SDK 子进程崩溃/被杀/传输中断），非模型结果。
            return self._failure("LLM_UNAVAILABLE", retryable=not self.results)
        if getattr(final, "is_error", False):
            subtype = str(getattr(final, "subtype", "") or "")
            terminal = str(getattr(final, "terminal_reason", "") or "")
            if "max_turns" in subtype or "max_turns" in terminal:
                return {"status": "failed", "error_code": "ITERATION_LIMIT", "needs_attention": True}
            return self._failure("LLM_UNAVAILABLE", retryable=not self.results)
        if not self.results:
            return {"status": "failed", "error_code": "NO_QUERY_RESULT", "needs_attention": True}
        return {"status": "success", "_analysis_results": list(self.results)}

    async def execute(self) -> dict:
        import shutil
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="adh-scheduled-"))
        self.run_dir = root
        (root / "workspace").mkdir()
        (root / "tmp").mkdir()
        try:
            try:
                options = _build_oneshot_options(self)
            except (PermissionError, ValueError, HTTPException) as exc:
                logger.warning("定时分析运行配置错误: %s", exc)
                return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                        "needs_attention": True}
            try:
                await asyncio.wait_for(self._consume(options), self.timeout)
            except RunInterrupted:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("定时分析一次性运行超时（max_turns/运行超时约束）")
                return {"status": "failed", "error_code": "ITERATION_LIMIT", "needs_attention": True}
            except (PermissionError, ValueError, HTTPException) as exc:
                logger.warning("定时分析运行权限或配置已失效: %s", exc)
                return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                        "needs_attention": True}
            except Exception:
                logger.exception("定时分析一次性运行未完成")
                return self._failure("LLM_UNAVAILABLE", retryable=not self.results)
            return self._finalize()
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _build_oneshot_options(run: "_ScheduledRun"):
    """复用 secure_sdk.build_options 的构造面（compose_system_prompt、in-process 工具
    服务器、permit/PreToolUse 守卫、env 沙箱）；one-shot 差异仅：不带 resume、不落会话。"""
    import os
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk, make_server
    from backend.modules.mind.execution.prompt_composer import compose_system_prompt
    from backend.modules.mind.execution.secure_sdk import resolve_access_token
    from backend.modules.mind.execution import as_bots
    from qodercn_agent_sdk.auth import AccessTokenAuthOptions

    sdk = load_sdk("qoder")

    def _wrap(name):
        async def handler(args):
            return await run.handle(name, args)
        return handler

    tools = [sdk.tool(t["name"], t["description"], t["input_schema"])(_wrap(t["name"])) for t in run.tools]
    server = make_server("qoder", "datahub_scheduled", tools)

    async def permit(name, args, context):
        try:
            await run.authorize(name)
            return sdk.PermissionResultAllow(updated_input=args)
        except Exception:
            logger.exception("定时分析权限校验拒绝: %s", name)
            return sdk.PermissionResultDeny(message="当前任务未授权该工具或权限已变化")

    async def before_tool(data, tool_use_id, context):
        name = data.get("tool_name", "")
        try:
            await run.authorize(name)
            return {}
        except Exception:
            logger.exception("定时分析执行前守卫拒绝: %s", name)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "当前任务未授权该工具或权限已变化"}}

    home = run.run_dir
    env = {k: None for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home), "TMPDIR": str(home / "tmp"),
                "QODERCN_CONFIG_DIR": str(home) + "/qoder-cn"})
    system_prompt = SCHEDULED_GUIDANCE + "\n\n" + compose_system_prompt(
        run.policy.as_bot, [run.policy.as_bot], run.ctx.username, run.ctx.user_role,
        knowledge_bases=as_bots.load_knowledge_bases(run.policy.as_bot.get("knowledge_base_ids") or []),
        skills=as_bots.load_skills(as_bots.collect_skill_names([run.policy.as_bot])),
        user_id=run.ctx.user_id, workspace_id=run.ctx.workspace_id, datasource_id=run.ctx.datasource_id,
        capabilities=run.policy.manifest(), report_theme_id="",
    )
    token = resolve_access_token()
    if not token:
        raise ValueError("执行层凭据未配置(QODERCN_PERSONAL_ACCESS_TOKEN)")
    return sdk.QoderAgentOptions(
        tools=[], allowed_tools=sorted(f"mcp__datahub_scheduled__{t['name']}" for t in run.tools),
        permission_mode="default", setting_sources=[], cwd=str(home / "workspace"), add_dirs=[], env=env,
        mcp_servers={"datahub_scheduled": server}, agents={}, system_prompt=system_prompt,
        resume=None, max_turns=run.max_turns, include_partial_messages=True,
        can_use_tool=permit, hooks={"PreToolUse": [sdk.HookMatcher(hooks=[before_tool])]},
        cli_path=None, model=None, auth=AccessTokenAuthOptions(access_token=token),
        strict_mcp_config=True, allowed_mcp_server_names=["datahub_scheduled"], skills=[])


async def analyze_question(question: str, config: dict, identity: dict, check=None) -> dict:
    """无人值守分析单题入口：qodercli -q 一次性任务模式（sdk.query 单发）。

    每个 question 一次独立运行（无 resume/会话状态，不走 ChatService 会话派发）；
    语义工具以 in-process MCP 注册进该次运行，run_semantic_query 在服务端经
    report_service.execute_semantic_source 受控执行（行数据不进 LLM）。返回结构
    {status, error_code, needs_attention, retryable, _analysis_results} 与错误码
    语义保持旧链路不变，executor._execute_agent_mode/_generate_report 零改动。
    """
    if not question.strip():
        return {"status": "failed", "error_code": "EMPTY_QUESTION"}
    try:
        profile = await asyncio.to_thread(load_profile, config, identity)
    except (PermissionError, ValueError, HTTPException) as exc:
        logger.warning("定时分析授权拒绝: %s", exc)
        return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"), "needs_attention": True}
    return await _ScheduledRun(question, config, identity, check, profile).execute()
