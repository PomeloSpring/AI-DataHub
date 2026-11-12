"""多源知识检索统一入口 —— 跨 provider 扇出与名次归并的唯一实现处。

为什么需要这一层（而不是让 `knowledge_search` 继续直接问 qmind）：AS-BOT 现在可以
同时绑定平台同步语料（qmind）与外部只读语料（百炼），两者的调用方式、分数量级、
能力边界都不同。这个差异**必须**在服务端收口：

1. 通道差异不进 LLM 视野。`notebook_id` / `index_id` 属内部标识（护栏 §7），把它们
   放进工具入参只会诱导模型猜值并被资源护栏拒（`waker-datasource-domain` §2 记录过
   的 `datasource_id` 同类缺陷）。LLM 侧永远只有一个 `knowledge_search`。
2. 各家 score 不可比，混排只能用名次（RRF，见 `providers/base.merge_by_rank`）。
3. **逐源状态必须可见**：哪个库答了、哪个库没内容、哪个库调用失败，都要如实回给
   模型与评测，不能压成一个"看起来能用"的空结果（no-silent-degradation）。

历史缺陷（本层消灭的对象）：旧路径 Python 侧逐库单问 + first-hit-wins，"第一个有
命中的库直接返回"，而那个第一来自一条没有 ORDER BY 的 SQL 的返回序 —— 绑定顺序
决定答案来源，用户既看不到也调不了。
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from backend.modules.mind.rag.providers import PROVIDERS, provider_for
from backend.modules.mind.rag.providers.base import (Chunk, SourceStatus,
                                                     merge_by_rank)

logger = logging.getLogger(__name__)

#: provider 扇出并发上限。每路 qmind 检索会 spawn 一个 node 子进程、百炼是一路 HTTPS，
#: 不封顶会在共享宿主上把内存打满（与 `semantic_tools._MAX_CONCURRENT_RETRIEVE` 同纪律）。
_MAX_CONCURRENT_PROVIDERS = 4


def _parse_cfg(value) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _load_bound_kbs(ids: list[int]) -> list[dict]:
    """回源读绑定知识库（**所有**类型，不按 kb_type 预过滤）。

    与旧实现的关键差别：旧 `_load_qmind_kbs` 在 SQL 里 `kb_type='qmind'`，于是百炼/本地库
    被静默过滤掉，管理员在配置页看得见、检索时永远问不到。现在类型裁决交给
    provider 注册表，SQL 只负责取"启用的、在绑定集内的"库。
    """
    from backend.common.db import execute_query

    cols = "SELECT id, name, kb_type, source_config FROM adh_knowledge_bases "
    if ids:
        marks = ", ".join(["%s"] * len(ids))
        rows = execute_query(f"{cols}WHERE status = 'active' AND id IN ({marks})", tuple(ids))
    else:
        rows = execute_query(f"{cols}WHERE status = 'active' ORDER BY id")
    out = []
    for r in rows or []:
        cfg = _parse_cfg(r.get("source_config"))
        out.append({"id": r["id"], "name": r.get("name"), "kb_type": r.get("kb_type") or "",
                    "notebook_id": str(cfg.get("notebook_id") or "").strip(),
                    "cfg": cfg})
    return out


def _bound_kbs(kb_ids: list | None) -> list[dict]:
    """绑定库清单（带接入配置），走配置缓存 live 档。

    边界与 `qmind_retriever._bound_qmind_kbs` 同一条纪律：本缓存只覆盖"id → 接入配置"
    这一层映射，**不是授权裁决** —— 知识库是否仍启用/是否越界由未缓存的
    `resource_guard.validate_knowledge_binding` 每次实读 fail-loud 判定；KB 写入口同批
    失效，陈旧窗口上限就是一个 live TTL。查库异常不缓存成空集（工厂函数上抛，外层
    转告警 + 空清单，保持"不毁掉检索"但也不固化瞬时故障）。
    """
    from backend.common import as_bot_config_cache as config_cache

    if kb_ids is not None and not kb_ids:
        return []
    try:
        ids = [int(x) for x in (kb_ids or []) if str(x).strip()]
        if any(x <= 0 for x in ids):
            raise ValueError("知识库标识无效")
    except (TypeError, ValueError) as exc:
        raise ValueError("知识库授权列表无效") from exc
    # key 不去排序：集合同序则复用；实际清单来自 AS-BOT 存储的固定数组，同一绑定永远同序。
    key = f"{config_cache.KEY_KB_SOURCES}" + (",".join(str(x) for x in ids) if ids else "all")
    try:
        return config_cache.get_or_set(key, lambda: _load_bound_kbs(ids), live=True)
    except Exception as exc:  # noqa: BLE001 — 回源失败只告警不毁检索，但不写缓存
        logger.warning("[kb] load bound knowledge bases failed: %s", exc)
        return []


def _group_by_provider(kbs: list[dict]) -> tuple[dict, list[dict]]:
    """按 provider 分组。返回 ({provider: [kb]}, 未接入清单)。

    未接入的类型**不静默丢弃**：它们会带着原因进入 `sources`，让模型如实告诉用户
    "绑了但本批问不到"。
    """
    groups: dict = {}
    unsupported: list[dict] = []
    for kb in kbs or []:
        provider = provider_for(kb)
        if provider is None:
            unsupported.append(kb)
            continue
        groups.setdefault(provider, []).append(kb)
    return groups, unsupported


def _run_provider(provider, kbs: list[dict], question: str, top_k: int,
                  datasource_id: int) -> tuple[list[Chunk], list[dict], str]:
    """单 provider 检索；异常收口成"该源失败"，不炸整条检索链。

    这里捕获异常**不是**把失败掩成空结果：错因随 `status=failed` 进 sources，
    对外可见、评测可分桶（no-silent-degradation §2 的合格回退标准）。
    """
    try:
        res = provider.search(kbs, question, top_k, datasource_id=datasource_id)
        return list(res.chunks), list(res.sources), ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kb] provider=%s 检索异常: %s", provider.kb_type, exc)
        names = [str(k.get("name") or "") for k in kbs]
        return [], [{"kb": n, "status": SourceStatus.FAILED, "error": str(exc)[:200]}
                    for n in names], str(exc)[:200]


def knowledge_retrieve(
    question: str,
    datasource_id: int = 0,
    kb_ids: list | None = None,
    top_k: int = 5,
    system_scope: bool = False,
) -> dict:
    """多源知识检索：并行问所有绑定库，按名次归并，逐源状态可见。

    Args:
        question: 用户问题（业务原词）。
        datasource_id: 会话生效源。只用于 ① 平台同步语料的新鲜度比对 ② 无命中时回退
            本地元数据按源过滤；**不作为选库依据**（绑库由 AS-BOT 与角色授权决定）。
        kb_ids: AS-BOT 绑定的知识库 id 清单（服务端注入，不进 LLM 视野）。
        top_k: 归并后返回条数。
        system_scope: 系统能力形态标注（能力叠加），只随结果标注供分桶归因。

    Returns:
        命中: {chunks, count, rag_source, knowledge_bases, sources, citations,
              hit_object_keys, doc_stale}；有源失败但另有命中时额外带 degraded。
        未命中: 本地 graphrag 统一结果 dict，按事实标注 rag_source/degraded 与原因。
        黑盒：chunk 只保留业务可读出处（标题 + 库业务名 + 归属），内部标识一律剥除。
    """
    from backend.modules.mind.rag import qmind_retriever as qr

    def _mark_system(result: dict) -> dict:
        out = dict(result or {})
        if system_scope:
            out["system_scope"] = True
        return out

    kbs = _bound_kbs(kb_ids)
    groups, unsupported = _group_by_provider(kbs)

    if not groups:
        # 没有任何可问的通道：回退可以，但必须把"为什么没问成"说清楚
        reason = ""
        if kb_ids:
            names = "、".join(str(k.get("name") or "") for k in (kbs or unsupported) if k.get("name"))
            reason = (f"绑定的知识库没有已接入的检索通道"
                      + (f"（{names}）" if names else "")
                      + f"；本批支持：{', '.join(p.kb_type for p in PROVIDERS)}")
        result = qr._fallback_graphrag(question, datasource_id, tagged=bool(kb_ids))
        result = _mark_system(result)
        if reason:
            result["degraded"] = True
            result["degraded_reason"] = reason
        if unsupported:
            result["sources"] = [{"kb": str(k.get("name") or ""),
                                  "status": SourceStatus.NOT_SUPPORTED,
                                  "error": "该类型的检索通道本批未接入"} for k in unsupported]
        return result

    # ── 并行扇出（provider 之间互不等待；单 provider 内部自行决定是否一次问多库）──
    workers = max(1, min(_MAX_CONCURRENT_PROVIDERS, len(groups)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_provider, p, ks, question, top_k, datasource_id)
                   for p, ks in groups.items()]
        ran = [f.result() for f in futures]

    per_source = [s for _chunks, sources, _err in ran for s in sources]
    groups_chunks = [chunks for chunks, _sources, _err in ran]
    merged = merge_by_rank(groups_chunks, top_k)

    if merged:
        public = [c.to_public() for c in merged]
        rag_source = _rag_source_of(ran)
        out = {
            "chunks": public,
            "count": len(public),
            "rag_source": rag_source,
            "knowledge_bases": sorted({c.kb_name for c in merged if c.kb_name}),
            "sources": per_source,
            "citations": _citations_of(merged),
            "hit_object_keys": qr.extract_object_keys(public),
            # 归并后各库新鲜度可能不同：整批结论取"是否存在过期"，逐条另在 chunk.doc_stale
            "doc_stale": True if any(c.doc_stale for c in merged) else (
                False if all(c.doc_stale is False for c in merged) else None),
        }
        failed = [s for s in per_source if s.get("status") in (SourceStatus.FAILED,
                                                               SourceStatus.CIRCUIT_OPEN)]
        if failed:
            out["degraded"] = True
            out["degraded_reason"] = "部分知识库未能检索: " + "; ".join(
                f"{s.get('kb')}({s.get('error') or s.get('status')})" for s in failed)[:300]
        return _mark_system(out)

    # 全部通道都问过且无命中：回退本地元数据（透明，且 rag_source 可区分）
    errored = [s for s in per_source if s.get("status") in (SourceStatus.FAILED,
                                                           SourceStatus.CIRCUIT_OPEN,
                                                           SourceStatus.NOT_SUPPORTED)]
    result = qr._fallback_graphrag(question, datasource_id, tagged=True)
    result = _mark_system(result)
    result["sources"] = per_source
    if errored:
        result["degraded"] = True
        result["degraded_reason"] = "知识库未能完成检索: " + "; ".join(
            f"{s.get('kb')}({s.get('error') or s.get('status')})" for s in errored)[:300]
    return result


def _rag_source_of(ran: list[tuple]) -> str:
    """归因标签：命中来自哪些通道。多通道同时命中记 `kb_multi`（评测按此分桶）。"""
    hit_types = []
    for chunks, _sources, _err in ran:
        if chunks:
            provider = str(chunks[0].provider or "")
            if provider and provider not in hit_types:
                hit_types.append(provider)
    if not hit_types:
        return "none"
    if len(hit_types) == 1:
        return hit_types[0]
    return "kb_multi"


def _citations_of(chunks: list[Chunk]) -> list[dict]:
    """给模型的出处清单：只含业务可读标题与库名，按 (标题, 库) 去重保序。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    for c in chunks:
        key = (c.title, c.kb_name)
        if not c.title or key in seen:
            continue
        seen.add(key)
        out.append({"title": c.title, "knowledge_base": c.kb_name,
                    "corpus_origin": c.corpus_origin})
    return out
