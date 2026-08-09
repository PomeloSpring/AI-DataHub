"""QoderSDKAdapter — 基于 qoder-agent-sdk 的 Qoder 执行层.

与 CLIProcessAdapter(子进程 + 命令模板)不同,本适配器通过官方 SDK
以 stream-json 结构化协议调用 qodercli,并支持会话级资源注入:

- adh_mcp_servers → options.mcp_servers(stdio/sse/http 直接映射)
- adh_agents      → options.agents(AgentDefinition: prompt/tools/mcpServers)
- adh_skills      → 预留(后续物化为 skill 文件经 skills/plugins 注入)

注入均为会话级,不落盘、不污染全局配置;permission_mode=bypassPermissions
避免 headless 环境卡在权限确认。
"""

import json
import logging
import os
from typing import AsyncIterator

from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask

logger = logging.getLogger(__name__)


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

    # ── Options 构建 ─────────────────────────────────────────────

    def _build_options(self, task: ExecutionTask):
        from qoder_agent_sdk import AgentDefinition, QoderAgentOptions
        from qoder_agent_sdk.auth import AccessTokenAuthOptions, AccessTokenEnvVar

        workspace_id = task.context.workspace_id if task.context else 0
        res = self._load_workspace_resources(workspace_id)
        env = {k: str(v) for k, v in (self.config.get("env") or {}).items()}
        tool_groups = self._build_tool_servers()
        # SDK 要求显式 auth:优先用 config.env 中的 PAT,其次进程环境变量
        token = env.get("QODER_PERSONAL_ACCESS_TOKEN") or os.environ.get("QODER_PERSONAL_ACCESS_TOKEN")
        if token:
            auth = AccessTokenAuthOptions(access_token=token)
        else:
            auth = AccessTokenAuthOptions(access_token=AccessTokenEnvVar(env_var="QODER_PERSONAL_ACCESS_TOKEN"))
        # 运行时 chat 选择的模型优先于执行层配置模型
        runtime_ref = ((task.context.extra or {}) if task.context else {}).get("model_ref") or ""
        model = runtime_ref or self.model or None
        options = QoderAgentOptions(
            cli_path=self.cli_path,
            model=model,
            env=env,
            auth=auth,
            permission_mode="bypassPermissions",  # headless 免权限确认
            setting_sources=[],  # 不加载机器上的用户/项目配置,保持进程独享
            cwd=self.config.get("cwd") or None,
            max_turns=int(self.config.get("max_turns", 0)) or None,
            include_partial_messages=True,  # 启用流式增量(stream_event)
        )
        # 多轮对话:恢复前端回传的 SDK 会话(首轮无 session_id 时新建)
        session_id = ((task.context.extra or {}) if task.context else {}).get("session_id") or ""
        if session_id:
            options.resume = session_id
        if res["mcp_servers"]:
            options.mcp_servers = res["mcp_servers"]
            options.allowed_mcp_server_names = list(res["mcp_servers"].keys())
        # 进程内自定义工具(SDK @tool):与外部 MCP server 合并注入
        for srv_name, srv_cfg in tool_groups["servers"].items():
            options.mcp_servers[srv_name] = srv_cfg
            options.allowed_mcp_server_names.append(srv_name)
        options.allowed_tools.extend(tool_groups["allowed_tools"])
        # tools 权限白名单(工作空间绑定配置):未允许的标准工具进 deny-list,
        # MCP/自定义工具不受影响
        if self.config.get("allowed_tools"):
            from services.datamind.execution.tool_catalog import disallowed_tools
            denied = disallowed_tools(self.config["allowed_tools"], "qoder")
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
        # skills 预留:adh_skills 物化为 skill 文件后注入 options.skills
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
        """按 config.sdk_tools 构建进程内自定义工具 server.

        config.sdk_tools 为工具组名列表(["catalog", "query"] 或 "all"),
        默认全部启用。构建逻辑与 ClaudeSDKAdapter 共享(sdk_tools 包)。
        """
        from services.datamind.execution.sdk_tools import build_tool_servers

        return build_tool_servers("qoder", self.config.get("sdk_tools"))

    # ── 执行 ─────────────────────────────────────────────────────

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        """通过 SDK 流式执行,将消息映射为 token/done 事件."""
        try:
            from qoder_agent_sdk import query
        except ImportError as e:
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"qoder-agent-sdk 未安装: {e}"),
            }
            return

        try:
            options = self._build_options(task)
        except Exception as e:
            logger.error("[ExecLayer:%s] Build SDK options failed: %s", self._name, e)
            yield {"type": "done", "result": ExecutionResult(success=False, error=str(e))}
            return

        logger.info("[ExecLayer:%s] Running via qoder-agent-sdk (stream)", self._name)
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
                    delta = (msg.event or {}).get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta" and delta.get("text"):
                        streamed = True
                        yield {"type": "token", "text": delta["text"]}
                    elif dtype == "thinking_delta" and delta.get("thinking"):
                        yield {"type": "thinking", "text": delta["thinking"]}
                elif kind == "AssistantMessage":
                    for block in msg.content:
                        btype = type(block).__name__
                        if btype == "TextBlock" and block.text:
                            texts.append(block.text)
                            # 已流式推送过则不重复发 token;未流式时回退整段输出
                            if not streamed:
                                yield {"type": "token", "text": block.text}
                        elif btype == "ToolUseBlock":
                            logger.info("[ExecLayer:%s] tool use: %s", self._name, block.name)
                elif kind == "ResultMessage":
                    final = msg
                    meta.update({
                        "duration_ms": msg.duration_ms,
                        "num_turns": msg.num_turns,
                        "session_id": msg.session_id,
                        "subtype": msg.subtype,
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

        if final is not None and final.is_error:
            err = final.result or "; ".join(final.errors or []) or f"subtype={final.subtype}"
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=err, output="".join(texts), meta=meta),
            }
            return

        output = (final.result if final and final.result else None) or "".join(texts)
        yield {"type": "done", "result": ExecutionResult(success=True, output=output, meta=meta)}

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """一次性执行(复用流式实现,收集最终结果)."""
        result = ExecutionResult(success=False, error="执行层未返回结果")
        async for ev in self.execute_stream(task):
            if ev.get("type") == "done":
                result = ev.get("result") or result
        return result
