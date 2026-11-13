"""数据源取数并发闸门：把"源被打爆"变成看得见、可退让的排队与提示。

为什么需要它：一次看板刷新 = N 张图，多个用户同时看 = N×M 条查询，而 SQL 型数据源
**每次取数都新建一条连接**（`datasource_db.get_datasource_conn` 没有池），源侧连接数与并发
查询数在平台这边完全没上限 → 撞源侧 `max_connections` / SLS 配额 / ES 线程池，最终表现是
"大家一起超时"。用户分不清是自己在排队、还是功能坏了；管理员也分不清是源慢了、还是我们把
源打爆了。ES 侧今天有了 Session 连接池（`es_client._session_for`），但**复用 ≠ 限流**：
`pool_maxsize` 之外 requests 仍会新建连接，池子只省握手，不挡并发。

口径（与 `modules/mind/execution/capacity.py` 的回合闸门同一套纪律，但这里是**同步**线程场景，
取数发生在 FastAPI 线程池 / `run_in_executor` 里，不能依赖 asyncio Future）：
1. **按数据源分桶**：一个源的预算不该被别的源吃掉，管理员也要能按源体量分别设；
2. 满员先**排队**（FIFO，位次是真序号不是估算），排队超过预算时间才拒；拒时给可读原因
   （上限多少、排了多少位、等了多久、预计还要多久），而不是笼统的"查询失败"；
3. 计数不变式 `0 <= active <= limit` 破了就**拒接新查询**并告警 —— 闸门自身失效时放行，
   等于把"超载→集体撞超时"伪装成"平台只是慢"，事后无从归因；
4. **同线程嵌套取数不重复占名额**：一次请求里串着查多个数据集是常态（跨站 fan-out、
   分页 count + 明细），limit=1 时若重复占位就是自己等自己 → 必然排队超时（假死锁）；
   重入靠**本线程在本网关上的句柄栈**判定（不是计数器），所以“拿了不还”不会让后续查询
   永久旁路闸门：上一个句柄一旦 released，栈顶就不成立，下一次 acquire 重新真占位；
5. 名额必须在 `finally` 归还（异常/超时/权限拒绝都不留泄漏），release 幂等；
6. 闸门是**进程内**的：web 单进程（`backend/app/main.py`）时即等价全局；celery-worker 与
   多副本部署**必须**先换成 Redis 租约式闸门，否则上限会被乘上进程数（这里不假装已跨进程限流）。

上限与排队预算都可在「数据源管理 → 高级配置」按源配（`adh_datasources.extra_config.max_concurrency`
/ `queue_wait_sec`），缺省取下面的常量；缺省值刻意保持"不改配置行为零变化"的宽松量级，
避免上线当天就把存量看板挡在门外。

配置读取只在**该源第一次建桶**时读一次库（后续取数零额外库读），保存数据源时由
`datasource_service` 调 `sync_datasource_gate()` 就地刷新 —— 既做到"改完立刻生效"，
又不用每条查询多跑一次 SELECT。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

# 每数据源同时允许的查询数（缺省宽松值：单张看板 30 图串行刷新也够，真超载源由管理员收紧）
DEFAULT_MAX_CONCURRENCY = 8
# 排队预算：超过就把"还在排"如实告诉用户，不让人无限等（比查询超时更短才有意义）
DEFAULT_QUEUE_WAIT_S = 10.0
# 配置面可填的天花板（与 datasource_service 的 extra_config 区间必须一致，两处不同口径必漂移）
LIMIT_FLOOR, LIMIT_CEIL = 1, 64
WAIT_FLOOR, WAIT_CEIL = 0.0, 120.0


def _env_num(name: str, default):
    """环境变量**延时读**：导入时定值会让管理员改了配置却静默不生效（"配了没用"类空承诺）。"""
    try:
        return type(default)(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def default_limit() -> int:
    return min(LIMIT_CEIL, max(LIMIT_FLOOR, int(_env_num("DATASOURCE_MAX_CONCURRENCY",
                                                          DEFAULT_MAX_CONCURRENCY))))


def default_wait_s() -> float:
    return min(WAIT_CEIL, max(WAIT_FLOOR, float(_env_num("DATASOURCE_QUEUE_WAIT_S",
                                                         DEFAULT_QUEUE_WAIT_S))))


class QueryQueueTimeout(RuntimeError):
    """排队超预算：面向用户的可读拒绝（既不是崩溃，也不是把空结果当成"没数据"）。

    两个显式声明都是给下游文案链路看的，不是装饰：
    - `safe_business_message`：`queue_message()` 已只含数据源业务名与数字（无 host/端口/账号），
      所以 `semantics.execute.execute_sql` 原样下发它，不再套"系统侧问题，建议稍后重试"；
    - `exec_category`：错因归类为容量退让，与"连不上/凭据不可用"分开（重试可能恰好排到，
      但把它说成连接故障会把排查方向指错）。混在 `_EXEC_FAIL_HINT` 里，管理员会去查网络，
      实际该做的是收紧并发上限或减少同刷图表数。
    """

    exec_category = "capacity_queue"
    safe_business_message = True


class GateIntegrityError(RuntimeError):
    """闸门计数不一致：此时唯一安全的动作是拒接新查询，不能"当没排队"放行。

    文案与类别同 `QueryQueueTimeout`（都是容量退让，不是数据/权限问题）。
    """

    exec_category = "capacity_queue"
    safe_business_message = True


class _Waiter:
    """一个排队位：名额交接靠 event，位次靠它在队列里的下标（真序号，不估算）。"""

    __slots__ = ("event", "granted")

    def __init__(self):
        self.event = threading.Event()
        self.granted = False


class _Handle:
    """一次名额占用句柄：`passthrough=True` 是同线程嵌套的重入记账，不占新名额。"""

    __slots__ = ("gate", "waited_ms", "started", "passthrough", "released")

    def __init__(self, gate, waited_ms: int, started: Optional[float],
                 passthrough: bool = False):
        self.gate = gate
        self.waited_ms = waited_ms
        self.started = started
        self.passthrough = passthrough
        self.released = False


class SourceGate:
    """单个数据源的 FIFO 并发闸门。"""

    def __init__(self, datasource_id: int, limit: Optional[int] = None,
                 wait_s: Optional[float] = None, label: str = ""):
        self.datasource_id = int(datasource_id or 0)
        self.label = label or ""
        self._lock = threading.Lock()
        self._waiters: list[_Waiter] = []
        self._durations: deque = deque(maxlen=24)      # 最近若干条查询的墙钟时长（毫秒）
        self.active = 0
        self._local = threading.local()              # 每线程在本闸门持有的句柄栈（重入判定）
        self.set_limit(limit)
        self.set_wait_s(wait_s)

    # ── 配置（管理员改完即时生效，不重启、不丢正在跑的查询）────────────
    def set_limit(self, limit: Optional[int]) -> None:
        raw = limit if limit is not None else default_limit()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default_limit()
        self.limit = min(LIMIT_CEIL, max(LIMIT_FLOOR, value))

    def set_wait_s(self, wait_s: Optional[float]) -> None:
        raw = wait_s if wait_s is not None else default_wait_s()
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default_wait_s()
        self.wait_s = min(WAIT_CEIL, max(WAIT_FLOOR, value))

    # ── 观测 ────────────────────────────────────────────────────────
    @property
    def queued(self) -> int:
        with self._lock:
            return len(self._waiters)

    def avg_query_ms(self) -> float:
        if not self._durations:
            return 0.0
        return sum(self._durations) / len(self._durations)

    def estimated_wait_s(self, position: int) -> Optional[float]:
        """第 `position` 位的预计等待 = 队前人数 × 平均查询时长 ÷ 并发槽。

        没有样本时返回 None —— 不编一个常数冒充"预计 30 秒"（那会比不告诉更糟）。
        """
        avg = self.avg_query_ms()
        if avg <= 0:
            return None
        return max(0.1, round(max(1, position) * avg / self.limit / 1000.0, 1))

    def snapshot(self) -> dict:
        with self._lock:
            return {"datasource_id": self.datasource_id, "label": self.label,
                    "limit": self.limit, "active": self.active,
                    "queued": len(self._waiters), "wait_s": self.wait_s,
                    "avg_query_ms": int(self.avg_query_ms()),
                    "estimated_wait_s": self.estimated_wait_s(len(self._waiters) or 1)}

    # ── 内部 ────────────────────────────────────────────────────────
    def _check_integrity(self) -> None:
        if 0 <= self.active <= self.limit:
            return
        detail = (f"datasource={self.datasource_id} active={self.active} "
                  f"limit={self.limit} queued={len(self._waiters)}")
        logger.error("取数并发闸门计数不一致，已拒绝新查询: %s", detail)
        raise GateIntegrityError(
            f"数据源「{self.label or self.datasource_id}」的并发调度状态异常，已暂停接收新查询；"
            "请稍后重试或联系管理员")

    def _held_stack(self) -> list:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        return stack

    def _holding(self) -> bool:
        """本线程是否已在本闸门上占着名额（栈顶未归还才算）。"""
        stack = self._held_stack()
        return bool(stack) and not stack[-1].released

    def _release_one(self, duration_ms: Optional[int] = None) -> None:
        """归还一个名额：优先交接给队首（active 不变），队列为空才真的降 active。"""
        if duration_ms and duration_ms > 0:
            self._durations.append(duration_ms)
        while self._waiters:
            waiter = self._waiters.pop(0)
            waiter.granted = True
            waiter.event.set()          # 名额转移给队首，active 保持不动
            return
        self.active = max(0, self.active - 1)

    def queue_message(self, position: int, waited_ms: int) -> str:
        """排队超预算的对外文案：说清"在排队、排不到"，并给可操作的下一步。

        只含数据源业务名（`label`）与数字，不含 host/端口/账号（护栏 §7）。
        """
        label = self.label or f"#{self.datasource_id}"
        est = self.estimated_wait_s(position)
        tail = f"预计还需约 {est:.0f} 秒" if est else "预计等待时间暂时算不出"
        return (f"数据源「{label}」当前查询较多：并发上限 {self.limit} 已占满，"
                f"排队 {waited_ms / 1000:.1f} 秒仍未轮到（当时排在第 {position} 位，{tail}）。"
                "请稍后重试；也可以缩小时间范围、减少分组基数，或减少同时刷新的图表数。")

    # ── 取用与归还 ───────────────────────────────────────────────────
    def acquire(self) -> _Handle:
        """占用一个查询名额；满员则排队，超过 `wait_s` 抛 `QueryQueueTimeout`。

        句柄必须由**取得它的同一线程**归还；优先用 `query_slot()`而不是手拼 acquire/release。
        """
        if self._holding():                           # 同线程嵌套：不重复占位（防自己等自己）
            handle = _Handle(self, 0, None, passthrough=True)
            self._held_stack().append(handle)
            return handle

        with self._lock:
            self._check_integrity()
            if self.active < self.limit:
                self.active += 1
                return self._claim(_Handle(self, 0, time.monotonic()))
            waiter = _Waiter()
            self._waiters.append(waiter)
            position = len(self._waiters)

        t0 = time.monotonic()
        deadline = t0 + self.wait_s
        waiter.event.wait(timeout=max(0.0, deadline - time.monotonic()))
        waited_ms = int((time.monotonic() - t0) * 1000)

        with self._lock:
            if waiter.granted:                        # 含"刚好在超时那一瞬被交接"，认账
                return self._claim(_Handle(self, waited_ms, time.monotonic()))
            if waiter in self._waiters:
                self._waiters.remove(waiter)          # 放弃位次，不占后来者的队

        logger.warning("[query-gate] 数据源 %s 排队超预算被拒: 上限=%s 排队位=%s 等待=%sms",
                       self.label or self.datasource_id, self.limit, position, waited_ms)
        raise QueryQueueTimeout(self.queue_message(position, waited_ms))

    def _claim(self, handle: _Handle) -> _Handle:
        self._held_stack().append(handle)
        return handle

    def release(self, handle: _Handle) -> None:
        """归还名额并记录本次时长（供后续查询预估等待）。幂等：句柄只能归还一次。"""
        if handle is None or handle.released:
            return
        stack = self._held_stack()
        if stack and stack[-1] is handle:
            stack.pop()
        elif handle in stack:                         # 乱序归还（不该发生）：摘掉它，不把栈搞坏
            stack.remove(handle)
        duration = (int((time.monotonic() - handle.started) * 1000)
                    if handle.started else None)
        handle.started = None
        handle.released = True
        if handle.passthrough:
            return                                    # 重入记账：本来就没占名额
        with self._lock:
            self._release_one(duration)


# ── 注册表（按数据源分桶）───────────────────────────────────────────────
_GATES: dict[int, SourceGate] = {}
_REGISTRY_LOCK = threading.Lock()


def gate_for(datasource_id: int, *, limit: Optional[int] = None,
             wait_s: Optional[float] = None, label: str = "") -> SourceGate:
    """取该数据源的闸门；已存在则**就地更新**上限/预算（配置改完立即生效）。"""
    key = int(datasource_id or 0)
    with _REGISTRY_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = _GATES[key] = SourceGate(key, limit=limit, wait_s=wait_s, label=label)
            return gate
    if limit is not None:
        gate.set_limit(limit)
    if wait_s is not None:
        gate.set_wait_s(wait_s)
    if label:
        gate.label = label
    return gate


def reset_gates(datasource_id: Optional[int] = None) -> None:
    """丢弃闸门（数据源被删除/配置页保存，以及测试隔离）。"""
    with _REGISTRY_LOCK:
        if datasource_id is None:
            _GATES.clear()
        else:
            _GATES.pop(int(datasource_id), None)


# ── 数据源配置面（`adh_datasources.extra_config`）─────────────────────────
def _datasource_row(datasource_id: int) -> dict:
    """读数据源行（仅**首次建桶**时调一次）。

    元数据库读不到时退回缺省上限并记 warning：这不是把功能失败遮成成功——闸门只是
    把“多大并发算超载”的口径暂按缺省 8，源侧仍然受到限流；而元数据库不可用时，
    取数主链路自己会在下一次读配置/建连接时显式报错（`_is_es_datasource` 与连接器都在读它），
    错误不会因为我们而被吞掉。反之，让“读不到限流配置”抢先杀掉一条本可执行的查询，
    把一个旁路问题伪装成取数故障。
    （`extra_config` 正文不是合法 JSON 仍照旧上抛：那是配置缺陷，不能当“没配”默认放行。）
    """
    from backend.common.db.datasource_db import get_datasource_by_id

    try:
        return get_datasource_by_id(int(datasource_id or 0)) or {}
    except Exception as exc:  # noqa: BLE001 — 旁路配置读不得抢先中断取数，主链路自己会报错
        logger.warning("[query-gate] 读数据源限流配置失败，按缺省上限继续限流: %s", exc)
        return {}


def config_from_row(row: Optional[dict]) -> dict:
    """数据源行 → 闸门配置（上限 / 排队预算 / 业务名）。

    没配就用缺省宽松值；`queue_wait_sec` 可以是 **0**（“不排队，满员直接拒”），
    所以只能判 `None` 不能判真值，否则 0 会被当成没配而退回缺省 10 秒。
    """
    from backend.common.db.datasource_db import source_extra

    extra = source_extra(row or {})
    wait = extra.get("queue_wait_sec")
    try:
        wait_s = None if wait is None or wait == "" else float(wait)
    except (TypeError, ValueError):
        logger.warning("[query-gate] queue_wait_sec 不是数字，按缺省处理：%r", wait)
        wait_s = None
    return {"limit": extra.get("max_concurrency"), "wait_s": wait_s,
            "label": str((row or {}).get("name") or "")}


def gate_for_source(datasource_id: Optional[int], *, row: Optional[dict] = None) -> SourceGate:
    """取该源的闸门，**首次建桶时**从数据源配置解析上限。

    已存在就直接返回（不再读库）：上限变更走 `sync_datasource_gate()`，每次取数多跑一条
    SELECT 不值得（尤其本模块的目的就是减轻源侧压力）。
    """
    key = int(datasource_id or 0)
    gate = _GATES.get(key)                 # 快路径：dict 读在 CPython 下是原子的
    if gate is not None:
        return gate
    return gate_for(key, **config_from_row(row if row is not None else _datasource_row(key)))


def sync_datasource_gate(datasource_id: int, *, row: Optional[dict] = None) -> dict:
    """保存数据源后就地刷新闸门配置（不打断在跑的查询），返回新水位快照。

    必须走“设新值”而不是“重建桶”：重建会把正在排队的线程留在旧对象上，
    短暂变成旧桶 + 新桶双倍限流。同理，“取消配置”必须回缺省而不是保留旧值，
    否则管理员删了旋钮却继续按旧上限跑（配了不生效的另一面：取消了也不生效）。
    """
    key = int(datasource_id or 0)
    cfg = config_from_row(row if row is not None else _datasource_row(key))
    gate = gate_for_source(key, row=row)
    gate.set_limit(cfg["limit"])
    gate.set_wait_s(cfg["wait_s"])
    if cfg["label"]:
        gate.label = cfg["label"]
    return gate.snapshot()


@contextmanager
def slot_for_datasource(datasource_id: Optional[int], *, row: Optional[dict] = None):
    """取数口的专用包装：上限/预算/展示名都从数据源配置解析（调用侧不必再读库）。

    `row` 可选：调用方已经拿得到数据源行时传进来，避开首次建桶那一次额外 SELECT。
    """
    gate = gate_for_source(datasource_id, row=row)
    handle = gate.acquire()
    try:
        yield gate
    finally:
        gate.release(handle)


@contextmanager
def query_slot(datasource_id: Optional[int], *, limit: Optional[int] = None,
               wait_s: Optional[float] = None, label: str = ""):
    """在"真正打源"的那段外面套一个名额：`with query_slot(ds_id, ...): ...`

    `datasource_id` 为 0/None（默认引擎等内部调用）时按 0 分桶，仍然限流 —— 默认引擎同样是
    真实数据库，"没登记源 id"不等于"没有资源要保护"。
    """
    gate = gate_for(datasource_id, limit=limit, wait_s=wait_s, label=label)
    handle = gate.acquire()
    try:
        yield gate
    finally:
        gate.release(handle)


def snapshot_all() -> list[dict]:
    """各数据源当前水位（管理侧观测/排障用；只给数字与业务名，不给连接信息）。"""
    with _REGISTRY_LOCK:
        gates = list(_GATES.values())
    return [g.snapshot() for g in gates]
