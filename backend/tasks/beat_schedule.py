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
from celery.schedules import crontab as CeleryCrontab

logger = logging.getLogger(__name__)


def parse_cron_expression(expr: str) -> dict:
    """Parse a 5-field cron expression into celery.schedules.crontab kwargs.

    Standard cron: minute hour day_of_month month day_of_week
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (expected 5 fields): {expr}")

    return {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month_of_year": parts[3],
        "day_of_week": parts[4],
    }


def build_beat_schedule() -> dict:
    """Load active scheduled tasks from DB and build Celery Beat schedule dict.

    Returns a dict suitable for app.conf.beat_schedule.
    Called on Beat startup and periodically for hot-reload.
    """
    from backend.services.scheduled_task_service import scheduled_task_service

    schedule = {}
    try:
        tasks = scheduled_task_service.list_active_tasks()
        for task in tasks:
            task_id = task["id"]
            try:
                cron_kwargs = parse_cron_expression(task["cron_expression"])
            except ValueError as e:
                logger.warning("[Beat] Skipping task %s: %s", task_id, e)
                continue

            entry_key = f"scheduled_task_{task_id}"
            schedule[entry_key] = {
                "task": "backend.tasks.executor.execute_scheduled_task",
                "schedule": CeleryCrontab(**cron_kwargs),
                "args": (task_id,),
                "options": {"queue": "scheduled"},
                "kwargs": {"trigger_type": "cron"},
            }
        logger.info("[Beat] Loaded %d active scheduled tasks", len(schedule))
    except Exception as e:
        logger.error("[Beat] Failed to load schedules from DB: %s", e)

    return schedule
