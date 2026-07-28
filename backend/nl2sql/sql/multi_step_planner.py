"""Multi-step Planner — decompose complex questions into execution plans.

Handles questions that require multiple SQL queries:
- Comparison queries (compare A vs B)
- Aggregation over aggregation (top N by metric)
- Multi-table joins with intermediate results
- Sequential analysis (first X, then Y based on X)

Output format:
{
    "needs_multi_step": true,
    "plan": [
        {"step": 1, "tool": "sql", "instruction": "...", "depends_on": []},
        {"step": 2, "tool": "sql", "instruction": "...", "depends_on": [1]},
    ],
    "reason": "..."
}
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Multi-step Detection Patterns ────────────────────────────────────

_MULTI_STEP_PATTERNS = [
    # Comparison patterns
    (r"(对比|比较|versus|vs\.?)", "comparison"),
    (r"(.+?)和(.+?)的(差异|区别|不同)", "comparison"),
    (r"(.+?)与(.+?)对比", "comparison"),

    # Ranking + aggregation
    (r"(前\d+|top\s*\d+).+?的(平均|总计|汇总)", "rank_aggregate"),
    (r"(最多|最少|最高|最低).+?(平均|总计|汇总)", "rank_aggregate"),

    # Sequential analysis
    (r"先(.+?)然后(.+?)", "sequential"),
    (r"首先(.+?)接着(.+?)", "sequential"),
    (r"找到(.+?)再(.+?)", "sequential"),

    # Multi-entity
    (r"(.+?)和(.+?)分别", "multi_entity"),
    (r"每个(.+?)的(.+?)排名", "entity_ranking"),
]


def detect_multi_step(question: str) -> Optional[str]:
    """Detect if question needs multi-step execution.

    Returns pattern type if multi-step needed, None otherwise.
    """
    q_lower = question.lower().strip()

    for pattern, ptype in _MULTI_STEP_PATTERNS:
        if re.search(pattern, q_lower):
            return ptype

    return None


# ── Plan Generation ──────────────────────────────────────────────────

def generate_plan(
    question: str,
    table_info: list[dict],
    column_metadata: list[dict],
    pattern_type: str,
    model_id: int = None,
) -> dict:
    """Generate multi-step execution plan.

    Args:
        question: User's question
        table_info: Table metadata
        column_metadata: Column metadata
        pattern_type: Detected pattern type
        model_id: LLM model ID for plan generation

    Returns:
        Plan dict with steps
    """
    # Build schema context
    schema_str = "可用表:\n"
    for t in table_info[:10]:
        schema_str += f"- {t['table_name']}: {t.get('table_comment', '')}\n"

    columns_str = "关键字段:\n"
    for c in column_metadata[:30]:
        columns_str += f"- {c['table_name']}.{c['column_name']}: {c.get('column_comment', '')}\n"

    prompt = f"""你是一个 SQL 执行计划专家。请将以下复杂问题分解为多个 SQL 查询步骤。

用户问题: {question}
问题类型: {pattern_type}

{schema_str}
{columns_str}

请生成执行计划，要求：
1. 每步指定工具类型（sql 或 analysis）
2. 每步的指令必须明确：目标表名、聚合维度、过滤条件
3. 如果后续步骤依赖前序结果，在 depends_on 中说明
4. 保持步骤简洁，通常 2-3 步即可

返回 JSON 格式：
{{
    "needs_multi_step": true,
    "plan": [
        {{"step": 1, "tool": "sql", "instruction": "具体指令", "depends_on": []}},
        {{"step": 2, "tool": "sql", "instruction": "具体指令", "depends_on": [1]}}
    ],
    "reason": "为什么需要多步"
}}

只返回 JSON，不要其他文字。"""

    try:
        from backend.common.llm.llm_client import generate_sql
        messages = [{"role": "user", "content": prompt}]
        result = generate_sql(messages, max_tokens=1500, model_id=model_id)

        # Parse LLM response
        response_text = result.get("sql", "") or result.get("content", "")
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
            if plan.get("needs_multi_step") and plan.get("plan"):
                logger.info("Generated multi-step plan: %d steps", len(plan["plan"]))
                return plan

    except Exception as e:
        logger.warning("Plan generation failed: %s", e)

    # Fallback: simple plan based on pattern type
    return _generate_simple_plan(question, pattern_type)


def _generate_simple_plan(question: str, pattern_type: str) -> dict:
    """Generate a simple plan based on pattern type without LLM."""
    if pattern_type == "comparison":
        return {
            "needs_multi_step": True,
            "plan": [
                {"step": 1, "tool": "sql", "instruction": "查询第一个实体的数据", "depends_on": []},
                {"step": 2, "tool": "sql", "instruction": "查询第二个实体的数据", "depends_on": []},
                {"step": 3, "tool": "analysis", "instruction": "对比两个结果", "depends_on": [1, 2]},
            ],
            "reason": "问题涉及对比分析",
        }
    elif pattern_type == "rank_aggregate":
        return {
            "needs_multi_step": True,
            "plan": [
                {"step": 1, "tool": "sql", "instruction": "获取排名数据", "depends_on": []},
                {"step": 2, "tool": "sql", "instruction": "对排名结果进行聚合统计", "depends_on": [1]},
            ],
            "reason": "问题涉及排名后的聚合",
        }
    elif pattern_type == "sequential":
        return {
            "needs_multi_step": True,
            "plan": [
                {"step": 1, "tool": "sql", "instruction": "执行第一步查询", "depends_on": []},
                {"step": 2, "tool": "sql", "instruction": "基于第一步结果执行第二步", "depends_on": [1]},
            ],
            "reason": "问题涉及顺序执行",
        }
    else:
        return {
            "needs_multi_step": True,
            "plan": [
                {"step": 1, "tool": "sql", "instruction": "获取基础数据", "depends_on": []},
                {"step": 2, "tool": "analysis", "instruction": "分析结果", "depends_on": [1]},
            ],
            "reason": "问题可能需要多步处理",
        }


# ── Main Entry Point ─────────────────────────────────────────────────

def plan_query(
    question: str,
    table_info: list[dict],
    column_metadata: list[dict],
    model_id: int = None,
) -> Optional[dict]:
    """Main entry point: check if multi-step needed and generate plan.

    Returns:
        Plan dict if multi-step needed, None if single SQL sufficient
    """
    # Detect if multi-step is needed
    pattern_type = detect_multi_step(question)
    if not pattern_type:
        return None

    logger.info("Multi-step detected: pattern=%s, question=%s", pattern_type, question[:50])

    # Generate plan
    plan = generate_plan(question, table_info, column_metadata, pattern_type, model_id)

    return plan
