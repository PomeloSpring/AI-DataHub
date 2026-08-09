"""SQL Agent — wraps the existing NL2SQL pipeline as an agent.

This agent handles standard data queries by generating and executing SQL.
It delegates to the existing quick_pipeline.
"""

import logging
from typing import Optional

from services.datamind.agent.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class SQLAgent(BaseAgent):
    """Agent for SQL-based data queries. Wraps existing NL2SQL pipeline."""

    name = "sql_agent"
    description = "数据查询 Agent：将自然语言转换为 SQL 并执行查询。适用于查询数据、统计分析、生成报表等场景。"
    system_prompt = "你是数据查询助手，将用户的自然语言问题转换为 SQL 并执行。"

    async def run(
        self,
        question: str,
        history: list[dict] = None,
        datasource_id: int = 0,
        model_id: int = None,
        **kwargs,
    ) -> AgentResult:
        """Execute the SQL generation pipeline.

        This agent delegates to the existing pipeline orchestrator.
        """
        from services.datamind.nl2sql.orchestrator.quick_pipeline import quick_generate

        # Collect all events from quick pipeline
        result_event = None
        for event_type, data in quick_generate(
            question=question,
            history=history or [],
            datasource_id=datasource_id,
            model_id=model_id,
            user_id=kwargs.get("user_id"),
            username=kwargs.get("username"),
            retrieval_strategy=kwargs.get("retrieval_strategy"),
        ):
            if event_type == "done":
                result_event = data

        if result_event:
            return AgentResult(
                success=not bool(result_event.get("error")),
                reply=result_event.get("reply", ""),
                sql=result_event.get("sql"),
                data=result_event.get("result"),
                error=result_event.get("error"),
                mode="sql_agent",
                timings=result_event.get("timings", {}),
                tokens=result_event.get("tokens", {}),
                warnings=result_event.get("warnings", []),
            )

        return AgentResult(
            success=False,
            reply="SQL 查询未返回结果",
            error="No result from SQL pipeline",
            mode="sql_agent",
        )


def create_sql_agent() -> SQLAgent:
    """Factory function to create a SQL agent instance."""
    return SQLAgent()
