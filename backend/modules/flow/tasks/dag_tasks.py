"""Celery 任务入口 — DAG 运行执行（多 Worker 可水平扩展）。

Celery 仅适配派发身份；run 实例在派发前已落 adh_dag_runs（run_key 幂等），
执行核心在 backend/modules/flow/dag/dag_executor（claim 租约认领，重投安全）。
"""

import logging

from backend.modules.flow.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="backend.modules.flow.tasks.dag_tasks.execute_dag_run",
    bind=True,
    queue="scheduled",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_dag_run(self, run_id: int):
    """执行一次 DAG 运行；执行体自身崩掉时把 run 收口，不留永远「排队中」的僵尸。

    ``execute_run_core`` 只负责节点级失败；导入失败/环境不一致/低层异常会让任务在
    进入核心前就抛（历史事故：worker 跑旧代码 → ImportError → run 一直 queued，
    而收取口只扫 running，没人能动它）。这里兜底成 failed/EXECUTOR_ERROR 并保留
    原始异常上抛（日志与 celery 监控可见），不假装成功。
    """
    run_id = int(run_id)
    worker_id = self.request.hostname or "celery"
    try:
        from backend.modules.flow.dag.dag_executor import execute_run_core
        return execute_run_core(run_id, worker_id)
    except Exception:  # noqa: BLE001 任何执行体异常都必须落到 run 状态上
        logger.exception("DAG run %s 执行体崩溃（worker=%s）", run_id, worker_id)
        try:
            from backend.modules.flow.dag.dag_service import dag_service
            dag_service.finish_run(
                run_id, "failed", "EXECUTOR_ERROR",
                "执行进程异常（未产生节点结果）：执行体崩溃或代码版本不一致，详见服务端日志")
        except Exception:  # noqa: BLE001 收口失败只记日志，不得吞掉原始异常
            logger.exception("DAG run %s 崩溃后收口失败", run_id)
        raise


@app.task(
    name="backend.modules.flow.tasks.dag_tasks.execute_sync_task",
    bind=True,
    queue="scheduled",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_sync_task(self, task_id: int, run_key: str = None):
    from backend.modules.flow.dag.sync_task_runner import execute_sync_task_core
    worker_id = self.request.hostname or "celery"
    return execute_sync_task_core(int(task_id), "cron", run_key or self.request.id, worker_id)
