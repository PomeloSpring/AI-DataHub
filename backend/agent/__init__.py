"""Agent Framework — pluggable agents for different query scenarios.

Each agent handles a specific type of user request:
- SQL Agent: NL2SQL query generation (existing pipeline)
- Log Agent: Log analysis via MCP tools
- Custom agents registered via adh_agents table
"""

from backend.agent.base import BaseAgent, AgentResult
from backend.agent.router import AgentRouter, get_agent_router

__all__ = ["BaseAgent", "AgentResult", "AgentRouter", "get_agent_router"]
