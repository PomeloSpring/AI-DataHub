"""Pipeline Orchestrator — Routes queries between Quick and Deep modes.

Modes:
- "quick": SQL data queries only (fast path, no Agent routing)
- "deep":  Full capability — Agent routing, MCP tools, log analysis, etc.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Agent registration (lazy, on first deep request) ──────────────

_agents_initialized = False


def _init_agents():
    """Register agents: SQL Agent (built-in) + all DB agents (ConfigurableAgent).

    Called once on first deep mode request.
    - data_analysis_agent: built-in, uses NL2SQL pipeline + analysis skills
    - All other agents: loaded from adh_agents table as ConfigurableAgent
    """
    global _agents_initialized
    if _agents_initialized:
        return

    from backend.agent.router import register_agent
    from backend.agent.data_analysis_agent import create_data_analysis_agent

    # Register Data Analysis Agent (built-in, always active)
    register_agent(create_data_analysis_agent())

    # Agents migrated to skills — skip if still in DB (stale records)
    _MIGRATED_TO_SKILLS = {"traffic", "user_profiling", "funnel", "retention", "anomaly", "trend"}

    # Load custom agents from DB
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        from backend.agent.configurable_agent import create_configurable_agent

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, display_name, description, agent_type, system_prompt, "
                    "mcp_server_ids, datasource_ids, tools, config, is_active "
                    "FROM adh_agents"
                )
                rows = cur.fetchall()

                for row in rows:
                    agent_name = row["name"]
                    # Skip migrated-to-skills agents
                    if agent_name in _MIGRATED_TO_SKILLS:
                        logger.info("[Orchestrator] Skipping migrated agent: %s (now a skill)", agent_name)
                        continue
                    # Skip if same name as built-in agent
                    if agent_name == "sql_agent":
                        # Sync is_active from DB for sql_agent
                        from backend.agent.router import _agents
                        if agent_name in _agents:
                            _agents[agent_name].is_active = bool(row["is_active"])
                            logger.info("[Orchestrator] sql_agent is_active=%s (from DB)", row["is_active"])
                        continue

                    agent = create_configurable_agent(row)
                    register_agent(agent)
                    logger.info("[Orchestrator] Loaded DB agent: %s (active=%s)", agent_name, agent.is_active)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[Orchestrator] Failed to load agents from DB: %s", e)

    _agents_initialized = True
    logger.info("[Orchestrator] Agents initialized")


async def execute_pipeline(
    question: str,
    history: list[dict] = None,
    datasource_id: int = 0,
    model_id: Optional[int] = None,
    pipeline_mode: str = "quick",
    workflow_id: Optional[int] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    retrieval_strategy: str = None,
    workspace_id: int = 0,
    system_tools: list[str] = None,
):
    """Execute query through Quick-Deep pipeline.

    Quick mode: SQL data queries only. Non-query intents rejected.
    Deep mode: Full Agent routing — SQL, log analysis, MCP tools, etc.

    Args:
        workspace_id: For Agent mode, use workspace's configured resources.

    Yields:
        (event_type, data) tuples matching SSE format.
    """
    chosen_mode = pipeline_mode if pipeline_mode in ("quick", "deep", "agent") else "quick"
    logger.info("Pipeline orchestrator: mode=%s, workspace_id=%d", chosen_mode, workspace_id)

    # ── Intent classification (all modes) ──
    # Non-query intents (chat, greeting, explain) are handled directly without RAG/LLM pipeline
    from backend.nl2sql.intent.intent_classifier import _quick_classify
    from backend.nl2sql.prompt.prompt_builder import build_chat_prompt
    from backend.common.llm.llm_client import generate_sql

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

    if chosen_mode == "quick":
        # ── Quick mode: SQL queries only ──
        yield "progress", {"stage": "intent", "message": "快速模式: 正在分析...", "mode": "quick"}

        result_event = None
        from backend.nl2sql.orchestrator.quick_pipeline import quick_generate
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
                "reply": "快速模式处理异常，请重试或切换到深度模式。",
                "sql": None,
                "warnings": [],
                "error": "Quick pipeline yielded no done event",
                "mode": "quick",
            }

    elif chosen_mode == "deep":
        # ── Deep mode: Full RAG + Loop Engineering (no agent/MCP routing) ──
        yield "progress", {"stage": "rag", "message": "深度模式: 正在检索元数据...", "mode": "deep"}

        from backend.nl2sql.orchestrator.deep_pipeline import deep_generate

        async for event_type, data in deep_generate(
            question=question,
            history=history,
            datasource_id=datasource_id,
            model_id=model_id,
            workflow_id=workflow_id,
            user_id=user_id,
            username=username,
            retrieval_strategy=retrieval_strategy,
        ):
            yield event_type, data

    else:
        # ── Agent mode: LLM autonomous tool calling (system + MCP + agents) ──
        _init_agents()

        yield "progress", {"stage": "agent_plan", "message": "Agent 模式: 正在规划...", "mode": "agent"}

        done_yielded = False
        try:
            from backend.nl2sql.orchestrator.agent_pipeline import agent_generate

            async for event_type, data in agent_generate(
                question=question,
                history=history,
                datasource_id=datasource_id,
                model_id=model_id,
                user_id=user_id,
                username=username,
                retrieval_strategy=retrieval_strategy,
                workspace_id=workspace_id,
                system_tools=system_tools,
            ):
                if event_type == "done":
                    done_yielded = True
                yield event_type, data
        except Exception as e:
            logger.error("[Orchestrator] Agent mode failed: %s", e, exc_info=True)
            if not done_yielded:
                yield "done", {
                    "intent": "query",
                    "reply": f"Agent 模式执行出错: {str(e)}",
                    "sql": None,
                    "warnings": [],
                    "error": str(e),
                    "mode": "agent",
                }
                done_yielded = True

        # Safety net: guarantee a done event even if generator exits without one
        if not done_yielded:
            logger.warning("[Orchestrator] agent_generate exited without yielding done event")
            yield "done", {
                "intent": "query",
                "reply": "Agent 模式未完成，请重试或切换到其他模式。",
                "sql": None,
                "warnings": ["Agent 未返回最终结果"],
                "error": "Agent 未返回完成事件",
                "mode": "agent",
            }
