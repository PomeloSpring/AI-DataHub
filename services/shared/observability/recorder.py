"""TraceRecorder:一个"用户回合"的 span 缓冲 + 汇总落库。

- `enabled=False`(默认)时 `begin()` 返回 None,整条链路 no-op。
- finalize 用后台线程写库,不阻塞对话/流式响应;观测异常一律吞。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from .context import (
    get_trace_id,
    new_uuid,
    set_recorder,
)

logger = logging.getLogger("observability.recorder")

# 单线程后台写库即可(内部小量)
_flush_pool: Optional[ThreadPoolExecutor] = None


def _get_flush_pool() -> ThreadPoolExecutor:
    global _flush_pool
    if _flush_pool is None:
        _flush_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="obs-flush")
    return _flush_pool


def _enabled() -> bool:
    from services.shared.common.config import OBSERVABILITY_ENABLED
    return bool(OBSERVABILITY_ENABLED)


class TraceRecorder:
    def __init__(self, meta: dict):
        self._t0 = time.monotonic()
        self.meta = meta
        self.spans: list[dict] = []
        self._flushed = False
        # 汇总
        self.input_tokens = 0
        self.output_tokens = 0
        self.credits: Optional[float] = None      # 本轮请求级之和
        self.cost_usd: Optional[float] = None
        self.session_credits: Optional[float] = None  # 会话累计(Qoder)
        self.llm_call_count = 0
        # 回合结果(由入口显式回写;None 表示未设置)
        self.result_status: Optional[str] = None
        self.result_error: Optional[str] = None
        self.result_answer: Optional[str] = None

    def set_result(self, status=None, error=None, final_answer=None) -> None:
        """回写本回合结果(供 agent 路径在子生成器中记录最终答案/状态)。"""
        if status is not None:
            self.result_status = status
        if error is not None:
            self.result_error = error
        if final_answer is not None:
            self.result_answer = final_answer

    # ── span 采集 ──────────────────────────────────────────────
    def add_llm_call(self, **kw: Any) -> None:
        dur = kw.get("duration_ms")
        if dur is None:
            dur = int((time.monotonic() - self._t0) * 1000)
        it = int(kw.get("input_tokens") or 0)
        ot = int(kw.get("output_tokens") or 0)
        self.spans.append({
            "span_id": new_uuid(),
            "trace_id": self.meta["trace_id"],
            "kind": "llm_call",
            "name": kw.get("name") or "llm_call",
            "status": kw.get("status") or "success",
            "duration_ms": int(dur),
            "model_ref": kw.get("model_ref") or "",
            "input_tokens": it,
            "output_tokens": ot,
            "total_tokens": it + ot,
            "credits": kw.get("credits"),
            "original_credits": kw.get("original_credits"),
            "billable": kw.get("billable"),
            "cost_usd": kw.get("cost_usd"),
            "input_text": kw.get("input_text") or "",
            "output_text": kw.get("output_text") or "",
            "error_text": kw.get("error") or "",
            "started_at": datetime.now(timezone.utc).replace(tzinfo=None),
        })
        self.input_tokens += it
        self.output_tokens += ot
        self.llm_call_count += 1
        c = kw.get("credits")
        if isinstance(c, (int, float)):
            self.credits = (self.credits or 0.0) + float(c)
        cu = kw.get("cost_usd")
        if isinstance(cu, (int, float)):
            self.cost_usd = (self.cost_usd or 0.0) + float(cu)

    def set_session_credits(self, total_credits, model_usage: Optional[dict]) -> None:
        if isinstance(total_credits, (int, float)):
            self.session_credits = float(total_credits)
        self.meta["_model_usage"] = model_usage  # 预留,当前仅会话累计入 session_credits

    def add_span(self, *, kind: str, name: str = "", status: str = "success",
                 duration_ms: int = 0, input_text: str = "", output_text: str = "",
                 error_text: str = "", model_ref: str = "") -> None:
        """记录一个非 LLM 请求的产物 span(工具调用/SQL 执行/图表等),不累计 token。"""
        self.spans.append({
            "span_id": new_uuid(),
            "trace_id": self.meta["trace_id"],
            "kind": kind,
            "name": name or kind,
            "status": status,
            "duration_ms": int(duration_ms or 0),
            "model_ref": model_ref or "",
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "credits": None,
            "original_credits": None,
            "billable": None,
            "cost_usd": None,
            "input_text": input_text or "",
            "output_text": output_text or "",
            "error_text": error_text or "",
            "started_at": datetime.now(timezone.utc).replace(tzinfo=None),
        })

    # ── 汇总落库 ────────────────────────────────────────────────
    def finalize(self, status: str = "", error: str = "", final_answer: str = "") -> None:
        if self._flushed:
            return
        self._flushed = True
        # 显式入参优先,否则用 set_result 回写的值,再否则默认 success
        status = status or self.result_status or "success"
        error = error or self.result_error or ""
        final_answer = final_answer or self.result_answer or ""
        summary = dict(self.meta)
        summary.update({
            "status": status,
            "error_message": error or "",
            "final_answer": final_answer or "",
            "duration_ms": int((time.monotonic() - self._t0) * 1000),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "credits": self.credits,
            "cost_usd": self.cost_usd,
            "session_credits": self.session_credits,
            "llm_call_count": self.llm_call_count,
            "span_count": len(self.spans),
        })
        summary.pop("_model_usage", None)
        self._submit(summary)

    def _submit(self, summary: dict) -> None:
        spans = list(self.spans)

        def _job():
            from .store import save_trace
            save_trace(summary, spans)

        try:
            _get_flush_pool().submit(_job)
        except Exception as e:  # noqa: BLE001
            logger.debug("flush submit failed, fallback sync: %s", e)
            try:
                _job()
            except Exception as e2:  # noqa: BLE001
                logger.debug("flush job failed: %s", e2)


# ── 入口 API ────────────────────────────────────────────────────

def begin(
    *,
    entrypoint: str = "chat",
    user_id: int = 0,
    username: str = "",
    user_role: str = "",
    workspace_id: int = 0,
    datasource_id: int = 0,
    conversation_id: int = 0,
    model_ref: str = "",
    question: str = "",
) -> Optional[TraceRecorder]:
    """创建当前回合 recorder 并注入 contextvar;未开启返回 None。"""
    if not _enabled():
        return None
    try:
        trace_id = get_trace_id() or new_uuid()
        rec = TraceRecorder({
            "trace_id": trace_id,
            "message_uuid": new_uuid(),
            "conversation_id": conversation_id or 0,
            "user_id": user_id or 0,
            "username": username or "",
            "user_role": user_role or "",
            "workspace_id": workspace_id or 0,
            "datasource_id": datasource_id or 0,
            "entrypoint": entrypoint,
            "model_ref": model_ref or "",
            "question": question or "",
            "started_at": datetime.now(timezone.utc).replace(tzinfo=None),
        })
        set_recorder(rec)
        return rec
    except Exception as e:  # noqa: BLE001
        logger.debug("begin trace ignored: %s", e)
        return None


def current() -> Optional[TraceRecorder]:
    from .context import current_recorder
    return current_recorder()


def finalize(status: str = "", error: str = "", final_answer: str = "") -> None:
    rec = current()
    if rec is None:
        return
    try:
        rec.finalize(status=status, error=error, final_answer=final_answer)
    except Exception as e:  # noqa: BLE001
        logger.debug("finalize ignored: %s", e)
    finally:
        set_recorder(None)


def set_result(status=None, error=None, final_answer=None) -> None:
    """入口/子生成器回写本回合结果(无 recorder 或未开启时 no-op)。"""
    rec = current()
    if rec is None:
        return
    try:
        rec.set_result(status=status, error=error, final_answer=final_answer)
    except Exception as e:  # noqa: BLE001
        logger.debug("set_result ignored: %s", e)


def trace_id_for_response() -> Optional[str]:
    rec = current()
    if rec is not None:
        return rec.meta.get("trace_id")
    return get_trace_id()


def message_uuid() -> Optional[str]:
    rec = current()
    return rec.meta.get("message_uuid") if rec is not None else None
