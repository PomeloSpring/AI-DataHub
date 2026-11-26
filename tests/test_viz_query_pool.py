"""看板取数专用有界通道（`backend/core/viz_query_pool.py`）的口径用例。

锁四件事（都是"看板查询拖死主应用"的直接防线）：
1. 并发度/队列上限/排队预算只能由显式配置决定，非法值必须报错（不静默吃配置）；
2. 队列满了**立即拒绝**（`VizQueryBusy`），不排进看不见的内部队列；
3. 排到队时已过预算**执行前取消**（`VizQueryExpired`），不拿过期点击去跑；
4. 任务结束（无论成功/失败）在途计数必须归还，否则通道会被"永久占满"。
"""
import asyncio
import threading
import time

import pytest

from backend.core import viz_query_pool as pool


@pytest.fixture(autouse=True)
def _clean_pool(monkeypatch):
    for name in ("VIZ_CHART_WORKERS", "VIZ_CHART_MAX_QUEUE", "VIZ_CHART_QUEUE_WAIT_SEC"):
        monkeypatch.delenv(name, raising=False)
    pool.reset_for_tests()
    yield
    pool.reset_for_tests()


def test_defaults_come_from_constants():
    assert pool.workers() == pool.DEFAULT_WORKERS
    assert pool.max_queue() == pool.DEFAULT_MAX_QUEUE
    assert pool.queue_wait_sec() == pool.DEFAULT_QUEUE_WAIT_SEC


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("VIZ_CHART_WORKERS", "2")
    monkeypatch.setenv("VIZ_CHART_MAX_QUEUE", "0")
    monkeypatch.setenv("VIZ_CHART_QUEUE_WAIT_SEC", "0")
    assert pool.stats()["workers"] == 2
    assert pool.max_queue() == 0
    assert pool.queue_wait_sec() == 0


@pytest.mark.parametrize("raw", ["abc", "  ", "-1"])
def test_illegal_env_is_loud(monkeypatch, raw):
    if raw.strip() == "":
        # 空串是"未配置"，按默认值处理（区别于非法值）
        monkeypatch.setenv("VIZ_CHART_WORKERS", raw)
        assert pool.workers() == pool.DEFAULT_WORKERS
        return
    monkeypatch.setenv("VIZ_CHART_WORKERS", raw)
    with pytest.raises(ValueError):
        pool.workers()


def test_queue_full_is_rejected_immediately(monkeypatch):
    monkeypatch.setenv("VIZ_CHART_WORKERS", "1")
    monkeypatch.setenv("VIZ_CHART_MAX_QUEUE", "0")
    gate = threading.Event()
    fut = pool.submit(gate.wait, 5)
    try:
        # 通道容量 = 1 线程 + 0 队列：第一个还在跑，第二个必须当场被拒
        with pytest.raises(pool.VizQueryBusy) as exc:
            pool.submit(lambda: None)
        assert "看板取数通道已满" in str(exc.value)
    finally:
        gate.set()
        fut.result(timeout=5)
    assert pool.stats()["pending"] == 0


def test_queue_wait_budget_cancels_before_running(monkeypatch):
    monkeypatch.setenv("VIZ_CHART_WORKERS", "1")
    monkeypatch.setenv("VIZ_CHART_MAX_QUEUE", "8")
    monkeypatch.setenv("VIZ_CHART_QUEUE_WAIT_SEC", "0.05")
    gate = threading.Event()
    first = pool.submit(gate.wait, 5)
    ran = []
    second = pool.submit(ran.append, "x")
    time.sleep(0.2)          # 让第二个任务在排队期间超出预算
    gate.set()
    first.result(timeout=5)
    with pytest.raises(pool.VizQueryExpired):
        second.result(timeout=5)
    assert ran == [], "超预算的取数不得执行"
    assert pool.stats()["pending"] == 0


def test_pending_is_released_on_failure():
    def boom():
        raise RuntimeError("数据源不可用")

    with pytest.raises(RuntimeError):
        pool.run(boom)
    assert pool.stats()["pending"] == 0


def test_run_async_returns_value():
    out = asyncio.run(pool.run_async(lambda: 42))
    assert out == 42
    assert pool.stats()["pending"] == 0


def test_messages_are_actionable():
    busy = pool.busy_message(inflight=1, queued=2)
    expired = pool.expired_message(30)
    assert "稍后重试" in busy and "1 张图在跑" in busy
    assert "已取消" in expired and "30" in expired
