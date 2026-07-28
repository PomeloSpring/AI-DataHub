"""Agent Pipeline — tool-augmented LLM agent with autonomous tool calling.

The agent has access to:
- System tools: table search, metadata retrieval, SQL generation/execution/validation
- Context tools: list_tables, search_columns, get_sample_data, think
- Interaction tools: ask_user (pause loop and ask user for clarification)
- MCP tools: external services configured in adh_mcp_servers
- Other agents: log_analysis, custom agents from DB

Design philosophy follows Claude Code methodology:
1. Plan & gather context before acting (think → select_tables → retrieve_metadata)
2. Never guess — use tools to verify (search_columns, get_sample_data)
3. Self-correct on errors (explain_error → retry)
4. Ask user when uncertain (ask_user)
5. Verify before executing — execute_sql checks that all tables/columns are metadata-confirmed
"""

import asyncio
import json
import logging
import math
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from backend.agent.base import AgentResult

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────

MAX_ITERATIONS = 20           # Max tool-use rounds per agent run
MAX_CONTEXT_TOKENS = 180_000  # Estimated token budget before auto-compact
COMPACT_KEEP_RECENT = 6       # Number of recent messages to keep verbatim

# ── Doom Loop Detection ──────────────────────────────────────────────
# Track recent tool call signatures to detect repetitive patterns.
# If the same (tool_name, input_hash) appears DOOM_LOOP_THRESHOLD times
# consecutively, force terminate the agent loop.
DOOM_LOOP_WINDOW = 6          # Number of recent tool calls to track
DOOM_LOOP_THRESHOLD = 3       # Consecutive identical calls to trigger
TOOL_RESULT_MAX_CHARS = {
    "retrieve_metadata": None,    # No truncation (critical info)
    "execute_sql": 8000,          # ~50 rows
    "list_tables": 4000,          # ~20 entries
    "get_sample_data": None,      # No truncation (5 rows)
    "search_columns": 6000,       # ~30 entries
    "generate_sql": None,         # No truncation
    "think": 800,                 # Compact thinking
    "search_business_terms": 4000,
    "search_relations": 4000,
    "explain_error": None,
    "ask_user": None,
    "get_sql_rules": None,
    "validate_sql": None,
}
DEFAULT_TOOL_RESULT_MAX = 6000

# ── Ask-User interaction mechanism ──────────────────────────────────
# When the agent calls ask_user, the loop pauses and waits for user response.
# The /api/pipeline/ask/respond endpoint resolves the future to resume the loop.

_pending_asks: dict[str, asyncio.Future] = {}
_CANCEL = "__CANCEL__"


class AgentCancelledError(Exception):
    """Raised when user cancels the agent loop via ask_user."""
    pass


async def _wait_for_user_response(request_id: str, timeout: float = 300) -> str:
    """Wait for user response. Called inside agent_generate when ask_user is invoked.

    Returns the user's response string.

    Raises:
        AgentCancelledError: If the user sends __CANCEL__ to cancel the agent loop.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    _pending_asks[request_id] = future
    try:
        response = await asyncio.wait_for(future, timeout=timeout)
        if response == _CANCEL:
            raise AgentCancelledError("用户取消了任务")
        return response
    except asyncio.TimeoutError:
        logger.warning("[Agent] ask_user timeout for request_id=%s", request_id)
        return "用户未回复，请基于已有信息继续处理。"
    finally:
        _pending_asks.pop(request_id, None)


def submit_user_response(request_id: str, response: str):
    """Called by the API endpoint when user submits a response to an ask_user question."""
    future = _pending_asks.get(request_id)
    if future and not future.done():
        future.set_result(response)
        logger.info("[Agent] ask_user response received: request_id=%s", request_id)
    else:
        logger.warning("[Agent] ask_user: no pending future for request_id=%s", request_id)


def _sanitize_row(row: dict) -> dict:
    """Sanitize a single row for JSON serialization."""
    for k, v in row.items():
        if v is None:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            row[k] = None
        elif isinstance(v, Decimal):
            row[k] = float(v)
        elif hasattr(v, "isoformat"):
            row[k] = v.isoformat()
        elif isinstance(v, bytes):
            row[k] = v.decode("utf-8", errors="replace")
        elif isinstance(v, (set, frozenset)):
            row[k] = list(v)
    return row


# ── Progress Display Helpers ──────────────────────────────────────

def _brief_args(tool_input: dict) -> str:
    """Format tool arguments for progress display (one-line summary)."""
    if not tool_input:
        return ""
    parts = []
    for k, v in tool_input.items():
        if isinstance(v, str):
            s = v[:60] + "…" if len(v) > 60 else v
            parts.append(f"{k}={s}")
        elif isinstance(v, list):
            if len(v) <= 3:
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}=[{v[0]}, …{len(v)}项]")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def _brief_result(tool_name: str, tool_result: str) -> str:
    """Format tool result for progress display (one-line summary)."""
    try:
        data = json.loads(tool_result)
    except (json.JSONDecodeError, TypeError):
        s = str(tool_result)
        return s[:80] + "…" if len(s) > 80 else s

    if isinstance(data, dict):
        if data.get("error"):
            return f"错误: {data['error'][:60]}"
        if tool_name == "select_tables":
            tables = data.get("tables", [])
            return f"找到 {len(tables)} 个表: {', '.join(tables[:5])}"
        if tool_name == "retrieve_metadata":
            return f"元数据已获取 ({len(str(data))} 字符)"
        if tool_name == "execute_sql":
            rows = data.get("row_count", 0)
            cols = data.get("columns", [])
            elapsed = data.get("elapsed_ms", 0)
            return f"{rows} 行, {len(cols)} 列, {elapsed}ms"
        if tool_name == "generate_sql":
            if data.get("success"):
                sql = data.get("sql", "")
                return f"SQL 已生成 ({len(sql)} 字符)"
            return f"失败: {data.get('message', '')[:60]}"
        if tool_name == "validate_sql":
            warns = data.get("warnings", [])
            return f"校验通过" + (f" ({len(warns)} 条警告)" if warns else "")
        if tool_name == "search_columns":
            total = data.get("total", 0)
            return f"找到 {total} 个字段"
        if tool_name == "search_business_terms":
            terms = data.get("terms", [])
            return f"找到 {len(terms)} 个术语"
        if tool_name == "search_relations":
            total = data.get("total", 0)
            return f"找到 {total} 条关联关系"
        if tool_name == "list_tables":
            results = data.get("results", {})
            total = sum(len(v) for v in results.values())
            return f"找到 {total} 个表"
        if tool_name == "get_sample_data":
            rows = data.get("row_count", 0)
            cols = data.get("columns", [])
            return f"{rows} 行, {len(cols)} 列"
        if tool_name == "think":
            return "已记录思考"
        if tool_name == "ask_user":
            return f"提问: {data.get('question', '')[:50]}"
        if tool_name == "explain_error":
            return data.get("message", "分析完成")[:60]
    s = str(tool_result)
    return s[:80] + "…" if len(s) > 80 else s


# ── Token Estimation ────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Improved token estimation for mixed CN/EN content."""
    from backend.common.llm.token_estimator import estimate_tokens
    return estimate_tokens(text)


def _estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    from backend.common.llm.token_estimator import estimate_messages_tokens
    return estimate_messages_tokens(messages)
    return total


# ── Datasource Helpers ──────────────────────────────────────────────

_ds_cache: dict = {}


def _get_datasource_info(datasource_id: int) -> dict:
    """Get datasource info by ID."""
    if not datasource_id:
        return {}
    if datasource_id in _ds_cache:
        return _ds_cache[datasource_id]
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, db_type FROM adh_datasources WHERE id = %s", (datasource_id,))
                row = cur.fetchone()
                if row:
                    info = {"id": row["id"], "name": row["name"], "db_type": row["db_type"]}
                    _ds_cache[datasource_id] = info
                    return info
        finally:
            conn.close()
    except Exception:
        pass
    return {}


def _get_datasource_name(datasource_id: int) -> str:
    """Get datasource name by ID."""
    info = _get_datasource_info(datasource_id)
    return info.get("name", "")


# ── Tool Result Truncation ──────────────────────────────────────────

def _is_retryable_error(error_msg: str) -> bool:
    """Quick check if an error is retryable (can be fixed by modifying SQL/DSL)."""
    lower = error_msg.lower()
    non_retryable = [
        "index_not_found", "no such index",
        "not_found", "'found': false",
        "access denied", "permission denied",
        "connection refused", "timeout", "unreachable",
    ]
    return not any(p in lower for p in non_retryable)


def _truncate_tool_result(tool_name: str, result: str) -> str:
    """Truncate tool result based on per-tool budget. Returns original if within budget."""
    max_chars = TOOL_RESULT_MAX_CHARS.get(tool_name, DEFAULT_TOOL_RESULT_MAX)
    if max_chars is None or len(result) <= max_chars:
        return result

    # Truncate and add notice
    truncated = result[:max_chars]
    # Try to cut at a line boundary
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars * 0.7:
        truncated = truncated[:last_newline]
    omitted = result.count("\n") - truncated.count("\n")
    return f"{truncated}\n… {omitted} additional line(s) omitted."


# ── Context Compaction ──────────────────────────────────────────────

def _build_compaction_summary(
    messages: list[dict],
    question: str,
    confirmed_context: dict,
    all_tool_calls: list[dict],
) -> str:
    """Build a structured summary of compacted messages for context continuation."""
    # Extract key info from tool calls
    confirmed_tables = set()
    executed_sqls = []
    key_findings = []
    tools_used = []

    for tc in all_tool_calls:
        tool = tc["tool"]
        tools_used.append(tool)

        if tool == "select_tables":
            try:
                result = json.loads(tc["result"])
                confirmed_tables.update(result.get("tables", []))
            except (json.JSONDecodeError, TypeError):
                pass
        elif tool == "retrieve_metadata":
            try:
                args = tc.get("arguments", {})
                confirmed_tables.update(args.get("table_names", []))
            except Exception:
                pass
        elif tool == "execute_sql":
            sql = tc.get("arguments", {}).get("sql", "")
            if sql:
                executed_sqls.append(sql[:200])
            try:
                result = json.loads(tc["result"])
                row_count = result.get("row_count", 0)
                key_findings.append(f"execute_sql: {row_count} rows returned")
            except (json.JSONDecodeError, TypeError):
                pass
        elif tool == "ask_user":
            key_findings.append(f"ask_user: {tc.get('result', '')[:100]}")

    # Build timeline from tool calls
    timeline_lines = []
    for tc in all_tool_calls[-8:]:  # Last 8 tool calls
        elapsed = tc.get("elapsed", 0)
        timeline_lines.append(f"  - {tc['tool']}: {tc.get('result_preview', '')[:80]} ({elapsed:.1f}s)")

    # User confirmed info
    user_confirmed = ""
    if confirmed_context:
        user_confirmed = "\n- 用户确认的信息:\n"
        for k, v in confirmed_context.items():
            user_confirmed += f"  - {k}: {v}\n"

    summary = f"""会话摘要:
- 范围: 压缩了之前的对话消息
- 用户问题: {question}
- 已确认的表: {', '.join(sorted(confirmed_tables)) if confirmed_tables else '无'}
- 已执行的 SQL: {len(executed_sqls)} 条
- 关键发现: {'; '.join(key_findings[-5:]) if key_findings else '无'}
- 工具调用历史: {', '.join(tools_used[-10:]) if tools_used else '无'}{user_confirmed}
- 关键时间线:
{chr(10).join(timeline_lines) if timeline_lines else '  (无)'}

注意: "已确认的表"是数据库表名，不是 ES 索引名。ES 索引名必须通过 list_indices 或用户指定获取。

最近的消息已原文保留。从上次中断的地方继续，不要复述摘要内容。"""

    return summary


# ── Hooks ────────────────────────────────────────────────────────────

async def _pre_tool_use_hook(
    tool_name: str,
    tool_input: dict,
    agent_context: dict,
) -> dict:
    """PreToolUse hook — auto-inject parameters, log.

    Returns possibly modified tool_input.
    """
    # Auto-inject question for retrieve_metadata
    if tool_name == "retrieve_metadata" and not tool_input.get("question"):
        tool_input["question"] = agent_context.get("question", "")

    # Log to audit
    logger.info("[Hook] PreToolUse: %s(%s)", tool_name,
                json.dumps(tool_input, ensure_ascii=False)[:200])

    return tool_input


async def _post_tool_use_hook(
    tool_name: str,
    tool_input: dict,
    tool_output: str,
    agent_context: dict,
) -> str:
    """PostToolUse hook — truncate results, track execution patterns.

    Returns possibly modified tool_output.
    """
    # Truncate oversized results
    truncated = _truncate_tool_result(tool_name, tool_output)
    if len(truncated) < len(tool_output):
        logger.info("[Hook] PostToolUse: %s truncated %d → %d chars",
                     tool_name, len(tool_output), len(truncated))

    return truncated


# ── System Prompt Builder ────────────────────────────────────────────

def _build_agent_system_prompt(
    engine: str,
    datasource_name: str,
    question: str,
    prev_context: dict,
    tools_listing: str,
    agent_graph: str = "",
    workspace_context: str = "",
) -> str:
    """Build the 4-layer system prompt for the agent.

    Layer 1 (static): Identity + capability
    Layer 2 (static): Behavior principles
    --- DYNAMIC BOUNDARY ---
    Layer 3 (dynamic): Runtime environment + conversation context
    Layer 4 (dynamic): Current question

    Metadata (layer 4 equivalent) enters via tool results, not here.
    """
    current_date = datetime.now().strftime("%Y-%m-%d %A")

    # ── Static Layer — loaded from files ──
    from backend.config.agent_loader import load_agent_prompt, load_orchestrator_rules

    # Load orchestrator system prompt from file
    system_template = load_agent_prompt("orchestrator")
    # Load sandbox_coder rules for code generation
    sandbox_coder_rules = load_agent_prompt("sandbox_coder") or ""

    if system_template:
        static = system_template.format(
            tools_listing=tools_listing,
            agent_graph=agent_graph,
            scheduler_rules=load_orchestrator_rules(),
            sandbox_coder_rules=sandbox_coder_rules,
        )
    else:
        # Fallback if file not found
        static = f"""你是 ChatBI 数据分析助手的编排调度层。

## 可用工具
{tools_listing}

## 子 Agent 调度
{agent_graph}

## 代码生成规则
{sandbox_coder_rules}"""

    # ── Dynamic Layer ──
    dynamic = f"""## 运行环境
- 数据引擎: {engine}
- 当前日期: {current_date}"""

    # Add workspace context if available
    if workspace_context:
        dynamic += "\n" + workspace_context

    if prev_context:
        parts = []
        if prev_context.get("question"):
            parts.append(f"上一次查询: {prev_context['question']}")
        if prev_context.get("sql"):
            parts.append(f"生成的 SQL: {prev_context['sql'][:300]}")
        if prev_context.get("row_count") is not None:
            parts.append(f"结果: {prev_context['row_count']} 行, 耗时 {prev_context.get('elapsed_ms', 0)}ms")
        if prev_context.get("feedback"):
            fb = prev_context["feedback"]
            if fb == "down":
                parts.append("用户反馈: 不满意，请重新分析问题")
            elif fb == "up":
                parts.append("用户反馈: 满意")
        if parts:
            dynamic += "\n\n## 对话上下文\n" + "\n".join(f"- {p}" for p in parts)

    dynamic += f"""

## 当前用户问题
{question}"""

    return f"{static}\n\n---\n\n{dynamic}"


# ── System Tool Definitions (Anthropic tool_use format) ──────────────

SYSTEM_TOOLS = [
    # === Context Gathering (Claude Code: Read/Grep/Explore) ===
    {
        "name": "list_tables",
        "description": "【补充工具】按关键词模糊搜索数据表。仅当 select_tables 结果不理想时使用，用于补充发现 select_tables 可能遗漏的表。一次传多个关键词（如 ['用户', '公司', '设备']），每个关键词最多返回10条。不要反复调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "搜索关键词列表（如 ['用户', '公司', '设备']），按表名和注释模糊匹配，每个关键词最多返回10条",
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "select_tables",
        "description": "【首选工具】根据自然语言问题，使用 BM25+向量混合检索选出最相关的数据表。自动提取关键词、扩展同义词、融合稀疏+稠密排序。返回匹配的表名列表。处理数据查询问题时必须首先调用此工具，不要用 list_tables 替代。",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的自然语言问题（直接传入原始问题即可，工具会自动提取关键词）",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "search_columns",
        "description": "在所有表中搜索匹配关键词的字段名。相当于 Grep，用于跨表查找特定字段（如搜索哪些表有 'company_id' 字段）。当你不确定某个字段在哪个表中时使用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要搜索的字段名关键词（如 'company'、'user_id'、'设备'）",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "retrieve_metadata",
        "description": "一次性检索指定表的完整元数据：M-Schema表结构、ER图(表关联关系)、业务术语、SQL模板示例。返回的内容可直接作为 generate_sql 的 context 参数使用。只需调用一次，传入所有需要的表名。",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要检索元数据的表名列表（建议传入 select_tables 返回的全部表名）",
                },
                "question": {
                    "type": "string",
                    "description": "用户的原始问题（必填），用于语义检索 SQL 模板和业务术语",
                },
            },
            "required": ["table_names", "question"],
        },
    },
    {
        "name": "get_sample_data",
        "description": "预览表的样本数据（前 5 行），帮助理解数据的实际内容和格式。相当于 cat 文件。在生成复杂 SQL 前先查看数据样本，确认字段含义和数据分布。",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "要预览的表名",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只预览指定的列",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "search_business_terms",
        "description": "搜索业务术语库，查找与关键词匹配的术语定义、计算公式、对应字段。用于理解用户问题中的业务概念。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要搜索的业务关键词",
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "search_relations",
        "description": "搜索表之间的关联关系（JOIN关系）。传入表名列表，返回这些表与其他表之间的外键关联。用于理解多表查询时如何 JOIN。当问题涉及多个实体（如用户+公司、订单+商品）时，必须调用此工具确认关联关系。",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查询关联关系的表名列表",
                },
            },
            "required": ["table_names"],
        },
    },
    # === SQL Generation & Execution ===
    {
        "name": "generate_sql",
        "description": "根据用户问题和元数据上下文，调用 LLM 生成 SQL。返回 JSON：{\"success\":true,\"sql\":\"...\",\"tables\":[...],\"chart-type\":\"...\"} 或 {\"success\":false,\"message\":\"...\"}。必须先调用 retrieve_metadata 获取上下文，并通过 context 参数传入。",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的自然语言问题",
                },
                "context": {
                    "type": "string",
                    "description": "元数据上下文（必填），由 retrieve_metadata 返回的完整内容，包含表结构、关联关系、术语、SQL模板",
                },
            },
            "required": ["question", "context"],
        },
    },
    {
        "name": "get_sql_rules",
        "description": "获取当前数据源的 SQL 语法规则和约束。在手写 SQL 之前必须先调用此工具获取规则。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "validate_sql",
        "description": "校验 SQL 语句的语法和安全性，返回校验结果和修复建议。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "要校验的 SQL 语句",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "execute_sql",
        "description": "执行 SQL 查询并返回结果。支持 Doris、MySQL、Elasticsearch 数据源。返回查询结果（列名、行数据、耗时）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "要执行的 SQL 语句",
                },
                "query_type": {
                    "type": "string",
                    "enum": ["sql", "rest", "dsl"],
                    "description": "查询类型：sql（标准SQL）、rest（ES REST API）、dsl（ES DSL JSON）。默认 sql。",
                },
            },
            "required": ["sql"],
        },
    },
    # === Self-Correction (Claude Code: error → fix loop) ===
    {
        "name": "explain_error",
        "description": "分析 SQL 执行错误的原因并给出修复建议。返回中包含 retryable 字段：true 表示可修正后重试，false 表示应直接告知用户失败原因。",
        "input_schema": {
            "type": "object",
            "properties": {
                "error_message": {
                    "type": "string",
                    "description": "execute_sql 返回的错误信息",
                },
                "failed_sql": {
                    "type": "string",
                    "description": "执行失败的 SQL 语句",
                },
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SQL 涉及的表名（可选，用于检查表结构）",
                },
            },
            "required": ["error_message", "failed_sql"],
        },
    },
    # === Reasoning (Claude Code: Think) ===
    {
        "name": "think",
        "description": (
            "结构化推理工具，用于复杂问题的分析和决策。无副作用。\n\n"
            "**何时使用**：\n"
            "- 问题涉及多个实体/维度，需要拆解查询策略\n"
            "- SQL 执行失败，需要分析错误原因并制定修复方案\n"
            "- 需要验证 SQL 逻辑是否正确\n\n"
            "**何时停止（必须遵守）**：\n"
            "- 你已经知道下一步该调用什么工具 → 直接调用，不要再 think\n"
            "- 你已经有了 SQL 和元数据 → 直接 execute_sql，不要再 think\n"
            "- 你已经执行了 SQL 并有结果 → 直接回答用户，不要再 think\n"
            "- 连续调用 think 不超过 2 次。第 2 次 think 后必须行动\n\n"
            "**禁止**：\n"
            "- 在 think 中重复已经想过的方案\n"
            "- 在 think 中生成 SQL（应该调用 generate_sql 或直接写 SQL 执行）\n"
            "- 用 think 代替行动（think 完必须接工具调用）"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "你的思考内容。每次 think 必须产生明确的下一步行动决策。",
                },
            },
            "required": ["thought"],
        },
    },
    # === User Interaction ===
    {
        "name": "ask_user",
        "description": (
            "当你无法确定应该查询哪些表、使用哪些字段、或需要用户澄清问题时，调用此工具向用户提问。\n\n"
            "**重要**：\n"
            "- 调用本工具时，不要同时调用其他工具\n"
            "- 等待用户回复后，再决定下一步操作\n"
            "- 如果用于代码执行确认，用户同意后在下一轮调用 propose_code"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "向用户提出的问题",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "供用户选择的选项列表（可选）",
                },
            },
            "required": ["question"],
        },
    },
]

# ── Orchestrator Tools ─────────────────────────────────────────────
# Main agent only needs orchestration tools. SQL tools are internal to sub-agents.
ORCHESTRATOR_TOOLS = [
    {
        "name": "think",
        "description": (
            "结构化推理工具，用于分析用户意图、选择子 Agent、制定调度策略。无副作用。\n\n"
            "**何时使用**：\n"
            "- 分析用户问题应该交给哪个子 Agent\n"
            "- 子 Agent 返回结果后，反思是否满足需求\n"
            "- 需要决定是否并行调用多个子 Agent\n\n"
            "**何时停止**：\n"
            "- 你已经确定该调用哪个子 Agent → 直接调用\n"
            "- 你已经有了最终答案 → 直接回答用户\n"
            "- 连续调用 think 不超过 2 次"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "你的思考内容。每次 think 必须产生明确的下一步行动决策。",
                },
            },
            "required": ["thought"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "向用户提问，获取用户确认或澄清。\n\n"
            "**重要**：\n"
            "- 调用本工具时，不要同时调用其他工具\n"
            "- 等待用户回复后，再决定下一步操作\n"
            "- **代码执行确认必须提供 options**，让用户点击按钮，例如：options=['同意执行', '不需要']\n"
            "- 用户同意后在下一轮调用 propose_code"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "向用户提出的问题",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "供用户点击选择的选项列表（代码确认时必须提供，如 ['同意执行', '不需要']）",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "propose_code",
        "description": (
            "向用户展示待执行的 Python 代码，请求确认后在沙箱中执行。\n\n"
            "**使用前提**：\n"
            "- 必须先单独调用 ask_user 征得用户同意\n"
            "- 用户同意后才能调用本工具\n"
            "- 仅当 SQL 无法完成分析时才使用\n\n"
            "**使用流程**：\n"
            "1. 第一轮：只调用 ask_user，等待用户回复\n"
            "2. 第二轮：用户同意后，只调用本工具\n"
            "3. 等待用户在前端确认执行\n"
            "4. 执行结果会自动返回\n\n"
            "**⚠️ 重要**：\n"
            "- 禁止与 ask_user 在同一轮调用\n"
            "- 调用本工具后，必须等待执行结果返回才能回答用户\n"
            "- 禁止编造结果，只能使用实际返回的数据\n"
            "- 代码必须遵循 sandbox_coder 规则（见系统提示词）"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码",
                },
                "description": {
                    "type": "string",
                    "description": "代码功能简述（展示给用户）",
                },
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要安装的 pip 包列表（如 ['pandas', 'numpy']）",
                },
            },
            "required": ["code", "description"],
        },
    },
]


async def _execute_system_tool(
    tool_name: str,
    tool_input: dict,
    datasource_id: int,
    model_id: int,
    user_id: int,
    username: str,
    question: str = "",
    allowed_datasource_ids: list = None,
) -> str:
    """Execute a system tool and return the result as a string.

    Args:
        allowed_datasource_ids: When set, metadata queries search across all these datasources.
            SQL execution still uses the primary datasource_id.
    """
    # For metadata queries, use all allowed datasources; for SQL execution, use primary
    ds_ids_for_meta = allowed_datasource_ids if allowed_datasource_ids else ([datasource_id] if datasource_id else [])
    try:
        # ── Context Gathering ──────────────────────────────────────

        if tool_name == "list_tables":
            from backend.rag.table_selector import _get_all_tables
            keywords = tool_input.get("keywords", [])
            if not keywords:
                return json.dumps({
                    "error": "必须提供 keywords 列表。如果无法确定要搜索什么，请调用 ask_user 向用户询问。",
                }, ensure_ascii=False)
            # Query across all allowed datasources
            all_tables = []
            for ds_id in ds_ids_for_meta:
                all_tables.extend(_get_all_tables(ds_id))
            result = {}
            for kw in keywords:
                kw_lower = kw.lower().strip()
                if not kw_lower:
                    continue
                matched = [
                    {"table_name": t["table_name"], "comment": t.get("table_comment", "")}
                    for t in all_tables
                    if kw_lower in t.get("table_name", "").lower()
                    or kw_lower in t.get("table_comment", "").lower()
                ][:10]
                result[kw] = matched
            return json.dumps({"results": result}, ensure_ascii=False)
            return json.dumps({"total": len(all_tables), "tables": result}, ensure_ascii=False)

        elif tool_name == "select_tables":
            from backend.rag.table_selector import select_tables
            # Query across all allowed datasources and merge results
            all_tables = []
            seen = set()
            for ds_id in ds_ids_for_meta or [datasource_id]:
                tables = select_tables(tool_input["question"], top_k=15, datasource_id=ds_id)
                for t in tables:
                    if t not in seen:
                        seen.add(t)
                        all_tables.append(t)
            return json.dumps({"tables": all_tables[:15]}, ensure_ascii=False)

        elif tool_name == "search_columns":
            import pymysql
            from backend.common.config import DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE

            keyword = tool_input["keyword"]
            conn = pymysql.connect(
                host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
                password=DORIS_PASSWORD, database=METADATA_DB_DATABASE,
                charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
            )
            try:
                with conn.cursor() as cur:
                    if ds_ids_for_meta:
                        ids_str = ",".join(str(i) for i in ds_ids_for_meta)
                        ds_filter = f"AND (datasource_id IN ({ids_str}) OR datasource_id = 0)"
                    else:
                        ds_filter = ""
                    cur.execute(
                        f"SELECT table_name, column_name, data_type, column_comment "
                        f"FROM adh_column_metadata "
                        f"WHERE is_active = 1 {ds_filter} "
                        f"AND (column_name LIKE %s OR column_comment LIKE %s) "
                        f"LIMIT 30",
                        (f"%{keyword}%", f"%{keyword}%"),
                    )
                    columns = cur.fetchall()
            finally:
                conn.close()

            return json.dumps({
                "keyword": keyword,
                "total": len(columns),
                "columns": columns,
            }, ensure_ascii=False, default=str)

        elif tool_name == "retrieve_metadata":
            from backend.rag.rag_retriever import (
                _get_table_info_for_names, _get_columns_for_tables,
                retrieve_sql_templates, retrieve_business_terms, retrieve_table_relations,
            )
            from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

            table_names = tool_input["table_names"]
            search_question = tool_input.get("question", "") or question

            table_info = _get_table_info_for_names(table_names, datasource_id)
            columns = _get_columns_for_tables(table_names, datasource_id)

            from backend.nl2sql.prompt.prompt_builder import _to_m_schema, _to_er_diagram, _to_terminologies, _to_sql_examples
            schema_text = _to_m_schema(table_info, columns)

            vec_literal = ""
            if search_question:
                try:
                    vec_literal = embedding_to_sql_literal(generate_embedding(search_question))
                except Exception:
                    pass

            templates = retrieve_sql_templates(search_question, 5, vec_literal, datasource_id)
            terms = retrieve_business_terms(search_question, 20, vec_literal=vec_literal, datasource_id=datasource_id)
            relations = retrieve_table_relations(search_question, 20, table_names, vec_literal, datasource_id)

            er_text = _to_er_diagram(relations)
            terms_text = _to_terminologies(terms)
            examples_text = _to_sql_examples(templates)

            parts = [f"## 表结构\n{schema_text}"]
            if er_text:
                parts.append(f"\n{er_text}")
            if terms_text:
                parts.append(f"\n{terms_text}")
            if examples_text:
                parts.append(f"\n{examples_text}")

            logger.info("[Agent] retrieve_metadata: tables=%d, cols=%d, templates=%d, terms=%d, relations=%d",
                        len(table_info), len(columns), len(templates), len(terms), len(relations))

            return "\n".join(parts)

        elif tool_name == "get_sample_data":
            from backend.nl2sql.sql.query_executor import execute_query

            table_name = tool_input["table_name"]
            columns = tool_input.get("columns", [])
            col_str = ", ".join(f"`{c}`" for c in columns) if columns else "*"
            sql = f"SELECT {col_str} FROM `{table_name}` LIMIT 5"

            df, elapsed_ms, row_count = execute_query(sql, datasource_id)
            cols = list(df.columns) if not df.empty else []
            rows = [_sanitize_row(row) for row in df.to_dict(orient="records")] if not df.empty else []

            return json.dumps({
                "table": table_name,
                "columns": cols,
                "rows": rows,
                "row_count": row_count,
            }, ensure_ascii=False, default=str)

        elif tool_name == "search_business_terms":
            from backend.rag.rag_retriever import retrieve_business_terms
            from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

            keywords = tool_input["keywords"]
            q = " ".join(keywords)
            try:
                vec_literal = embedding_to_sql_literal(generate_embedding(q))
            except Exception:
                vec_literal = None

            terms = retrieve_business_terms(
                q, 20, keywords=keywords,
                vec_literal=vec_literal, datasource_id=datasource_id,
            )
            return json.dumps({"terms": terms[:10]}, ensure_ascii=False, default=str)

        elif tool_name == "search_relations":
            import pymysql as _pymysql
            from backend.common.config import (
                DORIS_HOST as _H, DORIS_PORT as _P, DORIS_USER as _U,
                DORIS_PASSWORD as _PW, METADATA_DB_DATABASE as _DB,
            )

            table_names = tool_input["table_names"]
            if not table_names:
                return json.dumps({"error": "必须提供 table_names"}, ensure_ascii=False)

            conn = _pymysql.connect(
                host=_H, port=_P, user=_U, password=_PW,
                database=_DB, charset="utf8mb4",
                cursorclass=_pymysql.cursors.DictCursor, connect_timeout=10,
            )
            try:
                with conn.cursor() as cur:
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    placeholders = ",".join(["%s"] * len(table_names))
                    # Find relations where source OR target is in the provided tables
                    cur.execute(
                        f"SELECT source_table, source_column, target_table, target_column, "
                        f"relation_type, description "
                        f"FROM adh_table_relations "
                        f"WHERE is_active = 1 {ds_filter} "
                        f"AND (source_table IN ({placeholders}) OR target_table IN ({placeholders}))",
                        table_names + table_names,
                    )
                    relations = cur.fetchall()
            finally:
                conn.close()

            return json.dumps({
                "table_names": table_names,
                "total": len(relations),
                "relations": relations,
            }, ensure_ascii=False, default=str)

        # ── SQL Generation & Execution ─────────────────────────────

        elif tool_name == "generate_sql":
            from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
            from backend.common.llm.llm_client import generate_sql as llm_generate_sql
            from backend.rag.rag_retriever import (
                retrieve_all, _get_table_info_for_names, _get_columns_for_tables,
                retrieve_sql_templates, retrieve_business_terms, retrieve_table_relations,
            )
            from backend.nl2sql.sql.query_executor import _get_ds_conn_params
            from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

            gen_question = tool_input["question"]
            agent_context = tool_input.get("context", "")

            ds_params = _get_ds_conn_params(datasource_id)
            db_type = ds_params.get("db_type", "doris")
            engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
            engine = engine_map.get(db_type, db_type.capitalize())

            # Always use build_nl2sql_prompt for consistent, full-featured prompts
            # When agent has context, extract table names from it for targeted retrieval
            if agent_context:
                # Extract table names from the agent's context (M-Schema format)
                # M-Schema has lines like: "# Table: table_name, comment"
                import re as _re_tables
                recovered_tables = list(dict.fromkeys(
                    m.group(1) for m in _re_tables.finditer(
                        r'#\s*Table:\s*(\w+)', agent_context
                    )
                ))

                if recovered_tables:
                    table_info = _get_table_info_for_names(recovered_tables, datasource_id)
                    columns = _get_columns_for_tables(recovered_tables, datasource_id)
                    try:
                        vec = embedding_to_sql_literal(generate_embedding(gen_question))
                    except Exception:
                        vec = ""
                    templates = retrieve_sql_templates(gen_question, 5, vec, datasource_id)
                    terms = retrieve_business_terms(gen_question, 20, vec_literal=vec, datasource_id=datasource_id)
                    relations = retrieve_table_relations(gen_question, 20, recovered_tables, vec, datasource_id)
                else:
                    # Fallback: no table names found, do full RAG
                    rag = retrieve_all(gen_question, datasource_id=datasource_id)
                    table_info = rag["table_info"]
                    columns = rag["column_metadata"]
                    templates = rag["sql_templates"]
                    terms = rag["business_terms"]
                    relations = rag.get("table_relations", [])
            else:
                # No context — full RAG retrieval
                rag = retrieve_all(gen_question, datasource_id=datasource_id)
                table_info = rag["table_info"]
                columns = rag["column_metadata"]
                templates = rag["sql_templates"]
                terms = rag["business_terms"]
                relations = rag.get("table_relations", [])

            messages = build_nl2sql_prompt(
                question=gen_question,
                table_info=table_info,
                column_metadata=columns,
                sql_templates=templates,
                business_terms=terms,
                table_relations=relations,
                engine=engine,
            )

            result = llm_generate_sql(messages, model_id=model_id)
            raw = result.get("sql", "")

            # Parse LLM response and return structured JSON
            import json as _json
            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            try:
                parsed = _json.loads(text)
                return _json.dumps(parsed, ensure_ascii=False)
            except _json.JSONDecodeError:
                # Try extracting JSON object
                match = _re.search(r'\{.*\}', text, _re.DOTALL)
                if match:
                    try:
                        parsed = _json.loads(match.group(0))
                        return _json.dumps(parsed, ensure_ascii=False)
                    except _json.JSONDecodeError:
                        pass
                # Treat entire text as raw SQL
                return _json.dumps({
                    "success": True,
                    "sql": text,
                    "tables": [],
                    "chart-type": "table",
                }, ensure_ascii=False)

        elif tool_name == "get_sql_rules":
            from backend.nl2sql.sql.template_loader import get_sql_prompt
            from backend.nl2sql.sql.query_executor import _get_ds_conn_params

            ds_params = _get_ds_conn_params(datasource_id)
            db_type = ds_params.get("db_type", "doris")
            engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
            engine = engine_map.get(db_type, db_type.capitalize())

            tpl = get_sql_prompt(engine, query_limit=True)
            return tpl.get("system", "")

        elif tool_name == "validate_sql":
            from backend.nl2sql.sql.sql_validator import validate_and_fix
            sql, warnings = validate_and_fix(tool_input["sql"], query_type=tool_input.get("query_type", "sql"))
            return json.dumps({"sql": sql, "warnings": warnings}, ensure_ascii=False)

        elif tool_name == "execute_sql":
            from backend.nl2sql.sql.query_executor import execute_query
            from backend.nl2sql.sql.sql_validator import validate_and_fix

            sql = tool_input["sql"]
            query_type = tool_input.get("query_type", "sql")

            # ── Pre-execution verification (like CI before deploy) ──
            # Extract table names from SQL and verify they exist in metadata
            if query_type == "sql":
                import re as _re
                sql_upper = sql.upper()
                table_pattern = _re.compile(r'(?:FROM|JOIN)\s+`?(\w+)`?', _re.IGNORECASE)
                tables_in_sql = list(set(m.group(1).lower() for m in table_pattern.finditer(sql)))

                if tables_in_sql:
                    import pymysql as _pymysql
                    from backend.common.config import (
                        DORIS_HOST as _H, DORIS_PORT as _P, DORIS_USER as _U,
                        DORIS_PASSWORD as _PW, METADATA_DB_DATABASE as _DB,
                    )
                    try:
                        _conn = _pymysql.connect(
                            host=_H, port=_P, user=_U, password=_PW,
                            database=_DB, charset="utf8mb4",
                            cursorclass=_pymysql.cursors.DictCursor, connect_timeout=5,
                        )
                        try:
                            with _conn.cursor() as _cur:
                                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                                placeholders = ",".join(["%s"] * len(tables_in_sql))
                                _cur.execute(
                                    f"SELECT table_name FROM adh_table_info "
                                    f"WHERE is_active = 1 {ds_filter} AND LOWER(table_name) IN ({placeholders})",
                                    tables_in_sql,
                                )
                                verified = {r["table_name"].lower() for r in _cur.fetchall()}
                                missing = [t for t in tables_in_sql if t not in verified]
                        finally:
                            _conn.close()

                        if missing:
                            return json.dumps({
                                "error": f"SQL 中的以下表在元数据中不存在: {', '.join(missing)}",
                                "missing_tables": missing,
                                "tables_in_sql": tables_in_sql,
                                "hint": (
                                    "这些表未经 retrieve_metadata 确认。请：\n"
                                    "1. 调用 select_tables 或 list_tables 找到正确的表名\n"
                                    "2. 调用 retrieve_metadata 获取表结构\n"
                                    "3. 基于返回的元数据重新生成 SQL\n"
                                    "禁止使用未经元数据确认的表名。"
                                ),
                            }, ensure_ascii=False)

                        # Also verify columns exist in metadata
                        col_pattern = _re.compile(r'`(\w+)`\.`(\w+)`', _re.IGNORECASE)
                        table_col_pairs = list(set(
                            (m.group(1).lower(), m.group(2).lower())
                            for m in col_pattern.finditer(sql)
                        ))
                        # Filter out SQL keywords that might match
                        sql_keywords = {"select", "from", "where", "and", "or", "on", "in", "as",
                                        "group", "by", "order", "having", "limit", "join", "inner",
                                        "left", "right", "outer", "union", "insert", "update", "delete",
                                        "create", "drop", "alter", "index", "null", "not", "is", "like",
                                        "between", "exists", "case", "when", "then", "else", "end",
                                        "distinct", "count", "sum", "avg", "min", "max", "desc", "asc"}
                        table_col_pairs = [
                            (t, c) for t, c in table_col_pairs
                            if c not in sql_keywords and t not in sql_keywords
                        ]

                        if table_col_pairs:
                            try:
                                _conn2 = _pymysql.connect(
                                    host=_H, port=_P, user=_U, password=_PW,
                                    database=_DB, charset="utf8mb4",
                                    cursorclass=_pymysql.cursors.DictCursor, connect_timeout=5,
                                )
                                try:
                                    with _conn2.cursor() as _cur2:
                                        missing_cols = []
                                        for tbl, col in table_col_pairs:
                                            ds_filter2 = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                                            _cur2.execute(
                                                f"SELECT COUNT(*) as cnt FROM adh_column_metadata "
                                                f"WHERE is_active = 1 {ds_filter2} "
                                                f"AND LOWER(table_name) = %s AND LOWER(column_name) = %s",
                                                (tbl, col),
                                            )
                                            if _cur2.fetchone()["cnt"] == 0:
                                                missing_cols.append(f"{tbl}.{col}")

                                        if missing_cols:
                                            return json.dumps({
                                                "error": f"SQL 中的以下字段在元数据中不存在: {', '.join(missing_cols)}",
                                                "missing_columns": missing_cols,
                                                "hint": (
                                                    "这些字段未经 retrieve_metadata 确认。请：\n"
                                                    "1. 调用 retrieve_metadata 获取正确的表结构\n"
                                                    "2. 调用 search_columns 搜索正确的字段名\n"
                                                    "3. 基于返回的元数据重新生成 SQL\n"
                                                    "禁止使用未经元数据确认的字段名。"
                                                ),
                                            }, ensure_ascii=False)
                                finally:
                                    _conn2.close()
                            except Exception:
                                pass  # Column verification is best-effort
                    except Exception as _verify_err:
                        logger.warning("[Agent] Pre-execution verification failed: %s", _verify_err)

            # Auto-fix common issues (LIMIT, etc.) — skip for REST/DSL
            sql, val_warnings = validate_and_fix(sql, query_type=query_type)

            df, elapsed_ms, row_count = execute_query(sql, datasource_id, query_type=query_type)

            columns = list(df.columns) if not df.empty else []
            rows = [_sanitize_row(row) for row in df.head(100).to_dict(orient="records")] if not df.empty else []

            return json.dumps({
                "columns": columns,
                "rows": rows,
                "row_count": row_count,
                "elapsed_ms": elapsed_ms,
                "fixed_sql": sql if val_warnings else None,
                "warnings": val_warnings,
            }, ensure_ascii=False, default=str)

        # ── Self-Correction ────────────────────────────────────────

        elif tool_name == "explain_error":
            error_msg = tool_input["error_message"]
            failed_sql = tool_input["failed_sql"]
            table_names = tool_input.get("table_names", [])

            # ── Determine if error is retryable ──
            is_retryable = _is_retryable_error(error_msg)

            if not is_retryable:
                result = {
                    "retryable": False,
                    "error": error_msg,
                    "reason": "此错误无法通过修改 SQL 解决，请直接告知用户失败原因。",
                }
                return json.dumps(result, ensure_ascii=False)

            # ── Retryable: provide fix suggestions ──
            err_lower = error_msg.lower()
            fix_suggestions = []
            if "syntax" in err_lower or "语法" in err_lower:
                fix_suggestions.append("SQL 语法错误，请检查关键字拼写和语句结构")
            if "unknown column" in err_lower or "column" in err_lower and "not found" in err_lower:
                fix_suggestions.append("字段名有误，请调用 search_columns 确认正确字段名")
            if "doesn't exist" in err_lower or "not found" in err_lower:
                fix_suggestions.append("表名有误，请调用 select_tables 确认正确表名")
            if "limit" in err_lower:
                fix_suggestions.append("添加 LIMIT 限制数据量")
            if "group by" in err_lower:
                fix_suggestions.append("SELECT 中的非聚合字段必须出现在 GROUP BY 中")

            # If table names provided, check their structure
            context_info = ""
            if table_names:
                try:
                    from backend.rag.rag_retriever import _get_columns_for_tables
                    columns = _get_columns_for_tables(table_names, datasource_id)
                    if columns:
                        col_text = ", ".join(f"{c['table_name']}.{c['column_name']}" for c in columns[:30])
                        context_info = f"可用字段: {col_text}"
                except Exception:
                    pass

            result = {
                "retryable": True,
                "error": error_msg,
                "suggestions": fix_suggestions,
                "context": context_info,
                "instruction": "请根据上述信息修正 SQL 后重新执行，最多重试 2 次。如果仍然失败，请直接告知用户。",
            }
            return json.dumps(result, ensure_ascii=False)

        # ── Reasoning ──────────────────────────────────────────────

        elif tool_name == "think":
            # Think tool has no side effects — just acknowledge
            return json.dumps({"status": "ok", "message": "已记录思考内容。请继续执行下一步。"}, ensure_ascii=False)

        # ── User Interaction ───────────────────────────────────────

        elif tool_name == "ask_user":
            # Return a special marker that the agent loop will detect
            return json.dumps({
                "__ask_user__": True,
                "question": tool_input["question"],
                "options": tool_input.get("options", []),
            }, ensure_ascii=False)

        elif tool_name == "propose_code":
            # Return code proposal for frontend confirmation
            return json.dumps({
                "__propose_code__": True,
                "code": tool_input["code"],
                "description": tool_input.get("description", ""),
                "requirements": tool_input.get("requirements", []),
            }, ensure_ascii=False)

        # ── Analysis Skill Loading ─────────────────────────────────

        elif tool_name == "load_analysis_skill":
            from backend.config.skill_loader import load_skill, get_analysis_skill_names
            skill_name = tool_input.get("skill_name", "")
            if not skill_name:
                names = get_analysis_skill_names()
                return json.dumps({
                    "error": "必须提供 skill_name。",
                    "available_skills": names,
                }, ensure_ascii=False)
            skill = load_skill(skill_name)
            if not skill:
                names = get_analysis_skill_names()
                return json.dumps({
                    "error": f"未找到分析技能: {skill_name}",
                    "available_skills": names,
                }, ensure_ascii=False)
            prompt = skill.get("system_prompt", "")
            if not prompt:
                return json.dumps({
                    "error": f"技能 {skill_name} 没有提示词内容",
                }, ensure_ascii=False)
            return json.dumps({
                "skill_name": skill_name,
                "display_name": skill.get("display_name", skill_name),
                "system_prompt": prompt,
                "message": f"已加载「{skill.get('display_name', skill_name)}」分析技能。请严格遵循以下提示词中的执行流程和分析框架。",
            }, ensure_ascii=False)

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        logger.error("System tool %s failed: %s", tool_name, e, exc_info=True)
        error_msg = str(e)
        if tool_name == "execute_sql":
            failed_sql = tool_input.get("sql", "")
            retryable = _is_retryable_error(error_msg)
            return json.dumps({
                "error": error_msg,
                "failed_sql": failed_sql,
                "retryable": retryable,
                "hint": "可修正则重试，不可修正则直接告知用户。" if retryable else "此错误无法通过修改 SQL 解决，请直接告知用户原因。",
            }, ensure_ascii=False)
        return json.dumps({"error": error_msg})


def _detect_chart_type(result: dict) -> str:
    """Auto-detect appropriate chart type from query result shape."""
    if not result or result.get("error"):
        return "table"

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    row_count = result.get("row_count", 0)

    if not columns or not rows or row_count == 0:
        return "table"

    if row_count == 1 and len(columns) == 1:
        return "big_number_trend"

    if row_count == 1:
        return "table"

    time_keywords = {"time", "date", "日期", "时间", "月", "年", "日", "created", "updated",
                     "month", "year", "day", "hour", "timestamp", "dt"}
    has_time_col = any(
        any(kw in col.lower() for kw in time_keywords)
        for col in columns
    )

    if len(columns) == 2:
        return "line" if has_time_col else "column"

    if has_time_col and len(columns) >= 3:
        return "line"

    return "table"


async def agent_generate(
    question: str,
    history: list[dict] = None,
    datasource_id: int = 0,
    model_id: int = None,
    user_id: int = None,
    username: str = None,
    retrieval_strategy: str = None,
    workspace_id: int = 0,
    disable_ask_user: bool = False,
    context: str = None,
    allowed_mcp_server_ids: list = None,
    allowed_agent_names: list = None,
    max_iterations: int = None,
    system_tools: list[str] = None,
):
    """Agent pipeline — LLM fully autonomous planning with streaming.

    Uses 4-layer system prompt (identity + principles + environment + question)
    and streaming LLM calls via generate_with_tools_stream(). The LLM decides
    everything autonomously — no hardcoded workflow steps.

    Args:
        workspace_id: If provided, use workspace's configured resources (datasources + MCP servers).
                     If 0, fall back to global MCP tools (backward compatible).
    """
    from backend.common.llm.llm_client import _get_model_config, generate_with_tools_stream
    from backend.mcp_client.tools import MCPToolCaller
    # Ensure agents are registered before building tools
    from backend.nl2sql.orchestrator.pipeline_orchestrator import _init_agents
    _init_agents()

    from backend.agent.router import get_all_agents
    from backend.nl2sql.sql.query_executor import _get_ds_conn_params

    t_start = time.time()

    # 0. Load workspace configuration if workspace_id provided
    workspace = None
    workspace_tools = None
    allowed_datasource_ids = []  # All datasources available in this workspace
    if workspace_id:
        from backend.services.workspace_service import get_workspace_service
        workspace_service = get_workspace_service()
        workspace = await workspace_service.get_workspace(workspace_id)
        if workspace:
            workspace_tools = await workspace_service.get_workspace_tools(workspace_id)
            # Load ALL workspace datasources (not just primary)
            ws_datasources = workspace.get('datasources', [])
            if ws_datasources:
                allowed_datasource_ids = [ds['id'] for ds in ws_datasources]
            # Use workspace's primary datasource if not explicitly provided
            if not datasource_id:
                primary_ds = await workspace_service.get_primary_datasource(workspace_id)
                if primary_ds:
                    datasource_id = primary_ds['id']
            logger.info("[Agent] Using workspace '%s' (id=%d), datasource_id=%d, all_datasources=%s",
                        workspace.get('name'), workspace_id, datasource_id, allowed_datasource_ids)

    # 1. Engine info
    ds_params = _get_ds_conn_params(datasource_id)
    db_type = ds_params.get("db_type", "doris")
    engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
    engine = engine_map.get(db_type, db_type.capitalize())
    ds_name = ds_params.get("name", "")

    # 2. Collect tools — orchestrator tools + MCP tools + agent tools
    # MCP tools are available for fallback when sub-agents fail
    tools = list(ORCHESTRATOR_TOOLS)
    if disable_ask_user:
        tools = [t for t in tools if t["name"] != "ask_user"]
    # Filter system tools if user manually selected specific tools
    # When system_tools is specified, only include those system tools
    if system_tools:
        tools = [t for t in tools if t["name"] in system_tools]
        logger.info("[Agent] Filtered system tools to: %s", system_tools)
    seen_tool_names = {t["name"] for t in tools}
    mcp_caller = MCPToolCaller()

    if workspace_tools:
        # Use workspace's MCP tools (prefix with server name)
        # Filter by allowed_mcp_server_ids if specified
        for t in workspace_tools.get('mcp_tools', []):
            server_name = t.get('server_name', '')
            server_id = t.get('server_id', 0)
            # Skip if MCP server not in allowed list
            if allowed_mcp_server_ids is not None and server_id not in allowed_mcp_server_ids:
                continue
            tool_name = f"{server_name}__{t['name']}" if server_name else t['name']
            if tool_name in seen_tool_names:
                continue
            seen_tool_names.add(tool_name)
            tools.append({
                "name": tool_name,
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
            })
        logger.info("[Agent] Loaded %d MCP tools from workspace", len(workspace_tools.get('mcp_tools', [])))
    else:
        # Fallback: load all global MCP tools
        # Skip entirely if allowed_mcp_server_ids is set but empty (no MCP allowed)
        if allowed_mcp_server_ids is not None and len(allowed_mcp_server_ids) == 0:
            logger.info("[Agent] No MCP servers configured, skipping global MCP tools")
        else:
            try:
                await mcp_caller.initialize()
                mcp_tools = await mcp_caller.list_tools()
                for t in mcp_tools:
                    if t["name"] in seen_tool_names:
                        continue
                    seen_tool_names.add(t["name"])
                    tools.append({
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
                    })
            except Exception as e:
                logger.warning("[Agent] Failed to load MCP tools: %s", e)

    agent_tools_map = {}
    all_agents = get_all_agents()
    for agent_name, agent in all_agents.items():
        if not agent.is_active:
            continue
        # Scheduled task: only allow explicitly configured sub-agents
        if allowed_agent_names is not None and agent_name not in allowed_agent_names:
            continue
        tool_name = f"agent__{agent_name}"
        tools.append({
            "name": tool_name,
            "description": agent.description or f"调用 {agent_name} Agent 处理特定类型的任务",
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "传给子 Agent 的完整问题，必须包含用户原话和所有相关上下文",
                    },
                },
                "required": ["question"],
            },
        })
        agent_tools_map[tool_name] = agent

    # 3. Build tools listing for system prompt
    tool_descs = []
    for t in ORCHESTRATOR_TOOLS:
        tool_descs.append(f"- {t['name']}: {t['description']}")
    for tname, agent in agent_tools_map.items():
        tool_descs.append(f"- {tname}: {agent.description}")
    tools_listing = "\n".join(tool_descs)

    # 3.5 Build agent graph from file configs
    from backend.config.agent_loader import build_agent_graph
    ds_info = _get_datasource_info(datasource_id)
    agent_graph = build_agent_graph(agent_tools_map, ds_info)

    # 4. Previous context from history
    prev_context = {}
    if history:
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                if msg.get("sql"):
                    prev_context["sql"] = msg["sql"]
                if msg.get("result"):
                    prev_context["row_count"] = msg["result"].get("row_count", 0)
                    prev_context["elapsed_ms"] = msg["result"].get("elapsed_ms", 0)
                if msg.get("feedback"):
                    prev_context["feedback"] = msg["feedback"]
                if msg.get("question"):
                    prev_context["question"] = msg["question"]
                break

    # 5. Build system prompt (4-layer architecture)
    # Include workspace context if available
    workspace_context = ""
    if workspace:
        ws_datasources = workspace.get('datasources', [])
        ws_mcp_servers = workspace_tools.get('mcp_servers', []) if workspace_tools else []
        ws_agents = workspace_tools.get('agents', []) if workspace_tools else []

        ds_lines = []
        for d in ws_datasources:
            primary_mark = " ⭐主数据源" if d.get('is_primary') else ""
            ds_lines.append(f"  - {d.get('name', '未知')} (id={d['id']}, {d.get('db_type', '?')}/{d.get('database_name', '?')}){primary_mark}")
        mcp_names = [m.get('name', '未知') for m in ws_mcp_servers]
        agent_names = [a.get('name', '未知') for a in ws_agents]

        workspace_context = f"""
## 当前工作空间
- 名称: {workspace.get('name', '未命名')}
- 类型: {workspace.get('workspace_type', 'custom')}
- 可用数据源 ({len(ws_datasources)} 个):
{chr(10).join(ds_lines) if ds_lines else '  无'}
- 可用MCP服务: {', '.join(mcp_names) if mcp_names else '无'}
- 可用Agent: {', '.join(agent_names) if agent_names else '无'}
- 检索策略: {workspace.get('retrieval_strategy', 'full_table')}
"""

    # Inject RLS column constraints into workspace context
    if user_id and workspace_id:
        try:
            from backend.services.rls_service import rls_service
            # Collect all RLS restrictions across workspace datasources
            rls_hidden = set()
            rls_masked = set()
            ds_ids = [d['id'] for d in (workspace.get('datasources', []) if workspace else [])]
            if datasource_id:
                ds_ids.append(datasource_id)
            for ds_id in set(ds_ids):
                # Check common tables for policies
                conn_tmp = None
                try:
                    from backend.common.db.metadata_db import get_metadata_conn
                    conn_tmp = get_metadata_conn()
                    with conn_tmp.cursor() as cur:
                        cur.execute(
                            "SELECT DISTINCT table_name FROM adh_rls_policies WHERE datasource_id = %s AND is_active = 1",
                            (ds_id,)
                        )
                        tables = [r["table_name"] for r in cur.fetchall()]
                finally:
                    if conn_tmp:
                        conn_tmp.close()

                for tname in tables:
                    policies = rls_service.get_effective_policies(user_id, workspace_id, ds_id, tname)
                    for col in policies.get("hidden_columns", []):
                        rls_hidden.add(f"{tname}.{col}")
                    for col in policies.get("masked_columns", {}):
                        rls_masked.add(f"{tname}.{col}")

            if rls_hidden or rls_masked:
                rls_context = "\n## 数据安全约束（必须严格遵守）\n"
                if rls_hidden:
                    rls_context += f"- 以下列已被隐藏，禁止在 SQL 中使用: {', '.join(sorted(rls_hidden))}\n"
                if rls_masked:
                    rls_context += f"- 以下列需要脱敏处理，查询时请勿暴露原始值: {', '.join(sorted(rls_masked))}\n"
                workspace_context += rls_context
        except Exception as e:
            logger.debug("RLS context injection skipped: %s", e)

    system_prompt = _build_agent_system_prompt(
        engine=engine,
        datasource_name=ds_name,
        question=question,
        prev_context=prev_context,
        tools_listing=tools_listing,
        agent_graph=agent_graph,
        workspace_context=workspace_context,
    )

    # Inject task context into system prompt (scheduled task background info)
    if context:
        system_prompt = f"{system_prompt}\n\n## 任务背景\n\n{context}"

    # 6. Build initial messages
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for msg in history[-4:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")[:300]
            if content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    # ── Main loop state ──
    all_tool_calls = []
    final_reply = ""
    final_sql = ""
    final_result = None
    total_tokens = {"input": 0, "output": 0}
    all_text_parts = []
    confirmed_context = {}  # User-confirmed info via ask_user
    agent_context = {       # Passed to hooks
        "question": question,
    }

    yield "progress", {"stage": "agent_start", "message": "开始推理", "mode": "agent"}

    model_config = _get_model_config(model_id)

    # Loop detection: track thinking content to detect repetitive patterns
    thinking_history = []
    REPEAT_THRESHOLD = 3  # If same pattern repeats 3 times, force break
    loop_detected = False

    # Doom loop detection: track recent tool call signatures
    recent_tool_signatures = []  # List of (tool_name, input_hash) tuples
    doom_loop_count = 0

    # Failure pattern detection: track consecutive SQL failures
    recent_sql_failures = []  # List of (sql_hash, error_msg) tuples
    MAX_CONSECUTIVE_FAILURES = 2

    # Get context window from model config (default 180K if not configured)
    context_window = model_config.get("context_window") or MAX_CONTEXT_TOKENS
    if context_window and context_window != MAX_CONTEXT_TOKENS:
        logger.info("[Agent] Using model context_window=%d (default=%d)", context_window, MAX_CONTEXT_TOKENS)

    for iteration in range(MAX_ITERATIONS):
        if loop_detected:
            break
        # ── Token budget check & auto-compact ──
        est_tokens = _estimate_messages_tokens(messages)
        if est_tokens > context_window:
            logger.info("[Agent] Context %d tokens exceeds %d (context_window), auto-compacting",
                        est_tokens, context_window)
            summary = _build_compaction_summary(
                messages, question, confirmed_context, all_tool_calls,
            )
            keep_start = max(1, len(messages) - COMPACT_KEEP_RECENT)
            compacted_messages = [
                messages[0],  # system prompt
                {"role": "user", "content": summary},
            ] + messages[keep_start:]
            messages = compacted_messages
            yield "progress", {
                "stage": "agent_compact",
                "message": f"上下文已压缩（原 {est_tokens} tokens）",
                "mode": "agent",
            }

        # ── Streaming LLM call ──
        yield "progress", {
            "stage": "agent_think",
            "message": "推理中...",
            "iteration": iteration + 1,
            "mode": "agent",
        }

        t_llm = time.time()
        text_parts = []
        tool_calls_this_round = []

        # generate_with_tools_stream is a sync generator — drain in thread
        # Use stdlib queue.Queue (thread-safe) since drain runs in a thread
        import queue as _queue
        _q = _queue.Queue()

        def _drain(gen, q):
            try:
                for ev in gen:
                    q.put(ev)
            except Exception as e:
                # Put error marker so the consumer doesn't hang forever
                q.put(("error", str(e)))
            finally:
                q.put(None)  # sentinel

        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, _drain,
            generate_with_tools_stream(messages, tools, model_id=model_id),
            _q,
        )

        while True:
            event = await loop.run_in_executor(None, _q.get)
            if event is None:
                break
            event_type, data = event
            if event_type == "token":
                all_text_parts.append(data)
                text_parts.append(data)
                # Buffer tokens — only yield after we know there are no tool calls
            elif event_type == "thinking":
                yield "thinking", {"text": data}
                # Loop detection: check for repetitive thinking patterns
                thinking_history.append(data)
                if len(thinking_history) > 10:
                    # Check if the last few thinking chunks are repetitive
                    recent = thinking_history[-10:]
                    # Simple check: if the same text appears multiple times
                    from collections import Counter
                    text_counts = Counter(recent)
                    for text, count in text_counts.items():
                        if count >= REPEAT_THRESHOLD and len(text) > 50:
                            logger.warning("[Agent] Detected repetitive thinking pattern, forcing break")
                            yield "token", {"text": "\n\n[检测到推理循环，已自动终止]"}
                            loop_detected = True
                            break
            elif event_type == "tool_use":
                tool_calls_this_round.append(data)
            elif event_type == "done":
                total_tokens["input"] += data.get("input", 0)
                total_tokens["output"] += data.get("output", 0)
            elif event_type == "error":
                logger.error("[Agent] LLM streaming error: %s", data)
                yield "error", {"message": f"LLM 调用失败: {data}"}
                return

        llm_elapsed = round(time.time() - t_llm, 2)

        # ── No tool calls → LLM is done, yield buffered text ──
        if not tool_calls_this_round:
            final_reply = "".join(text_parts).strip()
            # Only keep this round's text as the final reply (not all_text_parts)
            if final_reply:
                yield "token", {"text": final_reply}
            break

        # ── Has tool calls → execute each ──
        # Separate agent calls from tool calls for display
        agent_calls = [tc for tc in tool_calls_this_round if tc["name"].startswith("agent__")]
        other_calls = [tc for tc in tool_calls_this_round if not tc["name"].startswith("agent__")]

        if agent_calls and other_calls:
            agent_names = [tc["name"].replace("agent__", "") for tc in agent_calls]
            tool_names = [f"{tc['name']}({_brief_args(tc['input'])})" for tc in other_calls]
            msg = f"调度子Agent: {', '.join(agent_names)}，调用工具: {', '.join(tool_names)}"
        elif agent_calls:
            agent_names = [tc["name"].replace("agent__", "") for tc in agent_calls]
            msg = f"调度子Agent: {', '.join(agent_names)}"
        else:
            tool_names = [f"{tc['name']}({_brief_args(tc['input'])})" for tc in other_calls]
            msg = f"调用 {len(other_calls)} 个工具: {', '.join(tool_names)}"

        yield "progress", {
            "stage": "agent_decide",
            "message": msg,
            "iteration": iteration + 1,
            "mode": "agent",
        }

        # ── Separate agent calls from other calls ──
        agent_calls = [tc for tc in tool_calls_this_round if tc["name"].startswith("agent__")]
        other_calls = [tc for tc in tool_calls_this_round if not tc["name"].startswith("agent__")]

        # ── Helper: execute a single tool call ──
        async def _execute_one_tool(tc: dict) -> tuple[dict, str, float]:
            """Execute one tool call, return (tc, tool_result, elapsed)."""
            tool_name = tc["name"]
            tool_input = tc["input"]
            t_tool = time.time()

            tool_input = await _pre_tool_use_hook(tool_name, tool_input, agent_context)
            tc["input"] = tool_input  # update in case hook modified it

            # System tools include both SYSTEM_TOOLS and ORCHESTRATOR_TOOLS (think, ask_user, propose_code)
            all_system_tool_names = {t["name"] for t in SYSTEM_TOOLS} | {t["name"] for t in ORCHESTRATOR_TOOLS}
            if tool_name in all_system_tool_names:
                result = await _execute_system_tool(
                    tool_name, tool_input,
                    datasource_id, model_id, user_id, username,
                    question=question,
                    allowed_datasource_ids=allowed_datasource_ids,
                )
            elif tool_name in agent_tools_map:
                agent = agent_tools_map[tool_name]
                try:
                    # Sub-agent timeout: must be longer than agent's own max_time_seconds
                    AGENT_TIMEOUT = getattr(agent, 'max_time_seconds', 120) + 30
                    try:
                        agent_result = await asyncio.wait_for(
                            agent.run(
                                question=tool_input.get("question", question),
                                history=history,
                                datasource_id=datasource_id,
                                model_id=model_id,
                                user_id=user_id,
                                username=username,
                                max_iterations=max_iterations,
                            ),
                            timeout=AGENT_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[Orchestrator] Agent %s timed out after %ds, cancelling", tool_name, AGENT_TIMEOUT)
                        await agent.cancel()
                        agent_result = AgentResult(
                            success=False,
                            reply=f"Agent {tool_name} 执行超时",
                            error="timeout",
                            retryable=True,
                            agent_name=agent.name,
                        )
                    result = json.dumps({
                        "success": agent_result.success,
                        "reply": agent_result.reply,
                        "sql": agent_result.sql,
                        "data": agent_result.data,
                        "error": agent_result.error,
                        "retryable": agent_result.retryable,
                        "retry_count": agent_result.retry_count,
                        "tool_calls": agent_result.tool_calls,
                    }, ensure_ascii=False, default=str)
                except Exception as e:
                    result = json.dumps({"error": str(e), "retryable": False})
            else:
                try:
                    result = await mcp_caller.call(tool_name, tool_input)
                    if not isinstance(result, str):
                        result = json.dumps(result, ensure_ascii=False, default=str)
                except Exception as e:
                    result = json.dumps({"error": str(e)})

            elapsed = round(time.time() - t_tool, 2)
            return tc, result, elapsed

        # ── Execute tools: agents in parallel, others serial ──
        # Progress events for all calls
        for i, tc in enumerate(tool_calls_this_round):
            step_num = len(all_tool_calls) + i + 1
            args_desc = _brief_args(tc["input"])
            yield "progress", {
                "stage": "agent_exec",
                "message": f"{tc['name']}({args_desc})",
                "step": step_num,
                "iteration": iteration + 1,
                "mode": "agent",
            }

        # Run agent calls in parallel, other calls serial
        executed_results = {}  # tool_id -> (tc, result, elapsed)

        if agent_calls and len(agent_calls) > 1:
            # Multiple agent calls → parallel execution
            logger.info("[Orchestrator] Parallel execution: %d agents: %s",
                        len(agent_calls), [tc["name"] for tc in agent_calls])
            parallel_results = await asyncio.gather(
                *[_execute_one_tool(tc) for tc in agent_calls],
                return_exceptions=True,
            )
            for res in parallel_results:
                if isinstance(res, Exception):
                    logger.error("[Orchestrator] Parallel agent call failed: %s", res)
                    continue
                tc, result, elapsed = res
                executed_results[tc["id"]] = (tc, result, elapsed)
        else:
            # Single agent or no agents → serial
            for tc in agent_calls:
                tc, result, elapsed = await _execute_one_tool(tc)
                executed_results[tc["id"]] = (tc, result, elapsed)

        # Other calls always serial
        for tc in other_calls:
            tc, result, elapsed = await _execute_one_tool(tc)
            executed_results[tc["id"]] = (tc, result, elapsed)

        # ── Build tool_results_list in original order ──
        tool_results_list = []
        for tc in tool_calls_this_round:
            tc, tool_result, tool_elapsed = executed_results.get(tc["id"], (tc, "{}", 0))
            tool_name = tc["name"]
            tool_input = tc["input"]
            tool_id = tc["id"]
            step_num = len(all_tool_calls) + 1

            # ── Failure Pattern Detection (execute_sql) ──
            if tool_name == "execute_sql":
                try:
                    result_data = json.loads(tool_result)
                    if result_data.get("error"):
                        import hashlib as _hashlib
                        sql_hash = _hashlib.md5(tool_input.get("sql", "").encode()).hexdigest()[:8]
                        recent_sql_failures.append((sql_hash, result_data["error"][:100]))

                        # Check for consecutive failures with same SQL
                        if len(recent_sql_failures) >= MAX_CONSECUTIVE_FAILURES:
                            last_failures = recent_sql_failures[-MAX_CONSECUTIVE_FAILURES:]
                            if len(set(f[0] for f in last_failures)) == 1:
                                # Same SQL failed multiple times
                                yield "token", {"text": f"[SQL 连续失败 {MAX_CONSECUTIVE_FAILURES} 次，已自动终止。请检查表名和字段名是否正确。]"}
                                loop_detected = True
                                break
                    else:
                        recent_sql_failures.clear()  # Reset on success
                except (json.JSONDecodeError, TypeError):
                    pass

            # ── PostToolUse Hook (truncation + tracking) ──
            tool_result = await _post_tool_use_hook(tool_name, tool_input, tool_result, agent_context)

            # ── Record tool call ──
            all_tool_calls.append({
                "step": step_num,
                "tool": tool_name,
                "arguments": tool_input,
                "result": str(tool_result)[:2000],
                "result_preview": str(tool_result)[:200],
                "elapsed": tool_elapsed,
            })

            # ── Doom Loop Detection ──
            # Create a signature for this tool call
            import hashlib
            input_str = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
            input_hash = hashlib.md5(input_str.encode()).hexdigest()[:8]
            sig = (tool_name, input_hash)

            recent_tool_signatures.append(sig)
            if len(recent_tool_signatures) > DOOM_LOOP_WINDOW:
                recent_tool_signatures.pop(0)

            # Check for consecutive identical calls
            if len(recent_tool_signatures) >= DOOM_LOOP_THRESHOLD:
                last_n = recent_tool_signatures[-DOOM_LOOP_THRESHOLD:]
                if len(set(last_n)) == 1:  # All identical
                    doom_loop_count += 1
                    logger.warning(
                        "[Agent] Doom loop detected: %s called %d times with same input",
                        tool_name, DOOM_LOOP_THRESHOLD,
                    )
                    if doom_loop_count >= 2:  # Second detection = force break
                        yield "token", {"text": f"\n\n[检测到工具调用循环 ({tool_name} 重复 {DOOM_LOOP_THRESHOLD} 次)，已自动终止]"}
                        loop_detected = True
                        break
                    else:
                        # First detection: inject warning into context
                        yield "progress", {
                            "stage": "agent_warning",
                            "message": f"⚠️ 检测到重复调用 {tool_name}，请尝试不同方法",
                            "mode": "agent",
                        }

            result_brief = _brief_result(tool_name, tool_result)
            yield "progress", {
                "stage": "agent_exec_done",
                "message": f"{tool_name} → {result_brief}",
                "step": step_num,
                "elapsed": tool_elapsed,
                "mode": "agent",
            }

            # ── Handle ask_user ──
            if tool_name == "ask_user":
                if disable_ask_user:
                    # No user interaction available (e.g. scheduled task)
                    # Return the question as an error so the agent can work around it
                    tool_result = json.dumps({
                        "error": "无法与用户交互（定时任务模式）。请基于已有信息自行判断并继续执行，或直接报告无法完成的原因。",
                        "question": parsed_result.get("question", "") if 'parsed_result' in dir() else "",
                    }, ensure_ascii=False)
                else:
                    try:
                        parsed_result = json.loads(tool_result)
                        if parsed_result.get("__ask_user__"):
                            ask_id = str(uuid.uuid4())[:8]
                            yield "ask_user", {
                                "request_id": ask_id,
                                "question": parsed_result["question"],
                                "options": parsed_result.get("options", []),
                            }
                            try:
                                user_response = await _wait_for_user_response(ask_id)
                            except AgentCancelledError:
                                logger.info("[Agent] User cancelled via ask_user, request_id=%s", ask_id)
                                yield "token", {"text": "🚫 任务已取消"}
                                yield "done", {
                                    "success": False,
                                    "reply": "🚫 任务已取消",
                                    "sql": final_sql,
                                    "error": "cancelled",
                                    "retryable": False,
                                    "mode": "agent",
                                }
                                return
                            # Record confirmed info
                            confirmed_context[parsed_result["question"]] = user_response
                            tool_result = json.dumps({
                                "user_response": user_response,
                            }, ensure_ascii=False)
                    except (json.JSONDecodeError, KeyError):
                        pass

            # ── Handle propose_code ──
            elif tool_name == "propose_code":
                try:
                    parsed_result = json.loads(tool_result)
                    if parsed_result.get("__propose_code__"):
                        # Code proposal — send to frontend for confirmation
                        code_id = str(uuid.uuid4())[:8]
                        code_content = parsed_result["code"]
                        code_desc = parsed_result.get("description", "")
                        code_reqs = parsed_result.get("requirements", [])
                        yield "propose_code", {
                            "request_id": code_id,
                            "code": code_content,
                            "description": code_desc,
                            "requirements": code_reqs,
                        }
                        try:
                            user_response = await _wait_for_user_response(code_id)
                        except AgentCancelledError:
                            logger.info("[Agent] User cancelled code execution, code_id=%s", code_id)
                            yield "token", {"text": "🚫 代码执行已取消"}
                            tool_result = json.dumps({
                                "cancelled": True,
                                "message": "用户取消了代码执行。请尝试用其他方式完成分析。",
                            }, ensure_ascii=False)
                        else:
                            if user_response == "__EXECUTE__":
                                # Execute code in sandbox
                                from backend.services.sandbox_service import sandbox_service
                                from backend.services.sandbox_executor import SandboxExecutor
                                # Resolve sandbox: workspace config > system default
                                sandbox = None
                                if workspace and workspace.get('config'):
                                    ws_sandbox_id = workspace['config'].get('sandbox_id')
                                    if ws_sandbox_id:
                                        sandbox = sandbox_service.get_sandbox(ws_sandbox_id)
                                        if sandbox:
                                            logger.info("[Agent] Using workspace sandbox: %s (id=%d)",
                                                        sandbox.get('name'), ws_sandbox_id)
                                if not sandbox:
                                    sandbox = sandbox_service.get_default_sandbox()
                                if not sandbox:
                                    tool_result = json.dumps({
                                        "success": False,
                                        "error": "未配置默认沙箱，无法执行代码。请在系统管理或工作空间配置中设置沙箱环境。",
                                    }, ensure_ascii=False)
                                    yield "execution_result", {
                                        "code": code_content,
                                        "description": code_desc,
                                        "requirements": code_reqs,
                                        "sandbox_name": "无",
                                        "success": False,
                                        "error": "未配置沙箱",
                                        "stdout": "",
                                        "stderr": "",
                                    }
                                else:
                                    executor = SandboxExecutor(sandbox)
                                    exec_result = await asyncio.get_event_loop().run_in_executor(
                                        None,
                                        lambda: executor.execute(
                                            code=code_content,
                                            requirements=code_reqs,
                                        ),
                                    )
                                    exec_result["code"] = code_content
                                    exec_result["description"] = code_desc
                                    exec_result["requirements"] = code_reqs
                                    exec_result["sandbox_name"] = sandbox.get("name", "unknown")
                                    tool_result = json.dumps(exec_result, ensure_ascii=False)
                                    yield "execution_result", exec_result
                            else:
                                tool_result = json.dumps({
                                    "cancelled": True,
                                    "message": f"用户拒绝了代码执行: {user_response}",
                                }, ensure_ascii=False)
                except (json.JSONDecodeError, KeyError):
                    pass

            # ── Immediate Compaction Check ──
            # If a tool result is very large, trigger compaction before next iteration
            if len(tool_result) > 15000:  # > 15KB
                est_tokens = _estimate_messages_tokens(messages)
                if est_tokens > context_window * 0.7:  # > 70% of context window
                    logger.warning(
                        "[Agent] Large tool result (%d chars) + context at %d tokens, triggering immediate compaction",
                        len(tool_result), est_tokens,
                    )
                    summary = _build_compaction_summary(
                        messages, question, confirmed_context, all_tool_calls,
                    )
                    keep_start = max(1, len(messages) - COMPACT_KEEP_RECENT)
                    compacted_messages = [
                        messages[0],  # system prompt
                        {"role": "user", "content": summary},
                    ] + messages[keep_start:]
                    messages = compacted_messages
                    yield "progress", {
                        "stage": "agent_compact",
                        "message": f"上下文已压缩（大结果触发）",
                        "mode": "agent",
                    }

            # ── Track SQL ──
            if tool_name == "execute_sql" and "sql" in tool_input:
                final_sql = tool_input["sql"]
                try:
                    final_result = json.loads(tool_result)
                except Exception:
                    pass

            if tool_name == "generate_sql":
                try:
                    gen_parsed = json.loads(tool_result)
                    if gen_parsed.get("success") and gen_parsed.get("sql"):
                        final_sql = gen_parsed["sql"]
                except (json.JSONDecodeError, TypeError):
                    pass

            tool_results_list.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": tool_result,
            })

        # ── Update messages (no scratchpad) ──
        # Build assistant message with text + tool_use blocks
        assistant_content = []
        if text_parts:
            assistant_content.append({"type": "text", "text": "".join(text_parts)})
        for tc in tool_calls_this_round:
            assistant_content.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            })
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results_list})

        # ── Last iteration: request summary instead of continuing ──
        if iteration >= MAX_ITERATIONS - 2:
            logger.info("[Agent] Max iterations reached (%d/%d), requesting final summary",
                        iteration + 1, MAX_ITERATIONS)
            # Tool results are already in messages — ask LLM to summarize
            messages.append({
                "role": "user",
                "content": "你已达到最大工具调用次数。请根据已有的工具调用结果，直接给出最终回答。不要再调用任何工具。"
            })
            try:
                from backend.common.llm.llm_client import generate_with_tools_stream
                summary_parts = []
                for ev in generate_with_tools_stream(messages, [], model_id=model_id):
                    if ev[0] == "token":
                        summary_parts.append(ev[1])
                summary_text = "".join(summary_parts).strip()
                if summary_text:
                    final_reply = summary_text
                    yield "token", {"text": final_reply}
            except Exception as e:
                logger.warning("[Agent] Summary generation failed: %s", e)
            break

    elapsed = round(time.time() - t_start, 2)

    # ── Build meaningful final reply ──
    # Don't use all_text_parts — it contains intermediate reasoning
    # final_reply should only come from a clean LLM response (no tool calls)

    warnings = []
    if not final_reply.strip():
        if not final_sql and not final_result:
            # ── Auto-recovery: try generating SQL from collected metadata ──
            metadata_parts = []
            for tc in all_tool_calls:
                if tc["tool"] == "retrieve_metadata" and tc["result_preview"]:
                    metadata_parts.append(tc["result"][:5000])

            if metadata_parts:
                logger.info("[Agent] Auto-recovery: attempting SQL generation with collected metadata")
                try:
                    from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
                    from backend.common.llm.llm_client import generate_sql as llm_generate_sql
                    from backend.rag.rag_retriever import (
                        _get_table_info_for_names, _get_columns_for_tables,
                        retrieve_sql_templates, retrieve_business_terms, retrieve_table_relations,
                    )
                    from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

                    recovered_tables = []
                    for tc in all_tool_calls:
                        if tc["tool"] == "retrieve_metadata":
                            args = tc.get("arguments", {})
                            recovered_tables.extend(args.get("table_names", []))
                    recovered_tables = list(dict.fromkeys(recovered_tables))

                    if recovered_tables:
                        table_info = _get_table_info_for_names(recovered_tables, datasource_id)
                        columns = _get_columns_for_tables(recovered_tables, datasource_id)
                        try:
                            vec = embedding_to_sql_literal(generate_embedding(question))
                        except Exception:
                            vec = ""
                        templates = retrieve_sql_templates(question, 5, vec, datasource_id)
                        terms = retrieve_business_terms(question, 20, vec_literal=vec, datasource_id=datasource_id)
                        relations = retrieve_table_relations(question, 20, recovered_tables, vec, datasource_id)

                        engine_name = engine

                        messages = build_nl2sql_prompt(
                            question=question,
                            table_info=table_info,
                            column_metadata=columns,
                            sql_templates=templates,
                            business_terms=terms,
                            table_relations=relations,
                            engine=engine_name,
                        )
                        llm_result = llm_generate_sql(messages, model_id=model_id)
                        raw_sql = llm_result.get("sql", "")

                        import re as _re
                        text = raw_sql.strip()
                        if text.startswith("```"):
                            lines = text.split("\n")
                            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                        try:
                            parsed = json.loads(text)
                        except json.JSONDecodeError:
                            match = _re.search(r'\{.*\}', text, _re.DOTALL)
                            parsed = json.loads(match.group(0)) if match else {"success": False}

                        if parsed.get("success") and parsed.get("sql"):
                            final_sql = parsed["sql"]
                            logger.info("[Agent] Auto-recovery generated SQL: %s", final_sql[:200])

                            try:
                                from backend.nl2sql.sql.sql_validator import validate_and_fix
                                from backend.nl2sql.sql.query_executor import execute_query as _exec_q
                                final_sql, val_warns = validate_and_fix(final_sql)
                                df, exec_ms, exec_rows = _exec_q(final_sql, datasource_id)
                                cols = list(df.columns) if not df.empty else []
                                rows_data = [_sanitize_row(r) for r in df.head(100).to_dict("records")] if not df.empty else []
                                final_result = {
                                    "columns": cols, "rows": rows_data,
                                    "row_count": exec_rows, "elapsed_ms": exec_ms,
                                }
                                final_reply = f"已自动恢复生成并执行 SQL 查询，返回 {exec_rows} 条结果。"
                                warnings.append("Agent 探索阶段耗尽迭代次数，已自动恢复生成 SQL")
                                logger.info("[Agent] Auto-recovery executed SQL: %d rows", exec_rows)
                            except Exception as exec_err:
                                logger.warning("[Agent] Auto-recovery SQL execution failed: %s", exec_err)
                                final_reply = f"已自动恢复生成 SQL，但执行失败：{exec_err}"
                                warnings.append("SQL 已生成但执行失败")
                except Exception as recover_err:
                    logger.warning("[Agent] Auto-recovery failed: %s", recover_err)

            if not final_sql:
                tool_names = [tc["tool"] for tc in all_tool_calls]
                if tool_names:
                    final_reply = (
                        f"我已经调用了以下工具进行分析：{', '.join(tool_names)}，"
                        "但未能生成有效的查询结果。这可能是因为：\n"
                        "1. 问题涉及的表结构较复杂，元数据不足以生成准确的 SQL\n"
                        "2. 问题表述不够明确，无法确定具体需要查询哪些表\n\n"
                        "请尝试更具体地描述您的查询需求，例如指定表名或关键字段。"
                    )
                else:
                    final_reply = "未能完成查询分析。请尝试切换到快速模式或深度模式。"
                warnings.append("Agent 未生成有效 SQL，请尝试更具体的查询描述")
        elif final_sql and not final_result:
            final_reply = final_reply or f"已生成 SQL 但未执行查询：\n```sql\n{final_sql}\n```"
            warnings.append("SQL 已生成但未执行")

    chart_type = _detect_chart_type(final_result)

    yield "done", {
        "intent": "query",
        "reply": final_reply,
        "sql": final_sql or None,
        "result": final_result,
        "chart_type": chart_type,
        "warnings": warnings,
        "timings": {"total": elapsed},
        "tokens": total_tokens,
        "mode": "agent",
        "tool_calls": all_tool_calls,
    }

    # ── MCP cleanup (must be in same task as initialization) ──
    # Only cleanup if we created an MCP caller (not using workspace tools)
    if not workspace_tools:
        try:
            await mcp_caller.close()
        except Exception:
            pass
