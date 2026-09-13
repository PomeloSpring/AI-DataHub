"""Execution-layer self-registration & heartbeat (Phase 3.2).

The built-in execution layer lives inside the datamind process, so it
registers itself into the registry on startup and refreshes its heartbeat
periodically. External SDK adapters (qoder / claude) follow the
same contract by calling the HTTP `/execution-layers/register` + `/heartbeat`
endpoints from their own host processes.
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

#: Capabilities advertised by the built-in execution layer.
BUILTIN_CAPABILITIES = ["nl2sql", "chat", "analysis", "agent"]

#: Default heartbeat interval (seconds) for self-registered in-process layers.
HEARTBEAT_INTERVAL = int(os.getenv("EXEC_LAYER_HEARTBEAT_INTERVAL", "30"))


async def register_builtin_layer() -> int | None:
    """Register / refresh the built-in execution layer (source=self)."""
    from services.datamind.execution import service as exec_service

    tools: list[dict] = []
    try:
        from services.datamind.execution.manager import get_execution_layer_manager

        row = exec_service.get_layer_by_name("builtin") or {}
        adapter = get_execution_layer_manager().build_adapter({
            **row,
            "name": "builtin",
            "layer_type": "builtin",
            "config": row.get("config") or {},
        })
        tools = await adapter.list_tools()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[Registry] builtin list_tools failed: %s", e)

    try:
        layer_id = exec_service.register_layer({
            "name": "builtin",
            "layer_type": "builtin",
            "display_name": row.get("display_name") or "内置执行层",
            "description": row.get("description") or "平台内置 Agent 执行引擎(SQL/数据分析/可配置 Agent)",
            "capabilities": BUILTIN_CAPABILITIES,
            "tools": tools,
        })
        logger.info("[Registry] builtin execution layer registered (id=%s, tools=%d)", layer_id, len(tools))
        return layer_id
    except Exception as e:  # pragma: no cover - defensive
        logger.error("[Registry] register builtin failed: %s", e)
        return None


async def registry_heartbeat_loop(interval: int = HEARTBEAT_INTERVAL):
    """Background loop refreshing heartbeats for in-process self-registered layers."""
    from services.datamind.execution import service as exec_service

    logger.info("[Registry] heartbeat loop started (interval=%ss)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            exec_service.heartbeat(name="builtin")
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[Registry] builtin heartbeat failed: %s", e)


def start_registry_background() -> asyncio.Task:
    """Register builtin layer and launch the heartbeat loop. Returns the task.

    Call from the FastAPI startup handler and keep a reference to the task.
    """
    async def _boot():
        await register_builtin_layer()
        await registry_heartbeat_loop()

    return asyncio.create_task(_boot())
