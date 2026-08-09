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

# ── Cache ────────────────────────────────────────────────────────────

_CACHE_TTL = 300  # 5 minutes
_cache: dict = {}
_cache_ts: float = 0


def _get_connection():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _load_terms() -> list[dict]:
    """Load all active business terms from database."""
    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, term_cn, term_en, term_aliases, term_type, "
                    "target_table, target_column, description "
                    "FROM adh_business_terms WHERE is_active = 1"
                )
                return cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to load business terms: %s", e)
        return []


def _load_table_keywords() -> dict[str, list[str]]:
    """Load table keywords from adh_table_info.keywords field.

    Returns: {table_name: [keyword1, keyword2, ...]}
    """
    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, keywords FROM adh_table_info "
                    "WHERE is_active = 1 AND keywords IS NOT NULL AND keywords != ''"
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


def _ensure_cache():
    """Refresh cache if TTL expired."""
    global _cache, _cache_ts
    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return

    terms = _load_terms()
    table_keywords = _load_table_keywords()

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

    _cache = {
        "terms": terms,
        "synonym_map": synonym_map,
        "keyword_set": list(keyword_set),
        "table_keywords": table_keywords,
    }
    _cache_ts = now
    logger.info("Terminology cache refreshed: %d terms, %d synonym entries, %d keywords",
                len(terms), len(synonym_map), len(keyword_set))


def get_synonym_map() -> dict[str, list[str]]:
    """Get the full synonym map. Keys are terms, values are lists of synonyms."""
    _ensure_cache()
    return _cache.get("synonym_map", {})


def expand_synonyms(keywords: list[str]) -> list[str]:
    """Expand a list of keywords with their synonyms from the database.

    Replaces the hardcoded _SYNONYM_MAP in table_selector.py.
    """
    synonym_map = get_synonym_map()
    expanded = set(keywords)
    for kw in keywords:
        if kw in synonym_map:
            expanded.update(synonym_map[kw])
    return list(expanded)


def get_business_keywords() -> list[str]:
    """Get all business term keywords for RAG filtering.

    Replaces the hardcoded keyword list in intent_classifier.py extract_keywords.
    """
    _ensure_cache()
    return _cache.get("keyword_set", [])


def get_all_terms() -> list[dict]:
    """Get all active business terms."""
    _ensure_cache()
    return _cache.get("terms", [])


def get_term_for_table(table_name: str) -> list[dict]:
    """Get business terms associated with a specific table."""
    terms = get_all_terms()
    return [t for t in terms if t.get("target_table") == table_name]


def clear_cache():
    """Force cache refresh on next access."""
    global _cache, _cache_ts
    _cache = {}
    _cache_ts = 0
