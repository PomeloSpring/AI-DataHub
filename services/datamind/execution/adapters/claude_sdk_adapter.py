"""ClaudeSDKAdapter — 基于 claude-agent-sdk 的 Claude 执行层.

与 QoderSDKAdapter 对称:通过官方 Python SDK 以结构化协议调用
SDK 内置的 claude 运行时(自包含原生二进制,无需 Node),
并支持会话级资源注入:

- adh_mcp_servers → options.mcp_servers(stdio/sse/http 直接映射)
- adh_agents      → options.agents(AgentDefinition 子代理)
- sdk_tools       → 进程内自定义工具(catalog/query/semantic)

与 qoder 的差异:
- 鉴权不用 PAT,模型凭据从系统模型中心(adh_llm_models)解析,
  经 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL 注入(仅支持 anthropic 协议模型)
- SDK 自带运行时二进制(_bundled/claude),无需外部 CLI 路径
"""

import logging
import os
from typing import AsyncIterator

from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask, HealthStatus
from services.datamind.execution.workspace_resources import (
    load_workspace_resources,
    parse_json_field,
)

logger = logging.getLogger(__name__)


class ClaudeSDKAdapter(CLIProcessAdapter):
    """claude-agent-sdk 执行层适配器(cli 类型,mode=sdk)."""

    @property
    def layer_type(self) -> str:
        return "cli"

    def __init__(self, layer_name: str, config: dict):
        super().__init__(layer_name, config)
        # SDK 自带运行时二进制;校准 cli_path / version_cmd 供健康检查使用
        from services.datamind.execution.discovery import claude_bundled_cli

        bundled = claude_bundled_cli()
        if bundled:
            self.cli_path = bundled
            self.version_cmd = [bundled, "--version"]

    # ── 工作空间资源加载 ──────────────────────────────────────────

    def _load_workspace_resources(self, workspace_id: int) -> dict:
        """按 workspace_id 加载 MCP servers / agents(含全局 workspace_id=0)."""
        res = load_workspace_resources(workspace_id)
        out: dict = {"mcp_servers": {}, "agents": {}}
        for r in res["mcp_rows"]:
            cfg = self._map_mcp_server(dict(r))
            if cfg:
                out["mcp_servers"][r["name"]] = cfg
        for r in res["agent_rows"]:
            out["agents"][r["name"]] = self._map_agent(dict(r), out["mcp_servers"])
        return out

    @staticmethod
    def _map_mcp_server(row: dict) -> dict | None:
        """adh_mcp_servers 行 → SDK McpServerConfig(与 qoder 格式一致)."""
        transport = (row.get("transport") or "").lower()
        if transport == "stdio":
            if not row.get("command"):
                return None
            env = parse_json_field(row.get("env"), {})
            return {
                "type": "stdio",
                "command": row["command"],
                "args": parse_json_field(row.get("args"), []),
                **({"env": {k: str(v) for k, v in env.items()}} if env else {}),
            }
        if transport in ("sse", "http") and row.get("url"):
            return {"type": transport, "url": row["url"]}
        return None

    @staticmethod
    def _map_agent(row: dict, injected_mcps: dict) -> dict:
        """adh_agents 行 → SDK AgentDefinition 参数(mcp_server_ids 转为已注入的 server 名)."""
        ids = parse_json_field(row.get("mcp_server_ids"), [])
        return {
            "description": row.get("description") or row.get("display_name") or row["name"],
            "prompt": row.get("system_prompt") or "",
            "mcpServers": list(injected_mcps.keys()) if ids else None,
        }

    # ── 模型凭据解析 ─────────────────────────────────────────────

    def _resolve_llm(self, task: ExecutionTask) -> tuple[dict, str]:
        """从系统模型中心解析 anthropic 协议模型凭据.

        Returns:
            (env 凭据字典, model_name);provider 非 anthropic 时抛 ValueError。
        """
        from services.datamind.execution.llm_resources import resolve_system_llm_model

        ref = self._effective_model(task)
        llm = resolve_system_llm_model(ref)
        if not llm or not llm.get("api_key"):
            raise ValueError(f"未找到可用的系统 LLM 配置(model_ref={ref or '默认'})")
        provider = (llm.get("provider") or "anthropic").lower()
        if provider != "anthropic":
            raise ValueError(
                f"Claude 执行层仅支持 anthropic 协议模型,当前模型 provider={provider}"
            )
        env = {"ANTHROPIC_API_KEY": llm["api_key"]}
        if llm.get("base_url"):
            # Anthropic SDK 协议:base_url 原样使用(客户端自行拼 /v1/messages)
            env["ANTHROPIC_BASE_URL"] = llm["base_url"].rstrip("/")
        return env, llm.get("model_name") or ""

    # ── Options 构建 ─────────────────────────────────────────────

    def _build_options(self, task: ExecutionTask):
        from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

        workspace_id = task.context.workspace_id if task.context else 0
        res = self._load_workspace_resources(workspace_id)
        # 执行层 config.env 显式凭据优先,其次系统模型中心
        env = {k: str(v) for k, v in (self.config.get("env") or {}).items()}
        if not env.get("ANTHROPIC_API_KEY"):
            llm_env, model_name = self._resolve_llm(task)
            for k, v in llm_env.items():
                env.setdefault(k, v)
        else:
            model_name = ""
        # 运行时 chat 选择的模型优先于执行层配置模型
        runtime_ref = ((task.context.extra or {}) if task.context else {}).get("model_ref") or ""
        if runtime_ref and "/" in runtime_ref:
            model_name = runtime_ref.split("/", 1)[1] or model_name
        model = model_name or self.model or None
        # root 下 bypassPermissions 被 CLI 拒绝(exit 1);平台已按工作空间目录沙箱,
        # 以 IS_SANDBOX 声明沙箱环境(官方容器场景同款豁免)
        if os.geteuid() == 0 and not env.get("IS_SANDBOX"):
            env["IS_SANDBOX"] = "1"

        tool_groups = self._build_tool_servers()
        options = ClaudeAgentOptions(
            model=model,
            env=env,
            permission_mode="bypassPermissions",  # headless 免权限确认
            setting_sources=[],  # 不加载机器上的用户/项目配置,保持进程独享
            max_turns=int(self.config.get("max_turns", 0)) or None,
            include_partial_messages=True,  # 启用流式增量(stream_event)
        )
        # 工作目录:工作空间文件沙箱(与内置 Agent 文件工具同目录)
        cwd = self.config.get("cwd")
        if not cwd and workspace_id:
            try:
                from services.datamind.agent.file_tools import workspace_root
                cwd = str(workspace_root(workspace_id))
            except Exception as e:
                logger.warning("[ExecLayer:%s] Resolve workspace cwd failed: %s", self._name, e)
        if cwd:
            options.cwd = cwd
        # 多轮对话:恢复前端回传的 SDK 会话(首轮无 session_id 时新建)
        session_id = ((task.context.extra or {}) if task.context else {}).get("session_id") or ""
        if session_id:
            options.resume = session_id
        # bypassPermissions 模式下无需枚举预授权,外部 MCP 工具可直接使用
        if res["mcp_servers"]:
            options.mcp_servers.update(res["mcp_servers"])
        # 进程内自定义工具(SDK @tool):与外部 MCP server 合并注入
        for srv_name, srv_cfg in tool_groups["servers"].items():
            options.mcp_servers[srv_name] = srv_cfg
        options.allowed_tools.extend(tool_groups["allowed_tools"])
        # tools 权限白名单(工作空间绑定配置):未允许的标准工具进 deny-list,
        # MCP/自定义工具不受影响
        if self.config.get("allowed_tools"):
            from services.datamind.execution.tool_catalog import disallowed_tools
            denied = disallowed_tools(self.config["allowed_tools"], "claude")
            if denied:
                options.disallowed_tools.extend(denied)
        if res["agents"]:
            options.agents = {
                name: AgentDefinition(
                    description=a.get("description") or name,
                    prompt=a.get("prompt") or "",
                    mcpServers=a.get("mcpServers"),
                )
                for name, a in res["agents"].items()
            }
        if task.context and task.context.system_prompt:
            options.system_prompt = task.context.system_prompt
        logger.info(
            "[ExecLayer:%s] SDK options: workspace=%s mcp=%s agents=%s sdk_tools=%s model=%s disallowed=%s",
            self._name, workspace_id,
            list(res["mcp_servers"].keys()), list(res["agents"].keys()),
            list(tool_groups["servers"].keys()), model or "-",
            list(options.disallowed_tools or []),
        )
        return options

    def _build_tool_servers(self) -> dict:
        """按 config.sdk_tools 构建进程内自定义工具 server(qoder 适配器共享逻辑)."""
        from services.datamind.execution.sdk_tools import build_tool_servers

        return build_tool_servers("claude", self.config.get("sdk_tools"))

    # ── 执行 ─────────────────────────────────────────────────────

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        """通过 SDK 流式执行,将消息映射为 token/thinking/done 事件."""
        try:
            from claude_agent_sdk import query
        except ImportError as e:
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"claude-agent-sdk 未安装: {e}"),
            }
            return

        try:
            options = self._build_options(task)
        except Exception as e:
            logger.error("[ExecLayer:%s] Build SDK options failed: %s", self._name, e)
            yield {"type": "done", "result": ExecutionResult(success=False, error=str(e))}
            return

        logger.info("[ExecLayer:%s] Running via claude-agent-sdk (stream)", self._name)
        # 工具 handler 通过 ContextVar 读取工作空间/用户上下文
        from services.datamind.execution.sdk_tools import set_execution_context

        ctx_token = set_execution_context(task.context) if task.context else None
        extra = (task.context.extra or {}) if task.context else {}
        resuming = bool(extra.get("session_id"))
        # resume 会话时历史已在会话内,不再文本注入
        prompt = self._prompt_with_attachments(task, include_history=not resuming)
        texts: list[str] = []
        meta: dict = {"cli": self.cli_name, "mode": "sdk"}
        final = None
        streamed = False  # 是否已通过 stream_event 推送过增量

        async def _consume_stream(prompt_text: str):
            nonlocal final, streamed
            async for msg in query(prompt=prompt_text, options=options):
                kind = type(msg).__name__
                if kind == "StreamEvent":
                    # partial messages:细粒度增量片段(打字机效果)
                    delta = (getattr(msg, "event", None) or {}).get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta" and delta.get("text"):
                        streamed = True
                        yield {"type": "token", "text": delta["text"]}
                    elif dtype == "thinking_delta" and delta.get("thinking"):
                        yield {"type": "thinking", "text": delta["thinking"]}
                elif kind == "AssistantMessage":
                    for block in getattr(msg, "content", []) or []:
                        btype = type(block).__name__
                        if btype == "TextBlock" and getattr(block, "text", ""):
                            texts.append(block.text)
                            # 已流式推送过则不重复发 token;未流式时回退整段输出
                            if not streamed:
                                yield {"type": "token", "text": block.text}
                        elif btype == "ToolUseBlock":
                            logger.info("[ExecLayer:%s] tool use: %s", self._name, getattr(block, "name", "?"))
                elif kind == "ResultMessage":
                    final = msg
                    meta.update({
                        "duration_ms": getattr(msg, "duration_ms", None),
                        "num_turns": getattr(msg, "num_turns", None),
                        "session_id": getattr(msg, "session_id", None),
                        "subtype": getattr(msg, "subtype", None),
                        "total_cost_usd": getattr(msg, "total_cost_usd", None),
                    })

        try:
            async for ev in _consume_stream(prompt):
                yield ev
        except Exception as e:
            if resuming and not streamed:
                # 会话不存在/已失效:降级为新会话(带历史注入)重试一次
                logger.warning(
                    "[ExecLayer:%s] Resume session failed, retry as new session: %s", self._name, e
                )
                options.resume = None
                meta.pop("session_id", None)
                try:
                    async for ev in _consume_stream(self._prompt_with_attachments(task)):
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

        if final is not None and getattr(final, "is_error", False):
            err = getattr(final, "result", "") or getattr(final, "subtype", "unknown_error")
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=err, output="".join(texts), meta=meta),
            }
            return

        output = (getattr(final, "result", None) if final else None) or "".join(texts)
        yield {"type": "done", "result": ExecutionResult(success=True, output=output, meta=meta)}

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """一次性执行(复用流式实现,收集最终结果)."""
        result = ExecutionResult(success=False, error="执行层未返回结果")
        async for ev in self.execute_stream(task):
            if ev.get("type") == "done":
                result = ev.get("result") or result
        return result

    # ── 健康检查 ─────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """校验 SDK 已安装且内置运行时可用."""
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            return HealthStatus(healthy=False, message=f"claude-agent-sdk 未安装: {e}")
        version = await self.get_version(timeout=30)
        if version is None:
            return HealthStatus(
                healthy=False,
                message="SDK 内置运行时不可用",
                details={"cli_path": self.cli_path},
            )
        return HealthStatus(
            healthy=True,
            message=version,
            details={"cli_path": self.cli_path, "version": version},
        )
