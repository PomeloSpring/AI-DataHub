"""Terminology Manager — dynamic loading of business terms and synonyms from DB.

Replaces hardcoded _SYNONYM_MAP and keyword lists in table_selector and intent_classifier.
Loads from adh_business_terms table with TTL caching.
"""

import logging
import time
from typing import Optional

import pymysql

from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

# ── Cache (per-datasource buckets) ─────────────────────────────────────
# Keyed by datasource_id: 术语/同义词不允许跨数据源串味。
# datasource_id=0 视为全局/系统调用 → 加载全量(保持既有行为);
# datasource_id>0 → 只加载本数据源 + 全局(datasource_id=0)术语。

_CACHE_TTL = 300  # 5 minutes
_cache: dict[int, dict] = {}
_cache_ts: dict[int, float] = {}


def _get_connection():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _ds_scope(datasource_id: int) -> tuple[str, list]:
    """Return (sql_condition, params) shared by terms/keywords loading."""
    if datasource_id:
        # 占位符与参数必须同数：字面量 0 不占位，传两个参数会让 pymysql 格式化报
        # "not all arguments converted"，两加载函数全部静默回退空缓存（术语/关键词整体失效）。
        return "(datasource_id = %s OR datasource_id = 0)", [datasource_id]
    return "", []


def _load_terms(datasource_id: int = 0) -> list[dict]:
    """Load active business terms from database, scoped to the datasource."""
    cond, params = _ds_scope(datasource_id)
    where = "WHERE is_active = 1" + (f" AND {cond}" if cond else "")
    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, datasource_id, term_cn, term_en, term_aliases, term_type, "
                    "target_table, target_column, description "
                    f"FROM adh_business_terms {where}",
                    params,
                )
                return cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to load business terms: %s", e)
        return []


def _load_table_keywords(datasource_id: int = 0) -> dict[str, list[str]]:
    """Load table keywords from adh_table_info.keywords field.

    Returns: {table_name: [keyword1, keyword2, ...]}
    """
    cond, params = _ds_scope(datasource_id)
    where = "WHERE is_active = 1 AND keywords IS NOT NULL AND keywords != ''" \
        + (f" AND {cond}" if cond else "")
    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT table_name, keywords FROM adh_table_info {where}",
                    params,
                )
                result = {}
                for row in cur.fetchall():
                    kws = [k.strip() for k in row["keywords"].split(",") if k.strip()]
                    if kws:
                        result[row["table_name"]] = kws
                return result
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to load table keywords: %s", e)
        return {}


def _ensure_cache(datasource_id: int = 0):
    """Refresh the per-datasource cache bucket if TTL expired."""
    now = time.time()
    bucket = _cache.get(datasource_id)
    if bucket and (now - _cache_ts.get(datasource_id, 0)) < _CACHE_TTL:
        return

    terms = _load_terms(datasource_id)
    table_keywords = _load_table_keywords(datasource_id)

    # Build synonym map: term → [synonym1, synonym2, ...]
    # Sources: term_cn, term_en, term_aliases (comma-separated)
    synonym_map: dict[str, list[str]] = {}
    keyword_set: set[str] = set()

    for t in terms:
        term_cn = t.get("term_cn", "").strip()
        term_en = t.get("term_en", "").strip()
        aliases_raw = t.get("term_aliases", "") or ""

        if not term_cn:
            continue

        # Collect all synonyms for this term
        synonyms = set()
        if term_cn:
            synonyms.add(term_cn)
        if term_en:
            synonyms.add(term_en)
        for alias in aliases_raw.split(","):
            alias = alias.strip()
            if alias:
                synonyms.add(alias)

        synonyms_list = list(synonyms)

        # Map each synonym to the full synonym set
        for s in synonyms_list:
            synonym_map[s] = synonyms_list

        # Collect keywords for RAG filtering
        keyword_set.add(term_cn)
        if term_en:
            keyword_set.add(term_en)

    # Add table keywords to synonym map
    for table_name, kws in table_keywords.items():
        for kw in kws:
            if kw not in synonym_map:
                synonym_map[kw] = [kw]
            keyword_set.add(kw)

    _cache[datasource_id] = {
        "terms": terms,
        "synonym_map": synonym_map,
        "keyword_set": list(keyword_set),
        "table_keywords": table_keywords,
    }
    _cache_ts[datasource_id] = now
    logger.info("Terminology cache refreshed (ds=%s): %d terms, %d synonym entries, %d keywords",
                datasource_id, len(terms), len(synonym_map), len(keyword_set))


def get_synonym_map(datasource_id: int = 0) -> dict[str, list[str]]:
    """Get the synonym map for a datasource. Keys are terms, values are lists of synonyms."""
    _ensure_cache(datasource_id)
    return _cache[datasource_id].get("synonym_map", {})


def expand_synonyms(keywords: list[str], datasource_id: int = 0) -> list[str]:
    """Expand a list of keywords with their synonyms from the database.

    Replaces the hardcoded _SYNONYM_MAP in table_selector.py.
    Synonyms are resolved within the given datasource (+ global terms).
    """
    synonym_map = get_synonym_map(datasource_id)
    expanded = set(keywords)
    for kw in keywords:
        if kw in synonym_map:
            expanded.update(synonym_map[kw])
    return list(expanded)


def get_business_keywords(datasource_id: int = 0) -> list[str]:
    """Get business term keywords for RAG filtering (datasource-scoped).

    Replaces the hardcoded keyword list in intent_classifier.py extract_keywords.
    """
    _ensure_cache(datasource_id)
    return _cache[datasource_id].get("keyword_set", [])


def get_all_terms(datasource_id: int = 0) -> list[dict]:
    """Get active business terms for a datasource (0 = all)."""
    _ensure_cache(datasource_id)
    return _cache[datasource_id].get("terms", [])


def get_term_for_table(table_name: str, datasource_id: int = 0) -> list[dict]:
    """Get business terms associated with a specific table."""
    terms = get_all_terms(datasource_id)
    return [t for t in terms if t.get("target_table") == table_name]


def clear_cache(datasource_id: int = None):
    """Force cache refresh on next access (single bucket or all)."""
    if datasource_id is None:
        _cache.clear()
        _cache_ts.clear()
    else:
        _cache.pop(datasource_id, None)
        _cache_ts.pop(datasource_id, None)
