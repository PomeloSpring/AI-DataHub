"""Pipeline Execution API — Execute queries via the qoder execution layer (agent mode).

quick 单发管道已退役;非 agent 模式显式报错。
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
    pipeline_mode: Optional[str] = "agent"  # agent(quick 管道已退役)
    retrieval_strategy: Optional[str] = None
    workspace_id: Optional[int] = 0
    attachments: list[dict] = []  # 服务端解析回填的上传附件(send_payload);JSON 请求不得携带
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)
    conversation_id: Optional[int] = 0  # chat 会话 ID(qoder 长对话池 key)
    as_bot_key: Optional[str] = ""  # 聊天端选定的 AS-BOT(空=按工作空间+角色全部生效 AS-BOT 合并)


# ── Pipeline Execute ─────────────────────────────────────────────────

def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


@router.post("/send/stream")
async def pipeline_send_stream(
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Chat 前端的 SSE 流式入口(matches frontend's expected URL)。

    请求体双模: JSON(无附件)或 multipart(payload + files,附件随消息上传落盘会话工作区)。
    """
    from services.datamind.api.send_payload import parse_send_request

    req = await parse_send_request(request, PipelineExecuteRequest)
    return await _pipeline_stream(req, request, user)


async def _pipeline_stream(
    req: PipelineExecuteRequest,
    request: Request,
    user: UserInfo,
):
    from services.datamind.execution.session_workspace import preflight_request
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(preflight_request, req, user)
    question = req.question
    history = req.history or []
    datasource_id = req.datasource_id or 0
    model_id = req.model_id
    pipeline_mode = req.pipeline_mode or "agent"
    workspace_id = req.workspace_id or 0
    attachments = req.attachments or []

    start_time = time.time()

    async def event_generator():
        # 全部消息派发到执行层(默认 qoder)
        from services.datamind.services.chat_service import ChatService
        from services.shared import observability

        user_role = user.get("role") or ""
        if pipeline_mode not in ("", "agent"):
            err = f"quick 管道已退役，请使用 agent 模式（收到 pipeline_mode={pipeline_mode!r}）"
            logger.error(err)
            yield _sse_event("error", {"message": err})
            yield _sse_event("done", {
                "intent": "agent", "reply": err, "sql": None,
                "warnings": [], "error": err,
            })
            return
        # 可观测:一次用户回合 = 一个 trace。前端 Chat 走本端点(/api/pipeline/send/stream),
        # 故必须在此 begin/finalize(与 ChatService.stream_query 对齐);未开启时全程 no-op。
        observability.begin(
            entrypoint="agent",
            user_id=user["user_id"], username=user["username"], user_role=user_role,
            workspace_id=workspace_id, datasource_id=datasource_id or 0,
            conversation_id=req.conversation_id or 0, model_ref=req.model_ref or "",
            question=question,
        )
        _status, _err, _final = "", "", ""
        try:
            handled = False
            stream = ChatService()._try_dispatch_via_execution_layer(
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
                as_bot_key=req.as_bot_key or "",
            )
            try:
                async for event in stream:
                    handled = True
                    yield event
            finally:
                await stream.aclose()
            if handled:
                return
            # 派发未接住:显式报错,不得静默丢弃本次请求(no-silent-degradation)
            err = ("附件消息需要执行层支持,当前执行层不可用,附件未被处理" if attachments
                   else "执行层不可用,本次请求未被处理")
            _status, _err = "error", err
            yield _sse_event("error", {"message": err})
            yield _sse_event("done", {
                "intent": "agent", "reply": err, "sql": None,
                "warnings": [], "error": err,
            })
        finally:
            observability.finalize(status=_status, error=_err, final_answer=_final)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
