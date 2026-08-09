"""Data Analysis Agent — handles data queries via SQL generation and execution.

This agent uses LLM-driven tool calling to autonomously:
1. Select relevant tables
2. Retrieve metadata
3. Generate SQL
4. Validate and execute
5. Analyze results

It reuses the existing system tools from the orchestrator.
"""

import logging
import time

from services.datamind.agent.base import BaseAgent, AgentResult
from services.datamind.agent.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


class DataAnalysisAgent(BaseAgent):
    """Agent for data analysis tasks via SQL.

    Uses LLM-driven tool calling loop instead of hardcoded pipeline.
    Reuses existing system tools (select_tables, retrieve_metadata, execute_sql, etc.)
    """

    name = "data_analysis_agent"
    description = "数据分析 Agent：检索元数据、生成SQL、执行查询、分析结果。适用于查询数据、统计分析、生成报表等场景。"
    system_prompt = """你是数据分析助手，负责将自然语言问题转换为 SQL 查询并执行，然后分析结果。

## 工作流程（高效模式）

**核心原则：减少工具调用轮次，能合并的步骤合并执行。**

### 快速路径（简单查询）
对于简单明确的查询（"有几个表"、"查一下XX表的数据"、"统计XX的数量"），直接执行：
```
select_tables → retrieve_metadata → generate_sql → execute_sql → 回答
```
跳过 validate_sql（generate_sql 已内置校验），跳过 think（无需额外推理）。

### 标准路径（复杂查询）
1. **检索元数据**：调用 `retrieve_metadata` 获取表结构、关联关系、业务术语
2. **生成并执行 SQL**：调用 `generate_sql` → `execute_sql`
3. **分析结果**：对查询结果进行分析和解读

### 合并调用技巧
- `retrieve_metadata` 可以一次传入多个表名，不要逐个调用
- `select_tables` 返回结果后，直接将所有表名传给 `retrieve_metadata`
- 对于简单查询，可以跳过 `validate_sql`，直接 `generate_sql` → `execute_sql`
- 不要先 `think` 再调工具 — 直接调工具，边执行边思考

## 注意事项

- 表名和字段名必须通过工具确认，不要编造
- 生成 SQL 前必须先获取元数据
- 如果执行失败，分析错误原因后重试
- 如果找不到相关表，如实告知用户
"""

    # DataAnalysis may need more iterations for complex queries
    # Each LLM call takes 5-15s, full pipeline needs 5-6 calls + tool execution
    max_iterations: int = 15
    max_time_seconds: int = 180

    async def run(
        self,
        question: str,
        history: list[dict] = None,
        datasource_id: int = 0,
        model_id: int = None,
        **kwargs,
    ) -> AgentResult:
        """Execute the data analysis agent with LLM-driven tool calling."""
        self._start_time = time.time()

        # 1. Get system tools for data analysis
        tools = self._get_system_tools()

        # 2. Build system prompt (enhanced with context)
        system_prompt = self._build_system_prompt(datasource_id)

        # 3. Execute with AgentLoop
        loop = AgentLoop(
            agent=self,
            tools=tools,
            execute_tool_fn=lambda name, args: self._execute_system_tool(
                name, args, datasource_id, model_id,
                user_id=kwargs.get("user_id"),
                username=kwargs.get("username"),
                question=question,
            ),
        )

        result = await loop.run(
            question=question,
            system_prompt=system_prompt,
            model_id=model_id,
            history=history,
        )

        result.agent_name = self.name
        result.mode = "data_analysis"
        result.timings["total"] = round(time.time() - self._start_time, 2)

        # Extract SQL from tool calls if available
        for tc in result.tool_calls:
            if tc.get("tool") == "execute_sql" and not result.sql:
                result.sql = tc.get("arguments", {}).get("sql", "")

        return result

    def _get_system_tools(self) -> list[dict]:
        """Get system tools for data analysis."""
        from services.datamind.nl2sql.orchestrator.agent_constants import SYSTEM_TOOLS

        # Filter to data analysis relevant tools
        allowed_tools = {
            "select_tables", "list_tables", "search_columns",
            "retrieve_metadata", "get_sample_data", "search_business_terms",
            "search_relations", "get_sql_rules", "validate_sql", "execute_sql",
            "generate_sql", "analyze_result", "think", "ask_user",
            "load_analysis_skill",
        }

        return [t for t in SYSTEM_TOOLS if t["name"] in allowed_tools]

    def _build_system_prompt(self, datasource_id: int = 0) -> str:
        """Build system prompt with datasource context and analysis skill summary."""
        from services.datamind.nl2sql.sql.query_executor import _get_ds_conn_params
        from services.datamind.config.skill_loader import get_skill_summary_for_prompt

        # Get engine info
        ds_params = _get_ds_conn_params(datasource_id)
        db_type = ds_params.get("db_type", "doris")
        engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
        engine = engine_map.get(db_type, db_type.capitalize())

        # Get analysis skill summary
        skill_summary = get_skill_summary_for_prompt()

        return f"""{self.system_prompt}

## 运行环境

- 数据引擎: {engine}
- 数据源ID: {datasource_id or '默认'}

{skill_summary}
"""

    async def _execute_system_tool(
        self,
        tool_name: str,
        tool_input: dict,
        datasource_id: int,
        model_id: int,
        user_id: int = None,
        username: str = None,
        question: str = "",
    ) -> str:
        """Execute a system tool."""
        from services.datamind.nl2sql.orchestrator.agent_pipeline import _execute_system_tool

        try:
            result = await _execute_system_tool(
                tool_name, tool_input,
                datasource_id, model_id,
                user_id, username,
                question=question,
            )
            return result
        except Exception as e:
            logger.error("[DataAnalysisAgent] Tool %s failed: %s", tool_name, e)
            raise


def create_data_analysis_agent() -> DataAnalysisAgent:
    """Factory function to create a data analysis agent instance."""
    return DataAnalysisAgent()
