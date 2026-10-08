"""评测的 Celery 任务（业务评测：需真实检索链路 / 真实 LLM，耗时较长）。

编排逻辑全部在 ``backend.eval.runner``；本模块只负责
**派发身份 + 失败可见 + 幂等**三件事：

* 幂等：``run_key`` 用 Celery task id，重复投递不会产生第二条运行记录
  （分布式约束：重复投递不得产生重复副作用）。
* 失败可见：整条任务崩掉也要落一条 ``status='failed'`` 的运行记录，
  让页面看得到"跑挂了"，而不是列表里凭空少一条（no-silent-degradation §1）。
* 不吞异常：落完失败记录后仍 re-raise，让 Celery 侧也记录失败。
"""

from __future__ import annotations

import logging

from backend.modules.flow.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="backend.modules.flow.tasks.eval_tasks.run_eval",
    bind=True,
    queue="scheduled",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_eval(self, suite: str = "all", trigger_type: str = "celery", created_by: str = ""):
    """跑一次评测并落库。suite 可为 compile/retrieval/llm/all。"""
    from backend.eval import store
    from backend.eval.contract import SUITES
    from backend.eval.runner import run_all, run_suite

    run_key = f"eval-{self.request.id}" if self.request and self.request.id else ""
    targets = list(SUITES) if suite == "all" else [suite]
    for s in targets:
        if s not in SUITES:
            raise ValueError(f"未知评测集: {s!r}")

    try:
        if suite == "all":
            out = run_all(trigger_type=trigger_type, created_by=created_by or "celery",
                          persist=True)
        else:
            out = run_suite(suite, trigger_type=trigger_type,
                            created_by=created_by or "celery", persist=True,
                            run_key=run_key)
    except Exception as exc:  # noqa: BLE001 —— 崩了也要让失败在页面可见
        logger.exception("[eval] 评测任务失败 suite=%s", suite)
        _record_failed(targets, trigger_type, created_by, run_key, exc)
        raise

    logger.info("[eval] 评测完成 suite=%s baseline_ok=%s", suite,
                out.get("baseline_ok") if isinstance(out, dict) else "?")
    return {"ok": True, "suite": suite,
            "baseline_ok": out.get("baseline_ok") if isinstance(out, dict) else None}


def _record_failed(targets, trigger_type, created_by, run_key, exc) -> None:
    """落失败记录，让管理页能看到"跑挂了"及原因（不静默）。"""
    try:
        from backend.eval import store
        for s in targets:
            key = run_key or f"eval-fail-{s}-{int(__import__('time').time())}"
            rid = store.create_run(s, trigger_type=trigger_type,
                                   created_by=created_by or "celery", run_key=key)
            store.finish_run(rid, total=0, passed=0, accuracy=0.0,
                             by_tag={}, by_source={}, baseline_ok=False,
                             error=f"{type(exc).__name__}: {exc}")
    except Exception:  # noqa: BLE001 —— 连失败记录都写不进去时，只剩日志可诊断
        logger.exception("[eval] 连失败记录都落不了库，评测结果不可见，请立即排查")
