"""并发闸门与提供商繁忙分类：把"排队"和"模型繁忙"变成用户看得见、可操作的状态。

为什么需要它：平台此前**没有任何并发闸门** —— 只有 `adh_agent_sessions` 的"同一会话不能并发跑
两轮"执行权互斥（单会话粒度，不是全平台），和 `qoder_quota` 的账号 credit 配额（额度，不是并发）。
人多时表现为"每轮都变慢、最后一起撞墙钟超时"，用户完全不知道是自己在排队还是功能坏了。

三条边界，写清楚避免误解：
1. 闸门是**进程内**的。web 是单进程（`backend/app/main.py`），本模块对交互问答即等价于全局；
   celery-worker 是另一个进程，它跑的定时分析不受本闸门约束（由 worker 自己的并发槽控制）。
   多副本部署时**必须**先换成 Redis 租约式闸门，否则 `limit` 会被乘上进程数（不假装已跨进程限流）。
2. 名额交接用 FIFO + Future，不用裸 Semaphore：排队位置（"你在第 3 位"）必须是真序号，
   而不是"估算人数"。
3. 自动重试只针对**可恢复**的提供商繁忙/限流。超时、用户停止、权限拒绝一律不重试
   —— 重试它们等于重复取数、重复写动作。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# 全平台同时在跑的交互回合数（**每进程**口径，见模块头的边界说明）。
# 为什么默认 6：一轮 = 一个 qodercn CLI 子进程 + 一次模型请求，个位数已经让远端模型网关
# 与本机 CPU/内存同时吃紧；给到几十只是把"排队"换成"集体超时"。
DEFAULT_MAX_CONCURRENT_AGENT_TURNS = 6


def _env_int(name: str, default: int) -> int:
    """环境变量**延时读**：模块导入可能早于 `backend.common.config` 装载 backend/.env，
    在导入时定值会让管理员改了配置却静默不生效（配了没用类空承诺）。解析不了就用默认值。
    """
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


# 提供商繁忙的可恢复分类（其余错误一律不自动重试）
RECOVERABLE_KINDS = ("provider_busy", "rate_limited")
DEFAULT_MAX_AUTO_RETRIES = 2
DEFAULT_MAX_RETRY_WAIT_S = 20.0
_BUSY_WAIT_DEFAULT_S = {"provider_busy": 5.0, "rate_limited": 10.0}


def max_auto_retries() -> int:
    """自动重试次数上限（0=关掉自动重试）。"""
    return max(0, _env_int("AGENT_BUSY_MAX_RETRIES", DEFAULT_MAX_AUTO_RETRIES))


def max_retry_wait_s() -> float:
    """单次退避上限：超过就不让用户干等，直接给可操作提示。"""
    return max(1.0, _env_float("AGENT_BUSY_MAX_RETRY_WAIT_S", DEFAULT_MAX_RETRY_WAIT_S))

# 提供商繁忙/限流的结构化特征（CLI 上报文本里实测存在的字面量），每项自带归类：
# 从正则文本里反推“算繁忙还是算限流”会漏（too_many_requests 里没有 rate 这个词）。
_BUSY_PATTERNS = (
    (re.compile(r"rate[_ ]?limit", re.I), "rate_limited"),
    (re.compile(r"too[_ ]?many[_ ]?requests", re.I), "rate_limited"),
    (re.compile(r"\b429\b"), "rate_limited"),
    (re.compile(r"overloaded", re.I), "provider_busy"),
    (re.compile(r"\b529\b"), "provider_busy"),
    (re.compile(r"\b503\b"), "provider_busy"),
    (re.compile(r"服务(繁忙|超载|不可用)"), "provider_busy"),
)
_TIMEOUT_PATTERNS = (re.compile(r"timeout", re.I), re.compile(r"timed out", re.I), re.compile(r"超时"))
_ABORT_PATTERNS = (re.compile(r"cancel", re.I), re.compile(r"interrupt", re.I), re.compile(r"中止"))
_RETRY_AFTER_RE = re.compile(r"retry[_ -]?after[^0-9]{0,12}(\d+(?:\.\d+)?)", re.I)

# 面向用户的分类文案（细节只进日志，护栏 §7：不外泄原始报错栈）
_USER_HINTS = {
    "provider_busy": "模型服务当前繁忙",
    "rate_limited": "模型服务请求过于频繁",
    "timeout": "模型服务响应超时",
    "aborted": "本轮已被停止",
    "failed": "模型执行未完成",
}


def classify_failure(*texts: str) -> tuple[str, Optional[float]]:
    """把 CLI/异常文本归一成 `(kind, retry_after_s)`。

    只认结构化特征（rate_limit/overloaded/429/529/503/超时/取消），命中不了就是 `failed` ——
    宁可不重试，也不把"未知错误"当成可重试反复烧 credit。
    """
    blob = " ".join(str(t or "") for t in texts)
    if not blob.strip():
        return "failed", None
    if any(p.search(blob) for p in _TIMEOUT_PATTERNS):
        return "timeout", None
    if any(p.search(blob) for p in _ABORT_PATTERNS):
        return "aborted", None
    for pattern, kind in _BUSY_PATTERNS:
        if pattern.search(blob):
            return kind, _retry_after_s(blob, kind)
    return "failed", None


def _retry_after_s(blob: str, kind: str) -> float:
    """退避时长：优先采纳提供商给的 retry_after，否则按分类给保守默认；一律封顶。"""
    m = _RETRY_AFTER_RE.search(blob)
    raw = float(m.group(1)) if m else float(_BUSY_WAIT_DEFAULT_S.get(kind, 5.0))
    return min(max(raw, 1.0), max_retry_wait_s())


def user_hint(kind: str) -> str:
    return _USER_HINTS.get(kind, _USER_HINTS["failed"])


def is_recoverable(kind: str) -> bool:
    return kind in RECOVERABLE_KINDS


class SlotHandle:
    """一次名额占用的句柄（等待时长 + 起始时刻），release 时用它算实际服务时长。"""

    __slots__ = ("waited_ms", "_loop", "_started")

    def __init__(self, waited_ms: int, loop, started: float):
        self.waited_ms = waited_ms
        self._loop = loop
        self._started = started


class GateIntegrityError(RuntimeError):
    """闸门自身计数不一致。

    此时唯一安全的动作是**拒绝新回合**（回 503 + 可读文案），不能当成“当下没排队”直接放行：
    闸门失效时放行会把“超载 → 集体撞墙钟”伪装成“平台只是慢”，且事后无从归因。
    """


class TurnGate:
    """FIFO 并发闸门：满员时后来者排队，并能报出真实排队位置。"""

    def __init__(self, limit: Optional[int] = None):
        self.limit = max(1, int(limit if limit is not None
                               else _env_int("MAX_CONCURRENT_AGENT_TURNS",
                                             DEFAULT_MAX_CONCURRENT_AGENT_TURNS)))
        self.active = 0
        self._waiters: list[asyncio.Future] = []
        # 最近若干回合的墙钟时长（毫秒）：预估等待用它算，不用拍脑袋常数
        self._durations: deque = deque(maxlen=24)

    @property
    def queued(self) -> int:
        return len(self._waiters)

    def avg_turn_ms(self) -> float:
        if not self._durations:
            return 0.0
        return sum(self._durations) / len(self._durations)

    def estimated_wait_s(self, position: int) -> Optional[float]:
        """第 `position` 位的预计等待 = 队前人数 × 平均回合时长 ÷ 并发槽。

        每个名额释放就交接给队首，所以完成速率是 `limit / avg`；拍脑袋乘上批次数
        会把“第 3 位”算成“等两个整批”。没有样本时返回 None（不编一个常数冒充预估）。
        """
        avg = self.avg_turn_ms()
        if avg <= 0:
            return None
        return round(max(1, position) * avg / self.limit / 1000.0, 1)

    def snapshot(self) -> dict:
        return {"limit": self.limit, "active": self.active, "queued": self.queued,
                "avg_turn_ms": int(self.avg_turn_ms()),
                "estimated_wait_s": self.estimated_wait_s(self.queued or 1)}

    def _check_integrity(self) -> None:
        """计数不变式：`0 <= active <= limit`。破了就显式拒，不默默放行。"""
        if 0 <= self.active <= self.limit:
            return
        detail = f"active={self.active} limit={self.limit} queued={self.queued}"
        logger.error("并发闸门计数不一致，已拒绝新回合: %s", detail)
        raise GateIntegrityError("平台会话调度异常，已暂停接收新回合；请稍后重试或联系管理员")

    def _drop_waiter(self, waiter: asyncio.Future) -> None:
        """取消等待：出队；若名额已交接给自己，立刻转交下一位，否则计数会漏。"""
        if waiter in self._waiters:
            self._waiters.remove(waiter)
        if waiter.done() and not waiter.cancelled():
            self._release_one()

    def _release_one(self) -> None:
        """释放一个名额：优先交接给队首（active 不变），队列为空才真的降 active。"""
        while self._waiters:
            fut = self._waiters.pop(0)
            if fut.done():
                continue                       # 已取消的等待者，跳过
            fut.set_result(True)
            return
        self.active = max(0, self.active - 1)

    async def acquire(self, on_queued: Optional[Callable[[dict], Awaitable[None]]] = None
                      ) -> SlotHandle:
        """占用一个回合名额；满员则排队。

        `on_queued` 收 `{position, limit, estimated_wait_s}` —— 由调用方转成 SSE 事件，
        让前端显示"你在第 N 位，预计 X 秒"，而不是对着转圈的加载图标猜。
        等待期间被取消（客户端断连/用户点取消）不出错、不泄漏计数。
        """
        loop = asyncio.get_running_loop()
        self._check_integrity()
        waited_ms = 0
        if self.active >= self.limit:
            waiter = loop.create_future()
            self._waiters.append(waiter)
            position = len(self._waiters)
            if on_queued is not None:
                await on_queued({"position": position, "limit": self.limit,
                                 "estimated_wait_s": self.estimated_wait_s(position)})
            t0 = loop.time()
            try:
                await waiter
            except asyncio.CancelledError:
                self._drop_waiter(waiter)
                raise
            waited_ms = int((loop.time() - t0) * 1000)
            # 走交接路径时名额已由 `_release_one` 记在自己头上，不能再 +1（否则 active 超上限）
        else:
            self.active += 1
        return SlotHandle(waited_ms, loop, loop.time())

    def release(self, handle: SlotHandle) -> None:
        """归还名额并记录本回合时长（供后续回合预估等待）。幂等：句柄只能归还一次。"""
        if handle is None:
            return
        if getattr(handle, "_started", None) is None:
            return                              # 已归还，重复 release 会把 active 压成负数
        duration = max(0, int((handle._loop.time() - handle._started) * 1000))
        handle._started = None
        self._durations.append(duration)
        self._release_one()

    @asynccontextmanager
    async def slot(self, on_queued: Optional[Callable[[dict], Awaitable[None]]] = None):
        handle = await self.acquire(on_queued)
        try:
            yield self
        finally:
            self.release(handle)


_GATE: Optional[TurnGate] = None


def get_gate() -> TurnGate:
    """首次使用时才建闸门（上限那时才从环境变量读，不在导入时定死）。"""
    global _GATE
    if _GATE is None:
        _GATE = TurnGate()
    return _GATE


def capacity_snapshot() -> dict:
    """当前容量水位（供前端提示与管理侧观测）。"""
    return get_gate().snapshot()
