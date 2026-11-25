"""DAG 执行核心 — 运行实例的调度、节点执行、重试与状态聚合。

分布式口径（distributed-first）：
- run/node 认领一律条件更新 + 租约，两实例并发只执行一次；
- run_key 幂等（调用方在派发前生成），重复投递不产生重复运行；
- 停止 = 条件更新终态保护，执行侧轮询 run 状态中止。

失败暴露口径（no-silent-degradation / 护栏 §7）：
- 节点失败显式记 failed + error_code；业务可诊断文案对外，原始报错只进
  服务端日志（不含连接串/凭据/数据行）；
- 失败传播下游 skipped 并记录原因，run 聚合 partial/failed，绝不假成功。
"""

import logging
import threading
import time
from datetime import datetime

from backend.modules.flow.dag import dag_planner
from backend.modules.flow.dag.dag_service import (
    dag_service, RUN_HEARTBEAT_INTERVAL_SECONDS, RUN_HEARTBEAT_LEASE_SECONDS,
)
from backend.modules.flow.dag.dag_validator import validate_graph, DagValidationError
from backend.modules.flow.dag.node_runners import run_node, NodeExecutionError

logger = logging.getLogger(__name__)

_EXEC_FAIL_HINT = "节点执行失败，详情见服务端日志"


class RunStopped(Exception):
    """运行被停止/超时（status: cancelled / timeout）。"""

    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


def _public_error(exc: Exception) -> tuple:
    """对外错误 = 可诊断但脱敏（护栏 §7）；原始细节由调用方 logger.exception 记录。"""
    if isinstance(exc, NodeExecutionError):
        return exc.error_code, str(exc)[:500]
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED", str(exc)[:500]
    if isinstance(exc, (ValueError, DagValidationError)):
        return "CONFIG_INVALID", str(exc)[:500]
    return "NODE_FAILED", _EXEC_FAIL_HINT


_RESULT_KEEP_KEYS = (
    "dataset", "target", "write_mode", "partitions", "reconciled", "strategy",
    "budget", "quality", "data_product", "catalog", "udf_versions", "window",
    "last_committed_date",
)


def _result_summary(result: dict) -> dict:
    """节点结果摘要（落 adh_dag_node_runs.result_json 供执行日志展示）。

    白名单取字段：只留平台自产的结构化指标（分区/对账/策略/预算/质量），
    不落数据行、列明细、生成 SQL 与凭据（护栏 §7）；列名清单只回数量。
    """
    if not isinstance(result, dict):
        return {}
    summary = {k: result[k] for k in _RESULT_KEEP_KEYS if result.get(k) not in (None, "", [], {})}
    columns = result.get("columns")
    if isinstance(columns, list) and columns:
        summary["column_count"] = len(columns)
    return summary


def _resolve_owner(workflow: dict) -> dict:
    owner_id = int(workflow.get("owner_id") or 0)
    if owner_id <= 0:
        raise ValueError("工作流未认领创建者，无法执行（OWNER_REQUIRED）")
    from backend.common.db import execute_query
    row = execute_query("SELECT username, user_role FROM adh_users WHERE id = %s",
                        (owner_id,), fetchone=True) or {}
    # role 必须随身份下发（authorize_workspace 对 admin 与普通用户口径不同，
    # 缺 role 会被误判为非 admin 而拒绝 ws=0 的全局域操作）
    return {"user_id": owner_id, "username": row.get("username") or "",
            "role": row.get("user_role") or ""}


def execute_run_core(run_id: int, worker_id: str = "local") -> dict:
    """执行一个 DAG 运行实例（Celery/后台/同步适配器共用执行核心）。"""
    run = dag_service.get_run(run_id)
    if not run:
        return {"status": "failed", "error_code": "RUN_NOT_FOUND"}
    workflow = dag_service.get_workflow(run["workflow_id"])
    if not workflow:
        dag_service.finish_run(run_id, "failed", "WORKFLOW_NOT_FOUND", "工作流不存在")
        return {"status": "failed", "error_code": "WORKFLOW_NOT_FOUND"}

    timeout_seconds = max(60, int(workflow.get("timeout_seconds") or 3600))
    max_retries = max(0, int(workflow.get("max_retries") or 0))

    if not dag_service.claim_run(run_id, worker_id, timeout_seconds):
        current = dag_service.get_run(run_id) or {}
        return {"run_id": run_id, "status": current.get("status"), "duplicate": True}

    # 心跳续租：认领时给的租约是整轮预算（最多 1 小时），执行体跑起来后由本线程
    # 按短租约续期。为什么要这么绕：worker 被杀 / 机器下线 / background 线程随 web
    # 重启时没有任何人改状态，短租约能让残留实例在 3 分钟内被判死并收口成 timeout，
    # 而不是挂着「运行中」等到整轮预算用尽（单节点预算 ≤1800s 由心跳覆盖，不会误杀）。
    heartbeat = threading.Event()

    def _renew_lease():
        while not heartbeat.wait(RUN_HEARTBEAT_INTERVAL_SECONDS):
            try:
                dag_service.renew_run_lease(run_id, RUN_HEARTBEAT_LEASE_SECONDS)
            except Exception:  # noqa: BLE001 续租失败不能中断执行，下一轮再试
                logger.warning("run %s 租约续期失败（不影响执行，下一轮再试）", run_id)

    threading.Thread(target=_renew_lease, name=f"dag-lease-{run_id}", daemon=True).start()

    start = time.monotonic()
    deadline = start + timeout_seconds
    status_map: dict = {}
    stats = {"nodes_total": 0, "nodes_success": 0, "nodes_failed": 0,
             "nodes_skipped": 0, "rows_read": 0, "rows_written": 0}

    def stop_check():
        if time.monotonic() > deadline:
            raise RunStopped("timeout")
        current = dag_service.get_run(run_id) or {}
        if current.get("status") == "cancelled":
            raise RunStopped("cancelled")

    try:
        try:
            graph = validate_graph(workflow["graph_json"])
        except DagValidationError as exc:
            logger.exception("run %s 图校验失败", run_id)
            dag_service.finish_run(run_id, "failed", "GRAPH_INVALID", str(exc)[:500])
            return {"run_id": run_id, "status": "failed", "error_code": "GRAPH_INVALID"}

        owner_identity = _resolve_owner(workflow)
        # 本次运行参数（物化时间窗口）：服务端落库、此处注入节点 context。
        # 它不参与图校验（图是“怎么跑”，参数是“跑哪一段”）。
        run_params = dict(run.get("params_json") or {})
        node_map = {str(n["key"]): n for n in graph["nodes"]}
        stats["nodes_total"] = len(node_map)

        while True:
            dag_planner.propagate_skips(graph, status_map, dag_service, run_id)
            ready = dag_planner.ready_nodes(graph, status_map)
            if not ready:
                break
            for key in ready:
                stop_check()
                dag_service.renew_run_lease(run_id, RUN_HEARTBEAT_LEASE_SECONDS)
                node = node_map[key]
                node_status, node_stats = _execute_node_with_retry(
                    run_id=run_id,
                    workflow_id=workflow["id"],
                    node=node,
                    owner_identity=owner_identity,
                    workspace_id=int(workflow.get("workspace_id") or 0),
                    watermark_key=f"dag:{workflow['id']}:{key}",
                    deadline=deadline,
                    max_retries=max_retries,
                    worker_id=worker_id,
                    stop_check=stop_check,
                    run_params=run_params,
                )
                status_map[key] = node_status
                stats["rows_read"] += node_stats.get("rows_read", 0)
                stats["rows_written"] += node_stats.get("rows_written", 0)
                if node_status == "success":
                    stats["nodes_success"] += 1
                elif node_status == "skipped":
                    # 跳过单独汇总（nodes_skipped），不得计入失败
                    pass
                else:
                    stats["nodes_failed"] += 1

        dag_planner.propagate_skips(graph, status_map, dag_service, run_id)
        stats["nodes_skipped"] = sum(1 for s in status_map.values() if s == "skipped")
        final = dag_planner.run_status_aggregate(status_map)
        dag_service.finish_run(run_id, final, "", "", stats)
        dag_service.mark_run_stats(workflow["id"], final)
        return {"run_id": run_id, "status": final, "stats": stats}

    except RunStopped as exc:
        stats["nodes_skipped"] = sum(1 for s in status_map.values() if s == "skipped")
        dag_service.finish_run(run_id, exc.status, exc.status.upper(),
                               f"运行已{exc.status}", stats)
        dag_service.mark_run_stats(workflow["id"], exc.status)
        return {"run_id": run_id, "status": exc.status}
    except Exception as exc:
        logger.exception("run %s 执行异常", run_id)
        code, message = _public_error(exc)
        dag_service.finish_run(run_id, "failed", code, message, stats)
        dag_service.mark_run_stats(workflow["id"], "failed")
        return {"run_id": run_id, "status": "failed", "error_code": code}
    finally:
        # 无论正常结束/被终止/抛错都要停心跳，否则线程会一直续租已终结的 run
        heartbeat.set()


def _execute_node_with_retry(run_id: int, workflow_id: int, node: dict,
                             owner_identity: dict, workspace_id: int,
                             watermark_key: str, deadline: float,
                             max_retries: int, worker_id: str, stop_check,
                             run_params: dict | None = None) -> tuple:
    """执行单节点（含重试）。返回 (node_status, node_stats)。"""
    key = str(node["key"])
    ntype = node.get("type", "unknown")
    config = node.get("config") or {}
    node_stats = {"rows_read": 0, "rows_written": 0}
    attempt = 0

    while True:
        attempt += 1
        node_run_id = dag_service.create_node_run(
            run_id, workflow_id, key, ntype, config, attempt=attempt)
        remaining = max(30, int(deadline - time.monotonic()))
        if not dag_service.claim_node_run(node_run_id, worker_id, remaining):
            logger.warning("节点 %s attempt %s 认领失败（已被其他实例执行）", key, attempt)
            return "failed", node_stats

        context = {
            "run_id": run_id, "workflow_id": workflow_id, "node_key": key,
            "owner_identity": owner_identity, "workspace_id": workspace_id,
            "watermark_key": watermark_key, "stop_check": stop_check,
            "run_params": dict(run_params or {}),
        }
        start = time.monotonic()
        try:
            result = run_node(node, context)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            node_stats["rows_read"] = int(result.get("rows_read") or 0)
            node_stats["rows_written"] = int(result.get("rows_written") or 0)
            if result.get("skipped"):
                # 运行参数主动跳过的粒度（如历史初始化只跑月/年档）：记 skipped
                # 而不是 success —— “零写入的成功”会让运行页看不出哪些档位没跑。
                dag_service.finish_node_run(
                    node_run_id, "skipped",
                    elapsed_ms=elapsed_ms, result=_result_summary(result),
                    error_code="RUN_PARAM_SKIPPED",
                    error_message=str((result.get("window") or {}).get("reason") or "")[:500])
                return "skipped", node_stats
            dag_service.finish_node_run(
                node_run_id, "success",
                rows_read=node_stats["rows_read"], rows_written=node_stats["rows_written"],
                elapsed_ms=elapsed_ms, result=_result_summary(result))
            return "success", node_stats
        except RunStopped:
            dag_service.finish_node_run(node_run_id, "cancelled",
                                        error_code="CANCELLED", error_message="运行已中止")
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.exception("节点 %s attempt %s 执行失败", key, attempt)
            code, message = _public_error(exc)
            dag_service.finish_node_run(node_run_id, "failed",
                                        rows_read=node_stats["rows_read"],
                                        elapsed_ms=elapsed_ms,
                                        error_code=code, error_message=message)
            if attempt <= max_retries:
                logger.info("节点 %s 第 %s 次重试", key, attempt)
                continue
            return "failed", node_stats


def dispatch_run(workflow_id: int, trigger_type: str = "manual",
                 run_key: str = None, params: dict | None = None) -> dict:
    """创建运行实例并派发（手动/重跑入口；cron 派发走 beat 的 run_key）。

    `params` 是本次运行的参数（物化时间窗口 `start_date`/`end_date`），
    调用方（API）必须先经 `dag_validator.validate_run_params` 校验再传进来。

    幂等：run_key 冲突时返回既有 run，不重复派发。
    """
    import uuid
    workflow = dag_service.get_workflow(workflow_id)
    if not workflow:
        raise ValueError("工作流不存在")
    key = run_key or f"dagrun:{workflow_id}:{uuid.uuid4().hex}"
    run = dag_service.create_run(workflow_id, key, trigger_type,
                                 int(workflow.get("workspace_id") or 0), params=params)
    if not run:
        raise RuntimeError("运行实例创建失败")
    if run.get("status") != "queued":
        return {"run_id": run["id"], "run_key": key, "status": run.get("status"), "duplicate": True}

    import os
    mode = os.getenv("ADH_TASK_EXECUTION_MODE", "celery")
    timeout = max(60, int(workflow.get("timeout_seconds") or 3600))
    try:
        if mode == "background":
            import threading
            threading.Thread(
                target=execute_run_core, args=(run["id"], "async-bg"), daemon=True
            ).start()
        elif mode == "celery":
            from backend.modules.flow.tasks.celery_app import app as celery_app
            celery_app.send_task(
                "backend.modules.flow.tasks.dag_tasks.execute_dag_run",
                args=(run["id"],), task_id=key, queue="scheduled",
                soft_time_limit=timeout, time_limit=timeout + 60)
        else:
            raise ValueError(f"不支持的执行适配器: {mode}")
    except Exception:
        logger.exception("DAG 运行派发失败 run=%s", run["id"])
        dag_service.finish_run(run["id"], "failed", "DISPATCH_FAILED", "任务队列暂不可用")
        raise
    return {"run_id": run["id"], "run_key": key, "status": "queued", "mode": mode}


def execute_dag_run_sync(run_id: int, worker_id: str = "sync-process") -> dict:
    """显式同步适配器（测试/本地调试用）。"""
    return execute_run_core(run_id, worker_id)
