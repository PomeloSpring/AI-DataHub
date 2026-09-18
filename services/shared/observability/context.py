"""LLM 交互可观测 — 上下文传播 (contextvars)。

一次"用户回合"= 一个 trace_id。中间件设置 trace_id;入口(chat/playground/agent)
创建 TraceRecorder 并注入身份;深层调用(llm_client / QoderSDKAdapter)通过
current_recorder() 记录 llm_call span,无需层层透传参数。

所有 API 均为"未开启即 no-op",且绝不抛出(观测不得影响主链路)。
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("observability.context")

# 请求级上下文
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("adh_trace_id", default=None)
_recorder: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("adh_trace_recorder", default=None)


def new_uuid() -> str:
    return uuid.uuid4().hex


def set_trace_id(trace_id: Optional[str] = None) -> str:
    """设置当前请求 trace_id(中间件调用);返回该 id。"""
    tid = trace_id or new_uuid()
    _trace_id.set(tid)
    return tid


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def current_recorder() -> Optional[Any]:
    return _recorder.get()


def set_recorder(recorder: Optional[Any]) -> None:
    _recorder.set(recorder)


# ── 便捷记录 API(深层调用用;无 recorder 或关闭时静默) ──────────────

def record_llm_call(
    *,
    name: str = "llm_call",
    model_ref: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    credits: Optional[float] = None,
    original_credits: Optional[float] = None,
    billable: Optional[bool] = None,
    cost_usd: Optional[float] = None,
    duration_ms: Optional[int] = None,
    status: str = "success",
    error: str = "",
    input_text: str = "",
    output_text: str = "",
) -> None:
    """记录一次模型请求(请求级 credit 来源:Qoder Assistant.usage / 直连 tokens)。"""
    rec = _recorder.get()
    if rec is None:
        return
    try:
        rec.add_llm_call(
            name=name, model_ref=model_ref,
            input_tokens=input_tokens, output_tokens=output_tokens,
            credits=credits, original_credits=original_credits, billable=billable,
            cost_usd=cost_usd, duration_ms=duration_ms, status=status, error=error,
            input_text=input_text, output_text=output_text,
        )
    except Exception as e:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("record_llm_call ignored: %s", e)


def record_span(
    *,
    kind: str = "tool_call",
    name: str = "",
    status: str = "success",
    duration_ms: int = 0,
    input_text: str = "",
    output_text: str = "",
    error_text: str = "",
    model_ref: str = "",
) -> None:
    """记录一个关键产物 span(工具调用/生成 SQL/执行结果/图表等)。

    与 record_llm_call 不同:不累计 token/credit,仅保存执行步骤的输入输出产物。
    """
    rec = _recorder.get()
    if rec is None:
        return
    try:
        rec.add_span(
            kind=kind, name=name, status=status, duration_ms=duration_ms,
            input_text=input_text, output_text=output_text, error_text=error_text,
            model_ref=model_ref,
        )
    except Exception as e:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("record_span ignored: %s", e)


def set_session_credits(total_credits: Optional[float], model_usage: Optional[dict]) -> None:
    """记录会话累计 credit(Qoder Result.total_credits / model_usage),写 trace 级。"""
    rec = _recorder.get()
    if rec is None:
        return
    try:
        rec.set_session_credits(total_credits, model_usage)
    except Exception as e:  # noqa: BLE001
        logger.debug("set_session_credits ignored: %s", e)
