"""OpencodeSDKAdapter — 基于 opencode serve + opencode-sdk 的执行层.

opencode 为 client/server 架构:适配器在派发时
1. 物化临时工作目录:opencode.json 注入工作空间 MCP/agent 资源与模型凭据
2. 启动 `opencode serve`(随机端口,会话级隔离,结束即销毁)
3. 通过 opencode-sdk OpencodeClient 建会话发消息,
   并消费 /event SSE 流的 message.part.delta 实现流式 token
4. 完成后杀进程、清理临时目录

与 qoder SDK 的差异:opencode 无进程内 @tool 注入,自定义工具需
通过 adh_mcp_servers 的真实 MCP server 提供;sdk_tools 不适用。
"""

import asyncio
import json
import logging
import os
import shutil
import socket
import tempfile
from typing import AsyncIterator, Optional

from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask
from services.datamind.execution.workspace_resources import (
    load_workspace_resources,
    parse_json_field,
)

logger = logging.getLogger(__name__)

SERVER_START_TIMEOUT = 60


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OpencodeSDKAdapter(CLIProcessAdapter):
    """opencode serve + SDK 执行层适配器(cli 类型,mode=sdk)."""

    @property
    def layer_type(self) -> str:
        return "cli"

    # ── 配置物化 ─────────────────────────────────────────────────

    def _resolve_llm(self, model_ref: str = "") -> dict:
        """解析主模型凭据:运行时 model_ref 优先,其次 config.model,再次系统默认模型.

        支持 `{provider}/{model_name}` 引用与配置名,返回字段含 ref。
        """
        try:
            from services.datamind.execution.llm_resources import resolve_system_llm_model
            ref = model_ref or self.config.get("model") or ""
            return resolve_system_llm_model(ref)
        except Exception as e:
            logger.warning("[ExecLayer:%s] Resolve LLM failed: %s", self._name, e)
            return {}

    def _map_mcp(self, rows: list) -> dict:
        """adh_mcp_servers 行 → opencode.json mcp 配置."""
        result = {}
        for r in rows:
            transport = (r.get("transport") or "").lower()
            if transport == "stdio" and r.get("command"):
                env = parse_json_field(r.get("env"), {})
                result[r["name"]] = {
                    "type": "local",
                    "command": [r["command"]] + parse_json_field(r.get("args"), []),
                    "enabled": True,
                    **({"environment": {k: str(v) for k, v in env.items()}} if env else {}),
                }
            elif transport in ("sse", "http") and r.get("url"):
                result[r["name"]] = {"type": "remote", "url": r["url"], "enabled": True}
        return result

    @staticmethod
    def _map_agents(rows: list) -> dict:
        """adh_agents 行 → opencode.json agent(subagent)配置."""
        result = {}
        for r in rows:
            result[r["name"]] = {
                "description": r.get("description") or r.get("display_name") or r["name"],
                "prompt": r.get("system_prompt") or "",
                "mode": "subagent",
            }
        return result

    @staticmethod
    def _normalize_base_url(base_url: str, provider: str) -> str:
        """网关地址适配:anthropic 兼容端点在 /v1/messages,需补齐 /v1 后缀."""
        base = (base_url or "").rstrip("/")
        if provider == "anthropic" and base and not base.endswith("/v1"):
            base += "/v1"
        return base

    def _apply_system_models(self, cfg: dict, env: dict, primary: dict) -> None:
        """将系统模型中心(adh_llm_models)的全部启用模型注入 opencode 配置.

        - 按 provider 分组注册进 cfg["provider"],供 opencode 会话内切换
        - anthropic:凭据走 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL 环境变量
        - 其他 provider:以 OpenAI 兼容方式内联 baseURL / apiKey
        - 主模型(config.model 或系统默认)设为 cfg["model"]
        """
        from services.datamind.execution.llm_resources import list_system_llm_models

        models = list_system_llm_models()
        if primary and primary.get("model_name"):
            if not any(m.get("id") == primary.get("id") for m in models):
                models = [primary] + models
        elif models:
            primary = next((m for m in models if m.get("is_default")), models[0])
        if not models or not primary.get("model_name"):
            return

        groups: dict = {}
        for m in models:
            if not m.get("model_name"):
                continue
            key = (m.get("provider") or "anthropic").lower()
            groups.setdefault(key, []).append(m)

        providers: dict = {}
        for provider, rows in groups.items():
            models_map = {r["model_name"]: {"name": r.get("name") or r["model_name"]} for r in rows}
            if provider == "anthropic":
                # opencode 内置 anthropic provider,仅注册自定义模型清单
                providers["anthropic"] = {"models": models_map}
            else:
                # 同 provider 取默认模型的凭据作为该组 baseURL/apiKey
                cred = next((r for r in rows if r.get("is_default")), rows[0])
                if not cred.get("api_key"):
                    continue
                providers[provider] = {
                    "name": cred.get("name") or provider,
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {
                        "apiKey": cred["api_key"],
                        **({"baseURL": self._normalize_base_url(cred.get("base_url"), provider)}
                           if cred.get("base_url") else {}),
                    },
                    "models": models_map,
                }
        if providers:
            cfg["provider"] = providers

        provider = (primary.get("provider") or "anthropic").lower()
        model_ref = primary.get("ref") or f"{provider}/{primary['model_name']}"
        cfg["model"] = model_ref
        # 后台小模型(title 生成等)也指向同一网关模型,避免默认 haiku 401
        cfg["small_model"] = model_ref
        if provider == "anthropic" and primary.get("api_key"):
            env["ANTHROPIC_API_KEY"] = primary["api_key"]
            if primary.get("base_url"):
                env["ANTHROPIC_BASE_URL"] = self._normalize_base_url(primary["base_url"], provider)

    def _tools_permission_map(self) -> dict | None:
        """tools 权限白名单 → opencode.json tools 开关映射.

        仅对目录内的标准工具做开关(read/write/edit/glob/grep/bash/webfetch),
        不影响 task/MCP 等其他工具;未配置白名单时返回 None(不限制)。
        """
        allowed = self.config.get("allowed_tools")
        if not allowed:
            return None
        from services.datamind.execution.tool_catalog import (
            CANONICAL_NAMES,
            TOOL_NAME_MAP,
            expand_allowed_tools,
        )

        mapping = TOOL_NAME_MAP["opencode"]
        allowed_set = expand_allowed_tools(allowed, "opencode")
        return {
            mapping[c]: (mapping[c] in allowed_set)
            for c in CANONICAL_NAMES if mapping.get(c)
        }

    def _materialize(self, task: ExecutionTask) -> tuple[str, dict]:
        """生成临时工作目录(opencode.json)与 server 进程环境变量."""
        workspace_id = task.context.workspace_id if task.context else 0
        res = load_workspace_resources(workspace_id)
        # 运行时 chat 选择的模型优先于执行层配置模型
        runtime_ref = ((task.context.extra or {}) if task.context else {}).get("model_ref") or ""
        llm = self._resolve_llm(runtime_ref)

        cfg: dict = {
            "$schema": "https://opencode.ai/config.json",
            # headless 免确认
            "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
        }
        mcp = self._map_mcp(res["mcp_rows"])
        if mcp:
            cfg["mcp"] = mcp
        agents = self._map_agents(res["agent_rows"])
        if agents:
            cfg["agent"] = agents

        # tools 权限白名单(工作空间绑定配置):主会话与 subagent 同步受限
        tools_map = self._tools_permission_map()
        if tools_map is not None:
            cfg["tools"] = tools_map
            for a in agents.values():
                a["tools"] = dict(tools_map)

        env = os.environ.copy()
        env.update({k: str(v) for k, v in (self.config.get("env") or {}).items()})
        # 系统模型中心的大模型配置自动注入 opencode(含凭据与全量模型清单)
        self._apply_system_models(cfg, env, llm)

        workdir = tempfile.mkdtemp(prefix=f"datahub_oc_{workspace_id}_")
        with open(os.path.join(workdir, "opencode.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info(
            "[ExecLayer:%s] Materialized opencode workspace: ws=%s mcp=%s agents=%s model=%s",
            self._name, workspace_id, list(mcp.keys()), list(agents.keys()),
            cfg.get("model", "-"),
        )
        return workdir, env

    # ── server 生命周期 ──────────────────────────────────────────

    async def _start_server(self, workdir: str, env: dict, port: int):
        proc = await asyncio.create_subprocess_exec(
            self.cli_path, "serve", "--port", str(port), "--hostname", "127.0.0.1",
            cwd=workdir, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        import httpx

        base = f"http://127.0.0.1:{port}"
        loop = asyncio.get_event_loop()
        deadline = loop.time() + SERVER_START_TIMEOUT
        async with httpx.AsyncClient() as hc:
            while loop.time() < deadline:
                if proc.returncode is not None:
                    err = (await proc.stderr.read()).decode(errors="replace")[-800:]
                    raise RuntimeError(f"opencode serve 启动失败(exit {proc.returncode}): {err}")
                try:
                    r = await hc.get(f"{base}/session", timeout=3)
                    if r.status_code == 200:
                        return proc, base
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        proc.kill()
        raise TimeoutError(f"opencode serve 启动超时({SERVER_START_TIMEOUT}s)")

    # ── 执行 ─────────────────────────────────────────────────────

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        """启动临时 server,SSE 流式收集 delta,发送消息并映射 token/done."""
        try:
            import httpx
            from opencode_sdk import OpencodeClient
        except ImportError as e:
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"opencode-sdk 未安装: {e}"),
            }
            return

        if not ((os.path.isabs(self.cli_path) and os.path.exists(self.cli_path)) or shutil.which(self.cli_path)):
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"opencode 可执行文件不存在: {self.cli_path}"),
            }
            return

        workdir, env = self._materialize(task)
        port = _free_port()
        proc = None
        meta: dict = {"cli": self.cli_name, "mode": "sdk", "server": "opencode-serve"}
        try:
            proc, base = await self._start_server(workdir, env, port)
            logger.info("[ExecLayer:%s] opencode serve ready on %s", self._name, base)

            queue: asyncio.Queue = asyncio.Queue()
            stop_evt = asyncio.Event()
            seen_deltas: list[str] = []
            part_types: dict = {}  # partID -> part type(text/reasoning/...)

            async def collect_events():
                """消费 /event SSE:message.part.updated 记录 part 类型,
                message.part.delta 仅对 text part 入队(reasoning 思考流不输出)."""
                try:
                    async with httpx.AsyncClient(timeout=None) as hc:
                        async with hc.stream("GET", f"{base}/event") as resp:
                            async for line in resp.aiter_lines():
                                if stop_evt.is_set():
                                    return
                                if not line.startswith("data:"):
                                    continue
                                try:
                                    ev = json.loads(line[5:].strip())
                                except Exception:
                                    continue
                                ev_type = ev.get("type")
                                props = ev.get("properties") or {}
                                if ev_type == "message.part.updated":
                                    part = props.get("part") or {}
                                    if part.get("id"):
                                        part_types[part["id"]] = part.get("type")
                                elif ev_type == "message.part.delta":
                                    part_id = props.get("partID")
                                    # 仅输出已确认为 text 的 part(排除 reasoning 思考流)
                                    if part_types.get(part_id) != "text":
                                        continue
                                    delta = props.get("delta") or ""
                                    if delta:
                                        seen_deltas.append(delta)
                                        await queue.put(delta)
                except Exception as e:
                    logger.warning("[ExecLayer:%s] SSE collect stopped: %s", self._name, e)

            collector = asyncio.create_task(collect_events())
            try:
                timeout = task.timeout or self.timeout
                client = OpencodeClient(base_url=base, timeout=float(timeout))
                sess = await asyncio.to_thread(client.create_session, task.question[:40])
                session_id = sess.get("id", "")
                meta["session_id"] = session_id

                send_task = asyncio.create_task(
                    asyncio.to_thread(client.send_message, session_id, self._prompt_with_attachments(task))
                )
                # 消息处理期间持续输出增量 token
                while not send_task.done():
                    try:
                        delta = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield {"type": "token", "text": delta}
                    except asyncio.TimeoutError:
                        continue
                send_task.result()  # 抛出服务端错误(如有)
                # 排空残余增量
                while not queue.empty():
                    yield {"type": "token", "text": queue.get_nowait()}

                # 兜底:SSE 未捕获到增量时从消息列表取完整回答(仅 text part)
                full_text = await asyncio.to_thread(self._extract_assistant_text, client, session_id)
                if not seen_deltas and full_text:
                    yield {"type": "token", "text": full_text}
                output = "".join(seen_deltas) or full_text
                yield {
                    "type": "done",
                    "result": ExecutionResult(success=True, output=output, meta=meta),
                }
            finally:
                stop_evt.set()
                collector.cancel()
        except Exception as e:
            logger.error("[ExecLayer:%s] opencode sdk exec failed: %s", self._name, e)
            yield {"type": "done", "result": ExecutionResult(success=False, error=str(e), meta=meta)}
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _extract_assistant_text(client, session_id: str) -> str:
        """从会话消息列表提取最后一条 assistant 消息的文本."""
        try:
            messages = client.list_messages(session_id)
        except Exception:
            return ""
        texts = []
        for item in reversed(messages or []):
            info = item.get("info") or {}
            if info.get("role") != "assistant":
                continue
            for part in item.get("parts") or []:
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
            if texts:
                break
        return "\n".join(reversed(texts))

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """一次性执行(复用流式实现,收集最终结果)."""
        result = ExecutionResult(success=False, error="执行层未返回结果")
        async for ev in self.execute_stream(task):
            if ev.get("type") == "done":
                result = ev.get("result") or result
        return result
