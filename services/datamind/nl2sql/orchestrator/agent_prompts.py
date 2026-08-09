"""Agent Pipeline Prompts — system prompt, token estimation, compaction.

Contains:
- _build_agent_system_prompt: 4-layer system prompt
- _estimate_tokens / _estimate_messages_tokens: rough token counting
- _build_compaction_summary: structured summary for auto-compact
"""

import json
from datetime import datetime


# ── Token Estimation ────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token count estimation (≈3 chars per token for mixed CN/EN)."""
    return max(1, len(text) // 3)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(json.dumps(block, ensure_ascii=False, default=str))
                elif hasattr(block, "text"):
                    total += estimate_tokens(block.text)
                elif hasattr(block, "input"):
                    total += estimate_tokens(json.dumps(block.input, ensure_ascii=False, default=str))
    return total


# ── System Prompt Builder ────────────────────────────────────────────

def build_agent_system_prompt(
    engine: str,
    datasource_name: str,
    question: str,
    prev_context: dict,
    tools_listing: str,
) -> str:
    """Build the 4-layer system prompt for the agent.

    Layer 1 (static): Identity + capability
    Layer 2 (static): Behavior principles
    --- DYNAMIC BOUNDARY ---
    Layer 3 (dynamic): Runtime environment + conversation context
    Layer 4 (dynamic): Current question

    Metadata enters via tool results, not here.
    """
    current_date = datetime.now().strftime("%Y-%m-%d %A")

    # ── Static Layer ──
    static = f"""你是 ChatBI 数据分析助手，通过工具与数据源交互，将用户的自然语言问题转化为 SQL 查询并执行。

你的核心能力：
- 理解中文自然语言的数据查询意图
- 检索和理解数据库元数据（表结构、字段含义、关联关系）
- 生成并执行 SQL 查询
- 解读查询结果并给出分析

你通过工具完成所有工作。不要凭空猜测表名或字段名——必须通过工具确认。

## 可用工具

### 信息检索（只读，无副作用）
- select_tables: 根据问题检索相关表（首选，BM25+向量混合检索）
- list_tables: 按关键词搜索表名（补充用，一次传多个关键词）
- search_columns: 跨表搜索字段名（当你不确定某字段在哪个表时）
- retrieve_metadata: 获取表的完整元数据（结构、关联关系、业务术语、SQL模板），一次传入所有需要的表名
- get_sample_data: 预览表的前5行数据（理解实际数据内容）
- search_business_terms: 搜索业务术语定义和计算公式
- search_relations: 查询表间 JOIN 关系（多表查询时必须确认）
- get_sql_rules: 获取当前数据源的 SQL 语法规则

### SQL 操作
- validate_sql: 校验 SQL 语法和安全性（execute_sql 前建议先调用）
- execute_sql: 执行 SQL 查询并返回结果
- generate_sql: 调用 LLM 辅助生成 SQL（可选，你也可以直接写 SQL）

### 推理与交互
- think: 结构化思考（无副作用，用于复杂问题的推理规划）
- ask_user: 向用户提问（暂停流程等待回答，提供选项让用户点击）

### 高级
- analyze_result: 对查询结果进行深度分析（趋势、异常、关联）

## 工作原则

### 思考优先
- 在调用工具前，先思考：用户到底在问什么？需要哪些表？最高效的查询路径是什么？
- 复合问题（涉及多个维度）先拆解，再逐个查询
- 不要在没有元数据的情况下尝试写 SQL

### 最小工具调用
- 能一次完成的不要分两次（如 retrieve_metadata 一次传入所有表名）
- 如果你已经从之前的工具结果中知道了答案，直接使用，不要重复调用
- select_tables 只需调用一次，结果不满意时用 list_tables 补充搜索

### 验证驱动
- 生成 SQL 后，先 validate_sql 再 execute_sql
- 执行失败时，先分析错误原因（检查表名/字段名是否正确），再决定是修正 SQL、换表、还是问用户
- 结果为空不代表查询错误——可能是数据本身就没有匹配项

### 不确定时问用户
- 无法确定用户要查哪个表 → ask_user 并给出选项
- 问题有多种理解方式 → ask_user 并给出选项
- 时间范围、过滤条件不明确 → ask_user

### 诚实报告
- 如果查询结果为空，如实告知，不要编造数据
- 如果找不到相关表，说明原因，不要猜测表名
- 如果 SQL 执行失败且无法修复，明确告知用户

### 安全约束
- SQL 中的表名和字段名必须通过工具确认，禁止编造
- 不要执行 DELETE、UPDATE、DROP 等写操作
- 查询结果中的数据直接展示，不要篡改"""

    # ── Dynamic Layer ──
    dynamic = f"""## 运行环境
- 数据引擎: {engine}
- 当前日期: {current_date}"""

    if prev_context:
        parts = []
        if prev_context.get("question"):
            parts.append(f"上一次查询: {prev_context['question']}")
        if prev_context.get("sql"):
            parts.append(f"生成的 SQL: {prev_context['sql'][:300]}")
        if prev_context.get("row_count") is not None:
            parts.append(f"结果: {prev_context['row_count']} 行, 耗时 {prev_context.get('elapsed_ms', 0)}ms")
        if prev_context.get("feedback"):
            fb = prev_context["feedback"]
            if fb == "down":
                parts.append("用户反馈: 不满意，请重新分析问题")
            elif fb == "up":
                parts.append("用户反馈: 满意")
        if parts:
            dynamic += "\n\n## 对话上下文\n" + "\n".join(f"- {p}" for p in parts)

    dynamic += f"""

## 当前用户问题
{question}"""

    return f"{static}\n\n---\n\n{dynamic}"


# ── Context Compaction ──────────────────────────────────────────────

def build_compaction_summary(
    messages: list[dict],
    question: str,
    confirmed_context: dict,
    all_tool_calls: list[dict],
) -> str:
    """Build a structured summary of compacted messages for context continuation."""
    confirmed_tables = set()
    executed_sqls = []
    key_findings = []
    tools_used = []

    for tc in all_tool_calls:
        tool = tc["tool"]
        tools_used.append(tool)

        if tool == "select_tables":
            try:
                result = json.loads(tc["result"])
                confirmed_tables.update(result.get("tables", []))
            except (json.JSONDecodeError, TypeError):
                pass
        elif tool == "retrieve_metadata":
            try:
                args = tc.get("arguments", {})
                confirmed_tables.update(args.get("table_names", []))
            except Exception:
                pass
        elif tool == "execute_sql":
            sql = tc.get("arguments", {}).get("sql", "")
            if sql:
                executed_sqls.append(sql[:200])
            try:
                result = json.loads(tc["result"])
                row_count = result.get("row_count", 0)
                key_findings.append(f"execute_sql: {row_count} rows returned")
            except (json.JSONDecodeError, TypeError):
                pass
        elif tool == "ask_user":
            key_findings.append(f"ask_user: {tc.get('result', '')[:100]}")

    timeline_lines = []
    for tc in all_tool_calls[-8:]:
        elapsed = tc.get("elapsed", 0)
        timeline_lines.append(f"  - {tc['tool']}: {tc.get('result_preview', '')[:80]} ({elapsed:.1f}s)")

    user_confirmed = ""
    if confirmed_context:
        user_confirmed = "\n- 用户确认的信息:\n"
        for k, v in confirmed_context.items():
            user_confirmed += f"  - {k}: {v}\n"

    summary = f"""会话摘要:
- 范围: 压缩了之前的对话消息
- 用户问题: {question}
- 已确认的表: {', '.join(sorted(confirmed_tables)) if confirmed_tables else '无'}
- 已执行的 SQL: {len(executed_sqls)} 条
- 关键发现: {'; '.join(key_findings[-5:]) if key_findings else '无'}
- 工具调用历史: {', '.join(tools_used[-10:]) if tools_used else '无'}{user_confirmed}
- 关键时间线:
{chr(10).join(timeline_lines) if timeline_lines else '  (无)'}

注意: "已确认的表"是数据库表名，不是 ES 索引名。ES 索引名必须通过 list_indices 或用户指定获取。

最近的消息已原文保留。从上次中断的地方继续，不要复述摘要内容。"""

    return summary
