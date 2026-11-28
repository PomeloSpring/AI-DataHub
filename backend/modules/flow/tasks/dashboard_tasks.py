"""看板整板刷新 Celery 适配器。

调度与业务编排分离：本文件只接任务身份，实际配置、互斥、取数与缓存写入都在
viz 域经 task_runtime 回调执行，避免 flow → viz 的横向静态依赖。
"""
from __future__ import annotations

from backend.core.task_runtime import call
from backend.modules.flow.tasks.celery_app import app


@app.task(
    name="backend.modules.flow.tasks.dashboard_tasks.refresh_dashboard",
    bind=True,
    queue="dashboard_refresh",
    acks_late=True,
    reject_on_worker_lost=True,
)
def refresh_dashboard(self, run_id: int, trigger_type: str = ""):
    # trigger_type 由 Beat 入口携带，仅用于运行实例展示；执行口径以库内 run 为准。
    del trigger_type
    return call("dashboard_refresh.execute", int(run_id), self.request.hostname or "celery")
