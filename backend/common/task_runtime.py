"""后台任务的可中止等待：逻辑取消不等待阻塞驱动，也不发布迟到结果。"""
import contextvars
import queue
import threading
import time


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
