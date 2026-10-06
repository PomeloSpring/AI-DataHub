"""Task Executor — Celery tasks for scheduled SQL and Agent execution.

Two execution modes:
- query: Direct SQL execution on datasource, returns raw results
- agent: Calls agent_generate() pipeline, LLM autonomously plans and executes

Each task creates an execution log, runs the queries, optionally generates
a report from a template, and sends notifications to the configured channel.
"""

import asyncio
import logging
import time
from billiard.exceptions import SoftTimeLimitExceeded
from services.shared.common.task_runtime import guarded_call, RunInterrupted

from services.dataflow.tasks.celery_app import app

logger = logging.getLogger(__name__)

def cancel_running_task(log_id: int):
    """跨进程取消，终态由数据库条件更新保护。"""
    from services.dataflow.services.scheduled_task_service import scheduled_task_service
    return scheduled_task_service.finish_log(log_id, status="cancelled",
                                             stage_error_code="CANCELLED", finished_at=_now_iso())


def _now_iso():
    from datetime import datetime
    return datetime.now().isoformat()


def _persist_progress(task: dict):
    """逐题进度落库供任务监控展示；旁路观测失败不阻断执行主链路（护栏 §9）。"""
    try:
        from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
        results = task.get("_results") or []
        succeeded = sum(r.get("status") == "success" for r in results)
        service.update_progress(task["_log_id"], succeeded, len(results) - succeeded)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Executor] 进度落库失败(不影响执行): %s", exc)


def _execute_sql_on_datasource(sql: str, datasource_id: int, identity: dict) -> dict:
    """可信配置 SQL 必须先校验限流，再使用统一治理入口。"""
    from services.dataviz.services.governed_query import governed_execute
    from services.shared.semantics.sql_guard import bounded_query
    sql = bounded_query(sql, 1000)
    return governed_execute(sql, datasource_id, identity["user_id"],
                            identity["workspace_id"], identity.get("username", ""))


def _collect_lineage(sql: str, datasource_id: int, workspace_id: int, title: str):
    """Best-effort lineage collection after successful SQL execution.

    纯 SELECT 无写目标不会产生血缘边；解析/写入失败不影响任务本身。
    """
    try:
        from services.datagov.services.lineage_service import persist_sql_lineage
        result = persist_sql_lineage(sql, datasource_id, workspace_id)
        if result["edges_created"]:
            logger.info(
                "[Executor] Lineage collected for '%s': %d nodes, %d edges",
                title, len(result["nodes_created"]), len(result["edges_created"]),
            )
    except Exception as e:
        logger.warning("[Executor] Lineage collection failed for '%s': %s", title, e)


def _execute_sql_mode(task: dict) -> list:
    """SQL mode: execute each SQL statement directly."""
    config = task["task_config"]
    datasource_id = config["datasource_id"]
    workspace_id = task.get("workspace_id", 0)
    results = task.setdefault("_results", [])

    for q in config.get("questions", []):
        _check_running(task)
        title = q.get("title", "统计")
        sql = q.get("sql", "")
        if not sql:
            results.append({"title": title, "status": "failed", "error": "Empty SQL"})
            _persist_progress(task)
            continue
        try:
            security = None
            if task.get("report_template_key"):
                from services.dataviz.services import report_service
                security = report_service._snapshot(task["_identity"], sql, datasource_id)
            for attempt in range(max(0, min(int(task.get("max_retries") or 0), 3)) + 1):
                _check_running(task)
                try:
                    result = _wait(task, _execute_sql_on_datasource, sql, datasource_id, task["_identity"])
                    break
                except (RunInterrupted, PermissionError, TimeoutError, asyncio.TimeoutError, SoftTimeLimitExceeded):
                    raise
                except Exception:
                    if attempt >= min(int(task.get("max_retries") or 0), 3):
                        raise
            if security:
                report_service._verify_snapshot(task["_identity"], security)
            if result.get("error"):
                raise RuntimeError("查询未完成")
            results.append({
                "title": title,
                "status": "success",
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "row_count": result.get("row_count", 0),
                "_security_context": security,
            })
            # 执行成功后自动采集血缘（用原始 SQL，不带 LIMIT 后缀）
            _collect_lineage(sql.strip().rstrip(";"), datasource_id, workspace_id, title)
            _persist_progress(task)
        except (RunInterrupted, TimeoutError, asyncio.TimeoutError, SoftTimeLimitExceeded):
            raise
        except Exception as e:
            logger.warning("[Executor] SQL failed for '%s': %s", title, e)
            results.append({"title": title, "status": "failed", "error": "查询未完成，请检查配置或权限"})
            _persist_progress(task)

    return results


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
    from services.datamind.execution.scheduled_analysis import analyze_question
    results = task.setdefault("_results", [])
    for q in task["task_config"].get("questions", []):
        _check_running(task)
        try:
            for attempt in range(max(0, min(int(task.get("max_retries") or 0), 3)) + 1):
                response = _wait(task, lambda: _run_async(analyze_question(
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
    from services.dataviz.services import report_service, report_access
    flattened = [item for result in results for item in result.get("_analysis_results", [result])]
    sources = []
    for result in flattened:
        if result.get("status") != "success":
            continue
        security = result.get("_security_context")
        if not security:
            raise PermissionError("报告缺少来源权限快照")
        report_service._verify_snapshot(task["_identity"], security)
        for source in security["sources"]:
            if source not in sources:
                sources.append(source)
    task["_security_context"] = report_access.policy_snapshot(task["owner_id"], task["workspace_id"], sources)
    return report_service.render_fact_report(task.get("name", "分析报告"), flattened), "markdown"


def _send_notification(task: dict, results: list, report_content: str = None,
                       report_format: str = "markdown", report_link: str = None) -> str:
    """Send notification with report link and summary.

    Uses channel's message_template if configured, otherwise uses default format.
    """
    from services.dataflow.tasks.notification import notification_sender
    from services.dataflow.services.scheduled_task_service import scheduled_task_service

    channel_id = task.get("channel_id")
    if not channel_id:
        return "skipped"

    channel = scheduled_task_service.get_channel(channel_id)
    if not channel or not channel.get("is_active"):
        return "skipped"
    if int(channel.get("workspace_id") or 0) != int(task.get("workspace_id") or 0):
        return "failed"
    # 通知只发送执行状态和需登录的报告链接，不含业务标题、正文、数据行或 token。
    status = results[0].get("status", "failed") if results else "failed"
    content = f"定时分析任务已结束，状态：{status}。请登录平台查看。"
    if report_link:
        content += f"\n{report_link}"

    try:
        notification_sender.send(channel_id, content)
        scheduled_task_service.update_channel_test_status(channel_id, "success")
        return "sent"
    except Exception as e:
        logger.error("[Executor] Notification failed: %s", e)
        scheduled_task_service.update_channel_test_status(channel_id, "failed")
        return "failed"


def _save_report_and_get_link(task: dict, log_id: int, report_content: str,
                               report_format: str) -> str:
    """Save report to DB and return a viewable link."""
    from services.dataflow.services.scheduled_task_service import scheduled_task_service
    import os

    task_name = task.get("name", "报告")
    task_id = task.get("id", 0)
    workspace_id = task.get("workspace_id", 0)
    owner_id = task.get("owner_id", 0)
    access_mode = "private"  # Default to private

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
        generation_status="degraded",
        evidence_summary={"validation_status": "unverified", "quality_status": "unknown", "completeness": "sample_only"},
    )

    # Build link
    base_url = os.getenv("ADH_BASE_URL", "http://localhost:3000")
    report_id = report["id"]
    return f"{base_url}/report/{report_id}"


@app.task(
    name="services.dataflow.tasks.executor.execute_scheduled_task",
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
    from services.shared.common.auth import resolve_execution_owner
    return resolve_execution_owner(task.get("owner_id"), task.get("workspace_id") or 0)


def _check_datasource(task, identity):
    from services.authservice.services.role_service import role_service
    ds_id = int(task["task_config"].get("datasource_id") or 0)
    if ds_id <= 0:
        raise PermissionError("任务必须明确绑定数据源")
    # 纯角色裁决: 数据源可用集=执行身份(任务创建者)的角色授权; 空授权 fail-closed。
    allowed = set(role_service.get_user_allowed_datasources(
        identity["user_id"], identity["workspace_id"]))
    if ds_id not in allowed:
        raise PermissionError("执行身份的角色未授权该数据源")


def _check_running(task):
    if task.get("_stop_event") and task["_stop_event"].is_set():
        raise _Stopped("cancelled")
    from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
    log = service.get_log(task["_log_id"])
    if not log or log["status"] != "running":
        raise _Stopped((log or {}).get("status", "cancelled"))
    if time.monotonic() >= task["_deadline"]:
        raise _Stopped("timeout")


def _execute_core(task_id, trigger_type, run_key=None, worker_id="sync-process"):
    from uuid import uuid4
    from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
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
        from services.datamind.execution.scheduled_analysis import validate_task_as_bot
        validate_task_as_bot(task)
        _check_datasource(task, task["_identity"])
        _check_running(task)
        config = task["task_config"]
        if task["task_type"] not in ("query", "agent"):
            raise ValueError("任务类型无效")
        if not config.get("questions"):
            raise ValueError("任务没有可执行的问题")
        agent_mode = task["task_type"] == "agent"
        results = _execute_agent_mode(task) if agent_mode else _execute_sql_mode(task)
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


@app.task(name="services.dataflow.tasks.executor.generate_report_task", queue="scheduled", acks_late=True)
def generate_report_task(report_id: int):
    from services.dataviz.services.report_service import run_report
    return run_report(report_id)


@app.task(name="services.dataflow.tasks.executor.reconcile_runs", queue="scheduled")
def reconcile_runs():
    """内置任务：运行对账与卡死清理（可经任务监控页暂停，运行结果落 adh_system_jobs）。"""
    from services.shared.common import system_jobs
    if not system_jobs.is_job_active("runs_reconcile"):
        logger.info("[Reconcile] 内置任务已人工暂停，本轮对账跳过")
        return {"skipped": "paused"}
    from services.dataflow.services.scheduled_task_service import scheduled_task_service
    from services.dataviz.services.report_service import cleanup_stale_reports
    try:
        result = {"tasks": scheduled_task_service.cleanup_stale_running_logs(),
                  "reports": cleanup_stale_reports()}
        system_jobs.record_run("runs_reconcile", "success",
                               f"清理卡死执行实例 {result['tasks']} 个、报表生成 {result['reports']} 个")
        return result
    except Exception as exc:  # noqa: BLE001 — 记录后显式抛出，不把失败掩成成功
        logger.exception("[Reconcile] 运行对账失败")
        system_jobs.record_run("runs_reconcile", "failed",
                               f"对账失败（{type(exc).__name__}），详见服务端日志")
        raise


def execute_scheduled_task_sync(task_id: int, trigger_type: str = "manual", run_key: str = None):
    """显式同步适配器，与 Celery/后台使用相同执行核心。"""
    return _execute_core(task_id, trigger_type, run_key, "sync-process")


async def execute_scheduled_task_async(task_id: int, trigger_type: str = "manual", run_key: str = None):
    """本地后台适配器；线程传播上下文，不阻塞 FastAPI 事件循环。"""
    import asyncio
    return await asyncio.to_thread(_execute_core, task_id, trigger_type, run_key, "async-bg")
