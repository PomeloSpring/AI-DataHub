"""Chat API — NL2SQL conversation with keyword-based table selection."""

import json
import logging
import math
import re
import time as _time
from time import perf_counter

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from backend.api.auth import get_current_user
from backend.common.db.metadata_db import get_metadata_conn
from backend.models.schemas import ChatRequest, UserInfo, ConfirmSqlRequest, QueryResult
from backend.nl2sql.intent.intent_classifier import _quick_classify
from backend.rag.table_selector import select_tables, clear_cache
from backend.rag.rag_retriever import retrieve_all, retrieve_with_strategy
from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt, build_correction_prompt, build_chat_prompt
from backend.common.llm.llm_client import (
    generate_sql, generate_sql_stream, generate_with_tools,
    async_generate_sql, async_generate_with_tools,
)
from backend.mcp_client.tools import convert_tools_for_anthropic, parse_tool_name
from backend.mcp_client.registry import get_mcp_registry
from backend.nl2sql.sql.sql_validator import validate_and_fix
from backend.nl2sql.sql.query_executor import execute_query, log_audit, _get_ds_conn_params, _extract_sql_from_text
from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE
from backend.api.dashboard import save_snapshot
from backend.api.model_config import get_system_config

logger = logging.getLogger(__name__)
router = APIRouter()


def _sanitize_for_json(obj):
    """Make object JSON-serializable: handle NaN/inf, Decimal, datetime, bytes, set."""
    from decimal import Decimal
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


# Legacy _get_chatbi_conn() removed — use get_metadata_conn() directly


def _parse_llm_json(raw: str) -> dict:
    """Parse LLM JSON response, handling markdown fences and edge cases."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from text (sometimes LLM wraps in explanation)
    # First try to find a JSON object with "success" field
    match = re.search(r'\{[^{}]*"success"\s*:\s*(true|false)[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object (more permissive)
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
    # If extraction found actual SQL (starts with SELECT/WITH), use it; otherwise use raw text
    if re.match(r'^\s*(SELECT|WITH)\b', extracted_sql, re.IGNORECASE):
        brief = text[:text.lower().find(extracted_sql.lower().split('\n')[0])].strip()
        return {"success": True, "sql": extracted_sql, "tables": [], "chart-type": "table", "brief": brief}
    return {"success": True, "sql": text, "tables": [], "chart-type": "table"}


def _fix_markdown_tables(text: str) -> str:
    """Fix markdown tables broken by LLM streaming newlines within cell content.

    When LLM streams tokens, newlines may be inserted within table cell values,
    breaking the markdown table format. This function merges broken table rows
    back into valid markdown table syntax.
    """
    if not text or '|' not in text:
        return text

    # Find table regions and fix them
    lines = text.split('\n')
    result = []
    table_buffer = []

    for line in lines:
        stripped = line.strip()

        # Check if this line could be part of a table
        is_table_candidate = '|' in stripped or (
            table_buffer and stripped and
            not stripped.startswith('#') and
            not stripped.startswith('**') and
            not stripped.startswith('-') and
            not stripped.startswith('*')
        )

        if is_table_candidate:
            table_buffer.append(stripped)
        else:
            # Process any accumulated table buffer
            if table_buffer:
                fixed = _fix_table_buffer(table_buffer)
                result.extend(fixed)
                table_buffer = []
            result.append(line)

    # Process any remaining table buffer
    if table_buffer:
        fixed = _fix_table_buffer(table_buffer)
        result.extend(fixed)

    return '\n'.join(result)


def _fix_table_buffer(table_lines: list) -> list:
    """Fix a buffer of table lines by removing newlines within cells."""
    if not table_lines:
        return []

    # Join all lines
    all_text = ''.join(table_lines)

    # Split by | to get parts
    parts = all_text.split('|')

    # Remove empty first/last parts
    if parts and parts[0].strip() == '':
        parts = parts[1:]
    if parts and parts[-1].strip() == '':
        parts = parts[:-1]

    if not parts:
        return []

    # Remove empty parts that are artifacts from broken content
    cleaned_parts = []
    for p in parts:
        stripped = p.strip()
        if stripped:
            cleaned_parts.append(p)
    parts = cleaned_parts

    if not parts:
        return []

    # Determine column count
    # Look for separator pattern (---|---|---)
    num_cols = None
    for i, part in enumerate(parts):
        stripped_part = part.strip()
        # Check if this looks like a separator (only dashes, colons, spaces)
        if re.match(r'^[\s\-:]+$', stripped_part) and len(stripped_part) > 2:
            num_cols = i
            break

    if num_cols is None or num_cols == 0:
        # Try to detect from data
        # Look for the most common column count in the data
        # Count how many parts look like data vs separators
        data_parts = [p for p in parts if not re.match(r'^[\s\-:]+$', p.strip())]
        if len(data_parts) > 0:
            # Try different column counts and see which one fits best
            for try_cols in [2, 3, 4, 5]:
                if len(data_parts) % try_cols == 0:
                    num_cols = try_cols
                    break
        if num_cols is None or num_cols == 0:
            num_cols = 3  # Default

    # Reconstruct rows
    rows = []
    for i in range(0, len(parts), num_cols):
        row_parts = parts[i:i + num_cols]
        if len(row_parts) == num_cols:
            cleaned = [p.strip().replace('\n', '') for p in row_parts]
            row = '| ' + ' | '.join(cleaned) + ' |'
            rows.append(row)

    return rows if rows else ['| ' + ' | '.join(p.strip().replace('\n', '') for p in parts) + ' |']


async def _execute_mcp_tool_call(tool_use_id: str, tool_name: str, tool_input: dict) -> dict:
    """Execute an MCP tool call and format as tool_result for LLM.

    Returns:
        Dict formatted as a tool_result message content block.
    """
    try:
        registry = get_mcp_registry()
        result = await registry.call_tool(tool_name, tool_input)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(result) if result else "(无结果)",
        }
    except Exception as e:
        logger.error("MCP tool call failed: %s — %s", tool_name, e)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": f"工具调用失败: {str(e)}",
        }


def _collect_mcp_tools(server_ids: list[int] = None, manual_tool_names: list[str] = None) -> list[dict]:
    """Collect MCP tools from specified servers, formatted for Anthropic.

    Args:
        server_ids: MCP server IDs to load tools from. None = all active.
        manual_tool_names: Additional manual tool names to include.

    Returns:
        List of tool dicts in Anthropic format.
    """
    registry = get_mcp_registry()
    configs = registry.load_configs()

    all_tools = []
    for cfg in configs:
        if not cfg.is_active:
            continue
        if server_ids and cfg.id not in server_ids:
            continue

        tools = cfg.tools_config or []
        anthropic_tools = convert_tools_for_anthropic(tools, cfg.name)
        all_tools.extend(anthropic_tools)

    return all_tools


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
            # Build feedback context
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


# ── Interpretation Loop ──────────────────────────────────────────

def _build_interpretation_messages(
    question: str, sql: str, result: dict, interpretation_prompt: str,
    column_metadata: list[dict] = None, current_round: int = 1, max_rounds: int = 3,
) -> list[dict]:
    """Build messages for result interpretation LLM call."""
    from backend.nl2sql.sql.template_loader import get_sql_prompt

    tpl = get_sql_prompt("Doris", query_limit=False)

    # Build result summary
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    row_count = result.get("row_count", 0)

    # Limit rows sent to LLM to avoid token overflow
    sample_rows = rows[:50]
    result_text = f"查询结果（共{row_count}行）:\n"
    result_text += f"列: {', '.join(columns)}\n"
    result_text += json.dumps(sample_rows, ensure_ascii=False, default=str)

    # Build metadata context for columns used in SQL
    metadata_context = ""
    if column_metadata:
        sql_upper = sql.upper()
        relevant_meta = [
            c for c in column_metadata
            if c["column_name"].upper() in sql_upper or c["table_name"].upper() in sql_upper
        ]
        if relevant_meta:
            metadata_context = "\n相关字段元数据:\n"
            for m in relevant_meta:
                parts = [f"{m['table_name']}.{m['column_name']}"]
                if m.get("column_comment"):
                    parts.append(f"说明: {m['column_comment']}")
                if m.get("business_desc"):
                    parts.append(f"业务描述: {m['business_desc']}")
                metadata_context += " | ".join(parts) + "\n"

    # Last round warning
    last_round_warning = ""
    if current_round >= max_rounds:
        last_round_warning = f"""

<important>
这是第{current_round}轮分析，也是最后一轮。你无法再进行后续分析。
请在这次回复中尽可能给出完整的解读。如果由于信息不足无法完全解答用户的问题，请说明原因。
</important>"""

    system_content = f"""你是一个数据分析助手。你的任务是解读SQL查询结果，回答用户的问题。
{last_round_warning}

规则：
1. 根据用户的问题和SQL结果，给出有意义的解读
2. 如果有字段元数据（如枚举说明），结合元数据解读结果
3. 如果结果是列表/枚举，用清晰的格式展示
4. 如果结果是数值，给出简要总结
5. 返回JSON格式：{{"reply": "你的解读文案", "chart_type": "推荐的图表类型", "needs_interpretation": false, "interpretation_prompt": ""}}
6. 如果解读后发现还需要进一步分析（比如需要更详细的细分），可以设置needs_interpretation=true
7. 如果已经给出了完整答案，设置needs_interpretation=false

Markdown表格格式化规则（重要）：
- 表格的每一行必须在一行内完成，不能换行
- 每个单元格内容不能包含换行符
- 表格格式示例：
  | 列1 | 列2 | 列3 |
  |-----|-----|-----|
  | 数据1 | 数据2 | 数据3 |
  | 数据4 | 数据5 | 数据6 |
- 如果数据较多，确保每行数据都在同一行内，不要分行显示{metadata_context}"""

    user_content = f"""用户问题: {question}

生成的SQL: {sql}

{result_text}

解读指令: {interpretation_prompt}"""

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def _interpret_results_stream(
    question: str, sql: str, result: dict, interpretation_prompt: str,
    column_metadata: list[dict] = None, current_round: int = 1, max_rounds: int = 3,
):
    """Run interpretation loop, yielding SSE events."""
    from backend.common.llm.llm_client import generate_sql_stream

    messages = _build_interpretation_messages(
        question, sql, result, interpretation_prompt,
        column_metadata, current_round, max_rounds,
    )

    full_text = ""
    interp_tokens = {"input": 0, "output": 0, "total": 0}
    for event_type, data in generate_sql_stream(messages):
        if event_type == "token":
            full_text += data
            yield "token", {"text": data}
        elif event_type == "thinking":
            yield "thinking", {"text": data}
        elif event_type == "done":
            # Accumulate tokens from interpretation rounds
            if isinstance(data, dict):
                interp_tokens["input"] += data.get("input", 0)
                interp_tokens["output"] += data.get("output", 0)
                interp_tokens["total"] += data.get("total", 0)

    # Parse interpretation result
    parsed = _parse_llm_json(full_text)
    raw_reply = parsed.get("reply", full_text)
    yield "interpretation_done", {
        "reply": _fix_markdown_tables(raw_reply),
        "chart_type": parsed.get("chart_type"),
        "needs_interpretation": parsed.get("needs_interpretation", False),
        "interpretation_prompt": parsed.get("interpretation_prompt", ""),
        "tokens": interp_tokens,
    }


@router.post("/send")
def chat_send(req: ChatRequest, user: UserInfo = Depends(get_current_user)):
    """Process a user message through the NL2SQL pipeline.

    Pipeline: regex fast-path → keyword table selection → RAG metadata → LLM SQL generation → validation.
    """
    question = req.question
    history = req.history or []
    model_id = req.model_id
    prev_sql, prev_result_summary, feedback_context = _get_prev_context(history)

    # Step 1: Fast-path regex classification (no LLM)
    quick = _quick_classify(question)
    if quick:
        intent = quick["intent"]
    else:
        intent = "query"

    # Handle chat intent (regex fast-path)
    if intent == "chat":
        reply = quick.get("reply", "") if quick else ""
        if not reply:
            messages = build_chat_prompt(question, history)
            llm_result = generate_sql(messages, model_id=model_id)
            reply = llm_result.get("sql", "你好！有什么数据查询需求吗？")
        return {
            "intent": "chat",
            "reply": _fix_markdown_tables(reply),
            "sql": None,
            "warnings": [],
            "is_large": False,
            "large_reason": "",
            "thinking": None,
            "rag": None,
        }

    # Handle explain intent (regex fast-path)
    if intent == "explain":
        explain_prompt = f"用户想了解上一次查询结果的含义。上一次SQL: {prev_sql}\n结果摘要: {prev_result_summary}\n用户问题: {question}\n请用简洁的中文解释查询结果的含义。"
        messages = [{"role": "system", "content": "你是数据分析助手。根据查询结果解释数据含义。"}, {"role": "user", "content": explain_prompt}]
        llm_result = generate_sql(messages, model_id=model_id)
        return {
            "intent": "explain",
            "reply": _fix_markdown_tables(llm_result.get("sql", "暂无解释")),
            "sql": None,
            "warnings": [],
            "is_large": False,
            "large_reason": "",
            "thinking": None,
            "rag": None,
        }

    # Step 2: Keyword-based table selection (no LLM)
    datasource_id = req.datasource_id or 0
    selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)

    # Step 3: RAG retrieval with selected tables
    retrieval_strategy = getattr(req, 'retrieval_strategy', None) or None
    rag_results = retrieve_with_strategy(
        question, selected_tables=selected_tables,
        datasource_id=datasource_id, strategy_name=retrieval_strategy,
        model_id=model_id,
    )
    rag_source = rag_results.get("rag_source", "keyword_selected")
    logger.info(
        "RAG retrieval: source=%s, table_info=%d, column_metadata=%d, selected_tables=%s",
        rag_source,
        len(rag_results["table_info"]),
        len(rag_results["column_metadata"]),
        selected_tables,
    )

    if intent == "correction" and prev_sql:
        messages = build_correction_prompt(
            question=question,
            prev_sql=prev_sql,
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
            feedback_context=feedback_context,
        )

    # Step 4: Generate SQL (LLM returns JSON: {"success":true,"sql":"...","tables":[...],"chart-type":"..."})
    datasets = rag_results.get("saved_datasets", [])
    llm_result = generate_sql(messages, model_id=model_id)
    parsed = _parse_llm_json(llm_result["sql"])

    # Step 5: Handle LLM failure response
    if not parsed.get("success", True):
        return {
            "intent": intent,
            "reply": _fix_markdown_tables(parsed.get("message", "无法生成 SQL")),
            "sql": None,
            "warnings": [],
            "is_large": False,
            "large_reason": "",
            "chart_type": None,
            "thinking": llm_result.get("thinking", ""),
            "rag": {
                "rag_source": rag_source,
                "table_info_count": len(rag_results["table_info"]),
                "column_metadata_count": len(rag_results["column_metadata"]),
                "sql_templates_count": len(rag_results["sql_templates"]),
                "business_terms_count": len(rag_results["business_terms"]),
                "table_relations_count": len(rag_results.get("table_relations", [])),
                "datasets_count": len(datasets),
                "table_info": rag_results["table_info"][:20],
                "column_metadata": rag_results["column_metadata"][:30],
                "sql_templates": rag_results["sql_templates"],
                "business_terms": rag_results["business_terms"][:20],
                "table_relations": rag_results.get("table_relations", [])[:20],
                "saved_datasets": datasets[:5],
            },
        }

    # Step 6: Extract fields from parsed JSON
    sql = parsed.get("sql", "")
    chart_type = parsed.get("chart-type", "table")
    tables_used = parsed.get("tables", [])
    brief = parsed.get("brief", "")
    query_type = parsed.get("query_type", "sql")  # sql | rest | dsl

    # Step 7: Validate and fix (only for SQL queries)
    if query_type == "sql":
        sql, warnings = validate_and_fix(sql)
    else:
        warnings = []

    # Add dataset context as warning if matched
    if datasets:
        ds_names = [d["name"] for d in datasets[:3]]
        warnings.insert(0, f"已关联数据集: {', '.join(ds_names)}")

    # Add tables used as warning
    if tables_used:
        warnings.insert(0, f"涉及表: {', '.join(tables_used)}")

    return {
        "intent": intent,
        "sql": sql,
        "warnings": warnings,
        "query_type": query_type,
        "is_large": False,
        "large_reason": "",
        "chart_type": chart_type,
        "brief": brief,
        "thinking": llm_result.get("thinking", ""),
        "ai_raw_response": llm_result["sql"],
        "tokens": llm_result.get("tokens", {"input": 0, "output": 0, "total": 0}),
        "rag": {
            "rag_source": rag_source,
            "table_info_count": len(rag_results["table_info"]),
            "column_metadata_count": len(rag_results["column_metadata"]),
            "sql_templates_count": len(rag_results["sql_templates"]),
            "business_terms_count": len(rag_results["business_terms"]),
            "table_relations_count": len(rag_results.get("table_relations", [])),
            "datasets_count": len(datasets),
            "table_info": rag_results["table_info"][:20],
            "column_metadata": rag_results["column_metadata"][:30],
            "sql_templates": rag_results["sql_templates"],
            "business_terms": rag_results["business_terms"][:20],
            "table_relations": rag_results.get("table_relations", [])[:20],
            "saved_datasets": datasets[:5],
        },
    }


def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


@router.post("/send/stream")
async def chat_send_stream(req: ChatRequest, request: Request, user: UserInfo = Depends(get_current_user)):
    """Stream NL2SQL pipeline via SSE.

    Pipeline: regex fast-path → keyword table selection → RAG metadata → LLM SQL generation → execute.
    Events: progress, thinking, token, done, error.
    Auto-retries up to 5 rounds on SQL errors with self-correction.
    """
    question = req.question
    history = req.history or []
    model_id = req.model_id
    prev_sql, prev_result_summary, feedback_context = _get_prev_context(history)
    MAX_RETRIES = 5

    async def event_generator():
        t0_total = perf_counter()
        timings = {}
        # These are set in the try block and used in the finally block
        intent = "query"
        sql = ""
        warnings = []
        thinking_text = ""
        full_text = ""
        tokens = {}
        query_result = None
        chart_type = "table"
        tables_used = []
        brief = ""
        rag_results = {}
        rag_source = "unknown"
        datasets = []
        error_message = None
        done_yielded = False  # Track if 'done' event was already yielded

        try:
            logger.debug("[SSE] question=%s, history_len=%d", question, len(history))

            # Step 1: Fast-path regex classification (no LLM)
            t0 = perf_counter()
            quick = _quick_classify(question)
            timings["intent"] = round(perf_counter() - t0, 2)

            if quick:
                intent = quick["intent"]
                logger.debug("[SSE] quick_classify: intent=%s", intent)
            else:
                intent = "query"
                logger.debug("[SSE] quick_classify: no match, defaulting to query")

            # Handle chat intent (regex fast-path)
            if intent == "chat":
                reply = quick.get("reply", "") if quick else ""
                if not reply:
                    messages = build_chat_prompt(question, history)
                    llm_result = await async_generate_sql(messages, model_id=model_id)
                    reply = llm_result.get("sql", "你好！有什么数据查询需求吗？")
                yield _sse_event("done", {
                    "intent": "chat", "reply": _fix_markdown_tables(reply), "sql": None,
                    "warnings": [], "rag": None, "timings": timings,
                })
                done_yielded = True
                return

            # Handle explain intent (regex fast-path)
            if intent == "explain":
                explain_prompt = f"用户想了解上一次查询结果的含义。上一次SQL: {prev_sql}\n结果摘要: {prev_result_summary}\n用户问题: {question}\n请用简洁的中文解释查询结果的含义。"
                messages = [{"role": "system", "content": "你是数据分析助手。根据查询结果解释数据含义。"}, {"role": "user", "content": explain_prompt}]
                llm_result = await async_generate_sql(messages, model_id=model_id)
                yield _sse_event("done", {
                    "intent": "explain", "reply": _fix_markdown_tables(llm_result.get("sql", "暂无解释")),
                    "sql": None, "warnings": [], "rag": None, "timings": timings,
                })
                done_yielded = True
                return

            # Step 2: Keyword-based table selection (no LLM)
            datasource_id = req.datasource_id or 0
            ds_params = _get_ds_conn_params(datasource_id)
            db_type = ds_params.get("db_type", "doris")
            # Map db_type to engine name for prompt
            engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
            engine = engine_map.get(db_type, db_type.capitalize())
            yield _sse_event("progress", {"stage": "rag", "message": "正在检索元数据..."})
            t0 = perf_counter()

            selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)
            logger.debug("[SSE] selected_tables=%s", selected_tables)

            # Step 3: RAG retrieval with selected tables
            retrieval_strategy = getattr(req, 'retrieval_strategy', None) or None
            rag_results = retrieve_with_strategy(
                question, selected_tables=selected_tables,
                datasource_id=datasource_id, strategy_name=retrieval_strategy,
                model_id=model_id,
            )
            rag_source = rag_results.get("rag_source", "keyword_selected")
            timings["rag"] = round(perf_counter() - t0, 2)
            logger.info(
                "RAG retrieval (stream): source=%s, table_info=%d, column_metadata=%d, selected_tables=%s",
                rag_source,
                len(rag_results["table_info"]),
                len(rag_results["column_metadata"]),
                selected_tables,
            )

            # Step 4-6: Generate SQL with auto-retry on error
            error_context = ""
            sql = ""
            chart_type = "table"
            tables_used = []
            brief = ""
            warnings = []
            thinking_text = ""
            full_text = ""
            tokens = {}
            query_result = None
            interp_round = 0
            metadata_loop_round = 0
            metadata_tables_attempted = set()  # Track tables already supplemented
            MAX_METADATA_LOOP_ROUNDS = int(get_system_config("max_interpretation_rounds", "3"))
            datasets = rag_results.get("saved_datasets") or []

            # ── MCP Tool Use Flow ──────────────────────────────────
            # If MCP tools are selected, use the tool_use flow instead of normal SQL generation.
            mcp_tools_param = getattr(req, 'mcp_tools', []) or []
            if mcp_tools_param:
                mcp_tool_list = _collect_mcp_tools()
                mcp_tool_list = [t for t in mcp_tool_list if t["name"] in mcp_tools_param]

                if mcp_tool_list:
                    logger.info("[SSE] MCP tool_use flow: %d tools selected", len(mcp_tool_list))
                    # Build messages for tool use
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

                    yield _sse_event("progress", {"stage": "llm", "message": "正在调用 MCP 工具..."})
                    t_llm = perf_counter()
                    mcp_tool_calls_log = []

                    llm_result = await async_generate_with_tools(messages, mcp_tool_list, model_id=model_id)
                    total_tool_tokens = {"input": 0, "output": 0, "total": 0}
                    if llm_result.get("tokens"):
                        for k in total_tool_tokens:
                            total_tool_tokens[k] = llm_result["tokens"].get(k, 0)

                    thinking_text = llm_result.get("thinking", "")

                    # Handle tool calls (max 5 rounds)
                    max_tool_rounds = 5
                    for round_num in range(max_tool_rounds):
                        if not llm_result.get("tool_uses"):
                            break

                        logger.debug("[SSE] MCP tool round %d: %d tool calls",
                                     round_num + 1, len(llm_result["tool_uses"]))

                        # Emit tool call events
                        for tool_call in llm_result["tool_uses"]:
                            yield _sse_event("tool_call", {
                                "id": tool_call["id"],
                                "name": tool_call["name"],
                                "input": tool_call["input"],
                            })

                        # Execute tools and build tool_result messages
                        tool_results = []
                        for tool_call in llm_result["tool_uses"]:
                            t_tool = perf_counter()
                            result = await _execute_mcp_tool_call(
                                tool_call["id"], tool_call["name"], tool_call["input"]
                            )
                            tool_elapsed = round(perf_counter() - t_tool, 2)
                            tool_results.append(result)
                            result_content = result.get("content", "")
                            mcp_tool_calls_log.append({
                                "step": len(mcp_tool_calls_log) + 1,
                                "tool": tool_call["name"],
                                "arguments": tool_call["input"],
                                "result": str(result_content)[:1000],
                                "result_preview": str(result_content)[:200],
                                "elapsed": tool_elapsed,
                            })
                            yield _sse_event("tool_result", {
                                "id": tool_call["id"],
                                "name": tool_call["name"],
                                "result": result["content"],
                            })

                        # Build assistant message with tool_use blocks
                        assistant_content = []
                        for tool_call in llm_result["tool_uses"]:
                            assistant_content.append({
                                "type": "tool_use",
                                "id": tool_call["id"],
                                "name": tool_call["name"],
                                "input": tool_call["input"],
                            })
                        if llm_result.get("text"):
                            assistant_content.insert(0, {"type": "text", "text": llm_result["text"]})

                        messages.append({"role": "assistant", "content": assistant_content})
                        messages.append({"role": "user", "content": tool_results})

                        # Re-call LLM with tool results
                        yield _sse_event("progress", {"stage": "llm", "message": f"MCP 工具调用完成，继续推理（第{round_num + 2}轮）..."})
                        llm_result = await async_generate_with_tools(messages, mcp_tool_list, model_id=model_id)
                        if llm_result.get("tokens"):
                            for k in total_tool_tokens:
                                total_tool_tokens[k] += llm_result["tokens"].get(k, 0)
                        if llm_result.get("thinking"):
                            thinking_text += llm_result["thinking"]

                    timings["llm"] = round(perf_counter() - t_llm, 2)

                    # After tool loop, emit final text as tokens
                    full_text = llm_result.get("text", "")
                    if full_text:
                        # Stream the final response in chunks for a smooth UX
                        chunk_size = 20
                        for i in range(0, len(full_text), chunk_size):
                            yield _sse_event("token", {"text": full_text[i:i + chunk_size]})

                    # Parse the final text for SQL if present
                    parsed = _parse_llm_json(full_text) if full_text else {}
                    sql = parsed.get("sql", "")
                    chart_type = parsed.get("chart-type", "table")
                    tables_used = parsed.get("tables", [])
                    brief = parsed.get("brief", "")
                    warnings = []
                    if tables_used:
                        warnings.append(f"涉及表: {', '.join(tables_used)}")

                    # Validate and execute SQL if generated
                    if sql:
                        yield _sse_event("progress", {"stage": "validate", "message": "正在校验 SQL..."})
                        t_val = perf_counter()
                        sql, val_warnings = validate_and_fix(sql)
                        warnings.extend(val_warnings)
                        timings["validate"] = round(perf_counter() - t_val, 2)

                        yield _sse_event("progress", {"stage": "execute", "message": "正在执行查询..."})
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
                            try:
                                save_snapshot(
                                    user_id=user.id, question=question, sql_query=sql,
                                    chart_type=chart_type, brief=brief[:100],
                                    columns=columns, rows=rows, row_count=row_count,
                                    datasource_id=datasource_id,
                                )
                            except Exception:
                                pass
                            log_audit(
                                user_id=user.id, username=user.username, role=user.role,
                                question=question, sql=sql, status="success",
                                row_count=row_count, time_ms=elapsed_ms, datasource_id=datasource_id,
                            )
                        except Exception as exec_err:
                            timings["execute"] = round(perf_counter() - t_exec, 2)
                            logger.error("MCP flow SQL execution error: %s", exec_err)
                            query_result = {"error": f"SQL执行失败: {exec_err}"}
                            log_audit(
                                user_id=user.id, username=user.username, role=user.role,
                                question=question, sql=sql, status="error",
                                error=str(exec_err), datasource_id=datasource_id,
                            )

                    tokens = total_tool_tokens
                    timings["total"] = round(sum(v for k, v in timings.items() if k != "total" and isinstance(v, (int, float))), 2)

                    # Yield done event
                    reply_text = parsed.get("message", full_text)
                    done_payload = _sanitize_for_json({
                        "intent": intent, "sql": sql, "warnings": warnings,
                        "chart_type": chart_type, "brief": brief,
                        "reply": _fix_markdown_tables(reply_text),
                        "analysis": reply_text,  # Also set analysis for frontend display
                        "thinking": thinking_text, "ai_raw_response": full_text,
                        "tokens": tokens, "timings": timings,
                        "result": query_result if sql else None,
                        "rag": {
                            "rag_source": rag_source,
                            "table_info_count": len(rag_results.get("table_info") or []),
                            "column_metadata_count": len(rag_results.get("column_metadata") or []),
                            "sql_templates_count": len(rag_results.get("sql_templates") or []),
                            "business_terms_count": len(rag_results.get("business_terms") or []),
                            "table_relations_count": len(rag_results.get("table_relations") or []),
                            "datasets_count": len(datasets),
                        },
                        "mcp_tools_used": [t["name"] for t in mcp_tool_list],
                        "tool_calls": mcp_tool_calls_log,
                    })
                    yield _sse_event("done", done_payload)
                    done_yielded = True
                    return

            # ── Normal SQL Generation Flow ─────────────────────────
            for attempt in range(1, MAX_RETRIES + 1):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("[SSE] Client disconnected, stopping pipeline")
                    return

                logger.debug("[SSE] LLM attempt %d/%d, error_context=%s", attempt, MAX_RETRIES, bool(error_context))
                # Build prompt (with error context for retries)
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

                # Debug: check if template rules are in the prompt
                system_content = messages[0]["content"] if messages else ""
                has_template_rules = "<Template-Rules>" in system_content
                logger.debug("[SSE] Prompt built: has_template_rules=%s, system_len=%d, template_rules=%s",
                            has_template_rules, len(system_content),
                            [t.get("template_name") for t in rag_results.get("sql_templates", []) if t.get("rules")])

                # Append error context for retries (include the failed SQL so LLM can see what went wrong)
                if error_context:
                    messages.append({
                        "role": "user",
                        "content": f"上一次生成的SQL执行失败：\n{error_context}\n\n请分析错误原因并修正SQL。",
                    })

                # Stream LLM generation
                retry_label = f"（第{attempt}次尝试）" if attempt > 1 else ""
                yield _sse_event("progress", {"stage": "llm", "message": f"正在生成 SQL{retry_label}..."})
                t_llm = perf_counter()

                full_text = ""
                thinking_text = ""
                tokens = {}

                token_buf = ""
                token_buf_start = perf_counter()
                TOKEN_FLUSH_INTERVAL = 0.05  # 50ms

                for event_type, data in generate_sql_stream(messages, model_id=model_id):
                    if event_type == "thinking":
                        thinking_text += data
                        yield _sse_event("thinking", {"text": data})
                    elif event_type == "token":
                        full_text += data
                        token_buf += data
                        now = perf_counter()
                        if now - token_buf_start >= TOKEN_FLUSH_INTERVAL:
                            yield _sse_event("token", {"text": token_buf})
                            token_buf = ""
                            token_buf_start = now
                    elif event_type == "done":
                        tokens = data

                # Flush remaining tokens
                if token_buf:
                    yield _sse_event("token", {"text": token_buf})

                timings["llm"] = round(perf_counter() - t_llm, 2)
                logger.debug("[SSE] LLM done: attempt=%d, full_text_len=%d, thinking_len=%d, tokens=%s", attempt, len(full_text), len(thinking_text), tokens)

                # Validate
                yield _sse_event("progress", {"stage": "validate", "message": f"正在校验 SQL{retry_label}..."})
                t_val = perf_counter()

                parsed = _parse_llm_json(full_text)

                # Check if LLM requests more metadata (Loop Engineering)
                needs_more_metadata = parsed.get("needs_more_metadata", False)
                requested_tables = parsed.get("requested_tables", [])
                metadata_reason = parsed.get("metadata_reason", "")

                if needs_more_metadata and requested_tables:
                    # Check if metadata supplementation is enabled
                    enable_metadata_loop = get_system_config("enable_metadata_supplementation", "1") == "1"

                    # Filter out already-attempted tables to avoid infinite loops
                    new_tables = [t for t in requested_tables if t not in metadata_tables_attempted]

                    if enable_metadata_loop and metadata_loop_round <= MAX_METADATA_LOOP_ROUNDS and new_tables:
                        metadata_loop_round += 1
                        metadata_tables_attempted.update(new_tables)
                        logger.info("[SSE] Metadata supplementation round %d/%d, tables=%s, reason=%s",
                                   metadata_loop_round, MAX_METADATA_LOOP_ROUNDS, new_tables, metadata_reason)

                        yield _sse_event("progress", {
                            "stage": "metadata_supplement",
                            "message": f"正在补充表元数据（第{metadata_loop_round}轮）: {', '.join(new_tables)}...",
                        })

                        # Fetch metadata for requested tables (direct lookup, no vector search)
                        from backend.rag.rag_retriever import retrieve_tables_metadata
                        supplement = retrieve_tables_metadata(new_tables, datasource_id)
                        new_table_info = supplement["table_info"]
                        new_column_metadata = supplement["column_metadata"]

                        if new_table_info or new_column_metadata:
                            # Merge with existing RAG results
                            existing_table_names = {t["table_name"] for t in rag_results.get("table_info", [])}
                            for t in new_table_info:
                                if t["table_name"] not in existing_table_names:
                                    rag_results["table_info"].append(t)

                            existing_col_keys = {(c["table_name"], c["column_name"]) for c in rag_results.get("column_metadata", [])}
                            for c in new_column_metadata:
                                if (c["table_name"], c["column_name"]) not in existing_col_keys:
                                    rag_results["column_metadata"].append(c)

                            # Rebuild prompt with updated metadata
                            messages = build_nl2sql_prompt(
                                question=question,
                                table_info=rag_results["table_info"],
                                column_metadata=rag_results["column_metadata"],
                                sql_templates=rag_results["sql_templates"],
                                business_terms=rag_results["business_terms"],
                                table_relations=rag_results.get("table_relations", []),
                                conversation_history=history,
                                engine=engine,
                            )

                            # Add context about the supplementation
                            messages.append({
                                "role": "user",
                                "content": f"已补充以下表的元数据: {', '.join(new_tables)}。\n原因: {metadata_reason}\n请基于补充后的元数据重新生成SQL。",
                            })

                            # Continue to next attempt with updated metadata
                            error_context = ""
                            continue
                        else:
                            logger.warning("[SSE] Metadata supplementation failed: no data found for tables %s", new_tables)
                            warnings.append(f"无法获取表 {', '.join(new_tables)} 的元数据")
                            # Still retry with available metadata
                            error_context = f"无法获取表 {', '.join(new_tables)} 的元数据。请基于已有元数据尝试生成SQL。"
                            continue
                    else:
                        # Metadata loop exhausted or disabled — retry with context hint
                        logger.info("[SSE] Metadata loop exhausted/disabled, retrying with context hint")
                        error_context = f"元数据不足: {metadata_reason}。请基于已有元数据尝试生成SQL，如果确实无法生成请返回 success:false。"
                        continue

                # AI explicitly says it cannot generate SQL (no relevant metadata) — don't retry
                if not parsed.get("success", True):
                    timings["validate"] = round(perf_counter() - t_val, 2)
                    ai_message = _fix_markdown_tables(parsed.get("message", "无法生成 SQL"))
                    log_audit(
                        user_id=user.id, username=user.username, role=user.role,
                        question=question, sql="", status="error",
                        error=ai_message, datasource_id=datasource_id,
                    )
                    yield _sse_event("done", {
                        "intent": intent, "reply": ai_message, "sql": None,
                        "warnings": [], "thinking": thinking_text,
                        "timings": timings,
                        "rag": {
                            "rag_source": rag_source,
                            "table_info_count": len(rag_results["table_info"]),
                            "column_metadata_count": len(rag_results["column_metadata"]),
                            "sql_templates_count": len(rag_results["sql_templates"]),
                            "business_terms_count": len(rag_results["business_terms"]),
                            "table_relations_count": len(rag_results.get("table_relations", [])),
                            "datasets_count": len(datasets),
                            "table_info": rag_results["table_info"][:20],
                            "column_metadata": rag_results["column_metadata"][:30],
                            "sql_templates": rag_results["sql_templates"],
                            "business_terms": rag_results["business_terms"][:20],
                            "table_relations": rag_results.get("table_relations", [])[:20],
                            "saved_datasets": datasets[:5],
                        },
                    })
                    done_yielded = True
                    return

                sql = parsed.get("sql", "")
                chart_type = parsed.get("chart-type", "table")
                tables_used = parsed.get("tables", [])
                brief = parsed.get("brief", "")
                query_type = parsed.get("query_type", "sql")  # sql | rest | dsl

                # Only validate SQL queries (not REST/DSL)
                if query_type == "sql":
                    sql, warnings = validate_and_fix(sql)
                else:
                    warnings = []
                timings["validate"] = round(perf_counter() - t_val, 2)
                logger.debug("[SSE] Validated: query_type=%s, sql=%s, warnings=%s", query_type, sql[:200], warnings)

                if datasets:
                    ds_names = [d["name"] for d in datasets[:3]]
                    warnings.insert(0, f"已关联数据集: {', '.join(ds_names)}")
                if tables_used:
                    warnings.insert(0, f"涉及表: {', '.join(tables_used)}")

                # Execute SQL directly
                yield _sse_event("progress", {"stage": "execute", "message": f"正在执行查询{retry_label}..."})
                t_exec = perf_counter()
                try:
                    df, elapsed_ms, row_count = execute_query(sql, datasource_id, query_type=query_type)
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
                    logger.debug("[SSE] Query success: rows=%d, elapsed=%dms, cols=%s", row_count, elapsed_ms, columns)

                    # Save chart snapshot for dashboard use
                    try:
                        save_snapshot(
                            user_id=user.id, question=question, sql_query=sql,
                            chart_type=chart_type, brief=brief[:100],
                            columns=columns, rows=rows, row_count=row_count,
                            datasource_id=datasource_id,
                        )
                    except Exception:
                        pass

                    log_audit(
                        user_id=user.id, username=user.username, role=user.role,
                        question=question, sql=sql, status="success",
                        row_count=row_count, time_ms=elapsed_ms, datasource_id=datasource_id,
                    )

                    # ── Interpretation Loop ──────────────────────────
                    needs_interp = parsed.get("needs_interpretation", False)
                    interp_prompt = parsed.get("interpretation_prompt", "")
                    interp_round = 0
                    MAX_INTERP_ROUNDS = int(get_system_config("max_interpretation_rounds", "3"))
                    interp_tokens_total = {"input": 0, "output": 0, "total": 0}

                    while needs_interp and interp_round < MAX_INTERP_ROUNDS:
                        interp_round += 1
                        is_last_round = interp_round >= MAX_INTERP_ROUNDS
                        logger.info("[SSE] Interpretation round %d/%d, is_last=%s", interp_round, MAX_INTERP_ROUNDS, is_last_round)

                        yield _sse_event("progress", {
                            "stage": "interpret",
                            "message": f"正在分析结果（第{interp_round}轮）...",
                        })

                        interp_result = None
                        for evt_type, evt_data in _interpret_results_stream(
                            question=question, sql=sql, result=query_result,
                            interpretation_prompt=interp_prompt,
                            column_metadata=rag_results.get("column_metadata", []),
                            current_round=interp_round, max_rounds=MAX_INTERP_ROUNDS,
                        ):
                            if evt_type == "token":
                                yield _sse_event("token", evt_data)
                            elif evt_type == "thinking":
                                yield _sse_event("thinking", evt_data)
                            elif evt_type == "interpretation_done":
                                interp_result = evt_data

                        if interp_result:
                            # Accumulate tokens from interpretation rounds
                            interp_round_tokens = interp_result.get("tokens", {})
                            interp_tokens_total["input"] += interp_round_tokens.get("input", 0)
                            interp_tokens_total["output"] += interp_round_tokens.get("output", 0)
                            interp_tokens_total["total"] += interp_round_tokens.get("total", 0)

                            # Update brief with interpretation
                            interp_reply = interp_result.get("reply", "")
                            if interp_reply:
                                brief = interp_reply[:200]
                                # Store full interpretation for the done event
                                query_result["interpretation"] = interp_reply

                            # Update chart_type if interpretation suggests a different one
                            new_chart_type = interp_result.get("chart_type")
                            if new_chart_type and new_chart_type != chart_type:
                                chart_type = new_chart_type

                            # Check if more interpretation is needed
                            needs_interp = interp_result.get("needs_interpretation", False)
                            interp_prompt = interp_result.get("interpretation_prompt", "")
                        else:
                            break

                    # Merge interpretation tokens into main tokens
                    if interp_round > 0:
                        tokens["input"] = tokens.get("input", 0) + interp_tokens_total["input"]
                        tokens["output"] = tokens.get("output", 0) + interp_tokens_total["output"]
                        tokens["total"] = tokens.get("total", 0) + interp_tokens_total["total"]
                        logger.info("[SSE] Interpretation completed: rounds=%d, tokens=%s", interp_round, interp_tokens_total)

                    break  # Success, exit retry loop
                except Exception as exec_err:
                    timings["execute"] = round(perf_counter() - t_exec, 2)
                    logger.error("SQL execution error (attempt %d/%d): %s", attempt, MAX_RETRIES, exec_err)
                    error_context = f"SQL执行失败: {exec_err}\n执行的SQL: {sql}"
                    log_audit(
                        user_id=user.id, username=user.username, role=user.role,
                        question=question, sql=sql, status="error",
                        error=f"[attempt {attempt}/{MAX_RETRIES}] {exec_err}",
                        datasource_id=datasource_id,
                    )
                    if attempt < MAX_RETRIES:
                        continue  # Retry with error context
                    else:
                        query_result = {"error": f"SQL执行失败（已重试{MAX_RETRIES}次）: {exec_err}"}

            timings["total"] = round(sum(v for k, v in timings.items() if k != "total" and isinstance(v, (int, float))), 2)

        except Exception as e:
            # Catch any exception and store error message for the finally block
            logger.error("SSE stream error: %s", e, exc_info=True)
            error_message = str(e)
            log_audit(
                user_id=user.id, username=user.username, role=user.role,
                question=question, sql="", status="error", error=error_message,
                datasource_id=datasource_id,
            )

        finally:
            # Skip if 'done' event was already yielded (e.g. chat/explain intents)
            if not done_yielded:
                # ALWAYS send a done event, even on error — this guarantees the frontend gets a response
                timings["total"] = round(sum(v for k, v in timings.items() if k != "total" and isinstance(v, (int, float))), 2)
                logger.debug("[SSE] Sending done event: timings=%s, has_error=%s, result_type=%s, sql=%s",
                            timings, bool(error_message), type(query_result).__name__, sql[:100] if sql else "")

                # Filter RAG details to only include tables/templates actually used in SQL
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
                # Filter templates: only show ones whose SQL appears in the generated SQL
                filtered_templates = [
                    t for t in (rag_results.get("sql_templates") or [])
                    if t.get("sql_template") and t["sql_template"][:50].upper() in sql_upper
                ] if sql else (rag_results.get("sql_templates") or [])[:5]
                # Filter terms: only show ones whose target_column appears in SQL
                filtered_terms = [
                    t for t in (rag_results.get("business_terms") or [])
                    if t.get("target_column") and t["target_column"].upper() in sql_upper
                ] if sql else (rag_results.get("business_terms") or [])[:5]
                # Filter relations: only show ones involving used tables
                filtered_relations = [
                    r for r in (rag_results.get("table_relations") or [])
                    if (not used_tables or
                        r["source_table"] in used_tables or r["target_table"] in used_tables)
                ] if used_tables else (rag_results.get("table_relations") or [])[:20]

                # Generate default reply if interpretation is empty
                reply_text = query_result.get("interpretation", "") if query_result else ""
                if not reply_text and query_result:
                    # Generate a simple summary if no interpretation
                    row_count = query_result.get("row_count", 0)
                    if row_count == 0:
                        reply_text = "查询结果为空，没有找到符合条件的数据。"
                    else:
                        columns = query_result.get("columns", [])
                        reply_text = f"查询完成，共返回 {row_count} 行数据。"

                done_payload = _sanitize_for_json({
                    "intent": intent, "sql": sql, "warnings": warnings,
                    "chart_type": chart_type, "brief": brief, "query_type": query_type,
                    "reply": _fix_markdown_tables(reply_text),
                    "analysis": reply_text,  # Also set analysis for frontend display
                    "thinking": thinking_text, "ai_raw_response": full_text,
                    "tokens": tokens, "timings": timings,
                    "interpretation_rounds": interp_round,
                    "result": query_result,
                    "rag": {
                        "rag_source": rag_source,
                        # Original RAG retrieval counts
                        "retrieved_table_info_count": len(rag_results.get("table_info") or []),
                        "retrieved_column_metadata_count": len(rag_results.get("column_metadata") or []),
                        "retrieved_sql_templates_count": len(rag_results.get("sql_templates") or []),
                        "retrieved_business_terms_count": len(rag_results.get("business_terms") or []),
                        "retrieved_table_relations_count": len(rag_results.get("table_relations") or []),
                        # Filtered counts (only tables used in SQL)
                        "table_info_count": len(filtered_table_info),
                        "column_metadata_count": len(filtered_column_metadata),
                        "sql_templates_count": len(filtered_templates),
                        "business_terms_count": len(filtered_terms),
                        "table_relations_count": len(filtered_relations),
                        "datasets_count": len(datasets),
                        # Filtered details
                        "table_info": filtered_table_info,
                        "column_metadata": filtered_column_metadata,
                        "sql_templates": filtered_templates,
                        "business_terms": filtered_terms,
                        "table_relations": filtered_relations,
                        "saved_datasets": datasets[:5],
                    },
                })

                # Inject knowledge-based recommend questions and followup suggestions
                try:
                    from backend.services.knowledge_service import knowledge_service
                    ws_id = req.workspace_id or 0
                    if ws_id:
                        rec_qs = knowledge_service.get_recommend_questions(ws_id, datasource_id, limit=3)
                        if rec_qs:
                            done_payload["recommend_questions"] = [q.get("content", q.get("title", "")) for q in rec_qs]
                        followups = knowledge_service.get_followup_suggestions(ws_id, question, datasource_id, top_k=3)
                        if followups:
                            done_payload["followup_suggestions"] = followups
                except Exception as e:
                    logger.debug("Knowledge context injection skipped: %s", e)

                if error_message:
                    done_payload["error"] = error_message
                    done_payload["reply"] = f"处理出错: {error_message}"

                yield _sse_event("done", done_payload)

                # Record quality review (objective metrics only, no auto-LLM)
                try:
                    from backend.services.quality_service import quality_service
                    total_elapsed = int((perf_counter() - t0_total) * 1000) if 't0_total' in dir() else 0
                    quality_service.create_review(
                        workspace_id=req.workspace_id or 0,
                        user_id=user.id, username=user.username,
                        question=question, sql=sql or "",
                        result=query_result,
                        datasource_id=datasource_id,
                        pipeline_mode="quick",
                        retry_count=attempt if 'attempt' in dir() else 0,
                        elapsed_ms=total_elapsed,
                    )
                except Exception as e:
                    logger.warning("Quality review failed: %s", e)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/feedback")
def chat_feedback(req: dict, user: UserInfo = Depends(get_current_user)):
    """Record user feedback (👍/👎) on query results."""
    question = req.get("question", "")
    tables_used = req.get("tables_used", "")
    datasource_id = req.get("datasource_id", 0)
    satisfied = req.get("satisfied")
    top_tables = req.get("top_tables", "")
    expected_table = req.get("expected_table", "")

    if satisfied is None or not question:
        raise HTTPException(status_code=400, detail="question and satisfied are required")

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            row_id = int(_time.time() * 1000000)
            now = _time.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(tables_used, list):
                tables_used = ",".join(tables_used)
            if isinstance(top_tables, list):
                top_tables = ",".join(top_tables)
            cur.execute(
                "INSERT INTO adh_search_feedback "
                "(id, user_id, question, tables_used, datasource_id, satisfied, top_tables, expected_table, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, user.id, question, tables_used, datasource_id, 1 if satisfied else 0,
                 top_tables, expected_table, now),
            )
        conn.commit()

        # Also update quality review feedback
        try:
            from backend.services.quality_service import quality_service
            quality_service.update_feedback_by_question(user.id, question, satisfied)
        except Exception:
            pass

        return {"success": True}
    finally:
        conn.close()


@router.post("/execute")
def chat_execute(req: ConfirmSqlRequest, user: UserInfo = Depends(get_current_user)):
    """Execute a confirmed SQL query."""
    datasource_id = req.datasource_id or 0
    try:
        df, elapsed_ms, row_count = execute_query(req.sql, datasource_id)
        log_audit(
            user_id=user.id, username=user.username, role=user.role,
            question=req.question, sql=req.sql, status="success",
            row_count=row_count, time_ms=elapsed_ms, datasource_id=datasource_id,
        )
        columns = list(df.columns) if not df.empty else []
        rows = df.to_dict(orient="records") if not df.empty else []
        for row in rows:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")
        rows = _sanitize_for_json(rows)

        # Save chart snapshot for dashboard use
        try:
            save_snapshot(
                user_id=user.id, question=req.question, sql_query=req.sql,
                chart_type=req.chart_type if hasattr(req, 'chart_type') and req.chart_type else "table",
                brief=req.question[:100], columns=columns,
                rows=rows, row_count=row_count, datasource_id=datasource_id,
            )
        except Exception:
            pass  # Snapshot save should never block query result

        return QueryResult(columns=columns, rows=rows, row_count=row_count, elapsed_ms=elapsed_ms).model_dump()
    except Exception as e:
        log_audit(
            user_id=user.id, username=user.username, role=user.role,
            question=req.question, sql=req.sql, status="error", error=str(e),
            datasource_id=datasource_id,
        )
        return {"error": str(e)}


# ── Conversation History Management ──────────────────────────────────

@router.get("/conversations")
def list_conversations(user: UserInfo = Depends(get_current_user), workspace_id: int = 0):
    """List user's conversations, optionally filtered by workspace."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id:
                cur.execute(
                    "SELECT id, title, datasource_id, workspace_id, created_at, updated_at FROM adh_conversations "
                    "WHERE user_id = %s AND workspace_id = %s ORDER BY updated_at DESC LIMIT 50",
                    (user.id, workspace_id),
                )
            else:
                cur.execute(
                    "SELECT id, title, datasource_id, workspace_id, created_at, updated_at FROM adh_conversations "
                    "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
                    (user.id,),
                )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return rows
    finally:
        conn.close()


@router.post("/conversations")
def create_conversation(req: dict = {}, user: UserInfo = Depends(get_current_user)):
    """Create a new conversation."""
    datasource_id = req.get("datasource_id", 0) if req else 0
    workspace_id = req.get("workspace_id", 0) if req else 0
    conv_id = int(_time.time() * 1000)
    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations (id, title, user_id, datasource_id, workspace_id, messages, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (conv_id, "新对话", user.id, datasource_id, workspace_id, "[]", now, now),
            )
        conn.commit()
        return {"id": conv_id, "title": "新对话", "datasource_id": datasource_id, "workspace_id": workspace_id, "created_at": now}
    finally:
        conn.close()


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: int, user: UserInfo = Depends(get_current_user)):
    """Get conversation with messages."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, datasource_id, messages, created_at, updated_at FROM adh_conversations "
                "WHERE id = %s AND user_id = %s",
                (conv_id, user.id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="对话不存在")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            try:
                messages = json.loads(row["messages"]) if row["messages"] else []
            except (json.JSONDecodeError, TypeError):
                logger.warning("Conversation %s has malformed messages JSON, returning empty", conv_id)
                messages = []
            # Fix broken markdown tables in historical data
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = _fix_markdown_tables(msg["content"])
                if msg.get("role") == "assistant" and msg.get("reply"):
                    msg["reply"] = _fix_markdown_tables(msg["reply"])
            row["messages"] = messages
            return row
    finally:
        conn.close()


@router.put("/conversations/{conv_id}")
def update_conversation(conv_id: int, req: dict, user: UserInfo = Depends(get_current_user)):
    """Update conversation (title and/or messages)."""
    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Verify ownership
            cur.execute(
                "SELECT id FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user.id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="对话不存在")

            updates = ["updated_at = %s"]
            params = [now]
            if "title" in req:
                updates.append("title = %s")
                params.append(req["title"])
            if "messages" in req:
                updates.append("messages = %s")
                params.append(json.dumps(req["messages"], ensure_ascii=False))
            params.append(conv_id)
            cur.execute(f"UPDATE adh_conversations SET {', '.join(updates)} WHERE id = %s", params)
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, user: UserInfo = Depends(get_current_user)):
    """Delete a conversation."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user.id),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ── Data Analysis & Prediction ────────────────────────────────────────

@router.post("/analyze")
def analyze_data(req: dict, user: UserInfo = Depends(get_current_user)):
    """Analyze query result data using LLM."""
    from backend.nl2sql.sql.template_loader import get_analysis_prompt
    from backend.common.llm.llm_client import generate_sql as call_llm

    question = req.get("question", "")
    columns = req.get("columns", [])
    rows = req.get("rows", [])

    if not columns or not rows:
        return {"reply": "没有可分析的数据"}

    tpl = get_analysis_prompt()
    fields_text = "\n".join([f"- {c}" for c in columns])
    data_text = json.dumps(rows[:100], ensure_ascii=False, default=str)

    user_content = tpl["user_tpl"].format(fields=fields_text, data=data_text)
    messages = [
        {"role": "system", "content": tpl["system"]},
        {"role": "user", "content": f"用户问题: {question}\n\n{user_content}"},
    ]

    try:
        result = call_llm(messages)
        return {"reply": _fix_markdown_tables(result["sql"]), "tokens": result.get("tokens", {})}
    except Exception as e:
        logger.error("Analysis failed: %s", e)
        return {"reply": f"分析失败: {str(e)}"}


@router.post("/predict")
def predict_data(req: dict, user: UserInfo = Depends(get_current_user)):
    """Predict future trends based on query result data."""
    from backend.nl2sql.sql.template_loader import get_predict_prompt
    from backend.common.llm.llm_client import generate_sql as call_llm

    question = req.get("question", "")
    columns = req.get("columns", [])
    rows = req.get("rows", [])

    if not columns or not rows:
        return {"reply": "没有可预测的数据"}

    tpl = get_predict_prompt()
    fields_text = "\n".join([f"- {c}" for c in columns])
    data_text = json.dumps(rows[:100], ensure_ascii=False, default=str)

    user_content = tpl["user_tpl"].format(fields=fields_text, data=data_text)
    messages = [
        {"role": "system", "content": tpl["system"]},
        {"role": "user", "content": f"用户问题: {question}\n\n{user_content}"},
    ]

    try:
        result = call_llm(messages)
        return {"reply": _fix_markdown_tables(result["sql"]), "tokens": result.get("tokens", {})}
    except Exception as e:
        logger.error("Prediction failed: %s", e)
        return {"reply": f"预测失败: {str(e)}"}


# ── Loop Engineering Endpoint ────────────────────────────────────────

@router.post("/send/loop")
async def chat_send_loop(req: ChatRequest, user: UserInfo = Depends(get_current_user)):
    """Process a user message through the Loop Engineering workflow.

    This endpoint uses the Loop Engine to:
    1. Retrieve initial metadata
    2. Analyze and supplement metadata (configurable loop)
    3. Generate SQL
    4. Execute SQL
    5. Analyze results (optional)

    The workflow respects configurable max rounds and supports metadata supplement loops.
    """
    from backend.nl2sql.orchestrator.workflow.loop_engine import execute_loop

    question = req.question
    history = req.history or []
    datasource_id = req.datasource_id or 0
    model_id = req.model_id
    workflow_id = req.workflow_id

    try:
        result = await execute_loop(
            question=question,
            history=history,
            datasource_id=datasource_id,
            model_id=model_id,
            workflow_id=workflow_id,
            user_id=user.id,
            username=user.username,
        )

        if result.get("success"):
            return {
                "intent": "query",
                "sql": result.get("sql", ""),
                "warnings": [
                    f"工作流: {result.get('workflow', {}).get('name', '默认')}",
                    f"使用轮数: {result.get('workflow', {}).get('rounds_used', 0)}",
                    f"Loop次数: {result.get('workflow', {}).get('loop_count', 0)}",
                ],
                "is_large": False,
                "large_reason": "",
                "chart_type": result.get("chart_type", "table"),
                "brief": "",
                "thinking": None,
                "rag": result.get("metadata_context", {}),
                "result": result.get("result", {}),
                "analysis": result.get("analysis"),
                "workflow_info": result.get("workflow", {}),
                "log_id": result.get("log_id"),
                "elapsed_ms": result.get("elapsed_ms", 0),
            }
        else:
            return {
                "intent": "query",
                "reply": result.get("message", "处理失败"),
                "sql": None,
                "warnings": [],
                "is_large": False,
                "large_reason": "",
                "chart_type": None,
                "thinking": None,
                "rag": None,
                "error": result.get("message"),
                "log_id": result.get("log_id"),
            }

    except Exception as e:
        logger.error("Loop execution failed: %s", e, exc_info=True)
        return {
            "intent": "query",
            "reply": f"处理出错: {str(e)}",
            "sql": None,
            "warnings": [],
            "is_large": False,
            "large_reason": "",
            "chart_type": None,
            "thinking": None,
            "rag": None,
            "error": str(e),
        }


@router.post("/send/loop/stream")
async def chat_send_loop_stream(req: ChatRequest, request: Request, user: UserInfo = Depends(get_current_user)):
    """Stream Loop Engineering workflow via SSE.

    Events: progress, thinking, token, done, error.
    """
    from backend.nl2sql.orchestrator.workflow.loop_engine import execute_loop

    question = req.question
    history = req.history or []
    datasource_id = req.datasource_id or 0
    model_id = req.model_id
    workflow_id = req.workflow_id

    async def event_generator():
        result = None
        try:
            import asyncio

            # Thread-safe queue for progress + streaming callbacks
            event_queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def progress_callback(stage: str, message: str):
                """Called by execute_loop (in thread) to report progress."""
                loop.call_soon_threadsafe(event_queue.put_nowait, ("progress", stage, message))

            def stream_callback(event_type: str, data: str):
                """Called by execute_loop (in thread) to stream tokens/thinking."""
                loop.call_soon_threadsafe(event_queue.put_nowait, ("stream", event_type, data))

            # Sync wrapper for the async execute_loop (it's an async generator)
            def run_execute_loop():
                async def _collect():
                    final = None
                    async for event_type, data in execute_loop(
                        question=question,
                        history=history,
                        datasource_id=datasource_id,
                        model_id=model_id,
                        workflow_id=workflow_id,
                        user_id=user.id,
                        username=user.username,
                        progress_callback=progress_callback,
                        stream_callback=stream_callback,
                    ):
                        if event_type == "done":
                            final = data
                        else:
                            loop.call_soon_threadsafe(event_queue.put_nowait, ("stream", event_type, data))
                    return final

                try:
                    exec_result = asyncio.run(_collect())
                    loop.call_soon_threadsafe(event_queue.put_nowait, ("done", None, exec_result or {"success": False, "message": "无结果"}))
                except Exception as e:
                    logger.error("run_execute_loop crashed: %s", e, exc_info=True)
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        ("done", None, {"success": False, "message": f"执行异常: {e}"}),
                    )

            # Run in thread pool to avoid blocking the event loop
            loop.run_in_executor(None, run_execute_loop)

            # Yield events as they arrive
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("[SSE/Loop] Client disconnected, stopping pipeline")
                    break

                try:
                    kind, event_type, data = await asyncio.wait_for(event_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield _sse_event("error", {"message": "执行超时"})
                    break

                if kind == "done":
                    result = data
                    break
                elif kind == "stream":
                    yield _sse_event(event_type, {"text": data})
                else:
                    yield _sse_event("progress", {"stage": event_type, "message": data})

            if result and result.get("success"):
                # Filter RAG metadata to only include tables used in SQL (same as default mode)
                metadata = result.get("metadata_context", {})
                generated_sql = result.get("sql", "")
                sql_upper = generated_sql.upper() if generated_sql else ""

                # Extract table names from SQL
                used_tables = set()
                if generated_sql:
                    import re
                    # Match table names after FROM/JOIN keywords
                    table_matches = re.findall(
                        r'(?:FROM|JOIN)\s+`?(\w+)`?',
                        sql_upper
                    )
                    used_tables = {t.lower() for t in table_matches}

                filtered_table_info = [
                    t for t in (metadata.get("table_info") or [])
                    if not used_tables or t["table_name"].lower() in used_tables
                ]
                filtered_column_metadata = [
                    c for c in (metadata.get("column_metadata") or [])
                    if not used_tables or c["table_name"].lower() in used_tables
                ]
                filtered_relations = [
                    r for r in (metadata.get("table_relations") or [])
                    if not used_tables or
                       r["source_table"].lower() in used_tables or
                       r["target_table"].lower() in used_tables
                ]
                filtered_terms = [
                    t for t in (metadata.get("business_terms") or [])
                    if t.get("target_table") and t["target_table"].lower() in used_tables
                ] if used_tables else (metadata.get("business_terms") or [])[:5]

                rag_payload = {
                    "rag_source": "loop_engine",
                    "table_info": filtered_table_info,
                    "table_info_count": len(filtered_table_info),
                    "column_metadata": filtered_column_metadata,
                    "column_metadata_count": len(filtered_column_metadata),
                    "table_relations": filtered_relations,
                    "table_relations_count": len(filtered_relations),
                    "business_terms": filtered_terms,
                    "business_terms_count": len(filtered_terms),
                    "sql_templates": metadata.get("sql_templates", []),
                    "sql_templates_count": len(metadata.get("sql_templates", [])),
                }

                # Send done event with full result
                analysis = result.get("analysis") or {}
                # Convert analysis object to string for frontend
                analysis_text = analysis.get("summary", "")
                if not analysis_text and analysis.get("insights"):
                    analysis_text = "\n".join(analysis["insights"])

                done_payload = _sanitize_for_json({
                    "intent": "query",
                    "sql": generated_sql,
                    "warnings": [
                        f"工作流: {result.get('workflow', {}).get('name', '默认')}",
                        f"使用轮数: {result.get('workflow', {}).get('rounds_used', 0)}",
                        f"Loop次数: {result.get('workflow', {}).get('loop_count', 0)}",
                    ],
                    "chart_type": result.get("chart_type", "table"),
                    "brief": "",
                    "reply": _fix_markdown_tables(analysis_text),
                    "thinking": result.get("thinking"),
                    "tokens": result.get("tokens", {}),
                    "timings": {"total": result.get("elapsed_ms", 0) / 1000},
                    "result": result.get("result", {}),
                    "analysis": analysis_text,
                    "rag": rag_payload,
                    "workflow_info": result.get("workflow", {}),
                    "log_id": result.get("log_id"),
                    "tool_calls": result.get("tool_calls", []),
                })
                yield _sse_event("done", done_payload)

                # Record quality review (objective metrics)
                try:
                    from backend.services.quality_service import quality_service
                    quality_service.create_review(
                        workspace_id=req.workspace_id or 0,
                        user_id=user.id, username=user.username,
                        question=question, sql=generated_sql,
                        result=result.get("result", {}),
                        datasource_id=datasource_id,
                        pipeline_mode="deep",
                        retry_count=result.get("workflow", {}).get("loop_count", 0),
                        elapsed_ms=result.get("elapsed_ms", 0),
                    )
                except Exception as e:
                    logger.warning("Quality review failed: %s", e)

            elif result:
                yield _sse_event("error", {"message": result.get("message", "处理失败")})
                yield _sse_event("done", {
                    "intent": "query",
                    "reply": result.get("message", "处理失败"),
                    "error": result.get("message"),
                })
            else:
                # result is None — timeout or client disconnect, ensure done event is always sent
                yield _sse_event("done", {
                    "intent": "query",
                    "reply": "执行超时或连接中断",
                    "sql": None,
                    "warnings": [],
                    "error": "未收到执行结果",
                })

        except Exception as e:
            logger.error("Loop stream error: %s", e, exc_info=True)
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("done", {"intent": "query", "reply": f"处理出错: {str(e)}", "error": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/mcp-tools")
def list_mcp_tools(user: UserInfo = Depends(get_current_user)):
    """List all available MCP tools for the Chat page.

    Returns tools from tools_config (whitelist) if set, otherwise from discovered_tools.
    """
    from backend.mcp_client.registry import get_mcp_registry
    import json

    registry = get_mcp_registry()
    configs = registry.load_configs()

    result = []
    for cfg in configs:
        if not cfg.is_active:
            continue

        # Use whitelist if available, otherwise use filtered tools_config
        tools = cfg.tools_whitelist if cfg.tools_whitelist else cfg.tools_config

        result.append({
            "server_id": cfg.id,
            "server_name": cfg.name,
            "description": cfg.description,
            "transport": cfg.transport,
            "tools": tools,
        })

    return {"servers": result}
