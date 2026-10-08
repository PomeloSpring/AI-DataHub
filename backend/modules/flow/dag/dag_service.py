"""DAG 数据访问层 — 工作流定义 / 运行实例 / 节点实例 / 水位线。

表：adh_dag_workflows / adh_dag_runs / adh_dag_node_runs / adh_sync_watermarks。

分布式口径（distributed-first）：
- 任务表为唯一队列真值；节点认领一律条件更新（queued→running 写租约），
  两实例并发只执行一次；
- run_key 唯一键幂等，重复投递不产生重复运行；
- 水位线比较推进（advance 只前进不回退），重复投递不产生重复副作用。

失败暴露口径（no-silent-degradation）：本层不吞写入失败，SQL/约束错误直接抛出。
"""

import json
import logging
import time as _time
from datetime import datetime, timedelta
from typing import Optional

from backend.common.db import execute_query, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


def _json_loads(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def _dataflow_summary(graph: dict) -> str:
    """列表展示用数据流摘要：源表 → 目标表（UI 资源规范：显示可读名）。"""
    sources, targets = [], []
    for node in (graph or {}).get("nodes") or []:
        config = node.get("config") or {}
        if node.get("type") == "sync":
            if config.get("source_datasource") and config.get("source_table"):
                sources.append(f"{config['source_datasource']}.{config['source_table']}")
            if config.get("target_datasource") and config.get("target_table"):
                targets.append(f"{config['target_datasource']}.{config['target_table']}")
        elif node.get("type") == "sql_task":
            if config.get("source_datasource"):
                sources.append(config["source_datasource"])
            tgt = config.get("target") or {}
            if tgt.get("datasource") and tgt.get("table"):
                targets.append(f"{tgt['datasource']}.{tgt['table']}")
    src = "、".join(dict.fromkeys(sources)) or "—"
    dst = "、".join(dict.fromkeys(targets)) or "—"
    return f"{src} → {dst}"


class DagService:
    """DAG 工作流与运行实例的持久化操作。"""

    # ── Workflows ─────────────────────────────────────────────────

    def create_workflow(self, data: dict, owner_id: int, workspace_id: int = 0) -> int:
        wf_id = _generate_id()
        kind = data.get("kind") or "dag"
        if kind not in ("simple", "dag"):
            kind = "dag"
        changed = execute_write(
            "INSERT INTO adh_dag_workflows "
            "(id, name, description, kind, graph_json, version, cron_expression, timezone, "
            " is_active, workspace_id, owner_id, timeout_seconds, max_retries, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                wf_id,
                data["name"],
                data.get("description", ""),
                kind,
                json.dumps(data["graph_json"]),
                data.get("cron_expression") or None,
                data.get("timezone") or "Asia/Shanghai",
                1 if data.get("is_active", True) else 0,
                workspace_id,
                owner_id,
                int(data.get("timeout_seconds") or 3600),
                int(data.get("max_retries") or 0),
                _now(), _now(),
            ),
        )
        return wf_id if changed else 0

    def update_workflow(self, wf_id: int, data: dict, expected_version: int = None) -> bool:
        """更新工作流（graph_json 变更时 version+1，乐观锁防并发覆盖）。"""
        if not data:
            return True
        sets, params = [], []
        for key in ("name", "description", "cron_expression", "timezone"):
            if key in data:
                sets.append(f"{key} = %s")
                params.append(data[key])
        for key in ("is_active",):
            if key in data:
                sets.append(f"{key} = %s")
                params.append(1 if data[key] else 0)
        for key in ("timeout_seconds", "max_retries"):
            if key in data:
                sets.append(f"{key} = %s")
                params.append(int(data[key]))
        if "graph_json" in data:
            sets.append("graph_json = %s")
            params.append(json.dumps(data["graph_json"]))
            sets.append("version = version + 1")
        if not sets:
            return True
        sets.append("updated_at = %s")
        params.append(_now())
        params.append(wf_id)
        where = "id = %s"
        if expected_version is not None:
            where += " AND version = %s"
            params.append(expected_version)
        return execute_write(
            f"UPDATE adh_dag_workflows SET {', '.join(sets)} WHERE {where}", params) > 0

    def get_workflow(self, wf_id: int) -> Optional[dict]:
        rows = execute_query("SELECT * FROM adh_dag_workflows WHERE id = %s", (wf_id,), fetchone=True)
        if rows:
            rows["graph_json"] = _json_loads(rows.get("graph_json")) or {"nodes": [], "edges": []}
        return rows

    def list_workflows(self, workspace_id: int = 0, page: int = 1, size: int = 20) -> dict:
        where, params = "WHERE 1=1", []
        if workspace_id:
            where += " AND workspace_id = %s"
            params.append(workspace_id)
        total = execute_query(f"SELECT COUNT(*) AS total FROM adh_dag_workflows {where}",
                              params, fetchone=True)["total"]
        rows = execute_query(
            f"SELECT id, name, description, kind, graph_json, cron_expression, timezone, is_active, "
            f"version, workspace_id, owner_id, last_run_at, last_status, run_count, "
            f"timeout_seconds, max_retries, created_at, updated_at "
            f"FROM adh_dag_workflows {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [size, (page - 1) * size]) or []
        items = []
        for row in rows:
            graph = _json_loads(row.pop("graph_json", None)) or {}
            row["kind"] = row.get("kind") or "dag"
            row["dataflow_summary"] = _dataflow_summary(graph)
            items.append(row)
        return {"total": total, "page": page, "size": size, "items": items}

    def delete_workflow(self, wf_id: int) -> bool:
        """删除工作流（含运行实例）；运行中的工作流禁止删除（终态保护）。"""
        active = execute_query(
            "SELECT COUNT(*) AS total FROM adh_dag_runs "
            "WHERE workflow_id = %s AND status IN ('queued','running')",
            (wf_id,), fetchone=True)["total"]
        if active:
            raise ValueError("工作流存在运行中的实例，不能删除")
        execute_write("DELETE FROM adh_dag_node_runs WHERE workflow_id = %s", (wf_id,))
        execute_write("DELETE FROM adh_dag_runs WHERE workflow_id = %s", (wf_id,))
        return execute_write("DELETE FROM adh_dag_workflows WHERE id = %s", (wf_id,)) > 0

    def list_active_scheduled(self) -> list:
        """供 Beat 加载调度：启用中且 owner 已认领（owner_id<=0 不调度，fail-closed）。"""
        return execute_query(
            "SELECT * FROM adh_dag_workflows WHERE is_active = 1 AND cron_expression IS NOT NULL "
            " AND cron_expression != '' AND owner_id > 0") or []

    # ── Runs ──────────────────────────────────────────────────────

    def create_run(self, workflow_id: int, run_key: str, trigger_type: str,
                   workspace_id: int = 0) -> dict:
        """幂等创建运行实例（run_key 唯一键；重复投递返回既有 run）。"""
        existing = execute_query("SELECT * FROM adh_dag_runs WHERE run_key = %s",
                                 (run_key,), fetchone=True)
        if existing:
            return existing
        run_id = _generate_id()
        execute_write(
            "INSERT INTO adh_dag_runs "
            "(id, workflow_id, run_key, trigger_type, status, workspace_id, started_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s)",
            (run_id, workflow_id, run_key, trigger_type, workspace_id, _now(), _now()))
        created = execute_query("SELECT * FROM adh_dag_runs WHERE id = %s", (run_id,), fetchone=True)
        if not created:
            # 并发下被另一实例抢先创建，回到唯一键上的既有行
            created = execute_query("SELECT * FROM adh_dag_runs WHERE run_key = %s",
                                    (run_key,), fetchone=True)
        return created or {}

    def claim_run(self, run_id: int, worker_id: str, timeout_seconds: int) -> bool:
        """条件更新认领：queued→running 写租约，两实例并发只认领成功一次。"""
        lease = (datetime.now() + timedelta(seconds=max(60, timeout_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        return execute_write(
            "UPDATE adh_dag_runs SET status='running', worker_id=%s, lease_expires_at=%s "
            "WHERE id = %s AND status = 'queued'",
            (worker_id, lease, run_id)) == 1

    def renew_run_lease(self, run_id: int, timeout_seconds: int) -> bool:
        lease = (datetime.now() + timedelta(seconds=max(60, timeout_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        return execute_write(
            "UPDATE adh_dag_runs SET lease_expires_at=%s WHERE id=%s AND status='running'",
            (lease, run_id)) == 1

    def finish_run(self, run_id: int, status: str, error_code: str = "",
                   error_message: str = "", stats: dict = None) -> bool:
        """终态保护：仅 running/queued 可结束，重复投递不覆盖终态。"""
        changed = execute_write(
            "UPDATE adh_dag_runs SET status=%s, error_code=%s, error_message=%s, "
            " stats_json=%s, finished_at=%s "
            "WHERE id=%s AND status IN ('queued','running')",
            (status, error_code, error_message, json.dumps(stats or {}), _now(), run_id))
        return changed == 1

    def cancel_run(self, run_id: int) -> bool:
        return execute_write(
            "UPDATE adh_dag_runs SET status='cancelled', error_code='CANCELLED', finished_at=%s "
            "WHERE id=%s AND status IN ('queued','running')",
            (_now(), run_id)) == 1

    def get_run(self, run_id: int) -> Optional[dict]:
        row = execute_query("SELECT * FROM adh_dag_runs WHERE id = %s", (run_id,), fetchone=True)
        if row:
            row["stats_json"] = _json_loads(row.get("stats_json")) or {}
        return row

    def get_run_by_key(self, run_key: str) -> Optional[dict]:
        return execute_query("SELECT * FROM adh_dag_runs WHERE run_key = %s", (run_key,), fetchone=True)

    def list_runs(self, workflow_id: int = None, status: str = None,
                  page: int = 1, size: int = 20) -> dict:
        where, params = "WHERE 1=1", []
        if workflow_id:
            where += " AND r.workflow_id = %s"
            params.append(workflow_id)
        if status:
            where += " AND r.status = %s"
            params.append(status)
        total = execute_query(f"SELECT COUNT(*) AS total FROM adh_dag_runs r {where}",
                              params, fetchone=True)["total"]
        rows = execute_query(
            f"SELECT r.*, w.name AS workflow_name, "
            f"(SELECT COUNT(*) FROM adh_dag_node_runs n WHERE n.run_id = r.id) AS node_count "
            f"FROM adh_dag_runs r LEFT JOIN adh_dag_workflows w ON w.id = r.workflow_id "
            f"{where} ORDER BY r.created_at DESC LIMIT %s OFFSET %s",
            params + [size, (page - 1) * size])
        for row in rows or []:
            row["stats_json"] = _json_loads(row.get("stats_json")) or {}
        return {"total": total, "page": page, "size": size, "items": rows or []}

    def cleanup_stale_runs(self, timeout_minutes: int = 10) -> dict:
        """卡死清理：租约到期/超时未完成的 run 与 node 条件更新为 timeout（幂等）。"""
        cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        runs = execute_write(
            "UPDATE adh_dag_runs SET status='timeout', error_code='TIMEOUT', finished_at=%s "
            "WHERE status='running' AND (lease_expires_at <= %s OR started_at <= %s)",
            (_now(), _now(), cutoff))
        nodes = execute_write(
            "UPDATE adh_dag_node_runs SET status='timeout', error_code='TIMEOUT', finished_at=%s "
            "WHERE status='running' AND (lease_expires_at <= %s OR started_at <= %s)",
            (_now(), _now(), cutoff))
        return {"dag_runs": runs, "dag_node_runs": nodes}

    # ── Node Runs ─────────────────────────────────────────────────

    def create_node_run(self, run_id: int, workflow_id: int, node_key: str,
                        node_type: str, node_config: dict, attempt: int = 1) -> int:
        node_run_id = _generate_id()
        execute_write(
            "INSERT INTO adh_dag_node_runs "
            "(id, run_id, workflow_id, node_key, node_type, node_config, status, attempt, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s)",
            (node_run_id, run_id, workflow_id, node_key, node_type,
             json.dumps(node_config or {}), attempt, _now()))
        return node_run_id

    def claim_node_run(self, node_run_id: int, worker_id: str, timeout_seconds: int) -> bool:
        lease = (datetime.now() + timedelta(seconds=max(60, timeout_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        return execute_write(
            "UPDATE adh_dag_node_runs SET status='running', worker_id=%s, "
            " lease_expires_at=%s, started_at=%s "
            "WHERE id=%s AND status='queued'",
            (worker_id, lease, _now(), node_run_id)) == 1

    def finish_node_run(self, node_run_id: int, status: str, rows_read: int = 0,
                        rows_written: int = 0, elapsed_ms: int = 0,
                        error_code: str = "", error_message: str = "") -> bool:
        return execute_write(
            "UPDATE adh_dag_node_runs SET status=%s, rows_read=%s, rows_written=%s, "
            " elapsed_ms=%s, error_code=%s, error_message=%s, finished_at=%s "
            "WHERE id=%s AND status='running'",
            (status, rows_read, rows_written, elapsed_ms, error_code, error_message,
             _now(), node_run_id)) == 1

    def skip_node_run(self, run_id: int, node_key: str, node_type: str,
                      reason: str, attempt: int = 1) -> int:
        node_run_id = _generate_id()
        execute_write(
            "INSERT INTO adh_dag_node_runs "
            "(id, run_id, node_key, node_type, status, attempt, error_code, error_message, "
            " started_at, finished_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'skipped', %s, 'UPSTREAM_FAILED', %s, %s, %s, %s)",
            (node_run_id, run_id, node_key, node_type, attempt, reason, _now(), _now(), _now()))
        return node_run_id

    def list_node_runs(self, run_id: int) -> list:
        rows = execute_query(
            "SELECT * FROM adh_dag_node_runs WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,)) or []
        for row in rows:
            row["node_config"] = _json_loads(row.get("node_config")) or {}
        return rows

    def latest_node_status(self, run_id: int) -> dict:
        """run 内每个 node_key 的最新 attempt 状态（调度/重跑判断用）。"""
        rows = execute_query(
            "SELECT n.node_key, n.status FROM adh_dag_node_runs n "
            "JOIN (SELECT node_key, MAX(attempt) AS ma FROM adh_dag_node_runs "
            "      WHERE run_id = %s GROUP BY node_key) m "
            " ON m.node_key = n.node_key AND m.ma = n.attempt WHERE n.run_id = %s",
            (run_id, run_id)) or []
        return {row["node_key"]: row["status"] for row in rows}

    def count_attempts(self, run_id: int, node_key: str) -> int:
        return execute_query(
            "SELECT COUNT(*) AS total FROM adh_dag_node_runs WHERE run_id = %s AND node_key = %s",
            (run_id, node_key), fetchone=True)["total"]

    # ── Watermarks（增量同步水位线：比较推进，幂等） ────────────────

    def get_watermark(self, node_key: str) -> Optional[str]:
        row = execute_query(
            "SELECT watermark_value FROM adh_sync_watermarks WHERE node_key = %s",
            (node_key,), fetchone=True)
        return row.get("watermark_value") if row else None

    def advance_watermark(self, node_key: str, value: str, rows: int = 0) -> bool:
        """推进水位线：仅当新值大于当前值才前进（字符串化比较，重复投递不回退）。"""
        current = self.get_watermark(node_key)
        if current is not None and str(value) <= str(current):
            return False
        execute_write(
            "INSERT INTO adh_sync_watermarks (id, node_key, watermark_value, last_rows, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE watermark_value = IF(VALUES(watermark_value) > watermark_value, "
            " VALUES(watermark_value), watermark_value), last_rows = VALUES(last_rows)",
            (_generate_id(), node_key, value, rows, _now(), _now()))
        return True

    # ── 调度运行统计回写 ─────────────────────────────────────────

    def mark_run_stats(self, workflow_id: int, status: str) -> None:
        execute_write(
            "UPDATE adh_dag_workflows SET last_run_at=%s, last_status=%s, "
            " run_count = run_count + 1 WHERE id=%s",
            (_now(), status, workflow_id))


dag_service = DagService()
