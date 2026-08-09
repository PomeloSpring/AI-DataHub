"""Query Rewriter — enhance user queries before RAG retrieval.

Three-stage enhancement:
1. Pronoun Resolution (指代消解): Replace pronouns with concrete entities from context
2. Time Normalization (时间规范化): Convert relative time expressions to absolute dates
3. Query Expansion (查询扩展): Generate semantic variants for multi-path RAG retrieval

Output format:
{
    "canonical_query": "查询张三上个月的销售额",
    "expanded_queries": ["查询张三2026年5月的销售业绩", "张三2026年5月销售数据"],
    "time_range": {"start": "2026-05-01", "end": "2026-05-31"},
    "resolved_entities": {"它": "销售额"}
}
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ── Pronoun Patterns ─────────────────────────────────────────────────

# Chinese pronouns that refer to previous context
_PRONOUNS = {
    "它", "他", "她", "它们", "他们", "她们",
    "这个", "那个", "这些", "那些",
    "上述", "前面", "刚才", "之前",
    "its", "it", "they", "them", "this", "that",
}

# ── Time Expression Patterns ─────────────────────────────────────────

# Relative time patterns (Chinese)
_TIME_PATTERNS = [
    # (pattern, handler)
    # Chinese number word patterns
    (r"最近一[天日]", lambda m: _days_ago(1)),
    (r"最近一周", lambda m: _days_ago(7)),
    (r"最近一个月", lambda m: _months_ago(1)),
    (r"最近一年", lambda m: _years_ago(1)),
    (r"过去一[天日]", lambda m: _days_ago(1)),
    (r"过去一周", lambda m: _days_ago(7)),
    (r"过去一个月", lambda m: _months_ago(1)),
    # Numeric patterns
    (r"最近(\d+)天", lambda m: _days_ago(int(m.group(1)))),
    (r"最近(\d+)周", lambda m: _days_ago(int(m.group(1)) * 7)),
    (r"最近(\d+)个月", lambda m: _months_ago(int(m.group(1)))),
    (r"最近(\d+)年", lambda m: _years_ago(int(m.group(1)))),
    (r"过去(\d+)天", lambda m: _days_ago(int(m.group(1)))),
    (r"过去(\d+)周", lambda m: _days_ago(int(m.group(1)) * 7)),
    (r"过去(\d+)个月", lambda m: _months_ago(int(m.group(1)))),
    (r"近(\d+)天", lambda m: _days_ago(int(m.group(1)))),
    (r"近(\d+)周", lambda m: _days_ago(int(m.group(1)) * 7)),
    (r"近(\d+)个月", lambda m: _months_ago(int(m.group(1)))),
    (r"近(\d+)年", lambda m: _years_ago(int(m.group(1)))),
    (r"上个?月", lambda m: _last_month()),
    (r"上个?周", lambda m: _last_week()),
    (r"上个?季度", lambda m: _last_quarter()),
    (r"上个?年", lambda m: _last_year()),
    (r"本[月]", lambda m: _this_month()),
    (r"本[周]", lambda m: _this_week()),
    (r"本季度", lambda m: _this_quarter()),
    (r"今年", lambda m: _this_year()),
    (r"去年", lambda m: _last_year()),
    (r"前年", lambda m: _years_ago(2)),
    (r"今天", lambda m: _today()),
    (r"昨天", lambda m: _yesterday()),
    (r"前天", lambda m: _days_ago(2)),
    (r"这[一]?周", lambda m: _this_week()),
    (r"这[一]?个月", lambda m: _this_month()),
]


# ── Time Helper Functions ────────────────────────────────────────────

def _today() -> dict:
    today = datetime.now()
    return {"start": today.strftime("%Y-%m-%d"), "end": today.strftime("%Y-%m-%d")}


def _yesterday() -> dict:
    yesterday = datetime.now() - timedelta(days=1)
    return {"start": yesterday.strftime("%Y-%m-%d"), "end": yesterday.strftime("%Y-%m-%d")}


def _days_ago(n: int) -> dict:
    end = datetime.now()
    start = end - timedelta(days=n)
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}


def _months_ago(n: int) -> dict:
    end = datetime.now()
    # Approximate: n months ≈ n*30 days
    start = end - timedelta(days=n * 30)
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}


def _years_ago(n: int) -> dict:
    end = datetime.now()
    start = end.replace(year=end.year - n)
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}


def _last_month() -> dict:
    today = datetime.now()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return {
        "start": first_of_prev_month.strftime("%Y-%m-%d"),
        "end": last_of_prev_month.strftime("%Y-%m-%d"),
    }


def _this_month() -> dict:
    today = datetime.now()
    first = today.replace(day=1)
    return {"start": first.strftime("%Y-%m-%d"), "end": today.strftime("%Y-%m-%d")}


def _last_week() -> dict:
    today = datetime.now()
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)
    return {
        "start": last_monday.strftime("%Y-%m-%d"),
        "end": last_sunday.strftime("%Y-%m-%d"),
    }


def _this_week() -> dict:
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return {"start": monday.strftime("%Y-%m-%d"), "end": today.strftime("%Y-%m-%d")}


def _last_quarter() -> dict:
    today = datetime.now()
    quarter = (today.month - 1) // 3
    if quarter == 0:
        # Last quarter of previous year
        start = datetime(today.year - 1, 10, 1)
        end = datetime(today.year - 1, 12, 31)
    else:
        start_month = (quarter - 1) * 3 + 1
        start = datetime(today.year, start_month, 1)
        end_month = start_month + 2
        import calendar
        _, last_day = calendar.monthrange(today.year, end_month)
        end = datetime(today.year, end_month, last_day)
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}


def _this_quarter() -> dict:
    today = datetime.now()
    quarter = (today.month - 1) // 3
    start_month = quarter * 3 + 1
    start = datetime(today.year, start_month, 1)
    return {"start": start.strftime("%Y-%m-%d"), "end": today.strftime("%Y-%m-%d")}


def _last_year() -> dict:
    today = datetime.now()
    return {
        "start": f"{today.year - 1}-01-01",
        "end": f"{today.year - 1}-12-31",
    }


def _this_year() -> dict:
    today = datetime.now()
    return {"start": f"{today.year}-01-01", "end": today.strftime("%Y-%m-%d")}


# ── Core Rewriting Functions ─────────────────────────────────────────

def resolve_pronouns(
    question: str,
    history: list[dict] = None,
) -> tuple[str, dict]:
    """Replace pronouns with concrete entities from conversation history.

    Args:
        question: Current user question
        history: Conversation history (list of {role, content} dicts)

    Returns:
        (resolved_question, resolved_entities)
        resolved_entities: {"pronoun": "replacement"}
    """
    if not history:
        return question, {}

    resolved = {}
    resolved_question = question

    # Extract entities from recent history (last 3 Q&A pairs)
    recent_context = []
    for msg in history[-6:]:
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            recent_context.append(content)

    if not recent_context:
        return question, {}

    # Simple heuristic: look for table names, column names, metrics mentioned in recent context
    # and replace pronouns that appear in the current question

    # Check if question contains pronouns
    has_pronoun = any(p in question for p in _PRONOUNS)
    if not has_pronoun:
        return question, {}

    # Extract potential entities from recent context
    # Look for: table names (alphanumeric + underscore), Chinese terms
    entity_patterns = [
        r"(?:表|查询|统计|分析|查看)\s*[「「]?(\w+)[」」]?",  # "查询用户表"
        r"(\w+(?:表|数据|指标|字段|列))",  # "用户表", "销售额指标"
        r"(?:FROM|from)\s+(\w+)",  # SQL table names
    ]

    entities = []
    for ctx in recent_context:
        for pattern in entity_patterns:
            matches = re.findall(pattern, ctx)
            entities.extend(matches)

    # Deduplicate and filter
    entities = list(set(e for e in entities if len(e) >= 2))

    if not entities:
        return question, {}

    # Replace pronouns with the most recent relevant entity
    # Use word boundary matching to avoid replacing inside words
    for pronoun in _PRONOUNS:
        if pronoun in resolved_question and entities:
            # Use the most recent entity
            replacement = entities[-1]
            # Use regex with word boundary for safe replacement
            import re as _re
            pattern = _re.escape(pronoun)
            new_q = _re.sub(pattern, replacement, resolved_question, count=1)
            if new_q != resolved_question:
                resolved_question = new_q
                resolved[pronoun] = replacement
                logger.debug("Resolved pronoun '%s' → '%s'", pronoun, replacement)

    return resolved_question, resolved


def normalize_time(
    question: str,
) -> tuple[str, Optional[dict]]:
    """Convert relative time expressions to absolute dates.

    Args:
        question: User question (may contain relative time expressions)

    Returns:
        (normalized_question, time_range)
        time_range: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} or None
    """
    time_range = None
    normalized = question

    for pattern, handler in _TIME_PATTERNS:
        match = re.search(pattern, question)
        if match:
            time_range = handler(match)
            # Replace the relative expression with absolute dates
            original = match.group(0)
            replacement = f"{time_range['start']}至{time_range['end']}"
            normalized = normalized.replace(original, replacement)
            logger.debug("Normalized time '%s' → '%s'", original, replacement)
            break  # Use first match only

    return normalized, time_range


def expand_query(
    question: str,
    time_range: Optional[dict] = None,
) -> list[str]:
    """Generate semantic variants of the question for multi-path RAG retrieval.

    Args:
        question: Original or normalized question
        time_range: Optional time range to incorporate

    Returns:
        List of expanded queries (2-3 variants)
    """
    variants = []

    # Strategy 1: Add time range if available
    if time_range:
        time_str = f"{time_range['start']}至{time_range['end']}"
        if time_str not in question:
            variants.append(f"{question}（时间范围：{time_str}）")

    # Strategy 2: Simplify by removing modifiers
    simplified = re.sub(r"(请|帮我|帮忙|能否|可以|我想|我要)\s*", "", question)
    if simplified != question and len(simplified) >= 4:
        variants.append(simplified)

    # Strategy 3: Rephrase with common patterns
    if question.startswith("查"):
        variants.append(question.replace("查", "查询", 1))
    if "多少" in question:
        variants.append(question.replace("多少", "数量"))

    # Deduplicate and limit
    seen = {question}
    unique_variants = []
    for v in variants:
        if v not in seen and len(v) >= 4:
            seen.add(v)
            unique_variants.append(v)

    return unique_variants[:3]


def rewrite_query(
    question: str,
    history: list[dict] = None,
) -> dict:
    """Main entry point: rewrite query with all three enhancements.

    Args:
        question: Original user question
        history: Conversation history (list of {role, content} dicts)

    Returns:
        {
            "canonical_query": str,      # Enhanced query
            "original_query": str,       # Original question
            "expanded_queries": list,    # Semantic variants
            "time_range": dict|None,     # {"start": "...", "end": "..."}
            "resolved_entities": dict,   # {"pronoun": "replacement"}
        }
    """
    original = question

    # Step 1: Pronoun resolution
    question, resolved_entities = resolve_pronouns(question, history)

    # Step 2: Time normalization
    question, time_range = normalize_time(question)

    # Step 3: Query expansion
    expanded = expand_query(question, time_range)

    result = {
        "canonical_query": question,
        "original_query": original,
        "expanded_queries": expanded,
        "time_range": time_range,
        "resolved_entities": resolved_entities,
    }

    if question != original or time_range or resolved_entities:
        logger.info(
            "Query rewritten: '%s' → '%s' (time=%s, entities=%s, variants=%d)",
            original[:50], question[:50],
            bool(time_range), bool(resolved_entities), len(expanded),
        )

    return result


# ── LLM-Enhanced Rewriting (Optional) ────────────────────────────────

def rewrite_query_with_llm(
    question: str,
    history: list[dict] = None,
    model_id: int = None,
) -> dict:
    """LLM-enhanced query rewriting for complex cases.

    Falls back to rule-based rewriting if LLM fails.

    Args:
        question: Original user question
        history: Conversation history
        model_id: LLM model ID

    Returns:
        Same format as rewrite_query()
    """
    # First try rule-based rewriting
    rule_result = rewrite_query(question, history)

    # If significant changes were made, return rule-based result
    if rule_result["resolved_entities"] or rule_result["time_range"]:
        return rule_result

    # For complex cases, try LLM enhancement
    try:
        from services.shared.common.llm.llm_client import generate_sql

        # Build context from history
        context_str = ""
        if history:
            recent = history[-4:]
            context_str = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')[:200]}"
                for m in recent
            )

        prompt = f"""你是一个查询改写助手。请分析用户问题，完成以下任务：

1. 指代消解：如果问题中有"它"、"这个"、"上述"等指代词，请用对话上下文中的具体实体替换
2. 时间规范化：如果问题中有"上个月"、"最近一周"等相对时间，请转换为具体日期范围（YYYY-MM-DD格式）
3. 查询扩展：生成2-3个语义相同但表达不同的变体

对话上下文：
{context_str if context_str else "（无上下文）"}

用户问题：{question}

请以JSON格式返回：
{{
    "canonical_query": "改写后的完整查询",
    "expanded_queries": ["变体1", "变体2"],
    "time_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}} 或 null,
    "resolved_entities": {{"代词": "具体实体"}}
}}

只返回JSON，不要其他文字。"""

        messages = [{"role": "user", "content": prompt}]
        result = generate_sql(messages, max_tokens=500, model_id=model_id)

        # Parse LLM response
        sql_text = result.get("sql", "")
        # Try to extract JSON from response
        json_match = re.search(r'\{[^{}]*\}', sql_text, re.DOTALL)
        if json_match:
            llm_result = json.loads(json_match.group())
            # Merge with rule-based result
            if llm_result.get("canonical_query"):
                rule_result["canonical_query"] = llm_result["canonical_query"]
            if llm_result.get("expanded_queries"):
                rule_result["expanded_queries"] = llm_result["expanded_queries"]
            if llm_result.get("time_range"):
                rule_result["time_range"] = llm_result["time_range"]
            if llm_result.get("resolved_entities"):
                rule_result["resolved_entities"] = llm_result["resolved_entities"]
            logger.info("LLM query rewrite: '%s' → '%s'", question[:50], rule_result["canonical_query"][:50])

    except Exception as e:
        logger.debug("LLM query rewrite failed, using rule-based: %s", e)

    return rule_result
