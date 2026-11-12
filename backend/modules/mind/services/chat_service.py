"""Chat Service — 聊天入口,全部派发 qoder 执行层(agent dispatch).

quick 单发管道(NL2SQL LLM 编排)已退役,非 agent 模式显式报错。

本模块同时是**前置意图漏斗**的接线点（见 execution/intent_gate.py）：
`_try_dispatch_via_execution_layer` 是两个 chat SSE 入口（/api/chat/send/stream 与
/api/pipeline/send/stream）的唯一汇合点，漏斗只插在这一处，两条入口不可能漂移。
"""

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)


def _sse_event(event: str, data: dict, decision_identity: Optional[dict] = None) -> bytes:
    """Format a Server-Sent Event.

    done 事件的两个服务端子判定都在这里补（前端只消费，不另写一套判断）：
    * 「存为报表」可行性 —— report_service.with_reportability
    * 本轮分析结果能支撑哪些决策建议 —— decision.outlet.with_decision_advice
      只接了身份的出口才评估（其余出口都是错误/中止回合，回答本身未交付，
      再挂一句“未评估”只是噪声）。
    """
    if event == "done":
        # 「存为报表」入口由报表域单一口径标注(前端只消费, 不另写一套判断)
        from backend.modules.viz.services.report_service import with_reportability
        data = with_reportability(data)
        if decision_identity is not None:
            from backend.modules.decision.outlet import with_decision_advice
            data = with_decision_advice(data, decision_identity)
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def _decision_identity(ctx, message_uuid: str = "") -> dict:
    """从服务端已构造的执行上下文给出决策评估身份（不从请求体取任何身份字段）。

    身份与生效源/站点都是 ctx 里已经裁好的事实（会话站点已在上面经
    `_apply_session_site` 重算），这里只是把它递给出入口，不在这里新建判断。
    """
    return {
        "user_id": int(getattr(ctx, "user_id", 0) or 0),
        "username": str(getattr(ctx, "username", "") or ""),
        "role": str(getattr(ctx, "user_role", "") or ""),
        "workspace_id": int(getattr(ctx, "workspace_id", 0) or 0),
        "datasource_id": int(getattr(ctx, "datasource_id", 0) or 0),
        "site_code": str((ctx.extra or {}).get("site_code") or ""),
        "conversation_id": int((ctx.extra or {}).get("conversation_id") or 0),
        "message_uuid": str(message_uuid or ""),
    }


def _mark_turn_interrupted(reason: str) -> None:
    """把"回合没跑完/结果没送达"如实写进 trace，不得让 finalize 回落成 success。

    观测未开启时是 no-op（护栏 §9：旁路不影响主链路），落库与日志路径照旧。
    """
    from backend import observability
    observability.set_result(status="error", error=reason)


def _server_elapsed_ms(result) -> int:
    """回合耗时的**唯一对外口径 = 服务端单调钟**（与 trace.duration_ms 同源）。

    历史缺陷：消息里的 elapsed_ms 是前端 `Date.now() - startTime`，与 trace 的
    300.2s 差出 22.7s，同一个回合两套数字，"到底跑满没跑满"各说各话。
    优先取 trace recorder 的口径（回合起点 = 入口 begin）；观测未开启时退回
    执行层自己的单调钟（execute_stream 起点），仍是服务端时钟 —— 绝不回落到
    浏览器时间（那是把口径分裂原样保留）。
    """
    from backend import observability
    ms = observability.turn_elapsed_ms()
    if ms is None:
        ms = (result.meta or {}).get("elapsed_ms")
    return int(ms) if ms is not None else 0


def _slash_directive(key: str) -> str:
    """把 `/` 指令 key 翻译成提示词作业要求（空 key 返回空串）。

    未知 key 在两个 API 入口已显式拒（不静默丢弃），这里只兜内部调用方漏校验的情形：
    记 warning + 不注入，绝不拿一个不认识的指令冒充“已按用户要求执行”。
    """
    if not key:
        return ""
    from backend.modules.mind.execution import slash_commands
    directive = slash_commands.directive_for(key)
    if not directive:
        logger.warning("[chat] 未登记的 / 指令被忽略: %s", key)
    return directive


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
        selected_skills: Optional[list] = None,
        selected_scenes: Optional[list] = None,
        slash_command: str = "",
        chat_mode: str = "",
    ):
        """Stream a query through the qoder execution layer (agent dispatch).

        attachments: 随消息上传的附件文件描述 [{filename, category, size, content}](见 send_payload)。
        task_binding: 服务端注入的任务绑定标记(如 {"kind": "ontology_generate", "datasource_id": N}),
        供工具侧域约束裁决(assert_ontology_draft_scope)；不进 LLM 视野。
        selected_skills / slash_command: 聊天输入框 `/` 选中的技能名与指令 key（只接受
            服务端已登记的指令，见 slash_commands）；它们只影响提示词组装，不影响权限裁决。
        chat_mode: 输入框快速入口（analysis / knowledge，见 slash_commands.MODES）：只决定
            本轮主路展开哪份作业细则，不是权限开关，也不屏蔽另一条路的能力。
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
                selected_skills=selected_skills,
                selected_scenes=selected_scenes,
                slash_command=slash_command,
                chat_mode=chat_mode,
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
        selected_skills: Optional[list] = None,
        selected_scenes: Optional[list] = None,
        slash_command: str = "",
        chat_mode: str = "",
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
        from backend.modules.mind.execution import capacity
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
                    # `/` 选中的技能名与指令翻译出的作业要求：服务端内部键，
                    # 只影响提示词组装（见 secure_sdk.build_options），不进工具入参。
                    ("selected_skills", [str(s) for s in (selected_skills or []) if str(s).strip()]),
                    # `/` 选中的分析场景 id（与场景候选同源下发）：服务端按主体权限裁决后
                    # 强制注入方案，无权/不存在的连同原因回传给用户（见 secure_sdk + prompt_composer）。
                    ("selected_scenes", [str(s).strip() for s in (selected_scenes or []) if str(s).strip()]),
                    ("slash_directive", _slash_directive(slash_command)),
                    # 问答模式（深度分析/快速问答）：只带 key，由 secure_sdk 同源翻译为
                    # 作业要求与对应剧本名（避免两套措辞漂移）；不进工具入参/LLM 直接视野。
                    ("chat_mode", str(chat_mode or "")),
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
                yield _sse_event("done", _local_done,
                                 _decision_identity(ctx, str(_local_done.get("message_uuid") or "")))
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
        try:
            # 选层口径走 `service.pick_layer_for_as_bot` 唯一实现（与上下文压缩 / 定时分析共用）：
            # AS-BOT 绑了执行层就用它，没绑则跟随工作空间默认层。不在此内联一份规则——
            # 两份很容易一处判 active、一处判 healthy 而漂移成“问答能跑、压缩说找不到执行层”。
            row = exec_service.pick_layer_for_as_bot(
                getattr(policy, "as_bot", None) or {}, workspace_id)
        except exec_service.LayerSelectionError as exc:
            # AS-BOT 绑的层不可用：把原因与改法直接告知，不静默退回默认层（两条层的
            # 模型通道/能力上限/计费口径都不同，默默换层等于给用户错的答案）。
            logger.warning("AS-BOT 执行层不可用: %s", exc)
            yield _sse_event("error", {"message": str(exc)})
            yield _sse_event("done", {"error": str(exc), "handled_by": intent_gate.HANDLED_AGENT,
                                      "gate": gate_snapshot})
            return
        except Exception:  # noqa: BLE001 — 配置读取失败必须显式告知，不能默默换层
            logger.exception("工作空间执行层权限解析失败")
            yield _sse_event("error", {"message": "Harness 权限配置暂不可用"})
            yield _sse_event("done", {"error": "Harness 权限配置暂不可用",
                                      "handled_by": intent_gate.HANDLED_AGENT, "gate": gate_snapshot})
            return
        if row is None or row.get("status") != "active":
            err = "Agent 模式不可用:未找到可用的外部 Harness(qoder 层缺失或未启用)"
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

        # 并发闸门：人多时让后来者**看得见自己在排队**（第几位/预计多久），而不是对着
        # 加载图标猜。必须在 claim_session 之前拿名额：先占会话执行权再排队，既会让
        # status='running' 把用户自己的重试也挡在 409 外，又空转着一个名额。
        gate = capacity.get_gate()
        queue_events: asyncio.Queue = asyncio.Queue()

        async def _on_queued(info: dict) -> None:
            await queue_events.put({"event": "queued", **info})

        acquire = asyncio.create_task(gate.acquire(_on_queued))
        slot_handle = None
        try:
            # 等待期间不停下发排队位置（排队可能是几十秒，不能只报一次就静默）
            while not acquire.done():
                try:
                    _info = await asyncio.wait_for(queue_events.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                yield _sse_event("capacity", _info)
            slot_handle = acquire.result()
        except capacity.GateIntegrityError as exc:
            # 闸门计数不一致：宁可拒接本回合也不能“当没排队”放行（超载会把所有回合
            # 一起推到墙钟超时，且事后无从归因）。文案自带下一步，不是裸错误码。
            logger.error("并发闸门拒接新回合: %s", exc)
            err = str(exc)
            observability.set_result(status="error", error=err)
            yield _sse_event("error", {"message": err, "retry_after_s": 5})
            yield _sse_event("done", {"intent": "agent", "reply": err, "error": err,
                                      "handled_by": intent_gate.HANDLED_AGENT, "gate": gate_snapshot,
                                      "error_kind": "capacity", "retryable": True})
            return
        except BaseException:
            # 排队期间断连/被取消：已排到的名额当场归还，未排到的退出队列，不泄漏计数
            if not acquire.done():
                acquire.cancel()
            elif acquire.succeeded():
                gate.release(acquire.result())
            raise

        if slot_handle and slot_handle.waited_ms > 0:
            observability.set_capacity(queue_wait_ms=slot_handle.waited_ms)
            yield _sse_event("capacity", {"event": "started", "waited_ms": slot_handle.waited_ms})

        try:
            if row.get("layer_type") != "cli" or (row.get("config") or {}).get("mode") != "sdk":
                raise ValueError("所选 Harness 不支持 AS-BOT 安全执行")
            adapter = manager.build_adapter(row)
            # 回合时长上限以**执行层配置**为准：ExecutionTask.timeout 的数据类默认值是 300，
            # 不覆盖的话管理员把层的 timeout 调成 600/120 都不会生效（配置面承诺了却没人读，
            # 与"角色页配了没用"同一类空承诺）。secure_sdk 侧仍保留 900s 天花板。
            _layer_timeout = int(getattr(adapter, "timeout", 0) or 0)
            if _layer_timeout > 0:
                task.timeout = _layer_timeout
            # 流式执行:CLI 输出逐块以 token 事件推送到前端
            result = None
            stream = adapter.execute_stream(task)
            try:
                async for ev in stream:
                    if ev.get("type") == "capabilities":
                        yield _sse_event("capabilities", ev["data"])
                    elif ev.get("type") == "token":
                        if await request.is_disconnected():
                            _mark_turn_interrupted("客户端已断开，本回合未产出最终结果（正文由服务端兜底落库）")
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
                    elif ev.get("type") in ("context", "capacity"):
                        # 上下文压缩、自动重试这类“额外耗时但过去完全不看得见”的事件原样透出
                        yield _sse_event(ev["type"], {k: v for k, v in ev.items() if k != "type"})
                    elif ev.get("type") == "done":
                        result = ev.get("result")
            finally:
                await stream.aclose()
            if result is None:
                result = ExecutionResult(success=False, error="Harness 未返回结果")
        except Exception as e:
            logger.error("Execution layer dispatch error: %s", e, exc_info=True)
            message = str(e) if isinstance(e, (ValueError, PermissionError)) else "Harness 未能安全完成，请联系管理员"
            yield _sse_event("error", {"message": message})
            yield _sse_event("done", {"intent": "agent", "reply": message, "error": message,
                                      "handled_by": intent_gate.HANDLED_AGENT, "gate": gate_snapshot})
            return
        finally:
            # 名额只包住“模型工作”本身（后台收尾/上云镜像不占并发槽），否则长尾 IO
            # 会把后续用户排在毫无必要的队尾。
            gate.release(slot_handle)
            slot_handle = None

        if await request.is_disconnected():
            # done 还没发给前端就被断开：这条 trace 若记 success + 空 final_answer，
            # 管理端看到的就是"跑了 126s 的成功的回合"，而用户什么都没拿到
            # （会话 1783069459150 的假成功根因）。
            _mark_turn_interrupted("客户端已断开，本回合结果未送达（正文由服务端兜底落库）")
            return

        if result.success:
            observability.set_result(status="success", final_answer=result.output or "")
            _done = {
                "intent": "agent",
                "reply": result.output,
                "sql": None,
                # 执行层给出的用户可见告警（如本轮工具轮次触顶后收尾）必须原样下发，
                # 不得回空列表把「本轮没收尾完」这件事藏掉（no-silent-degradation §1）。
                "warnings": list(result.meta.get("warnings") or []),
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
                    "elapsed_ms": _server_elapsed_ms(result),
                    "tool_rounds": result.meta.get("tool_rounds") or {},
                    "retry_attempts": int(result.meta.get("retry_attempts") or 0),
                },
                # 上下文占用（本轮客户端还活着时采集的权威值）。采不到就不塞假值：
                # 前端转去读 /context-usage，由它区分“未测量”与“测量失败”，不显示 0%
                "context_usage": result.meta.get("context_usage") or None,
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
            yield _sse_event("done", _done,
                             _decision_identity(ctx, str(_done.get("message_uuid") or "")))
        else:
            err = result.error or "Harness 执行失败"
            observability.set_result(status="error", error=err)
            # error_kind 是前端错误卡的分支依据：“模型繁忙/超时”与“配置坏了”不可能是同一句话，
            # 否则用户分不清是自己问法问题还是平台过载（原始报错细节只进日志，护栏 §7）。
            _kind = result.meta.get("error_kind") or "failed"
            yield _sse_event("error", {"message": err, "status_code": result.meta.get("status_code"),
                                       "error_kind": _kind,
                                       "retryable": bool(result.meta.get("retryable"))})
            # 中止回合同样要交代清楚：已跑时长/已发出的工具调用次数走 stats，
            # 关联键 trace_id/message_uuid 一并回带（否则管理端 trace 与前端消息对不上号，
            # 用户只能反问「到底是单次查询慢还是轮次多」）。
            _err_done = {
                "intent": "agent",
                "reply": err,
                "sql": None,
                "warnings": list(result.meta.get("warnings") or []),
                "error": err,
                "handled_by": intent_gate.HANDLED_AGENT,
                "gate": gate_snapshot,
                "stats": {
                    "elapsed_ms": _server_elapsed_ms(result),
                    "tool_call_count": result.meta.get("tool_call_count") or 0,
                    "queries": result.meta.get("query_count") or 0,
                    "timed_out": bool(result.meta.get("timed_out")),
                    "turn_limit_seconds": result.meta.get("turn_limit_seconds"),
                },
                "error_kind": _kind,
                "retryable": bool(result.meta.get("retryable")),
                "retry_after_s": result.meta.get("retry_after_s"),
                "retry_attempts": int(result.meta.get("retry_attempts") or 0),
            }
            _tid = observability.trace_id_for_response()
            _muuid = observability.message_uuid()
            if _tid:
                _err_done["trace_id"] = _tid
            if _muuid:
                _err_done["message_uuid"] = _muuid
            yield _sse_event("done", _err_done)
