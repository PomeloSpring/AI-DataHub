"""Agent Router — determines which agent handles a user question.

Uses LLM to classify user intent and route to the appropriate agent.
Route patterns are loaded from adh_agents.route_patterns (JSON array of regex).
Falls back to SQL Agent for data queries.
"""

import json
import logging
import re
from typing import Optional

from backend.agent.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

# ── Agent Registry ─────────────────────────────────────────────────

_agents: dict[str, BaseAgent] = {}
_router_instance: Optional["AgentRouter"] = None
# Cache: agent_name -> list[compiled regex]
_route_patterns_cache: dict[str, list[re.Pattern]] = {}


def register_agent(agent: BaseAgent):
    """Register an agent instance."""
    _agents[agent.name] = agent
    logger.info("[Agent Router] Registered agent: %s", agent.name)


def get_agent(name: str) -> Optional[BaseAgent]:
    """Get a registered agent by name."""
    return _agents.get(name)


def get_all_agents() -> dict[str, BaseAgent]:
    """Get all registered agents."""
    return _agents.copy()


# ── Intent Routing ─────────────────────────────────────────────────

def _load_route_patterns() -> dict[str, list[re.Pattern]]:
    """Load quick-route regex patterns from agent skill.yaml files.

    Returns dict of agent_name -> list of compiled regex patterns.
    Each agent's skill.yaml has a route_patterns list of regex strings.
    """
    if _route_patterns_cache:
        return _route_patterns_cache

    from backend.config.agent_loader import list_agent_dirs, get_route_patterns

    for agent_name in list_agent_dirs():
        patterns = get_route_patterns(agent_name)
        if not patterns:
            continue

        # Only load patterns for active agents
        agent = _agents.get(agent_name)
        if agent and not agent.is_active:
            continue

        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                logger.warning("[Router] Invalid regex for %s: %s", agent_name, e)
        if compiled:
            _route_patterns_cache[agent_name] = compiled

    logger.info("[Router] Loaded route patterns for %d agents from files", len(_route_patterns_cache))
    return _route_patterns_cache


def reload_route_patterns():
    """Force reload route patterns from DB (call after admin updates)."""
    _route_patterns_cache.clear()
    return _load_route_patterns()


def _quick_route(question: str) -> Optional[str]:
    """Fast-path routing using regex patterns from DB. Returns agent name or None."""
    q = question.strip()
    patterns_map = _load_route_patterns()
    for agent_name, patterns in patterns_map.items():
        agent = _agents.get(agent_name)
        if agent and agent.is_active:
            for pattern in patterns:
                if pattern.search(q):
                    logger.info("[Router] Quick route: %s (matched: %s)", agent_name, pattern.pattern)
                    return agent_name
    return None


async def _llm_route(question: str, history: list[dict] = None) -> str:
    """LLM-based intent routing for ambiguous cases."""
    from backend.common.llm.llm_client import _get_client
    from backend.common.config import ANTHROPIC_MODEL

    # Build agent descriptions for the prompt (only active agents)
    agent_descs = []
    for name, agent in _agents.items():
        if agent.is_active:
            agent_descs.append(f"- **{name}**: {agent.description}")
    agents_text = "\n".join(agent_descs) if agent_descs else "（无可用 Agent）"

    # Build context from history
    context = ""
    if history:
        recent = history[-4:]
        parts = []
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")[:100]
            parts.append(f"{role}: {content}")
        context = "\n".join(parts)

    prompt = f"""你是一个意图路由器。根据用户问题，选择最合适的 Agent 来处理。

## 可用 Agent
{agents_text}

## 路由规则
- 默认返回 sql_agent
- 根据 Agent 的描述匹配用户意图
- 如果不确定，返回 sql_agent

## 对话上下文
{context or "（无）"}

## 用户问题
{question}

只返回 Agent 名称，不要其他文字："""

    try:
        client = _get_client()
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = ""
        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                raw = block.text.strip().lower()
                break

        # Extract agent name (only active agents)
        for name, agent in _agents.items():
            if name in raw and agent.is_active:
                logger.info("[Router] LLM route: %s", name)
                return name

        # Default to sql_agent
        logger.info("[Router] LLM route: sql_agent (default, raw=%s)", raw[:50])
        return "sql_agent"

    except Exception as e:
        logger.warning("[Router] LLM routing failed: %s, defaulting to sql_agent", e)
        return "sql_agent"


class AgentRouter:
    """Routes user questions to the appropriate agent."""

    async def route(
        self,
        question: str,
        history: list[dict] = None,
        force_agent: str = None,
    ) -> str:
        """Determine which agent should handle the question.

        Args:
            question: User's question.
            history: Conversation history.
            force_agent: Force a specific agent (skip routing).

        Returns:
            Agent name.
        """
        if force_agent and force_agent in _agents:
            if not _agents[force_agent].is_active:
                logger.warning("[Router] Forced agent %s is disabled, falling back to routing", force_agent)
            else:
                logger.info("[Router] Forced agent: %s", force_agent)
                return force_agent

        # Fast path: regex patterns
        quick = _quick_route(question)
        if quick:
            return quick

        # Slow path: LLM classification
        return await _llm_route(question, history)

    async def execute(
        self,
        question: str,
        history: list[dict] = None,
        datasource_id: int = 0,
        model_id: int = None,
        force_agent: str = None,
        **kwargs,
    ) -> AgentResult:
        """Route and execute the appropriate agent.

        Args:
            question: User's question.
            history: Conversation history.
            datasource_id: Target datasource ID.
            model_id: LLM model ID.
            force_agent: Force a specific agent.

        Returns:
            AgentResult from the selected agent.
        """
        agent_name = await self.route(question, history, force_agent)
        agent = _agents.get(agent_name)

        if not agent:
            logger.error("[Router] Agent '%s' not found, falling back to sql_agent", agent_name)
            agent = _agents.get("sql_agent")

        if not agent:
            return AgentResult(
                success=False,
                reply="没有可用的 Agent",
                error="No agent available",
            )

        logger.info("[Router] Executing agent: %s for question: %s", agent.name, question[:50])

        try:
            result = await agent.run(
                question=question,
                history=history,
                datasource_id=datasource_id,
                model_id=model_id,
                **kwargs,
            )
            result.agent_name = agent.name
            return result
        except Exception as e:
            logger.error("[Router] Agent %s failed: %s", agent.name, e, exc_info=True)
            return AgentResult(
                success=False,
                reply=f"Agent 执行失败: {str(e)}",
                error=str(e),
                agent_name=agent.name,
            )
        finally:
            await agent.cleanup()


def get_agent_router() -> AgentRouter:
    """Get the global agent router singleton."""
    global _router_instance
    if _router_instance is None:
        _router_instance = AgentRouter()
    return _router_instance
