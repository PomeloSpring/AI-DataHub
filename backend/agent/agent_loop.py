"""Agent Loop — LLM-driven tool calling loop for autonomous agents.

Provides a reusable loop that:
1. Calls LLM with available tools
2. Executes tool calls
3. Detects doom loops (repetitive tool calls)
4. Supports cancellation and timeout
5. Returns when LLM produces a final answer (no more tool calls)
"""

import asyncio
import logging
import time
from typing import Optional

from backend.agent.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class AgentLoop:
    """LLM-driven tool calling loop.

    Usage:
        loop = AgentLoop(agent, tools, execute_tool_fn)
        result = await loop.run(question, system_prompt, model_id)
    """

    def __init__(
        self,
        agent: BaseAgent,
        tools: list[dict],
        execute_tool_fn,
        llm_call_fn=None,
    ):
        """Initialize the agent loop.

        Args:
            agent: The agent instance (for cancel/timeout checks).
            tools: List of tools in Anthropic tool_use format.
            execute_tool_fn: async fn(tool_name, tool_input) -> str
            llm_call_fn: async fn(messages, tools, model_id) -> LLMResponse
                         If None, uses default generate_with_tools_stream
        """
        self.agent = agent
        self.tools = tools
        self.execute_tool_fn = execute_tool_fn
        self.llm_call_fn = llm_call_fn or self._default_llm_call

        # Doom loop detection
        self.recent_tool_calls: list[tuple[str, int]] = []
        self.doom_loop_threshold = agent.doom_loop_threshold

        # Tracking
        self.tool_calls_log: list[dict] = []
        self.total_tokens = {"input": 0, "output": 0}

    async def run(
        self,
        question: str,
        system_prompt: str,
        model_id: int = None,
        history: list[dict] = None,
    ) -> AgentResult:
        """Execute the agent loop.

        Args:
            question: User's question.
            system_prompt: System prompt for the agent.
            model_id: LLM model ID.
            history: Conversation history (optional).

        Returns:
            AgentResult with the final answer or error.
        """
        self.agent._start_time = time.time()

        # Build initial messages
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for msg in history[-4:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:300]
                if content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})

        logger.info("[AgentLoop:%s] Starting with %d tools, max_iterations=%d",
                     self.agent.name, len(self.tools), self.agent.max_iterations)

        for iteration in range(self.agent.max_iterations):
            # 0. Check cancellation
            if self.agent.is_cancelled():
                logger.warning("[AgentLoop:%s] Cancelled at iteration %d", self.agent.name, iteration)
                return AgentResult(
                    success=False,
                    reply="任务被取消",
                    error="cancelled",
                    agent_name=self.agent.name,
                    tool_calls=self.tool_calls_log,
                )

            # 1. Check timeout
            if self.agent.check_timeout():
                logger.warning("[AgentLoop:%s] Timeout at iteration %d", self.agent.name, iteration)
                return AgentResult(
                    success=False,
                    reply="执行超时",
                    error="timeout",
                    agent_name=self.agent.name,
                    tool_calls=self.tool_calls_log,
                )

            # 2. Call LLM
            logger.info("[AgentLoop:%s] Iteration %d/%d, calling LLM with %d messages",
                        self.agent.name, iteration + 1, self.agent.max_iterations, len(messages))

            try:
                response = await self.llm_call_fn(messages, self.tools, model_id)
            except Exception as e:
                logger.error("[AgentLoop:%s] LLM call failed: %s", self.agent.name, e)
                return AgentResult(
                    success=False,
                    reply=f"LLM调用失败: {str(e)}",
                    error=str(e),
                    agent_name=self.agent.name,
                    tool_calls=self.tool_calls_log,
                )

            # 3. No tool calls → final answer
            if not response.get("tool_calls"):
                reply = response.get("text", "")
                logger.info("[AgentLoop:%s] Completed after %d iterations", self.agent.name, iteration + 1)
                return AgentResult(
                    success=True,
                    reply=reply,
                    agent_name=self.agent.name,
                    tool_calls=self.tool_calls_log,
                    tokens=self.total_tokens,
                )

            # 4. Execute tool calls
            # Add assistant message with tool_calls
            messages.append({
                "role": "assistant",
                "content": response.get("text", ""),
                "tool_calls": response["tool_calls"],
            })

            for tool_call in response["tool_calls"]:
                tool_name = tool_call["name"]
                tool_input = tool_call.get("input", {})
                tool_id = tool_call.get("id", "")

                # Doom loop detection
                if self._detect_doom_loop(tool_name, tool_input):
                    logger.error("[AgentLoop:%s] Doom loop detected: %s called %d times consecutively",
                                 self.agent.name, tool_name, self.doom_loop_threshold)
                    return AgentResult(
                        success=False,
                        reply=f"检测到循环调用: {tool_name} 被连续调用 {self.doom_loop_threshold} 次",
                        error="doom_loop_detected",
                        agent_name=self.agent.name,
                        tool_calls=self.tool_calls_log,
                    )

                # Execute tool
                t_tool = time.time()
                try:
                    tool_result = await self.execute_tool_fn(tool_name, tool_input)
                    tool_elapsed = round(time.time() - t_tool, 2)

                    # Log tool call
                    self.tool_calls_log.append({
                        "tool": tool_name,
                        "arguments": tool_input,
                        "result_preview": str(tool_result)[:200] if tool_result else None,
                        "elapsed": tool_elapsed,
                    })

                    logger.info("[AgentLoop:%s] Tool %s completed in %.2fs",
                                self.agent.name, tool_name, tool_elapsed)

                except Exception as e:
                    tool_elapsed = round(time.time() - t_tool, 2)
                    tool_result = f"Error: {str(e)}"

                    self.tool_calls_log.append({
                        "tool": tool_name,
                        "arguments": tool_input,
                        "error": str(e),
                        "elapsed": tool_elapsed,
                    })

                    logger.warning("[AgentLoop:%s] Tool %s failed: %s",
                                   self.agent.name, tool_name, e)

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(tool_result),
                })

        # Exceeded max iterations
        logger.warning("[AgentLoop:%s] Exceeded max iterations (%d)",
                       self.agent.name, self.agent.max_iterations)
        return AgentResult(
            success=False,
            reply=f"超过最大迭代次数 ({self.agent.max_iterations})",
            error="max_iterations_exceeded",
            agent_name=self.agent.name,
            tool_calls=self.tool_calls_log,
        )

    def _detect_doom_loop(self, tool_name: str, tool_input: dict) -> bool:
        """Detect if the same tool is being called repeatedly with similar inputs."""
        signature = (tool_name, hash(str(sorted(tool_input.items()))))
        self.recent_tool_calls.append(signature)

        # Keep only recent calls
        if len(self.recent_tool_calls) > 6:
            self.recent_tool_calls.pop(0)

        # Check for consecutive identical calls
        if len(self.recent_tool_calls) >= self.doom_loop_threshold:
            last_n = self.recent_tool_calls[-self.doom_loop_threshold:]
            if len(set(last_n)) == 1:
                return True

        return False

    async def _default_llm_call(self, messages: list, tools: list, model_id: int = None) -> dict:
        """Default LLM call using the project's LLM client."""
        from backend.common.llm.llm_client import generate_with_tools_stream
        import asyncio

        text_parts = []
        tool_calls = []
        tokens = {}

        # generate_with_tools_stream is a sync generator — drain in thread
        def _drain():
            for event_type, data in generate_with_tools_stream(messages, tools, model_id=model_id):
                if event_type == "token":
                    text_parts.append(data)
                elif event_type == "thinking":
                    pass  # Ignore thinking for now
                elif event_type == "tool_use":
                    # data is {"id": str, "name": str, "input": dict}
                    tool_calls.append(data)
                elif event_type == "done":
                    # data is {"input": int, "output": int, "total": int, "stop_reason": str}
                    nonlocal tokens
                    tokens = data

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _drain)

        # Update token tracking
        if tokens:
            self.total_tokens["input"] = self.total_tokens.get("input", 0) + tokens.get("input", 0)
            self.total_tokens["output"] = self.total_tokens.get("output", 0) + tokens.get("output", 0)

        return {
            "text": "".join(text_parts),
            "tool_calls": tool_calls,
        }
