"""Loop Engine — Core execution engine for Loop Engineering workflow.

This module implements the main loop logic for the ChatBI query flow:
1. Metadata Retrieval (RAG)
2. LLM Analysis (intent understanding + metadata supplement request)
3. Metadata Supplement (on-demand, loops with step 2)
4. SQL Generation
5. SQL Execution
6. Result Analysis (optional)

The engine respects configurable max rounds and reserved steps.
"""

import json
import logging
import math
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

import pymysql

from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.llm.llm_client import (
    generate_sql, generate_sql_stream,
    async_generate_sql, async_generate_with_tools,
)
from services.datamind.rag.rag_retriever import retrieve_all, retrieve_tables_metadata, retrieve_with_strategy
from services.datamind.nl2sql.intent.query_rewriter import rewrite_query
from services.datamind.nl2sql.prompt.prompt_builder import build_nl2sql_prompt, build_nl2sql_prompt_with_supplement
from services.datamind.nl2sql.sql.sql_validator import validate_and_fix
from services.datamind.nl2sql.sql.query_executor import execute_query
from services.datamind.nl2sql.intent.intent_classifier import classify_intent
from services.datamind.rag.table_selector import select_tables

logger = logging.getLogger(__name__)


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id():
    return int(time.time() * 1000000)


# ════════════════════════════════════════════════════════════════════
# Workflow Configuration Loader
# ════════════════════════════════════════════════════════════════════

def load_workflow_config(workflow_id: Optional[int] = None, conn=None) -> Dict[str, Any]:
    """Load workflow configuration from database.

    Args:
        workflow_id: Specific workflow ID, or None for default workflow.
        conn: Optional pymysql connection to reuse. If None, creates a new one.

    Returns:
        Workflow configuration dict with steps.
    """
    _own_conn = conn is None
    if _own_conn:
        conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workflow_id:
                cur.execute(
                    "SELECT id, name, description, max_rounds, is_active "
                    "FROM adh_workflow_configs WHERE id = %s AND is_active = 1",
                    (workflow_id,),
                )
            else:
                cur.execute(
                    "SELECT id, name, description, max_rounds, is_active "
                    "FROM adh_workflow_configs WHERE is_default = 1 AND is_active = 1",
                )
            workflow = cur.fetchone()
            if not workflow:
                # Fallback to hardcoded defaults
                return _get_default_workflow()

            # Load steps
            cur.execute(
                "SELECT id, step_type, step_name, step_order, max_rounds, "
                "is_enabled, prompt_key, config "
                "FROM adh_workflow_steps WHERE workflow_id = %s ORDER BY step_order",
                (workflow["id"],),
            )
            steps = cur.fetchall()
            for step in steps:
                step["is_enabled"] = bool(step.get("is_enabled"))
                if step.get("config"):
                    try:
                        step["config"] = json.loads(step["config"])
                    except:
                        step["config"] = {}

            workflow["steps"] = steps
            return workflow
    finally:
        if _own_conn:
            conn.close()


def _get_default_workflow() -> Dict[str, Any]:
    """Return hardcoded default workflow configuration."""
    return {
        "id": 0,
        "name": "默认工作流",
        "description": "默认的NL2SQL问数流程",
        "steps": [
            {
                "id": 1, "step_type": "metadata_retrieval", "step_name": "元数据检索",
                "step_order": 1, "max_rounds": 1, "is_enabled": True,
                "prompt_key": None, "config": {"source": "rag"}
            },
            {
                "id": 2, "step_type": "llm_analysis", "step_name": "LLM意图分析",
                "step_order": 2, "max_rounds": 3, "is_enabled": True,
                "prompt_key": "metadata_supplement", "config": {}
            },
            {
                "id": 3, "step_type": "metadata_supplement", "step_name": "元数据补充",
                "step_order": 3, "max_rounds": 3, "is_enabled": True,
                "prompt_key": None, "config": {"mode": "on_demand"}
            },
            {
                "id": 4, "step_type": "sql_generation", "step_name": "SQL生成",
                "step_order": 4, "max_rounds": 1, "is_enabled": True,
                "prompt_key": "sql_generation", "config": {}
            },
            {
                "id": 5, "step_type": "sql_execution", "step_name": "SQL执行",
                "step_order": 5, "max_rounds": 1, "is_enabled": True,
                "prompt_key": None, "config": {}
            },
            {
                "id": 6, "step_type": "result_analysis", "step_name": "结果分析",
                "step_order": 6, "max_rounds": 1, "is_enabled": True,
                "prompt_key": "result_analysis", "config": {"optional": True}
            },
        ]
    }


# ════════════════════════════════════════════════════════════════════
# Prompt Loader
# ════════════════════════════════════════════════════════════════════

def load_prompt(prompt_key: str, conn=None) -> Optional[Dict[str, str]]:
    """Load prompt from database by key.

    Args:
        prompt_key: The prompt key to look up.
        conn: Optional pymysql connection to reuse. If None, creates a new one.

    Returns:
        Dict with 'system_prompt' and 'user_prompt_template', or None.
    """
    _own_conn = conn is None
    if _own_conn:
        conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT system_prompt, user_prompt_template "
                "FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "system_prompt": row.get("system_prompt", ""),
                    "user_prompt_template": row.get("user_prompt_template", ""),
                }
            return None
    finally:
        if _own_conn:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# Loop Calculation
# ════════════════════════════════════════════════════════════════════

def get_step_max_rounds(workflow: Dict[str, Any], step_type: str) -> int:
    """Get max rounds for a specific step type from workflow config.

    Args:
        workflow: Workflow configuration dict.
        step_type: Step type to query.

    Returns:
        Max rounds configured for the step, or 1 if not found.
    """
    steps = workflow.get("steps", [])
    for step in steps:
        if step["step_type"] == step_type and step.get("is_enabled", True):
            return step.get("max_rounds", 1)
    return 1


# ════════════════════════════════════════════════════════════════════
# Execution Log
# ════════════════════════════════════════════════════════════════════

def create_execution_log(
    workflow_id: int,
    workflow_name: str,
    session_id: str,
    user_id: Optional[int],
    username: Optional[str],
    question: str,
    conn=None,
) -> int:
    """Create a new workflow execution log entry."""
    _own_conn = conn is None
    if _own_conn:
        conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            log_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_workflow_logs "
                "(id, workflow_id, workflow_name, session_id, user_id, username, "
                "question, current_step, current_round, "
                "status, started_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 'running', %s)",
                (log_id, workflow_id, workflow_name, session_id, user_id, username,
                 question, "metadata_retrieval", _now()),
            )
        conn.commit()
        return log_id
    finally:
        if _own_conn:
            conn.close()


def update_execution_log(
    log_id: int,
    conn=None,
    **kwargs
):
    """Update workflow execution log with partial data."""
    _own_conn = conn is None
    if _own_conn:
        conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Build dynamic update
            fields = []
            params = []
            for key, value in kwargs.items():
                if value is not None:
                    if key in ("metadata_context", "metadata_requested", "metadata_supplemented",
                               "execution_result", "llm_analysis", "analysis_result"):
                        value = json.dumps(value, ensure_ascii=False)
                    fields.append(f"{key} = %s")
                    params.append(value)

            if not fields:
                return

            params.append(log_id)
            sql = f"UPDATE adh_workflow_logs SET {', '.join(fields)} WHERE id = %s"
            cur.execute(sql, params)
        conn.commit()
    finally:
        if _own_conn:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# Metadata Supplement Analysis
# ════════════════════════════════════════════════════════════════════

def _repair_truncated_json(content: str) -> str:
    """Attempt to repair a truncated JSON string from LLM output.

    Handles common truncation patterns:
    - Unterminated string values
    - Missing closing braces/brackets
    """
    content = content.strip()

    # If it's already valid, return as-is
    try:
        json.loads(content)
        return content
    except json.JSONDecodeError:
        pass

    # Strategy: try to close open strings and braces
    # Count open braces/brackets
    open_braces = 0
    open_brackets = 0
    in_string = False
    escape_next = False
    last_key = None  # Track if we're mid-value

    for i, ch in enumerate(content):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1

    # If we're inside a string, close it and add a default value
    if in_string:
        content += '"'

    # If the last character is a colon or comma, add a default value
    stripped = content.rstrip()
    if stripped.endswith(':'):
        content += '""'
    elif stripped.endswith(','):
        # Remove trailing comma and close
        content = content.rstrip().rstrip(',')

    # Close any open brackets/braces
    content += ']' * open_brackets
    content += '}' * open_braces

    # Verify the repair worked
    try:
        json.loads(content)
        return content
    except json.JSONDecodeError:
        # Last resort: extract just the need_more field
        if '"need_more"' in content:
            if '"need_more": false' in content or '"need_more":false' in content:
                return '{"need_more": false, "reason": "元数据充足", "analysis": "LLM响应截断，假设元数据充足"}'
            else:
                return '{"need_more": true, "reason": "需要补充元数据", "analysis": "LLM响应截断"}'
        return '{"need_more": false, "reason": "解析失败", "analysis": "LLM响应无法解析"}'


def _summarize_metadata(current_metadata: Dict[str, Any]) -> str:
    """Convert metadata dict to a compact summary string for LLM analysis.

    Instead of dumping the full JSON (which can be thousands of tokens for
    many tables/columns), produce a human-readable summary that gives the LLM
    enough context to decide whether more metadata is needed.

    Example output:
        已有表(3):
        - t_user_customer: 用户信息表 (id, name, email, phone, ...共15列)
        - t_orders: 订单表 (order_id, user_id, amount, ...共8列)
        - t_products: 产品表 (product_id, product_name, price, ...共6列)
        业务术语(2): 用户→t_user_customer, 订单→t_orders
        表关系(1): t_user_customer.id → t_orders.user_id (1:N)
        SQL模板(2): 查询用户订单, 统计订单金额
    """
    lines = []

    # Tables
    table_info = current_metadata.get("table_info", [])
    lines.append(f"已有表({len(table_info)}):")
    for t in table_info:
        name = t.get("table_name", "?")
        comment = t.get("table_comment", "")
        desc = t.get("table_business_desc", "")
        label = comment or desc or ""
        # Find columns for this table (no truncation — let retrieval strategy decide)
        cols = [
            c for c in current_metadata.get("column_metadata", [])
            if c.get("table_name") == name
        ]
        col_names = [c.get("column_name", "?") for c in cols]
        col_preview = ", ".join(col_names)
        suffix = f" ({label})" if label else ""
        lines.append(f"  - {name}{suffix}: {col_preview}" if col_preview else f"  - {name}{suffix}")

    # Business terms
    terms = current_metadata.get("business_terms", [])
    if terms:
        term_items = []
        for t in terms:
            cn = t.get("term_cn", "?")
            target = t.get("target_table", "")
            term_items.append(f"{cn}→{target}" if target else cn)
        lines.append(f"业务术语({len(terms)}): {', '.join(term_items)}")

    # Table relations
    relations = current_metadata.get("table_relations", [])
    if relations:
        rel_items = []
        for r in relations:
            src = r.get("source_table", "?")
            src_col = r.get("source_column", "?")
            tgt = r.get("target_table", "?")
            tgt_col = r.get("target_column", "?")
            rel_type = r.get("relation_type", "1:N")
            rel_items.append(f"{src}.{src_col} → {tgt}.{tgt_col} ({rel_type})")
        lines.append(f"表关系({len(relations)}): {'; '.join(rel_items)}")

    # SQL templates
    templates = current_metadata.get("sql_templates", [])
    if templates:
        tpl_names = [t.get("template_name", "?") for t in templates[:5]]
        lines.append(f"SQL模板({len(templates)}): {', '.join(tpl_names)}")

    return "\n".join(lines)


def analyze_metadata_need(
    question: str,
    current_metadata: Dict[str, Any],
    prompt_key: str = "metadata_supplement",
    conn=None,
) -> Dict[str, Any]:
    """Analyze if more metadata is needed using LLM.

    Args:
        question: User's question.
        current_metadata: Current metadata context (tables, columns, etc.).
        conn: Optional pymysql connection to reuse for prompt loading.
        prompt_key: Key to load the analysis prompt.

    Returns:
        Dict with 'need_more', 'reason', 'analysis', 'required_tables', 'required_columns'.
    """
    # Load prompt from database
    prompt = load_prompt(prompt_key, conn=conn)
    if not prompt:
        # Fallback to default prompt
        system_prompt = """你是ChatBI数据分析助手的元数据分析模块。你的任务是分析用户问题，判断当前已提供的元数据是否足够生成准确的SQL。

<当前已有的元数据>
{current_metadata}
</当前已有的元数据>

<分析规则>
1. 仔细分析用户问题，识别需要查询的表、字段、关联关系
2. 对比已有元数据，评估信息是否充足
3. 如果元数据充足，返回：{"need_more": false, "reason": "元数据充足", "analysis": "简要分析"}
4. 如果需要补充元数据，返回：{"need_more": true, "required_tables": ["表名"], "required_columns": [{"table": "表名", "columns": ["字段"]}], "reason": "原因", "analysis": "分析"}
5. 优先请求关键表和核心字段，避免过度请求
6. 如果无法理解问题，返回：{"need_more": false, "reason": "无法理解", "analysis": "分析", "need_clarify": true}
</分析规则>

<输出格式>必须返回合法的JSON对象，不要包含任何其他文字。</输出格式>"""
        user_prompt_template = "用户问题: {question}\n\n请分析当前元数据是否足够生成准确的SQL。"
    else:
        system_prompt = prompt["system_prompt"]
        user_prompt_template = prompt["user_prompt_template"]

    # Format metadata context — use compact summary instead of full JSON
    metadata_str = _summarize_metadata(current_metadata)

    # Build messages
    system_content = system_prompt.replace("{current_metadata}", metadata_str)
    user_content = user_prompt_template.replace("{question}", question)

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    try:
        llm_result = generate_sql(messages=messages)
        content = llm_result.get("sql", "").strip()

        # Try to parse JSON response
        # Handle case where LLM might include markdown code blocks
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Try to repair truncated JSON
            repaired = _repair_truncated_json(content)
            parsed = json.loads(repaired)
        parsed["_llm_result"] = llm_result  # Attach raw result for token/thinking tracking
        return parsed
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response as JSON: %s, content: %s", e, content[:200])
        return {
            "need_more": False,
            "reason": "LLM返回格式错误，跳过元数据补充",
            "analysis": content[:500],
            "error": True,
        }
    except Exception as e:
        logger.error("Error in metadata analysis: %s", e)
        return {
            "need_more": False,
            "reason": f"分析出错: {str(e)}",
            "analysis": "",
            "error": True,
        }


# ════════════════════════════════════════════════════════════════════
# Result Analysis
# ════════════════════════════════════════════════════════════════════

def analyze_result(
    question: str,
    query_result: Dict[str, Any],
    column_metadata: list = None,
    prompt_key: str = "result_analysis",
    conn=None,
) -> Dict[str, Any]:
    """Analyze query result with column metadata for enum mapping.

    Args:
        question: User's question.
        query_result: Query execution result.
        column_metadata: Column metadata with descriptions and enums.
        prompt_key: Key to load the analysis prompt.

    Returns:
        Dict with 'need_analysis', 'summary', 'insights', 'suggestions'.
    """
    # Build column description context
    col_desc_text = ""
    if column_metadata:
        col_desc_lines = []
        for col in column_metadata:
            parts = [f"{col.get('table_name', '')}.{col.get('column_name', '')}"]
            if col.get('column_comment'):
                parts.append(f"说明: {col['column_comment']}")
            if col.get('business_desc'):
                parts.append(f"业务描述: {col['business_desc']}")
            col_desc_lines.append(" | ".join(parts))
        if col_desc_lines:
            col_desc_text = "\n".join(col_desc_lines)

    # Load prompt from database
    prompt = load_prompt(prompt_key, conn=conn)
    if not prompt:
        system_prompt = """你是ChatBI数据分析助手。你的任务是对查询结果进行深入分析。

<分析方向>
1. 数据趋势分析：识别数据的变化趋势
2. 异常检测：发现异常值或异常模式
3. 对比分析：对比不同维度的数据差异
4. 关联分析：发现数据之间的关联关系
5. 预测建议：基于历史数据给出预测性建议
</分析方向>

<输出格式>
以JSON格式返回：
{"need_analysis": true/false, "summary": "简要总结", "insights": ["洞察1", "洞察2"], "suggestions": ["建议1", "建议2"]}

如果不需要二次分析，返回：
{"need_analysis": false, "summary": "直接总结查询结果"}
</输出格式>

<特殊场景>
如果查询结果包含枚举值或编码，且字段元数据中有对应的描述说明，请在summary中逐一对应列出。
</特殊场景>"""
        user_prompt_template = "用户问题: {question}\n\n原始查询结果:\n{query_result}\n\n字段元数据:\n{col_desc}\n\n请判断是否需要进行二次分析。如果结果中有枚举值请结合字段说明解读。"
    else:
        system_prompt = prompt["system_prompt"]
        user_prompt_template = prompt["user_prompt_template"]

    # Format query result (sanitize non-serializable types)
    from decimal import Decimal
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return obj
    result_str = json.dumps(_sanitize(query_result), ensure_ascii=False, indent=2)

    # Build messages
    user_content = user_prompt_template.replace("{question}", question)
    user_content = user_content.replace("{query_result}", result_str)
    user_content = user_content.replace("{col_desc}", col_desc_text or "（无字段元数据）")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        llm_result = generate_sql(messages=messages)
        content = llm_result.get("sql", "").strip()

        # Handle markdown code blocks
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(content)
            parsed = json.loads(repaired)
        parsed["_llm_result"] = llm_result
        return parsed
    except json.JSONDecodeError as e:
        logger.error("Failed to parse analysis response: %s", e)
        return {
            "need_analysis": False,
            "reason": "分析结果解析失败",
            "raw_response": content[:500],
        }
    except Exception as e:
        logger.error("Error in result analysis: %s", e)
        return {
            "need_analysis": False,
            "reason": f"分析出错: {str(e)}",
        }


# ════════════════════════════════════════════════════════════════════
# Column Filtering
# ════════════════════════════════════════════════════════════════════

def _filter_columns_by_sql(sql: str, column_metadata: list[dict]) -> list[dict]:
    """Filter column_metadata to only columns referenced in the SQL.

    Reduces LLM context size for result analysis by only passing
    columns that actually appear in the generated SQL.
    """
    if not sql or not column_metadata:
        return column_metadata

    sql_upper = sql.upper()

    # Extract column names from SQL (simple heuristic: match known column names)
    relevant = []
    for col in column_metadata:
        col_name = col.get("column_name", "")
        if col_name and col_name.upper() in sql_upper:
            relevant.append(col)

    # If no match found (e.g., SELECT *), return all
    if not relevant:
        return column_metadata

    return relevant


# ════════════════════════════════════════════════════════════════════
# Main Loop Engine
# ════════════════════════════════════════════════════════════════════

async def execute_loop(
    question: str,
    history: Optional[List[Dict]] = None,
    datasource_id: int = 0,
    model_id: Optional[int] = None,
    workflow_id: Optional[int] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    progress_callback=None,  # Callable[[str, str], None] — (stage, message)
    stream_callback=None,    # Callable[[str, str], None] — (event_type, data) for token streaming
    retrieval_strategy: str = None,
) -> Dict[str, Any]:
    """Execute the Loop Engineering workflow.

    This is the main entry point for the query flow. It orchestrates:
    1. Metadata retrieval
    2. LLM analysis loop (with metadata supplement)
    3. SQL generation
    4. SQL execution
    5. Result analysis (optional)

    Args:
        question: User's natural language question.
        history: Conversation history.
        datasource_id: Target datasource ID.
        model_id: LLM model ID (optional).
        workflow_id: Workflow configuration ID (optional, uses default if None).
        user_id: User ID for logging.
        username: Username for logging.

    Returns:
        Dict with query results, analysis, and execution metadata.
    """
    start_time = time.time()
    session_id = str(uuid.uuid4())

    # Token and thinking tracking
    total_tokens = {"input": 0, "output": 0, "total": 0}
    thinking_texts = []
    step_timings = {}

    def _emit_progress(stage: str, message: str, elapsed: float = None):
        """Call progress callback if provided."""
        if progress_callback:
            try:
                progress_callback(stage, message, elapsed)
            except Exception:
                pass

    def _emit_stream(event_type: str, data: str):
        """Call stream callback if provided (for token/thinking streaming)."""
        if stream_callback:
            try:
                stream_callback(event_type, data)
            except Exception:
                pass

    def _track_llm_result(llm_result: dict):
        """Accumulate tokens and thinking from an LLM call."""
        tokens = llm_result.get("tokens", {})
        total_tokens["input"] += tokens.get("input", 0)
        total_tokens["output"] += tokens.get("output", 0)
        total_tokens["total"] += tokens.get("total", 0)
        thinking = llm_result.get("thinking", "")
        if thinking:
            thinking_texts.append(thinking)

    # Create a single DB connection for the entire workflow execution.
    # All internal functions (load_workflow_config, create_execution_log,
    # update_execution_log, load_prompt) reuse this connection to avoid
    # ~19 separate connect/disconnect cycles per execution.
    conn = _get_metadata_conn()

    try:
        # Step 0: Load workflow configuration
        workflow = load_workflow_config(workflow_id, conn=conn)
        workflow_id = workflow["id"]
        workflow_name = workflow["name"]

        # Get max rounds for each step
        llm_analysis_max_rounds = get_step_max_rounds(workflow, "llm_analysis")
        logger.info("Workflow '%s': llm_analysis_max_rounds=%d",
                    workflow_name, llm_analysis_max_rounds)

        # Create execution log
        log_id = create_execution_log(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            session_id=session_id,
            user_id=user_id,
            username=username,
            question=question,
            conn=conn,
        )
        # Step 1: Initial metadata retrieval (RAG)
        _emit_progress("rag", "正在检索元数据...")
        logger.info("Step 1: Metadata retrieval")
        step_start = time.time()
        update_execution_log(log_id, conn=conn, current_step="metadata_retrieval", current_round=1)

        # Keyword-based table pre-selection (same as default pipeline)
        # Query rewriting: pronoun resolution, time normalization, expansion
        rewrite_result = rewrite_query(question, history=history)
        canonical_query = rewrite_result["canonical_query"]
        if canonical_query != question:
            logger.info("[Loop] Query rewritten: '%s' → '%s'", question[:50], canonical_query[:50])
            yield "progress", {
                "stage": "rewrite",
                "message": f"查询已改写: {canonical_query[:60]}",
                "mode": "loop",
            }
        rag_query = canonical_query

        selected_tables = select_tables(rag_query, top_k=5, datasource_id=datasource_id)
        logger.info("Pre-selected tables: %s", selected_tables)

        rag_context = retrieve_with_strategy(
            question=question,
            selected_tables=selected_tables,
            datasource_id=datasource_id,
            strategy_name=retrieval_strategy,
            model_id=model_id,
        )

        current_metadata = {
            "table_info": rag_context.get("table_info", []),
            "column_metadata": rag_context.get("column_metadata", []),
            "business_terms": rag_context.get("business_terms", []),
            "table_relations": rag_context.get("table_relations", []),
            "sql_templates": rag_context.get("sql_templates", []),
        }

        update_execution_log(log_id, conn=conn, metadata_context=current_metadata)

        # RAG complete — re-emit with elapsed
        _emit_progress("rag", "元数据检索完成", elapsed=round(time.time() - step_start, 2))
        step_start = time.time()

        # Step 2-4: SQL Generation with metadata supplement loop
        # The LLM can either generate SQL (if metadata is sufficient)
        # or return need_more JSON (if metadata is insufficient)
        _emit_progress("llm", "正在生成 SQL...")
        logger.info("Step 2-4: SQL Generation with metadata supplement loop")
        current_round = 1
        loop_count = 0
        metadata_sufficient = False
        metadata_supplemented = False  # Track if supplement actually happened
        llm_analysis_result = None
        requested_tables_history = set()
        SQL_MAX_RETRIES = 3
        generated_sql = ""
        chart_type = "table"
        exec_result = None
        sql_error_context = ""

        while loop_count < llm_analysis_max_rounds:
            current_round += 1
            loop_count += 1

            update_execution_log(
                log_id,
                conn=conn,
                current_step="sql_generation" if loop_count == 1 else "metadata_supplement",
                current_round=current_round,
            )

            logger.info("SQL Generation loop %d/%d", loop_count, llm_analysis_max_rounds)

            # Build NL2SQL prompt with supplement support (Deep mode)
            from services.datamind.nl2sql.sql.query_executor import _get_ds_conn_params
            ds_params = _get_ds_conn_params(datasource_id)
            db_type = ds_params.get("db_type", "doris")
            engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
            engine = engine_map.get(db_type, db_type.capitalize())

            messages = build_nl2sql_prompt_with_supplement(
                question=question,
                table_info=current_metadata["table_info"],
                column_metadata=current_metadata["column_metadata"],
                sql_templates=current_metadata.get("sql_templates", []),
                business_terms=current_metadata["business_terms"],
                table_relations=current_metadata.get("table_relations", []),
                conversation_history=history or [],
                engine=engine,
            )

            # Append error context for retries
            if sql_error_context:
                messages.append({
                    "role": "user",
                    "content": f"上一次生成的SQL执行失败：\n```sql\n{generated_sql}\n```\n错误信息：{sql_error_context}\n\n请分析错误原因并修正SQL。",
                })

            # Call LLM (async to avoid blocking event loop)
            llm_result = await async_generate_sql(messages=messages, model_id=model_id)
            _track_llm_result(llm_result)
            raw_content = llm_result.get("sql", "").strip()

            # Parse LLM response: check if it's need_more JSON or SQL
            is_need_more = False
            analysis = {}

            if raw_content.startswith("{"):
                try:
                    import json as _json
                    # Try to parse as JSON (need_more response)
                    parsed = _json.loads(raw_content)
                    if parsed.get("need_more"):
                        is_need_more = True
                        analysis = parsed
                        llm_analysis_result = analysis
                        update_execution_log(log_id, conn=conn, llm_analysis=analysis)
                except _json.JSONDecodeError:
                    pass  # Not JSON, treat as SQL

            if is_need_more:
                # LLM says metadata is insufficient
                _emit_progress("metadata_supplement", f"正在补充元数据: {analysis.get('reason', '')}")
                logger.info("LLM requests more metadata: %s", analysis.get("reason"))

                # Retrieve supplementary metadata
                required_tables = analysis.get("required_tables", [])
                required_columns = analysis.get("required_columns", [])

                # Early exit if requesting same tables as before
                new_tables = [t for t in required_tables if t not in requested_tables_history]
                if required_tables and not new_tables and not required_columns:
                    logger.info("All requested tables already fetched, stopping loop")
                    metadata_sufficient = True
                    break
                requested_tables_history.update(required_tables)

                supplement_metadata = {
                    "table_info": [],
                    "column_metadata": [],
                    "business_terms": [],
                    "table_relations": [],
                }

                if new_tables:
                    supplement_context = retrieve_tables_metadata(
                        table_names=new_tables,
                        datasource_id=datasource_id,
                    )
                    supplement_metadata["table_info"] = supplement_context.get("table_info", [])
                    supplement_metadata["column_metadata"] = supplement_context.get("column_metadata", [])
                    supplement_metadata["table_relations"] = supplement_context.get("table_relations", [])

                if required_columns:
                    supplement_cols = _fetch_specific_columns(
                        required_columns, datasource_id, conn=conn,
                    )
                    supplement_metadata["column_metadata"].extend(supplement_cols)

                if supplement_metadata["table_info"] or supplement_metadata["column_metadata"]:
                    current_metadata = _merge_metadata(current_metadata, supplement_metadata)
                    metadata_supplemented = True
                    update_execution_log(
                        log_id, conn=conn,
                        metadata_supplemented=supplement_metadata,
                        metadata_context=current_metadata,
                    )
                else:
                    logger.info("No new metadata to supplement, stopping loop")
                    break
            else:
                # LLM generated SQL directly
                metadata_sufficient = True
                generated_sql = raw_content
                logger.info("SQL generated directly (metadata sufficient)")
                break

        if not metadata_sufficient and loop_count > 0:
            logger.info("Loop exhausted after %d iterations", loop_count)

        # Step 5: Execute SQL (with self-correction retry)
        meta_elapsed = round(time.time() - step_start, 2)
        if metadata_supplemented:
            _emit_progress("metadata_supplement", "元数据补充完成", elapsed=meta_elapsed)
        _emit_progress("llm", "SQL生成完成")
        step_start = time.time()
        SQL_MAX_RETRIES = 3
        chart_type = "table"
        exec_result = None
        sql_error_context = ""

        if not generated_sql:
            # Loop exhausted without generating SQL
            update_execution_log(
                log_id, conn=conn,
                status="failed",
                error_message="元数据补充循环未能生成SQL",
                completed_at=_now(),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )
            yield "done", {
                "success": False,
                "message": "元数据补充循环未能生成SQL",
                "log_id": log_id,
            }
            return

        # Parse SQL from response
        sql_result = _parse_sql_response(generated_sql)

        if not sql_result.get("success"):
            update_execution_log(
                log_id, conn=conn,
                status="failed",
                error_message=sql_result.get("message", "SQL生成失败"),
                completed_at=_now(),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )
            yield "done", {
                "success": False,
                "message": sql_result.get("message", "SQL生成失败"),
                "log_id": log_id,
            }
            return

        generated_sql = sql_result["sql"]
        chart_type = sql_result.get("chart-type", "table")
        query_type = sql_result.get("query_type", "sql")

        update_execution_log(log_id, conn=conn, generated_sql=generated_sql, chart_type=chart_type)

        # SQL Validation + Execution with retry
        for sql_attempt in range(1, SQL_MAX_RETRIES + 1):
            current_round += 1
            update_execution_log(
                log_id, conn=conn,
                current_step="sql_execution" if sql_attempt == 1 else "sql_retry",
                current_round=current_round,
            )

            logger.info("Step 5: SQL Execution (attempt %d/%d)", sql_attempt, SQL_MAX_RETRIES)
            _emit_progress("validate", "正在校验 SQL...")
            validate_elapsed = round(time.time() - step_start, 2)
            _emit_progress("validate", "SQL校验完成", elapsed=validate_elapsed)
            _emit_progress("execute", "正在执行查询...")
            step_start = time.time()

            # Validate and fix SQL
            if query_type == "sql":
                generated_sql, warnings = validate_and_fix(generated_sql)
            else:
                warnings = []
            if warnings:
                logger.info("SQL warnings: %s", warnings)

            # Execute query
            try:
                df, elapsed_ms, row_count = execute_query(generated_sql, datasource_id=datasource_id, query_type=query_type)
                columns = list(df.columns) if not df.empty else []
                rows = df.to_dict(orient="records") if not df.empty else []
                rows = _sanitize_rows(rows)
                exec_result = {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": row_count,
                    "elapsed_ms": elapsed_ms,
                }
                break
            except Exception as exec_err:
                logger.warning("SQL execution failed (attempt %d/%d): %s", sql_attempt, SQL_MAX_RETRIES, exec_err)
                sql_error_context = f"SQL执行失败: {exec_err}"
                exec_result = {"success": False, "error": str(exec_err)}

                # If retrying, generate new SQL with error context
                if sql_attempt < SQL_MAX_RETRIES:
                    messages = build_nl2sql_prompt(
                        question=question,
                        table_info=current_metadata["table_info"],
                        column_metadata=current_metadata["column_metadata"],
                        sql_templates=current_metadata.get("sql_templates", []),
                        business_terms=current_metadata["business_terms"],
                        table_relations=current_metadata.get("table_relations", []),
                        conversation_history=history or [],
                        engine=engine,
                    )
                    messages.append({
                        "role": "user",
                        "content": f"上一次生成的SQL执行失败：\n```sql\n{generated_sql}\n```\n错误信息：{sql_error_context}\n\n请分析错误原因并修正SQL。",
                    })
                    retry_result = await async_generate_sql(messages=messages, model_id=model_id)
                    _track_llm_result(retry_result)
                    generated_sql = retry_result.get("sql", "")

                if sql_attempt < SQL_MAX_RETRIES:
                    logger.info("Retrying SQL generation with error context...")
                    continue
                # Last attempt failed — fall through to error handling

        try:
            update_execution_log(
                log_id,
                conn=conn,
                execution_result=exec_result,
            )
        except Exception as log_err:
            logger.warning("Failed to log execution_result (non-fatal): %s", log_err)

        if not exec_result or not exec_result.get("success"):
            update_execution_log(
                log_id,
                conn=conn,
                status="failed",
                error_message=exec_result.get("error", "SQL执行失败") if exec_result else "SQL执行失败",
                completed_at=_now(),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )
            yield "done", {
                "success": False,
                "message": f"SQL执行失败（已重试{SQL_MAX_RETRIES}次）: {exec_result.get('error', '未知错误') if exec_result else '未知错误'}",
                "sql": generated_sql,
                "log_id": log_id,
            }
            return

        # Step 6: Result Analysis (optional)
        analysis_result = None
        result_analysis_step = next(
            (s for s in workflow["steps"] if s["step_type"] == "result_analysis"),
            None
        )

        if result_analysis_step and result_analysis_step.get("is_enabled", True):
            current_round += 1
            update_execution_log(
                log_id,
                conn=conn,
                current_step="result_analysis",
                current_round=current_round,
            )

            logger.info("Step 6: Result Analysis")
            exec_elapsed = round(time.time() - step_start, 2)
            _emit_progress("execute", "查询执行完成", elapsed=exec_elapsed)
            _emit_progress("interpret", "正在分析结果...")
            step_start = time.time()

            # Only pass columns used in the SQL (not full metadata) to reduce LLM context
            relevant_columns = _filter_columns_by_sql(
                generated_sql, current_metadata.get("column_metadata", [])
            )
            analysis_result = analyze_result(
                question=question,
                query_result=exec_result,
                column_metadata=relevant_columns,
                conn=conn,
            )
            _track_llm_result(analysis_result.pop("_llm_result", {}))

            # Enhance analysis with actual result data if summary is missing or generic
            summary = analysis_result.get("summary", "") or analysis_result.get("reason", "")
            if exec_result and exec_result.get("success"):
                rows = exec_result.get("rows", [])
                columns = exec_result.get("columns", [])
                row_count = exec_result.get("row_count", 0)
                # If summary doesn't contain actual data values, enhance it
                if rows and columns and row_count <= 50:
                    # Build a concise result description
                    if len(columns) == 1 and row_count <= 20:
                        # Single column: list all values
                        values = [str(row.get(columns[0], "")) for row in rows if row.get(columns[0]) is not None]
                        data_desc = f"共{len(values)}种: {', '.join(values)}"
                    elif len(columns) == 2 and row_count <= 20:
                        # Two columns: key-value pairs
                        pairs = [f"{row.get(columns[0], '')}={row.get(columns[1], '')}" for row in rows]
                        data_desc = f"共{row_count}条: {'; '.join(pairs)}"
                    else:
                        data_desc = f"共{row_count}行, {len(columns)}列"

                    if not summary or len(summary) < 20:
                        analysis_result["summary"] = f"查询结果: {data_desc}"
                    elif data_desc not in summary:
                        analysis_result["summary"] = f"{summary}\n\n数据概览: {data_desc}"

            update_execution_log(
                log_id,
                conn=conn,
                analysis_result=analysis_result,
            )

        # Complete execution
        elapsed_ms = int((time.time() - start_time) * 1000)
        interp_elapsed = round(time.time() - step_start, 2)
        _emit_progress("interpret", "结果分析完成", elapsed=interp_elapsed)
        _emit_progress("completed", "处理完成")
        update_execution_log(
            log_id,
            conn=conn,
            status="completed",
            current_step="completed",
            completed_at=_now(),
            elapsed_ms=elapsed_ms,
        )

        yield "done", {
            "success": True,
            "sql": generated_sql,
            "chart_type": chart_type,
            "result": exec_result,
            "analysis": analysis_result,
            "metadata_context": current_metadata,
            "thinking": "\n\n".join(thinking_texts) if thinking_texts else None,
            "tokens": total_tokens,
            "step_timings": step_timings,
            "workflow": {
                "id": workflow_id,
                "name": workflow_name,
                "rounds_used": current_round,
                "loop_count": loop_count,
            },
            "log_id": log_id,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        logger.error("Loop execution failed: %s", e, exc_info=True)
        elapsed_ms = int((time.time() - start_time) * 1000)
        update_execution_log(
            log_id,
            conn=conn,
            status="failed",
            error_message=str(e),
            completed_at=_now(),
            elapsed_ms=elapsed_ms,
        )
        yield "done", {
            "success": False,
            "message": f"执行失败: {str(e)}",
            "log_id": log_id,
            "elapsed_ms": elapsed_ms,
        }
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# Helper Functions
# ════════════════════════════════════════════════════════════════════

def _sanitize_rows(rows: list[dict]) -> list[dict]:
    """Make DataFrame rows JSON-serializable.

    Handles types that json.dumps cannot serialize:
    - datetime/date/time → isoformat string
    - Decimal → float
    - bytes → utf-8 string
    - NaN/inf → None
    - set/frozenset → list
    """
    import math
    from decimal import Decimal

    for row in rows:
        for k, v in row.items():
            if v is None:
                continue
            if hasattr(v, 'isoformat'):
                row[k] = v.isoformat()
            elif isinstance(v, Decimal):
                row[k] = float(v)
            elif isinstance(v, bytes):
                row[k] = v.decode('utf-8', errors='replace')
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None
            elif isinstance(v, (set, frozenset)):
                row[k] = list(v)
    return rows


def _fetch_specific_columns(
    required_columns: list[dict],
    datasource_id: int = 0,
    conn=None,
) -> list[dict]:
    """Fetch specific columns from database by table_name + column_name.

    Args:
        required_columns: List of dicts like [{"table": "t_user", "columns": ["user_type", "status"]}]
        datasource_id: Filter by datasource.
        conn: Optional pymysql connection to reuse.

    Returns:
        List of column metadata dicts.
    """
    if not required_columns:
        return []

    # Build WHERE conditions for each table.column pair
    conditions = []
    params = []
    for req in required_columns:
        table = req.get("table", "")
        columns = req.get("columns", [])
        if not table or not columns:
            continue
        for col in columns:
            conditions.append("(table_name = %s AND column_name = %s)")
            params.extend([table, col])

    if not conditions:
        return []

    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
    where_clause = " OR ".join(conditions)

    sql = f"""
        SELECT table_name, column_name, data_type, column_comment, business_desc, is_key
        FROM adh_column_metadata
        WHERE is_active = 1 AND ({where_clause}) {ds_filter}
    """

    try:
        close_conn = False
        if conn is None:
            conn = _get_metadata_conn()
            close_conn = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                logger.info("[_fetch_specific_columns] fetched %d columns", len(rows))
                return rows
        finally:
            if close_conn:
                conn.close()
    except Exception as e:
        logger.error("[_fetch_specific_columns] failed: %s", e)
        return []


def _merge_metadata(
    base: Dict[str, Any],
    supplement: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge supplementary metadata into base metadata, avoiding duplicates."""
    result = {
        "table_info": list(base.get("table_info", [])),
        "column_metadata": list(base.get("column_metadata", [])),
        "business_terms": list(base.get("business_terms", [])),
        "table_relations": list(base.get("table_relations", [])),
        "sql_templates": list(base.get("sql_templates", [])),
    }

    # Track existing IDs to avoid duplicates
    existing_table_ids = {t.get("id") for t in result["table_info"]}
    existing_col_ids = {c.get("id") for c in result["column_metadata"]}
    existing_term_ids = {t.get("id") for t in result["business_terms"]}
    existing_rel_ids = {r.get("id") for r in result["table_relations"]}

    # Merge tables
    for table in supplement.get("table_info", []):
        if table.get("id") not in existing_table_ids:
            result["table_info"].append(table)
            existing_table_ids.add(table.get("id"))

    # Merge columns
    for col in supplement.get("column_metadata", []):
        if col.get("id") not in existing_col_ids:
            result["column_metadata"].append(col)
            existing_col_ids.add(col.get("id"))

    # Merge terms
    for term in supplement.get("business_terms", []):
        if term.get("id") not in existing_term_ids:
            result["business_terms"].append(term)
            existing_term_ids.add(term.get("id"))

    # Merge relations
    for rel in supplement.get("table_relations", []):
        if rel.get("id") not in existing_rel_ids:
            result["table_relations"].append(rel)
            existing_rel_ids.add(rel.get("id"))

    return result


def _parse_sql_response(response: str) -> Dict[str, Any]:
    """Parse LLM response to extract SQL and metadata.

    Expected format:
    {"success": true, "sql": "...", "tables": [...], "chart-type": "...", "brief": "..."}
    or
    {"success": false, "message": "..."}
    """
    content = response.strip()

    # Handle markdown code blocks
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        result = json.loads(content)
        return result
    except json.JSONDecodeError as e:
        logger.error("Failed to parse SQL response: %s, content: %s", e, content[:200])
        return {
            "success": False,
            "message": "LLM返回格式错误，请重试",
        }
