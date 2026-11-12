"""决策建议定时预警的任务壳（Beat 第五个调度源的派发目标）。

为什么任务壳在 flow 而编排在 decision：Celery 任务壳与 Beat 条目一直由调度域
（flow/tasks）持有，业务模块只出自己的函数；两侧经 ``task_runtime`` 回调表对接，
不新增 L2 横向 import 边（分层契约 §0，先例见 evolution.run_digest）。

不动 `adh_scheduled_tasks.task_type`：那张表的任务形态仍只有 agent（api/scheduled.py
显式拒非 agent 形态，是既有产品决策）。决策树的 Cron 存在 `adh_decision_trees.schedule_cron`，
由 Beat 直接读，两套调度不互相冒充。
"""

import logging

from backend.core.task_runtime import call
from backend.modules.flow.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="backend.modules.flow.tasks.decision_tasks.evaluate_and_notify",
          queue="scheduled")
def evaluate_and_notify(tree_id: int, run_key: str = ""):
    """按树配置的 Cron 跑一次完整评估；只有命中才发通知。

    ``run_key`` 是墙钟分钟幂等键（Beat 派发时生成并传入）：同一分钟重投不再发
    第二条预警。业务编排与通知发送都在 decision/scheduler 里完成，本壳只做：
    幂等复查 → 调用 → 失败照抛（no-silent-degradation：预警坏了必须看得见）。
    """
    if run_key and call("decision.dispatched", run_key):
        logger.info("[DecisionTask] run_key=%s 已执行过，跳过重复预警", run_key)
        return {"skipped": "duplicate_run"}
    try:
        out = call("decision.scheduled_run", int(tree_id or 0), run_key=run_key or "")
    except Exception:  # noqa: BLE001
        logger.exception("[DecisionTask] 定时评估失败 tree_id=%s", tree_id)
        raise
    if isinstance(out, dict) and out.get("skipped"):
        logger.info("[DecisionTask] 本轮未评估 tree_id=%s 原因=%s", tree_id, out["skipped"])
    return out
