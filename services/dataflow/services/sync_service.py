"""Sync Service — business logic for sync tasks and scheduled tasks.

Tables:
- adh_sync_tasks: Sync task definitions
- adh_sync_logs: Sync execution logs
- adh_scheduled_tasks: Scheduled task definitions
- adh_scheduled_logs: Scheduled task execution logs
"""

import json
import logging
from datetime import datetime
from typing import Optional

from services.shared.common.db import get_metadata_conn

logger = logging.getLogger(__name__)


def _get_conn():
    """Get a database connection from the shared pool."""
    return get_metadata_conn()



class SyncService:
    """Service for managing sync tasks and scheduled tasks."""

    # ── Sync Tasks ────────────────────────────────────────────────

    def list_tasks(self, page: int = 1, size: int = 20, status: Optional[str] = None) -> dict:
        """List sync tasks with pagination."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                where = "WHERE 1=1"
                params = []
                if status:
                    where += " AND status = %s"
                    params.append(status)

                cur.execute(f"SELECT COUNT(*) as total FROM adh_sync_tasks {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_sync_tasks {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()

                # Parse JSON fields
                for row in rows:
                    self._deserialize_row(row)

                return {"total": total, "page": page, "size": size, "items": rows}
        finally:
            conn.close()

    def get_task(self, task_id: int) -> Optional[dict]:
        """Get a single sync task by ID."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_sync_tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                if row:
                    self._deserialize_row(row)
                return row
        finally:
            conn.close()

    def create_task(self, data: dict, dag_id: str) -> int:
        """Create a new sync task."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """INSERT INTO adh_sync_tasks
                    (name, description, source_type, source_config, target_type, target_config,
                     sync_mode, schedule, dag_id, task_config, is_active, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'idle', %s, %s)""",
                    (
                        data["name"],
                        data.get("description", ""),
                        data["source_type"],
                        json.dumps(data["source_config"]),
                        data["target_type"],
                        json.dumps(data["target_config"]),
                        data.get("sync_mode", "full"),
                        data.get("schedule"),
                        dag_id,
                        json.dumps(data.get("task_config", {})),
                        now, now,
                    ),
                )
                conn.commit()
                return cur.lastrowid
        finally:
            conn.close()

    def update_task(self, task_id: int, data: dict) -> bool:
        """Update a sync task."""
        if not data:
            return True

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                sets = []
                params = []
                for key, value in data.items():
                    if key in ("source_config", "target_config", "task_config"):
                        sets.append(f"{key} = %s")
                        params.append(json.dumps(value))
                    else:
                        sets.append(f"{key} = %s")
                        params.append(value)

                sets.append("updated_at = %s")
                params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                params.append(task_id)

                cur.execute(
                    f"UPDATE adh_sync_tasks SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_task(self, task_id: int) -> bool:
        """Delete a sync task and its logs."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_sync_logs WHERE task_id = %s", (task_id,))
                cur.execute("DELETE FROM adh_sync_tasks WHERE id = %s", (task_id,))
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Sync Logs ─────────────────────────────────────────────────

    def list_logs(self, task_id: Optional[int] = None, page: int = 1, size: int = 20) -> dict:
        """List execution logs, optionally filtered by sync task."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                where = "WHERE task_id = %s" if task_id is not None else ""
                params: tuple = (task_id,) if task_id is not None else ()
                cur.execute(
                    f"SELECT COUNT(*) as total FROM adh_sync_logs {where}",
                    params,
                )
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_sync_logs {where} ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    params + (size, offset),
                )
                rows = cur.fetchall()
                return {"total": total, "page": page, "size": size, "items": rows}
        finally:
            conn.close()

    def create_log(self, task_id: int, dag_run_id: str = "", status: str = "running") -> int:
        """Create a new sync execution log."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """INSERT INTO adh_sync_logs
                    (task_id, dag_run_id, status, started_at, records_synced, error_message)
                    VALUES (%s, %s, %s, %s, 0, '')""",
                    (task_id, dag_run_id, status, now),
                )
                conn.commit()
                return cur.lastrowid
        finally:
            conn.close()

    def update_log(self, log_id: int, **kwargs) -> bool:
        """Update a sync log entry."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                sets = []
                params = []
                for key, value in kwargs.items():
                    sets.append(f"{key} = %s")
                    params.append(value)
                params.append(log_id)

                cur.execute(
                    f"UPDATE adh_sync_logs SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Scheduled Tasks ───────────────────────────────────────────

    def list_scheduled_tasks(self, workspace_id: int = 0, page: int = 1, size: int = 20) -> dict:
        """List scheduled tasks with pagination."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                where = "WHERE workspace_id = %s"
                params = [workspace_id]

                cur.execute(f"SELECT COUNT(*) as total FROM adh_scheduled_tasks {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_scheduled_tasks {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()

                for row in rows:
                    self._deserialize_row(row)

                return {"total": total, "page": page, "size": size, "items": rows}
        finally:
            conn.close()

    def get_scheduled_task(self, task_id: int) -> Optional[dict]:
        """Get a single scheduled task by ID."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_scheduled_tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                if row:
                    self._deserialize_row(row)
                return row
        finally:
            conn.close()

    def create_scheduled_task(self, data: dict, workspace_id: int = 0) -> int:
        """Create a new scheduled task."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """INSERT INTO adh_scheduled_tasks
                    (name, description, task_type, task_config, cron_expression,
                     notification_channel_ids, workspace_id, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)""",
                    (
                        data["name"],
                        data.get("description", ""),
                        data.get("task_type", "query"),
                        json.dumps(data["task_config"]),
                        data["cron_expression"],
                        json.dumps(data.get("notification_channel_ids", [])),
                        workspace_id,
                        now, now,
                    ),
                )
                conn.commit()
                return cur.lastrowid
        finally:
            conn.close()

    def update_scheduled_task(self, task_id: int, data: dict) -> bool:
        """Update a scheduled task."""
        if not data:
            return True

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                sets = []
                params = []
                for key, value in data.items():
                    if key in ("task_config", "notification_channel_ids"):
                        sets.append(f"{key} = %s")
                        params.append(json.dumps(value))
                    else:
                        sets.append(f"{key} = %s")
                        params.append(value)

                sets.append("updated_at = %s")
                params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                params.append(task_id)

                cur.execute(
                    f"UPDATE adh_scheduled_tasks SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_scheduled_task(self, task_id: int) -> bool:
        """Delete a scheduled task and its logs."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_scheduled_logs WHERE task_id = %s", (task_id,))
                cur.execute("DELETE FROM adh_scheduled_tasks WHERE id = %s", (task_id,))
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Scheduled Logs ────────────────────────────────────────────

    def list_scheduled_logs(self, task_id: int, page: int = 1, size: int = 20, status: Optional[str] = None) -> dict:
        """List execution logs for a scheduled task."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                where = "WHERE task_id = %s"
                params = [task_id]
                if status:
                    where += " AND status = %s"
                    params.append(status)

                cur.execute(f"SELECT COUNT(*) as total FROM adh_scheduled_logs {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_scheduled_logs {where} ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()
                return {"total": total, "page": page, "size": size, "items": rows}
        finally:
            conn.close()

    def create_scheduled_log(self, task_id: int, trigger_type: str = "manual") -> int:
        """Create a new scheduled task execution log."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """INSERT INTO adh_scheduled_logs
                    (task_id, status, trigger_type, started_at)
                    VALUES (%s, 'running', %s, %s)""",
                    (task_id, trigger_type, now),
                )
                conn.commit()
                return cur.lastrowid
        finally:
            conn.close()

    def update_scheduled_log(self, log_id: int, **kwargs) -> bool:
        """Update a scheduled log entry."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                sets = []
                params = []
                for key, value in kwargs.items():
                    sets.append(f"{key} = %s")
                    params.append(value)
                params.append(log_id)

                cur.execute(
                    f"UPDATE adh_scheduled_logs SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def cleanup_stale_running_logs(self, timeout_minutes: int = 10) -> int:
        """Mark stale running logs as timeout."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE adh_scheduled_logs SET status = 'timeout'
                    WHERE status = 'running'
                    AND started_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)""",
                    (timeout_minutes,),
                )
                conn.commit()
                return cur.rowcount
        finally:
            conn.close()

    # ── Helpers ───────────────────────────────────────────────────

    def _deserialize_row(self, row: dict):
        """Parse JSON string fields in a DB row."""
        json_fields = ("source_config", "target_config", "task_config", "notification_channel_ids")
        for field in json_fields:
            if field in row and isinstance(row[field], str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass


# Singleton
sync_service = SyncService()


def execute_scheduled_task(task_id: int, trigger_type: str = "manual"):
    """Execute a scheduled task (called from background tasks).

    This is a simplified executor. The main backend has a full executor
    with agent integration — this provides basic execution for the DataFlow service.
    """
    logger.info("Executing scheduled task %s (trigger=%s)", task_id, trigger_type)

    log_id = sync_service.create_scheduled_log(task_id, trigger_type)

    try:
        task = sync_service.get_scheduled_task(task_id)
        if not task:
            sync_service.update_scheduled_log(log_id, status="failed", error_message="Task not found")
            return

        task_config = task.get("task_config", {})
        # Placeholder for actual execution logic
        # In production, this would call the main backend's agent pipeline
        # or execute SQL directly against the configured datasource
        logger.info("Task config: %s", task_config)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sync_service.update_scheduled_log(log_id, status="success", finished_at=now)
        logger.info("Scheduled task %s completed successfully", task_id)

    except Exception as e:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sync_service.update_scheduled_log(
            log_id,
            status="failed",
            finished_at=now,
            error_message=str(e)[:1000],
        )
        logger.error("Scheduled task %s failed: %s", task_id, e)
