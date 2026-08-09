"""Execution Layer data models."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HealthStatus:
    """执行层健康检查结果."""
    healthy: bool
    message: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"healthy": self.healthy, "message": self.message, "details": self.details}


@dataclass
class ExecutionContext:
    """执行上下文 — 由宿主在派发任务时注入(工作空间资源、身份等)."""
    workspace_id: int = 0
    datasource_id: int = 0
    user_id: int = 0
    username: str = ""
    model_id: Optional[int] = None
    system_prompt: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class ExecutionTask:
    """执行任务."""
    task_id: str
    question: str
    history: list = field(default_factory=list)
    context: ExecutionContext = field(default_factory=ExecutionContext)
    stream: bool = False
    timeout: int = 300
    # 多模态附件清单: [{id, filename, category, path}]
    attachments: list = field(default_factory=list)


@dataclass
class ExecutionResult:
    """执行结果."""
    success: bool
    output: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "meta": self.meta,
        }


@dataclass
class DiscoveredCLI:
    """自动发现的 CLI 工具."""
    name: str
    path: str
    version: str = ""
    capabilities: list = field(default_factory=list)
    default_command: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "capabilities": self.capabilities,
            "default_command": self.default_command,
        }
