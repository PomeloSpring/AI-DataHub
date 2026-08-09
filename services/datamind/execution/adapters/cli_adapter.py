"""CLIProcessAdapter — 本地 CLI 进程执行层.

通过子进程调用本地 CLI 工具(opencode / qoder 等)。
每个 CLI 有独立的命令模板(见 discovery.KNOWN_CLIS),
模板中 {question} 占位符会被替换为任务问题;
也可在执行层 config.command 中整体覆盖默认模板。

安全约束:
- 仅执行白名单内的已知 CLI(KNOWN_CLIS),命令参数不经过 shell
- 数据能力后续经 MCP 配置注入,CLI 进程不接触数据源凭据
"""

import asyncio
import logging
import os
import shutil
from typing import AsyncIterator, Optional

from services.datamind.execution.adapters.base import ExecutionLayerAdapter
from services.datamind.execution.models import (
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
)

logger = logging.getLogger(__name__)


class CLIProcessAdapter(ExecutionLayerAdapter):
    """本地 CLI 进程执行层适配器."""

    def __init__(self, layer_name: str, config: dict):
        """初始化.

        Args:
            layer_name: 执行层名称(来自 adh_execution_layers.name)
            config: 执行层配置,支持字段:
                - cli_name: CLI 名称(opencode / qoder ...)
                - cli_path: 可执行文件路径(默认从 PATH 查找)
                - env: 附加环境变量
                - command: 自定义命令模板(覆盖默认)
                - timeout: 默认超时秒数
        """
        from services.datamind.execution.discovery import KNOWN_CLIS

        self._name = layer_name
        self.config = config or {}
        self.cli_name = self.config.get("cli_name", "")
        cli_info = KNOWN_CLIS.get(self.cli_name, {})
        self.binary = cli_info.get("binary", self.cli_name)
        self.cli_path = self.config.get("cli_path") or shutil.which(self.binary) or self.binary
        self.version_cmd = cli_info.get("version_cmd", [self.binary, "--version"])
        if self.cli_path != self.binary and self.version_cmd and self.version_cmd[0] == self.binary:
            self.version_cmd = [self.cli_path] + self.version_cmd[1:]
        self.capabilities = cli_info.get("capabilities", [])
        self.default_command = self.config.get("command") or cli_info.get("command", [])
        self.timeout = int(self.config.get("timeout", 300))
        # 模型配置:命令行参数 + 可用模型列表命令
        self.model_flag = list(cli_info.get("model_flag", []))
        self.model = self.config.get("model") or ""
        self.models_cmd = list(cli_info.get("models_cmd", []))
        if self.cli_path != self.binary and self.models_cmd and self.models_cmd[0] == self.binary:
            self.models_cmd = [self.cli_path] + self.models_cmd[1:]

    @property
    def name(self) -> str:
        return self._name

    @property
    def layer_type(self) -> str:
        return "cli"

    # ── 执行 ──────────────────────────────────────────────────────

    def _prompt_with_attachments(self, task: ExecutionTask, include_history: bool = True) -> str:
        """组装最终 prompt:多轮对话历史 + 当前问题 + 附件清单.

        CLI/SDK 无会话概念,多轮上下文以文本形式前置注入;
        附件以绝对路径清单追加,CLI 可直接读文件。
        SDK 会话恢复(resume)场景下 include_history=False,避免与
        会话内已有上下文重复。
        """
        prompt = task.question
        # 多轮对话历史(取最近 10 轮,避免 prompt 过长)
        if include_history and task.history:
            lines = []
            for h in task.history[-10:]:
                role = "用户" if h.get("role") == "user" else "助手"
                content = str(h.get("content") or "").strip()
                if content:
                    lines.append(f"{role}: {content[:2000]}")
            if lines:
                prompt = (
                    "以下是之前的对话历史,请结合上下文回答最后的问题:\n"
                    + "\n".join(lines)
                    + f"\n\n当前问题: {task.question}"
                )
        if not task.attachments:
            return prompt
        lines = ["", "用户上传了以下多模态附件文件,可按绝对路径直接读取处理:"]
        for a in task.attachments:
            lines.append(f"- {a.get('filename', '')} ({a.get('category', '')}): {a.get('path', '')}")
        return prompt + "\n" + "\n".join(lines)

    def _effective_model(self, task: ExecutionTask) -> str:
        """生效模型:运行时 chat 选择的 model_ref 优先,其次执行层配置模型."""
        extra = task.context.extra if task.context else None
        ref = (extra or {}).get("model_ref") or ""
        return ref or self.model

    def _build_command(self, task: ExecutionTask) -> list:
        """根据命令模板构建参数列表({question} 占位符替换).

        模板首元素为 CLI 二进制名,会被替换为解析后的实际路径;
        配置了 model 且 CLI 支持 model_flag 时,在问题参数前插入模型参数。
        """
        if not self.default_command:
            raise ValueError(f"CLI '{self.cli_name}' 未配置命令模板")
        prompt = self._prompt_with_attachments(task)
        model = self._effective_model(task)
        cmd: list = []
        model_args = ([*self.model_flag, model] if model and self.model_flag else [])
        for part in self.default_command:
            if isinstance(part, str) and "{question}" in part:
                cmd.extend(model_args)
                cmd.append(part.replace("{question}", prompt))
            elif isinstance(part, str):
                cmd.append(part.replace("{question}", prompt))
            else:
                cmd.append(part)
        # 模板中无 {question} 占位符时,模型参数追加到末尾
        if model_args and not any("{question}" in str(p) for p in self.default_command):
            cmd.extend(model_args)
        if cmd and cmd[0] == self.binary:
            cmd[0] = self.cli_path
        return cmd

    def _build_env(self, task: ExecutionTask) -> dict:
        env = os.environ.copy()
        env.update({k: str(v) for k, v in (self.config.get("env") or {}).items()})
        self._inject_llm_env(env, task)
        return env

    def _inject_llm_env(self, env: dict, task: ExecutionTask) -> None:
        """为无自带凭据配置的 CLI 注入系统模型中心的大模型凭据.

        opencode 子进程模式不物化 opencode.json,模型由 --model 指定,
        凭据通过环境变量(ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL 等)注入;
        执行层 config.env 中已显式设置的变量优先,不覆盖。
        """
        if self.cli_name != "opencode":
            return
        try:
            from services.datamind.execution.llm_resources import resolve_system_llm_model
            llm = resolve_system_llm_model(self._effective_model(task))
            if not llm or not llm.get("api_key"):
                return
            provider = (llm.get("provider") or "anthropic").lower()
            if provider == "anthropic":
                env.setdefault("ANTHROPIC_API_KEY", llm["api_key"])
                if llm.get("base_url"):
                    # opencode 的 anthropic provider 在 baseURL 后直接拼 /messages,
                    # 网关的 Anthropic 兼容端点在 /v1/messages,需补齐 /v1 后缀
                    base = llm["base_url"].rstrip("/")
                    if not base.endswith("/v1"):
                        base += "/v1"
                    env.setdefault("ANTHROPIC_BASE_URL", base)
            elif provider == "openai":
                env.setdefault("OPENAI_API_KEY", llm["api_key"])
                if llm.get("base_url"):
                    env.setdefault("OPENAI_BASE_URL", llm["base_url"])
        except Exception as e:
            logger.warning("[ExecLayer:%s] Inject LLM env failed: %s", self._name, e)

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """通过子进程执行 CLI 命令并收集输出."""
        try:
            cmd = self._build_command(task)
        except ValueError as e:
            return ExecutionResult(success=False, error=str(e))

        if not shutil.which(cmd[0]):
            return ExecutionResult(
                success=False,
                error=f"CLI 可执行文件不存在: {cmd[0]}",
            )

        timeout = task.timeout or self.timeout
        logger.info("[ExecLayer:%s] Running CLI: %s", self._name, cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(task),
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return ExecutionResult(success=False, error=f"CLI 执行超时({timeout}s)")
        except FileNotFoundError:
            return ExecutionResult(success=False, error=f"CLI 可执行文件不存在: {cmd[0]}")
        except Exception as e:
            logger.error("[ExecLayer:%s] CLI exec failed: %s", self._name, e)
            return ExecutionResult(success=False, error=str(e))

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        success = proc.returncode == 0
        return ExecutionResult(
            success=success,
            output=stdout,
            error="" if success else (stderr or f"exit code {proc.returncode}"),
            meta={
                "exit_code": proc.returncode,
                "cli": self.cli_name,
                "command": cmd,
                "stderr_tail": stderr[-2000:] if stderr else "",
            },
        )

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        """流式执行:逐块读取 CLI stdout 并 yield token 事件."""
        try:
            cmd = self._build_command(task)
        except ValueError as e:
            yield {"type": "done", "result": ExecutionResult(success=False, error=str(e))}
            return

        if not ((os.path.isabs(cmd[0]) and os.path.exists(cmd[0])) or shutil.which(cmd[0])):
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"CLI 可执行文件不存在: {cmd[0]}"),
            }
            return

        timeout = task.timeout or self.timeout
        logger.info("[ExecLayer:%s] Running CLI (stream): %s", self._name, cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(task),
            )
        except FileNotFoundError:
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"CLI 可执行文件不存在: {cmd[0]}"),
            }
            return

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        chunks: list[str] = []
        timed_out = False
        try:
            assert proc.stdout is not None
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=remaining)
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                chunks.append(text)
                yield {"type": "token", "text": text}
            if timed_out:
                proc.kill()
            await proc.wait()
        except Exception as e:
            logger.error("[ExecLayer:%s] CLI stream failed: %s", self._name, e)
            try:
                proc.kill()
            except Exception:
                pass
            yield {"type": "done", "result": ExecutionResult(success=False, error=str(e))}
            return

        if timed_out:
            yield {
                "type": "done",
                "result": ExecutionResult(success=False, error=f"CLI 执行超时({timeout}s)", output="".join(chunks)),
            }
            return

        stderr_b = await proc.stderr.read() if proc.stderr else b""
        stderr = stderr_b.decode(errors="replace")
        success = proc.returncode == 0
        yield {
            "type": "done",
            "result": ExecutionResult(
                success=success,
                output="".join(chunks),
                error="" if success else (stderr or f"exit code {proc.returncode}"),
                meta={
                    "exit_code": proc.returncode,
                    "cli": self.cli_name,
                    "command": cmd,
                    "stderr_tail": stderr[-2000:] if stderr else "",
                },
            ),
        }

    # ── 能力与健康检查 ────────────────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """CLI 执行层的能力以 capabilities 描述(非结构化工具列表)."""
        return [{"name": cap, "type": "capability"} for cap in self.capabilities]

    async def health_check(self) -> HealthStatus:
        """运行版本命令验证 CLI 可用性."""
        version = await self.get_version(timeout=15)
        if version is None:
            return HealthStatus(
                healthy=False,
                message=f"无法执行版本命令: {' '.join(self.version_cmd)}",
                details={"cli_path": self.cli_path},
            )
        return HealthStatus(
            healthy=True,
            message=version,
            details={"cli_path": self.cli_path, "version": version},
        )

    async def get_version(self, timeout: int = 15) -> Optional[str]:
        """执行版本命令,返回首行输出;失败返回 None."""
        if not self.version_cmd:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.version_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._build_env(None),
            )
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                return None
            out = out_b.decode(errors="replace").strip()
            return out.splitlines()[0] if out else None
        except Exception as e:
            logger.warning("[ExecLayer:%s] Version check failed: %s", self._name, e)
            return None

    async def list_models(self, timeout: int = 30) -> list[str]:
        """查询 CLI 可用模型列表;CLI 无模型清单时回退系统模型中心配置."""
        if not self.models_cmd:
            return self._list_system_models()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.models_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._build_env(None),
            )
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                return []
            models = []
            for line in out_b.decode(errors="replace").splitlines():
                line = line.strip()
                # 跳过表头与空行
                if line and line.upper() != "MODEL":
                    models.append(line)
            return models
        except Exception as e:
            logger.warning("[ExecLayer:%s] List models failed: %s", self._name, e)
            return []

    def _list_system_models(self) -> list[str]:
        """CLI 不支持列出模型时,用系统模型中心(adh_llm_models)配置填充.

        返回模型引用(`{provider}/{model_name}`),可直接作为
        --model 参数 / opencode model 配置使用。
        """
        try:
            from services.datamind.execution.llm_resources import list_system_llm_models
            return [m["ref"] for m in list_system_llm_models() if m.get("ref", "/") != "/"]
        except Exception as e:
            logger.warning("[ExecLayer:%s] List system models failed: %s", self._name, e)
            return []
