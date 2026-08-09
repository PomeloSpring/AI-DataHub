"""Pipeline Execution API — Execute queries via Quick/Deep/Agent pipeline.

Proxies to the existing backend pipeline orchestrator.
"""

import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.shared.common.auth import get_current_user
from services.shared.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request Models ────────────────────────────────────────────────────

class PipelineExecuteRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    pipeline_mode: Optional[str] = "quick"  # quick | deep | agent
    workflow_id: Optional[int] = None
    retrieval_strategy: Optional[str] = None
    workspace_id: Optional[int] = 0
    attachments: Optional[list[str]] = []  # 多模态附件 ID 列表
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)


# ── Pipeline Execute ─────────────────────────────────────────────────

def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


@router.post("/send/stream")
async def pipeline_send_stream(
    req: PipelineExecuteRequest,
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Alias for /execute — matches frontend's expected URL."""
    return await execute_pipeline(req, request, user)


@router.post("/execute")
async def execute_pipeline(
    req: PipelineExecuteRequest,
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Execute a query through the pipeline (Quick/Deep/Agent mode).

    Returns an SSE stream with progress, thinking, token, and done events.
    """
    from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline as _execute_pipeline

    question = req.question
    history = req.history or []
    datasource_id = req.datasource_id or 0
    model_id = req.model_id
    pipeline_mode = req.pipeline_mode or "quick"
    workflow_id = req.workflow_id
    retrieval_strategy = req.retrieval_strategy
    workspace_id = req.workspace_id or 0
    attachments = req.attachments or []

    start_time = time.time()

    async def event_generator():
        # Agent 模式(或携带多模态附件)派发到执行层(默认 claude);
        # quick/deep 模式走内置管线
        from services.datamind.services.chat_service import ChatService

        if pipeline_mode == "agent" or attachments:
            handled = False
            async for event in ChatService()._try_dispatch_via_execution_layer(
                question=question,
                datasource_id=datasource_id,
                model_id=model_id,
                history=history,
                workspace_id=workspace_id,
                user_id=user["user_id"],
                username=user["username"],
                request=request,
                attachments=attachments,
                model_ref=req.model_ref or "",
                session_id=req.session_id or "",
            ):
                handled = True
                yield event
            if handled:
                return

        try:
            async for event_type, data in _execute_pipeline(
                question=question,
                history=history,
                datasource_id=datasource_id,
                model_id=model_id,
                pipeline_mode=pipeline_mode,
                workflow_id=workflow_id,
                user_id=user["user_id"],
                username=user["username"],
                retrieval_strategy=retrieval_strategy,
                workspace_id=workspace_id,
                attachments=attachments,
            ):
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping pipeline (mode=%s)", pipeline_mode)
                    break

                yield _sse_event(event_type, data)

        except Exception as e:
            logger.error("Pipeline stream error: %s", e, exc_info=True)
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("done", {
                "intent": "query",
                "reply": f"Error: {str(e)}",
                "sql": None,
                "warnings": [],
                "error": str(e),
                "mode": pipeline_mode,
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
