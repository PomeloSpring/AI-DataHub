"""Claude SDK 适配器：与 Qoder 共用服务端会话与 fail-closed 工具策略。"""
from typing import AsyncIterator

from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask, HealthStatus


class ClaudeSDKAdapter(CLIProcessAdapter):
    def __init__(self, layer_name: str, config: dict):
        super().__init__(layer_name, config)
        from services.datamind.execution.discovery import claude_bundled_cli
        bundled = claude_bundled_cli()
        if bundled:
            self.cli_path = bundled
            self.version_cmd = [bundled, "--version"]

    def _resolve_llm(self, task: ExecutionTask) -> tuple[dict, str]:
        from services.datamind.execution.llm_resources import resolve_system_llm_model
        llm = resolve_system_llm_model(self._effective_model(task))
        if not llm or not llm.get("api_key"):
            raise ValueError("未找到可用的系统 LLM 配置")
        if (llm.get("provider") or "anthropic").lower() != "anthropic":
            raise ValueError("Claude 执行层仅支持 anthropic 协议模型")
        env = {"ANTHROPIC_API_KEY": llm["api_key"]}
        if llm.get("base_url"):
            env["ANTHROPIC_BASE_URL"] = llm["base_url"].rstrip("/")
        return env, llm.get("model_name") or ""

    def _build_options(self, task: ExecutionTask):
        from services.datamind.execution.secure_sdk import build_options
        return build_options(self, task, "claude")

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        from contextlib import aclosing
        from services.datamind.execution.secure_sdk import execute_stream
        async with aclosing(execute_stream(self, task, "claude")) as stream:
            async for event in stream:
                yield event

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        result = ExecutionResult(success=False, error="执行层未返回结果")
        async for event in self.execute_stream(task):
            if event.get("type") == "done":
                result = event["result"]
        return result

    async def health_check(self) -> HealthStatus:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return HealthStatus(healthy=False, message="claude-agent-sdk 未安装")
        version = await self.get_version(timeout=30)
        return HealthStatus(healthy=bool(version), message=version or "SDK 运行时不可用")
