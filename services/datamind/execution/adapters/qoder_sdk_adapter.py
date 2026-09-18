"""QoderSDKAdapter — 基于 qoder-agent-sdk 的 Qoder 执行层.

与 CLIProcessAdapter(子进程 + 命令模板)不同,本适配器通过官方 SDK
以 stream-json 结构化协议调用 qodercli,并支持会话级资源注入:

- adh_mcp_servers → options.mcp_servers(stdio/sse/http 直接映射)
- adh_agents      → options.agents(AgentDefinition: prompt/tools/mcpServers)
- adh_skills      → 预留(后续物化为 skill 文件经 skills/plugins 注入)

注入均为会话级,不落盘、不污染全局配置;permission_mode=bypassPermissions
避免 headless 环境卡在权限确认。

多轮对话为长对话模式: 每个 chat 会话(conversation_id)维护一个持久 QoderSDKClient
(即一个 qodercli 进程),同会话后续轮次只发消息不再起进程/恢复会话/重连 MCP;
不可用时自动回落单发 query() + resume 模式。
"""

import asyncio
import json
import logging
import os
import time
from typing import AsyncIterator

from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask

logger = logging.getLogger(__name__)


# ── 可观测埋点辅助(未开启/无 recorder 时安全 no-op) ─────────────────

_OBS_ASSISTANT_TEXT_MAX = 4000   # 每轮模型输出产物的落库上限(可观测截断另有全局限制)


def _obs_record_assistant(msg) -> None:
    """从 AssistantMessage.usage 记录一次模型请求级 credit/token与模型输出产物。"""
    try:
        from services.shared import observability
        # 模型每轮可见输出(文本/工具调用指令)作为 assistant 产物 span 先行入库,
        # 不依赖 usage 字段(部分版本无 usage 时仍需保留执行过程)
        _obs_record_assistant_output(msg)
        usage = getattr(msg, "usage", None) or {}
        if not isinstance(usage, dict) or not usage:
            return
        observability.record_llm_call(
            name="qoder_llm_request",
            model_ref=getattr(msg, "model", "") or "",
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            credits=usage.get("credits"),
            original_credits=usage.get("original_credits"),
            billable=usage.get("billable"),
        )
    except Exception as e:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("obs record assistant ignored: %s", e)


def _obs_record_assistant_output(msg) -> None:
    """记录一轮 AssistantMessage 的产物:模型文本、思考、拟调用的工具及入参(如生成的 SQL)。"""
    try:
        from services.shared import observability
        texts, thinkings, tools = [], [], []
        for block in (getattr(msg, "content", None) or []):
            btype = type(block).__name__
            if btype == "TextBlock" and getattr(block, "text", ""):
                texts.append(block.text)
            elif btype == "ThinkingBlock" and getattr(block, "thinking", ""):
                thinkings.append(block.thinking)
            elif btype == "ToolUseBlock":
                tools.append({
                    "tool": getattr(block, "name", "") or "unknown",
                    "arguments": getattr(block, "input", None) or {},
                })
        if not (texts or thinkings or tools):
            return
        payload = {
            "text": "\n".join(texts),
            "thinking": "\n".join(thinkings),
            "tool_calls": tools,
        }
        observability.record_span(
            kind="assistant",
            name=f"round_{getattr(msg, 'num_turns', None) or 'output'}",
            model_ref=getattr(msg, "model", "") or "",
            input_text="",
            output_text=json.dumps(payload, ensure_ascii=False, default=str)[:_OBS_ASSISTANT_TEXT_MAX],
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("obs record assistant output ignored: %s", e)


def _obs_record_tool_result(ev, arguments: dict) -> None:
    """记录一次工具调用的完整产物:入参(含生成的 SQL)+ 执行结果/错误 + 耗时。"""
    try:
        from services.shared import observability
        err = ev.get("error") or ""
        observability.record_span(
            kind="tool_call",
            name=ev.get("tool") or "unknown",
            status="error" if err else "success",
            duration_ms=int((ev.get("elapsed") or 0) * 1000),
            input_text=json.dumps(arguments, ensure_ascii=False, default=str) if arguments else "",
            output_text="" if err else (ev.get("output") or ""),
            error_text=err,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("obs record tool result ignored: %s", e)


def _obs_record_result(msg) -> None:
    """从 ResultMessage 记录会话累计 credit(total_credits / model_usage)与回合收尾产物。"""
    try:
        from services.shared import observability
        observability.set_session_credits(
            getattr(msg, "total_credits", None),
            getattr(msg, "model_usage", None),
        )
        observability.record_span(
            kind="result",
            name="execution_result",
            status="success" if getattr(msg, "subtype", "") == "success" else "error",
            duration_ms=int(getattr(msg, "duration_ms", 0) or 0),
            output_text=json.dumps({
                "subtype": getattr(msg, "subtype", ""),
                "num_turns": getattr(msg, "num_turns", None),
                "session_id": getattr(msg, "session_id", ""),
                "total_credits": getattr(msg, "total_credits", None),
                "model_usage": getattr(msg, "model_usage", None),
            }, ensure_ascii=False, default=str),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("obs record result ignored: %s", e)


def _parse_json(value, default):
    """DB JSON 字段兼容解析(可能已是 dict/list,也可能是字符串)."""
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


# ── 长对话池(QoderSDKClient 持久连接, 进程内共享) ───────────────────
#
# key = chat 会话 ID(conversation_id), 同会话复用同一 qodercli 进程;
# 指纹(options+执行上下文稳定投影)变化即 resume 重建, 空闲/LRU 淘汰,
# 流中断/异常即退役(interrupt→排空→disconnect), 当轮回落单发。
_POOL: dict = {}
_POOL_LOCK = None  # 懒创建, 绑定 datamind 唯一事件循环


def _pool_lock() -> asyncio.Lock:
    global _POOL_LOCK
    if _POOL_LOCK is None:
        _POOL_LOCK = asyncio.Lock()
    return _POOL_LOCK


class _PooledChatSession:
    """一条 chat 会话 ↔ 一个持久 QoderSDKClient(qodercli 进程)."""

    __slots__ = ("client", "fingerprint", "last_used", "busy", "turn_active")

    def __init__(self, fingerprint: str):
        self.client = None
        self.fingerprint = fingerprint
        self.last_used = time.monotonic()
        self.busy = False       # 是否有流式轮次进行中/占位(并发轮次回落单发)
        self.turn_active = False  # query 已发出且未收到 Result(退役时需排空残留)


class QoderSDKAdapter(CLIProcessAdapter):
    """qoder-agent-sdk 执行层适配器(cli 类型,mode=sdk)."""

    @property
    def layer_type(self) -> str:
        return "cli"

    # ── 工作空间资源加载 ──────────────────────────────────────────

    def _load_workspace_resources(self, workspace_id: int) -> dict:
        """按 workspace_id 加载 MCP servers / agents(含全局 workspace_id=0)."""
        from services.shared.common.db import execute_query

        res: dict = {"mcp_servers": {}, "agents": {}, "skills": []}
        if not workspace_id:
            return res
        try:
            rows = execute_query(
                "SELECT name, transport, url, command, args, env FROM adh_mcp_servers "
                "WHERE is_active=1 AND workspace_id IN (%s, 0)",
                (workspace_id,),
            )
            for r in rows:
                cfg = self._map_mcp_server(r)
                if cfg:
                    res["mcp_servers"][r["name"]] = cfg

            rows = execute_query(
                "SELECT name, display_name, description, system_prompt, tools, mcp_server_ids "
                "FROM adh_agents WHERE is_active=1 AND workspace_id IN (%s, 0)",
                (workspace_id,),
            )
            for r in rows:
                res["agents"][r["name"]] = self._map_agent(r, res["mcp_servers"])
        except Exception as e:
            logger.warning("[ExecLayer:%s] Load workspace resources failed: %s", self._name, e)
        return res

    @staticmethod
    def _map_mcp_server(row: dict) -> dict | None:
        """adh_mcp_servers 行 → SDK McpServerConfig."""
        transport = (row.get("transport") or "").lower()
        if transport == "stdio":
            if not row.get("command"):
                return None
            env = _parse_json(row.get("env"), {})
            return {
                "type": "stdio",
                "command": row["command"],
                "args": _parse_json(row.get("args"), []),
                **({"env": {k: str(v) for k, v in env.items()}} if env else {}),
            }
        if transport in ("sse", "http") and row.get("url"):
            return {"type": transport, "url": row["url"]}
        return None

    @staticmethod
    def _map_agent(row: dict, injected_mcps: dict) -> dict:
        """adh_agents 行 → SDK AgentDefinition(mcp_server_ids 转为已注入的 server 名)."""
        ids = _parse_json(row.get("mcp_server_ids"), [])
        return {
            "description": row.get("description") or row.get("display_name") or row["name"],
            "prompt": row.get("system_prompt") or "",
            "mcpServers": list(injected_mcps.keys()) if ids else None,
        }

    def _load_mcp_by_ids(self, mcp_ids: list[int]) -> tuple[dict, dict]:
        """按共享 MCP 服务 ID 加载 → (name→config, id→name).

        Waker 引用 MCP 作为共享资源,只注入被引用的服务。
        """
        from services.shared.common.db import execute_query

        by_name: dict = {}
        id_to_name: dict = {}
        if not mcp_ids:
            return by_name, id_to_name
        try:
            placeholders = ", ".join(["%s"] * len(mcp_ids))
            rows = execute_query(
                f"SELECT id, name, transport, url, command, args, env FROM adh_mcp_servers "
                f"WHERE is_active=1 AND id IN ({placeholders})",
                tuple(mcp_ids),
            )
            for r in rows:
                cfg = self._map_mcp_server(r)
                if cfg:
                    by_name[r["name"]] = cfg
                    id_to_name[r["id"]] = r["name"]
        except Exception as e:  # noqa: BLE001
            logger.warning("[ExecLayer:%s] Load MCP by ids failed: %s", self._name, e)
        return by_name, id_to_name

    # ── Options 构建 ─────────────────────────────────────────────

    def _build_options(self, task: ExecutionTask):
        from qoder_agent_sdk import AgentDefinition, QoderAgentOptions
        from qoder_agent_sdk.auth import AccessTokenAuthOptions, AccessTokenEnvVar

        ctx = task.context
        workspace_id = ctx.workspace_id if ctx else 0
        user_role = (ctx.user_role if ctx else "") or ""
        username = (ctx.username if ctx else "") or ""

        res = self._load_workspace_resources(workspace_id)
        env = {k: str(v) for k, v in (self.config.get("env") or {}).items()}
        # SDK 要求显式 auth:优先用 config.env 中的 PAT,其次进程环境变量
        token = env.get("QODER_PERSONAL_ACCESS_TOKEN") or os.environ.get("QODER_PERSONAL_ACCESS_TOKEN")
        if token:
            auth = AccessTokenAuthOptions(access_token=token)
        else:
            auth = AccessTokenAuthOptions(access_token=AccessTokenEnvVar(env_var="QODER_PERSONAL_ACCESS_TOKEN"))
        # 运行时 chat 选择的模型优先于执行层配置模型
        runtime_ref = ((ctx.extra or {}) if ctx else {}).get("model_ref") or ""
        model = runtime_ref or self.model or None
        # 目录沙箱:cwd=allowed_dirs[0](或工作空间目录),add_dirs=其余白名单目录
        cwd, add_dirs = self._resolve_cwd(workspace_id)
        options = QoderAgentOptions(
            cli_path=self.cli_path,
            model=model,
            env=env,
            auth=auth,
            permission_mode="bypassPermissions",  # headless 免权限确认
            setting_sources=[],  # 不加载机器上的用户/项目配置,保持进程独享
            cwd=cwd or None,
            add_dirs=add_dirs,
            max_turns=int(self.config.get("max_turns", 0)) or None,
            include_partial_messages=True,  # 启用流式增量(stream_event)
        )
        # 多轮对话:恢复前端回传的 SDK 会话(首轮无 session_id 时新建)
        session_id = ((ctx.extra or {}) if ctx else {}).get("session_id") or ""
        if session_id:
            options.resume = session_id

        # ── Waker 解析(优先)→ MCP/工具/子代理/系统提示词按 Waker 为单位注入 ──
        from services.datamind.execution import prompt_composer, wakers as waker_service

        # 聊天端选定的 Waker(空=按 工作空间+角色 解析出的全部生效 Waker 合并)
        selected_waker_key = ((ctx.extra or {}) if ctx else {}).get("waker_key") or ""
        # AS-BOT 系统助手: 使用专用系统 Waker, 绕过工作空间/角色解析
        if selected_waker_key == waker_service.SYSTEM_BOT_WAKER_KEY:
            _sys_bot = waker_service.resolve_system_bot_waker()
            waker_list = [_sys_bot] if _sys_bot else []
        else:
            waker_list = waker_service.resolve_wakers(workspace_id, user_role, selected_waker_key)
        enabled_tool_groups = None
        mcp_tool_selection: dict = {}
        allowed_standard_tools = None
        waker_id_to_name: dict = {}
        bound_knowledge_bases: list = []
        bound_skills: list = []
        if waker_list:
            mcp_ids = waker_service.collect_mcp_server_ids(waker_list)
            waker_mcps, waker_id_to_name = self._load_mcp_by_ids(mcp_ids)
            if waker_mcps:
                # Waker 引用的共享 MCP 覆盖"全工作空间 MCP 一股脑注入"
                res["mcp_servers"] = waker_mcps
            enabled_tool_groups = waker_service.collect_tool_groups(waker_list) or None
            # 细粒度:若任一 waker 配了 tools.mcp 逐工具勾选,则以其为准(优先于粗粒度 groups)
            mcp_tool_selection = waker_service.collect_mcp_tool_selection(waker_list)
            allowed_standard_tools = waker_service.collect_standard_tools(waker_list)
            # 生效 Waker 绑定的知识库(合并去重),注入系统提示词引导检索
            bound_knowledge_bases = waker_service.load_knowledge_bases(
                waker_service.collect_knowledge_base_ids(waker_list)
            )
            # 生效 Waker 勾选绑定的技能(按名加载 SKILL.md 提示词)
            bound_skills = waker_service.load_skills(
                waker_service.collect_skill_names(waker_list)
            )

        # 进程内自定义工具(SDK @tool):waker 逐工具勾选优先,否则工具组,再否则执行层 config.sdk_tools
        tool_groups = self._build_tool_servers(enabled_tool_groups, selection=mcp_tool_selection)
        if res["mcp_servers"]:
            options.mcp_servers = dict(res["mcp_servers"])
            options.allowed_mcp_server_names = list(res["mcp_servers"].keys())
        else:
            options.mcp_servers = {}
            options.allowed_mcp_server_names = []
        # 与外部 MCP server 合并注入
        for srv_name, srv_cfg in tool_groups["servers"].items():
            options.mcp_servers[srv_name] = srv_cfg
            options.allowed_mcp_server_names.append(srv_name)
        options.allowed_tools = list(options.allowed_tools or [])
        options.allowed_tools.extend(tool_groups["allowed_tools"])
        # tools 权限白名单:Waker 生效时用其标准工具集,否则用工作空间绑定 config.allowed_tools;
        # 未允许的标准工具进 deny-list,MCP/自定义工具不受影响
        allowed = allowed_standard_tools if waker_list else self.config.get("allowed_tools")
        if allowed:
            from services.datamind.execution.tool_catalog import disallowed_tools
            denied = disallowed_tools(allowed, "qoder")
            if denied:
                options.disallowed_tools.extend(denied)
        # 子代理:Waker 生效时每个 Waker → 一个 AgentDefinition,否则回退 adh_agents
        if waker_list:
            options.agents = {
                w["name"]: AgentDefinition(
                    description=w.get("display_name") or w.get("description") or w["name"],
                    prompt=self._waker_agent_prompt(w),
                    mcpServers=[
                        waker_id_to_name[mid]
                        for mid in (w.get("mcp_server_ids") or [])
                        if mid in waker_id_to_name
                    ] or None,
                )
                for w in waker_list
            }
        elif res["agents"]:
            options.agents = {
                name: AgentDefinition(
                    description=a.get("description") or name,
                    prompt=a.get("prompt") or "",
                    mcpServers=a.get("mcpServers"),
                )
                for name, a in res["agents"].items()
            }
        # 系统提示词:Waker 生效时组合(persona + skills + 图表契约 + 角色权限),
        # 否则回退调用方传入的 system_prompt
        if waker_list:
            options.system_prompt = prompt_composer.compose_system_prompt(
                waker_service.default_waker(waker_list), waker_list, username, user_role,
                knowledge_bases=bound_knowledge_bases, skills=bound_skills,
                user_id=(ctx.user_id if ctx else 0) or 0,
                workspace_id=workspace_id or 0,
                datasource_id=(ctx.datasource_id if ctx else 0) or 0,
            )
        elif ctx and ctx.system_prompt:
            options.system_prompt = ctx.system_prompt
        logger.info(
            "[ExecLayer:%s] SDK options: workspace=%s role=%s wakers=%s cwd=%s add_dirs=%s "
            "mcp=%s agents=%s sdk_tools=%s model=%s disallowed=%s",
            self._name, workspace_id, user_role or "-",
            [w["name"] for w in waker_list] or "(legacy)", cwd or "-", list(add_dirs),
            list(res["mcp_servers"].keys()), list(getattr(options, "agents", {}) or {}),
            list(tool_groups["servers"].keys()), model or "-",
            list(options.disallowed_tools or []),
        )
        if bound_knowledge_bases:
            logger.info(
                "[ExecLayer:%s] bound knowledge bases: %s",
                self._name, [kb["name"] for kb in bound_knowledge_bases],
            )
        return options

    @staticmethod
    def _waker_agent_prompt(waker: dict) -> str:
        """单个 Waker 作为子代理时的 prompt:角色设定 + 勾选绑定技能的完整指引."""
        parts = [waker.get("system_prompt") or ""]
        persona = waker.get("persona") or {}
        for key, label in (("responsibility", "职责"), ("style", "风格"), ("boundary", "边界")):
            if persona.get(key):
                parts.append(f"{label}: {persona[key]}")
        try:
            from services.datamind.execution import wakers as waker_service

            names = waker_service.collect_skill_names([waker])
            for sk in waker_service.load_skills(names):
                prompt = (sk.get("system_prompt") or "").strip()
                if prompt:
                    parts.append(f"# 技能:{sk.get('display_name') or sk.get('name')}\n{prompt}")
        except Exception:  # noqa: BLE001  技能加载失败不影响子代理构建
            pass
        return "\n".join(p for p in parts if p).strip()

    def _build_tool_servers(self, enabled_groups=None, selection=None) -> dict:
        """构建进程内自定义工具 server.

        selection 为 waker 汇总的细粒度 {group: [tool,...]} 逐工具勾选(优先);
        为空时回退 enabled_groups(Waker 工具组名列表);
        仍为空时回退执行层 config.sdk_tools(["catalog","query"] 或 "all"/None 全部启用)。
        构建逻辑与 ClaudeSDKAdapter 共享(sdk_tools 包)。
        """
        from services.datamind.execution.sdk_tools import build_tool_servers

        groups = enabled_groups if enabled_groups else self.config.get("sdk_tools")
        return build_tool_servers("qoder", groups, selection=selection)

    # ── 执行 ─────────────────────────────────────────────────────

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        """流式执行入口: 优先长对话(持久会话池), 否则单发 query()+resume."""
        try:
            from qoder_agent_sdk import query  # noqa: F401  (单发回落路径亦使用)
        except ImportError as e:
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"qoder-agent-sdk 未安装: {e}"),
            }
            return

        try:
            options = self._build_options(task)
        except Exception as e:  # noqa: BLE001
            logger.error("[ExecLayer:%s] Build SDK options failed: %s", self._name, e)
            yield {"type": "done", "result": ExecutionResult(success=False, error=str(e))}
            return

        ctx = task.context
        conv_id = (ctx.extra or {}).get("conversation_id") if ctx else None
        if conv_id and self.config.get("session_pool", True):
            try:
                fingerprint = self._options_fingerprint(options, ctx)
            except Exception as e:  # noqa: BLE001  指纹失败只影响复用, 回落单发不影响功能
                logger.warning("[ExecLayer:%s] Options fingerprint failed: %s", self._name, e)
                fingerprint = ""
            if fingerprint:
                async for ev in self._execute_stream_pooled(task, options, str(conv_id), fingerprint):
                    yield ev
                return
        async for ev in self._execute_stream_oneshot(task, options):
            yield ev

    # ── 事件映射(单发/长对话共用) ──────────────────────────────

    async def _map_messages(
        self, messages, texts: list, meta: dict, tracker, state: dict
    ) -> AsyncIterator[dict]:
        """SDK 消息流 → token/thinking/tool 事件的统一映射.

        state: {"final": ResultMessage|None, "streamed": bool} —
        final 表示本轮是否正常收到结果(决定长对话 entry 去留)。
        """
        async for msg in messages:
            kind = type(msg).__name__
            if kind == "StreamEvent":
                # partial messages:细粒度增量片段(打字机效果)
                delta = (msg.event or {}).get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta" and delta.get("text"):
                    state["streamed"] = True
                    yield {"type": "token", "text": delta["text"]}
                elif dtype == "thinking_delta" and delta.get("thinking"):
                    yield {"type": "thinking", "text": delta["thinking"]}
            elif kind == "AssistantMessage":
                for block in msg.content:
                    btype = type(block).__name__
                    if btype == "TextBlock" and block.text:
                        texts.append(block.text)
                        # 已流式推送过则不重复发 token;未流式时回退整段输出
                        if not state["streamed"]:
                            yield {"type": "token", "text": block.text}
                    elif btype == "ToolUseBlock":
                        logger.info("[ExecLayer:%s] tool use: %s", self._name, block.name)
                        yield tracker.on_tool_use(block)
                # 可观测:请求级 credit/token 从 Assistant.usage 读(官方红线,勿从 Result.usage 取)
                _obs_record_assistant(msg)
            elif kind == "UserMessage":
                # 工具执行结果:content 为 ToolResultBlock 列表(普通文本为 str,跳过)
                if isinstance(msg.content, (list, tuple)):
                    for block in msg.content:
                        if type(block).__name__ == "ToolResultBlock":
                            ev = tracker.on_tool_result(block)
                            if ev:
                                # 可观测:工具产物 span(入参含生成 SQL,输出含执行结果)
                                _obs_record_tool_result(
                                    ev,
                                    tracker.arguments_of(ev.get("tool_call_id") or ""),
                                )
                            yield ev
            elif kind == "ResultMessage":
                state["final"] = msg
                meta.update({
                    "duration_ms": msg.duration_ms,
                    "num_turns": msg.num_turns,
                    "session_id": msg.session_id,
                    "subtype": msg.subtype,
                })
                tracker.update_meta(meta)
                # 可观测:会话累计 credit(Qoder Result.total_credits, 勿与请求级相加)
                _obs_record_result(msg)

    @staticmethod
    def _final_event(state: dict, texts: list, meta: dict) -> dict:
        """ResultMessage → 终结 done 事件(错误携带 error)."""
        final = state.get("final")
        if final is not None and final.is_error:
            err = final.result or "; ".join(final.errors or []) or f"subtype={final.subtype}"
            return {
                "type": "done",
                "result": ExecutionResult(success=False, error=err, output="".join(texts), meta=meta),
            }
        output = (final.result if final and final.result else None) or "".join(texts)
        return {"type": "done", "result": ExecutionResult(success=True, output=output, meta=meta)}

    # ── 单发模式(回落路径): 每轮新起进程 + resume ────────────────

    async def _execute_stream_oneshot(self, task: ExecutionTask, options) -> AsyncIterator[dict]:
        """通过 SDK query() 单发流式执行(长对话不可用/失败时的回落)."""
        from qoder_agent_sdk import query

        logger.info("[ExecLayer:%s] Running via qoder-agent-sdk (one-shot stream)", self._name)
        # 工具 handler 通过 ContextVar 读取工作空间/用户上下文
        from services.datamind.execution.sdk_tools import set_execution_context

        ctx_token = set_execution_context(task.context) if task.context else None
        extra = (task.context.extra or {}) if task.context else {}
        resuming = bool(extra.get("session_id"))
        # resume 会话时历史已在会话内,不再文本注入
        prompt = self._prompt_with_attachments(task, include_history=not resuming)
        texts: list[str] = []
        meta: dict = {"cli": self.cli_name, "mode": "sdk"}
        state: dict = {"final": None, "streamed": False}
        # 工具调用跟踪:ToolUseBlock/ToolResultBlock → tool_start/tool_result 事件
        from services.datamind.execution.stream_utils import ToolEventTracker

        tracker = ToolEventTracker()

        async def run(prompt_text: str) -> AsyncIterator[dict]:
            async for ev in self._map_messages(
                query(prompt=prompt_text, options=options), texts, meta, tracker, state
            ):
                yield ev

        try:
            try:
                async for ev in run(prompt):
                    yield ev
            except Exception as e:
                if resuming and not state["streamed"]:
                    # 会话不存在/已失效:降级为新会话(带历史注入)重试一次
                    logger.warning(
                        "[ExecLayer:%s] Resume session failed, retry as new session: %s", self._name, e
                    )
                    options.resume = None
                    meta.pop("session_id", None)
                    state["final"] = None
                    try:
                        async for ev in run(self._prompt_with_attachments(task)):
                            yield ev
                    except Exception as e2:
                        logger.error("[ExecLayer:%s] SDK query failed: %s", self._name, e2)
                        yield {
                            "type": "done",
                            "result": ExecutionResult(
                                success=False, error=str(e2), output="".join(texts), meta=meta
                            ),
                        }
                        return
                else:
                    logger.error("[ExecLayer:%s] SDK query failed: %s", self._name, e)
                    yield {
                        "type": "done",
                        "result": ExecutionResult(
                            success=False, error=str(e), output="".join(texts), meta=meta
                        ),
                    }
                    return
        finally:
            if ctx_token is not None:
                from services.datamind.execution.sdk_tools import ExecutionContextVar

                ExecutionContextVar.reset(ctx_token)

        yield self._final_event(state, texts, meta)

    # ── 长对话模式: 会话级持久 QoderSDKClient ────────────────────

    async def _execute_stream_pooled(
        self, task: ExecutionTask, options, key: str, fingerprint: str
    ) -> AsyncIterator[dict]:
        """取得/创建本会话持久客户端并执行一轮流式对话."""
        from qoder_agent_sdk import QoderSDKClient
        from services.datamind.execution.sdk_tools import set_execution_context
        from services.datamind.execution.stream_utils import ToolEventTracker

        ctx = task.context
        # 每轮都设当前上下文(task-local): 兼容 handler 在本任务内联派发的实现;
        # 首次 connect 前必须设置: SDK 读循环/控制处理任务冻结的是创建时的上下文快照,
        # 而 ctx 全部字段均已参与指纹, 复用期间不会漂移
        if ctx:
            set_execution_context(ctx)

        entry, stale, need_connect = await self._pool_acquire(key, fingerprint)
        if stale is not None:
            await self._pool_disconnect(stale)
        if entry is None:
            # 同一会话并发轮次(多标签页): 不抢占, 回落单发
            logger.info("[ExecLayer:%s] Session %s busy, fallback to one-shot", self._name, key)
            async for ev in self._execute_stream_oneshot(task, options):
                yield ev
            return

        if need_connect:
            try:
                entry.client = QoderSDKClient(options=options)
                await entry.client.connect()
                logger.info("[ExecLayer:%s] Pooled qoder session opened (conversation %s)", self._name, key)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[ExecLayer:%s] Pooled session connect failed, fallback to one-shot: %s", self._name, e
                )
                async with _pool_lock():
                    if _POOL.get(key) is entry:
                        _POOL.pop(key, None)
                entry.client = None
                entry.turn_active = False
                async for ev in self._execute_stream_oneshot(task, options):
                    yield ev
                return
        else:
            logger.info("[ExecLayer:%s] Reusing pooled qoder session (conversation %s)", self._name, key)

        texts: list[str] = []
        meta: dict = {"cli": self.cli_name, "mode": "sdk-pooled"}
        state: dict = {"final": None, "streamed": False}
        tracker = ToolEventTracker()
        keep = False
        try:
            # 持久会话内已有全部历史;指纹重建走 resume 时同样在会话内
            prompt = self._prompt_with_attachments(task, include_history=not options.resume)
            await entry.client.query(prompt)
            entry.turn_active = True
            async for ev in self._map_messages(
                entry.client.receive_response(), texts, meta, tracker, state
            ):
                yield ev
            keep = state["final"] is not None
        except Exception as e:  # noqa: BLE001
            logger.error("[ExecLayer:%s] Pooled turn failed (conversation %s): %s", self._name, key, e)
            if not state["streamed"]:
                # 尚未输出: 本轮换单发重试(resume 失效另有处理)
                async for ev in self._execute_stream_oneshot(task, options):
                    yield ev
            else:
                yield {
                    "type": "done",
                    "result": ExecutionResult(
                        success=False, error=str(e), output="".join(texts), meta=meta
                    ),
                }
        finally:
            # GeneratorExit/CancelledError(客户端断开)亦经此: 不保留, 退役清理
            await self._pool_release(key, entry, keep)

        if keep:
            yield self._final_event(state, texts, meta)

    # ── 池管理 ──────────────────────────────────────────────

    async def _pool_acquire(self, key: str, fingerprint: str):
        """锁内获取可复用 entry 并标记 busy.

        返回 (entry, stale, need_connect):
        - (命中, None, False): 复用已有持久会话
        - (新建占位, 旧 entry|None, True): 调用方 connect 后填入(占位防并发重复 connect)
        - (None, None, False): 被并发轮次占用, 调用方回落单发
        """
        async with _pool_lock():
            await self._pool_evict_expired()
            existing = _POOL.get(key)
            stale = None
            if existing is not None:
                if existing.busy:
                    return None, None, False
                if existing.fingerprint == fingerprint:
                    existing.busy = True
                    existing.last_used = time.monotonic()
                    return existing, None, False
                # 指纹变化: 摘除旧连接, 新建后由调用方 connect(resume 保历史)
                _POOL.pop(key, None)
                stale = existing
            entry = _PooledChatSession(fingerprint)
            entry.busy = True
            _POOL[key] = entry
            await self._pool_enforce_cap()
            return entry, stale, True

    async def _pool_release(self, key: str, entry, keep: bool) -> None:
        """轮次结束: 正常收到 Result 则归还在池; 否则摘除并后台退役(interrupt→排空→断连)."""
        if keep:
            async with _pool_lock():
                entry.turn_active = False
                entry.last_used = time.monotonic()
                entry.busy = False
            return
        async with _pool_lock():
            if _POOL.get(key) is entry:
                _POOL.pop(key, None)
        asyncio.create_task(self._pool_retire(entry))

    async def _pool_retire(self, entry) -> None:
        """后台退役: 中断在途轮次并排空至 Result, 避免残留污染下轮复用, 最后断连."""
        client = entry.client
        if client is None:
            return
        try:
            if entry.turn_active:
                await client.interrupt()

                async def _drain():
                    async for _ in client.receive_response():
                        pass

                try:
                    await asyncio.wait_for(_drain(), timeout=20)
                except Exception:  # noqa: BLE001  排空失败/超时不阻碍断连
                    pass
        except Exception as e:  # noqa: BLE001
            logger.debug("[ExecLayer:%s] Retire interrupt skipped: %s", self._name, e)
        finally:
            await self._pool_disconnect(entry)

    @staticmethod
    async def _pool_disconnect(entry) -> None:
        if entry is None or entry.client is None:
            return
        entry.turn_active = False
        try:
            await asyncio.wait_for(entry.client.disconnect(), timeout=10)
        except Exception:  # noqa: BLE001
            pass

    async def _pool_evict_expired(self) -> None:
        """空闲超时淘汰(锁内调用): 限制闲置 qodercli 进程驻留内存."""
        ttl = float(self.config.get("session_idle_ttl", 900) or 0)
        if ttl <= 0:
            return
        now = time.monotonic()
        for k, e in list(_POOL.items()):
            if not e.busy and now - e.last_used > ttl:
                _POOL.pop(k, None)
                logger.info("[ExecLayer:%s] Idle-evict qoder session (conversation %s)", self._name, k)
                await self._pool_disconnect(e)

    async def _pool_enforce_cap(self) -> None:
        """LRU 上限(锁内调用): 防止长对话进程无限增长."""
        max_n = int(self.config.get("session_max", 8) or 0)
        if max_n <= 0 or len(_POOL) <= max_n:
            return
        victims = sorted(
            ((e, k) for k, e in _POOL.items() if not e.busy),
            key=lambda kv: kv[0].last_used,
        )
        while len(_POOL) > max_n and victims:
            e, k = victims.pop(0)
            if _POOL.get(k) is not e:
                continue
            _POOL.pop(k, None)
            logger.info("[ExecLayer:%s] LRU-evict qoder session (conversation %s)", self._name, k)
            await self._pool_disconnect(e)

    def _options_fingerprint(self, options, ctx) -> str:
        """options+执行上下文的稳定投影 → sha1(长对话重建判据).

        Waker 提示词/逐工具勾选/MCP 配置/agents/模型/数据源/用户任一变化均触发
        重建(resume 保历史);排除 instance 对象引用、resume/auth 等易变项。
        """
        import hashlib

        extra = dict((ctx.extra or {}) if ctx else {})
        extra.pop("session_id", None)  # 会话 id 正常轮转不触发重建
        proj = {
            "cli_path": options.cli_path,
            "model": options.model,
            "waker_key": extra.get("waker_key"),
            "cwd": options.cwd,
            "add_dirs": sorted(str(d) for d in (options.add_dirs or [])),
            "max_turns": options.max_turns,
            "system_prompt": options.system_prompt,
            "allowed_tools": sorted(options.allowed_tools or []),
            "disallowed_tools": sorted(options.disallowed_tools or []),
            "permission_mode": options.permission_mode,
            "env": sorted((options.env or {}).items()),
            "mcp_servers": {
                name: {k: v for k, v in (cfg or {}).items() if k != "instance"}
                for name, cfg in (options.mcp_servers or {}).items()
            },
            "agents": {
                name: {
                    "description": getattr(a, "description", None),
                    "prompt": getattr(a, "prompt", None),
                    "tools": getattr(a, "tools", None),
                    "mcpServers": getattr(a, "mcpServers", None),
                }
                for name, a in (getattr(options, "agents", None) or {}).items()
            },
            "ctx": {
                "workspace_id": ctx.workspace_id if ctx else 0,
                "datasource_id": ctx.datasource_id if ctx else 0,
                "user_id": ctx.user_id if ctx else 0,
                "username": ctx.username if ctx else "",
                "user_role": ctx.user_role if ctx else "",
                "model_id": getattr(ctx, "model_id", None) if ctx else None,
                "extra": extra,
            },
        }
        return hashlib.sha1(
            json.dumps(proj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """一次性执行(复用流式实现,收集最终结果)."""
        result = ExecutionResult(success=False, error="执行层未返回结果")
        async for ev in self.execute_stream(task):
            if ev.get("type") == "done":
                result = ev.get("result") or result
        return result
