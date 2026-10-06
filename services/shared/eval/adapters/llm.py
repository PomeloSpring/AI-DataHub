"""LLM 功能适配器：工具调用轨迹 / 出站脱敏 / 涉密拒绝 的评测。

三种模式（``payload.mode``）：

* ``trace``（默认，纯确定性，无 LLM、零成本）
    回放录制好的工具调用轨迹 ``payload.trace``，断言"该调的调了、不该调的没调、
    涉密被拒、出站无泄露"。适合进 CI。

* ``handler``（纯确定性，**调真实工具 handler**，无 LLM）
    直接调用 ``function_tools.FUNCTION_ACTION_SPECS`` 里的真实 handler，
    把它的返回文本交给断言。这样能真正覆盖安全视图与出站守卫的实现，
    而不是只测"轨迹看起来对"。

* ``live``（真实调 LLM，结果只进 ``score``）
    走注入的 ``llm_caller``。**判定通过与否仍由确定性断言给**，
    LLM 评分只辅助评价解释质量（既定决策：不让 LLM 评分决定安全/取数正确性）。
    未注入 caller 时显式报错，不静默降级成"跳过"。
"""

from __future__ import annotations

from services.shared.eval.contract import Case, CaseContractError

SOURCES_LABEL = "LLM 功能（工具调用轨迹 / 出站结果）"

#: 可注入的真实 LLM 调用器与评分器（由调用方按需装配；未注入则 live 模式报错）
LLM_CALLER = None      # callable(question, payload) -> {"answer": str, "tool_calls": [...]}
LLM_JUDGE = None       # callable(question, answer) -> float in [0,1]，仅作 score


def _trace_mode(payload: dict) -> dict:
    calls = payload.get("trace") or []
    if not isinstance(calls, list):
        raise CaseContractError("payload.trace 必须是工具调用数组")
    return {
        "tool_calls": list(calls),
        "answer": str(payload.get("answer") or ""),
        "user_confirmed": bool(payload.get("user_confirmed")),
        "sources": {"mode": "trace"},
    }


def _handler_mode(payload: dict) -> dict:
    """调用真实功能工具 handler，产出一条等价的"工具调用 + 返回文本"观测。"""
    from services.datamind.execution import function_tools

    action = (payload.get("action") or "").strip()
    spec = function_tools.FUNCTION_ACTION_SPECS.get(action)
    if not spec:
        raise CaseContractError(
            f"payload.action={action!r} 未登记；可用: {sorted(function_tools.FUNCTION_ACTION_SPECS)}")

    import asyncio
    handler = spec["handler"]
    args = payload.get("args") or {}
    try:
        out = handler(args)
        if asyncio.iscoroutine(out):
            out = asyncio.get_event_loop().run_until_complete(out)
    except Exception as exc:  # noqa: BLE001 —— handler 抛错本身也是被测行为
        out = {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}

    texts = [str(b.get("text") or "") for b in (out.get("content") or []) if isinstance(b, dict)]
    calls = [{
        "name": spec["tool_name"],
        "args": args,
        "is_error": bool(out.get("isError")),
        "is_write": not spec.get("read_only", True),
        "text": " || ".join(texts),
    }]
    return {
        "tool_calls": calls,
        "answer": "",
        "user_confirmed": bool(payload.get("user_confirmed", True)),
        "sources": {"mode": "handler", "action": action},
    }


def _live_mode(case: Case, payload: dict) -> dict:
    if LLM_CALLER is None:
        raise CaseContractError(
            "用例配置为 live 模式，但未注入 LLM_CALLER。"
            "请由调用方装配真实 LLM 调用器，或把 mode 改为 trace/handler（确定性）。")
    out = LLM_CALLER(case.question, payload) or {}
    return {
        "tool_calls": list(out.get("tool_calls") or []),
        "answer": str(out.get("answer") or ""),
        "user_confirmed": bool(out.get("user_confirmed", payload.get("user_confirmed"))),
        "sources": {"mode": "live"},
    }


def run_case(case: Case) -> dict:
    payload = case.payload or {}
    mode = (payload.get("mode") or "trace").strip()
    if mode == "trace":
        return _trace_mode(payload)
    if mode == "handler":
        return _handler_mode(payload)
    if mode == "live":
        return _live_mode(case, payload)
    raise CaseContractError(f"payload.mode 无效: {mode!r}，应为 trace/handler/live")


def score_answer(case: Case, observed: dict) -> float | None:
    """LLM 辅助评分：只评价解释质量，**不参与 passed 判定**。

    未注入评分器或非 live 模式时返回 None（不是 0，避免被误读为"答得差"）。
    """
    if LLM_JUDGE is None:
        return None
    if (case.payload or {}).get("mode") != "live":
        return None
    try:
        val = LLM_JUDGE(case.question, observed.get("answer") or "")
        return max(0.0, min(1.0, float(val)))
    except Exception:  # noqa: BLE001 —— 评分失败不影响确定性判定
        return None
