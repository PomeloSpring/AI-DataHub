"""Configurable Agent — DB-driven agent that reads config from adh_agents.

This is the generic agent implementation for user-created agents from the admin page.
It reads system_prompt, MCP bindings, and datasource bindings from the database,
then uses LLM + MCP tools to answer questions.

For specialized built-in agents (like SQL Agent), use dedicated code classes instead.
"""

import json
import logging
import time
from typing import Optional

from backend.agent.base import BaseAgent, AgentResult
from backend.agent.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


class ConfigurableAgent(BaseAgent):
    """Agent that reads its configuration from the database.

    Config source: adh_agents table
    Capabilities:
    - Custom system_prompt from DB
    - MCP tool calling (if MCP servers are bound)
    - LLM-driven autonomous tool selection and execution
    - Multi-step tool calling loop with doom loop detection
    """

    def __init__(self, db_config: dict):
        """Initialize from DB config row.

        Args:
            db_config: Row from adh_agents table with keys:
                name, display_name, description, agent_type, system_prompt,
                mcp_server_ids, datasource_ids, tools, config, is_active
        """
        super().__init__()
        self.name = db_config.get("name", "custom")
        self.is_active = bool(db_config.get("is_active", 1))
        self.agent_type = db_config.get("agent_type", "custom")

        # Load from file first, DB fallback
        from backend.config.agent_loader import load_agent_skill, load_agent_prompt
        skill = load_agent_skill(self.name)
        if skill:
            self.description = skill.get("description", db_config.get("description", ""))
        else:
            self.description = db_config.get("description", "")

        file_prompt = load_agent_prompt(self.name)
        self.system_prompt = file_prompt or db_config.get("system_prompt", "")

        # Parse bindings
        mcp_ids = db_config.get("mcp_server_ids", "")
        self.mcp_server_ids = [int(x.strip()) for x in mcp_ids.split(",") if x.strip()] if mcp_ids else []

        ds_ids = db_config.get("datasource_ids", "")
        self.datasource_ids = [int(x.strip()) for x in ds_ids.split(",") if x.strip()] if ds_ids else []

        tools_str = db_config.get("tools", "")
        self.tools = [t.strip() for t in tools_str.split(",") if t.strip()] if tools_str else []

        # Parse extra config
        config_str = db_config.get("config", "")
        self.extra_config = {}
        if config_str:
            try:
                self.extra_config = json.loads(config_str) if isinstance(config_str, str) else config_str
            except json.JSONDecodeError:
                pass

        # Protection config from DB or defaults
        self.max_iterations = self.extra_config.get("max_iterations", 10)
        self.max_time_seconds = self.extra_config.get("max_time_seconds", 60)
        # doom_loop_threshold: consecutive identical tool calls to trigger doom loop
        # Default 4 to allow retry after transient failures (e.g., ES mapping failure)
        self.doom_loop_threshold = self.extra_config.get("doom_loop_threshold", 4)

        # max_retries priority: DB config > skill.yaml > rules.md default
        from backend.config.agent_loader import get_max_retries
        db_max_retries = self.extra_config.get("max_retries")
        self.max_retries = get_max_retries(self.name, db_override=db_max_retries)

    async def run(
        self,
        question: str,
        history: list[dict] = None,
        datasource_id: int = 0,
        model_id: int = None,
        **kwargs,
    ) -> AgentResult:
        """Execute the configurable agent with LLM-driven tool calling.

        Uses AgentLoop for autonomous tool selection and execution.
        """
        self._start_time = time.time()

        # Use bound datasource if not specified
        if not datasource_id and self.datasource_ids:
            datasource_id = self.datasource_ids[0]

        # 1. Collect available tools (MCP tools)
        tools = await self._collect_tools()

        if not tools:
            return AgentResult(
                success=False,
                reply=f"Agent {self.name} 没有可用的工具",
                error="no_tools_available",
                agent_name=self.name,
                mode="configurable_agent",
                timings={"total": round(time.time() - self._start_time, 2)},
            )

        # 2. Build system prompt
        system_prompt = self._build_system_prompt(tools, datasource_id)

        # 3. Execute with AgentLoop
        loop = AgentLoop(
            agent=self,
            tools=tools,
            execute_tool_fn=lambda name, args: self._execute_mcp_tool(name, args),
        )

        result = await loop.run(
            question=question,
            system_prompt=system_prompt,
            model_id=model_id,
            history=history,
        )

        result.agent_name = self.name
        result.mode = "configurable_agent"
        result.timings["total"] = round(time.time() - self._start_time, 2)
        return result

    async def _collect_tools(self) -> list[dict]:
        """Collect available tools from MCP servers."""
        tools = []

        if not self.mcp_server_ids:
            return tools

        from backend.mcp_client.tools import MCPToolCaller

        caller = MCPToolCaller(server_ids=self.mcp_server_ids)
        try:
            mcp_tools = await caller.list_tools()
            # Filter to configured tools if specified
            if self.tools:
                mcp_tools = [t for t in mcp_tools if t["name"] in self.tools]
            tools.extend(mcp_tools)
        except Exception as e:
            logger.error("[ConfigAgent:%s] Failed to load MCP tools: %s", self.name, e)

        return tools

    def _build_system_prompt(self, tools: list[dict], datasource_id: int = 0) -> str:
        """Build system prompt with tool descriptions."""
        tools_desc = []
        for t in tools:
            desc = f"- **{t['name']}**: {t.get('description', '')}"
            schema = t.get("input_schema", {})
            props = schema.get("properties", {})
            if props:
                params = []
                for pname, pinfo in props.items():
                    ptype = pinfo.get("type", "string")
                    pdesc = pinfo.get("description", "")
                    params.append(f"  - {pname} ({ptype}): {pdesc}")
                desc += "\n" + "\n".join(params)
            tools_desc.append(desc)

        tools_text = "\n".join(tools_desc) if tools_desc else "（无可用工具）"

        return f"""{self.system_prompt}

## 可用工具

{tools_text}

## 工作原则

1. **先理解问题**：分析用户到底在问什么，需要哪些信息
2. **选择合适的工具**：根据问题选择最合适的工具
3. **分步执行**：如果需要多个步骤，分步执行
4. **错误处理**：如果工具返回错误，分析原因后重试或调整策略
5. **数据真实性**：只使用工具返回的数据，不要编造

## 输出要求

- 用中文回答
- 基于工具返回的数据进行分析
- 如果数据不足，说明原因
"""

    async def _execute_mcp_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute an MCP tool and return the result."""
        from backend.mcp_client.tools import MCPToolCaller

        caller = MCPToolCaller(server_ids=self.mcp_server_ids)
        try:
            result = await caller.call(tool_name, arguments)
            return str(result) if result else "工具执行成功，但无返回内容"
        except Exception as e:
            logger.error("[ConfigAgent:%s] Tool %s failed: %s", self.name, tool_name, e)
            raise


def create_configurable_agent(db_config: dict) -> ConfigurableAgent:
    """Factory function to create a configurable agent from DB config."""
    return ConfigurableAgent(db_config)
