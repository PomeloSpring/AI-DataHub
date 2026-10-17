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
    from backend.common.config import OBSERVABILITY_ENABLED
    return bool(OBSERVABILITY_ENABLED)


def _credit_cost_rate() -> float:
    """credit → 成本折算单价（0 = 未配置，成本列保持 NULL 由前端显示 —，绝不臆造）。"""
    try:
        from backend.common.config import OBSERVABILITY_CREDIT_COST_RATE
        return max(0.0, float(OBSERVABILITY_CREDIT_COST_RATE or 0))
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERVABILITY_CREDIT_COST_RATE 解析失败, 不做成本折算: %s", e)
        return 0.0


def _credit_cost_currency() -> str:
    """折算单价的币种（与单价同源，默认 CNY = Qoder CN 资源包官方价）。"""
    try:
        from backend.common.config import OBSERVABILITY_CREDIT_COST_CURRENCY
        return (OBSERVABILITY_CREDIT_COST_CURRENCY or "CNY").strip().upper()
    except Exception:  # noqa: BLE001
        return "CNY"


class TraceRecorder:
    def __init__(self, meta: dict):
        self._t0 = time.monotonic()
        self.meta = meta
        self.spans: list[dict] = []
        self._flushed = False
        # 汇总
        self.input_tokens = 0
        self.output_tokens = 0
        self.credits: Optional[float] = None      # 本回合 credit 消耗（请求级之和或会话累计增量）
        self.cost_amount: Optional[float] = None   # 本回合成本（币种见 cost_currency）
        self.cost_currency: str = ""
        self.session_credits: Optional[float] = None  # 会话累计(Qoder)
        self.llm_call_count = 0
        # ResultMessage 上的回合级汇总(CLI 不给请求级 credit/token 时的唯一真实来源)
        self.result_usage: dict = {}
        self.result_cost_usd: Optional[float] = None
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

    def set_handled_by(self, handled_by: str) -> None:
        """回写本回合的**处理方**（agent=走了执行层模型 / intent_gate=前置意图层本地直答）。

        这一列是意图漏斗拦截率的权威口径：拦截回合不起子进程、不烧 credit，
        与"模型答的"必须在同一张表上分得开，否则省了多少根本无从回答。
        """
        if handled_by:
            self.meta["handled_by"] = handled_by

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
            "cost_amount": kw.get("cost_usd"),
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
            self.cost_amount = (self.cost_amount or 0.0) + float(cu)
            self.cost_currency = "USD"   # CLI/直连上报的成本就是美元

    def set_session_credits(self, total_credits, model_usage: Optional[dict], *,
                            usage: Optional[dict] = None,
                            cost_usd: Optional[float] = None) -> None:
        """收 Qoder ResultMessage 的回合/会话汇总。

        `total_credits`/`model_usage[].credits` 是**会话累计**,单列存 session_credits;
        本回合 credit 由落库线程按"与上一条 trace 累计值之差"补算(见 _resolve_turn_credits)。
        """
        if isinstance(total_credits, (int, float)):
            self.session_credits = float(total_credits)
        if isinstance(usage, dict) and usage:
            self.result_usage = usage
        if isinstance(cost_usd, (int, float)) and cost_usd:
            self.result_cost_usd = float(cost_usd)
            self.cost_currency = "USD"
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
            "cost_amount": None,
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
        # token:请求级累加为 0(CLI 不在 AssistantMessage.usage 里给 token)时,
        # 取 ResultMessage.usage 的回合汇总 —— 仍是 CLI 上报的真实值,不是估算。
        it, ot = self.input_tokens, self.output_tokens
        if it + ot == 0 and self.result_usage:
            it = int(self.result_usage.get("input_tokens") or 0)
            ot = int(self.result_usage.get("output_tokens") or 0)
        summary = dict(self.meta)
        summary.update({
            "status": status,
            "error_message": error or "",
            "final_answer": final_answer or "",
            "duration_ms": int((time.monotonic() - self._t0) * 1000),
            "input_tokens": it,
            "output_tokens": ot,
            "total_tokens": it + ot,
            "credits": self.credits,
            "cost_amount": self.result_cost_usd or self.cost_amount,
            "cost_currency": self.cost_currency,
            "session_credits": self.session_credits,
            "llm_call_count": self.llm_call_count,
            "span_count": len(self.spans),
        })
        # 生效模型名:入口未传 model_ref(走默认模型)时,从 llm_call span 回填真实模型,
        # 否则模型用量/Trace 列表整列显示空(历史缺陷根因)。
        if not (summary.get("model_ref") or "").strip():
            summary["model_ref"] = self._first_span_model()
        summary.pop("_model_usage", None)
        self._submit(summary)

    def _first_span_model(self) -> str:
        for sp in self.spans:
            if sp.get("kind") == "llm_call" and (sp.get("model_ref") or "").strip():
                return str(sp["model_ref"]).strip()
        return ""

    def _submit(self, summary: dict) -> None:
        spans = list(self.spans)

        def _job():
            from .store import save_trace
            _resolve_turn_credits(summary)
            _apply_cost_amount(summary)
            save_trace(summary, spans)

        try:
            _get_flush_pool().submit(_job)
        except Exception as e:  # noqa: BLE001
            logger.debug("flush submit failed, fallback sync: %s", e)
            try:
                _job()
            except Exception as e2:  # noqa: BLE001
                logger.debug("flush job failed: %s", e2)


# ── 落库前的 credit / 成本补算(后台线程,不占请求链路) ────────────────

def _resolve_turn_credits(summary: dict) -> None:
    """请求级 credit 缺失时,用"会话累计增量"补齐本回合 credit。

    Qoder CLI 在流式模式(include_partial_messages=True)下不再在 AssistantMessage.usage
    里给 credits,只在 ResultMessage 上给**会话累计** total_credits —— 不补算则回合/
    模型维度 credit 恒为 0(历史缺陷)。基线 = 同会话上一条 trace 的累计值;
    累计回落视为会话重置(清上下文/新建 session),按全额计。
    基线查询失败时**不猜测**:credit 保持 NULL(前端显示 —),只留日志。
    """
    if summary.get("credits") is not None:
        return
    cum = summary.get("session_credits")
    conversation_id = int(summary.get("conversation_id") or 0)
    if cum is None or conversation_id <= 0:
        return
    from .store import prior_session_credits
    status, base = prior_session_credits(conversation_id, str(summary.get("trace_id") or ""))
    if status == "unknown":
        logger.warning("会话 credit 基线不可得, 本回合 credit 不估算 (trace_id=%s)",
                       summary.get("trace_id"))
        return
    cum = float(cum)
    delta = cum if status == "first" or cum < float(base or 0) else cum - float(base)
    summary["credits"] = round(delta, 6)


def _apply_cost_amount(summary: dict) -> None:
    """CLI 未上报成本时,按配置的 credit 单价与币种折算(前端标为估算)。

    CLI 上报的 total_cost_usd 是美元 → 已带 cost_currency='USD'，不在此覆盖；
    单价未配置(OBSERVABILITY_CREDIT_COST_RATE=0)则保持 NULL → 前端显示 —，
    绝不拿臆想价格表充数。
    """
    if summary.get("cost_amount"):
        return
    rate = _credit_cost_rate()
    credits = summary.get("credits")
    if rate > 0 and credits is not None:
        summary["cost_amount"] = round(float(credits) * rate, 6)
        summary["cost_currency"] = _credit_cost_currency()


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
    handled_by: str = "agent",
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
            "handled_by": handled_by or "agent",
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


def set_handled_by(handled_by: str) -> None:
    """回写本回合处理方(agent / intent_gate)；无 recorder 或未开启时 no-op。"""
    rec = current()
    if rec is None:
        return
    try:
        rec.set_handled_by(handled_by)
    except Exception as e:  # noqa: BLE001
        logger.debug("set_handled_by ignored: %s", e)


def trace_id_for_response() -> Optional[str]:
    rec = current()
    if rec is not None:
        return rec.meta.get("trace_id")
    return get_trace_id()


def message_uuid() -> Optional[str]:
    rec = current()
    return rec.meta.get("message_uuid") if rec is not None else None
