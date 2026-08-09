"""Quick Pipeline — Equivalent to original /api/chat/send/stream.

Pipeline: Intent → RAG Metadata → LLM SQL Gen → Validate → Execute
Same as the default pipeline but without metadata supplement loop or result analysis.
"""

import json
import logging
import math
import re
from time import perf_counter
from typing import Generator, Optional

from services.datamind.nl2sql.intent.intent_classifier import _quick_classify
from services.shared.common.llm.llm_client import generate_sql, generate_sql_stream
from services.datamind.nl2sql.prompt.prompt_builder import build_nl2sql_prompt, build_correction_prompt, build_chat_prompt
from services.datamind.nl2sql.sql.sql_validator import validate_and_fix
from services.datamind.nl2sql.sql.semantic_validator import validate_semantic
from services.datamind.nl2sql.sql.feasibility_checker import assess_feasibility
from services.datamind.nl2sql.sql.multi_step_planner import plan_query
from services.datamind.nl2sql.sql.sensitive_detector import check_sensitive_keywords
from services.datamind.nl2sql.sql.query_executor import execute_query, log_audit, _get_ds_conn_params, _extract_sql_from_text
from services.datamind.rag.table_selector import select_tables
from services.datamind.rag.rag_retriever import retrieve_with_strategy
from services.datamind.nl2sql.intent.query_rewriter import rewrite_query

logger = logging.getLogger(__name__)


def _sanitize_for_json(obj):
    """Make object JSON-serializable: handle NaN/inf, Decimal, datetime, bytes."""
    from decimal import Decimal
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def _parse_llm_json(raw: str) -> dict:
    """Parse LLM JSON response, handling markdown fences and edge cases."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[^{}]*"success"\s*:\s*(true|false)[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    # Fallback: extract clean SQL from text, put remainder in brief
    extracted_sql = _extract_sql_from_text(text)
    # If extraction found actual SQL (starts with SELECT/WITH), use it; otherwise fail
    if extracted_sql and re.match(r'^\s*(SELECT|WITH)\b', extracted_sql, re.IGNORECASE):
        brief = text[:text.lower().find(extracted_sql.lower().split('\n')[0])].strip()
        return {"success": True, "sql": extracted_sql, "tables": [], "chart-type": "table", "brief": brief}
    # No valid SQL found in LLM response — return error so caller can retry
    return {"success": False, "error": "LLM 未返回有效的 SQL 语句", "sql": "", "tables": [], "chart-type": "table"}


def _get_prev_context(history: list[dict]) -> tuple[str, str, str]:
    """Extract previous SQL, result summary, and feedback from history."""
    prev_sql = ""
    prev_result_summary = ""
    feedback_context = ""
    for msg in reversed(history or []):
        if msg.get("role") == "assistant":
            if msg.get("sql") and not prev_sql:
                prev_sql = msg["sql"]
            if msg.get("result") and not prev_result_summary:
                r = msg["result"]
                prev_result_summary = f"{r.get('row_count', 0)}行, {r.get('elapsed_ms', 0)}ms"
            fb = msg.get("feedback")
            if fb and not feedback_context:
                if fb == "up":
                    feedback_context = "用户对上次查询结果满意。"
                elif fb == "down":
                    expected = msg.get("expected_table", "")
                    if expected:
                        feedback_context = (
                            f"用户对上次查询结果不满意。用户期望使用的表是: {expected}。"
                            f"请重新审视问题，如果 {expected} 在 <m-schema> 中不存在，请先查询该表的元数据结构再生成 SQL。"
                            f"不要基于上次的 SQL 修改，应该重新分析并生成新的 SQL。"
                        )
                    else:
                        feedback_context = (
                            "用户对上次查询结果不满意。"
                            "请重新审视问题，不要基于上次的 SQL 修改，应该重新分析并生成新的 SQL。"
                        )
            if prev_sql and prev_result_summary and feedback_context:
                break
    return prev_sql, prev_result_summary, feedback_context


def quick_generate(
    question: str,
    history: list[dict] = None,
    datasource_id: int = 0,
    model_id: Optional[int] = None,
    engine: str = "Doris",
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    retrieval_strategy: str = None,
) -> Generator[tuple[str, dict], None, None]:
    """Quick pipeline: same as original /api/chat/send/stream.

    Steps: Intent → RAG Metadata → LLM SQL Gen → Validate → Execute
    No metadata supplement loop, no result analysis.

    Yields:
        (event_type, data) tuples matching SSE format.
    """
    t_start = perf_counter()
    timings = {}
    history = history or []
    prev_sql, prev_result_summary, feedback_context = _get_prev_context(history)

    # Step 1: Intent classification (fast regex only)
    t0 = perf_counter()
    quick = _quick_classify(question)
    timings["intent"] = round(perf_counter() - t0, 2)

    if quick:
        intent = quick["intent"]
    else:
        intent = "query"

    # ── Guard: quick mode only supports data queries ──
    # Detect non-query intents that require deep mode (log analysis, MCP, agent, etc.)
    _DEEP_ONLY_PATTERNS = [
        (r'(日志|log|LOG).*(分析|查询|搜索|排查|错误|异常|告警)', "日志分析"),
        (r'(分析|查询|搜索|排查).*(日志|log|LOG)', "日志分析"),
        (r'Index:\s*\S+.*_id:\s*\S+', "日志文档查询"),
        (r'(系统日志|应用日志|错误日志|访问日志)', "日志分析"),
        (r'(告警|alarm|alert).*(分析|排查|查询|趋势)', "告警分析"),
        (r'(链路|trace|调用链).*(分析|排查|查询)', "链路追踪"),
    ]
    import re
    q_lower = question.lower()
    for pattern, feature_name in _DEEP_ONLY_PATTERNS:
        if re.search(pattern, q_lower):
            yield "done", _sanitize_for_json({
                "intent": intent,
                "reply": f"快速模式不支持{feature_name}功能，请切换到Agent模式后重试。",
                "sql": None,
                "warnings": [f"该功能需要深度模式支持"],
                "rag": None,
                "timings": timings,
                "mode": "quick",
            })
            return

    # Handle chat intent
    if intent == "chat":
        reply = quick.get("reply", "") if quick else ""
        if not reply:
            messages = build_chat_prompt(question, history)
            llm_result = generate_sql(messages, model_id=model_id)
            reply = llm_result.get("sql", "你好！有什么数据查询需求吗？")
        yield "done", _sanitize_for_json({
            "intent": "chat", "reply": reply, "sql": None,
            "warnings": [], "rag": None, "timings": timings, "mode": "quick",
        })
        return

    # Handle explain intent
    if intent == "explain":
        explain_prompt = f"用户想了解上一次查询结果的含义。上一次SQL: {prev_sql}\n结果摘要: {prev_result_summary}\n用户问题: {question}\n请用简洁的中文解释查询结果的含义。"
        messages = [{"role": "system", "content": "你是数据分析助手。根据查询结果解释数据含义。"}, {"role": "user", "content": explain_prompt}]
        llm_result = generate_sql(messages, model_id=model_id)
        yield "done", _sanitize_for_json({
            "intent": "explain", "reply": llm_result.get("sql", "暂无解释"),
            "sql": None, "warnings": [], "rag": None, "timings": timings, "mode": "quick",
        })
        return

    # Step 2: RAG metadata retrieval (SAME as original pipeline)
    yield "progress", {"stage": "rag", "message": "正在检索元数据...", "mode": "quick"}
    t_rag = perf_counter()

    ds_params = _get_ds_conn_params(datasource_id)
    db_type = ds_params.get("db_type", "doris")
    engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
    engine = engine_map.get(db_type, db_type.capitalize())

    # Keyword-based table pre-selection
    # Query rewriting: pronoun resolution, time normalization, expansion
    rewrite_result = rewrite_query(question, history=history)
    canonical_query = rewrite_result["canonical_query"]
    if canonical_query != question:
        logger.info("[Quick] Query rewritten: '%s' → '%s'", question[:50], canonical_query[:50])
        yield "progress", {
            "stage": "rewrite",
            "message": f"查询已改写: {canonical_query[:60]}",
            "mode": "quick",
        }
    # Use canonical query for RAG retrieval
    rag_query = canonical_query

    selected_tables = select_tables(rag_query, top_k=5, datasource_id=datasource_id)
    logger.info("[Quick] selected_tables=%s", selected_tables)

    # RAG retrieval with selected tables
    rag_results = retrieve_with_strategy(
        rag_query, selected_tables=selected_tables,
        datasource_id=datasource_id, strategy_name=retrieval_strategy,
        model_id=model_id,
    )
    rag_source = rag_results.get("rag_source", "keyword_selected")
    timings["rag"] = round(perf_counter() - t_rag, 2)

    logger.info(
        "[Quick] RAG: source=%s, table_info=%d, column_metadata=%d, selected_tables=%s",
        rag_source,
        len(rag_results["table_info"]),
        len(rag_results["column_metadata"]),
        selected_tables,
    )

    # Step 2.4: Sensitive keyword check
    sensitive_warning = check_sensitive_keywords(question)
    if sensitive_warning:
        yield "progress", {"stage": "security", "message": sensitive_warning, "mode": "quick"}
        yield "token", {"text": sensitive_warning}
        yield "done", _sanitize_for_json({
            "success": False,
            "error": sensitive_warning,
            "mode": "quick",
            "elapsed": round(perf_counter() - t_start, 2),
        })
        return

    # Step 2.5: Feasibility check
    feasibility = assess_feasibility(question, rag_results["table_info"], rag_results["column_metadata"])
    if not feasibility.feasible:
        yield "progress", {"stage": "feasibility", "message": feasibility.reason, "mode": "quick"}
        yield "token", {"text": f"无法回答此问题：{feasibility.reason}"}
        if feasibility.suggestions:
            yield "token", {"text": "\n建议：" + "；".join(feasibility.suggestions)}
        yield "done", _sanitize_for_json({
            "success": False,
            "error": feasibility.reason,
            "mode": "quick",
            "elapsed": round(perf_counter() - t_start, 2),
        })
        return

    # Step 2.6: Multi-step planning
    multi_step_plan = plan_query(question, rag_results["table_info"], rag_results["column_metadata"], model_id)
    if multi_step_plan:
        yield "progress", {
            "stage": "plan",
            "message": f"检测到复杂查询，分解为 {len(multi_step_plan['plan'])} 步执行",
            "mode": "quick",
        }
        logger.info("[Quick] Multi-step plan: %s", multi_step_plan.get("reason"))

    # Step 3: LLM SQL generation with RAG context
    yield "progress", {"stage": "llm", "message": "正在生成 SQL...", "mode": "quick", "elapsed": timings["rag"]}
    t_llm = perf_counter()

    if intent == "correction" and prev_sql:
        messages = build_correction_prompt(
            question=question, prev_sql=prev_sql,
            table_info=rag_results["table_info"],
            column_metadata=rag_results["column_metadata"],
            business_terms=rag_results["business_terms"],
            table_relations=rag_results.get("table_relations", []),
        )
    else:
        messages = build_nl2sql_prompt(
            question=question,
            table_info=rag_results["table_info"],
            column_metadata=rag_results["column_metadata"],
            sql_templates=rag_results["sql_templates"],
            business_terms=rag_results["business_terms"],
            table_relations=rag_results.get("table_relations", []),
            conversation_history=history,
            engine=engine,
            feedback_context=feedback_context,
        )

    # Collect LLM response (no SSE streaming for Quick mode)
    full_text = ""
    thinking_text = ""
    tokens = {}

    for event_type, data in generate_sql_stream(messages, model_id=model_id):
        if event_type == "thinking":
            thinking_text += data
        elif event_type == "token":
            full_text += data
        elif event_type == "done":
            tokens = data

    timings["llm"] = round(perf_counter() - t_llm, 2)

    # Step 4: Validate
    yield "progress", {"stage": "validate", "message": "正在校验 SQL...", "mode": "quick", "elapsed": timings["llm"]}
    t_val = perf_counter()

    parsed = _parse_llm_json(full_text)

    # If LLM says it cannot generate SQL
    if not parsed.get("success", True):
        timings["validate"] = round(perf_counter() - t_val, 2)
        ai_message = parsed.get("message", "无法生成 SQL")
        yield "done", _sanitize_for_json({
            "intent": intent,
            "reply": ai_message,
            "sql": None,
            "warnings": [],
            "thinking": thinking_text,
            "timings": timings,
            "mode": "quick",
            "rag": {
                "rag_source": rag_source,
                "table_info_count": len(rag_results["table_info"]),
                "column_metadata_count": len(rag_results["column_metadata"]),
            },
        })
        return

    sql = parsed.get("sql", "")
    chart_type = parsed.get("chart-type", "table")
    tables_used = parsed.get("tables", [])
    brief = parsed.get("brief", "")

    sql, warnings = validate_and_fix(sql)

    # Semantic validation (hallucination, logic, completeness)
    if sql:
        semantic_result = validate_semantic(
            sql, question,
            table_info=rag_results.get("table_info", []),
            column_metadata=rag_results.get("column_metadata", []),
            time_range=rewrite_result.get("time_range"),
        )
        if semantic_result.issues:
            for issue in semantic_result.issues:
                prefix = "⚠️" if issue["severity"] == "warning" else "ℹ️"
                warnings.append(f"{prefix} {issue['message']}")
            if not semantic_result.is_valid:
                logger.warning("[Quick] Semantic validation failed: %s", [i["message"] for i in semantic_result.issues])

    timings["validate"] = round(perf_counter() - t_val, 2)

    datasets = rag_results.get("saved_datasets") or []
    if datasets:
        ds_names = [d["name"] for d in datasets[:3]]
        warnings.insert(0, f"已关联数据集: {', '.join(ds_names)}")
    if tables_used:
        warnings.insert(0, f"涉及表: {', '.join(tables_used)}")

    # Step 5: Execute
    yield "progress", {"stage": "execute", "message": "正在执行查询...", "mode": "quick", "elapsed": timings["validate"]}
    t_exec = perf_counter()

    try:
        df, elapsed_ms, row_count = execute_query(sql, datasource_id)
        timings["execute"] = round(perf_counter() - t_exec, 2)

        columns = list(df.columns) if not df.empty else []
        rows = df.to_dict(orient="records") if not df.empty else []
        for row in rows:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")
        rows = _sanitize_for_json(rows)

        query_result = {
            "columns": columns, "rows": rows,
            "row_count": row_count, "elapsed_ms": elapsed_ms,
        }

        log_audit(
            user_id=user_id, username=username, role="user",
            question=question, sql=sql, status="success",
            row_count=row_count, time_ms=elapsed_ms, datasource_id=datasource_id,
        )

        # Filter RAG details to only include tables used in SQL
        used_tables = set(tables_used) if tables_used else set()
        sql_upper = sql.upper() if sql else ""
        filtered_table_info = [
            t for t in (rag_results.get("table_info") or [])
            if not used_tables or t["table_name"] in used_tables
        ]
        filtered_column_metadata = [
            c for c in (rag_results.get("column_metadata") or [])
            if not used_tables or c["table_name"] in used_tables
        ]

        timings["total"] = round(sum(v for k, v in timings.items() if k != "total" and isinstance(v, (int, float))), 2)

        yield "done", _sanitize_for_json({
            "intent": intent, "sql": sql, "warnings": warnings,
            "chart_type": chart_type, "brief": brief,
            "thinking": thinking_text, "ai_raw_response": full_text,
            "tokens": tokens, "timings": timings,
            "result": query_result,
            "rag": {
                "rag_source": rag_source,
                "table_info": filtered_table_info,
                "table_info_count": len(filtered_table_info),
                "column_metadata": filtered_column_metadata,
                "column_metadata_count": len(filtered_column_metadata),
                "sql_templates": rag_results.get("sql_templates", []),
                "sql_templates_count": len(rag_results.get("sql_templates", [])),
                "business_terms": rag_results.get("business_terms", []),
                "business_terms_count": len(rag_results.get("business_terms", [])),
                "table_relations": rag_results.get("table_relations", []),
                "table_relations_count": len(rag_results.get("table_relations", [])),
                "datasets_count": len(datasets),
            },
            "mode": "quick",
        })

    except Exception as exec_err:
        timings["execute"] = round(perf_counter() - t_exec, 2)
        timings["total"] = round(sum(v for k, v in timings.items() if k != "total" and isinstance(v, (int, float))), 2)
        logger.warning("Quick pipeline SQL execution failed: %s", exec_err)

        log_audit(
            user_id=user_id, username=username, role="user",
            question=question, sql=sql, status="error",
            error=str(exec_err), datasource_id=datasource_id,
        )

        yield "done", _sanitize_for_json({
            "intent": intent, "sql": sql, "warnings": warnings,
            "chart_type": chart_type, "brief": brief,
            "thinking": thinking_text, "ai_raw_response": full_text,
            "tokens": tokens, "timings": timings,
            "result": {"error": str(exec_err)},
            "rag": {
                "rag_source": rag_source,
                "table_info": rag_results.get("table_info", [])[:20],
                "table_info_count": len(rag_results.get("table_info", [])),
                "column_metadata": rag_results.get("column_metadata", [])[:30],
                "column_metadata_count": len(rag_results.get("column_metadata", [])),
            },
            "mode": "quick",
            "error": f"快速模式执行失败: {exec_err}",
        })
