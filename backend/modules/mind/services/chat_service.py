"""Chat Service — 聊天入口,全部派发 qoder 执行层(agent dispatch).

quick 单发管道(NL2SQL LLM 编排)已退役,非 agent 模式显式报错。

本模块同时是**前置意图漏斗**的接线点（见 execution/intent_gate.py）：
`_try_dispatch_via_execution_layer` 是两个 chat SSE 入口（/api/chat/send/stream 与
/api/pipeline/send/stream）的唯一汇合点，漏斗只插在这一处，两条入口不可能漂移。
"""

import json
import logging
import time
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)


def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    if event == "done":
        # 「存为报表」入口由报表域单一口径标注(前端只消费, 不另写一套判断)
        from backend.modules.viz.services.report_service import with_reportability
        data = with_reportability(data)
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def _apply_session_site(site_code: str, datasource_id: int, user_id: int,
                        workspace_id: int) -> int:
    """按会话站点重算生效数据源（站点优先于陈旧/默认的 datasource_id）。

    返回 0 的两种合法含义：聚合态（全球 = 逐站编排）、或该站点有多个已授权源且本体未声明
    业务库 → 不猜源，由工具侧 site/datasource 参数逐查询解析。
    只有一种情况报错：该站点在当前身份下**没有任何已授权源**（选了查不到的站），
    绝不静默回退到旧数据源（那会把 A 站数据当 B 站答案）。
    """
    from backend.core.role_service import role_service
    from backend.core.site_service import (
        SiteError, get_site, normalize_site_ref, session_source_for_site)

    code = normalize_site_ref(site_code)
    if not code:
        # 会话带了无法识别的站点写法（清单改过名/脏数据）：不猜，也不能沿用旧源静默跑
        raise ValueError(
            f"会话查询站点「{site_code}」已失效，请在顶部重新选择查询站点后重试。")
    allowed = set(role_service.get_user_allowed_datasources(int(user_id or 0),
                                                            int(workspace_id or 0)))
    site = get_site(code) or {}
    if not int(site.get("is_aggregate") or 0):
        from backend.core.site_service import datasources_of_site
        if not datasources_of_site(code, sorted(allowed)):
            raise ValueError(
                f"当前身份无权访问站点「{site.get('display_name') or code}」，"
                f"请重新选择查询站点（已保留原选择未被误查）。")
    try:
        return int(session_source_for_site(code, sorted(allowed)) or 0)
    except SiteError as exc:
        raise ValueError(str(exc)) from exc


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
        force_agent: bool = False,
        policy=None,
        site_code: str = "",
    ):
        """Stream a query through the qoder execution layer (agent dispatch).

        attachments: 随消息上传的附件文件描述 [{filename, category, size, content}](见 send_payload)。
        task_binding: 服务端注入的任务绑定标记(如 {"kind": "ontology_generate", "datasource_id": N}),
        供工具侧域约束裁决(assert_ontology_draft_scope)；不进 LLM 视野。
        site_code: 会话查询站点(adh_sites.site_code)——只影响默认取数范围，
        不改变任何权限裁决（可查站点仍由角色数据源授权唯一裁决）。
        pipeline_mode: 仅接受 "agent"/空;quick 单发管道已退役,其他值显式报错。
        force_agent: 前端「转深度分析」显式绕过意图漏斗（只关分流，不关任何护栏）。
        policy: preflight 已解析的 AS-BOT 生效策略，透传给漏斗避开重复解析（None 则漏斗自解析）。
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
                force_agent=force_agent,
                policy=policy,
                site_code=site_code,
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
        force_agent: bool = False,
        policy=None,
        site_code: str = "",
    ):
        """Agent 模式执行层派发.

        层解析优先级:工作空间绑定的默认层 > 系统默认外部层(qoder)。
        外部执行层缺失或不可用时直接报错。

        开头先过**前置意图漏斗**：寒暄/无效输入/能力询问/使用说明/涉密索要本地直答，
        不起子进程不烧 credit；判定不准一律 fail-open 交模型（宁可多花，不可错答）。
        """
        import uuid

        from starlette.concurrency import run_in_threadpool

        from backend import observability
        from backend.modules.mind.execution import intent_gate
        from backend.modules.mind.execution import service as exec_service
        from backend.modules.mind.execution.manager import get_execution_layer_manager
        from backend.modules.mind.execution.models import ExecutionContext, ExecutionResult, ExecutionTask

        # 数据源权威回填: 兼容旧客户端/直连 API 未带 datasource_id 时, 从会话持久化行回填,
        # 避免语义工具以 datasource_id=0 命中空目录(取不到数)。回填值仍对 LLM 黑盒。
        # 必须排在漏斗之前: 业务词典按会话生效源分桶, ds=0 会把真实业务信号当成"无命中"放行。
        conv_row = None
        if conversation_id:
            try:
                from backend.common.db import execute_query
                conv_row = execute_query(
                    "SELECT datasource_id, site_code FROM adh_conversations WHERE id = %s",
                    (conversation_id,), fetchone=True)
            except Exception as e:  # noqa: BLE001 — 回填失败不阻断, 由下游 get_metrics/run_semantic_query fail-loud
                logger.debug("[agent] conversation context backfill skipped: %s", e)
        if not datasource_id and conv_row:
            datasource_id = int((conv_row or {}).get("datasource_id") or 0)
        # 会话站点：未显式带时从会话持久化行回填（站点是会话属性，切页/重进不应丢）
        if not site_code and conv_row:
            site_code = str((conv_row or {}).get("site_code") or "").strip()

        # 站点是会话作用域的**权威**：选定站点后，陈旧/默认的 datasource_id 必须被站点重算覆盖。
        # 历史缺陷：前端一直带着自动选中的默认源(prd-cn)，而 `bind_resources` 只在 ds=0 时才按
        # 站点派生 → 选英国站点实际查了中国库，而提示词已告诉模型“会话站点=英国”，
        # 结果把中国数据答成英国（错数比无答案危害大）。空站点保持原行为（未站点化部署）。
        if site_code:
            try:
                datasource_id = _apply_session_site(
                    site_code, int(datasource_id or 0), user_id, workspace_id)
            except ValueError as exc:
                # 站点与授权不匹配：显式告知并停止，不拿旧源跑出错误答案
                logger.warning("[agent] 会话站点不可用: %s", exc)
                yield _sse_event("error", {"message": str(exc)})
                yield _sse_event("done", {
                    "intent": "agent", "reply": str(exc), "sql": None,
                    "warnings": [], "error": str(exc),
                })
                return

        ctx = ExecutionContext(
            workspace_id=workspace_id,
            datasource_id=datasource_id or 0,
            user_id=user_id,
            username=username,
            user_role=user_role,
            model_id=model_id,
            # chat 运行时选择的执行层模型(如 provider/model_name)、
            # 上一轮执行层会话 ID(SDK 多轮对话 resume)及 chat 会话 ID
            # (qoder 长对话池 key: 同会话复用持久 qodercli 进程)、
            # 任务绑定标记(仅工具侧域约束可见, 不进 prompt)、
            # 会话查询站点(进 prompt 的「数据源拓扑」段, 站点内源解析在服务端)
            extra={
                k: v
                for k, v in (
                    ("model_ref", model_ref),
                    ("session_id", session_id),
                    ("conversation_id", conversation_id),
                    ("as_bot_key", as_bot_key),
                    ("report_theme", report_theme),
                    ("task_binding", task_binding),
                    ("site_code", site_code),
                    # preflight 已解析的生效策略：服务端内部键，claim_session 据此复用
                    # 同请求内已读到的 DB 事实（只重编译不重读）；不进 LLM 视野。
                    ("_chat_policy", policy),
                )
                if v
            },
        )

        # ── 前置意图漏斗 ────────────────────────────────────
        # policy 由 preflight 解析后透传；内部调用方(本体生成等)不带时在这里补解析。
        if policy is None and not force_agent:
            try:
                from backend.modules.mind.execution.tool_policy import resolve_policy
                policy = await run_in_threadpool(resolve_policy, ctx)
            except Exception as e:  # noqa: BLE001 — 解析失败只弱化本地文案/模式覆盖，不拿它做拦截依据
                logger.warning("[intent_gate] 策略解析失败（本次仅按全局模式，不拦能力清单类）: %s", e)
        decision = await run_in_threadpool(
            intent_gate.route_intent, question, ctx=ctx, history=history,
            attachments=attachments, task_binding=task_binding,
            force_agent=force_agent, policy=policy)
        gate_snapshot = {k: decision.get(k) for k in
                         ("mode", "layer", "label", "reason", "signals", "latency_ms", "gate_error")}

        if decision.get("intercept") and decision.get("mode") == intent_gate.MODE_ENFORCE:
            reply = await run_in_threadpool(
                intent_gate.build_local_reply, decision, policy=policy, ctx=ctx)
            if reply:
                decision["reply"] = reply
                intent_gate.record_observability(decision, question)
                await run_in_threadpool(
                    intent_gate.record_intercept, decision, question=question, ctx=ctx,
                    policy=policy, conversation_id=conversation_id, force_agent=force_agent)
                yield _sse_event("progress", {
                    "stage": "intent_gate", "step": "route",
                    # 对用户就是一条正常回答，不把内部术语（未调用模型/前置意图层）推到聊天界面；
                    # 处理方归属在 handled_by 字段与管理侧报表里，不靠文案留痕。
                    "message": "正在回答...",
                    "gate": gate_snapshot,
                })
                yield _sse_event("token", {"text": reply})
                _local_done = {
                    "intent": "intent_gate", "reply": reply, "sql": None, "warnings": [],
                    "handled_by": intent_gate.HANDLED_GATE, "gate": gate_snapshot,
                    # 本地直答没有模型用量：显式给 0，前端不会把它当成"统计缺失"
                    "tool_calls": [], "stats": {"num_turns": 0, "tool_call_count": 0, "duration_ms": 0},
                }
                _tid = observability.trace_id_for_response()
                _muuid = observability.message_uuid()
                if _tid:
                    _local_done["trace_id"] = _tid
                if _muuid:
                    _local_done["message_uuid"] = _muuid
                observability.set_result(status="success", final_answer=reply)
                yield _sse_event("done", _local_done)
                return
            # 判为可拦但文案没生成出来 = 拦截没做完：不能"假装拦住了"，回退 agent 并留下可诊断事实
            decision["gate_error"] = "local_reply_missing"
            logger.warning("[intent_gate] 判定为可拦但本地文案缺失，本轮回退 agent: label=%s",
                           decision.get("label"))
        intent_gate.record_observability(decision, question)
        await run_in_threadpool(
            intent_gate.record_intercept, decision, question=question, ctx=ctx,
            policy=policy, conversation_id=conversation_id, force_agent=force_agent)

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
            yield _sse_event("done", {"error": "执行层权限配置暂不可用",
                                      "handled_by": intent_gate.HANDLED_AGENT, "gate": gate_snapshot})
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
                "handled_by": intent_gate.HANDLED_AGENT,
                "gate": gate_snapshot,
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
        # （datasource 回填与 ExecutionContext 已在漏斗前完成，这里复用同一个上下文：
        # 判定用的作用域与执行用的作用域必须是同一个，不能长成两套口径。）
        task = ExecutionTask(
            task_id=uuid.uuid4().hex[:16],
            question=question,
            history=history,
            context=ctx,
            attachments=list(attachments or []),
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
            yield _sse_event("done", {"intent": "agent", "reply": message, "error": message,
                                      "handled_by": intent_gate.HANDLED_AGENT, "gate": gate_snapshot})
            return

        if await request.is_disconnected():
            return

        if result.success:
            observability.set_result(status="success", final_answer=result.output or "")
            _done = {
                "intent": "agent",
                "reply": result.output,
                "sql": None,
                "warnings": [],
                "execution_layer": layer_name,
                # 处理方式：本轮真的走了模型（intent_gate = 前置层直答，未走模型）
                "handled_by": intent_gate.HANDLED_AGENT,
                "gate": gate_snapshot,
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
            observability.set_result(status="error", error=err)
            yield _sse_event("error", {"message": err, "status_code": result.meta.get("status_code")})
            yield _sse_event("done", {
                "intent": "agent",
                "reply": err,
                "sql": None,
                "warnings": [],
                "error": err,
                "handled_by": intent_gate.HANDLED_AGENT,
                "gate": gate_snapshot,
            })
