"""工具调用上下文传递 — ContextVar 注入工作空间/用户信息.

qodercn-agent-sdk 的 @tool handler 只接收 args,不携带调用方身份;
由 QoderSDKAdapter 在 execute_stream 中 set_execution_context(),
同一 asyncio 任务链内的工具 handler 通过 get_execution_context() 读取。
"""

import contextvars
from typing import Optional

from backend.modules.mind.execution.models import ExecutionContext

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


def record_unattended_query(ctx, *, sql, datasource_id, dialect, columns, rows,
                            row_count, truncated, source) -> bool:
    """无人值守报告的取数结果采集点（定时任务专用，唯一入口）。

    只有无人值守链路会在 ``ctx.extra["unattended_query_sink"]`` 里挂采集回调；交互链路
    没有这个键，本函数直接返回 False——那不是我偷偷回退，而是交互回合的结果另有归属
    （会话消息与结果卡）。采集回调抛错必须向上冒：定时任务拿不到受控结果=报告没有证据，
    不能静默产出一份空报告（no-silent-degradation）。

    只登记服务端事实（待执行 SQL / 生效源 / 方言 / 行列），这些均不下发 LLM（护栏 §7）；
    报告侧据此算来源权限快照，与 ``report_service.execute_semantic_source`` 同一口径。
    """
    sink = (getattr(ctx, "extra", None) or {}).get("unattended_query_sink")
    if sink is None:
        return False
    sink({"sql": sql, "datasource_id": int(datasource_id or 0), "dialect": dialect or "mysql",
          "columns": list(columns or []), "rows": list(rows or []),
          "row_count": int(row_count or 0), "truncated": bool(truncated), "source": source})
    return True
