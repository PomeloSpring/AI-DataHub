"""LLM 交互可观测(O0 地基):trace_id 贯穿 + credit/token 用量落库(MySQL)。

公开 API:
    observability.begin(...) -> TraceRecorder|None      # 入口创建回合
    observability.finalize(status, error, final_answer) # 入口收尾落库
    observability.record_llm_call(...)                  # 深层记录模型请求(credit/token)
    observability.record_span(...)                      # 深层记录关键产物(工具调用/SQL/结果)
    observability.set_session_credits(total, model_usage)
    observability.set_trace_id() / get_trace_id()       # 中间件
    observability.trace_id_for_response() / message_uuid()
    observability.set_result(...) / set_handled_by(...)  # 入口回写回合结果/处理方
所有函数在未开启或无 recorder 时安全 no-op。
"""
from .context import (
    get_trace_id,
    new_uuid,
    record_llm_call,
    record_span,
    set_session_credits,
    set_trace_id,
)
from .recorder import (
    begin,
    current,
    finalize,
    message_uuid,
    set_handled_by,
    set_result,
    trace_id_for_response,
)

__all__ = [
    "begin", "current", "finalize",
    "record_llm_call", "record_span", "set_session_credits",
    "set_trace_id", "get_trace_id", "new_uuid",
    "trace_id_for_response", "message_uuid", "set_result", "set_handled_by",
]
