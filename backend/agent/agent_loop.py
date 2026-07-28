"""Agent Loop — LLM-driven tool calling loop for autonomous agents.

Provides a reusable loop that:
1. Calls LLM with available tools
2. Executes tool calls
3. Detects doom loops (repetitive tool calls)
4. Supports cancellation and timeout
5. Returns when LLM produces a final answer (no more tool calls)
"""

import json

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

        # Langfuse trace
        t_trace_start = time.time()
        lf_trace = None
        try:
            from backend.common.llm.langfuse_client import get_langfuse
            lf = get_langfuse()
            if lf:
                lf_trace = lf.trace(
                    name=f"agent_{self.agent.name}",
                    input={"question": question, "tools_count": len(self.tools)},
                    metadata={
                        "agent_name": self.agent.name,
                        "max_iterations": self.agent.max_iterations,
                        "max_time_seconds": getattr(self.agent, 'max_time_seconds', 0),
                    },
                )
        except Exception:
            pass

        max_iter = self.agent.max_iterations
        soft_limit = max_iter - 2  # Last 2 iterations are for summarization

        for iteration in range(max_iter):
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

            # Soft limit: inject summary request when approaching max iterations
            if iteration == soft_limit:
                logger.info("[AgentLoop:%s] Soft limit reached at iteration %d/%d, requesting summary",
                            self.agent.name, iteration + 1, max_iter)
                messages.append({
                    "role": "user",
                    "content": "你已接近最大工具调用次数。请根据已有的工具调用结果，直接给出最终回答。不要再调用任何工具。",
                })

            # 2. Call LLM
            logger.info("[AgentLoop:%s] Iteration %d/%d, calling LLM with %d messages",
                        self.agent.name, iteration + 1, max_iter, len(messages))

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

            # Log LLM response
            resp_text = response.get("text", "")
            resp_tools = response.get("tool_calls", [])
            if resp_tools:
                logger.info("[AgentLoop:%s] Iter %d/%d | LLM thinking: %s",
                            self.agent.name, iteration + 1, self.agent.max_iterations,
                            resp_text[:500] if resp_text else "(no text)")
                for tc in resp_tools:
                    logger.info("[AgentLoop:%s] Iter %d/%d | LLM wants to call: %s(%s)",
                                self.agent.name, iteration + 1, self.agent.max_iterations,
                                tc.get("name", "?"), json.dumps(tc.get("input", {}), ensure_ascii=False)[:500])
            elif resp_text:
                logger.info("[AgentLoop:%s] Iter %d/%d | LLM final answer: %s",
                            self.agent.name, iteration + 1, self.agent.max_iterations, resp_text[:500])

            # 3. No tool calls → final answer
            if not resp_tools:
                reply = resp_text
                logger.info("[AgentLoop:%s] Completed after %d iterations", self.agent.name, iteration + 1)
                # Update Langfuse trace
                if lf_trace:
                    try:
                        lf_trace.update(
                            output={"reply": reply[:500], "tool_calls_count": len(self.tool_calls_log)},
                            metadata={"iterations": iteration + 1, "elapsed": round(time.time() - t_trace_start, 2)},
                        )
                        from backend.common.llm.langfuse_client import flush
                        flush()
                    except Exception:
                        pass
                return AgentResult(
                    success=True,
                    reply=reply,
                    agent_name=self.agent.name,
                    tool_calls=self.tool_calls_log,
                    tokens=self.total_tokens,
                )

            # 4. Execute tool calls
            # Build assistant message with Anthropic format (tool_use content blocks)
            assistant_content = []
            if resp_text:
                assistant_content.append({"type": "text", "text": resp_text})
            for tc in resp_tools:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": tc["name"],
                    "input": tc.get("input", {}),
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # Doom loop detection (before execution)
            for tool_call in resp_tools:
                tool_name = tool_call["name"]
                tool_input = tool_call.get("input", {})
                consecutive_count = self._count_consecutive(tool_name, tool_input)
                if consecutive_count >= 2:
                    logger.warning("[AgentLoop:%s] ⚠️ %s called %d times consecutively (threshold: %d)",
                                   self.agent.name, tool_name, consecutive_count, self.doom_loop_threshold)

                if self._detect_doom_loop(tool_name, tool_input):
                    logger.error("[AgentLoop:%s] ❌ Doom loop detected: %s called %d times consecutively",
                                 self.agent.name, tool_name, self.doom_loop_threshold)
                    if lf_trace:
                        try:
                            lf_trace.update(
                                output={"error": "doom_loop_detected", "tool": tool_name},
                                metadata={"iterations": iteration + 1, "elapsed": round(time.time() - t_trace_start, 2)},
                                level="ERROR",
                            )
                            from backend.common.llm.langfuse_client import flush
                            flush()
                        except Exception:
                            pass
                    return AgentResult(
                        success=False,
                        reply=f"检测到循环调用: {tool_name} 被连续调用 {self.doom_loop_threshold} 次",
                        error="doom_loop_detected",
                        agent_name=self.agent.name,
                        tool_calls=self.tool_calls_log,
                    )

            # Execute tools — parallel when LLM returns multiple tool_use blocks
            async def _exec_one(tc):
                t_tool = time.time()
                t_name = tc["name"]
                t_input = tc.get("input", {})
                t_id = tc.get("id", "")
                try:
                    result = await self.execute_tool_fn(t_name, t_input)
                    elapsed = round(time.time() - t_tool, 2)
                    result_str = str(result)[:1000] if result else None
                    self.tool_calls_log.append({
                        "tool": t_name, "arguments": t_input,
                        "result": result_str,
                        "result_preview": str(result)[:200] if result else None,
                        "elapsed": elapsed,
                    })
                    logger.info("[AgentLoop:%s] Iter %d/%d | Tool: %s | Elapsed: %.2fs | Result: %s",
                                self.agent.name, iteration + 1, self.agent.max_iterations,
                                t_name, elapsed, str(result)[:300] if result else "None")
                    return {"type": "tool_result", "tool_use_id": t_id, "content": str(result)}
                except Exception as e:
                    elapsed = round(time.time() - t_tool, 2)
                    self.tool_calls_log.append({
                        "tool": t_name, "arguments": t_input,
                        "error": str(e), "elapsed": elapsed,
                    })
                    logger.warning("[AgentLoop:%s] Tool %s failed: %s", self.agent.name, t_name, e)
                    return {"type": "tool_result", "tool_use_id": t_id, "content": f"Error: {e}"}

            if len(resp_tools) > 1:
                logger.info("[AgentLoop:%s] Iter %d/%d | Executing %d tools in PARALLEL",
                            self.agent.name, iteration + 1, self.agent.max_iterations, len(resp_tools))
                tool_results_list = list(await asyncio.gather(*[_exec_one(tc) for tc in resp_tools]))
            else:
                tool_results_list = [await _exec_one(resp_tools[0])]

            # Add all tool results as a single user message (Anthropic format)
            messages.append({"role": "user", "content": tool_results_list})

        # Exceeded max iterations — try to extract partial result from last LLM response
        logger.warning("[AgentLoop:%s] Exceeded max iterations (%d)", self.agent.name, max_iter)
        partial_reply = ""
        if response:
            partial_reply = response.get("text", "")
        if not partial_reply and self.tool_calls_log:
            partial_reply = f"已完成 {len(self.tool_calls_log)} 次工具调用，但未生成最终回答。"

        if lf_trace:
            try:
                lf_trace.update(
                    output={"error": "max_iterations_exceeded", "partial_reply": partial_reply[:500]},
                    metadata={"iterations": max_iter, "elapsed": round(time.time() - t_trace_start, 2)},
                    level="ERROR",
                )
                from backend.common.llm.langfuse_client import flush
                flush()
            except Exception:
                pass
        return AgentResult(
            success=False,
            reply=partial_reply or f"超过最大迭代次数 ({max_iter})",
            error="max_iterations_exceeded",
            agent_name=self.agent.name,
            tool_calls=self.tool_calls_log,
        )

    def _detect_doom_loop(self, tool_name: str, tool_input: dict) -> bool:
        """Detect if the same tool is being called repeatedly with similar inputs."""
        signature = (tool_name, hash(str(sorted(tool_input.items()))))
        self.recent_tool_calls.append((signature, tool_name, tool_input))

        # Keep only recent calls
        if len(self.recent_tool_calls) > 6:
            self.recent_tool_calls.pop(0)

        # Check for consecutive identical calls (compare signatures only)
        if len(self.recent_tool_calls) >= self.doom_loop_threshold:
            last_n = [c[0] for c in self.recent_tool_calls[-self.doom_loop_threshold:]]
            if len(set(last_n)) == 1:
                return True

        return False

    def _count_consecutive(self, tool_name: str, tool_input: dict) -> int:
        """Count how many consecutive identical calls have been made."""
        if not self.recent_tool_calls:
            return 0
        signature = (tool_name, hash(str(sorted(tool_input.items()))))
        count = 0
        for call in reversed(self.recent_tool_calls):
            if call[0] == signature:  # call is (signature, tool_name, tool_input)
                count += 1
            else:
                break
        return count

    async def _default_llm_call(self, messages: list, tools: list, model_id: int = None) -> dict:
        """Default LLM call using the project's LLM client."""
        from backend.common.llm.llm_client import generate_with_tools_stream
        from backend.common.llm.langfuse_client import get_langfuse
        import asyncio

        text_parts = []
        tool_calls = []
        tokens = {}
        t_start = time.time()

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

        # Track with Langfuse
        try:
            lf = get_langfuse()
            if lf:
                from backend.common.llm.llm_client import _get_model_config
                config = _get_model_config(model_id)
                generation = lf.generation(
                    name=f"llm_call_{self.agent.name}",
                    model=config.get("model_name", "unknown"),
                    input=messages,
                    output={
                        "text": "".join(text_parts),
                        "tool_calls": tool_calls,
                    },
                    usage={
                        "input": tokens.get("input", 0),
                        "output": tokens.get("output", 0),
                        "total": tokens.get("total", 0),
                    },
                    metadata={
                        "agent_name": self.agent.name,
                        "iteration": len(self.tool_calls_log),
                    },
                    start_time=t_start,
                    end_time=time.time(),
                )
        except Exception as e:
            logger.debug("[Langfuse] Failed to track LLM call: %s", e)

        return {
            "text": "".join(text_parts),
            "tool_calls": tool_calls,
        }
