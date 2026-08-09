"""Prompt Loader — loads prompts from config/ directory.

Loads from config/skills/ and config/rules/ directories.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "config"

_DIALECT_MAP = {
    "Doris": "doris",
    "MySQL": "mysql",
    "Elasticsearch": "elasticsearch",
}


def _load_md(skill: str, component: str = None, dialect: str = None) -> str:
    """Load a markdown prompt file."""
    if dialect:
        path = _PROMPTS_DIR / "skills" / skill / "dialects" / f"{dialect}.md"
    else:
        path = _PROMPTS_DIR / "skills" / skill / f"{component}.md"

    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to load %s: %s", path, e)
    return ""


def get_sql_prompt(engine: str = "Doris", query_limit: bool = True) -> dict:
    """Build the full SQL generation prompt."""
    dialect = _DIALECT_MAP.get(engine, engine.lower())

    system = _load_md("nl2sql", "system")
    rules = _load_md("nl2sql", "rules")
    dialect_rules = _load_md("nl2sql", dialect=dialect)

    parts = [system]
    if rules:
        parts.append(rules)
    if dialect_rules:
        parts.append(f"\n## 方言规则\n{dialect_rules}")

    return {
        "system": "\n\n".join(parts),
        "basic_info_tpl": "## 以下是数据库与表结构信息\n<Info>\n<db-engine> {engine} </db-engine>\n<m-schema>\n{schema}\n</m-schema>\n</Info>",
        "terminologies_tpl": "以下是你可以参考的术语：\n{terminologies}",
        "data_training_tpl": "以下是你可以参考的SQL示例：\n{data_training}",
        "user_tpl": "<background-infos>\n<current-time>\n{current_time}\n</current-time>\n</background-infos>\n{error_msg}\n<user-question>\n{question}\n</user-question>",
        "regenerate_hint": "你之前生成的回答不符合预期，请再次检查规则和信息，重新回答。",
    }


def get_correction_prompt(engine: str = "Doris") -> str:
    """Get the regeneration hint for SQL correction."""
    return "你之前生成的回答不符合预期或者系统出现了其他问题，请再次检查提示词内要求的规则和提供的信息，重新回答："


def get_chart_prompt(engine: str = "Doris") -> dict:
    """Build the chart configuration generation prompt."""
    return {
        "system": _load_md("chart", "system"),
        "rules": "",
        "user_tpl": "<user-question>\n{question}\n</user-question>\n<sql>\n{sql}\n</sql>\n<m-schema>\n{schema}\n</m-schema>\n<chart-type>\n{chart_type}\n</chart-type>",
    }


def get_datasource_prompt() -> dict:
    """Build the datasource selection prompt."""
    return {
        "system": "你是数据分析师，根据用户提问和数据源列表，找出最匹配的数据源。\n\n返回格式：{\"id\": 数据源ID}\n如果没有匹配：{\"fail\": \"没有找到匹配的数据源\"}",
        "user_tpl": "### 数据源列表:\n{data}\n\n### 问题:\n{question}",
    }


def get_analysis_prompt() -> dict:
    """Build the data analysis prompt."""
    return {
        "system": _load_md("analysis", "system"),
        "user_tpl": "<fields>\n{fields}\n</fields>\n\n<data>\n{data}\n</data>",
    }


def get_predict_prompt() -> dict:
    """Build the data prediction prompt."""
    return {
        "system": _load_md("prediction", "system"),
        "user_tpl": "<fields>\n{fields}\n</fields>\n\n<data>\n{data}\n</data>",
    }
