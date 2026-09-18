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
    description = "数据分析 Agent：检索元数据、产出语义意图、经语义层执行查询、分析结果。适用于查询数据、统计分析、生成报表等场景。"
    system_prompt = """你是数据分析助手，负责将自然语言问题转换为**声明式语义意图(Intent)**，
交由语义层解析绑定、施加护栏与行级权限后执行，然后分析结果。

## 核心原则（决策 A）

**你不生成、也不执行任何 SQL。** SQL 的编译、选路、护栏、RLS 改写都由语义层确定性完成。
你只负责产出一个 Intent：`{object, metrics[], dimensions[], filters[], order[], limit, time_grain}`。

## 工作流程（高效模式）

### 主路（语义查询）
```
select_tables / retrieve_metadata (确认对象与可用指标、维度)
   → run_semantic_query(intent_json) → 分析结果
```
- `run_semantic_query` 只接受声明式意图；传入任何 SQL 都会在解析阶段被拒绝。
- 对象名、指标名、维度名必须来自元数据/本体回传（不得编造）。
- 需要先看口径或预估 SQL 时，可在 intent 里置 `dry_run:true`。

### 探索旁路（仅限语义层未覆盖时）
仅当目标表**尚未在本体中绑定**（run_semantic_query 返回未绑定错误）且确需临时取数时，
才使用 `generate_sql` → `execute_sql` 作为受控旁路；一旦绑定就绪应回到主路 run_semantic_query。

## 注意事项

- 对象/指标/维度必须通过工具确认，不要编造
- 若执行失败，分析错误原因后重新产出 Intent（而不是改去手写 SQL）
- 如果找不到相关对象，如实告知用户
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
        workspace_id = int(kwargs.get("workspace_id") or 0)
        system_prompt = self._build_system_prompt(datasource_id, workspace_id)

        # 3. Execute with AgentLoop
        loop = AgentLoop(
            agent=self,
            tools=tools,
            execute_tool_fn=lambda name, args: self._execute_system_tool(
                name, args, datasource_id, model_id,
                user_id=kwargs.get("user_id"),
                username=kwargs.get("username"),
                question=question,
                workspace_id=workspace_id,
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
        """Get system tools for data analysis.

        Phase 3 (决策 A): agent 模式默认暴露 run_semantic_query + retrieval 工具，
        不默认暴露 generate_sql/execute_sql（后者仅作未绑定时的探索旁路，
        由 system_prompt 约束使用条件）。
        """
        from services.datamind.nl2sql.orchestrator.agent_constants import SYSTEM_TOOLS

        # Filter to data analysis relevant tools (intent-first)
        allowed_tools = {
            "select_tables", "list_tables", "search_columns",
            "retrieve_metadata", "get_sample_data", "search_business_terms",
            "search_relations", "get_sql_rules", "validate_sql",
            "run_semantic_query",
            "analyze_result", "think", "ask_user",
            "load_analysis_skill",
        }

        return [t for t in SYSTEM_TOOLS if t["name"] in allowed_tools]

    def _build_system_prompt(self, datasource_id: int = 0, workspace_id: int = 0) -> str:
        """Build system prompt with datasource context and analysis skill summary."""
        from services.datamind.nl2sql.sql.query_executor import _get_ds_conn_params
        from services.datamind.config.skill_loader import get_skill_summary_for_prompt
        from services.datamind.config.guardrails import get_guardrail_prompt

        # Get engine info
        ds_params = _get_ds_conn_params(datasource_id)
        db_type = ds_params.get("db_type", "doris")
        engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
        engine = engine_map.get(db_type, db_type.capitalize())

        # Get analysis skill summary
        skill_summary = get_skill_summary_for_prompt()

        # 前置护栏（角色风格 + 权限边界），按工作空间作用域
        guardrail = get_guardrail_prompt(workspace_id)

        body = f"""{self.system_prompt}

## 运行环境

- 数据引擎: {engine}
- 数据源ID: {datasource_id or '默认'}

{skill_summary}
"""
        if guardrail:
            body = f"{guardrail}\n\n---\n\n{body}"
        return body

    async def _execute_system_tool(
        self,
        tool_name: str,
        tool_input: dict,
        datasource_id: int,
        model_id: int,
        user_id: int = None,
        username: str = None,
        question: str = "",
        workspace_id: int = 0,
    ) -> str:
        """Execute a system tool."""
        from services.datamind.nl2sql.orchestrator.agent_pipeline import _execute_system_tool

        try:
            result = await _execute_system_tool(
                tool_name, tool_input,
                datasource_id, model_id,
                user_id, username,
                question=question,
                workspace_id=workspace_id,
            )
            return result
        except Exception as e:
            logger.error("[DataAnalysisAgent] Tool %s failed: %s", tool_name, e)
            raise


def create_data_analysis_agent() -> DataAnalysisAgent:
    """Factory function to create a data analysis agent instance."""
    return DataAnalysisAgent()
