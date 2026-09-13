"""Prompt Loader — loads prompts via config.loader (DB override → file fallback).

Delegates to services.datamind.config.loader so that permission-boundary / dialect
edits made in the admin UI take effect on SQL generation (subject to the loader's
TTL cache; call loader.clear_prompt_cache() right after a DB update).
"""

import logging

logger = logging.getLogger(__name__)

_DIALECT_MAP = {
    "Doris": "doris",
    "MySQL": "mysql",
    "Elasticsearch": "elasticsearch",
}


def _load_md(skill: str, component: str = None, dialect: str = None, workspace_id: int = 0) -> str:
    """Load a prompt via the DB-aware loader (DB override first, then .md file)."""
    from services.datamind.config.loader import load_prompt

    try:
        if dialect:
            return load_prompt(skill, None, dialect=dialect, workspace_id=workspace_id) or ""
        return load_prompt(skill, component, workspace_id=workspace_id) or ""
    except Exception as e:
        logger.warning("Failed to load prompt skill=%s component=%s dialect=%s: %s",
                       skill, component, dialect, e)
        return ""


def get_sql_prompt(engine: str = "Doris", query_limit: bool = True, workspace_id: int = 0) -> dict:
    """Build the full SQL generation prompt (guardrail + system + rules + dialect)."""
    dialect = _DIALECT_MAP.get(engine, engine.lower())

    from services.datamind.config.guardrails import get_guardrail_prompt
    guardrail = get_guardrail_prompt(workspace_id)

    system = _load_md("nl2sql", "system", workspace_id=workspace_id)
    rules = _load_md("nl2sql", "rules", workspace_id=workspace_id)
    dialect_rules = _load_md("nl2sql", dialect=dialect, workspace_id=workspace_id)

    parts = []
    if guardrail:
        parts.append(guardrail)
    if system:
        parts.append(system)
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
