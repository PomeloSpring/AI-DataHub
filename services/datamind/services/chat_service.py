"""Chat Service — Thin wrapper that delegates to existing backend modules.

Provides streaming and non-streaming query execution via the pipeline orchestrator,
and agent dispatch via the agent pipeline.
"""

import json
import logging
import time
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)


def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


class ChatService:
    """Chat business logic — delegates to backend pipeline orchestrator."""

    async def stream_query(
        self,
        question: str,
        history: list[dict],
        datasource_id: int,
        model_id: Optional[int],
        pipeline_mode: str,
        retrieval_strategy: Optional[str],
        workspace_id: int,
        user_id: int,
        username: str,
        request: Request,
        attachments: list[str] = None,
        model_ref: str = "",
        session_id: str = "",
        conversation_id: int = 0,
        user_role: str = "",
        waker_key: str = "",
    ):
        """Stream a query through the pipeline orchestrator.

        Yields SSE bytes for streaming response.
        """
        from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline
        from services.shared import observability

        attachments = attachments or []

        # 可观测:一次用户回合 = 一个 trace;入口创建 recorder 并注入身份。
        # 关闭 OBSERVABILITY_ENABLED 时 begin 返回 None,整条链路 no-op。
        observability.begin(
            entrypoint="chat",
            user_id=user_id, username=username, user_role=user_role,
            workspace_id=workspace_id, datasource_id=datasource_id or 0,
            conversation_id=conversation_id or 0, model_ref=model_ref,
            question=question,
        )
        _status, _err, _final = "", "", ""
        try:
            # Agent 模式(或携带多模态附件)派发到执行层;
            # 工作空间绑定非内置层优先,否则默认非内置外部层(qoder);外部层不可用时直接报错
            if pipeline_mode == "agent" or attachments:
                handled = False
                async for event in self._try_dispatch_via_execution_layer(
                    question=question,
                    datasource_id=datasource_id,
                    model_id=model_id,
                    history=history,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    username=username,
                    request=request,
                    attachments=attachments,
                    model_ref=model_ref,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    user_role=user_role,
                    waker_key=waker_key,
                ):
                    handled = True
                    yield event
                if handled:
                    return

            try:
                async for event_type, data in execute_pipeline(
                    question=question,
                    history=history,
                    datasource_id=datasource_id,
                    model_id=model_id,
                    pipeline_mode=pipeline_mode,
                    user_id=user_id,
                    username=username,
                    retrieval_strategy=retrieval_strategy,
                    workspace_id=workspace_id,
                    attachments=attachments,
                ):
                    if await request.is_disconnected():
                        logger.info("Client disconnected, stopping stream")
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
                logger.error("ChatService stream error: %s", e, exc_info=True)
                _status, _err = "error", str(e)
                yield _sse_event("error", {"message": str(e)})
                yield _sse_event("done", {
                    "intent": "query",
                    "reply": f"Error: {str(e)}",
                    "sql": None,
                    "warnings": [],
                    "error": str(e),
                })
        finally:
            observability.finalize(status=_status, error=_err, final_answer=_final)

    async def query(
        self,
        question: str,
        history: list[dict],
        datasource_id: int,
        model_id: Optional[int],
        pipeline_mode: str,
        retrieval_strategy: Optional[str],
        workspace_id: int,
        user_id: int,
        username: str,
        attachments: list[str] = None,
    ) -> dict:
        """Execute a query non-streaming. Collects all events and returns the final result."""
        from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline

        result = {}
        try:
            async for event_type, data in execute_pipeline(
                question=question,
                history=history,
                datasource_id=datasource_id,
                model_id=model_id,
                pipeline_mode=pipeline_mode,
                user_id=user_id,
                username=username,
                retrieval_strategy=retrieval_strategy,
                workspace_id=workspace_id,
                attachments=attachments or [],
            ):
                if event_type == "done":
                    result = data
                elif event_type == "error":
                    result["error"] = data.get("message", str(data))

        except Exception as e:
            logger.error("ChatService query error: %s", e, exc_info=True)
            result = {"error": str(e)}

        return result

    async def dispatch_agent(
        self,
        question: str,
        agent_name: Optional[str],
        datasource_id: int,
        model_id: Optional[int],
        history: list[dict],
        workspace_id: int,
        user_id: int,
        username: str,
        request: Request,
        user_role: str = "user",
        attachments: list[str] = None,
        model_ref: str = "",
        session_id: str = "",
    ):
        """Dispatch a query to a specific agent or auto-route via orchestrator.

        Always runs on the built-in Agent pipeline (deep mode):
        explicit agent targeting and orchestrator intent routing both live there.
        External execution layers are reserved for the 'agent' chat mode.
        Yields SSE bytes.
        """
        attachments = attachments or []

        from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline

        # 内置 Agent 管线(deep 模式)支持意图路由与指定 agent 分发
        pipeline_mode = "deep"

        try:
            async for event_type, data in execute_pipeline(
                question=question,
                history=history,
                datasource_id=datasource_id,
                model_id=model_id,
                pipeline_mode=pipeline_mode,
                user_id=user_id,
                username=username,
                workspace_id=workspace_id,
                user_role=user_role,
                attachments=attachments,
            ):
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping agent dispatch")
                    break
                yield _sse_event(event_type, data)

        except Exception as e:
            logger.error("Agent dispatch error: %s", e, exc_info=True)
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("done", {
                "intent": "query",
                "reply": f"Agent error: {str(e)}",
                "sql": None,
                "warnings": [],
                "error": str(e),
            })

    async def _try_dispatch_via_execution_layer(
        self,
        question: str,
        datasource_id: int,
        model_id: Optional[int],
        history: list[dict],
        workspace_id: int,
        user_id: int,
        username: str,
        request: Request,
        attachments: list[str] = None,
        model_ref: str = "",
        session_id: str = "",
        conversation_id: int = 0,
        user_role: str = "",
        waker_key: str = "",
    ):
        """Agent 模式执行层派发.

        层解析优先级:工作空间绑定的非内置默认层 > 系统默认非内置外部层(qoder)。
        外部执行层缺失或不可用时直接报错(不回退内置管线)。
        """
        import uuid

        from services.datamind.execution import service as exec_service
        from services.datamind.execution.manager import get_execution_layer_manager
        from services.datamind.execution.models import ExecutionContext, ExecutionResult, ExecutionTask

        manager = get_execution_layer_manager()
        row = None
        fallback = None
        try:
            for l in exec_service.get_workspace_layers(workspace_id):
                if l.get("status") != "active" or l.get("layer_type") == "builtin":
                    continue
                # 跳过心跳超时的 stale 自注册层(手工/内置层始终视为健康)
                if not exec_service.is_healthy_layer(l):
                    logger.warning("Skipping unhealthy execution layer: %s", l.get("name"))
                    continue
                if l.get("is_default"):
                    row = l
                    break
                fallback = fallback or l
        except Exception as e:
            logger.warning("Resolve workspace execution layer failed: %s", e)
        if row is None:
            row = fallback
        if row is None:
            # Agent 模式默认执行层:系统级健康的非内置外部层(全面切 qoder 后通常为 cli-qoder)
            row = exec_service.get_default_external_layer()
        if row is None or row.get("status") != "active":
            err = "Agent 模式不可用:未找到可用的外部执行层(qoder 层缺失或未启用)"
            logger.error(err)
            yield _sse_event("error", {"message": err})
            yield _sse_event("done", {
                "intent": "agent",
                "reply": err,
                "sql": None,
                "warnings": [],
                "error": err,
            })
            return

        layer_name = row.get("display_name") or row.get("name")
        yield _sse_event("progress", {
            "stage": "execution_layer",
            "step": "dispatch",
            "message": f"已路由到执行层: {layer_name}",
            "execution_layer": layer_name,
        })

        # 加载多模态附件,以文件路径清单透传给执行层适配器
        task_attachments = []
        if attachments:
            try:
                from services.datamind.multimodal.loader import load_attachments
                task_attachments = [
                    {
                        "id": a["id"],
                        "filename": a["filename"],
                        "category": a["category"],
                        "path": a["storage_path"],
                    }
                    for a in load_attachments(attachments, user_id)
                ]
            except Exception as e:
                logger.warning("Load attachments for execution layer failed: %s", e)

        task = ExecutionTask(
            task_id=uuid.uuid4().hex[:16],
            question=question,
            history=history,
            context=ExecutionContext(
                workspace_id=workspace_id,
                datasource_id=datasource_id or 0,
                user_id=user_id,
                username=username,
                user_role=user_role,
                model_id=model_id,
                # chat 运行时选择的执行层模型(如 provider/model_name)、
                # 上一轮执行层会话 ID(SDK 多轮对话 resume)及 chat 会话 ID
                # (qoder 长对话池 key: 同会话复用持久 qodercli 进程)
                extra={
                    k: v
                    for k, v in (
                        ("model_ref", model_ref),
                        ("session_id", session_id),
                        ("conversation_id", conversation_id),
                        ("waker_key", waker_key),
                    )
                    if v
                },
            ),
            attachments=task_attachments,
        )

        try:
            adapter = manager.build_adapter(row)
            # 流式执行:CLI 输出逐块以 token 事件推送到前端
            result = None
            async for ev in adapter.execute_stream(task):
                if ev.get("type") == "token":
                    if await request.is_disconnected():
                        return
                    yield _sse_event("token", {"text": ev.get("text", "")})
                elif ev.get("type") == "thinking":
                    yield _sse_event("thinking", {"text": ev.get("text", "")})
                elif ev.get("type") == "tool_start":
                    # 执行层工具调用开始:前端时间线实时渲染 pending 步骤
                    yield _sse_event("tool_start", {
                        "tool_call_id": ev.get("tool_call_id", ""),
                        "tool": ev.get("tool", ""),
                        "arguments": ev.get("arguments") or {},
                    })
                elif ev.get("type") == "tool_result":
                    # 执行层工具调用结果:回填时间线对应步骤
                    yield _sse_event("tool_result", {
                        "tool_call_id": ev.get("tool_call_id", ""),
                        "tool": ev.get("tool", ""),
                        "output": ev.get("output", ""),
                        "error": ev.get("error", ""),
                        "elapsed": ev.get("elapsed"),
                    })
                elif ev.get("type") == "done":
                    result = ev.get("result")
            if result is None:
                result = ExecutionResult(success=False, error="执行层未返回结果")
        except Exception as e:
            logger.error("Execution layer dispatch error: %s", e, exc_info=True)
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("done", {
                "intent": "agent",
                "reply": f"执行层错误: {str(e)}",
                "sql": None,
                "warnings": [],
                "error": str(e),
            })
            return

        if await request.is_disconnected():
            return

        if result.success:
            from services.shared import observability
            observability.set_result(status="success", final_answer=result.output or "")
            _done = {
                "intent": "agent",
                "reply": result.output,
                "sql": None,
                "warnings": [],
                "execution_layer": layer_name,
                # 执行层会话 ID,前端回传以实现 SDK 多轮对话
                "session_id": result.meta.get("session_id") or "",
                # 完整工具调用清单(持久化回放)与执行统计(时间线摘要条)
                "tool_calls": result.meta.get("tool_calls") or [],
                "stats": {
                    "num_turns": result.meta.get("num_turns"),
                    "tool_call_count": result.meta.get("tool_call_count") or 0,
                    "duration_ms": result.meta.get("duration_ms"),
                },
            }
            # 回传 trace 关联键(只增不改),供前端赞踩/回看关联
            _tid = observability.trace_id_for_response()
            _muuid = observability.message_uuid()
            if _tid:
                _done.setdefault("trace_id", _tid)
            if _muuid:
                _done.setdefault("message_uuid", _muuid)
            yield _sse_event("done", _done)
        else:
            err = result.error or "执行层执行失败"
            from services.shared import observability
            observability.set_result(status="error", error=err)
            if result.meta.get("stderr_tail"):
                err = f"{err}\n{result.meta['stderr_tail'][-500:]}"
            yield _sse_event("error", {"message": err})
            yield _sse_event("done", {
                "intent": "agent",
                "reply": err,
                "sql": None,
                "warnings": [],
                "error": err,
            })
