"""同步任务执行核心 — adh_sync_tasks 的单节点快捷形态。

与 DAG sync 节点共用 node_runners.run_sync_node 执行器（消除双执行核心）；
执行实例落 adh_sync_logs（claim 租约认领，run_key 幂等）。
失败显式记 failed + error_code，对外脱敏文案，原始细节仅进服务端日志。
"""

import json
import logging
import time
from datetime import datetime, timedelta

from services.shared.common.db import execute_query, execute_write

from services.dataflow.dag.node_runners import run_sync_node, NodeExecutionError
from services.dataflow.dag.dag_executor import _public_error

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(time.time() * 1000000)


def execute_sync_task_core(task_id: int, trigger_type: str = "manual",
                           run_key: str = None, worker_id: str = "local") -> dict:
    """执行同步任务（cron/手动共用）。run_key 幂等：重投复用既有执行实例。"""
    task = execute_query("SELECT * FROM adh_sync_tasks WHERE id = %s",
                         (task_id,), fetchone=True)
    if not task:
        return {"status": "failed", "error_code": "TASK_NOT_FOUND"}

    key = run_key or f"sync:{task_id}:{_generate_id()}"
    log = execute_query("SELECT * FROM adh_sync_logs WHERE dag_run_id = %s",
                        (key,), fetchone=True)
    if log:
        if log.get("status") != "queued":
            return {"log_id": log["id"], "status": log.get("status"), "duplicate": True}
        log_id = log["id"]
    else:
        log_id = _generate_id()
        execute_write(
            "INSERT INTO adh_sync_logs (id, sync_task_id, workspace_id, dag_run_id, "
            " status, trigger_type, started_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s)",
            (log_id, task_id, int(task.get("workspace_id") or 0), key,
             trigger_type, _now(), _now()))

    timeout_seconds = max(60, int(task.get("timeout_seconds") or 1800))
    lease = (datetime.now() + timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    claimed = execute_write(
        "UPDATE adh_sync_logs SET status='running', worker_id=%s, lease_expires_at=%s "
        "WHERE id=%s AND status='queued'", (worker_id, lease, log_id))
    if claimed != 1:
        current = execute_query("SELECT status FROM adh_sync_logs WHERE id = %s",
                                (log_id,), fetchone=True) or {}
        return {"log_id": log_id, "status": current.get("status"), "duplicate": True}

    start = time.time()
    owner_id = int(task.get("owner_id") or 0)
    try:
        if trigger_type == "cron" and not task.get("is_active"):
            _finish(log_id, task_id, "cancelled", "CANCELLED", "任务已停用")
            return {"log_id": log_id, "status": "cancelled"}
        if owner_id <= 0:
            raise NodeExecutionError("同步任务未认领创建者，无法执行（OWNER_REQUIRED）",
                                     "OWNER_REQUIRED")
        username_row = execute_query(
            "SELECT username, user_role FROM adh_users WHERE id = %s",
            (owner_id,), fetchone=True) or {}
        # role 必须随身份下发（authorize_workspace 的 admin/普通用户口径依赖它）
        owner_identity = {"user_id": owner_id, "username": username_row.get("username") or "",
                          "role": username_row.get("user_role") or ""}

        task_config = task.get("task_config") or {}
        if isinstance(task_config, str):
            try:
                task_config = json.loads(task_config)
            except (json.JSONDecodeError, TypeError):
                raise NodeExecutionError("任务配置不是合法 JSON", "CONFIG_INVALID")
        if not isinstance(task_config, dict):
            task_config = {}
        node = {
            "type": "sync",
            "config": {
                "source_datasource": task["source_datasource_name"],
                "source_table": task["source_table"],
                "target_datasource": task["target_datasource_name"],
                "target_table": task["target_table"],
                "sync_mode": task.get("sync_mode") or "full",
                "incremental_column": task.get("incremental_column"),
                "write_mode": task_config.get("write_mode", "append"),
                "transform_sql": task_config.get("transform_sql") or "",
                "udf_refs": task_config.get("udf_refs") or [],
                "batch_size": 1000,
            },
        }
        deadline = start + timeout_seconds

        def stop_check():
            if time.monotonic() > deadline:
                raise NodeExecutionError("执行超时", "TIMEOUT")
            current = execute_query("SELECT status FROM adh_sync_logs WHERE id = %s",
                                    (log_id,), fetchone=True) or {}
            if current.get("status") == "cancelled":
                raise NodeExecutionError("任务已被停止", "CANCELLED")

        result = run_sync_node(node["config"], {
            "run_id": log_id, "workflow_id": 0, "node_key": f"sync_task:{task_id}",
            "owner_identity": owner_identity,
            "workspace_id": int(task.get("workspace_id") or 0),
            "watermark_key": f"sync_task:{task_id}",
            "stop_check": stop_check,
        })
        elapsed_ms = int((time.time() - start) * 1000)
        execute_write(
            "UPDATE adh_sync_logs SET status='success', rows_read=%s, rows_written=%s, "
            " records_synced=%s, elapsed_ms=%s, finished_at=%s WHERE id=%s AND status='running'",
            (result.get("rows_read", 0), result.get("rows_written", 0),
             result.get("rows_written", 0), elapsed_ms, _now(), log_id))
        _mark_task(task_id, "success")
        return {"log_id": log_id, "status": "success", **result}
    except Exception as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.exception("同步任务 %s 执行失败", task_id)
        code, message = _public_error(exc)
        status = "timeout" if code == "TIMEOUT" else (
            "cancelled" if code == "CANCELLED" else "failed")
        _finish(log_id, task_id, status, code, message, elapsed_ms)
        return {"log_id": log_id, "status": status, "error_code": code}


def _finish(log_id: int, task_id: int, status: str, error_code: str,
            error_message: str, elapsed_ms: int = 0) -> None:
    execute_write(
        "UPDATE adh_sync_logs SET status=%s, error_code=%s, error_message=%s, "
        " elapsed_ms=%s, finished_at=%s WHERE id=%s AND status IN ('queued','running')",
        (status, error_code, error_message, elapsed_ms, _now(), log_id))
    _mark_task(task_id, status)


def _mark_task(task_id: int, status: str) -> None:
    execute_write(
        "UPDATE adh_sync_tasks SET last_run_at=%s, last_status=%s, "
        " run_count = run_count + 1 WHERE id=%s", (_now(), status, task_id))
