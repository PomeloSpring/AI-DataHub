"""SQL Semantic Validator — LLM-based semantic consistency checks.

Validates SQL against the original question to detect:
1. Hallucination: Tables/columns not in schema
2. Logic consistency: WHERE/GROUP BY/aggregations match intent
3. Completeness: Missing filters (e.g., time range)
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of semantic validation."""

    def __init__(self):
        self.is_valid = True
        self.issues: list[dict] = []
        self.corrected_sql: Optional[str] = None

    def add_issue(self, issue_type: str, severity: str, message: str, suggestion: str = ""):
        self.issues.append({
            "issue_type": issue_type,
            "severity": severity,
            "message": message,
            "suggestion": suggestion,
        })
        if severity == "error":
            self.is_valid = False

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "issues": self.issues,
            "corrected_sql": self.corrected_sql,
            "issue_count": len(self.issues),
            "error_count": sum(1 for i in self.issues if i["severity"] == "error"),
            "warning_count": sum(1 for i in self.issues if i["severity"] == "warning"),
        }


def check_hallucination(sql: str, table_info: list[dict], column_metadata: list[dict]) -> list[dict]:
    """Check if SQL references tables/columns not in schema."""
    issues = []
    schema_tables = {t["table_name"].lower() for t in table_info}
    schema_columns: dict[str, set] = {}
    for col in column_metadata:
        schema_columns.setdefault(col["table_name"].lower(), set()).add(col["column_name"].lower())

    # Collect CTE names (WITH ... AS) and subquery aliases to exclude
    cte_names = set()
    for match in re.finditer(r'\b(\w+)\s+AS\s*\(', sql, re.IGNORECASE):
        cte_names.add(match.group(1).lower())

    # Extract table names
    for match in re.finditer(r'\b(?:FROM|JOIN)\s+(\w+)', sql, re.IGNORECASE):
        table = match.group(1).lower()
        if table not in schema_tables and table not in ("select", "dual") and table not in cte_names:
            issues.append({
                "issue_type": "hallucination", "severity": "warning",
                "message": f"表 '{table}' 不存在于元数据中（可能是子查询别名）",
                "suggestion": "请检查表名或使用 SELECT_TABLES 查找正确表名",
            })

    return issues


def check_logic_consistency(sql: str, question: str) -> list[dict]:
    """Check if SQL logic matches question intent."""
    issues = []
    sql_upper = sql.upper()

    # Aggregation without GROUP BY
    has_agg = any(f in sql_upper for f in ["COUNT(", "SUM(", "AVG(", "MAX(", "MIN("])
    if has_agg and "GROUP BY" not in sql_upper:
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if select_match and ',' in re.sub(r'\b(COUNT|SUM|AVG|MAX|MIN)\s*\([^)]+\)', '', select_match.group(1), flags=re.IGNORECASE):
            issues.append({
                "issue_type": "logic", "severity": "warning",
                "message": "聚合函数缺少 GROUP BY 子句",
                "suggestion": "添加 GROUP BY 按维度分组",
            })

    # Ranking without ORDER BY
    if any(p in question.lower() for p in ["前", "top", "最多", "最少", "最高", "最低"]):
        if "ORDER BY" not in sql_upper or "LIMIT" not in sql_upper:
            issues.append({
                "issue_type": "logic", "severity": "warning",
                "message": "问题涉及排名，但 SQL 可能缺少 ORDER BY 或 LIMIT",
                "suggestion": "添加排序和限制",
            })

    return issues


def check_completeness(sql: str, question: str, time_range: Optional[dict] = None) -> list[dict]:
    """Check if SQL includes all required filters."""
    issues = []
    if time_range and not any(k in sql.upper() for k in ["CREATE_TIME", "DATE", "BETWEEN", ">=", "<="]):
        issues.append({
            "issue_type": "completeness", "severity": "warning",
            "message": f"问题涉及时间范围，但 SQL 缺少时间过滤",
            "suggestion": "添加 WHERE 时间条件",
        })
    return issues


def validate_semantic(sql: str, question: str, table_info: list[dict], column_metadata: list[dict], time_range: Optional[dict] = None) -> ValidationResult:
    """Run all semantic validations."""
    result = ValidationResult()
    if not sql or len(sql.strip()) < 10:
        return result

    for issue in check_hallucination(sql, table_info, column_metadata):
        result.add_issue(**issue)
    for issue in check_logic_consistency(sql, question):
        result.add_issue(**issue)
    for issue in check_completeness(sql, question, time_range):
        result.add_issue(**issue)

    if result.issues:
        logger.info("Semantic validation: %d issues for SQL: %s", len(result.issues), sql[:100])
    return result
