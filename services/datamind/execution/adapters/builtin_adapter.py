"""BuiltInAdapter — 内置执行层.

包装平台现有 Agent 体系(BaseAgent / AgentRouter),作为默认执行层。
"""

import logging

from services.datamind.execution.adapters.base import ExecutionLayerAdapter
from services.datamind.execution.models import (
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
)

logger = logging.getLogger(__name__)


class BuiltInAdapter(ExecutionLayerAdapter):
    """内置执行层 — 委托 AgentRouter 自动路由到合适的 Agent."""

    def __init__(self, layer_name: str = "builtin", config: dict = None):
        self._name = layer_name
        self.config = config or {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def layer_type(self) -> str:
        return "builtin"

    def _ensure_agents(self):
        """确保 Agent 注册表已初始化."""
        try:
            from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import _init_agents
            _init_agents()
        except Exception as e:
            logger.warning("[ExecLayer:builtin] Agent init failed: %s", e)

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """通过 AgentRouter 执行(自动路由)."""
        self._ensure_agents()
        from services.datamind.agent.router import get_agent_router

        ctx = task.context
        try:
            result = await get_agent_router().execute(
                question=task.question,
                history=task.history,
                datasource_id=ctx.datasource_id,
                model_id=ctx.model_id,
                user_id=ctx.user_id or None,
                username=ctx.username or None,
                workspace_id=ctx.workspace_id,
                # tools 权限白名单(工作空间绑定执行层时配置,空=不限制)
                allowed_tools=self.config.get("allowed_tools") or [],
            )
            return ExecutionResult(
                success=result.success,
                output=result.reply,
                error=result.error or "",
                meta={
                    "agent_name": result.agent_name,
                    "mode": result.mode,
                    "sql": result.sql,
                    "data": result.data,
                    "tokens": result.tokens,
                    "timings": result.timings,
                },
            )
        except Exception as e:
            logger.error("[ExecLayer:builtin] execute failed: %s", e, exc_info=True)
            return ExecutionResult(success=False, error=str(e))

    async def list_tools(self) -> list[dict]:
        """列出已注册的 Agent 作为能力."""
        self._ensure_agents()
        from services.datamind.agent.router import get_all_agents

        return [
            {
                "name": agent.name,
                "description": getattr(agent, "description", ""),
                "type": "agent",
            }
            for agent in get_all_agents().values()
        ]

    async def health_check(self) -> HealthStatus:
        """检查 Agent 注册表是否有可用 Agent."""
        self._ensure_agents()
        from services.datamind.agent.router import get_all_agents

        agents = get_all_agents()
        if not agents:
            return HealthStatus(healthy=False, message="没有已注册的 Agent")
        return HealthStatus(
            healthy=True,
            message=f"{len(agents)} 个 Agent 可用",
            details={"agents": list(agents.keys())},
        )
