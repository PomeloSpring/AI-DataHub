"""ExecutionLayerAdapter — 执行层适配器抽象基类."""

from abc import ABC, abstractmethod
from typing import AsyncIterator

from services.datamind.execution.models import (
    ExecutionContext,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
)


class ExecutionLayerAdapter(ABC):
    """执行层适配器抽象基类.

    每种执行后端(builtin / cli / docker / remote)实现该接口,
    由 ExecutionLayerManager 根据数据库配置实例化。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """执行层名称(唯一标识)."""
        ...

    @property
    @abstractmethod
    def layer_type(self) -> str:
        """执行层类型: builtin | cli | docker | remote."""
        ...

    @abstractmethod
    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        """执行任务."""
        ...

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        """流式执行任务.

        逐步 yield 事件:
            {"type": "token", "text": str}   — 增量输出
            {"type": "done", "result": ExecutionResult} — 结束
        默认实现回退到一次性 execute,子类可覆盖以提供真流式。
        """
        result = await self.execute(task)
        if result.output:
            yield {"type": "token", "text": result.output}
        yield {"type": "done", "result": result}

    @abstractmethod
    async def list_tools(self) -> list[dict]:
        """列出该执行层提供的能力/工具."""
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """健康检查."""
        ...

    async def list_models(self, timeout: int = 30) -> list[str]:
        """查询可用模型列表(默认不支持,子类按需覆盖)."""
        return []

    async def inject_context(self, context: ExecutionContext):
        """注入系统能力上下文(默认无操作,子类按需覆盖)."""
