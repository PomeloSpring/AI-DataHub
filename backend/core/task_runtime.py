"""L1 任务运行时 — 可中止等待守卫 + 任务壳回调表（分层白名单②）。

- :func:`guarded_call` / :class:`RunInterrupted`：后台任务的可中止等待，
  逻辑取消不等待阻塞驱动，也不发布迟到结果。
- 回调表：L2 领域能力以 ``"backend.modules.<域>.<模块>:<属性>"`` 字符串登记，
  任务壳（flow）与跨域消费方按名解析调用。backend.core 不静态 import
  backend.modules；解析发生在调用时（fail-loud，未登记/未实现即抛），
  取属性亦在调用时，保持对提供方 monkeypatch 的测试语义。
- :func:`send_task`：按名派发 Celery 任务，生产方不见任务对象；
  Celery app 同样以 ``module:attr`` 绑定解析。
"""
import contextvars
import queue
import threading
import time
from importlib import import_module
from typing import Any, Callable


class RunInterrupted(Exception):
    def __init__(self, status="cancelled"):
        self.status = status
        super().__init__(status)


def guarded_call(operation, *args, check=None, deadline=None, stop_event=None, **kwargs):
    """每次进入阻塞操作前后检查租约；线程只计算结果，持久化由调用者负责。

    无法强杀第三方驱动中已经发送的只读查询；取消后丢弃结果并禁止下一操作。
    守护线程不会阻止宿主关闭，真实数据源仍受统一执行器的查询超时约束。
    """
    stop_event = stop_event or threading.Event()
    def verify():
        if stop_event.is_set():
            raise RunInterrupted("cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise RunInterrupted("timeout")
        if check:
            check()
    verify()
    result = queue.Queue(maxsize=1)
    context = contextvars.copy_context()
    def run():
        try:
            verify()
            result.put((True, operation(*args, **kwargs)))
        except BaseException as error:
            result.put((False, error))
    threading.Thread(target=context.run, args=(run,), daemon=True).start()
    try:
        while True:
            verify()
            try:
                ok, value = result.get(timeout=0.1)
            except queue.Empty:
                continue
            verify()
            if not ok:
                raise value
            return value
    except (RunInterrupted, KeyboardInterrupt):
        stop_event.set()
        raise


# ── 任务壳回调表（白名单②：core/task_runtime 对 modules 的注册式调用）──
# 登记格式 "backend.modules.<域>.<模块>:<属性>"；消费方按名取回调，禁止直接 import 领域模块。
_CALLBACKS: dict[str, str | Callable[..., Any]] = {
    # 报表域（viz）
    "report.create": "backend.modules.viz.services.report_service:create_report",
    "report.get": "backend.modules.viz.services.report_service:get_report",
    "report.list": "backend.modules.viz.services.report_service:list_reports",
    "report.run": "backend.modules.viz.services.report_service:run_report",
    "report.render_fact": "backend.modules.viz.services.report_service:render_fact_report",
    "report.security_snapshot": "backend.modules.viz.services.report_service:_snapshot",
    "report.verify_snapshot": "backend.modules.viz.services.report_service:_verify_snapshot",
    "report.cleanup_stale": "backend.modules.viz.services.report_service:cleanup_stale_reports",
    "report.policy_snapshot": "backend.modules.viz.services.report_access:policy_snapshot",
    "report.load": "backend.modules.viz.services.report_service:load_report",
    "report.submit": "backend.modules.viz.services.report_service:submit_report",
    "report.submission_model": "backend.modules.viz.services.report_service:ReportSubmission",
    # 无人值守分析域（mind）
    "mind.analyze_question": "backend.modules.mind.execution.scheduled_analysis:analyze_question",
    "mind.validate_task_as_bot": "backend.modules.mind.execution.scheduled_analysis:validate_task_as_bot",
    "mind.needs_as_bot_migration": "backend.modules.mind.execution.scheduled_analysis:needs_as_bot_migration",
    "mind.as_bot_options": "backend.modules.mind.execution.scheduled_analysis:as_bot_options",
    # 治理域（gov）
    "gov.persist_sql_lineage": "backend.modules.gov.services.lineage_service:persist_sql_lineage",
    "gov.execute_quality_rule": "backend.modules.gov.services.quality_engine:execute_single_rule",
    # 数据产品域（catalog）
    "catalog.data_product_service": "backend.modules.catalog.services:data_product_service",
}

# Celery app 绑定：任务派发与任务对象解耦（同一白名单②通道）。
_CELERY_APP_REF = "backend.modules.flow.tasks.celery_app:app"


def _resolve(ref: str | Callable[..., Any]) -> Any:
    if callable(ref):
        return ref
    module_path, _, attr = ref.partition(":")
    return getattr(import_module(module_path), attr)


def register_callback(name: str, target: str | Callable[..., Any]) -> None:
    """运行期注册/覆盖回调绑定（装配处扩展用）；未知名字的消费方直接 fail-loud。"""
    _CALLBACKS[name] = target


def get_callback(name: str) -> Callable[..., Any]:
    """按名解析回调；未登记即 KeyError，登记的属性不存在即 AttributeError，均不降级。"""
    if name not in _CALLBACKS:
        raise KeyError(f"任务壳回调未登记: {name}")
    return _resolve(_CALLBACKS[name])


def call(name: str, *args, **kwargs):
    return get_callback(name)(*args, **kwargs)


def send_task(name: str, args: tuple = (), task_id: str | None = None, **options):
    """按名派发 Celery 任务（白名单②）；broker 不可达等异常原样抛给调用方。"""
    app = _resolve(_CELERY_APP_REF)
    return app.send_task(name, args=args, task_id=task_id, **options)
