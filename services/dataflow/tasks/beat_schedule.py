"""Dynamic Beat Schedule — loads active tasks from DB into Celery Beat.

This module is called by the Beat scheduler to build the periodic task
schedule from the adh_scheduled_tasks table. It supports hot-reload:
Beat re-reads the schedule periodically without restarting.

Usage:
    # In celery_app.py or a separate beat config:
    app.conf.beat_schedule = {}
    # Beat will call get_beat_schedule() on each tick via a custom scheduler.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from celery.schedules import crontab as CeleryCrontab, schedstate

logger = logging.getLogger(__name__)


def parse_cron_expression(expr: str) -> dict:
    """Parse a 5-field cron expression into celery.schedules.crontab kwargs.

    Standard cron: minute hour day_of_month month day_of_week
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (expected 5 fields): {expr}")

    values = {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month_of_year": parts[3],
        "day_of_week": parts[4],
    }
    CeleryCrontab(**values)
    return values


class WallClockCrontab(CeleryCrontab):
    """按任务时区的实际分钟匹配；DST 跳时不补跑，回拨同一墙钟分钟仅执行一次。"""
    def __init__(self, expression, timezone_name="Asia/Shanghai", **kwargs):
        self.expression = expression
        self.timezone_name = timezone_name
        self.local_zone = ZoneInfo(timezone_name)
        super().__init__(**parse_cron_expression(expression), **kwargs)

    def is_due(self, last_run_at):
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        local = now.astimezone(self.local_zone)
        last = last_run_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        matches = (local.minute in self.minute and local.hour in self.hour
                   and local.day in self.day_of_month and local.month in self.month_of_year
                   and (local.weekday() + 1) % 7 in self.day_of_week)
        due = matches and last.timestamp() < now.replace(second=0, microsecond=0).timestamp()
        return schedstate(due, max(0.1, 60 - local.second - local.microsecond / 1000000))

    def run_key(self, task_id, now=None):
        local = (now or datetime.now(timezone.utc)).astimezone(self.local_zone)
        return f"cron:{task_id}:{self.timezone_name}:{local:%Y%m%d%H%M}"

    def __eq__(self, other):
        return (isinstance(other, WallClockCrontab) and self.expression == other.expression
                and self.timezone_name == other.timezone_name)


def build_beat_schedule() -> dict:
    """Load active scheduled tasks from DB and build Celery Beat schedule dict.

    覆盖三类周期任务：系统对账、定时分析任务（adh_scheduled_tasks）、
    DAG 工作流（adh_dag_workflows）、同步任务（adh_sync_tasks）。
    Returns a dict suitable for app.conf.beat_schedule.
    Called on Beat startup and periodically for hot-reload.
    """
    from services.dataflow.services.scheduled_task_service import scheduled_task_service
    from services.dataflow.dag.dag_service import dag_service
    from services.shared.common.db import execute_query

    schedule = {"reconcile_runs": {
        "task": "services.dataflow.tasks.executor.reconcile_runs", "schedule": 60.0,
        "options": {"queue": "scheduled"},
    }}
    try:
        tasks = scheduled_task_service.list_active_tasks()
        for task in tasks:
            if int(task.get("owner_id") or 0) <= 0:
                continue
            task_id = task["id"]
            try:
                cron = WallClockCrontab(task["cron_expression"], task.get("timezone") or "Asia/Shanghai")
            except (ValueError, KeyError) as e:
                logger.warning("[Beat] Skipping task %s: %s", task_id, e)
                continue

            entry_key = f"scheduled_task_{task_id}"
            schedule[entry_key] = {
                "task": "services.dataflow.tasks.executor.execute_scheduled_task",
                "schedule": cron,
                "args": (task_id,),
                "options": {"queue": "scheduled"},
                "kwargs": {"trigger_type": "cron"},
            }
        # DAG 工作流（active + owner 已认领）
        for wf in dag_service.list_active_scheduled():
            wf_id = wf["id"]
            try:
                cron = WallClockCrontab(wf["cron_expression"], wf.get("timezone") or "Asia/Shanghai")
            except (ValueError, KeyError) as e:
                logger.warning("[Beat] Skipping workflow %s: %s", wf_id, e)
                continue
            schedule[f"dag_workflow_{wf_id}"] = {
                "task": "services.dataflow.tasks.dag_tasks.execute_dag_run",
                "schedule": cron,
                "args": (wf_id,),
                "options": {"queue": "scheduled"},
            }
        # 同步任务（schedule_cron 非空 + active + owner 已认领）
        sync_tasks = execute_query(
            "SELECT id, schedule_cron, timezone FROM adh_sync_tasks "
            "WHERE is_active = 1 AND schedule_cron IS NOT NULL AND schedule_cron != '' "
            " AND owner_id > 0") or []
        for task in sync_tasks:
            task_id = task["id"]
            try:
                cron = WallClockCrontab(task["schedule_cron"], task.get("timezone") or "Asia/Shanghai")
            except (ValueError, KeyError) as e:
                logger.warning("[Beat] Skipping sync task %s: %s", task_id, e)
                continue
            schedule[f"sync_task_{task_id}"] = {
                "task": "services.dataflow.tasks.dag_tasks.execute_sync_task",
                "schedule": cron,
                "args": (task_id,),
                "options": {"queue": "scheduled"},
            }
        logger.info("[Beat] Loaded %d active scheduled tasks", len(schedule))
    except Exception as e:
        logger.error("[Beat] Failed to load schedules from DB: %s", e)

    return schedule


from celery.beat import Scheduler


class DatabaseScheduler(Scheduler):
    """加载现有任务表；派发前保存运行键，刷新配置不重置调度水位。"""
    def setup_schedule(self):
        super().setup_schedule()
        self.merge_inplace(build_beat_schedule())
        self._last_refresh = 0.0
        from services.dataflow.tasks.celery_app import BeatLease
        self._lease = BeatLease()

    def tick(self, *args, **kwargs):
        import time
        if not self._lease.renew():
            return 1.0
        if time.monotonic() - self._last_refresh >= 30:
            self.merge_inplace(build_beat_schedule())
            self._last_refresh = time.monotonic()
        try:
            return min(super().tick(*args, **kwargs), 3.0)
        except Exception:
            logger.exception("Beat 派发未完成，保留队列记录供对账")
            return 3.0

    def close(self):
        if getattr(self, "_lease", None):
            self._lease.close()
        super().close()

    def apply_async(self, entry, producer=None, advance=True, **kwargs):
        """派发前落运行记录（run_key 幂等），DISPATCH_FAILED 供对账。"""
        task_name = entry.task
        if task_name == "services.dataflow.tasks.executor.execute_scheduled_task":
            return self._dispatch_scheduled(entry, producer, advance, **kwargs)
        if task_name == "services.dataflow.tasks.dag_tasks.execute_dag_run":
            return self._dispatch_dag(entry, producer, advance, **kwargs)
        if task_name == "services.dataflow.tasks.dag_tasks.execute_sync_task":
            return self._dispatch_sync(entry, producer, advance, **kwargs)
        return super().apply_async(entry, producer, advance, **kwargs)

    def _dispatch_scheduled(self, entry, producer, advance, **kwargs):
        import copy
        from datetime import datetime, timezone
        from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
        task_id = entry.args[0]
        task = service.get_task(task_id)
        if not task or not task.get("is_active") or int(task.get("owner_id") or 0) <= 0:
            if advance:
                self.reserve(entry)
            return None
        cron = WallClockCrontab(task.get("cron_expression") or "* * * * *", task.get("timezone") or "Asia/Shanghai")
        run_key = cron.run_key(task_id)
        log_id = service.create_log(task_id, "cron", "queued", run_key=run_key, celery_task_id=run_key,
                           workspace_id=task.get("workspace_id") or 0)
        dispatched = copy.copy(entry)
        dispatched.kwargs = {**entry.kwargs, "run_key": run_key}
        timeout = max(1, min(int(task.get("timeout_seconds") or 300), 3600))
        dispatched.options = {**entry.options, "task_id": run_key,
                              "soft_time_limit": timeout, "time_limit": timeout + 30}
        try:
            return super().apply_async(dispatched, producer, advance, **kwargs)
        except Exception:
            from services.shared.common.db import execute_write
            execute_write("UPDATE adh_scheduled_logs SET stage_error_code='DISPATCH_FAILED' "
                          "WHERE id=%s AND status='queued'", (log_id,))
            raise

    def _dispatch_dag(self, entry, producer, advance, **kwargs):
        """DAG 派发：run_key 按墙钟分钟生成，重投复用同一运行实例。"""
        import copy
        from datetime import datetime, timezone
        from services.dataflow.dag.dag_service import dag_service
        workflow_id = entry.args[0]
        wf = dag_service.get_workflow(workflow_id)
        if not wf or not wf.get("is_active") or int(wf.get("owner_id") or 0) <= 0:
            if advance:
                self.reserve(entry)
            return None
        cron = WallClockCrontab(wf.get("cron_expression") or "* * * * *",
                                wf.get("timezone") or "Asia/Shanghai")
        local = datetime.now(timezone.utc).astimezone(cron.local_zone)
        run_key = f"dagcron:{workflow_id}:{cron.timezone_name}:{local:%Y%m%d%H%M}"
        run = dag_service.create_run(workflow_id, run_key, "cron",
                                     int(wf.get("workspace_id") or 0))
        if not run or run.get("status") != "queued":
            logger.info("[Beat] workflow %s 运行实例已存在（%s），跳过重复派发",
                        workflow_id, run_key)
            if advance:
                self.reserve(entry)
            return None
        dispatched = copy.copy(entry)
        dispatched.args = (run["id"],)
        timeout = max(60, int(wf.get("timeout_seconds") or 3600))
        dispatched.options = {**entry.options, "task_id": run_key,
                              "soft_time_limit": timeout, "time_limit": timeout + 60}
        try:
            return super().apply_async(dispatched, producer, advance, **kwargs)
        except Exception:
            dag_service.finish_run(run["id"], "failed", "DISPATCH_FAILED", "任务队列暂不可用")
            raise

    def _dispatch_sync(self, entry, producer, advance, **kwargs):
        """同步任务派发：run_key 幂等，重投复用同一执行实例。"""
        import copy
        from datetime import datetime, timezone
        from services.dataflow.dag.dag_service import dag_service
        from services.shared.common.db import execute_query, execute_write
        from services.dataflow.dag.sync_task_runner import _generate_id, _now
        task_id = entry.args[0]
        task = execute_query("SELECT * FROM adh_sync_tasks WHERE id = %s", (task_id,), fetchone=True)
        if not task or not task.get("is_active") or int(task.get("owner_id") or 0) <= 0:
            if advance:
                self.reserve(entry)
            return None
        cron = WallClockCrontab(task.get("schedule_cron") or "* * * * *",
                                task.get("timezone") or "Asia/Shanghai")
        local = datetime.now(timezone.utc).astimezone(cron.local_zone)
        run_key = f"synccron:{task_id}:{cron.timezone_name}:{local:%Y%m%d%H%M}"
        existing = execute_query("SELECT id FROM adh_sync_logs WHERE dag_run_id = %s",
                                 (run_key,), fetchone=True)
        if existing:
            logger.info("[Beat] sync task %s 执行实例已存在（%s），跳过重复派发", task_id, run_key)
            if advance:
                self.reserve(entry)
            return None
        log_id = _generate_id()
        execute_write(
            "INSERT INTO adh_sync_logs (id, sync_task_id, workspace_id, dag_run_id, "
            " status, trigger_type, started_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'queued', 'cron', %s, %s)",
            (log_id, task_id, int(task.get("workspace_id") or 0), run_key, _now(), _now()))
        dispatched = copy.copy(entry)
        dispatched.args = (task_id, run_key)
        timeout = max(60, int(task.get("timeout_seconds") or 1800))
        dispatched.options = {**entry.options, "task_id": run_key,
                              "soft_time_limit": timeout, "time_limit": timeout + 60}
        try:
            return super().apply_async(dispatched, producer, advance, **kwargs)
        except Exception:
            execute_write("UPDATE adh_sync_logs SET status='failed', error_code='DISPATCH_FAILED', "
                          "finished_at=%s WHERE id=%s AND status='queued'", (_now(), log_id))
            raise
