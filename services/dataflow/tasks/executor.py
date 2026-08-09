"""Task Executor — Celery tasks for scheduled SQL and Agent execution.

Two execution modes:
- query: Direct SQL execution on datasource, returns raw results
- agent: Calls agent_generate() pipeline, LLM autonomously plans and executes

Each task creates an execution log, runs the queries, optionally generates
a report from a template, and sends notifications to the configured channel.
"""

import json
import logging
import time

from services.dataflow.tasks.celery_app import app

logger = logging.getLogger(__name__)

# Cancellation registry: log_id -> True means "cancel this task"
_cancelled_tasks: set = set()


def cancel_running_task(log_id: int):
    """Mark a running task as cancelled. The executor checks this flag periodically."""
    _cancelled_tasks.add(log_id)


def is_task_cancelled(log_id: int) -> bool:
    """Check if a task has been marked for cancellation."""
    return log_id in _cancelled_tasks


def clear_cancel_flag(log_id: int):
    """Remove cancel flag after task finishes."""
    _cancelled_tasks.discard(log_id)


def _now_iso():
    from datetime import datetime
    return datetime.now().isoformat()


def _execute_sql_on_datasource(sql: str, datasource_id: int) -> dict:
    """Execute a SQL statement on the specified datasource. Reuses dashboard executor."""
    from services.dataviz.services.dashboard_service import _execute_on_datasource
    return _execute_on_datasource(sql, datasource_id)


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
    results = []

    for q in config.get("questions", []):
        title = q.get("title", "Untitled")
        sql = q.get("sql", "")
        if not sql:
            results.append({"title": title, "status": "failed", "error": "Empty SQL"})
            continue
        try:
            sql_clean = sql.strip().rstrip(";")
            if "limit" not in sql_clean.lower():
                sql_clean += " LIMIT 1000"
            result = _execute_sql_on_datasource(sql_clean, datasource_id)
            results.append({
                "title": title,
                "status": "success",
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "row_count": result.get("row_count", 0),
            })
            # 执行成功后自动采集血缘（用原始 SQL，不带 LIMIT 后缀）
            _collect_lineage(sql.strip().rstrip(";"), datasource_id, workspace_id, title)
        except Exception as e:
            logger.warning("[Executor] SQL failed for '%s': %s", title, e)
            results.append({"title": title, "status": "failed", "error": str(e)})

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
            return pool.submit(asyncio.run, _collect()).result()
    else:
        return asyncio.run(_collect())


def _execute_agent_mode(task: dict) -> list:
    """Agent mode execution — always uses orchestrator (multi-agent).

    agent_name in task_config acts as a whitelist to limit available sub-agents.
    - No agent_name → allowed_agent_names = [] → no sub-agents available
    - Has agent_name → allowed_agent_names = [agent_name] → only that sub-agent
    """
    return _execute_orchestrator(task, task["task_config"].get("context", ""))


def _execute_orchestrator(task: dict, context: str) -> list:
    """Execute using orchestrator (agent_generate, multi-agent routing).

    Applies resource constraints from task_config:
    - datasource_id: which datasource agents can use
    - mcp_server_ids: which MCP servers are allowed (None=all, []=none)
    - agent_name: which sub-agent is allowed (None=all, ""=none)
    """
    from services.datamind.nl2sql.orchestrator.agent_pipeline import agent_generate

    config = task["task_config"]
    datasource_id = config.get("datasource_id", 0)
    max_iterations = config.get("max_iterations")

    # Support both single value and array for MCP servers
    mcp_server_ids = config.get("mcp_server_ids") or []
    if not mcp_server_ids and config.get("mcp_server_id"):
        mcp_server_ids = [config["mcp_server_id"]]
    allowed_mcp_server_ids = mcp_server_ids

    # Support both single value and array for agent names
    agent_names_list = config.get("agent_names") or []
    if not agent_names_list and config.get("agent_name"):
        agent_names_list = [config["agent_name"]]
    allowed_agent_names = agent_names_list

    results = []

    for q in config.get("questions", []):
        title = q.get("title", "Untitled")
        question = q.get("question", "")
        if not question:
            results.append({"title": title, "status": "failed", "error": "Empty question"})
            continue
        try:
            response = _run_async(
                agent_generate(
                    question=question,
                    datasource_id=datasource_id,
                    user_id=0,
                    username="scheduled_task",
                    disable_ask_user=True,
                    context=context or None,
                    allowed_mcp_server_ids=allowed_mcp_server_ids,
                    allowed_agent_names=allowed_agent_names,
                    max_iterations=max_iterations,
                )
            )
            # Extract reply text from response dict
            if isinstance(response, dict):
                reply = response.get("reply", "")
                raw_result = response.get("result")
                row_count = 0
                if isinstance(raw_result, dict):
                    row_count = len(raw_result.get("rows", []))
                results.append({
                    "title": title, "status": "success", "response": reply,
                    "result": raw_result, "row_count": row_count, "sql": response.get("sql"),
                })
            else:
                results.append({
                    "title": title, "status": "success",
                    "response": str(response) if response else "",
                })
        except Exception as e:
            logger.warning("[Executor] Orchestrator failed for '%s': %s", title, e)
            results.append({"title": title, "status": "failed", "error": str(e)})

    return results


def _execute_mcp_mode(task: dict) -> list:
    """MCP mode: use agent_generate with MCP server context.

    The MCP server's tools are available to the agent during execution.
    Questions are treated as natural language queries.
    """
    from services.datamind.nl2sql.orchestrator.agent_pipeline import agent_generate

    config = task["task_config"]
    workspace_id = task.get("workspace_id", 0)
    context = config.get("context", "")
    max_iterations = config.get("max_iterations")
    results = []

    # Support both single value and array for MCP servers
    mcp_server_ids = config.get("mcp_server_ids") or []
    if not mcp_server_ids and config.get("mcp_server_id"):
        mcp_server_ids = [config["mcp_server_id"]]
    allowed_mcp_server_ids = mcp_server_ids

    for q in config.get("questions", []):
        title = q.get("title", "Untitled")
        question = q.get("question") or q.get("sql", "")
        if not question:
            results.append({"title": title, "status": "failed", "error": "Empty question"})
            continue
        try:
            response = _run_async(
                agent_generate(
                    question=question,
                    workspace_id=workspace_id,
                    user_id=0,
                    username="scheduled_task",
                    disable_ask_user=True,
                    context=context or None,
                    allowed_mcp_server_ids=allowed_mcp_server_ids,
                    allowed_agent_names=[],  # No sub-agents in MCP mode
                    max_iterations=max_iterations,
                )
            )
            if isinstance(response, dict):
                reply = response.get("reply", "")
                results.append({"title": title, "status": "success", "response": reply})
            else:
                results.append({"title": title, "status": "success", "response": str(response) if response else ""})
        except Exception as e:
            logger.warning("[Executor] MCP mode failed for '%s': %s", title, e)
            results.append({"title": title, "status": "failed", "error": str(e)})

    return results


def _generate_report(task: dict, results: list) -> tuple[str, str]:
    """Generate a report using LLM + template style reference.

    Loads the template as a style guide, then uses LLM to generate a complete
    report with analysis insights instead of simple placeholder substitution.

    Returns (content, format) tuple. format is 'markdown' or 'html'.
    """
    template_id = task.get("report_template_key", "")
    if not template_id:
        return "", "markdown"

    # Load template as style reference
    template_text = ""
    template_format = "markdown"
    try:
        tpl_id = int(template_id)
        from services.dataflow.services.scheduled_task_service import scheduled_task_service
        tpl = scheduled_task_service.get_template(tpl_id)
        if tpl:
            template_text = tpl["content"]
            template_format = tpl.get("format", "markdown")
    except (ValueError, TypeError):
        from pathlib import Path
        template_path = Path(__file__).resolve().parent.parent.parent.parent / "backend" / "config" / "templates" / f"{template_id}.md"
        if template_path.exists():
            template_text = template_path.read_text(encoding="utf-8")

    if not template_text:
        logger.warning("[Executor] Report template not found: %s", template_id)
        return "", "markdown"

    # Format results for the prompt
    now = _now_iso()
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]

    results_text = []
    for r in results:
        section = f"### {r['title']} ({'成功' if r['status'] == 'success' else '失败'})\n"
        if r["status"] == "success":
            if r.get("response"):
                section += f"Agent 分析结果：\n{r['response']}\n\n"
            if r.get("sql"):
                section += f"执行的 SQL：\n```sql\n{r['sql']}\n```\n\n"
            if r.get("columns") and r.get("rows"):
                section += f"查询结果（{r.get('row_count', len(r['rows']))} 行）：\n"
                section += "| " + " | ".join(str(c) for c in r["columns"]) + " |\n"
                section += "| " + " | ".join(["---"] * len(r["columns"])) + " |\n"
                for row in r["rows"][:20]:  # Limit to 20 rows for prompt
                    section += "| " + " | ".join(str(v) for v in row) + " |\n"
                if len(r.get("rows", [])) > 20:
                    section += f"\n*（共 {len(r['rows'])} 行，仅显示前 20 行）*\n"
            elif r.get("row_count"):
                section += f"数据量：{r['row_count']} 行\n"
        else:
            section += f"错误：{r.get('error', '未知错误')}\n"
        results_text.append(section)

    # Load report agent system prompt
    from services.datamind.config.agent_loader import load_agent_prompt
    agent_prompt = load_agent_prompt("report") or ""

    # Build LLM prompt
    system_msg = agent_prompt if agent_prompt else "你是数据分析报告撰写专家。请根据报告样式模板和执行结果生成完整的分析报告。"
    user_msg = f"""请根据以下报告样式模板和任务执行结果，生成一份完整的数据分析报告。

# 报告样式模板
{template_text}

# 任务信息
- 任务名称：{task.get('name', '')}
- 执行时间：{now[:10]}
- 成功：{len(succeeded)} 项
- 失败：{len(failed)} 项

# 执行结果明细
{chr(10).join(results_text)}

请严格按照上述报告样式模板的格式和风格，基于执行结果生成完整的分析报告。
要求：
1. 保持模板的标题层级、排版风格和分析角度
2. 基于数据给出分析洞察，不要简单罗列原始数据
3. 发现异常数据时给出可能的原因分析
4. 有趋势数据时给出趋势判断
5. 输出格式为 {template_format}，使用中文撰写
"""

    # Call LLM to generate report
    try:
        from services.shared.common.llm.llm_client import generate_sql
        result = generate_sql(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4096,
        )
        report_content = result.get("sql", "")  # generate_sql returns text in 'sql' field
        if report_content:
            return report_content, template_format
        else:
            logger.warning("[Executor] LLM returned empty report, falling back to template")
            return _generate_report_fallback(template_text, template_format, results, now, task)
    except Exception as e:
        logger.error("[Executor] LLM report generation failed: %s, falling back to template", e)
        return _generate_report_fallback(template_text, template_format, results, now, task)


def _generate_report_fallback(template_text: str, template_format: str,
                               results: list, now: str, task: dict) -> tuple[str, str]:
    """Fallback: generate report using Jinja2 template (original behavior)."""
    from jinja2 import Template
    try:
        tpl = Template(template_text)
        succeeded = [r for r in results if r["status"] == "success"]
        failed = [r for r in results if r["status"] == "failed"]
        content = tpl.render(
            date=now[:10], timestamp=now, results=results,
            succeeded=succeeded, failed=failed,
            task_name=task.get("name", ""),
        )
        return content, template_format
    except Exception as e:
        logger.error("[Executor] Fallback template render failed: %s", e)
        return "", "markdown"


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
    if not channel:
        return "skipped"

    # Build result summary
    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    total = len(results)

    result_lines = []
    for r in results:
        icon = "✅" if r["status"] == "success" else "❌"
        result_lines.append(f"{icon} {r['title']}")
    result_summary = "\n".join(result_lines)

    # Template variables
    from datetime import datetime
    now = datetime.now()
    template_vars = {
        "task_name": task.get("name", ""),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "total": str(total),
        "succeeded": str(succeeded),
        "failed": str(failed),
        "result_summary": result_summary,
        "report_link": report_link or "",
        "status": "✅ 全部成功" if failed == 0 else f"⚠️ {failed} 项失败",
    }

    # Use channel's custom template or default
    channel_config = channel.get("config", {})
    message_template = channel_config.get("message_template", "")

    if message_template:
        content = message_template
        for key, val in template_vars.items():
            content = content.replace(f"{{{{{key}}}}}", val)
    else:
        # Default format
        content = (
            f"📊 {template_vars['task_name']}\n"
            f"📅 {template_vars['date']} {template_vars['time']}\n"
            f"状态: {template_vars['status']}\n\n"
            f"{result_summary}"
        )
        if report_link:
            content += f"\n\n🔗 查看完整报告: {report_link}"

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
    )

    # Build link
    base_url = os.getenv("ADH_BASE_URL", "http://localhost:3000")
    report_id = report["id"]
    if access_mode == "private" and report.get("access_token"):
        return f"{base_url}/report/{report_id}?token={report['access_token']}"
    else:
        return f"{base_url}/report/{report_id}"


@app.task(
    name="services.dataflow.tasks.executor.execute_scheduled_task",
    bind=True,
    queue="scheduled",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_scheduled_task(self, task_id: int, trigger_type: str = "cron"):
    """Main Celery task: execute a scheduled task end-to-end.

    Flow:
    1. Load task config from DB
    2. Create execution log (status=running)
    3. Execute SQL or Agent mode
    4. Generate report (if template configured)
    5. Send notification (if channel configured)
    6. Update log and task status

    Retry: if max_retries > 0 and the task fails, Celery will retry automatically.
    """
    from services.dataflow.services.scheduled_task_service import scheduled_task_service

    task = scheduled_task_service.get_task(task_id)
    if not task:
        logger.warning("[Executor] Task %s not found, skipping", task_id)
        return {"skipped": True, "reason": "task not found"}

    if not task.get("is_active"):
        logger.info("[Executor] Task %s is inactive, skipping", task_id)
        return {"skipped": True, "reason": "task inactive"}

    workspace_id = task.get("workspace_id", 0)
    log_id = scheduled_task_service.create_log(
        task_id, trigger_type, "running",
        celery_task_id=self.request.id,
        workspace_id=workspace_id,
    )

    t_start = time.time()
    worker_id = self.request.hostname or "unknown"

    try:
        # Execute based on task_type + execution source
        task_type = task["task_type"]
        task_config = task.get("task_config", {})

        if task_config.get("mcp_server_id"):
            results = _execute_mcp_mode(task)
        elif task_config.get("agent_name") or task_type == "agent":
            results = _execute_agent_mode(task)
        elif task_type == "query":
            results = _execute_sql_mode(task)
        else:
            raise ValueError(f"Unknown task_type: {task_type}")

        elapsed_ms = int((time.time() - t_start) * 1000)

        # Count succeeded/failed
        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "failed")

        # Build result summary
        summary_parts = []
        for r in results:
            if r["status"] == "success":
                count = r.get("row_count", 0)
                summary_parts.append(f"{r['title']}: {count} rows")
            else:
                summary_parts.append(f"{r['title']}: FAILED")
        result_summary = "; ".join(summary_parts)

        # Generate report
        report_content, report_format = _generate_report(task, results)

        # Save report and get link
        report_link = None
        if report_content:
            try:
                report_link = _save_report_and_get_link(task, log_id, report_content, report_format)
            except Exception as e:
                logger.warning("[Executor] Failed to save report: %s", e)

        # Send notification
        notify_on_success = task.get("notify_on_success", True)
        notify_status = "skipped"
        if notify_on_success:
            notify_status = _send_notification(task, results, report_content, report_format, report_link)

        # Update log
        scheduled_task_service.update_log(
            log_id,
            status="success",
            result_summary=result_summary,
            result_data=results,
            questions_executed=[q.get("sql") or q.get("question", "") for q in task["task_config"].get("questions", [])],
            questions_succeeded=succeeded,
            questions_failed=failed,
            report_content=report_content,
            notify_status=notify_status,
            elapsed_ms=elapsed_ms,
            worker_id=worker_id,
            finished_at=_now_iso(),
        )

        scheduled_task_service.update_task_status(task_id, "success")
        logger.info("[Executor] Task %s completed: %d/%d succeeded, %dms", task_id, succeeded, len(results), elapsed_ms)
        return {"success": True, "log_id": log_id, "succeeded": succeeded, "failed": failed, "elapsed_ms": elapsed_ms}

    except Exception as e:
        elapsed_ms = int((time.time() - t_start) * 1000)
        error_msg = str(e)
        logger.error("[Executor] Task %s failed: %s", task_id, error_msg)

        scheduled_task_service.update_log(
            log_id, status="failed", error_message=error_msg,
            elapsed_ms=elapsed_ms, worker_id=worker_id, finished_at=_now_iso(),
        )
        scheduled_task_service.update_task_status(task_id, "failed", error=error_msg)

        if task.get("notify_on_failure", True):
            try:
                _send_notification(task, [{"title": "执行失败", "status": "failed", "error": error_msg}])
            except Exception:
                pass

        max_retries = task.get("max_retries", 0)
        if self.request.retries < max_retries:
            raise self.retry(exc=e, countdown=60)
        raise


def execute_scheduled_task_sync(task_id: int, trigger_type: str = "manual"):
    """Synchronous execution fallback when Celery/Redis is unavailable.

    Same logic as the Celery task but runs directly in the current process.
    Used by the manual trigger endpoint as a fallback.
    """
    from services.dataflow.services.scheduled_task_service import scheduled_task_service

    task = scheduled_task_service.get_task(task_id)
    if not task:
        logger.warning("[Executor:sync] Task %s not found, skipping", task_id)
        return {"skipped": True, "reason": "task not found"}

    workspace_id = task.get("workspace_id", 0)
    log_id = scheduled_task_service.create_log(
        task_id, trigger_type, "running",
        celery_task_id=None,
        workspace_id=workspace_id,
    )

    t_start = time.time()
    worker_id = "sync-process"

    try:
        task_type = task["task_type"]
        task_config = task.get("task_config", {})

        if task_config.get("mcp_server_id"):
            results = _execute_mcp_mode(task)
        elif task_config.get("agent_name") or task_type == "agent":
            results = _execute_agent_mode(task)
        elif task_type == "query":
            results = _execute_sql_mode(task)
        else:
            raise ValueError(f"Unknown task_type: {task_type}")

        elapsed_ms = int((time.time() - t_start) * 1000)

        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "failed")

        summary_parts = []
        for r in results:
            if r["status"] == "success":
                count = r.get("row_count", 0)
                summary_parts.append(f"{r['title']}: {count} rows")
            else:
                summary_parts.append(f"{r['title']}: FAILED")
        result_summary = "; ".join(summary_parts)

        report_content, report_format = _generate_report(task, results)

        report_link = None
        if report_content:
            try:
                report_link = _save_report_and_get_link(task, log_id, report_content, report_format)
            except Exception as e:
                logger.warning("[Executor:sync] Failed to save report: %s", e)

        notify_on_success = task.get("notify_on_success", True)
        notify_status = "skipped"
        if notify_on_success:
            notify_status = _send_notification(task, results, report_content, report_format, report_link)

        scheduled_task_service.update_log(
            log_id,
            status="success",
            result_summary=result_summary,
            result_data=results,
            questions_executed=[q.get("sql") or q.get("question", "") for q in task["task_config"].get("questions", [])],
            questions_succeeded=succeeded,
            questions_failed=failed,
            report_content=report_content,
            notify_status=notify_status,
            elapsed_ms=elapsed_ms,
            worker_id=worker_id,
            finished_at=_now_iso(),
        )
        scheduled_task_service.update_task_status(task_id, "success")

        logger.info("[Executor:sync] Task %s completed: %d/%d succeeded, %dms", task_id, succeeded, len(results), elapsed_ms)
        return {"success": True, "log_id": log_id, "succeeded": succeeded, "failed": failed, "elapsed_ms": elapsed_ms}

    except Exception as e:
        elapsed_ms = int((time.time() - t_start) * 1000)
        error_msg = str(e)
        logger.error("[Executor:sync] Task %s failed: %s", task_id, error_msg)

        scheduled_task_service.update_log(
            log_id,
            status="failed",
            error_message=error_msg,
            elapsed_ms=elapsed_ms,
            worker_id=worker_id,
            finished_at=_now_iso(),
        )
        scheduled_task_service.update_task_status(task_id, "failed", error=error_msg)

        notify_on_failure = task.get("notify_on_failure", True)
        if notify_on_failure:
            try:
                _send_notification(task, [{"title": "执行失败", "status": "failed", "error": error_msg}])
            except Exception:
                pass

        raise


async def execute_scheduled_task_async(task_id: int, trigger_type: str = "manual"):
    """Async execution for FastAPI BackgroundTasks.

    Runs in the main event loop, so async generators (agent_generate) work
    correctly without threading issues.
    """
    from services.dataflow.services.scheduled_task_service import scheduled_task_service
    from services.datamind.nl2sql.orchestrator.agent_pipeline import agent_generate

    task = scheduled_task_service.get_task(task_id)
    if not task:
        logger.warning("[Executor:async] Task %s not found, skipping", task_id)
        return

    workspace_id = task.get("workspace_id", 0)
    log_id = scheduled_task_service.create_log(
        task_id, trigger_type, "running",
        celery_task_id=None,
        workspace_id=workspace_id,
    )

    t_start = time.time()
    worker_id = "async-bg"

    try:
        task_type = task["task_type"]
        task_config = task.get("task_config", {})
        results = []

        if task_config.get("mcp_server_id") or task_config.get("mcp_server_ids") or task_config.get("agent_name") or task_config.get("agent_names") or task_type == "agent":
            # Always use orchestrator (multi-agent mode)
            context = task_config.get("context", "")
            max_iterations = task_config.get("max_iterations")

            # Support both single value and array for MCP servers
            mcp_server_ids = task_config.get("mcp_server_ids") or []
            if not mcp_server_ids and task_config.get("mcp_server_id"):
                mcp_server_ids = [task_config["mcp_server_id"]]
            allowed_mcp_server_ids = mcp_server_ids

            # Support both single value and array for agent names
            agent_names_list = task_config.get("agent_names") or []
            if not agent_names_list and task_config.get("agent_name"):
                agent_names_list = [task_config["agent_name"]]
            allowed_agent_names = agent_names_list

            for q in task_config.get("questions", []):
                title = q.get("title", "Untitled")
                question = q.get("question") or q.get("sql", "")
                if not question:
                    results.append({"title": title, "status": "failed", "error": "Empty question"})
                    continue
                try:
                    final_data = None
                    async for event_type, data in agent_generate(
                        question=question,
                        datasource_id=task_config.get("datasource_id", 0),
                        workspace_id=workspace_id,
                        user_id=0,
                        username="scheduled_task",
                        disable_ask_user=True,
                        context=context or None,
                        allowed_mcp_server_ids=allowed_mcp_server_ids,
                        allowed_agent_names=allowed_agent_names,
                        max_iterations=max_iterations,
                    ):
                        if event_type == "done":
                            final_data = data
                    if isinstance(final_data, dict):
                        reply = final_data.get("reply", "")
                        raw_result = final_data.get("result")
                        row_count = 0
                        if isinstance(raw_result, dict):
                            row_count = len(raw_result.get("rows", []))
                        elif isinstance(raw_result, list):
                            row_count = len(raw_result)
                        results.append({
                            "title": title, "status": "success", "response": reply,
                            "result": raw_result, "row_count": row_count, "sql": final_data.get("sql"),
                        })
                    else:
                        results.append({"title": title, "status": "success", "response": str(final_data) if final_data else ""})
                except Exception as e:
                    logger.warning("[Executor:async] Agent failed for '%s': %s", title, e)
                    results.append({"title": title, "status": "failed", "error": str(e)})
        elif task_type == "query":
            results = _execute_sql_mode(task)
        else:
            raise ValueError(f"Unknown task_type: {task_type}")

        elapsed_ms = int((time.time() - t_start) * 1000)
        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "failed")

        summary_parts = []
        for r in results:
            if r["status"] == "success":
                count = r.get("row_count", 0)
                summary_parts.append(f"{r['title']}: {count} rows")
            else:
                summary_parts.append(f"{r['title']}: FAILED")
        result_summary = "; ".join(summary_parts)

        report_content, report_format = _generate_report(task, results)

        report_link = None
        if report_content:
            try:
                report_link = _save_report_and_get_link(task, log_id, report_content, report_format)
            except Exception as e:
                logger.warning("[Executor:async] Failed to save report: %s", e)

        notify_status = "skipped"
        if task.get("notify_on_success", True):
            notify_status = _send_notification(task, results, report_content, report_format, report_link)

        scheduled_task_service.update_log(
            log_id, status="success",
            result_summary=result_summary, result_data=results,
            questions_executed=[q.get("sql") or q.get("question", "") for q in task_config.get("questions", [])],
            questions_succeeded=succeeded, questions_failed=failed,
            report_content=report_content, notify_status=notify_status,
            elapsed_ms=elapsed_ms, worker_id=worker_id, finished_at=_now_iso(),
        )
        scheduled_task_service.update_task_status(task_id, "success")
        logger.info("[Executor:async] Task %s done: %d/%d ok, %dms", task_id, succeeded, len(results), elapsed_ms)

    except Exception as e:
        elapsed_ms = int((time.time() - t_start) * 1000)
        error_msg = str(e)
        logger.error("[Executor:async] Task %s failed: %s", task_id, error_msg)
        scheduled_task_service.update_log(
            log_id, status="failed", error_message=error_msg,
            elapsed_ms=elapsed_ms, worker_id=worker_id, finished_at=_now_iso(),
        )
        scheduled_task_service.update_task_status(task_id, "failed", error=error_msg)
        if task.get("notify_on_failure", True):
            try:
                _send_notification(task, [{"title": "执行失败", "status": "failed", "error": error_msg}])
            except Exception:
                pass
