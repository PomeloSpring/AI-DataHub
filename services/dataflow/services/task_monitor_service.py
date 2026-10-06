"""任务监控服务 — 定时任务 / 队列任务 / 同步任务的统一运维视图。

覆盖三类任务对象（系统配置「任务监控」页消费）：
1. 定时任务定义：系统内置任务（`services/shared/common/system_jobs`，可暂停不可移除）
   + 用户创建的定时任务（`adh_scheduled_tasks`，可停止/移除）；
2. 队列任务＝执行实例：定时任务执行（`adh_scheduled_logs`）、报表生成（`adh_reports`）、
   数据同步执行（`adh_sync_logs`）三类队列实例统一视图，带进度/结果/卡死标记，可停止；
3. 同步任务：数据同步任务定义（`adh_sync_tasks`）+ 本体知识库同步水位线（`adh_ontology_kb_sync_state`）。

安全口径：
- 停止/删除一律条件更新（终态保护 + 幂等），重复投递不产生重复副作用（distributed-first §3）；
- 执行失败细节按护栏 §7 脱敏后回显，原始报错只留服务端日志。
"""

import logging
import re
from typing import Optional

from services.shared.common.db import execute_query, execute_write

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
RUN_KINDS = ("scheduled", "report", "sync")

# 报表生成态 → 统一运行态
_REPORT_STATUS_MAP = {"ready": "success", "degraded": "partial", "legacy": "success",
                      "queued": "queued", "running": "running", "failed": "failed",
                      "timeout": "timeout", "cancelled": "cancelled"}

# 护栏 §7: 失败细节脱敏——含连接信息/报错栈的行直接丢弃
_LEAK_PATTERN = re.compile(
    r"(password|passwd|pwd|host|hostname|port|username|user=|账号|密码|连接串|jdbc|"
    r"mysql://|postgres(ql)?://|oracle://|\bTraceback\b|File \")", re.IGNORECASE)
# UI 规范: 内部数值 id 不得当作展示内容，提示文本里的 kb=/id= 等一律抹值
_ID_PATTERN = re.compile(r"\b(kb|id|model|notebook|datasource)=\d+", re.IGNORECASE)
_HINT_BY_CODE = {
    "DISPATCH_FAILED": "任务队列暂不可用，派发未完成",
    "TIMEOUT": "执行超时，已被对账标记为超时",
    "CANCELLED": "任务已被停止",
    "OWNER_REQUIRED": "任务缺少可信创建者，未执行",
    "QUERY_FAILED": "查询未完成，请检查配置或权限",
    "REPORT_FAILED": "报告阶段未完成",
    "EXECUTION_FAILED": "执行未完成，请检查配置、权限或执行日志",
}


def _now_expr(alias: str = "") -> str:
    return f"{alias}.finished_at" if alias else "finished_at"


def _error_hint(stage_error_code, message) -> str:
    """失败提示：优先阶段错误码的标准文案，其次过滤后的错误行；绝不回显连接信息/报错栈。"""
    code = str(stage_error_code or "")
    lines = [ln.strip() for ln in str(message or "").splitlines()
             if ln.strip() and not _LEAK_PATTERN.search(ln)]
    text = _ID_PATTERN.sub(lambda m: f"{m.group(1).lower()}=?", "；".join(lines))[:200]
    if text:
        return text
    return _HINT_BY_CODE.get(code, "执行未完成，请在服务端日志排查") if code else "执行未完成，请在服务端日志排查"


def _iso(row: dict, *fields):
    for field in fields:
        if hasattr(row.get(field), "isoformat"):
            row[field] = row[field].isoformat()
    return row


class TaskMonitorService:
    """任务监控查询与运维操作（全部走共享元库，可多实例并发）。"""

    # ── 总览 ────────────────────────────────────────────────────────

    def summary(self) -> dict:
        sched = execute_query(
            "SELECT COUNT(*) AS total, SUM(is_active=1) AS active, "
            "SUM(owner_id IS NULL OR owner_id<=0) AS unclaimed FROM adh_scheduled_tasks")[0] or {}
        sched_runs = execute_query(
            "SELECT SUM(status IN ('queued','running')) AS active, "
            "SUM(status IN ('queued','running') AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at<=NOW()) AS stuck, "
            "SUM(status='failed') AS failed_total, SUM(status='timeout') AS timeout_total "
            "FROM adh_scheduled_logs")[0] or {}
        report_runs = execute_query(
            "SELECT SUM(generation_status IN ('queued','running')) AS active, "
            "SUM(generation_status IN ('queued','running') AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at<=NOW()) AS stuck, "
            "SUM(generation_status='failed') AS failed_total "
            "FROM adh_reports")[0] or {}
        sync_runs = execute_query(
            "SELECT SUM(status IN ('running')) AS active, "
            "SUM(status='running' AND started_at<=NOW()-INTERVAL 10 MINUTE) AS stuck, "
            "SUM(status='failed') AS failed_total FROM adh_sync_logs")[0] or {}
        sync_tasks = execute_query(
            "SELECT COUNT(*) AS total, SUM(is_active=1) AS active FROM adh_sync_tasks")[0] or {}
        from services.shared.common import system_jobs
        jobs = system_jobs.list_jobs()
        return {
            "scheduled_tasks": {"total": int(sched.get("total") or 0),
                                "active": int(sched.get("active") or 0),
                                "unclaimed": int(sched.get("unclaimed") or 0)},
            "system_jobs": {"total": len(jobs),
                            "paused": sum(1 for j in jobs if not int(j.get("is_active") or 0))},
            "runs": {
                "active": sum(int(r.get("active") or 0) for r in (sched_runs, report_runs, sync_runs)),
                "stuck": sum(int(r.get("stuck") or 0) for r in (sched_runs, report_runs, sync_runs)),
                "failed_total": sum(int(r.get("failed_total") or 0) for r in (sched_runs, report_runs, sync_runs)),
                "timeout_total": sum(int(r.get("timeout_total") or 0) for r in (sched_runs,)),
                "by_kind": {
                    "scheduled": {k: int(sched_runs.get(k) or 0) for k in ("active", "stuck", "failed_total")},
                    "report": {k: int(report_runs.get(k) or 0) for k in ("active", "stuck", "failed_total")},
                    "sync": {k: int(sync_runs.get(k) or 0) for k in ("active", "stuck", "failed_total")},
                },
            },
            "sync_tasks": {"total": int(sync_tasks.get("total") or 0),
                           "active": int(sync_tasks.get("active") or 0)},
        }

    # ── 定时任务（内置 + 用户）────────────────────────────────────────

    def list_tasks(self, source: str = None, keyword: str = None) -> list:
        """统一任务清单：source=system(内置) / user(用户创建)。"""
        items = []
        if source in (None, "", "system"):
            for job in self.list_system_jobs():
                items.append({
                    "kind": "system_job", "task_id": job["job_key"], "name": job["name"],
                    "description": job.get("description", ""), "schedule": job.get("schedule_desc", ""),
                    "task_type": "system", "source": "system", "removable": False,
                    "enabled": bool(job.get("is_active")), "ownership": "system",
                    "workspace_id": 0, "owner_name": "系统",
                    "last_run_at": job.get("last_run_at"), "last_status": job.get("last_status"),
                    "last_result": job.get("last_result"), "run_count": int(job.get("run_count") or 0),
                    "active_runs": 0, "stuck_runs": 0, "paused_at": job.get("paused_at"),
                    "paused_by": job.get("paused_by") or "",
                })
        if source in (None, "", "user"):
            rows = execute_query(
                "SELECT t.*, JSON_LENGTH(t.task_config, '$.questions') AS questions_total, "
                "(SELECT COUNT(*) FROM adh_scheduled_logs l WHERE l.scheduled_task_id=t.id "
                " AND l.status IN ('queued','running')) AS active_runs, "
                "(SELECT COUNT(*) FROM adh_scheduled_logs l WHERE l.scheduled_task_id=t.id "
                " AND l.status IN ('queued','running') AND l.lease_expires_at IS NOT NULL "
                " AND l.lease_expires_at<=NOW()) AS stuck_runs, "
                "u.username AS owner_name "
                "FROM adh_scheduled_tasks t LEFT JOIN adh_users u ON u.id=t.owner_id "
                "ORDER BY t.created_at DESC")
            for row in rows or []:
                _iso(row, "created_at", "updated_at", "last_run_at")
                unclaimed = int(row.get("owner_id") or 0) <= 0
                items.append({
                    "kind": "scheduled_task", "task_id": row["id"], "name": row.get("name", ""),
                    "description": row.get("description") or "", "schedule": row.get("cron_expression", ""),
                    "timezone": row.get("timezone") or "Asia/Shanghai",
                    "task_type": row.get("task_type") or "query", "source": "user", "removable": True,
                    "enabled": bool(row.get("is_active")), "workspace_id": int(row.get("workspace_id") or 0),
                    "ownership": "unclaimed" if unclaimed else "owned",
                    "owner_name": row.get("owner_name") or ("待认领" if unclaimed else "未知"),
                    "questions_total": row.get("questions_total"),
                    "last_run_at": row.get("last_run_at"), "last_status": row.get("last_status"),
                    "run_count": int(row.get("run_count") or 0),
                    "active_runs": int(row.get("active_runs") or 0),
                    "stuck_runs": int(row.get("stuck_runs") or 0),
                    "timeout_seconds": row.get("timeout_seconds"),
                })
        if keyword:
            key = str(keyword).strip().lower()
            items = [i for i in items if key in str(i.get("name", "")).lower()
                     or key in str(i.get("description", "")).lower()]
        return items

    def get_task(self, task_id: int) -> Optional[dict]:
        rows = execute_query("SELECT * FROM adh_scheduled_tasks WHERE id=%s", (task_id,))
        return rows[0] if rows else None

    def stop_task(self, task_id: int) -> dict:
        """停止任务：停用定义 + 取消其全部排队/运行中的实例（条件更新，幂等）。"""
        task = self.get_task(task_id)
        if not task:
            raise ValueError("定时任务不存在")
        execute_write("UPDATE adh_scheduled_tasks SET is_active=0, updated_at=NOW() WHERE id=%s", (task_id,))
        cancelled = self._cancel_task_runs(task_id)
        return {"task_id": task_id, "disabled": True, "cancelled_runs": cancelled}

    def remove_task(self, task_id: int) -> dict:
        """移除用户创建的任务（级联执行记录）；系统内置任务显式拒绝。"""
        task = self.get_task(task_id)
        if not task:
            raise ValueError("定时任务不存在")
        cancelled = self._cancel_task_runs(task_id)
        execute_write("DELETE FROM adh_scheduled_logs WHERE scheduled_task_id=%s", (task_id,))
        execute_write("DELETE FROM adh_scheduled_tasks WHERE id=%s", (task_id,))
        return {"task_id": task_id, "removed": True, "cancelled_runs": cancelled}

    def _cancel_task_runs(self, task_id: int) -> int:
        rows = execute_query(
            "SELECT id FROM adh_scheduled_logs WHERE scheduled_task_id=%s AND status IN ('queued','running')",
            (task_id,))
        count = 0
        for row in rows or []:
            if self._cancel_scheduled_run(row["id"]):
                count += 1
        return count

    # ── 系统内置任务 ─────────────────────────────────────────────────

    def list_system_jobs(self) -> list:
        from services.shared.common import system_jobs
        return system_jobs.list_jobs()

    def pause_system_job(self, job_key: str, paused: bool, actor: str = "") -> dict:
        """内置任务支持暂停/恢复；不支持移除（注册表由代码维护）。"""
        from services.shared.common import system_jobs
        return system_jobs.set_paused(job_key, paused, actor)

    # ── 队列任务（执行实例）───────────────────────────────────────────

    def list_runs(self, kind: str = None, status: str = None, task_id: int = None,
                  page: int = 1, size: int = 20) -> dict:
        """跨队列执行实例统一列表。status: active / stuck / success / partial / failed / timeout / cancelled。
        task_id: 按任务过滤其执行历史（任务详情弹窗消费）。
        """
        conditions, params = [], []
        if kind:
            if kind not in RUN_KINDS:
                raise ValueError(f"未知队列类型: {kind}")
            conditions.append("kind = %s")
            params.append(kind)
        if task_id:
            conditions.append("task_id = %s")
            params.append(int(task_id))
        if status == "active":
            conditions.append("raw_status IN ('queued','running')")
        elif status == "stuck":
            conditions.append("is_stuck = 1")
        elif status:
            conditions.append("normalized_status = %s")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        union = self._runs_union()
        total_rows = execute_query(f"SELECT COUNT(*) AS total FROM ({union}) runs {where}", tuple(params))
        total = int((total_rows[0] or {}).get("total") or 0)
        offset = (page - 1) * size
        rows = execute_query(
            f"SELECT * FROM ({union}) runs {where} ORDER BY started_at DESC LIMIT %s OFFSET %s",
            tuple(params) + (size, offset))
        items = []
        for row in rows or []:
            row["error_hint"] = _error_hint(row.get("stage_error_code"), row.get("error_message"))
            row.pop("error_message", None)
            row["stuck"] = bool(row.get("is_stuck"))
            _iso(row, "started_at", "finished_at")
            items.append(row)
        return {"items": items, "total": total}

    @staticmethod
    def _runs_union() -> str:
        """三类队列实例 UNION（列名对齐运行时元库 schema）。

        输出列固定为: kind, run_id, task_id, task_name, workspace_id, raw_status,
        normalized_status, trigger_type, started_at, finished_at, elapsed_ms, worker_id,
        result_summary, stage_error_code, error_message, done_count, fail_count,
        total_count, rows_read, rows_written, is_stuck。
        """
        report_status = ("CASE r.generation_status WHEN 'ready' THEN 'success' "
                         "WHEN 'degraded' THEN 'partial' WHEN 'legacy' THEN 'success' "
                         "ELSE r.generation_status END")
        return (
            "SELECT 'scheduled' AS kind, l.id AS run_id, l.scheduled_task_id AS task_id, "
            "t.name AS task_name, l.workspace_id, l.status AS raw_status, "
            "CASE l.status WHEN 'success' THEN 'success' WHEN 'partial' THEN 'partial' "
            " WHEN 'failed' THEN 'failed' WHEN 'timeout' THEN 'timeout' WHEN 'cancelled' THEN 'cancelled' "
            " ELSE l.status END AS normalized_status, l.trigger_type, l.started_at, l.finished_at, "
            "l.elapsed_ms, l.worker_id, l.result_summary, l.stage_error_code, l.error_message, "
            "l.questions_succeeded AS done_count, l.questions_failed AS fail_count, "
            "JSON_LENGTH(t.task_config, '$.questions') AS total_count, "
            "NULL AS rows_read, NULL AS rows_written, "
            "(l.status IN ('queued','running') AND l.lease_expires_at IS NOT NULL "
            " AND l.lease_expires_at<=NOW()) AS is_stuck "
            "FROM adh_scheduled_logs l JOIN adh_scheduled_tasks t ON t.id=l.scheduled_task_id "
            "UNION ALL "
            f"SELECT 'report', r.id, r.task_id, r.title, r.workspace_id, r.generation_status, "
            f"{report_status}, '', r.created_at, NULL, NULL, NULL, NULL, r.stage_error_code, NULL, "
            "NULL, NULL, NULL, NULL, NULL, "
            "(r.generation_status IN ('queued','running') AND r.lease_expires_at IS NOT NULL "
            " AND r.lease_expires_at<=NOW()) AS is_stuck "
            "FROM adh_reports r "
            "UNION ALL "
            "SELECT 'sync', sl.id, sl.sync_task_id, st.name, sl.workspace_id, sl.status, sl.status, "
            "sl.trigger_type, sl.started_at, sl.finished_at, sl.elapsed_ms, NULL, "
            "CONCAT('读取 ', COALESCE(sl.rows_read,0), ' 行，写入 ', COALESCE(sl.rows_written,0), ' 行'), "
            "NULL, sl.error_message, NULL, NULL, NULL, sl.rows_read, sl.rows_written, "
            "(sl.status='running' AND sl.started_at<=NOW()-INTERVAL 10 MINUTE) AS is_stuck "
            "FROM adh_sync_logs sl LEFT JOIN adh_sync_tasks st ON st.id=sl.sync_task_id"
        )

    def stop_run(self, kind: str, run_id: int) -> dict:
        """停止一个队列实例（终态保护：已结束的实例拒绝二次停止，幂等可重放）。"""
        if kind == "scheduled":
            if not self._cancel_scheduled_run(run_id):
                raise ValueError("任务已结束，不能停止")
            return {"kind": kind, "run_id": run_id, "status": "cancelled"}
        if kind == "report":
            changed = execute_write(
                "UPDATE adh_reports SET generation_status='cancelled', stage_error_code='CANCELLED', "
                "content='' WHERE id=%s AND generation_status IN ('queued','running')", (run_id,))
            if not changed:
                raise ValueError("任务已结束，不能停止")
            return {"kind": kind, "run_id": run_id, "status": "cancelled"}
        if kind == "sync":
            changed = execute_write(
                "UPDATE adh_sync_logs SET status='cancelled', finished_at=NOW(), "
                "error_message='任务已被停止' WHERE id=%s AND status='running'", (run_id,))
            if not changed:
                raise ValueError("任务已结束，不能停止")
            return {"kind": kind, "run_id": run_id, "status": "cancelled"}
        raise ValueError(f"未知队列类型: {kind}")

    @staticmethod
    def _cancel_scheduled_run(log_id: int) -> bool:
        from services.dataflow.services.scheduled_task_service import scheduled_task_service
        from services.dataflow.tasks.executor import _now_iso
        return bool(scheduled_task_service.finish_log(
            log_id, status="cancelled", stage_error_code="CANCELLED", finished_at=_now_iso()))

    def cleanup_stale(self, timeout_minutes: int = 10) -> dict:
        """手动卡死清理：定时执行实例 + 报表生成 + 同步执行，全部条件更新为超时。"""
        from services.dataflow.services.scheduled_task_service import scheduled_task_service
        from services.dataviz.services.report_service import cleanup_stale_reports
        sched = scheduled_task_service.cleanup_stale_running_logs(timeout_minutes)
        reports = cleanup_stale_reports()
        sync = execute_write(
            "UPDATE adh_sync_logs SET status='failed', finished_at=NOW(), "
            "error_message='执行超过租约未完成，已按超时清理' "
            "WHERE status='running' AND started_at<=NOW()-INTERVAL %s MINUTE", (timeout_minutes,))
        return {"scheduled_runs": sched, "report_runs": reports, "sync_runs": sync}

    # ── 同步任务 ─────────────────────────────────────────────────────

    def list_sync_tasks(self) -> list:
        rows = execute_query(
            "SELECT t.*, "
            "(SELECT COUNT(*) FROM adh_sync_logs l WHERE l.sync_task_id=t.id "
            " AND l.status='running') AS active_runs FROM adh_sync_tasks t ORDER BY t.created_at DESC")
        items = []
        for row in rows or []:
            _iso(row, "created_at", "updated_at", "last_run_at")
            row["source"] = "user"
            row["removable"] = True
            row["active_runs"] = int(row.get("active_runs") or 0)
            items.append(row)
        return items

    def toggle_sync_task(self, task_id: int, is_active: bool) -> dict:
        changed = execute_write("UPDATE adh_sync_tasks SET is_active=%s, updated_at=NOW() WHERE id=%s",
                                (1 if is_active else 0, task_id))
        if not changed:
            raise ValueError("同步任务不存在")
        return {"task_id": task_id, "is_active": bool(is_active)}

    def stop_sync_task(self, task_id: int) -> dict:
        execute_write("UPDATE adh_sync_tasks SET is_active=0, updated_at=NOW() WHERE id=%s", (task_id,))
        cancelled = execute_write(
            "UPDATE adh_sync_logs SET status='cancelled', finished_at=NOW(), "
            "error_message='任务已被停止' WHERE sync_task_id=%s AND status='running'", (task_id,))
        return {"task_id": task_id, "disabled": True, "cancelled_runs": cancelled}

    def remove_sync_task(self, task_id: int) -> dict:
        exists = execute_query("SELECT id FROM adh_sync_tasks WHERE id=%s", (task_id,))
        if not exists:
            raise ValueError("同步任务不存在")
        self.stop_sync_task(task_id)
        execute_write("DELETE FROM adh_sync_logs WHERE sync_task_id=%s", (task_id,))
        execute_write("DELETE FROM adh_sync_tasks WHERE id=%s", (task_id,))
        return {"task_id": task_id, "removed": True}

    def kb_sync_state(self) -> list:
        """本体知识库同步水位线（模型名 + 目标 + 同步版本/状态/错误）。
        列名对齐运行时 schema：水位线表用 synced_at（无 updated_at）。
        """
        rows = execute_query(
            "SELECT s.model_id, m.name AS model_name, s.notebook_id, s.synced_version, "
            "s.status, s.error, s.synced_at "
            "FROM adh_ontology_kb_sync_state s LEFT JOIN adh_ontology_models m ON m.id=s.model_id "
            "ORDER BY s.synced_at DESC LIMIT 200")
        items = []
        for row in rows or []:
            _iso(row, "synced_at")
            if not row.get("model_name"):
                row["model_name"] = "模型已删除"
            row["error_hint"] = _error_hint("", row.get("error"))
            row.pop("error", None)
            items.append(row)
        return items


task_monitor_service = TaskMonitorService()
