"""知识库 provider 契约 — 检索通道差异在服务端的收口点。

为什么要有这一层：AS-BOT 可以绑定多种知识库（qmind 云端 notebook、百炼外部语料、
尚未接入检索通道的 local/vector_db），它们的调用方式完全不同（Node 子进程 / HTTPS
API）。这个差异**不得**暴露给 LLM，两条理由都是踩过的坑：

1. `notebook_id` / `index_id` / 本地路径属内部标识（护栏 §7）。工具 schema 里要求
   LLM 填它，LLM 拿不到真实值只能猜，猜错就被资源护栏拒并空耗多轮 —— 与
   `waker-datasource-domain` §2 记录的 `datasource_id` 猜值缺陷同源，不得重演。
2. 一旦按通道拆成 `search_qmind` / `search_bailian`，模型会"挑一个看起来最像的查完
   就答"，多源交叉佐证的价值归零 —— 而多源恰恰是引入外部语料的唯一理由。

所以 LLM 侧永远只有一个 `knowledge_search`；本层负责：按 kb_type 分派 provider →
并行扇出 → **跨源归并只吃名次不吃分数**（各家 score 量级不可比）→ 逐源状态可见。

能力差异必须显式建模（`Capabilities`），不得在检索链路里做最小公分母：
"百炼是外部只读语料、平台不向其写入"是用户要看到的产品事实，不是一个静默 if 分支
（no-silent-degradation）。
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: 语料归属：平台同步派生（可判新鲜度、可写）vs 外部维护（只读、无版本戳）
CORPUS_ORIGIN_PLATFORM_SYNCED = "platform_synced"
CORPUS_ORIGIN_EXTERNAL = "external"

#: provider 原始返回里**不得**进对外响应的键：对象存储路径、带过期时间的签名 URL、
#: 内部标识、凭据。护栏 §7 与 `qmind_retriever.source_node()` 同一条纪律，
#: 百炼文档亦明确 `file_path` 无意义、`image_url`/`audio_url`/`video_url` 附过期时间。
INTERNAL_KEYS = frozenset({
    "originUrl", "sourceUri", "uri", "origin_url",
    "file_path", "image_url", "audio_url", "video_url",
    "notebookId", "notebook_id", "sourceId", "source_id", "chunkId", "chunk_id",
    "index_id", "indexId", "workspace_id", "workspaceId",
    "doc_id", "docId", "pipeline_id", "pipelineId", "nid", "_id",
    "api_key", "apiKey", "access_key_id", "access_key_secret", "credential",
})

#: 跨源名次融合（RRF）常数：k=60 是信息检索惯例值，写进常量便于日后调参留痕。
#: 为什么不用各家 score：qmind 是云端 rerank 的 0~1 相关度、百炼是它自家相似度分、
#: 本地 BM25 是词频分 —— 直接混排等于随机排序。名次是唯一跨源可比的信号。
RRF_K = 60


@dataclass(frozen=True)
class Capabilities:
    """provider 能力位：让"这个库能做什么"成为可查询的事实，而不是靠调用点猜。"""

    read: bool = True            # 能否检索
    write: bool = False          # 平台能否向其推送语料（本体上云、内容中心同步）
    doc_tree: bool = False       # 能否给出文档目录树（管理页「知识库文档」）
    freshness_stamp: bool = False  # 语料是否带平台版本戳（可判 doc_stale）

    def as_dict(self) -> dict:
        return {"read": self.read, "write": self.write, "doc_tree": self.doc_tree,
                "freshness_stamp": self.freshness_stamp}


@dataclass
class Chunk:
    """归一化检索片段。

    `ref` 只给服务端排障与去重用，**绝不进任何对外响应**（LLM、SSE、审计文本都不行），
    对外一律走 `to_public()`。
    """

    text: str
    title: str = ""              # 文档标题（业务可读；缺失时由 provider 退到库业务名）
    kb_name: str = ""            # 知识库业务名（出处标注用）
    provider: str = ""           # qmind | bailian
    rank: int = 0                # 源内名次（0-based），跨源归并只看它
    score: float | None = None   # 各家原始分，仅展示，不参与跨源排序
    doc_stale: bool | None = None
    corpus_origin: str = CORPUS_ORIGIN_EXTERNAL
    ref: dict = field(default_factory=dict)
    also_in: list = field(default_factory=list)   # 去重后标注"同一内容还在哪些库"

    @property
    def digest(self) -> str:
        """正文指纹：跨源去重键（同文重复登记在多个库时只保留权威那份）。"""
        return hashlib.sha256((self.text or "").strip().encode("utf-8")).hexdigest()

    def to_public(self) -> dict:
        """对外形状。保留 `content`/`title` 既有键名（下游提取器与评测契约读它们）。"""
        out = {
            "content": self.text,
            "title": self.title,
            "knowledge_base": self.kb_name,
            "provider": self.provider,
            "corpus_origin": self.corpus_origin,
            "doc_stale": self.doc_stale,
        }
        if self.score is not None:
            out["score"] = round(float(self.score), 4)
        if self.also_in:
            out["also_in"] = list(self.also_in)
        return out


class SourceStatus:
    """逐源状态取值清单（对外可见，不得压成"成功/失败"两态）。"""

    OK = "ok"                     # 有命中
    EMPTY = "empty"               # 问过，库里确实没有
    FAILED = "failed"             # 调用失败（原因见 error）
    CIRCUIT_OPEN = "circuit_open"  # 该 provider 熔断中，本次未发起调用
    NOT_SUPPORTED = "not_supported"  # 该 kb_type 检索通道本批未接入


def redact(payload):
    """递归剥掉内部标识键（护栏 §7）。provider 原始返回一律过这道再落地。"""
    if isinstance(payload, dict):
        return {k: redact(v) for k, v in payload.items() if k not in INTERNAL_KEYS}
    if isinstance(payload, list):
        return [redact(v) for v in payload]
    return payload


@dataclass
class ProviderResult:
    """一个 provider 一轮检索的结果。逐源状态与分块同层返回，不把失败压成空列表。

    `sources` 必须对外可见：用户与评测都要能分辨“这个库里没写”与“这个库没问到”。
    """

    chunks: list[Chunk] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [str(s.get("error") or "") for s in self.sources if s.get("error")]

    def add_source(self, kb_name: str, status: str, error: str = "") -> None:
        self.sources.append({"kb": kb_name, "status": status,
                             **({"error": error[:200]} if error else {})})


class KnowledgeProvider(ABC):
    """一种检索通道 = 一个 provider。新增通道只加实现，不改调用点。"""

    #: `adh_knowledge_bases.kb_type` 的取值（qmind / cloud_rag / ...）
    kb_type: str = ""
    #: 对外归因标签（业务可读，与 `prompt_composer` 的知识库段同源）
    label: str = ""
    capabilities: Capabilities = Capabilities()
    #: 语料归属：决定跨源 tie-break 的权威优先级与“能否写入”
    corpus_origin: str = CORPUS_ORIGIN_EXTERNAL

    def supports(self, kb: dict) -> bool:
        """该 provider 能否消费这条知识库行（cloud_rag 还要看 provider 字段）。"""
        return str(kb.get("kb_type") or "") == self.kb_type

    @abstractmethod
    def search(self, kbs: list[dict], question: str, top_k: int,
               datasource_id: int = 0) -> ProviderResult:
        """在给定库清单内检索。实现内部可自行排序，但 `Chunk.rank` 必须是
        **本 provider 的名次**（0-based），跨源归并只看名次不吃各家分。

        `datasource_id` 只用于平台同步语料的新鲜度比对（本体已改但云文档未同步时
        内容仅供参考），不是选库依据 —— 绑哪些库由 AS-BOT 与角色授权决定。

        实现方**可以**一次问多个库（qmind 的 `--nb a,b,c` 由云端联合召回并 rerank），
        也可以逐库调用（百炼低层接口单 index）。无论哪种，失败必须进 `sources`，
        不得把“没问到”与“库里没有”合并成一个空列表。
        """

    def probe(self, kb: dict) -> tuple[bool, str]:
        """连通性/配置自检（管理页「测试连接」）。默认只查配置完整性，子类可实调。"""
        return True, ""

    def describe(self, kb: dict) -> dict:
        """给提示词与管理页的业务措辞：只说归属与边界，不暴露通道实现。"""
        return {
            "name": str(kb.get("name") or ""),
            "provider": self.label or self.kb_type,
            "corpus_origin": self.corpus_origin,
            "capabilities": self.capabilities.as_dict(),
        }


def merge_by_rank(groups: list[list[Chunk]], top_k: int) -> list[Chunk]:
    """跨源 RRF 归并 + 去重 + tie-break。

    `groups` 每个元素是一个 provider 的**已排序**结果（组内顺序即名次，不重排）。

    为什么是 RRF 而不是"取 max(score)"或"min-max 归一后加权"：各家分数量级与语义
    不同，且某源只返回 1 条时 min-max 会把它顶成满分 —— 名次融合对分数分布无假设，
    加第四个 provider 也不需要重新标定。

    tie-break 依次：① 平台同步语料优先于外部只读语料（口径权威归属）；
    ② `doc_stale=True` 降级（本体已改但云文档未同步时内容仅供参考）；
    ③ 正文指纹去重，重复只保留权威那份并把其余库名记进 `also_in`（不静默丢弃）。
    """
    fused: dict[str, dict] = {}
    for group in groups or []:
        for chunk in group or []:
            key = chunk.digest
            slot = fused.setdefault(key, {"chunk": chunk, "score": 0.0})
            slot["score"] += 1.0 / (RRF_K + max(0, int(chunk.rank)) + 1)
            cur = slot["chunk"]
            # `also_in` 只记“除了保留那份之外还在哪些库”，包含自身就是噪声
            others = {cur.kb_name, chunk.kb_name}
            if _is_more_authoritative(chunk, cur):
                slot["chunk"] = chunk
                chunk.also_in = sorted({*chunk.also_in, *others} - {chunk.kb_name})
            else:
                cur.also_in = sorted({*cur.also_in, *others} - {cur.kb_name})

    ranked = sorted(
        fused.values(),
        key=lambda s: (-s["score"],
                       0 if s["chunk"].corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED else 1,
                       1 if s["chunk"].doc_stale else 0,
                       s["chunk"].kb_name),
    )
    out = [s["chunk"] for s in ranked]
    for i, c in enumerate(out):
        c.rank = i          # 归并后的名次（对外只作展示与审计，不回写各家分）
    return out[: max(1, int(top_k))] if out else []


def _is_more_authoritative(a: Chunk, b: Chunk) -> bool:
    """同文重复时判定保留哪份：平台同步 > 外部；未过期 > 过期；其余保先来者。"""
    rank_a = (0 if a.corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED else 1,
              1 if a.doc_stale else 0)
    rank_b = (0 if b.corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED else 1,
              1 if b.doc_stale else 0)
    return rank_a < rank_b
