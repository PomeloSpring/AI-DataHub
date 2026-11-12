"""百炼（阿里云 Model Studio / DashScope）provider — **只读外部语料**通道。

定位（2026-11-05 拍板）：百炼里的资料由企业自行在百炼侧维护（产品手册、SOP、外部法规），
平台**只检索、不写入**。因此本 provider 的 `capabilities.write=False`，本体上云文档与
内容中心文章的同步目标一律不含它 —— 这是用户要看到的产品事实，不能在检索链路里
被做成静默分支（no-silent-degradation）。

通道形态：DashScope RAG 检索接口 `POST {endpoint}/api/v1/indices/rag/index/retrieve`，
Bearer 鉴权，body `{index_id, query, top_k}`。**单 index 一次调用**（与 qmind 的多
notebook 联合召回不同），所以多百炼库要逐库问，跨源名次由上层 RRF 归并。

凭据纪律：API Key 只从环境变量读（`backend/.env` 的 `DASHSCOPE_API_KEY`，与 QMIND_TOKEN
同一条律），**绝不写入 `source_config`**；`source_config` 里只允许出现凭据的引用名。
返回体里的 `Metadata` 含带过期时间的签名 URL（`file_path`/`image_url`/`audio_url`/
`video_url`，官方文档亦说明 `file_path` 无意义）—— 一律按字段白名单取，不整份透出。
"""
from __future__ import annotations

import logging

from .base import (Capabilities, Chunk, KnowledgeProvider, ProviderResult, SourceStatus,
                   CORPUS_ORIGIN_EXTERNAL)

logger = logging.getLogger(__name__)

#: 本批只接了阿里云百炼；`cloud_rag` 下的其他厂商（混元/千帆/智谱）配置允许存在，
#: 但检索时显式回报"未接入"，不静默跳过（否则管理员在页面上看不出差别）。
SUPPORTED_PROVIDERS = ("aliyun",)

_RETRIEVE_PATH = "/api/v1/indices/rag/index/retrieve"


def _cfg(kb: dict) -> dict:
    cfg = kb.get("cfg")
    if isinstance(cfg, dict):
        return cfg
    import json
    raw = kb.get("source_config") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


def _resolve_api_key(cfg: dict) -> tuple[str, str]:
    """按 `credential_ref`（可选）→ `DASHSCOPE_API_KEY` 解析凭据。返回 (key, 错因)。"""
    import os

    from backend.common import config as _cfg_module

    ref = str(cfg.get("credential_ref") or "").strip()
    if ref:
        # 引用名指向环境变量：多套凭据时不改代码，但引用名本身不是密钥
        val = (os.getenv(ref) or "").strip()
        if val:
            return val, ""
        return "", f"凭据引用「{ref}」在当前环境没有对应值"
    key = str(getattr(_cfg_module, "DASHSCOPE_API_KEY", "") or "").strip()
    if key:
        return key, ""
    return "", "未配置百炼 API Key（backend/.env 的 DASHSCOPE_API_KEY）"


def _pick(node: dict, *keys: str) -> str:
    """按白名单取字段：百炼不同版本用大小写两种写法，且绝不整份透传 metadata。"""
    for k in keys:
        val = node.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _norm_node(node: dict) -> dict:
    """百炼 node → 归一 chunk。metadata 是 JSON 字符串或 dict，只取可读标题。"""
    import json

    meta = node.get("Metadata") or node.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) or {}
        except (TypeError, ValueError):
            meta = {}
    title = ""
    if isinstance(meta, dict):
        title = _pick(meta, "title", "doc_name", "docName", "hier_title", "nid")
    if not title:
        title = _pick(node, "Title", "title", "doc_name")
    text = _pick(node, "Text", "text") or _pick(node, "content") or (
        str(meta.get("content")) if isinstance(meta, dict) and meta.get("content") else "")
    score = node.get("Score", node.get("score"))
    out = {"text": text, "title": title}
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        out["score"] = float(score)
    return out


class BailianProvider(KnowledgeProvider):
    kb_type = "cloud_rag"
    label = "百炼知识库（外部只读语料）"
    corpus_origin = CORPUS_ORIGIN_EXTERNAL
    capabilities = Capabilities(read=True, write=False, doc_tree=False, freshness_stamp=False)

    def supports(self, kb: dict) -> bool:
        return (str(kb.get("kb_type") or "") == self.kb_type
                and str(_cfg(kb).get("provider") or "aliyun").strip().lower()
                in SUPPORTED_PROVIDERS)

    def describe(self, kb: dict) -> dict:
        out = super().describe(kb)
        out["note"] = "外部维护、平台只读；不作为指标/维度名称与口径的权威来源"
        return out

    def search(self, kbs: list[dict], question: str, top_k: int,
               datasource_id: int = 0) -> ProviderResult:
        res = ProviderResult()
        targets: list[tuple[dict, dict]] = []
        for kb in kbs or []:
            cfg = _cfg(kb)
            name = str(kb.get("name") or "")
            if str(cfg.get("provider") or "aliyun").strip().lower() not in SUPPORTED_PROVIDERS:
                res.add_source(name, SourceStatus.NOT_SUPPORTED,
                               f"云厂商「{cfg.get('provider')}」的检索通道本批未接入")
                continue
            if not str(cfg.get("index_id") or "").strip():
                res.add_source(name, SourceStatus.NOT_SUPPORTED,
                               "未填写知识库 ID（index_id），请在系统配置「知识库」里补齐")
                continue
            targets.append((kb, cfg))
        if not targets:
            return res

        # 凭据**逐库**解析（各库可指向不同 credential_ref）：缺凭据是配置问题，
        # 不是“库里没有”—— 该源如实报 failed 并带上原因，不得返回空列表冒充无结果。
        ready: list[tuple[dict, dict, str]] = []
        for kb, kb_cfg in targets:
            api_key, cred_err = _resolve_api_key(kb_cfg)
            if not api_key:
                res.add_source(str(kb.get("name") or ""), SourceStatus.FAILED, cred_err)
                continue
            ready.append((kb, kb_cfg, api_key))
        if not ready:
            return res

        limit = max(1, int(top_k))
        collected: list[dict] = []
        for kb, cfg, api_key in ready:
            name = str(kb.get("name") or "")
            if not self._breaker_allowed():
                res.add_source(name, SourceStatus.CIRCUIT_OPEN,
                               "百炼检索熔断中（连续失败已短路），本次未发起调用")
                continue
            nodes, err = self._retrieve(cfg, api_key, question, limit)
            if err:
                self._breaker_record(False)
                res.add_source(name, SourceStatus.FAILED, err)
                continue
            self._breaker_record(True)
            if not nodes:
                res.add_source(name, SourceStatus.EMPTY)
                continue
            res.add_source(name, SourceStatus.OK)
            for node in nodes:
                item = _norm_node(node)
                if item.get("text"):
                    collected.append({**item, "kb_name": name,
                                      "index_id": str(cfg.get("index_id") or "")})

        # 逐库调用时各库分不可比（本接口不做跨库 rerank），所以按"每库内部名次"
        # 交错合并出本 provider 的名次 —— 与上层 RRF 同一口径（名次优先）。
        by_kb: dict[str, list[dict]] = {}
        for item in collected:
            by_kb.setdefault(item["kb_name"], []).append(item)
        for rank, item in enumerate(_interleave(list(by_kb.values()))):
            res.chunks.append(Chunk(
                text=str(item.get("text") or ""),
                title=str(item.get("title") or "") or item["kb_name"],
                kb_name=item["kb_name"],
                provider=self.kb_type,
                rank=rank,
                score=item.get("score"),
                doc_stale=None,          # 外部语料无平台版本戳，不下新鲜度结论
                corpus_origin=self.corpus_origin,
                ref={"index_id": item.get("index_id") or ""},
            ))
        return res

    # ── 通道细节 ─────────────────────────────────────────────────────

    def _retrieve(self, cfg: dict, api_key: str, question: str, top_k: int):
        """单 index 检索。返回 (nodes, err)；失败与无命中严格区分，不回退。"""
        import requests

        from backend.common import config as _cfg_module

        endpoint = str(getattr(_cfg_module, "BAILIAN_ENDPOINT", "")
                       or "https://dashscope.aliyuncs.com").rstrip("/")
        url = f"{endpoint}{_RETRIEVE_PATH}"
        timeout = float(getattr(_cfg_module, "BAILIAN_TIMEOUT", 10) or 10)
        body = {"index_id": str(cfg.get("index_id") or "").strip(),
                "query": question, "top_k": int(top_k)}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        # 官方文档：接口幂等但响应可能较慢，建议合理设置超时与重试 —— 这里只补一次
        for attempt in (0, 1):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=timeout)
            except requests.exceptions.RequestException as exc:
                if attempt == 0:
                    continue
                return [], f"百炼检索请求失败: {type(exc).__name__}"
            if resp.status_code in (429, 500, 502, 503) and attempt == 0:
                continue
            if resp.status_code >= 400:
                # 对外文案只给状态码与可读原因，不回显响应体（可能含内部标识/URL）
                return [], f"百炼检索失败（HTTP {resp.status_code}）"
            try:
                data = resp.json() or {}
            except ValueError:
                return [], "百炼返回非 JSON 响应"
            nodes = data.get("nodes")
            if nodes is None:
                out = data.get("output") or data.get("data") or {}
                nodes = out.get("nodes") if isinstance(out, dict) else None
            if nodes is None:
                return [], "百炼返回结构不受支持（缺 nodes 清单）"
            if not isinstance(nodes, list):
                return [], "百炼返回的 nodes 不是列表"
            return [n for n in nodes if isinstance(n, dict)], ""
        return [], "百炼检索请求未成功（已重试 1 次）"

    def probe(self, kb: dict) -> tuple[bool, str]:
        cfg = _cfg(kb)
        if not str(cfg.get("index_id") or "").strip():
            return False, "未填写知识库 ID（index_id）"
        api_key, err = _resolve_api_key(cfg)
        if not api_key:
            return False, err
        nodes, err = self._retrieve(cfg, api_key, "连通性测试", 1)
        if err:
            return False, err
        return True, ""

    # 熔断按 provider 分键：百炼抖动不得熔掉 qmind（历史上全局单键就犯过这个错）
    _BREAKER_PREFIX = "chatbi:kb:bailian:breaker"
    _CB_FAIL_THRESHOLD = 3
    _CB_COOLDOWN_SEC = 120

    def _breaker_client(self):
        from backend.common.cache.factory import get_cache
        # 熔断是协调状态，必须使用会抛错的 Redis 原语，不能走 best-effort 缓存接口。
        return get_cache("qmind_breaker")._get_redis()

    def _breaker_allowed(self) -> bool:
        try:
            return not bool(self._breaker_client().exists(f"{self._BREAKER_PREFIX}:open"))
        except Exception as exc:  # noqa: BLE001 — Redis 不可用时照常尝试检索，不因此拒答
            logger.warning("[bailian] breaker unavailable, proceeding: %s", exc)
            return True

    def _breaker_record(self, success: bool) -> None:
        try:
            self._breaker_client().eval("""
                if ARGV[1] == '1' then
                    redis.call('DEL', KEYS[1], KEYS[2]); return 0
                end
                local n = redis.call('INCR', KEYS[1])
                if n >= tonumber(ARGV[2]) then
                    redis.call('SET', KEYS[2], '1', 'EX', ARGV[3])
                    redis.call('DEL', KEYS[1]); return 1
                end
                return 0
            """, 2, f"{self._BREAKER_PREFIX}:fails", f"{self._BREAKER_PREFIX}:open",
                "1" if success else "0", self._CB_FAIL_THRESHOLD, self._CB_COOLDOWN_SEC)
        except Exception as exc:  # noqa: BLE001 — 旁路记账不得影响检索主链路
            logger.debug("[bailian] breaker record failed: %s", exc)


def _interleave(groups: list[list[dict]]) -> list[dict]:
    """各库内部保序、库间轮询交错：在没有跨库 rerank 的前提下，这是唯一不偏向
    任何单一库的合并次序（与 `base.merge_by_rank` 的名次口径一致）。"""
    out: list[dict] = []
    depth = 0
    while any(len(g) > depth for g in groups):
        for g in groups:
            if len(g) > depth:
                out.append(g[depth])
        depth += 1
    return out
