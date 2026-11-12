"""Task Executor — Celery tasks for scheduled Agent analysis.

工作空间的定时任务只有一种形态：**Agent 分析任务**（task_type=agent）。执行身份、
能力面与可查数据范围都按任务属主的角色实时继承，任务本身不持有 AS-BOT 与数据源。
SQL 定时任务不在这里——那是数据中台「数据同步」的职责（adh_sync_tasks.schedule_cron /
adh_dag_workflows.cron_expression），两套调度不互相冒充。

每个任务创建一条执行日志、逐题跑无人值守分析、按模板可选地生成报告，
并向配置的通知渠道发送结果。
"""

import asyncio
import logging
import time
from billiard.exceptions import SoftTimeLimitExceeded
from backend.core.task_runtime import guarded_call, RunInterrupted, call, send_task

from backend.modules.flow.tasks.celery_app import app

logger = logging.getLogger(__name__)

def cancel_running_task(log_id: int):
    """跨进程取消，终态由数据库条件更新保护。"""
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service
    return scheduled_task_service.finish_log(log_id, status="cancelled",
                                             stage_error_code="CANCELLED", finished_at=_now_iso())


def _now_iso():
    from datetime import datetime
    return datetime.now().isoformat()


def _persist_progress(task: dict):
    """逐题进度落库供任务监控展示；旁路观测失败不阻断执行主链路（护栏 §9）。"""
    try:
        from backend.modules.flow.services.scheduled_task_service import scheduled_task_service as service
        results = task.get("_results") or []
        succeeded = sum(r.get("status") == "success" for r in results)
        service.update_progress(task["_log_id"], succeeded, len(results) - succeeded)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Executor] 进度落库失败(不影响执行): %s", exc)


def _run_async(coro_or_gen):
    """Run an async coroutine or async generator in sync context.

    For async generators (like agent_generate), collects all yielded values
    and returns the data from the last 'done' event.
    For regular coroutines, returns the result directly.
    """
    import asyncio
    import inspect

    async def _collect():
        if inspect.isasyncgen(coro_or_gen):
            # Async generator — collect all yields, return the 'done' event data
            result = None
            async for value in coro_or_gen:
                if isinstance(value, tuple) and len(value) == 2:
                    event_type, data = value
                    if event_type == "done":
                        result = data
            return result
        else:
            # Regular coroutine
            return await coro_or_gen

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            import contextvars
            return pool.submit(contextvars.copy_context().run, asyncio.run, _collect()).result()
    else:
        return asyncio.run(_collect())


def _execute_agent_mode(task: dict) -> list:
    """无人值守分析复用 SDK 语义工具链，身份来自任务创建者。"""
    import asyncio
    results = task.setdefault("_results", [])
    for q in task["task_config"].get("questions", []):
        _check_running(task)
        try:
            for attempt in range(max(0, min(int(task.get("max_retries") or 0), 3)) + 1):
                response = _wait(task, lambda: _run_async(call("mind.analyze_question", 
                    q.get("question", ""), task["task_config"], task["_identity"],
                    check=lambda: _check_running(task))))
                if not response.get("retryable") or response.get("_analysis_results") or attempt >= min(int(task.get("max_retries") or 0), 3):
                    break
            results.append({"title": q.get("title", "分析"), "attempts": attempt + 1, **response})
            _persist_progress(task)
        except (RunInterrupted, TimeoutError, asyncio.TimeoutError, SoftTimeLimitExceeded):
            raise
        except Exception:
            logger.exception("任务 Agent 问题执行失败")
            results.append({"title": q.get("title", "分析"), "status": "failed",
                            "error": "分析未完成，请检查配置或权限"})
            _persist_progress(task)
    return results


def _generate_report(task: dict, results: list) -> tuple[str, str]:
    flattened = [item for result in results for item in result.get("_analysis_results", [result])]
    sources = []
    for result in flattened:
        if result.get("status") != "success":
            continue
        security = result.get("_security_context")
        if not security:
            raise PermissionError("报告缺少来源权限快照")
        call("report.verify_snapshot", task["_identity"], security)
        for source in security["sources"]:
            if source not in sources:
                sources.append(source)
    task["_security_context"] = call("report.policy_snapshot", task["owner_id"], task["workspace_id"], sources)
    tpl = task.get("_report_template") or {}
    if tpl.get("found"):
        # 按模板出正文：单问且模型终稿达标则沿用版式（带叙述与 ```chart），否则退事实表格并显式记原因。
        # 与报表中心「用最新数据重新生成」同一 render 口径（viz 一处实现，执行器不复制判断）。
        answer = next((r.get("answer") for r in results if isinstance(r, dict) and r.get("answer")), "")
        content, fmt, layout_result = call("report.render_template",
            task.get("name", "分析报告"), flattened, answer, tpl.get("layout") or {},
            tpl.get("theme") or "", single_question=len(results) <= 1)
        task["_layout_result"] = layout_result
        return _with_evidence(task, flattened, content, fmt)
    return _with_evidence(task, flattened,
                          call("report.render_fact", task.get("name", "分析报告"), flattened), "markdown")


def _with_evidence(task: dict, flattened: list, content: str, fmt: str) -> tuple[str, str]:
    """把报表终态与证据摘要挂在任务上，随报表落库。

    判定口径在 viz 一处（report.evidence_verdict），执行器不复制一套、也不再写死 degraded；
    快照逐条已在上面 verify 过，所以“证据已登记”是事实，不是默认。
    """
    status, summary = call("report.evidence_verdict", flattened, content=content)
    task["_generation_status"] = status
    task["_evidence_summary"] = summary
    return content, fmt


def _send_notification(task: dict, results: list, report_content: str = None,
                       report_format: str = "markdown", report_link: str = None) -> str:
    """按渠道引用的消息模板渲染并发送通知，回 ``sent``/``failed``/``skipped``。

    正文唯一来源 = 渠道 ``config.message_template_key`` 指向的模板（模板中心维护）；
    没选模板或模板读不到时回平台默认文案，并把原因写进日志（此前这里硬编码一句、
    渠道里填的消息模板根本没人消费 —— docstring 与实现不符已一并纠正）。
    """
    from backend.modules.flow.services import message_template_service as mts
    from backend.modules.flow.services.notification_service import notification_service
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service

    channel_id = task.get("channel_id")
    if not channel_id:
        return "skipped"

    channel = scheduled_task_service.get_channel(channel_id)
    if not channel or not channel.get("is_active"):
        return "skipped"
    if int(channel.get("workspace_id") or 0) != int(task.get("workspace_id") or 0):
        return "failed"
    # 通知只发送执行状态与需登录的报告链接，不含业务标题、正文、数据行或 token。
    # 可用变量口径唯一来自 message_template_service.MESSAGE_TEMPLATE_VARIABLES：
    # 任务名与结果摘要**不在其中**，所以模板作者拿不到业务内容（护栏与用例同口径）。
    ctx = mts.build_context(task, results, report_link or "")
    try:
        out = notification_service.send_rendered(channel_id, ctx)
    except Exception as exc:
        logger.error("[Executor] Notification failed: %s", exc)
        scheduled_task_service.update_channel_test_status(channel_id, "failed")
        return "failed"
    # 渲染非正常态必须留痕：模板被删/下架、或正文用了发不出去的变量时，通知会突然变短
    # 或带着 {{xxx}} 字面量 —— 没有这行日志就只剩"通知怎么长这样"（§1 可诊断）。
    if (out.get("status") != "rendered" or out.get("unknown_variables")
            or out.get("warnings")):
        logger.warning(
            "[Executor] 通知渲染非正常态 channel=%s status=%s reasons=%s unknown=%s warnings=%s",
            channel_id, out.get("status"), "; ".join(out.get("reasons") or []),
            out.get("unknown_variables"), "; ".join(out.get("warnings") or []))
    scheduled_task_service.update_channel_test_status(channel_id, "success")
    return "sent"


def _save_report_and_get_link(task: dict, log_id: int, report_content: str,
                               report_format: str) -> str:
    """Save report to DB and return a viewable link."""
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service
    import os

    task_name = task.get("name", "报告")
    task_id = task.get("id", 0)
    workspace_id = task.get("workspace_id", 0)
    owner_id = task.get("owner_id", 0)
    access_mode = "private"  # Default to private

    # 把模板沿用结果（layout_result）写进 analysis_source：报表中心据此回显“这版有没按模板版式”，
    # 不沿用就把原因显式带给用户（no-silent-degradation）；不开第二列，与报表重跑同一回读口径。
    tpl = task.get("_report_template") or {}
    analysis_source = {"kind": "scheduled_report",
                       "template_key": task.get("report_template_key") or "",
                       "template_name": tpl.get("name") or "",
                       "report_theme": tpl.get("theme") or ""}
    if task.get("_layout_result"):
        analysis_source["layout_result"] = task["_layout_result"]

    report = scheduled_task_service.create_report(
        task_id=task_id,
        log_id=log_id,
        title=f"{task_name} - {_now_iso()[:10]}",
        content=report_content,
        format=report_format,
        access_mode=access_mode,
        workspace_id=workspace_id,
        owner_id=owner_id,
        run_key=task["_run_key"],
        security_context=task.get("_security_context"),
        generation_status=task.get("_generation_status") or "ready",
        analysis_source=analysis_source,
        evidence_summary=task.get("_evidence_summary") or {},
    )

    # Build link
    # 默认 base_url 与前端监听口同源（根目录 .env 的 FRONTEND_PORT，经 config 解析）：
    # 历史上此处写死 :3000，换端口后通知里的报告链接指向无服务的口。显式配置
    # ADH_BASE_URL 仍为最高优先级（多域名/生产对外地址）；空值按未配置处理，
    # 不得拼出 "/report/1" 这种缺 host 的坏链接。
    from backend.common import config as _cfg
    base_url = os.getenv("ADH_BASE_URL") or f"http://localhost:{_cfg.frontend_port()}"
    report_id = report["id"]
    return f"{base_url}/report/{report_id}"


@app.task(
    name="backend.modules.flow.tasks.executor.execute_scheduled_task",
    bind=True,
    queue="scheduled",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_scheduled_task(self, task_id: int, trigger_type: str = "cron", run_key: str = None):
    """Celery 仅适配派发身份；消息 ID 在入队前生成，重投复用同一逻辑运行键。"""
    return _execute_core(task_id, trigger_type, run_key or self.request.id,
                         self.request.hostname or "celery")


def execution_status(results: list, report_required: bool, report_ok: bool) -> str:
    succeeded = sum(r.get("status") == "success" for r in results)
    any_data = any(r.get("status") in ("success", "partial") for r in results)
    if not any_data or (report_required and not report_ok):
        return "failed"
    return "success" if succeeded == len(results) else "partial"


_Stopped = RunInterrupted


def _wait(task, operation, *args):
    return guarded_call(operation, *args, check=lambda: _check_running(task),
                        deadline=task["_deadline"], stop_event=task.get("_stop_event"))


def _resolve_owner(task):
    from backend.common.auth import resolve_execution_owner
    return resolve_execution_owner(task.get("owner_id"), task.get("workspace_id") or 0)


def _check_running(task):
    if task.get("_stop_event") and task["_stop_event"].is_set():
        raise _Stopped("cancelled")
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service as service
    log = service.get_log(task["_log_id"])
    if not log or log["status"] != "running":
        raise _Stopped((log or {}).get("status", "cancelled"))
    if time.monotonic() >= task["_deadline"]:
        raise _Stopped("timeout")


def _execute_core(task_id, trigger_type, run_key=None, worker_id="sync-process"):
    from uuid import uuid4
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service as service
    from billiard.exceptions import SoftTimeLimitExceeded

    task = service.get_task(task_id)
    if not task:
        return {"status": "failed", "stage_error_code": "TASK_NOT_FOUND"}
    run_key = run_key or uuid4().hex
    log_id = service.create_log(task_id, trigger_type, "queued", run_key=run_key,
                                workspace_id=task.get("workspace_id") or 0)
    timeout = max(1, min(int(task.get("timeout_seconds") or 300), 3600))
    if not service.claim_log(log_id, worker_id, timeout):
        log = service.get_log(log_id) or {}
        return {"log_id": log_id, "run_key": run_key, "status": log.get("status"), "duplicate": True}
    start = time.monotonic()
    task = {**task, "_log_id": log_id, "_run_key": run_key, "_deadline": start + timeout}
    results, report_link, error_code = [], None, ""
    task["_results"] = results
    import threading
    task["_stop_event"] = threading.Event()
    status = "failed"
    try:
        if trigger_type == "cron" and not task.get("is_active"):
            raise _Stopped("cancelled")
        if not task.get("owner_id"):
            error_code = "OWNER_REQUIRED"
        task["_identity"] = _resolve_owner(task)
        # validate_task_as_bot 内部即"按属主角色解析 AS-BOT + 可查范围"，
        # 不在这里再查一遍数据源（源可用性只有一个裁决点：角色授权集）。
        call("mind.validate_task_as_bot", task)
        _check_running(task)
        config = task["task_config"]
        if task["task_type"] != "agent":
            raise ValueError("任务类型无效：工作空间定时任务只有 Agent 分析形态"
                             "（SQL 定时任务请在数据中台的数据同步里配置）")
        if not config.get("questions"):
            raise ValueError("任务没有可执行的问题")
        # 报告模板：解析成版式契约随 agent 运行带走（与报表中心重跑/问数出报告同一口径）。
        # 设了模板却读不到 → 显式失败，不静默退通用事实表冒充“已按模板生成”（no-silent-degradation §1）。
        tpl_key = task.get("report_template_key")
        if tpl_key:
            tpl = call("report.resolve_template", tpl_key)
            if not tpl.get("found"):
                raise ValueError(tpl.get("reason") or "报告模板不可用")
            task["_report_template"] = tpl
            config["report_theme"] = tpl.get("theme") or ""
            # 无版式时 report_layout_contract 自判回空串，不往提示词灌空契约
            config["layout_contract"] = call("report.layout_contract",
                                             tpl.get("layout") or {}, tpl.get("theme") or "")
        results = _execute_agent_mode(task)
        _check_running(task)
        required = bool(task.get("report_template_key"))
        if required and any(r["status"] in ("success", "partial") for r in results):
            try:
                content, fmt = _wait(task, _generate_report, task, results)
                if not content:
                    raise ValueError("必需报告未生成")
                _check_running(task)
                report_link = _save_report_and_get_link(task, log_id, content, fmt)
            except (RunInterrupted, TimeoutError, asyncio.TimeoutError, SoftTimeLimitExceeded):
                raise
            except Exception:
                logger.exception("任务 %s 报告阶段失败", task_id)
                error_code = "REPORT_FAILED"
        status = execution_status(results, required, bool(report_link))
        if status != "success" and not error_code:
            error_code = next((r.get("error_code") for r in results if r.get("error_code")), "QUERY_FAILED")
        _check_running(task)
    except _Stopped as exc:
        status, error_code = exc.status, exc.status.upper()
    except (TimeoutError, asyncio.TimeoutError, SoftTimeLimitExceeded):
        status, error_code = "timeout", "TIMEOUT"
    except Exception as exc:
        logger.exception("任务 %s 执行失败", task_id)
        error_code = error_code or getattr(exc, "code", "EXECUTION_FAILED")
    if status not in ("success", "partial"):
        task["_stop_event"].set()
    succeeded = sum(r.get("status") == "success" for r in results)
    failed = len(results) - succeeded
    elapsed = int((time.monotonic() - start) * 1000)
    # 只持久化状态摘要；数据快照统一由报告访问策略保护，日志不存 SQL/结果行。
    committed = service.finish_log(
        log_id, status=status, questions_succeeded=succeeded, questions_failed=failed,
        result_summary=f"成功 {succeeded} 项，失败 {failed} 项", stage_error_code=error_code,
        error_message="任务未完整完成，请检查配置、权限或执行日志" if error_code else "",
        elapsed_ms=elapsed, finished_at=_now_iso(),
    )
    status = (service.get_log(log_id) or {}).get("status", status)
    notify_flag = "notify_on_success" if status == "success" else "notify_on_failure"
    notify_claimed = False
    if committed and task.get("channel_id") and task.get(notify_flag, True):
        try:
            notify_claimed = service.claim_notification(log_id)
        except Exception:
            logger.exception("通知领取不可用，不影响执行终态")
    if notify_claimed:
        try:
            notify_status = _send_notification(task, [{"status": status}], report_link=report_link)
        except Exception:
            logger.exception("任务 %s 通知失败", task_id)
            notify_status = "failed"
        try:
            service.update_log(log_id, notify_status=notify_status)
        except Exception:
            logger.exception("通知状态落库失败，保留 sending 供对账")
    return {"success": status == "success", "status": status, "log_id": log_id,
            "run_key": run_key, "succeeded": succeeded, "failed": failed, "elapsed_ms": elapsed}


@app.task(name="flow.generate_report", queue="scheduled", acks_late=True)
def generate_report_task(report_id: int):
    return call("report.run", report_id)


@app.task(name="backend.modules.flow.tasks.executor.reconcile_runs", queue="scheduled")
def reconcile_runs():
    """内置任务：运行对账与卡死清理（可经任务监控页暂停，运行结果落 adh_system_jobs）。"""
    from backend.common import system_jobs
    if not system_jobs.is_job_active("runs_reconcile"):
        logger.info("[Reconcile] 内置任务已人工暂停，本轮对账跳过")
        return {"skipped": "paused"}
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service
    try:
        result = {"tasks": scheduled_task_service.cleanup_stale_running_logs(),
                  "reports": call("report.cleanup_stale")}
        system_jobs.record_run("runs_reconcile", "success",
                               f"清理卡死执行实例 {result['tasks']} 个、报告生成 {result['reports']} 个")
        return result
    except Exception as exc:  # noqa: BLE001 — 记录后显式抛出，不把失败掩成成功
        logger.exception("[Reconcile] 运行对账失败")
        system_jobs.record_run("runs_reconcile", "failed",
                               f"对账失败（{type(exc).__name__}），详见服务端日志")
        raise


@app.task(name="backend.modules.flow.tasks.executor.evolution_digest", queue="scheduled")
def evolution_digest():
    """内置任务：进化候选归纳（自进化闭环的周期引擎）。

    失败必须落 `status='failed'` 并抛出：归纳挂了而进化中心看上去"没有新待办"，
    会把闭环失败掩成"本轮没发现东西"（no-silent-degradation §1）。
    """
    from backend.common import system_jobs
    if not system_jobs.is_job_active("evolution_digest"):
        logger.info("[Evolution] 内置任务已人工暂停，本轮归纳跳过")
        return {"skipped": "paused"}
    try:
        # 经 core.task_runtime 回调表跨模块调用（分层契约 §0：不新增 L2 横向 import 边）
        out = call("evolution.run_digest")
        system_jobs.record_run("evolution_digest", "success", out.get("summary", "")[:500])
        return out
    except Exception as exc:  # noqa: BLE001 — 记录后显式抛出，不把失败掩成成功
        logger.exception("[Evolution] 进化候选归纳失败")
        system_jobs.record_run("evolution_digest", "failed",
                               f"归纳失败（{type(exc).__name__}），详见服务端日志")
        raise


@app.task(name="backend.modules.flow.tasks.executor.intent_intercept_retention", queue="scheduled")
def intent_intercept_retention():
    """内置任务：前置意图漏斗判定流水滚动清理。

    shadow 档每一轮输入都落一行，不清理会把元库写成只增不减的日志海；
    但清理失败不能静默 —— 行越积越多最后拖慢写入是看得见的代价，
    所以失败照样 record_run('failed') 并抛出（no-silent-degradation）。
    """
    from backend.common import system_jobs
    if not system_jobs.is_job_active("intent_intercept_retention"):
        logger.info("[IntentGate] 内置任务已人工暂停，本轮清理跳过")
        return {"skipped": "paused"}
    try:
        deleted = call("intent_gate.purge_intercepts")
        system_jobs.record_run("intent_intercept_retention", "success",
                               f"清理过期判定流水 {deleted} 行（已复盘行保留）")
        return {"deleted": deleted}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[IntentGate] 判定流水清理失败")
        system_jobs.record_run("intent_intercept_retention", "failed",
                               f"清理失败（{type(exc).__name__}），详见服务端日志")
        raise


@app.task(name="backend.modules.flow.tasks.executor.conversation_history_retention", queue="scheduled")
def conversation_history_retention():
    """内置任务：会话与观测流水保留策略（超期观测删除 + 长期会话剥明细）。

    只增不减的代价是看得见的（写放大 / 敏感数据驻留窗口），清理失败必须
    record_run('failed') 并抛出，不把"流水还在涨"掩成"本轮无事可清"（no-silent-degradation §1）。
    子步骤失败不重试不回退：下一日调度会重跑（任务幂等，仅处理 storage_state='full'）。
    """
    from backend.common import system_jobs
    if not system_jobs.is_job_active("conversation_history_retention"):
        logger.info("[Retention] 内置任务已人工暂停，本轮保留策略跳过")
        return {"skipped": "paused"}
    try:
        # 经 core.task_runtime 回调表跨模块调用（分层契约 §0：不新增 L2 横向 import 边）
        out = call("conversation_retention.run")
        system_jobs.record_run("conversation_history_retention", "success",
                               str(out.get("summary") or "")[:500])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.exception("[Retention] 会话与观测流水保留策略执行失败")
        system_jobs.record_run("conversation_history_retention", "failed",
                               f"执行失败（{type(exc).__name__}），详见服务端日志")
        raise


def execute_scheduled_task_sync(task_id: int, trigger_type: str = "manual", run_key: str = None):
    """显式同步适配器，与 Celery/后台使用相同执行核心。"""
    return _execute_core(task_id, trigger_type, run_key, "sync-process")


async def execute_scheduled_task_async(task_id: int, trigger_type: str = "manual", run_key: str = None):
    """本地后台适配器；线程传播上下文，不阻塞 FastAPI 事件循环。"""
    import asyncio
    return await asyncio.to_thread(_execute_core, task_id, trigger_type, run_key, "async-bg")
