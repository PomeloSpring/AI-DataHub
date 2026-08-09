"""工具调用上下文传递 — ContextVar 注入工作空间/用户信息.

qoder-agent-sdk 的 @tool handler 只接收 args,不携带调用方身份;
由 QoderSDKAdapter 在 execute_stream 中 set_execution_context(),
同一 asyncio 任务链内的工具 handler 通过 get_execution_context() 读取。
"""

import contextvars
from typing import Optional

from services.datamind.execution.models import ExecutionContext

_context_var: contextvars.ContextVar[Optional[ExecutionContext]] = contextvars.ContextVar(
    "execution_tool_context", default=None
)

ExecutionContextVar = _context_var


def set_execution_context(ctx: ExecutionContext) -> contextvars.Token:
    """派发前注入上下文,返回 token 供 reset."""
    return _context_var.set(ctx)


def get_execution_context() -> ExecutionContext:
    """工具 handler 内读取上下文;未注入时返回空上下文(workspace_id=0)."""
    return _context_var.get() or ExecutionContext()
