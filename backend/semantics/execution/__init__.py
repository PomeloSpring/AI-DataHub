"""语义执行引擎层（Phase 6.2）—— datafusion-python 内嵌 + 远程下推。

对外门面：
- `get_engine()`：进程级 SemanticEngine 单例（规划/校验/下推编排）；
- `get_session_pool()` / `policy_fingerprint`：按 policy 指纹分桶的 SessionContext 管理；
- `PINNED_DATAFUSION` / `check_version`：datafusion 版本锁（升级走 adapter.py 单点）。

分层：本包为 L1（semantics），只依赖 common/core；消费方切换见计划 §6.4。
"""

from __future__ import annotations

import threading
from typing import Optional

from backend.semantics.execution.adapter import (
    PINNED_DATAFUSION,
    DataFusionVersionError,
    check_version,
    explain_sql,
    new_session,
)
from backend.semantics.execution.engine import SemanticEngine
from backend.semantics.execution.session import (
    SessionPool,
    get_session_pool,
    policy_fingerprint,
)

_engine: Optional[SemanticEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> SemanticEngine:
    """进程级引擎单例。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = SemanticEngine()
    return _engine


__all__ = [
    "PINNED_DATAFUSION",
    "DataFusionVersionError",
    "SemanticEngine",
    "SessionPool",
    "check_version",
    "explain_sql",
    "get_engine",
    "get_session_pool",
    "new_session",
    "policy_fingerprint",
]
