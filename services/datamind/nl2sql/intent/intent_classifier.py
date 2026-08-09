"""Intent Classifier — lightweight LLM-based intent detection for ChatBI.

Determines whether a user message is:
- query: A data query that should generate SQL
- chat: A general conversation (greeting, chitchat)
- correction: A request to fix/modify the previous SQL
- explain: A request to explain the previous result

Also extracts target_tables and refined_question for downstream RAG filtering.
"""

import json
import logging
import re
from typing import Optional

from services.shared.common.llm.llm_client import _get_client
from services.shared.common.config import ANTHROPIC_MODEL

logger = logging.getLogger(__name__)

_INTENT_PROMPT = """你是一个意图分类器。根据用户输入和对话上下文，判断用户意图。

## 意图类型
- **query**: 用户想要查询数据、生成SQL、分析数据
- **chat**: 普通对话、问候、闲聊、与数据无关的问题
- **correction**: 用户想要修改、纠正、调整上一条SQL（如"不对"、"应该按XX分组"、"加个条件"）
- **explain**: 用户想解释或分析上一次的查询结果（如"为什么这么少"、"详细说说"）

## 规则
1. 如果用户提到"你好"、"谢谢"、"你是谁"等非数据问题，返回 chat
2. 如果用户提到对之前SQL的修改意见（如"不对"、"改成"、"加上"、"去掉"），返回 correction
3. 如果用户想了解结果含义（如"为什么"、"解释一下"、"分析"），返回 explain
4. 其他涉及数据查询的返回 query

## 输出格式
只返回一个JSON对象，不要其他文字：
{{"intent": "query|chat|correction|explain", "refined_question": "优化后的问题描述", "target_tables": ["推测的表名1", "表名2"], "reply": "仅chat意图时的回复内容，其他意图为空字符串"}}

## 对话上下文
{context}

## 用户输入
{question}"""


def _build_context(history: list[dict], prev_sql: str = "", prev_result_summary: str = "") -> str:
    """Build context string from conversation history."""
    parts = []
    if prev_sql:
        parts.append(f"上一轮SQL: {prev_sql}")
    if prev_result_summary:
        parts.append(f"上一轮结果摘要: {prev_result_summary}")

    if history:
        for msg in history[-6:]:  # Last 6 messages for context
            role = msg.get("role", "")
            content = msg.get("content", "")
            sql = msg.get("sql", "")
            if role == "user":
                parts.append(f"用户: {content}")
            elif role == "assistant":
                if sql:
                    parts.append(f"助手SQL: {sql}")
                elif content:
                    parts.append(f"助手: {content[:100]}")

    return "\n".join(parts) if parts else "（无历史上下文）"


_CHAT_REPLIES = {
    "你好": "你好！我是 ChatBI 数据分析助手，可以帮你查询数据、生成 SQL。请问有什么需要？",
    "您好": "您好！我是 ChatBI 数据分析助手，可以帮你查询数据、生成 SQL。请问有什么需要？",
    "hi": "Hi! 我是 ChatBI 数据分析助手，有什么数据查询需求吗？",
    "hello": "Hello! 我是 ChatBI 数据分析助手，有什么数据查询需求吗？",
    "谢谢": "不客气！还有什么数据查询需求吗？",
    "thanks": "不客气！还有什么数据查询需求吗？",
    "你是谁": "我是 ChatBI 数据分析助手，可以通过自然语言帮你查询 Doris 数据库、生成 SQL、分析数据。",
}


def _quick_classify(question: str) -> Optional[dict]:
    """Fast-path classification for obvious chat/correction/explain intents without LLM."""
    q = question.strip()
    q_lower = q.lower()
    # Pure greetings / chitchat
    chat_patterns = r'^(你好|您好|hi|hello|hey|嗨|哈喽|谢谢|感谢|thanks|拜拜|再见|bye|ok|好的|嗯|👍|你是谁|介绍一下|help|帮助)$'
    if re.match(chat_patterns, q, re.IGNORECASE):
        reply = _CHAT_REPLIES.get(q_lower, _CHAT_REPLIES.get(q, ""))
        return {"intent": "chat", "refined_question": q, "target_tables": [], "reply": reply}
    # Correction keywords (references to previous SQL)
    correction_patterns = r'(不对|不对吧|改[成正为]|加上|去掉|删除|换个|换成|应该|分组|排序|加个|减个|条件|过滤)'
    if re.search(correction_patterns, q):
        # Only if there's likely a previous context (short message with correction words)
        if len(q) <= 30:
            return {"intent": "correction", "refined_question": q, "target_tables": [], "reply": ""}
    # Explain keywords
    explain_patterns = r'^(为什么|怎么回事|解释|分析一下|详细|说说|说明|什么原因)'
    if re.match(explain_patterns, q) and len(q) <= 20:
        return {"intent": "explain", "refined_question": q, "target_tables": [], "reply": ""}
    return None


def classify_intent(
    question: str,
    history: list[dict] = None,
    prev_sql: str = "",
    prev_result_summary: str = "",
) -> dict:
    """Classify user intent using a lightweight LLM call.

    Returns:
        {
            "intent": "query|chat|correction|explain",
            "refined_question": str,
            "target_tables": list[str],
            "reply": str  # Only for chat intent
        }
    """
    # Fast-path for obvious non-query intents
    quick = _quick_classify(question)
    if quick:
        return quick

    context = _build_context(history or [], prev_sql, prev_result_summary)
    prompt = _INTENT_PROMPT.format(context=context, question=question)

    try:
        client = _get_client()
        raw = ""

        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                raw = block.text.strip()
                break

        # Fallback: extract JSON from ThinkingBlock if no text block
        if not raw:
            for block in response.content:
                if hasattr(block, "thinking") and block.thinking:
                    match = re.search(r'\{[^{}]*"intent"\s*:\s*"[^"]+?"[^{}]*\}', block.thinking, re.DOTALL)
                    if match:
                        raw = match.group(0)
                        logger.info("Intent classifier: extracted JSON from ThinkingBlock")
                        break

        if not raw:
            logger.warning("Intent classifier: no usable response, content=%s",
                           [(type(b).__name__, getattr(b, 'type', 'N/A')) for b in response.content])
            return {"intent": "query", "refined_question": question, "target_tables": [], "reply": ""}

        # Strip markdown fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        # Try to parse JSON directly
        result = None
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from mixed text (LLM may include reasoning)
            import re
            match = re.search(r'\{[^{}]*"intent"\s*:\s*"[^"]+?"[^{}]*\}', raw, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

        if result is None:
            logger.warning("Intent classifier: no JSON found in response, raw=%s", raw[:200])
            return {"intent": "query", "refined_question": question, "target_tables": [], "reply": ""}

        # Validate intent
        valid_intents = {"query", "chat", "correction", "explain"}
        if result.get("intent") not in valid_intents:
            result["intent"] = "query"

        # Ensure required fields
        result.setdefault("refined_question", question)
        result.setdefault("target_tables", [])
        result.setdefault("reply", "")

        return result

    except Exception as e:
        logger.warning("Intent classification failed: %s", e)
        return {"intent": "query", "refined_question": question, "target_tables": [], "reply": ""}


def extract_keywords(question: str) -> list[str]:
    """Extract Chinese business keywords from a question for RAG filtering.

    Keywords are loaded dynamically from adh_business_terms via terminology_manager.
    """
    from services.datamind.rag.terminology_manager import get_business_keywords

    keywords = []
    patterns = get_business_keywords()

    for pat in patterns:
        if re.search(pat, question):
            keywords.append(pat)

    return keywords
