"""Chat Service — 聊天入口,全部派发 qoder 执行层(agent dispatch).

quick 单发管道(NL2SQL LLM 编排)已退役,非 agent 模式显式报错。
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
    """Chat business logic — delegates to the qoder execution layer."""

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
        attachments: list[dict] = None,
        model_ref: str = "",
        session_id: str = "",
        conversation_id: int = 0,
        user_role: str = "",
        as_bot_key: str = "",
        report_theme: str = "",
        task_binding: Optional[dict] = None,
    ):
        """Stream a query through the qoder execution layer (agent dispatch).

        attachments: 随消息上传的附件文件描述 [{filename, category, size, content}](见 send_payload)。
        task_binding: 服务端注入的任务绑定标记(如 {"kind": "ontology_generate", "datasource_id": N}),
        供工具侧域约束裁决(assert_ontology_draft_scope)；不进 LLM 视野。
        pipeline_mode: 仅接受 "agent"/空;quick 单发管道已退役,其他值显式报错。
        Yields SSE bytes for streaming response.
        """
        from backend import observability

        attachments = attachments or []

        if pipeline_mode not in ("", "agent"):
            err = f"quick 管道已退役，请使用 agent 模式（收到 pipeline_mode={pipeline_mode!r}）"
            logger.error(err)
            yield _sse_event("error", {"message": err})
            yield _sse_event("done", {
                "intent": "agent", "reply": err, "sql": None,
                "warnings": [], "error": err,
            })
            return

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
            # 全部消息派发到执行层(qoder);
            # 工作空间绑定非内置层优先,否则默认非内置外部层(qoder);外部层不可用时直接报错
            handled = False
            stream = self._try_dispatch_via_execution_layer(
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
                as_bot_key=as_bot_key,
                report_theme=report_theme,
                task_binding=task_binding,
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
        attachments: list[dict] = None,
        model_ref: str = "",
        session_id: str = "",
        conversation_id: int = 0,
        user_role: str = "",
        as_bot_key: str = "",
        report_theme: str = "",
        task_binding: Optional[dict] = None,
    ):
        """Agent 模式执行层派发.

        层解析优先级:工作空间绑定的默认层 > 系统默认外部层(qoder)。
        外部执行层缺失或不可用时直接报错。
        """
        import uuid

        from backend.modules.mind.execution import service as exec_service
        from backend.modules.mind.execution.manager import get_execution_layer_manager
        from backend.modules.mind.execution.models import ExecutionContext, ExecutionResult, ExecutionTask

        manager = get_execution_layer_manager()
        row = None
        fallback = None
        try:
            for l in exec_service.get_workspace_layers(workspace_id):
                if l.get("status") != "active":
                    continue
                if not exec_service.is_healthy_layer(l):
                    logger.warning("Skipping unhealthy execution layer: %s", l.get("name"))
                    continue
                if l.get("is_default"):
                    row = l
                    break
                fallback = fallback or l
        except Exception as e:
            logger.exception("工作空间执行层权限解析失败")
            yield _sse_event("error", {"message": "执行层权限配置暂不可用"})
            yield _sse_event("done", {"error": "执行层权限配置暂不可用"})
            return
        if row is None:
            row = fallback
        if row is None:
            # Agent 模式默认执行层:系统级健康的外部层(通常为 cli-qoder)
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
            "message": f"等待模型响应...",
            "execution_layer": layer_name,
        })

        # 多模态附件:上传文件描述随任务透传,由 place_attachments 落盘到会话工作区
        task_attachments = list(attachments or [])

        # 数据源权威回填: 兼容旧客户端/直连 API 未带 datasource_id 时, 从会话持久化行回填,
        # 避免语义工具以 datasource_id=0 命中空目录(取不到数)。回填值仍对 LLM 黑盒。
        if not datasource_id and conversation_id:
            try:
                from backend.common.db import execute_query
                _crow = execute_query(
                    "SELECT datasource_id FROM adh_conversations WHERE id = %s",
                    (conversation_id,), fetchone=True)
                datasource_id = int((_crow or {}).get("datasource_id") or 0)
            except Exception as e:  # noqa: BLE001 — 回填失败不阻断, 由下游 get_metrics/run_semantic_query fail-loud
                logger.debug("[agent] conversation datasource backfill skipped: %s", e)

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
                        ("as_bot_key", as_bot_key),
                        ("report_theme", report_theme),
                        # 任务绑定标记(仅工具侧域约束可见, 不进 prompt)
                        ("task_binding", task_binding),
                    )
                    if v
                },
            ),
            attachments=task_attachments,
        )

        try:
            if row.get("layer_type") != "cli" or (row.get("config") or {}).get("mode") != "sdk":
                raise ValueError("所选执行层不支持 AS-BOT 安全执行")
            adapter = manager.build_adapter(row)
            # 流式执行:CLI 输出逐块以 token 事件推送到前端
            result = None
            stream = adapter.execute_stream(task)
            try:
                async for ev in stream:
                    if ev.get("type") == "capabilities":
                        yield _sse_event("capabilities", ev["data"])
                    elif ev.get("type") == "token":
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
            finally:
                await stream.aclose()
            if result is None:
                result = ExecutionResult(success=False, error="执行层未返回结果")
        except Exception as e:
            logger.error("Execution layer dispatch error: %s", e, exc_info=True)
            message = str(e) if isinstance(e, (ValueError, PermissionError)) else "执行层未能安全完成，请联系管理员"
            yield _sse_event("error", {"message": message})
            yield _sse_event("done", {"intent": "agent", "reply": message, "error": message})
            return

        if await request.is_disconnected():
            return

        if result.success:
            from backend import observability
            observability.set_result(status="success", final_answer=result.output or "")
            _done = {
                "intent": "agent",
                "reply": result.output,
                "sql": None,
                "warnings": [],
                "execution_layer": layer_name,
                # 执行层会话 ID,前端回传以实现 SDK 多轮对话
                "capabilities": result.meta.get("capabilities"),
                # 完整工具调用清单(持久化回放)与执行统计(时间线摘要条)
                "tool_calls": result.meta.get("tool_calls") or [],
                "stats": {
                    "num_turns": result.meta.get("num_turns"),
                    "tool_call_count": result.meta.get("tool_call_count") or 0,
                    "duration_ms": result.meta.get("duration_ms"),
                },
            }
            # 回带落盘后的附件清单(工作区相对路径),前端据此持久化消息历史引用
            if result.meta.get("attachments"):
                _done["attachments"] = result.meta["attachments"]
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
            from backend import observability
            observability.set_result(status="error", error=err)
            yield _sse_event("error", {"message": err, "status_code": result.meta.get("status_code")})
            yield _sse_event("done", {
                "intent": "agent",
                "reply": err,
                "sql": None,
                "warnings": [],
                "error": err,
            })
