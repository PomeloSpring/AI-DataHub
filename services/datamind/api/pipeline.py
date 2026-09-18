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
    retrieval_strategy: Optional[str] = None
    workspace_id: Optional[int] = 0
    attachments: Optional[list[str]] = []  # 多模态附件 ID 列表
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)
    conversation_id: Optional[int] = 0  # chat 会话 ID(qoder 长对话池 key)
    waker_key: Optional[str] = ""  # 聊天端选定的 Waker(空=按工作空间+角色全部生效 Waker 合并)


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
    retrieval_strategy = req.retrieval_strategy
    workspace_id = req.workspace_id or 0
    attachments = req.attachments or []

    start_time = time.time()

    async def event_generator():
        # Agent 模式(或携带多模态附件)派发到执行层(默认 qoder);
        # quick/deep 模式走内置管线
        from services.datamind.services.chat_service import ChatService
        from services.shared import observability

        user_role = user.get("role") or ""
        # 可观测:一次用户回合 = 一个 trace。前端 Chat 走本端点(/api/pipeline/send/stream),
        # 故必须在此 begin/finalize(与 ChatService.stream_query 对齐);未开启时全程 no-op。
        observability.begin(
            entrypoint=("agent" if (pipeline_mode == "agent" or attachments) else (pipeline_mode or "chat")),
            user_id=user["user_id"], username=user["username"], user_role=user_role,
            workspace_id=workspace_id, datasource_id=datasource_id or 0,
            conversation_id=req.conversation_id or 0, model_ref=req.model_ref or "",
            question=question,
        )
        _status, _err, _final = "", "", ""
        try:
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
                    conversation_id=req.conversation_id or 0,
                    user_role=user_role,
                    waker_key=req.waker_key or "",
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
                    user_id=user["user_id"],
                    username=user["username"],
                    retrieval_strategy=retrieval_strategy,
                    workspace_id=workspace_id,
                    attachments=attachments,
                ):
                    if await request.is_disconnected():
                        logger.info("Client disconnected, stopping pipeline (mode=%s)", pipeline_mode)
                        break
                    if event_type == "done" and isinstance(data, dict):
                        _final = data.get("reply") or _final
                        if data.get("error"):
                            _status, _err = "error", str(data.get("error"))
                        # 回传 trace 关联键(只增不改),供前端赞踩/回看关联
                        tid = observability.trace_id_for_response()
                        muuid = observability.message_uuid()
                        if tid:
                            data.setdefault("trace_id", tid)
                        if muuid:
                            data.setdefault("message_uuid", muuid)
                    yield _sse_event(event_type, data)

            except Exception as e:
                logger.error("Pipeline stream error: %s", e, exc_info=True)
                _status, _err = "error", str(e)
                yield _sse_event("error", {"message": str(e)})
                yield _sse_event("done", {
                    "intent": "query",
                    "reply": f"Error: {str(e)}",
                    "sql": None,
                    "warnings": [],
                    "error": str(e),
                    "mode": pipeline_mode,
                })
        finally:
            observability.finalize(status=_status, error=_err, final_answer=_final)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
