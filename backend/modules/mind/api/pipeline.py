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

from backend.common.auth import get_current_user
from backend.models.schemas import UserInfo

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
    # 聊天输入框 `@` 选中的文件引用 [{source: asset|session, ref}]，由 send_payload 展开
    # 成 attachments（与 /api/chat 同口径，两条入口不得漂移）
    cloud_files: Optional[list[dict]] = []
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)
    conversation_id: Optional[int] = 0  # chat 会话 ID(qoder 长对话池 key)
    as_bot_key: Optional[str] = ""  # 聊天端选定的 AS-BOT(空=按工作空间+角色全部生效 AS-BOT 合并)
    # 前置意图漏斗：True = 「转深度分析」显式绕过分流（只关分流，不关护栏）
    force_agent: Optional[bool] = False
    # 聊天输入框 `/` 选中的技能名与指令 key（与 /api/chat 同一口径，两条入口不得漂移）
    selected_skills: Optional[list[str]] = []
    # 聊天输入框 `/` 选中的分析场景 id（与 /api/chat 同一口径：同样按主体授权裁决）
    selected_scenes: Optional[list[str]] = []
    slash_command: Optional[str] = ""
    # 输入框快速入口（analysis / knowledge），与 /api/chat 同一口径
    chat_mode: Optional[str] = ""


# ── Pipeline Execute ─────────────────────────────────────────────────

def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    if event == "done":
        # 「存为报表」入口由报表域单一口径标注(与 chat_service 同一入口, 前端只消费)
        from backend.modules.viz.services.report_service import with_reportability
        data = with_reportability(data)
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


@router.get("/send/status")
def send_status(conversation_id: int, user: UserInfo = Depends(get_current_user)):
    """会话执行权只读状态（供前端「正在停止…」轮询）。

    取消/停止没有显式指令端点（只靠断 SSE），释放执行权需几十秒的收尾核验；
    没这个接口时前端只能在“已停”与“仍在跑”之间猜，猜错就是 409。
    身份只信服务端（get_current_user），并按 user_id 隔离，不得从参数拿身份。
    """
    from backend.modules.mind.execution.session_workspace import execution_status
    return execution_status(conversation_id, int(user["user_id"]))


@router.post("/send/stream")
async def pipeline_send_stream(
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Chat 前端的 SSE 流式入口(matches frontend's expected URL)。

    请求体双模: JSON(无附件)或 multipart(payload + files,附件随消息上传落盘会话工作区)。
    """
    from backend.modules.mind.api.send_payload import parse_send_request

    req = await parse_send_request(request, PipelineExecuteRequest, user_id=int(user["user_id"]))
    from backend.modules.mind.api.chat import _assert_slash_command, _assert_chat_mode
    _assert_slash_command(req.slash_command or "")
    _assert_chat_mode(req.chat_mode or "")
    return await _pipeline_stream(req, request, user)


async def _pipeline_stream(
    req: PipelineExecuteRequest,
    request: Request,
    user: UserInfo,
):
    from backend.modules.mind.execution.session_workspace import preflight_request
    from starlette.concurrency import run_in_threadpool
    # preflight 已解析过一次生效策略，透传给 ChatService 里的前置漏斗，避开重复解析
    policy = await run_in_threadpool(preflight_request, req, user)
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
        from backend.modules.mind.services.chat_service import ChatService
        from backend import observability

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
                force_agent=bool(req.force_agent or False),
                selected_skills=list(req.selected_skills or []),
                selected_scenes=list(req.selected_scenes or []),
                slash_command=req.slash_command or "",
                chat_mode=req.chat_mode or "",
                policy=policy,
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
            err = ("附件消息需要 Harness 支持,当前 Harness 不可用,附件未被处理" if attachments
                   else "Harness 不可用,本次请求未被处理")
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
