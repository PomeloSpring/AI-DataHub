"""Table Selector — BM25 sparse + vector dense hybrid table selection.

Uses jieba Chinese text segmentation to extract keywords from user questions,
then retrieves via BM25 (sparse) and vector HNSW (dense), merging with RRF.
Synonyms are loaded dynamically from adh_business_terms via terminology_manager.
"""

import logging
import re
from typing import Optional

from services.shared.common.db.metadata_db import get_vector_conn
from services.datamind.rag.terminology_manager import expand_synonyms
from services.datamind.rag.bm25 import BM25, rrf_merge

logger = logging.getLogger(__name__)

# ── Stop words (common words to ignore) ──────────────────────────────

_STOP_WORDS = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那",
    "吗", "吧", "啊", "呢", "嗯", "哦", "哈", "呀", "么",
    "怎么", "什么", "如何", "多少", "几", "哪些", "哪个", "为什么",
    "请", "帮", "帮忙", "查", "查询", "看看", "显示", "展示", "统计",
    "分析", "一下", "给我", "告诉", "看看", "想要", "需要",
    "最近", "今天", "昨天", "本周", "本月", "今年", "去年",
    "各", "每个", "分别", "按", "以及", "还有", "和", "与",
    "数据", "信息", "情况", "报告", "报表",
})

# ── Cached table metadata ────────────────────────────────────────────

_tables_cache: dict[int, list[dict]] = {}
_bm25_cache: dict[int, BM25] = {}  # BM25 index per datasource


def _tokenize_text(text: str) -> list[str]:
    """Tokenize a text string using jieba, filtering stop words and short tokens."""
    try:
        import jieba
        words = jieba.lcut(text)
    except ImportError:
        words = re.findall(r'[一-鿿]+|[a-zA-Z_]\w*', text)

    tokens = []
    for w in words:
        w = w.strip()
        if not w or len(w) < 2:
            continue
        if w.lower() in _STOP_WORDS:
            continue
        tokens.append(w.lower())
    return tokens


def _build_bm25_index(datasource_id: int) -> BM25:
    """Build BM25 index from cached table metadata."""
    if datasource_id in _bm25_cache:
        return _bm25_cache[datasource_id]

    all_tables = _get_all_tables(datasource_id)
    if not all_tables:
        bm25 = BM25()
        bm25.index([])
        _bm25_cache[datasource_id] = bm25
        return bm25

    # Build document per table: tokenize table_name + comment + business_desc + tags
    documents = []
    for table in all_tables:
        text_parts = [
            table.get("table_name", ""),
            table.get("table_comment", ""),
            table.get("table_business_desc", ""),
            table.get("region_tag", ""),
            table.get("domain_tag", ""),
        ]
        doc_text = " ".join(p for p in text_parts if p)
        documents.append(_tokenize_text(doc_text))

    bm25 = BM25()
    bm25.index(documents)
    _bm25_cache[datasource_id] = bm25
    logger.info("Built BM25 index for %d tables (ds=%d)", len(all_tables), datasource_id)
    return bm25


def _get_all_tables(datasource_id: int = 0) -> list[dict]:
    """Get all active tables from adh_table_info (cached per datasource)."""
    if datasource_id in _tables_cache:
        return _tables_cache[datasource_id]

    try:
        conn = get_vector_conn()
        try:
            with conn.cursor() as cur:
                # Include both datasource-specific and global (ds=0) rows
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT table_name, table_comment, table_business_desc, "
                    f"region_tag, domain_tag "
                    f"FROM adh_table_info WHERE is_active = 1 {ds_filter}"
                )
                _tables_cache[datasource_id] = cur.fetchall()
                logger.info("Loaded %d active tables from adh_table_info (ds=%d)", len(_tables_cache[datasource_id]), datasource_id)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to load tables from adh_table_info: %s", e)
        _tables_cache[datasource_id] = []

    return _tables_cache[datasource_id]


def clear_cache(datasource_id: int = None):
    """Clear the cached table metadata and BM25 index (call after metadata changes)."""
    global _tables_cache, _bm25_cache
    if datasource_id is not None:
        _tables_cache.pop(datasource_id, None)
        _bm25_cache.pop(datasource_id, None)
    else:
        _tables_cache.clear()
        _bm25_cache.clear()


def _extract_keywords(question: str) -> list[str]:
    """Extract meaningful keywords from a Chinese question using jieba + heuristics."""
    try:
        import jieba
        words = jieba.lcut(question)
    except ImportError:
        # Fallback: simple character-level splitting for Chinese
        logger.warning("jieba not installed, using regex fallback for keyword extraction")
        words = re.findall(r'[一-鿿]+|[a-zA-Z_]\w*', question)

    keywords = []
    for w in words:
        w = w.strip()
        if not w or len(w) < 2:
            continue
        if w.lower() in _STOP_WORDS:
            continue
        keywords.append(w)

    return keywords


def _expand_synonyms(keywords: list[str]) -> list[str]:
    """Expand keywords with synonyms from database (via terminology_manager)."""
    return expand_synonyms(keywords)


def _bm25_search_tables(keywords: list[str], top_k: int, datasource_id: int) -> list[str]:
    """BM25 sparse retrieval for table names."""
    bm25 = _build_bm25_index(datasource_id)
    if bm25.is_empty:
        return []

    # Tokenize and expand query keywords for BM25
    query_tokens = []
    for kw in keywords:
        query_tokens.append(kw.lower())
        # Also tokenize each keyword in case it's a multi-char term
        query_tokens.extend(_tokenize_text(kw))
    # Deduplicate
    query_tokens = list(set(query_tokens))

    results = bm25.search(query_tokens, top_k=top_k)
    if not results:
        return []

    all_tables = _get_all_tables(datasource_id)
    return [all_tables[idx]["table_name"] for idx, _ in results]


def select_tables(
    question: str,
    top_k: int = 5,
    vector_literal: str = None,
    datasource_id: int = 0,
) -> list[str]:
    """Select relevant tables using BM25 sparse + vector dense hybrid retrieval.

    BM25 provides keyword-aware ranking (sparse), vector search provides semantic
    ranking (dense). Results are merged via Reciprocal Rank Fusion (RRF).

    Args:
        question: The user's question.
        top_k: Maximum number of tables to return.
        vector_literal: Pre-computed embedding vector for vector search.
                        If None, generates one automatically.
        datasource_id: Filter tables by this datasource.

    Returns:
        List of selected table names (up to top_k).
    """
    from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

    all_tables = _get_all_tables(datasource_id)
    if not all_tables:
        logger.warning("No tables available for selection")
        return []

    # Step 1: Extract and expand keywords
    keywords = _extract_keywords(question)
    expanded = _expand_synonyms(keywords)
    logger.debug("Table selector: question=%s, keywords=%s, expanded=%s", question[:50], keywords, expanded)

    # Step 2: BM25 sparse retrieval
    bm25_tables = _bm25_search_tables(expanded, top_k * 2, datasource_id)

    # Step 3: Vector dense retrieval
    if not vector_literal:
        try:
            vec_literal = embedding_to_sql_literal(generate_embedding(question))
        except Exception:
            vec_literal = None
    else:
        vec_literal = vector_literal

    vector_tables = []
    if vec_literal:
        vector_tables = _vector_search_tables(vec_literal, top_k * 2, datasource_id)

    # Step 4: RRF fusion of sparse + dense rankings
    rankings = []
    weights = []
    if bm25_tables:
        rankings.append(bm25_tables)
        weights.append(1.0)  # sparse weight
    if vector_tables:
        rankings.append(vector_tables)
        weights.append(1.0)  # dense weight

    if not rankings:
        logger.info("Table selector: no results from BM25 or vector search")
        return []

    if len(rankings) == 1:
        merged = rankings[0][:top_k]
    else:
        rrf_results = rrf_merge(rankings, k=60, weights=weights)
        merged = [name for name, _ in rrf_results[:top_k]]

    logger.info("Table selector: bm25=%s, vector=%s, rrf_merged=%s",
                bm25_tables[:5], vector_tables[:5], merged)
    return merged


def _vector_search_tables(vec_literal: str, limit: int = 5, datasource_id: int = 0) -> list[str]:
    """Fallback: use vector search to find relevant tables."""
    try:
        ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
        sql = f"""
            SELECT table_name
            FROM adh_table_info
            WHERE is_active = 1 {ds_filter}
            ORDER BY l2_distance_approximate(embedding, {vec_literal}) ASC
            LIMIT {limit}
        """
        conn = get_vector_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                selected = [r["table_name"] for r in rows]
                logger.info("Table selector (vector fallback): selected %d tables: %s", len(selected), selected)
                return selected
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Vector search fallback failed: %s", e)
        return []
