"""qmind provider — Qoder 云端知识库（notebook）通道。

平台派生语料的落点：本体上云文档（`to_cloud_md`）与内容中心文章都同步到这里，
所以它的语料**带版本戳**，可以判新鲜度（`doc_stale`）—— 这是"平台同步库可作口径解释、
外部语料不可"这条权威划分的物理依据，不是措辞偏好。

CLI 通道实现（子进程、超时、熔断、chunk 归一）全部在 `qmind_retriever`，本层只做
provider 契约适配与逐源状态还原：一次 `--nb a,b,c` 是云端联合召回，结果里带
`notebookId`，因此仍能按库标注"谁答的"，不必退回逐库单问。
"""
from __future__ import annotations

import logging

from .base import (Capabilities, Chunk, KnowledgeProvider, ProviderResult, SourceStatus,
                   CORPUS_ORIGIN_PLATFORM_SYNCED)

logger = logging.getLogger(__name__)


def _notebook_of(kb: dict) -> str:
    cfg = kb.get("cfg") if isinstance(kb.get("cfg"), dict) else {}
    return str(kb.get("notebook_id") or cfg.get("notebook_id") or "").strip()


class QMindProvider(KnowledgeProvider):
    kb_type = "qmind"
    label = "QMind 云端知识库"
    corpus_origin = CORPUS_ORIGIN_PLATFORM_SYNCED
    capabilities = Capabilities(read=True, write=True, doc_tree=True, freshness_stamp=True)

    def search(self, kbs: list[dict], question: str, top_k: int,
               datasource_id: int = 0) -> ProviderResult:
        from backend.modules.mind.rag import qmind_retriever as qr

        res = ProviderResult()
        usable: list[tuple[dict, str]] = []
        for kb in kbs or []:
            nb = _notebook_of(kb)
            if not nb:
                # 配置缺失不是"库里没有"：必须说清是没绑 notebook，否则用户以为空库
                res.add_source(str(kb.get("name") or ""), SourceStatus.NOT_SUPPORTED,
                               "该知识库还没有绑定云端 notebook，请在系统配置「知识库」里导入")
                continue
            usable.append((kb, nb))
        if not usable:
            return res

        if not qr._breaker_allowed():
            for kb, _nb in usable:
                res.add_source(str(kb.get("name") or ""), SourceStatus.CIRCUIT_OPEN,
                               "qmind 知识库熔断中（连续失败已短路），本次未发起云端检索")
            return res

        ids = [nb for _kb, nb in usable]
        name_by_nb = {nb: str(kb.get("name") or "") for kb, nb in usable}
        # 各库 top_k 取最大值作为联合召回上限：合并后无法再按库分配额度，
        # 取最大值是保守做法（宁可多召回后截，不能让某个库拿不到名额）。
        limit = max([int(((kb.get("cfg") or {}).get("top_k")) or top_k)
                     for kb, _nb in usable] or [int(top_k)])
        got = qr.retrieve_notebooks(ids, question, limit)
        chunks = got["chunks"]
        stale = qr._evaluate_doc_freshness(datasource_id, chunks).get("doc_stale") if chunks else None

        hit_by_nb = {str(c.get("_notebook_id") or "") for c in chunks}
        failed = bool(got["errors"])
        for kb, nb in usable:
            if not chunks and failed:
                res.add_source(str(kb.get("name") or ""), SourceStatus.FAILED,
                               "; ".join(got["errors"]))
            elif nb in hit_by_nb:
                res.add_source(str(kb.get("name") or ""), SourceStatus.OK)
            elif failed:
                # 有命中但也有批次失败：命中的库算 ok，问过的其他库只能标失败（不冒充空）
                res.add_source(str(kb.get("name") or ""), SourceStatus.FAILED,
                               "; ".join(got["errors"]))
            else:
                res.add_source(str(kb.get("name") or ""), SourceStatus.EMPTY)

        for i, c in enumerate(chunks):
            nb = str(c.get("_notebook_id") or "")
            res.chunks.append(Chunk(
                text=str(c.get("content") or ""),
                # 标题缺失时退回库名：出处标注不能因为云端没给 sourceTitle 就整个消失
                title=str(c.get("title") or "") or name_by_nb.get(nb, ""),
                kb_name=name_by_nb.get(nb, "") or self.label,
                provider=self.kb_type,
                rank=i,
                score=c.get("score"),
                doc_stale=stale,
                corpus_origin=self.corpus_origin,
                ref={"notebook_id": nb, "source_id": str(c.get("_source_id") or "")},
            ))
        return res

    def probe(self, kb: dict) -> tuple[bool, str]:
        """连通性自检：探测 notebook 对当前凭据是否可达（以 exit code 为准）。"""
        from backend.modules.mind.rag import qmind_retriever as qr

        nb = _notebook_of(kb)
        if not nb:
            return False, "未绑定云端 notebook"
        ok, err = qr.probe_notebook(nb)
        return ok, err or ""
