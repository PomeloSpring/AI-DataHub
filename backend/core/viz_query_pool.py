"""看板取数专用有界通道（与主应用的线程 / 连接资源隔离）。

**为什么需要它（实测账面，不是预防性设计）**

1. 图表刷新端点过去是同步 `def`，跑在 Starlette/anyio 的**全应用共享**线程池
   （默认 40）上。用户点一次「查询」= 一块板 24 张图并发下发 24 个同步请求，
   其中几张会卡在数据源上（单图实测 20~28s）——共享池被整批占住，主应用的其它
   同步接口（保存、列表、登录后的首屏）只能排队，表现就是"点了个看板查询，整个
   系统卡住"。
2. 更致命的一层：刷新路径**取数期间还持着元数据库连接**（`metadata_db` 连接池
   max 8、全应用共享、blocking）。4 张图同时在途即能占满，再往上并发就是互相
   等连接（实测 8 路并发刷新 24 图 **15 分钟不返回**，是等连接而不是慢）——
   主应用的数据库查询同样被这批"什么都不干、只是在等数据源"的连接挤死。

所以看板取数单独一条有界通道：线程数与队列上限显式可配，排队有预算；超预算
**显式拒绝**并给可读文案（`no-silent-degradation.md`：不静默丢图、不假装成功、
不无限占用主应用资源）。

**并发度的定值依据**：元数据库连接池是 8（`metadata_db.POOL_MAX_CONNECTIONS`），
取数期间每张图仍会短暂借用连接（审计、数据集配置、缓存写）。缺省 4 是给主应用
留一半连接余量；要调大请先用 `get_metadata_pool_stats()` 看真实占用，别把池打满。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

# 缺省并发：与前端 `chartQueryPool` 的 4 对齐（浏览器 HTTP/1.1 每源 6 连接的硬上限，
# 独占 6 会让同页其它请求排队）。改这里请同步看前端与元数据库连接池余量。
DEFAULT_WORKERS = 4
# 队列上限：一块板 24 图 + 多标签页/大屏/浏览页同时刷，128 足够；再多说明已经在过载，
# 拒绝比排队到天荒地老更有用。
DEFAULT_MAX_QUEUE = 128
# 排队预算：等这么久还没轮到就拒（交互式点击的容忍上限量级）。
DEFAULT_QUEUE_WAIT_SEC = 60.0

_BUSY_HINT = ("看板取数通道已满（{inflight} 张图在跑、{queued} 张在排队），本次未发起查询。"
              "请稍后重试；若持续出现，请减少单次刷新的图表数量或调大 VIZ_CHART_WORKERS。")
_EXPIRED_HINT = ("看板取数排队超过 {wait} 秒仍未轮到，本次已取消（未执行取数）。"
                 "请稍后重试，或缩小时间范围后再查。")


class VizQueryBusy(RuntimeError):
    """通道排队已满，本次取数未受理（还没开始就拒，不是执行失败）。"""


class VizQueryExpired(RuntimeError):
    """排队超过预算，本次取数已取消（保证不会无限期挂着用户的点击）。"""


def busy_message(inflight: int = 0, queued: int = 0) -> str:
    return _BUSY_HINT.format(inflight=inflight, queued=queued)


def expired_message(wait: float = DEFAULT_QUEUE_WAIT_SEC) -> str:
    return _EXPIRED_HINT.format(wait=int(wait))


def _env_num(name: str, default: float, *, minimum: float = 0) -> float:
    """环境变量读数字：缺省/空串才用默认值，非法值显式报错（不静默吃掉配置）。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"环境变量 {name} 必须是数字，当前值 {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"环境变量 {name} 不能小于 {minimum}，当前值 {raw!r}")
    return value


def workers() -> int:
    return max(1, int(_env_num("VIZ_CHART_WORKERS", DEFAULT_WORKERS, minimum=1)))


def max_queue() -> int:
    return max(0, int(_env_num("VIZ_CHART_MAX_QUEUE", DEFAULT_MAX_QUEUE, minimum=0)))


def queue_wait_sec() -> float:
    return _env_num("VIZ_CHART_QUEUE_WAIT_SEC", DEFAULT_QUEUE_WAIT_SEC, minimum=0)


_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_executor_workers = 0
# 在途 + 排队（含正在执行的）数量，用于队列上限判定；线程池自身的队列不设限，
# 上限由这里显式把关（否则拒绝就发生在"看不见的内部队列"里，排队长度也不可控）。
_pending = 0


def _get_executor() -> ThreadPoolExecutor:
    global _executor, _executor_workers
    n = workers()
    with _lock:
        if _executor is None or _executor_workers != n:
            if _executor is not None:
                # 并发度改了就换一条新通道；旧任务自然跑完（不打断在执行中的取数）
                _executor.shutdown(wait=False)
            _executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="viz-query")
            _executor_workers = n
        return _executor


def submit(fn: Callable[..., Any], *args, **kwargs) -> Future:
    """把一次取数交给专用通道，返回 Future（调用方自取结果）。

    满员/超排队上限立即抛 `VizQueryBusy`；排到但已过预算是**执行前**抛
    `VizQueryExpired`（Future 上体现，取结果的调用方会收到它）。
    """
    global _pending
    global_capacity = workers() + max_queue()
    with _lock:
        if _pending >= global_capacity:
            raise VizQueryBusy(busy_message(inflight=workers(), queued=max(0, _pending - workers())))
        _pending += 1
    submitted_at = time.monotonic()
    budget = queue_wait_sec()

    def _task() -> Any:
        global _pending
        waited = time.monotonic() - submitted_at
        try:
            if budget > 0 and waited > budget:
                # 排到队时用户的点击早就不新鲜了：明确取消，而不是拿一份几分钟前的
                # 请求去跑（跑完还要写缓存、弹提示，用户已不知道在等什么）
                raise VizQueryExpired(expired_message(budget))
            return fn(*args, **kwargs)
        finally:
            with _lock:
                _pending = max(0, _pending - 1)

    return _get_executor().submit(_task)


def run(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """同步等待版本（服务层内部串行调用、脚本与测试用）。"""
    return submit(fn, *args, **kwargs).result()


async def run_async(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """异步端点用：把任务放进专用通道，再 await 结果。

    关键点是 **await 期间不占 anyio 的共享线程池**：过去 `def` 端点整段（含数据源
    往返的几十秒）都在共享池的一个线程上，一块板 24 张图就把池吃掉大半。
    """
    future = submit(fn, *args, **kwargs)
    return await asyncio.wrap_future(future)


def stats() -> dict:
    """运行态（诊断/用例用）：并发度、排队上限、当前在途量。"""
    with _lock:
        return {"workers": workers(), "max_queue": max_queue(),
                "queue_wait_sec": queue_wait_sec(), "pending": _pending}


def reset_for_tests() -> None:
    """通道闲置（用例在改环境变量后重建线程池用）。"""
    global _executor, _executor_workers, _pending
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
        _executor = None
        _executor_workers = 0
        _pending = 0