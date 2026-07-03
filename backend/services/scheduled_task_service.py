"""Scheduled Task Service — CRUD for scheduled tasks, logs, and notification channels.

All operations are workspace-scoped for multi-tenant isolation.
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id():
    return int(_time.time() * 1000000)


class ScheduledTaskService:
    """Service for managing scheduled tasks, execution logs, and notification channels."""

    # ── Scheduled Tasks CRUD ────────────────────────────────────────

    def list_tasks(self, workspace_id: int = 0, page: int = 1, size: int = 20) -> dict:
        """List scheduled tasks with pagination, scoped by workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                conditions = []
                params = []
                if workspace_id:
                    conditions.append("workspace_id = %s")
                    params.append(workspace_id)
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                cur.execute(f"SELECT COUNT(*) AS total FROM adh_scheduled_tasks {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_scheduled_tasks {where} "
                    f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()
                for r in rows:
                    self._normalize_task(r)
                return {"items": rows, "total": total}
        finally:
            conn.close()

    def get_task(self, task_id: int) -> Optional[dict]:
        """Get a single scheduled task by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_scheduled_tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                if row:
                    self._normalize_task(row)
                return row
        finally:
            conn.close()

    def create_task(self, data: dict, owner_id: int, workspace_id: int = 0) -> int:
        """Create a new scheduled task. Returns the new task ID."""
        task_id = _generate_id()
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_scheduled_tasks "
                    "(id, name, description, task_type, task_config, report_template_key, "
                    "cron_expression, timezone, channel_id, notify_on_success, notify_on_failure, "
                    "is_active, workspace_id, owner_id, timeout_seconds, max_retries, "
                    "created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        task_id,
                        data["name"],
                        data.get("description", ""),
                        data["task_type"],
                        json.dumps(data["task_config"]),
                        data.get("report_template_key"),
                        data["cron_expression"],
                        data.get("timezone", "Asia/Shanghai"),
                        data.get("channel_id"),
                        1 if data.get("notify_on_success", True) else 0,
                        1 if data.get("notify_on_failure", True) else 0,
                        1 if data.get("is_active", True) else 0,
                        workspace_id,
                        owner_id,
                        data.get("timeout_seconds", 300),
                        data.get("max_retries", 0),
                        now,
                        now,
                    ),
                )
            conn.commit()
            return task_id
        finally:
            conn.close()

    def update_task(self, task_id: int, data: dict) -> bool:
        """Update a scheduled task."""
        if not data:
            return False
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                updates = ["updated_at = %s"]
                params = [_now()]

                field_map = {
                    "name": "name",
                    "description": "description",
                    "task_type": "task_type",
                    "report_template_key": "report_template_key",
                    "cron_expression": "cron_expression",
                    "timezone": "timezone",
                    "timeout_seconds": "timeout_seconds",
                    "max_retries": "max_retries",
                }
                for key, col in field_map.items():
                    if key in data and data[key] is not None:
                        updates.append(f"`{col}` = %s")
                        params.append(data[key])

                if "task_config" in data and data["task_config"] is not None:
                    updates.append("task_config = %s")
                    params.append(json.dumps(data["task_config"]))

                if "channel_id" in data:
                    updates.append("channel_id = %s")
                    params.append(data["channel_id"])

                for flag in ("notify_on_success", "notify_on_failure", "is_active"):
                    if flag in data and data[flag] is not None:
                        updates.append(f"`{flag}` = %s")
                        params.append(1 if data[flag] else 0)

                params.append(task_id)
                cur.execute(
                    f"UPDATE adh_scheduled_tasks SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_task(self, task_id: int) -> bool:
        """Delete a scheduled task and its logs."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_scheduled_logs WHERE scheduled_task_id = %s", (task_id,))
                cur.execute("DELETE FROM adh_scheduled_tasks WHERE id = %s", (task_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def toggle_task(self, task_id: int, is_active: int) -> bool:
        """Enable or disable a scheduled task."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_scheduled_tasks SET is_active = %s, updated_at = %s WHERE id = %s",
                    (is_active, _now(), task_id),
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def update_task_status(self, task_id: int, status: str, error: str = None):
        """Update task runtime status after execution."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_scheduled_tasks SET last_run_at = %s, last_status = %s, "
                    "last_error = %s, run_count = run_count + 1, updated_at = %s WHERE id = %s",
                    (_now(), status, error, _now(), task_id),
                )
            conn.commit()
        finally:
            conn.close()

    def list_active_tasks(self) -> list:
        """List all active tasks (for Beat schedule loading)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_scheduled_tasks WHERE is_active = 1")
                rows = cur.fetchall()
                for r in rows:
                    self._normalize_task(r)
                return rows
        finally:
            conn.close()

    def _normalize_task(self, row: dict):
        """Normalize task row for JSON serialization."""
        for field in ("task_config",):
            if isinstance(row.get(field), str):
                row[field] = json.loads(row[field])
        for ts in ("created_at", "updated_at", "last_run_at"):
            if hasattr(row.get(ts), "isoformat"):
                row[ts] = row[ts].isoformat()

    # ── Scheduled Logs ──────────────────────────────────────────────

    def create_log(self, task_id: int, trigger_type: str, status: str,
                   celery_task_id: str = None, workspace_id: int = 0) -> int:
        """Create an execution log entry."""
        log_id = _generate_id()
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_scheduled_logs "
                    "(id, scheduled_task_id, workspace_id, status, trigger_type, "
                    "celery_task_id, started_at, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (log_id, task_id, workspace_id, status, trigger_type,
                     celery_task_id, now, now),
                )
            conn.commit()
            return log_id
        finally:
            conn.close()

    def update_log(self, log_id: int, **kwargs):
        """Update an execution log entry."""
        if not kwargs:
            return
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                updates = []
                params = []
                for key, val in kwargs.items():
                    if val is not None:
                        if isinstance(val, (dict, list)):
                            val = json.dumps(val)
                        updates.append(f"`{key}` = %s")
                        params.append(val)
                if not updates:
                    return
                params.append(log_id)
                cur.execute(
                    f"UPDATE adh_scheduled_logs SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
        finally:
            conn.close()

    def list_logs(self, task_id: int = 0, workspace_id: int = 0,
                  page: int = 1, size: int = 20, status: str = None) -> dict:
        """List execution logs with pagination and filters."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                conditions = []
                params = []
                if task_id:
                    conditions.append("scheduled_task_id = %s")
                    params.append(task_id)
                if workspace_id:
                    conditions.append("workspace_id = %s")
                    params.append(workspace_id)
                if status:
                    conditions.append("status = %s")
                    params.append(status)
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                cur.execute(f"SELECT COUNT(*) AS total FROM adh_scheduled_logs {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_scheduled_logs {where} "
                    f"ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()
                for r in rows:
                    self._normalize_log(r)
                return {"items": rows, "total": total}
        finally:
            conn.close()

    def get_log(self, log_id: int) -> Optional[dict]:
        """Get a single execution log by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_scheduled_logs WHERE id = %s", (log_id,))
                row = cur.fetchone()
                if row:
                    self._normalize_log(row)
                return row
        finally:
            conn.close()

    def get_log_stats(self, task_id: int) -> dict:
        """Get execution statistics for a task."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT "
                    "COUNT(*) AS total_runs, "
                    "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_runs, "
                    "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs, "
                    "AVG(CASE WHEN status = 'success' THEN elapsed_ms ELSE NULL END) AS avg_elapsed_ms "
                    "FROM adh_scheduled_logs WHERE scheduled_task_id = %s",
                    (task_id,),
                )
                row = cur.fetchone() or {}
                total = row.get("total_runs", 0) or 0
                success = row.get("success_runs", 0) or 0
                return {
                    "total_runs": total,
                    "success_runs": success,
                    "failed_runs": row.get("failed_runs", 0) or 0,
                    "success_rate": round(success / total * 100, 1) if total else 0,
                    "avg_elapsed_ms": int(row.get("avg_elapsed_ms") or 0),
                }
        finally:
            conn.close()

    def cleanup_logs(self, days: int = 30):
        """Delete logs older than N days."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_scheduled_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
                    (days,),
                )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def _normalize_log(self, row: dict):
        """Normalize log row for JSON serialization."""
        for field in ("questions_executed", "result_data", "token_usage"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        for ts in ("started_at", "finished_at", "created_at"):
            if hasattr(row.get(ts), "isoformat"):
                row[ts] = row[ts].isoformat()
        # Attach report link if report exists for this log
        if row.get("id"):
            report = self._get_report_by_log_id(row["id"])
            if report:
                row["report_id"] = report["id"]
                row["report_access_token"] = report.get("access_token")

    def _get_report_by_log_id(self, log_id: int) -> Optional[dict]:
        """Get report by execution log ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, access_token FROM adh_reports WHERE log_id = %s LIMIT 1", (log_id,))
                return cur.fetchone()
        finally:
            conn.close()

    # ── Notification Channels CRUD ──────────────────────────────────

    def list_channels(self, workspace_id: int = 0) -> list:
        """List notification channels, scoped by workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id:
                    cur.execute(
                        "SELECT * FROM adh_notification_channels WHERE workspace_id = %s ORDER BY created_at DESC",
                        (workspace_id,),
                    )
                else:
                    cur.execute("SELECT * FROM adh_notification_channels ORDER BY created_at DESC")
                rows = cur.fetchall()
                for r in rows:
                    self._normalize_channel(r)
                return rows
        finally:
            conn.close()

    def get_channel(self, channel_id: int) -> Optional[dict]:
        """Get a single notification channel by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_notification_channels WHERE id = %s", (channel_id,))
                row = cur.fetchone()
                if row:
                    self._normalize_channel(row)
                return row
        finally:
            conn.close()

    def create_channel(self, data: dict, owner_id: int, workspace_id: int = 0) -> int:
        """Create a new notification channel. Returns the new channel ID."""
        channel_id = _generate_id()
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_notification_channels "
                    "(id, name, channel_type, config, is_active, workspace_id, owner_id, "
                    "created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        channel_id,
                        data["name"],
                        data["channel_type"],
                        json.dumps(data["config"]),
                        1 if data.get("is_active", True) else 0,
                        workspace_id,
                        owner_id,
                        now,
                        now,
                    ),
                )
            conn.commit()
            return channel_id
        finally:
            conn.close()

    def update_channel(self, channel_id: int, data: dict) -> bool:
        """Update a notification channel."""
        if not data:
            return False
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                updates = ["updated_at = %s"]
                params = [_now()]

                if "name" in data and data["name"] is not None:
                    updates.append("name = %s")
                    params.append(data["name"])
                if "channel_type" in data and data["channel_type"] is not None:
                    updates.append("channel_type = %s")
                    params.append(data["channel_type"])
                if "config" in data and data["config"] is not None:
                    updates.append("config = %s")
                    params.append(json.dumps(data["config"]))
                if "is_active" in data and data["is_active"] is not None:
                    updates.append("is_active = %s")
                    params.append(1 if data["is_active"] else 0)

                params.append(channel_id)
                cur.execute(
                    f"UPDATE adh_notification_channels SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_channel(self, channel_id: int) -> bool:
        """Delete a notification channel."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_notification_channels WHERE id = %s", (channel_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def update_channel_test_status(self, channel_id: int, status: str):
        """Update channel test status."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_notification_channels SET last_test_at = %s, last_test_status = %s, "
                    "updated_at = %s WHERE id = %s",
                    (_now(), status, _now(), channel_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _normalize_channel(self, row: dict):
        """Normalize channel row for JSON serialization."""
        if isinstance(row.get("config"), str):
            try:
                row["config"] = json.loads(row["config"])
            except (json.JSONDecodeError, TypeError):
                pass
        for ts in ("created_at", "updated_at", "last_test_at"):
            if hasattr(row.get(ts), "isoformat"):
                row[ts] = row[ts].isoformat()

    # ── Report Templates CRUD ───────────────────────────────────────

    def list_templates(self, workspace_id: int = 0) -> list:
        """List report templates (system + workspace-scoped)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id:
                    cur.execute(
                        "SELECT * FROM adh_report_templates "
                        "WHERE is_system = 1 OR workspace_id = %s "
                        "ORDER BY is_system DESC, name ASC",
                        (workspace_id,),
                    )
                else:
                    cur.execute("SELECT * FROM adh_report_templates ORDER BY is_system DESC, name ASC")
                rows = cur.fetchall()
                self._normalize_template(rows)
                return rows
        finally:
            conn.close()

    def get_template(self, template_id: int) -> Optional[dict]:
        """Get a single report template by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_report_templates WHERE id = %s", (template_id,))
                row = cur.fetchone()
                if row:
                    self._normalize_template([row])
                return row
        finally:
            conn.close()

    def create_template(self, data: dict, owner_id: int, workspace_id: int = 0) -> int:
        """Create a new report template. Returns the new template ID."""
        template_id = _generate_id()
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_report_templates "
                    "(id, name, description, content, format, is_system, workspace_id, owner_id, "
                    "created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        template_id,
                        data["name"],
                        data.get("description", ""),
                        data["content"],
                        data.get("format", "markdown"),
                        0,
                        workspace_id,
                        owner_id,
                        now,
                        now,
                    ),
                )
            conn.commit()
            return template_id
        finally:
            conn.close()

    def update_template(self, template_id: int, data: dict) -> bool:
        """Update a report template. System templates cannot be modified."""
        if not data:
            return False
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Check if system template
                cur.execute("SELECT is_system FROM adh_report_templates WHERE id = %s", (template_id,))
                row = cur.fetchone()
                if row and row.get("is_system"):
                    raise ValueError("系统内置模板不可修改")

                updates = ["updated_at = %s"]
                params = [_now()]
                for key in ("name", "description", "content", "format"):
                    if key in data and data[key] is not None:
                        updates.append(f"`{key}` = %s")
                        params.append(data[key])
                params.append(template_id)
                cur.execute(
                    f"UPDATE adh_report_templates SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_template(self, template_id: int) -> bool:
        """Delete a report template. System templates cannot be deleted."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT is_system FROM adh_report_templates WHERE id = %s", (template_id,))
                row = cur.fetchone()
                if row and row.get("is_system"):
                    raise ValueError("系统内置模板不可删除")
                cur.execute("DELETE FROM adh_report_templates WHERE id = %s", (template_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def _normalize_template(self, rows):
        """Normalize template rows for JSON serialization."""
        for row in rows if isinstance(rows, list) else [rows]:
            for ts in ("created_at", "updated_at"):
                if hasattr(row.get(ts), "isoformat"):
                    row[ts] = row[ts].isoformat()

    # ── Generated Reports ───────────────────────────────────────────

    def create_report(self, task_id: int, log_id: int, title: str, content: str,
                      format: str = "markdown", access_mode: str = "private",
                      workspace_id: int = 0, owner_id: int = 0) -> dict:
        """Create a generated report. Returns the report dict with access token."""
        report_id = _generate_id()
        import secrets
        access_token = secrets.token_urlsafe(32) if access_mode == "private" else None
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_reports "
                    "(id, task_id, log_id, title, content, format, access_mode, access_token, "
                    "workspace_id, owner_id, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (report_id, task_id, log_id, title, content, format,
                     access_mode, access_token, workspace_id, owner_id, now),
                )
            conn.commit()
            return {"id": report_id, "access_token": access_token, "access_mode": access_mode}
        finally:
            conn.close()

    def get_report(self, report_id: int, access_token: str = None) -> Optional[dict]:
        """Get a report by ID. For private reports, access_token is required."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_reports WHERE id = %s", (report_id,))
                row = cur.fetchone()
                if not row:
                    return None
                if row["access_mode"] == "private":
                    if not access_token or access_token != row.get("access_token"):
                        return {"id": row["id"], "access_mode": "private", "error": "需要访问令牌"}
                # Increment view count
                cur.execute("UPDATE adh_reports SET view_count = view_count + 1 WHERE id = %s", (report_id,))
                conn.commit()
                self._normalize_report(row)
                return row
        finally:
            conn.close()

    def _normalize_report(self, row):
        """Normalize report row."""
        if hasattr(row.get("created_at"), "isoformat"):
            row["created_at"] = row["created_at"].isoformat()

    # ── Stale Task Detection ────────────────────────────────────────

    def cleanup_stale_running_logs(self, timeout_minutes: int = 10) -> int:
        """Mark 'running' logs older than N minutes as 'timeout'.

        Returns the number of logs cleaned up.
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_scheduled_logs SET status = 'timeout', "
                    "error_message = '执行超时，可能已崩溃', "
                    "finished_at = NOW() "
                    "WHERE status = 'running' AND started_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)",
                    (timeout_minutes,),
                )
            conn.commit()
            count = cur.rowcount
            if count:
                logger.info("[Service] Cleaned up %d stale running logs (timeout=%dmin)", count, timeout_minutes)
            return count
        finally:
            conn.close()


# Singleton
scheduled_task_service = ScheduledTaskService()
