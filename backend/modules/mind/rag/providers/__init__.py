"""知识库检索通道注册表 —— `kb_type → provider` 的唯一分派点。

为什么必须集中一处：同一份"哪些类型真的能被检索"的判断，要在**三个消费点**同源
（检索分派、AS-BOT 绑定校验、前端选项过滤）。历史上这类清单散写导致"配置页能选、
检索时被静默过滤"的空承诺（`menu-permission-sync` 同一类漂移）。新增通道只在这里
登记，调用点一律读 `supported_kb_types()`。

注意 `provider_for` 的返回口径：**按 kb_type 命中就返回 provider**，具体某个库能不能
真问（云厂商是否已接入、index_id 是否填了）由 provider 自己在 `search` 里逐源回报
`not_supported` + 原因。这样"绑了 local 库"不会被静默丢弃，而是明确告知未接入。
"""
from __future__ import annotations

from .bailian import BailianProvider
from .base import (Capabilities, Chunk, KnowledgeProvider, ProviderResult, SourceStatus,
                   CORPUS_ORIGIN_EXTERNAL, CORPUS_ORIGIN_PLATFORM_SYNCED, RRF_K, merge_by_rank,
                   redact)
from .qmind import QMindProvider

#: 登记顺序即并行扇出顺序（不影响结果：跨源次序由名次归并决定，不由登记顺序决定）
PROVIDERS: tuple[KnowledgeProvider, ...] = (QMindProvider(), BailianProvider())

_BY_KB_TYPE = {p.kb_type: p for p in PROVIDERS}


def provider_for(kb: dict) -> KnowledgeProvider | None:
    """按 `kb_type` 取通道；未登记的类型返回 None（调用方必须显式暴露，不得静默跳过）。"""
    return _BY_KB_TYPE.get(str(kb.get("kb_type") or ""))


def supported_kb_types() -> tuple[str, ...]:
    """已接入检索通道的知识库类型清单（绑定校验与前端过滤共用这一份真值）。"""
    return tuple(_BY_KB_TYPE)


def describe(kb: dict) -> dict:
    """单库的业务描述（提示词知识库段与管理页同源消费；不含接入配置与凭据）。"""
    provider = provider_for(kb)
    if provider is None:
        # 未登记类型不下语料归属结论（拿 external 冒充“外部维护”会把本地库说成别人的库）
        kb_type = str(kb.get("kb_type") or "未登记")
        return {"name": str(kb.get("name") or ""), "provider": kb_type,
                "corpus_origin": "", "readable": False,
                "reason": f"「{kb_type}」类型的检索通道本批未接入"}
    out = provider.describe(kb)
    out["readable"] = bool(provider.capabilities.read and provider.supports(kb))
    if not out["readable"]:
        out["reason"] = "该库的厂商/配置尚未接入检索通道"
    return out


__all__ = [
    "PROVIDERS", "Capabilities", "Chunk", "KnowledgeProvider", "ProviderResult",
    "SourceStatus", "CORPUS_ORIGIN_PLATFORM_SYNCED", "CORPUS_ORIGIN_EXTERNAL", "RRF_K",
    "merge_by_rank", "redact", "provider_for", "supported_kb_types", "describe",
]
