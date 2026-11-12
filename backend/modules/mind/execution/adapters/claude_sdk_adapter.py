"""Claude SDK 适配器：Anthropic 协议直连模型供应商（凭据来自模型中心）.

与 QoderSDKAdapter 共用同一套安全执行链（`secure_sdk.build_options` / `execute_stream`）：
工具注册、执行前鉴权、资源护栏、出站脱敏、会话隔离**全部同源**，只有模型通道不同——
本后端不吃 Qoder 账号/Credits，因此不受企业 BYOK 开关约束（见 `anthropic_direct`）。
"""

from typing import AsyncIterator

from backend.modules.mind.execution.adapters.cli_adapter import CLIProcessAdapter
from backend.modules.mind.execution.models import ExecutionResult, ExecutionTask


class ClaudeSDKAdapter(CLIProcessAdapter):
    """执行状态只来自服务端会话，不使用进程内长会话池（与 Qoder 后端同口径）。"""

    #: 模型通道后端标识：Anthropic 协议直连供应商（凭据来自模型中心）
    backend = "claude"

    def _build_options(self, task: ExecutionTask):
        from backend.modules.mind.execution.secure_sdk import build_options
        return build_options(self, task, "claude")

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        from contextlib import aclosing
        from backend.modules.mind.execution.secure_sdk import execute_stream
        async with aclosing(execute_stream(self, task, "claude")) as stream:
            async for event in stream:
                yield event

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        result = ExecutionResult(success=False, error="Harness 未返回结果")
        async for event in self.execute_stream(task):
            if event.get("type") == "done":
                result = event["result"]
        return result
