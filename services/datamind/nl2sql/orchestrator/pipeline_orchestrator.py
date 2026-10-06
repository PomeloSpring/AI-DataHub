"""Pipeline Orchestrator — Quick mode SQL pipeline + intent classification.

Modes:
- "quick": SQL data queries only (fast path, no Agent routing)

注:"agent" 聊天模式(外部执行层,默认 qoder)由 API 层派发,
不会到达本编排器。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def execute_pipeline(
    question: str,
    history: list[dict] = None,
    datasource_id: int = 0,
    model_id: Optional[int] = None,
    pipeline_mode: str = "quick",
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    retrieval_strategy: str = None,
    workspace_id: int = 0,
    user_role: str = "user",
):
    """Execute query through Quick pipeline.

    Quick mode: SQL data queries only. Non-query intents handled inline.

    Args:
        workspace_id: Workspace context for resource resolution.

    Yields:
        (event_type, data) tuples matching SSE format.
    """
    chosen_mode = pipeline_mode if pipeline_mode == "quick" else "quick"

    logger.info("Pipeline orchestrator: mode=%s, workspace_id=%d", chosen_mode, workspace_id)

    # ── Intent classification (all modes) ──
    # Non-query intents (chat, greeting, explain) are handled directly without RAG/LLM pipeline
    from services.datamind.nl2sql.intent.intent_classifier import _quick_classify
    from services.datamind.nl2sql.prompt.prompt_builder import build_chat_prompt
    from services.shared.common.llm.llm_client import generate_sql

    # 多模态附件只能经执行层处理,不会到达本编排器(入口已 fail-loud 拦截)
    quick = _quick_classify(question)
    intent = quick["intent"] if quick else "query"

    if intent == "chat":
        reply = quick.get("reply", "") if quick else ""
        if not reply:
            messages = build_chat_prompt(question, history)
            llm_result = generate_sql(messages, model_id=model_id)
            reply = llm_result.get("sql", "你好！有什么数据查询需求吗？")
        yield "done", {
            "intent": "chat", "reply": reply, "sql": None,
            "warnings": [], "timings": {"intent": 0.01}, "mode": chosen_mode,
        }
        return

    if intent == "explain":
        prev_sql = ""
        prev_result_summary = ""
        for msg in reversed(history or []):
            if msg.get("role") == "assistant":
                if msg.get("sql") and not prev_sql:
                    prev_sql = msg["sql"]
                if msg.get("result") and not prev_result_summary:
                    r = msg["result"]
                    prev_result_summary = f"{r.get('row_count', 0)}行, {r.get('elapsed_ms', 0)}ms"
        explain_prompt = f"用户想了解上一次查询结果的含义。上一次SQL: {prev_sql}\n结果摘要: {prev_result_summary}\n用户问题: {question}\n请用简洁的中文解释查询结果的含义。"
        messages = [{"role": "system", "content": "你是数据分析助手。根据查询结果解释数据含义。"}, {"role": "user", "content": explain_prompt}]
        llm_result = generate_sql(messages, model_id=model_id)
        yield "done", {
            "intent": "explain", "reply": llm_result.get("sql", "暂无解释"),
            "sql": None, "warnings": [], "timings": {"intent": 0.01}, "mode": chosen_mode,
        }
        return

    # ── Quick mode: SQL queries only ──
    yield "progress", {"stage": "intent", "message": "快速模式: 正在分析...", "mode": "quick"}

    result_event = None
    from services.datamind.nl2sql.orchestrator.quick_pipeline import quick_generate
    for event_type, data in quick_generate(
        question=question,
        history=history,
        datasource_id=datasource_id,
        model_id=model_id,
        user_id=user_id,
        username=username,
        retrieval_strategy=retrieval_strategy,
    ):
        if event_type == "done":
            result_event = data
        else:
            yield event_type, data

    if result_event:
        yield "done", result_event
    else:
        logger.warning("Quick pipeline yielded no done event")
        yield "done", {
            "intent": "query",
            "reply": "快速模式处理异常，请重试。",
            "sql": None,
            "warnings": [],
            "error": "Quick pipeline yielded no done event",
            "mode": "quick",
        }
