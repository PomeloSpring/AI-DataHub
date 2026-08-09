"""Execution Layer — 多执行层架构核心包.

将 AI Agent 执行能力抽象为独立的执行层,支持多种执行后端:
- builtin: 平台内置 Agent 体系(默认)
- cli: 本地 CLI 进程(opencode / qoder 等)
- docker / remote: 预留

详见 .claude/plans/execution-layer-design.md
"""

from services.datamind.execution.adapters.base import ExecutionLayerAdapter
from services.datamind.execution.manager import ExecutionLayerManager, get_execution_layer_manager
from services.datamind.execution.models import (
    ExecutionContext,
    ExecutionResult,
    ExecutionTask,
    HealthStatus,
)

__all__ = [
    "ExecutionLayerAdapter",
    "ExecutionLayerManager",
    "get_execution_layer_manager",
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionTask",
    "HealthStatus",
]
