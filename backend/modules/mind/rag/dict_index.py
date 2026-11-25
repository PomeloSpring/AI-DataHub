"""指标/维度字典的条目级检索（get_metrics 发现层）。

为什么需要（缺陷由来）
--------------------
get_metrics 原先的形态是「关键词命中 → 把命中对象的**全部**指标/维度补全下发」。
泛词一次命中 15 个对象时，一次返回 70,598 字符，被 agent 运行时判「输出过大」整份落盘
（会话 1783069459352），文件又在会话沙箱之外读不回来，模型手里等于零目录，最后把
「没查到」说成「平台没有该口径」。发现层要回的是**命中条目 + 对象计数**，明细按对象
下钻，而不是把整本目录塞进上下文。

口径
----
- 检索面与 ``scoped_metadata._kw_hay`` 同源：``name / name_en / aliases / value_labels``
  （枚举业务名）。**不含 description**：口径文字会意外命中，模型据此把两个概念当同一个。
- 不做 embedding、不依赖外网：BM25（``bm25.py``）是纯词频稀疏检索，中文走 jieba，
  英文/缩写（GMV/ROI）原样保留 token；再叠一层子串加权补 BM25 的「lead ≠ leads」短板。
- 索引按 (datasource, 可见对象集合) 分桶缓存 60s；字典写入后由调用方 ``invalidate()``
  或等 TTL 过期。
"""

from __future__ import annotations

import json
import logging
import re
import time

from backend.common.db import execute_query
from backend.modules.mind.rag.bm25 import BM25

logger = logging.getLogger(__name__)

_TTL_SEC = 60.0
_EN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_CACHE: dict[tuple, tuple] = {}
# 调用方自带行（scoped_metadata 已完成权限/脱敏归一）时按行签名复用索引：
# 604 条 jieba 分词≈140ms，每次工具调用重建没必要；键含名称与对象，改名即自动换索引。
_ROWS_CACHE: dict[int, tuple] = {}
_ROWS_CACHE_MAX = 32

_METRIC_COLS = "name,name_en,aliases"
_DIM_COLS = "name,name_en,aliases,value_labels"


def _as_list(v) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:  # noqa: BLE001 — 脏 JSON 当空处理，不影响其它行
            return []
    return [str(x) for x in (v or []) if str(x or "").strip()]


def _as_dict(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:  # noqa: BLE001
            return {}
    return v if isinstance(v, dict) else {}


def _tokens(text: str) -> list[str]:
    """中英混排分词：英文/缩写保原样（下划线拆开），中文走 jieba。"""
    text = str(text or "")
    out: list[str] = []
    for w in _EN_TOKEN.findall(text):
        out.extend(p.lower() for p in re.split(r"[_\W]+", w) if p)
    try:
        import jieba
        out.extend(w.strip().lower() for w in jieba.lcut(text) if w.strip())
    except ImportError:  # jieba 缺失时退化为中文连续段，engine 会显式标 fallback
        out.extend(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return [t for t in out if len(t) >= 2]


def _hay(row: dict) -> set[str]:
    """匹配面（唯一口径）：名称 / 英文名 / 别名 / 枚举业务名。"""
    items = {str(row.get("name") or ""), str(row.get("name_en") or "")}
    items |= set(_as_list(row.get("aliases")))
    labels = row.get("value_labels") if row.get("value_labels") is not None else row.get("enum")
    items |= {str(v) for v in _as_dict(labels).values()}
    return {x.strip().lower() for x in items if x and str(x).strip()}


def _load_rows(datasource_id: int, allowed_keys: set | None) -> list[dict]:
    """加载可见字典行（datasource_id IN (ds, 0) + 绑定对象过滤，与取数层同口径）。"""
    ds = int(datasource_id or 0)
    out: list[dict] = []
    for table, cols, kind in (("adh_metrics", _METRIC_COLS, "metric"),
                              ("adh_dimensions", _DIM_COLS, "dimension")):
        try:
            rows = execute_query(
                f"SELECT bound_object_key,{cols} FROM {table} "
                "WHERE (datasource_id = %s OR datasource_id = 0) AND is_active = 1",
                (ds,)) or []
        except Exception as e:  # noqa: BLE001 — 读失败必须向上暴露，不静默空目录
            logger.error("[dict_index] 读取 %s 失败: %s", table, e)
            raise
        for r in rows:
            key = str(r.get("bound_object_key") or "").strip()
            if not key:
                continue
            if allowed_keys is not None and key not in allowed_keys:
                continue
            out.append({**r, "object": key, "kind": kind})
    return out


def _build_index(rows: list[dict]) -> tuple[BM25, str]:
    bm25 = BM25()
    try:
        bm25.index([_tokens(" ".join(sorted(_hay(r)))) for r in rows])
        return bm25, "bm25"
    except Exception as e:  # noqa: BLE001 — 显式降级，不静默
        logger.warning("[dict_index] BM25 建索引失败，降级子串匹配: %s", e)
        # 不再尝试 index([])：未索引的 BM25.search 直接返回 []，子串加权仍生效
        return BM25(), "substring_fallback"


def _build(datasource_id: int, allowed_keys: set | None):
    """构建/取缓存索引；返回 (rows, bm25, engine)。"""
    ck = (int(datasource_id or 0),
          None if allowed_keys is None else hash(frozenset(allowed_keys)))
    hit = _CACHE.get(ck)
    now = time.time()
    if hit and now - hit[0] < _TTL_SEC:
        return hit[1], hit[2], hit[3]
    rows = _load_rows(datasource_id, allowed_keys)
    bm25, engine = _build_index(rows)
    _CACHE[ck] = (now, rows, bm25, engine)
    return rows, bm25, engine


def _rows_index(rows: list[dict]) -> tuple[BM25, str]:
    """按行签名缓存索引（行已由调用方过滤，同一批行重复调用不重建）。"""
    sig = hash(tuple((r.get("object"), r.get("kind"), r.get("name"), r.get("name_en"))
                     for r in rows))
    hit = _ROWS_CACHE.get(sig)
    now = time.time()
    if hit and now - hit[0] < _TTL_SEC:
        return hit[1], hit[2]
    bm25, engine = _build_index(rows)
    if len(_ROWS_CACHE) >= _ROWS_CACHE_MAX:
        _ROWS_CACHE.pop(next(iter(_ROWS_CACHE)))
    _ROWS_CACHE[sig] = (now, bm25, engine)
    return bm25, engine


def invalidate(datasource_id: int | None = None) -> None:
    """字典写入后失效索引（None=全清）。"""
    _ROWS_CACHE.clear()
    if datasource_id is None:
        _CACHE.clear()
        return
    ds = int(datasource_id or 0)
    for k in [k for k in _CACHE if k[0] == ds]:
        _CACHE.pop(k, None)


def _match_strength(row: dict, keywords: list[str]) -> tuple[float, list[str]]:
    """子串命中强度：名称精确 > 名称包含 > 别名/英文名/枚举包含。"""
    name = str(row.get("name") or "").strip().lower()
    name_en = str(row.get("name_en") or "").strip().lower()
    alias_bag = [a.lower() for a in _as_list(row.get("aliases"))]
    enum_bag = [v.lower() for v in _as_dict(row.get("value_labels")).values()]
    best, hits = 0.0, []
    for kw in keywords:
        if not kw:
            continue
        if kw == name:
            best = max(best, 3.0)
            hits.append(kw)
        elif kw in name or (name_en and kw in name_en):
            best = max(best, 2.0)
            hits.append(kw)
        elif any(kw in a for a in alias_bag) or any(kw in e for e in enum_bag):
            best = max(best, 1.5)
            hits.append(kw)
    return best, hits


def extract_terms(text: str, limit: int = 12) -> list[str]:
    """从自然语言问题抽取检索词（供 knowledge_search 做字典候选校验）。

    只做分词与去重，不做同义扩展：候选校验靠的是**名称字面命中**，
    扩词会让“看起来命中”变成“猜”，反而掩盖未登记的口径。
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in _tokens(text):
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def rank(rows: list[dict], keywords: list[str], *, top_k: int = 12,
         bm25: BM25 | None = None, engine: str | None = None) -> dict:
    """对**已加载**的字典行排序取 topK（调用方已完成权限/作用域过滤）。

    返回的每个 hit 带 ``idx``：明细请由调用方从 ``rows[idx]`` 取，避免把整行
    冗余复制进检索结果。``objects`` 是每对象的全量计数——发现层必须让调用方
    能区分「命中的这几条」与「该对象一共多少条」（子集不得冒充全量）。
    """
    kws = [str(k or "").strip().lower() for k in (keywords or []) if str(k or "").strip()]
    if bm25 is None or engine is None:
        bm25, engine = _rows_index(rows)

    totals: dict[str, dict] = {}
    for r in rows:
        b = totals.setdefault(str(r.get("object") or ""), {
            "metric_total": 0, "dimension_total": 0})
        b["metric_total" if r.get("kind") == "metric" else "dimension_total"] += 1

    scored: dict[int, float] = {}
    if engine == "bm25" and kws:
        query_tokens: list[str] = []
        for kw in kws:
            query_tokens.extend(_tokens(kw))
        if query_tokens:
            for idx, score in bm25.search(query_tokens, top_k=max(top_k * 4, 40)):
                scored[idx] = float(score)

    hits: list[dict] = []
    for idx, row in enumerate(rows):
        strength, matched = _match_strength(row, kws)
        bm = scored.get(idx, 0.0)
        if strength <= 0 and bm <= 0:
            continue
        hits.append({
            "idx": idx,
            "object": row.get("object") or "",
            "kind": row.get("kind") or "",
            "name": row.get("name") or "",
            "matched_by": matched[:3],
            "name_en": row.get("name_en") or "",
            "aliases": _as_list(row.get("aliases"))[:4],
            "enum_labels": list(_as_dict(
                row.get("value_labels") if row.get("value_labels") is not None
                else row.get("enum")).values())[:4],
            "score": round(strength * 10 + bm, 4),
            "match": "substring" if strength > 0 else "bm25",
        })
    # 精确/子串优先，其次 BM25；同分按 (kind, name) 稳定排序（避免每次返回抖动）
    hits.sort(key=lambda h: (-h["score"], h["kind"], h["name"]))
    top = hits[:max(1, int(top_k))]
    return {
        "hits": top,
        "truncated": len(hits) > len(top),
        "matched_total": len(hits),
        "objects": totals,
        "engine": engine,
        "entries_total": len(rows),
        "keywords": kws,
    }


def search(keywords: list[str], *, datasource_id: int = 0,
           allowed_keys: set | None = None, top_k: int = 12) -> dict:
    """条目级检索（自带加载与缓存）；``allowed_keys=None``=不按对象过滤。"""
    rows, bm25, engine = _build(datasource_id, allowed_keys)
    return rank(rows, keywords, top_k=top_k, bm25=bm25, engine=engine)