"""Agent Dispatch API — Dispatch queries to agents, list agents, cancel execution.

Delegates to the existing backend agent pipeline and router.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.shared.common.auth import get_current_user
from services.shared.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()

# Track running agent tasks for cancellation
_running_tasks: dict[str, asyncio.Task] = {}


# ── Request Models ────────────────────────────────────────────────────

class AgentDispatchRequest(BaseModel):
    question: str
    agent_name: Optional[str] = None  # None = auto-route
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    history: Optional[list[dict]] = []
    workspace_id: Optional[int] = 0
    attachments: Optional[list[str]] = []  # 多模态附件 ID 列表
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)


# ── Agent Dispatch ────────────────────────────────────────────────────

@router.post("/dispatch")
async def dispatch_agent(
    req: AgentDispatchRequest,
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Dispatch a query to an agent with SSE streaming.

    If agent_name is specified, routes directly to that agent.
    Otherwise, uses the orchestrator to auto-route based on intent.
    """
    from services.datamind.services.chat_service import ChatService

    service = ChatService()
    return StreamingResponse(
        service.dispatch_agent(
            question=req.question,
            agent_name=req.agent_name,
            datasource_id=req.datasource_id or 0,
            model_id=req.model_id,
            history=req.history or [],
            workspace_id=req.workspace_id or 0,
            user_id=user["user_id"],
            username=user["username"],
            user_role=user.get("role", "user"),
            request=request,
            attachments=req.attachments or [],
            model_ref=req.model_ref or "",
            session_id=req.session_id or "",
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── List Agents ──────────────────────────────────────────────────────

@router.get("/list")
def list_agents(
    user: UserInfo = Depends(get_current_user),
):
    """List all available agents with their status and configuration."""
    from services.datamind.agent.router import get_all_agents
    from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import _init_agents

    # Ensure agents are loaded
    _init_agents()

    agents = get_all_agents()
    result = []
    for name, agent in agents.items():
        result.append({
            "name": agent.name,
            "display_name": getattr(agent, "display_name", agent.name),
            "description": getattr(agent, "description", ""),
            "agent_type": getattr(agent, "agent_type", "unknown"),
            "is_active": getattr(agent, "is_active", True),
            "max_retries": getattr(agent, "max_retries", 3),
            "max_iterations": getattr(agent, "max_iterations", 10),
            "datasource_ids": getattr(agent, "datasource_ids", []),
            "mcp_server_ids": getattr(agent, "mcp_server_ids", []),
        })

    return {"agents": result, "total": len(result)}


# ── Cancel Agent ──────────────────────────────────────────────────────

@router.post("/{agent_name}/cancel")
async def cancel_agent(
    agent_name: str,
    user: UserInfo = Depends(get_current_user),
):
    """Cancel a running agent execution.

    Note: Cancellation is best-effort. The agent loop checks for
    cancellation between tool-call rounds.
    """
    task_key = f"{user["user_id"]}:{agent_name}"
    task = _running_tasks.get(task_key)

    if task and not task.done():
        task.cancel()
        _running_tasks.pop(task_key, None)
        logger.info("Cancelled agent task: %s for user %d", agent_name, user["user_id"])
        return {"success": True, "message": f"Agent {agent_name} cancellation requested"}

    return {"success": False, "message": f"No running task found for agent {agent_name}"}
