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
    from backend.modules.flow.dag.dag_executor import execute_run_core
    worker_id = self.request.hostname or "celery"
    return execute_run_core(int(run_id), worker_id)


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
