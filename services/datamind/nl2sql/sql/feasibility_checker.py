"""Feasibility Checker — pre-assess if metadata can answer the question.

Three verdicts:
- feasible: Metadata is sufficient to generate SQL
- need_clarify: Information insufficient, need user clarification
- infeasible: Question not related to data analysis
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class FeasibilityResult:
    """Result of feasibility assessment."""

    def __init__(self):
        self.feasible = True
        self.confidence = 1.0
        self.verdict = "feasible"  # feasible | need_clarify | infeasible
        self.reason = ""
        self.suggestions: list[str] = []

    def to_dict(self) -> dict:
        return {
            "feasible": self.feasible,
            "confidence": self.confidence,
            "verdict": self.verdict,
            "reason": self.reason,
            "suggestions": self.suggestions,
        }


# ── Infeasible Patterns ──────────────────────────────────────────────

# Questions not related to data analysis
_INFEASIBLE_PATTERNS = [
    r"^(你好|hello|hi|hey)",
    r"^(谢谢|感谢|thank)",
    r"^(再见|bye|goodbye)",
    r"天气",
    r"笑话",
    r"故事",
    r"新闻",
    r"翻译",
    r"解释.*是什么",
    r"什么是",
    r"如何.*操作",
    r"怎么.*使用",
    r"帮助",
    r"功能",
]


def check_infeasible(question: str) -> Optional[str]:
    """Check if question is not related to data analysis.

    Returns reason if infeasible, None otherwise.
    """
    q_lower = question.lower().strip()

    for pattern in _INFEASIBLE_PATTERNS:
        if re.search(pattern, q_lower):
            return f"问题 '{question[:30]}...' 不是数据分析问题"

    return None


def check_metadata_sufficiency(
    question: str,
    table_info: list[dict],
    column_metadata: list[dict],
) -> tuple[float, list[str]]:
    """Assess if metadata is sufficient to answer the question.

    Returns (confidence, suggestions).
    confidence: 0.0-1.0, higher means more confident
    suggestions: List of suggestions to improve feasibility
    """
    confidence = 1.0
    suggestions = []

    # Check 1: No tables found
    if not table_info:
        confidence -= 0.5
        suggestions.append("未找到相关表，请尝试更具体的问题")

    # Check 2: Very few columns
    if len(column_metadata) < 3:
        confidence -= 0.3
        suggestions.append("找到的字段较少，可能无法完整回答问题")

    # Check 3: Question mentions specific entities not in metadata
    # Extract potential table/column names from question
    q_words = set(re.findall(r'[a-zA-Z_]\w*|[一-鿿]+', question))
    schema_words = set()
    for t in table_info:
        schema_words.add(t["table_name"].lower())
    for c in column_metadata:
        schema_words.add(c["column_name"].lower())
        schema_words.add(c.get("column_comment", "").lower())

    # Check if key entities are missing
    key_entities = [w for w in q_words if len(w) >= 2 and w.lower() not in schema_words]
    if len(key_entities) > len(q_words) * 0.5:
        confidence -= 0.2
        suggestions.append(f"问题中的概念 ({', '.join(key_entities[:3])}) 可能与现有数据不匹配")

    return max(0.0, confidence), suggestions


def assess_feasibility(
    question: str,
    table_info: list[dict],
    column_metadata: list[dict],
) -> FeasibilityResult:
    """Main entry point: assess if the question can be answered with available metadata.

    Args:
        question: User's question
        table_info: Table metadata from RAG
        column_metadata: Column metadata from RAG

    Returns:
        FeasibilityResult with verdict and confidence
    """
    result = FeasibilityResult()

    # Check 1: Infeasible question
    infeasible_reason = check_infeasible(question)
    if infeasible_reason:
        result.feasible = False
        result.confidence = 0.0
        result.verdict = "infeasible"
        result.reason = infeasible_reason
        result.suggestions = ["请提出与数据相关的查询问题，例如：查询用户数量、统计销售额等"]
        return result

    # Check 2: Metadata sufficiency
    confidence, suggestions = check_metadata_sufficiency(question, table_info, column_metadata)
    result.confidence = confidence

    if confidence < 0.3:
        result.feasible = False
        result.verdict = "need_clarify"
        result.reason = "元数据不足，无法确定如何回答此问题"
        result.suggestions = suggestions
    elif confidence < 0.6:
        result.verdict = "need_clarify"
        result.reason = "元数据可能不足，建议提供更多上下文"
        result.suggestions = suggestions
    else:
        result.verdict = "feasible"
        result.reason = "元数据充足，可以尝试生成 SQL"

    logger.info(
        "Feasibility: verdict=%s, confidence=%.2f, question=%s",
        result.verdict, result.confidence, question[:50],
    )

    return result
