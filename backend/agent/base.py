"""Base Agent — abstract base class for all agents.

Each agent implements a specific query scenario (SQL, log analysis, etc.)
and uses MCP tools to interact with external services.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result from an agent execution."""
    success: bool
    reply: str  # Human-readable answer
    sql: Optional[str] = None  # Generated SQL (if applicable)
    data: Optional[dict] = None  # Structured result data
    error: Optional[str] = None
    agent_name: str = ""
    mode: str = "agent"
    retryable: bool = False  # Whether the error can be retried by main agent
    retry_count: int = 0  # How many retries were attempted internally
    timings: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)  # MCP tool call log

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "reply": self.reply,
            "sql": self.sql,
            "data": self.data,
            "error": self.error,
            "agent_name": self.agent_name,
            "mode": self.mode,
            "retryable": self.retryable,
            "retry_count": self.retry_count,
            "timings": self.timings,
            "tokens": self.tokens,
            "warnings": self.warnings,
            "tool_calls": self.tool_calls,
        }


class BaseAgent(ABC):
    """Abstract base class for agents.

    Subclasses must implement:
    - name: agent identifier
    - description: what this agent does (used for routing)
    - system_prompt: LLM system prompt for this agent
    - run(): execute the agent logic

    Lifecycle:
    1. run() is called by orchestrator
    2. Agent executes autonomously with its own LLM loop
    3. Returns AgentResult when done

    Protection:
    - max_iterations: max tool call rounds
    - max_time_seconds: max execution time
    - doom_loop_threshold: consecutive identical tool calls
    - cancel_event: external cancellation signal
    """

    name: str = "base"
    description: str = ""
    system_prompt: str = ""
    is_active: bool = True

    # Protection config
    max_iterations: int = 10
    max_time_seconds: int = 60
    doom_loop_threshold: int = 3

    def __init__(self):
        """Initialize base agent with cancel event."""
        self._cancel_event = asyncio.Event()
        self._start_time: float = 0

    @abstractmethod
    async def run(
        self,
        question: str,
        history: list[dict] = None,
        datasource_id: int = 0,
        model_id: int = None,
        **kwargs,
    ) -> AgentResult:
        """Execute the agent.

        Args:
            question: User's question.
            history: Conversation history.
            datasource_id: Target datasource ID.
            model_id: LLM model ID.

        Returns:
            AgentResult with the execution outcome.
        """
        ...

    async def cancel(self):
        """Cancel the agent execution. Called by orchestrator."""
        self._cancel_event.set()
        logger.info("[Agent:%s] Cancel signal sent", self.name)

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancel_event.is_set()

    def check_timeout(self) -> bool:
        """Check if execution has exceeded max_time_seconds."""
        if self._start_time <= 0:
            return False
        return (time.time() - self._start_time) > self.max_time_seconds

    async def cleanup(self):
        """Cleanup resources after execution. Override if needed."""
        self._cancel_event = asyncio.Event()
        self._start_time = 0
