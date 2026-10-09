"""datafusion-python 薄适配模块 —— DataFusion API 单点收口（Phase 6.2）。

**锁版本纪律**：datafusion 升级必须只改本模块（PINNED_DATAFUSION + 兼容 shim），
升级影响不外溢到 engine/session/connectors 之外（计划 §10）；版本不符即 fail-loud，
禁止静默跑在未验证版本上。升级须过引擎层回归 + Phase 7 shadow 对拍后才可换锁。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 锁定版本（venv 已装 datafusion-54.1.0，Python 3.10 兼容）
PINNED_DATAFUSION = "54.1.0"


class DataFusionVersionError(RuntimeError):
    """datafusion 实际版本与锁定版本不符（升级须走薄适配回归）。"""


def check_version() -> str:
    """校验 datafusion 版本与锁定一致，返回实际版本。"""
    import datafusion

    version = getattr(datafusion, "__version__", "")
    if version != PINNED_DATAFUSION:
        raise DataFusionVersionError(
            f"datafusion 版本 {version!r} 与锁定版本 {PINNED_DATAFUSION!r} 不符；"
            "升级必须经 adapter.py 薄适配回归（引擎层单一收口）"
        )
    return version


def new_session():
    """创建 DataFusion SessionContext（先过版本锁）。"""
    check_version()
    from datafusion import SessionContext

    return SessionContext()


def explain_sql(ctx, sql: str) -> str:
    """EXPLAIN 逻辑计划文本（plan 期校验用，不执行）。"""
    batches = ctx.sql(f"EXPLAIN {sql}").collect()
    texts: list[str] = []
    for batch in batches:
        for row in batch.to_pylist():
            texts.append(f"{row.get('plan_type', '')}: {row.get('plan', '')}")
    return "\n".join(texts)
