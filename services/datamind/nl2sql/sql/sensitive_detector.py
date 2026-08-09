"""Sensitive Column Detector — identify and exclude sensitive data.

Sensitivity levels:
- public: Safe to use (default)
- internal: Internal use only, show with warning
- pii: Personally identifiable information, exclude from LLM
- confidential: Highly sensitive, exclude completely

Detection methods:
1. Pattern matching (field name + comment)
2. Data type analysis (e.g., varchar(18) for ID cards)
3. LLM-based classification (optional)
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Sensitivity Patterns ─────────────────────────────────────────────

# PII patterns (field name + comment)
_PII_PATTERNS = [
    # Phone numbers
    (r"(phone|mobile|tel|手机|电话|联系)", "pii"),
    # Email
    (r"(email|mail|邮箱|邮件)", "pii"),
    # ID card
    (r"(id_card|identity|身份证|证件号|社会信用)", "pii"),
    # Address
    (r"(address|addr|地址|住址|街道)", "pii"),
    # Name (full name)
    (r"(real_name|true_name|真实姓名|全名)", "pii"),
    # Bank card
    (r"(bank_card|card_no|银行卡|卡号)", "pii"),
    # Password
    (r"(password|passwd|pwd|密码|口令)", "confidential"),
    # Salary
    (r"(salary|wage|薪资|工资|收入|薪酬)", "confidential"),
    # IP address
    (r"(ip_addr|client_ip|ip_address|IP地址)", "internal"),
]

# Data type patterns
_DTYPE_PATTERNS = [
    # varchar(18) might be ID card
    (r"varchar\(18\)", "pii", "可能是身份证号"),
    # varchar(11) might be phone
    (r"varchar\(11\)", "pii", "可能是手机号"),
    # varchar(32) + hash might be password
    (r"varchar\(32\)", "internal", "可能是哈希值"),
]


def detect_sensitivity_by_name(
    column_name: str,
    column_comment: str = "",
) -> Optional[str]:
    """Detect sensitivity level by field name and comment.

    Returns sensitivity level or None if not detected.
    """
    text = f"{column_name} {column_comment}".lower()

    for pattern, level in _PII_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return level

    return None


def detect_sensitivity_by_type(
    data_type: str,
    column_name: str = "",
) -> Optional[str]:
    """Detect sensitivity level by data type.

    Returns sensitivity level or None if not detected.
    """
    dtype_lower = data_type.lower()

    for pattern, level, reason in _DTYPE_PATTERNS:
        if re.search(pattern, dtype_lower):
            # Additional check: column name should also be suspicious
            name_lower = column_name.lower()
            suspicious_names = ["id", "card", "phone", "mobile", "key", "secret", "token"]
            if any(s in name_lower for s in suspicious_names):
                return level

    return None


def classify_column(
    column_name: str,
    data_type: str,
    column_comment: str = "",
) -> str:
    """Classify column sensitivity level.

    Returns: 'public', 'internal', 'pii', or 'confidential'
    """
    # Check by name/comment first
    level = detect_sensitivity_by_name(column_name, column_comment)
    if level:
        return level

    # Check by data type
    level = detect_sensitivity_by_type(data_type, column_name)
    if level:
        return level

    return "public"


def should_exclude(sensitivity_level: str) -> bool:
    """Check if column should be excluded from LLM context.

    pii and confidential columns are excluded.
    """
    return sensitivity_level in ("pii", "confidential")


def should_warn(sensitivity_level: str) -> bool:
    """Check if column should show warning to user."""
    return sensitivity_level in ("internal", "pii", "confidential")


# ── Batch Classification ─────────────────────────────────────────────

def classify_columns(columns: list[dict]) -> list[dict]:
    """Classify a list of columns and add sensitivity_level field.

    Args:
        columns: List of column dicts with table_name, column_name, data_type, column_comment

    Returns:
        Same list with sensitivity_level added to each column
    """
    stats = {"public": 0, "internal": 0, "pii": 0, "confidential": 0}

    for col in columns:
        level = classify_column(
            col.get("column_name", ""),
            col.get("data_type", ""),
            col.get("column_comment", ""),
        )
        col["sensitivity_level"] = level
        stats[level] = stats.get(level, 0) + 1

    logger.info(
        "Column classification: public=%d, internal=%d, pii=%d, confidential=%d",
        stats["public"], stats["internal"], stats["pii"], stats["confidential"],
    )

    return columns


def filter_sensitive_columns(columns: list[dict]) -> list[dict]:
    """Filter out sensitive columns (pii and confidential).

    Returns only public and internal columns.
    """
    classified = classify_columns(columns)
    return [c for c in classified if not should_exclude(c.get("sensitivity_level", "public"))]

# ── Sensitive Keyword Detection ───────────────────────────────────────

# Keywords that indicate sensitive data requests
_SENSITIVE_KEYWORDS = [
    # PII
    "手机号", "电话号码", "手机", "电话",
    "身份证", "身份证号", "证件号",
    "邮箱", "邮件地址",
    "地址", "住址", "家庭地址",
    "真实姓名", "全名",
    "银行卡", "卡号", "银行账号",
    # Confidential
    "密码", "口令", "密码哈希",
    "薪资", "工资", "收入", "薪酬", "月薪", "年薪",
    "社保号", "社保账号",
    # English
    "phone", "mobile", "email", "address",
    "password", "salary", "id_card", "bank_card",
]


def check_sensitive_keywords(question: str) -> Optional[str]:
    """Check if question contains sensitive keywords.

    Returns warning message if sensitive keyword detected, None otherwise.
    """
    q_lower = question.lower()

    for keyword in _SENSITIVE_KEYWORDS:
        if keyword.lower() in q_lower:
            return f"您的查询可能涉及敏感数据（{keyword}），该数据受权限保护无法查询。如有需要请联系管理员。"

    return None


def get_sensitive_keywords() -> list[str]:
    """Get list of sensitive keywords."""
    return _SENSITIVE_KEYWORDS.copy()
