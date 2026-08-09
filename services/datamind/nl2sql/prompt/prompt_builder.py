"""Prompt Builder — constructs NL2SQL prompts using YAML templates with RAG context.

Supports:
- Template-based prompts with dialect-specific rules (Doris, MySQL, etc.)
- M-Schema format for table metadata
- XML terminology format for business terms
- JSON output format: {"success":true,"sql":"...","tables":[...],"chart-type":"..."}
"""

from datetime import datetime
from services.datamind.nl2sql.sql.template_loader import get_sql_prompt, get_correction_prompt


def _to_m_schema(
    table_info: list[dict],
    column_metadata: list[dict],
    database: str = "adh",
) -> str:
    """Convert RAG table info + column metadata to M-Schema format.

    Args:
        table_info: Table-level metadata (table_name, table_comment, table_business_desc, ...).
        column_metadata: Column-level metadata (table_name, column_name, data_type, column_comment, ...).
        database: Database name for display.
    """
    if not table_info and not column_metadata:
        return ""

    # Build table description map from table_info
    table_desc_map = {}
    for row in table_info:
        tname = row["table_name"]
        parts = []
        if row.get("table_comment"):
            parts.append(row["table_comment"])
        if row.get("table_business_desc"):
            parts.append(row["table_business_desc"])
        table_desc_map[tname] = "，".join(parts) if parts else ""

    # Build columns map from column_metadata
    columns_map = {}
    for row in column_metadata:
        tname = row["table_name"]
        if tname not in columns_map:
            columns_map[tname] = []
        columns_map[tname].append({
            "name": row["column_name"],
            "type": row["data_type"],
            "comment": row.get("column_comment", ""),
            "business_desc": row.get("business_desc", ""),
            "is_key": row.get("is_key", "false"),
        })

    # Merge: use all tables from both sources
    all_tables = set(table_desc_map.keys()) | set(columns_map.keys())

    lines = [f"【DB_ID】 {database}, ChatBI 数据分析"]
    lines.append("【Schema】")
    for tname in sorted(all_tables):
        desc = table_desc_map.get(tname, "")
        comment = f", {desc}" if desc else ""
        lines.append(f"# Table: {tname}{comment}")
        lines.append("[")
        for col in columns_map.get(tname, []):
            parts = [f"{col['name']}: {col['type']}"]
            if col["is_key"] == "true":
                parts.append("Primary key")
            if col["comment"]:
                parts.append(col["comment"])
            if col.get("business_desc"):
                parts.append(col["business_desc"])
            lines.append(f"({', '.join(parts)}),")
        lines.append("]")
        lines.append("")
    return "\n".join(lines)


def _to_er_diagram(table_relations: list[dict]) -> str:
    """Convert RAG table relations to ER diagram text format.

    Args:
        table_relations: List of relation dicts with source_table, source_column,
                        target_table, target_column, relation_type, join_type, description.
    """
    if not table_relations:
        return ""

    lines = ["【表关联关系 ER 图】"]
    lines.append("以下是表之间的关联关系，生成 JOIN 查询时请严格参照：")
    lines.append("")
    for rel in table_relations:
        src_cols = [c.strip() for c in rel['source_column'].split(',') if c.strip()]
        tgt_cols = [c.strip() for c in rel['target_column'].split(',') if c.strip()]
        rel_type = rel.get("relation_type", "1:N")
        join_type = rel.get("join_type", "INNER")
        desc = rel.get("description", "")
        desc_part = f" — {desc}" if desc else ""

        src_table = rel['source_table']
        tgt_table = rel['target_table']

        if len(src_cols) == 1:
            # Single field mapping
            lines.append(f"  {src_table}.{src_cols[0]} → {tgt_table}.{tgt_cols[0]} ({rel_type}, {join_type} JOIN){desc_part}")
        else:
            # Composite key: expand each pair for clarity
            pairs = []
            for i in range(max(len(src_cols), len(tgt_cols))):
                s = f"{src_table}.{src_cols[i]}" if i < len(src_cols) else "?"
                t = f"{tgt_table}.{tgt_cols[i]}" if i < len(tgt_cols) else "?"
                pairs.append(f"{s} = {t}")
            join_cond = " AND ".join(pairs)
            lines.append(f"  {join_cond} ({rel_type}, {join_type} JOIN){desc_part}")
    lines.append("")
    lines.append("注意：")
    lines.append("- 生成多表查询时，必须使用上述关联关系进行 JOIN")
    lines.append("- 默认使用指定的 JOIN 类型（未指定时使用 INNER JOIN）")
    lines.append("- 如果查询涉及多个关联表，确保 JOIN 顺序合理，避免笛卡尔积")
    return "\n".join(lines)


def _to_terminologies(business_terms: list[dict]) -> str:
    """Convert RAG business terms to XML terminology format."""
    if not business_terms:
        return ""

    parts = ["<terminologies>"]
    for term in business_terms:
        parts.append("  <terminology>")
        parts.append("    <words>")
        parts.append(f"      <word>{term['term_cn']}</word>")
        if term.get("term_en"):
            parts.append(f"      <word>{term['term_en']}</word>")
        parts.append("    </words>")
        desc_parts = []
        if term.get("description"):
            desc_parts.append(term["description"])
        if term.get("target_column"):
            desc_parts.append(f"对应字段: {term['target_column']}")
        if term.get("calculation"):
            desc_parts.append(f"计算公式: {term['calculation']}")
        desc = "；".join(desc_parts) if desc_parts else term["term_cn"]
        parts.append(f"    <description>{desc}</description>")
        parts.append("  </terminology>")
    parts.append("</terminologies>")
    return "\n".join(parts)


def _strip_sql_comments(sql: str) -> str:
    """Strip SQL comments from template SQL to prevent LLM from copying them.

    Removes:
    - Single-line comments: -- comment
    - Multi-line comments: /* comment */
    - Collapses multiple blank lines
    """
    import re
    # Remove single-line comments (-- to end of line)
    sql = re.sub(r'--[^\n]*', '', sql)
    # Remove multi-line comments (/* ... */)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # Collapse multiple blank lines
    sql = re.sub(r'\n\s*\n', '\n', sql)
    return sql.strip()


def _to_sql_examples(sql_templates: list[dict]) -> str:
    """Convert RAG SQL templates to XML sql-examples format."""
    if not sql_templates:
        return ""

    parts = ["<sql-examples>"]
    for tpl in sql_templates:
        parts.append("  <example>")
        parts.append(f"    <question>{tpl['template_name']}</question>")
        if tpl.get("description"):
            parts.append(f"    <description>{tpl['description']}</description>")
        # Strip comments from template SQL to prevent LLM from copying them
        clean_sql = _strip_sql_comments(tpl['sql_template'])
        parts.append(f"    <suggestion-answer>{clean_sql}</suggestion-answer>")
        parts.append("  </example>")
    parts.append("</sql-examples>")
    return "\n".join(parts)


def _extract_template_rules(sql_templates: list[dict]) -> str:
    """Extract rules from matched SQL templates and format as additional Rules block.

    Each template can have a 'rules' field with rules that apply when that template is relevant.
    These are injected into the prompt as <Template-Rules> after the main <Rules>.
    """
    import logging
    logger = logging.getLogger(__name__)

    rules = []
    for tpl in sql_templates:
        logger.debug("Template '%s': rules=%s", tpl.get("template_name"), repr(tpl.get("rules")))
        if tpl.get("rules"):
            rules.append(f"  <rule source=\"{tpl['template_name']}\">")
            rules.append(f"    {tpl['rules']}")
            rules.append(f"  </rule>")

    if not rules:
        logger.debug("No template rules found across %d templates", len(sql_templates))
        return ""

    result = "<Template-Rules>\n  <instruction>以下规则来自匹配的SQL模板，与主规则具有同等优先级，必须严格遵守：</instruction>\n" + "\n".join(rules) + "\n</Template-Rules>"
    logger.debug("Extracted template rules:\n%s", result)
    return result


def _build_conversation_block(history: list[dict]) -> str:
    """Format conversation history into context for the prompt."""
    if not history:
        return ""

    lines = ["## 对话历史"]
    for msg in history[-6:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        sql = msg.get("sql", "")
        if role == "user":
            lines.append(f"用户: {content}")
        elif role == "assistant":
            if sql:
                lines.append(f"助手SQL: {sql}")
            elif content:
                lines.append(f"助手: {content[:150]}")
    return "\n".join(lines)


def build_nl2sql_prompt(
    question: str,
    table_info: list[dict],
    column_metadata: list[dict],
    sql_templates: list[dict],
    business_terms: list[dict],
    table_relations: list[dict] = None,
    conversation_history: list[dict] = None,
    engine: str = "Doris",
    feedback_context: str = "",
) -> list[dict]:
    """Build messages for NL2SQL using template-based prompts.

    Returns:
        List of message dicts for LLM API.
    """
    tpl = get_sql_prompt(engine, query_limit=True)

    # Build context blocks
    schema_text = _to_m_schema(table_info, column_metadata)
    er_text = _to_er_diagram(table_relations or [])
    terminologies_text = _to_terminologies(business_terms)
    examples_text = _to_sql_examples(sql_templates)
    template_rules_text = _extract_template_rules(sql_templates)
    history_text = _build_conversation_block(conversation_history or [])

    # Compose system message: system rules + context
    context_parts = [tpl["system"]]

    # Add template-specific rules (after main rules, before examples)
    if template_rules_text:
        context_parts.append(template_rules_text)

    # Add basic info (table structure)
    if schema_text:
        basic_info = tpl["basic_info_tpl"].format(
            engine=engine,
            schema=schema_text,
            sample_data="",
        )
        context_parts.append(basic_info)

    # Add ER diagram (table relations)
    if er_text:
        context_parts.append(er_text)

    # Add terminologies
    if terminologies_text:
        context_parts.append(terminologies_text)

    # Add SQL examples
    if examples_text:
        context_parts.append(examples_text)

    # Add conversation history
    if history_text:
        context_parts.append(history_text)

    system_content = "\n\n".join(context_parts)

    # Compose user message
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Inject feedback context if present
    error_msg = ""
    if feedback_context:
        error_msg = f"[反馈] {feedback_context}"

    user_content = tpl["user_tpl"].format(
        lang="中文",
        current_time=now,
        error_msg=error_msg,
        question=question,
        change_title="False",
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_nl2sql_prompt_with_supplement(
    question: str,
    table_info: list[dict],
    column_metadata: list[dict],
    sql_templates: list[dict],
    business_terms: list[dict],
    table_relations: list[dict] = None,
    conversation_history: list[dict] = None,
    engine: str = "Doris",
    feedback_context: str = "",
) -> list[dict]:
    """Build messages for NL2SQL with metadata supplement support (Deep mode).

    The LLM can either:
    1. Generate SQL directly (if metadata is sufficient)
    2. Return JSON with need_more=true and required_tables/columns (if metadata is insufficient)

    Returns:
        List of message dicts for LLM API.
    """
    tpl = get_sql_prompt(engine, query_limit=True)

    # Build context blocks
    schema_text = _to_m_schema(table_info, column_metadata)
    er_text = _to_er_diagram(table_relations or [])
    terminologies_text = _to_terminologies(business_terms)
    examples_text = _to_sql_examples(sql_templates)
    template_rules_text = _extract_template_rules(sql_templates)
    history_text = _build_conversation_block(conversation_history or [])

    # Compose system message
    context_parts = [tpl["system"]]

    if template_rules_text:
        context_parts.append(template_rules_text)

    if schema_text:
        basic_info = tpl["basic_info_tpl"].format(
            engine=engine,
            schema=schema_text,
            sample_data="",
        )
        context_parts.append(basic_info)

    if er_text:
        context_parts.append(er_text)

    if terminologies_text:
        context_parts.append(terminologies_text)

    if examples_text:
        context_parts.append(examples_text)

    if history_text:
        context_parts.append(history_text)

    # Add supplement instruction
    supplement_instruction = """
<元数据补充规则>
如果当前提供的元数据不足以生成准确的SQL，返回以下JSON格式：
{
  "need_more": true,
  "required_tables": ["需要补充的表名"],
  "required_columns": [{"table": "表名", "columns": ["字段1", "字段2"]}],
  "reason": "缺少xxx信息"
}

如果元数据充足，直接生成SQL，不要返回JSON。
</元数据补充规则>
"""
    context_parts.append(supplement_instruction)

    system_content = "\n\n".join(context_parts)

    # Compose user message
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_msg = ""
    if feedback_context:
        error_msg = f"[反馈] {feedback_context}"

    user_content = tpl["user_tpl"].format(
        lang="中文",
        current_time=now,
        error_msg=error_msg,
        question=question,
        change_title="False",
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_correction_prompt(
    question: str,
    prev_sql: str,
    table_info: list[dict],
    column_metadata: list[dict],
    business_terms: list[dict],
    table_relations: list[dict] = None,
    engine: str = "Doris",
) -> list[dict]:
    """Build a prompt for SQL correction using template-based prompts."""
    tpl = get_sql_prompt(engine, query_limit=True)
    hint = get_correction_prompt(engine)

    # Build context
    schema_text = _to_m_schema(table_info, column_metadata)
    er_text = _to_er_diagram(table_relations or [])
    terminologies_text = _to_terminologies(business_terms)

    context_parts = [tpl["system"]]
    if schema_text:
        basic_info = tpl["basic_info_tpl"].format(
            engine=engine, schema=schema_text, sample_data="",
        )
        context_parts.append(basic_info)
    if er_text:
        context_parts.append(er_text)
    if terminologies_text:
        context_parts.append(terminologies_text)

    system_content = "\n\n".join(context_parts)

    user_content = f"""{hint}

## 上一条 SQL
```sql
{prev_sql}
```

## 用户修改要求
{question}

## 输出格式
只返回一个JSON对象：
{{"success":true,"sql":"修改后的SQL语句","tables":["表名"],"chart-type":"table","brief":"对话标题"}}

若无法修改，则返回：
{{"success":false,"message":"无法修改的原因"}}"""

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_chat_prompt(question: str, history: list[dict] = None) -> list[dict]:
    """Build a prompt for general conversation (non-SQL)."""
    system = "你是 ChatBI 数据分析助手。用户正在和你进行普通对话。请用简洁友好的中文回复。如果用户问了与数据相关的问题，可以建议他们用自然语言描述查询需求。"

    messages = [{"role": "system", "content": system}]
    if history:
        for msg in history[-6:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return messages
