"""SessionContext 管理 —— 按 policy 指纹分桶池化（Phase 6.2，Phase 6.3 强制面挂点）。

同一 policy 指纹（RLS 行策略/列掩码/身份域的稳定哈希）的查询共享一个 SessionContext；
Phase 6.3 的 RLS/mask **视图化强制**按指纹向会话注册过滤视图（plan 期强制、
天然覆盖 JOIN 两侧），指纹首次出现时重建该桶视图、指纹变化即换桶（不复用旧视图）。

池有界（LRU 逐出，桶数受控内存）；SessionContext 非线程安全，桶内并发由调用方
（engine 执行串行化/外部锁）承担——当前远程下推路径不共享 ctx 执行，仅规划校验用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

from backend.semantics.execution import adapter

logger = logging.getLogger(__name__)


def policy_fingerprint(policy: Optional[dict[str, Any]], *, extra: str = "") -> str:
    """policy（enforcer 裁决产物）→ 稳定指纹：键序无关、值稳定序列化。"""
    canonical = json.dumps(policy or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{extra}|{canonical}".encode("utf-8")).hexdigest()[:16]


class SessionPool:
    """按 policy 指纹分桶的 SessionContext 池（有界 LRU）。"""

    def __init__(self, max_buckets: int = 32):
        self._max_buckets = max(1, int(max_buckets))
        self._buckets: OrderedDict[str, Any] = OrderedDict()
        self._views: dict[str, dict[str, str]] = {}  # fingerprint -> {view_name: view_sql}
        self._lock = threading.Lock()

    def get(self, fingerprint: str) -> Any:
        """取（或建）该指纹的 SessionContext；LRU 提前 + 超限逐出最久未用桶。

        逐出只回收 SessionContext，**保留视图登记**（policy 指纹不变则视图不变，
        桶重建时复放）；登记表仅随显式 drop() 清理。
        """
        with self._lock:
            ctx = self._buckets.pop(fingerprint, None)
            if ctx is None:
                ctx = adapter.new_session()
                self._rehydrate_views(fingerprint, ctx)
            self._buckets[fingerprint] = ctx
            while len(self._buckets) > self._max_buckets:
                self._buckets.popitem(last=False)
            return ctx

    def register_view(self, fingerprint: str, name: str, view_sql: str) -> None:
        """按指纹向会话注册命名视图（Phase 6.3 RLS/mask 过滤视图的强制挂点）。

        注册即登记（换桶/逐出后重建时复放），同名视图重复注册以最新为准。
        """
        ctx = self.get(fingerprint)  # 先取/建桶（新建时复放既有登记，不含新登记）
        with self._lock:
            self._views.setdefault(fingerprint, {})[name] = view_sql
        self._replace_view(ctx, name, view_sql)

    @staticmethod
    def _replace_view(ctx: Any, name: str, view_sql: str) -> None:
        # datafusion 54.x: register_view(name, df) 收 DataFrame（内部 into_view）；
        # 同名注册报 already exists，覆盖语义先撤旧
        try:
            ctx.deregister_table(name)
        except Exception:  # noqa: BLE001 — 不存在即忽略
            pass
        ctx.register_view(name, ctx.sql(view_sql))

    def _rehydrate_views(self, fingerprint: str, ctx: Any) -> None:
        """桶重建时复放视图登记。单个复放失败仅告警跳过（登记保留）——
        datafusion 视图注册为**急切解析**（依赖底层表已注册），表未就绪时跳过是
        fail-safe 的：强制视图缺失时查询会因缺表拒绝，不会未过滤放行；
        表就绪后由调用方重新 apply/复放（幂等）。
        """
        for name, view_sql in (self._views.get(fingerprint) or {}).items():
            try:
                self._replace_view(ctx, name, view_sql)
            except Exception as e:  # noqa: BLE001 — 保留登记，表就绪后复放
                logger.warning(
                    "[session] 视图复放暂跳过（底层表未就绪）fp=%s view=%s: %s",
                    fingerprint, name, e,
                )

    def drop(self, fingerprint: str) -> None:
        with self._lock:
            self._buckets.pop(fingerprint, None)
            self._views.pop(fingerprint, None)

    def size(self) -> int:
        with self._lock:
            return len(self._buckets)

    def view_names(self, fingerprint: str) -> list[str]:
        with self._lock:
            return sorted((self._views.get(fingerprint) or {}).keys())


_default_pool: Optional[SessionPool] = None
_default_lock = threading.Lock()


def get_session_pool() -> SessionPool:
    """进程级共享池（web 单进程内唯一）。"""
    global _default_pool
    if _default_pool is None:
        with _default_lock:
            if _default_pool is None:
                _default_pool = SessionPool()
    return _default_pool
