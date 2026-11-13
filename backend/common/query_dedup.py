"""同参数查询去重（进程内 + 短 TTL）：一次看板刷新里多张卡打同一条重查询时只打一次。

为什么需要它：看板上每张卡都独立下发自己那次数据集查询，同一块板的 4 张 KPI 卡
用的是**同一条正文、同一组参数**（实测 ASSCANPRO 近一年 457 万条，单条 13s，
读预算 20s）—— 连发 4 遍等于把集群按在预算线上摩擦，间歇越界就是「刷新失败」红条。

为什么是进程内而不是 Redis：缓存内容就是查询结果行（含 userCode/caseUid 这类业务标识）。
落到 Redis 等于给敏感数据开第二副本 —— 审计都刻意只记行数不记数据行（`_es_audit`），
不能在这里反过来存一份。本模块只做几十秒的进程内记忆 + 在途合并：
重启即清、不跨实例共享、不持久化，TTL 到点就回源。

三条硬口径（缺一即视为把错数发给用户）：
1. **失败不记忆**：抛错的条目立即作废，下一个调用重新真跑（否则一次抖动会被复用几十秒）；
2. **返回拷贝**：结果 dict 与 rows 数组每次调用各得一份，调用方加字段（field_formats 等）
   不会污染备忘录，也不会让后来者拿到"别人改过的"结果；
3. **命中要可查**：调用方拿到 source 标记（ran / shared / memo），据此落审计与诊断字段，
   不得把去重藏起来让排查时以为"这条查询真的跑了"。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

# 等待首发的兜底上限：首发线程若异常卡死（例如被外部取消），等待者必须自己重跑，
# 不能被吊死在这里 —— 宁可多跑一次，也不能让页面永远转圈。
LEADER_WAIT_SEC = 120.0
MAX_ENTRIES = 256

_lock = threading.Lock()
# key -> _Entry
_entries: dict[Any, "_Entry"] = {}


class _Entry:
    __slots__ = ("event", "done", "result", "error", "expires_at")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.done = False
        self.result: Any = None
        self.error: BaseException | None = None
        self.expires_at = 0.0


def _copy(value: Any) -> Any:
    """浅拷贝结果容器 + 复制 rows：调用方加字段/截断列表都不该动到备忘录那份。"""
    if not isinstance(value, dict):
        return value
    out = dict(value)
    if isinstance(out.get("rows"), list):
        out["rows"] = list(out["rows"])
    return out


def _evict(now: float) -> None:
    """清掉过期项；仍超量就按最早到期逐出（键空间本应有界：板数 × 参数形状）。"""
    for k in [k for k, e in _entries.items() if e.done and e.expires_at <= now]:
        _entries.pop(k, None)
    if len(_entries) > MAX_ENTRIES:
        for k, _ in sorted(_entries.items(), key=lambda kv: kv[1].expires_at)[:len(_entries) - MAX_ENTRIES]:
            _entries.pop(k, None)


def run(key: Any, fn: Callable[[], Any], *, ttl: float) -> tuple[Any, str]:
    """同 key 的并发/短间隔重复调用只真跑一次，返回 `(结果, 来源)`。

    source: "ran"=本次真跑 / "shared"=等同批在途首发 / "memo"=复用 TTL 内已算好的结果。
    ttl<=0 时完全不去重（直接跑），给调用方留显式关闭开关。
    """
    if ttl <= 0:
        return fn(), "ran"

    waited = False          # 等同过首发 → 算 shared（而不是“隔了一会儿拿旧备忘录”的 memo）
    while True:
        with _lock:
            entry = _entries.get(key)
            if entry is None:
                entry = _Entry()
                _entries[key] = entry
                leader = True
            else:
                leader = False
                if entry.done:
                    now = time.monotonic()
                    if entry.error is not None:
                        _entries.pop(key, None)      # 失败不记忆
                        continue
                    if now < entry.expires_at:
                        return _copy(entry.result), "shared" if waited else "memo"
                    _entries.pop(key, None)
                    continue
            waiting = None if leader else entry.event
        if waiting is not None:
            waiting.wait(LEADER_WAIT_SEC)
            waited = True
            continue                                  # 醒来重新判定：拿结果 / 首发失败则自己重跑

        # ── 首发：真跑一次 ────────────────────────────────────────────────
        try:
            result = fn()
        except BaseException as exc:                  # noqa: BLE001 — 原样回抛，但条目必须作废
            with _lock:
                entry.error = exc
                entry.done = True
                entry.expires_at = 0.0
                _evict(time.monotonic())
            raise
        with _lock:
            entry.result = result
            entry.done = True
            entry.expires_at = time.monotonic() + ttl
            _evict(time.monotonic())
        entry.event.set()
        return _copy(result), "ran"


def shared_source(source: str) -> bool:
    """这次结果是不是**没有真的执行**（供调用方决定要不要另落一条可诊断的审计）。"""
    return source in ("memo", "shared")


def clear() -> None:
    """清空备忘录（测试与"配置刚改完想立刻看真实结果"的手工核验用）。"""
    with _lock:
        _entries.clear()
