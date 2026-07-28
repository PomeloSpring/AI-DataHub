"""Metadata Cache — Redis-backed caching for RAG metadata.

Replaces in-memory dict caches with distributed Redis cache.
Falls back to local memory if Redis is unavailable.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache instance (lazy init) ──────────────────────────────────────

_cache = None
_local_fallback: dict = {}


def _get_cache():
    """Get or create the Redis cache instance."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        from backend.common.cache.factory import get_cache
        _cache = get_cache(prefix="rag_meta", default_ttl=300)  # 5 min TTL
        logger.info("RAG metadata cache initialized")
    except Exception as e:
        logger.warning("Redis cache unavailable, using local fallback: %s", e)
        _cache = None
    return _cache


def _serialize(data):
    """Serialize data for Redis storage."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _deserialize(data):
    """Deserialize data from Redis storage."""
    if data is None:
        return None
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


# ── Public API ──────────────────────────────────────────────────────

def get_cached_columns(datasource_id: int) -> Optional[list]:
    """Get cached column metadata for a datasource."""
    key = f"columns:ds{datasource_id}"
    cache = _get_cache()

    if cache:
        try:
            data = cache.get(key)
            if data is not None:
                return _deserialize(data)
        except Exception as e:
            logger.debug("Redis get failed for %s: %s", key, e)

    # Local fallback
    return _local_fallback.get(key)


def set_cached_columns(datasource_id: int, columns: list, ttl: int = 300):
    """Cache column metadata for a datasource."""
    key = f"columns:ds{datasource_id}"
    cache = _get_cache()

    if cache:
        try:
            cache.set(key, _serialize(columns), ttl=ttl)
        except Exception as e:
            logger.debug("Redis set failed for %s: %s", key, e)

    # Always update local fallback
    _local_fallback[key] = columns


def get_cached_tables(datasource_id: int) -> Optional[list]:
    """Get cached table metadata for a datasource."""
    key = f"tables:ds{datasource_id}"
    cache = _get_cache()

    if cache:
        try:
            data = cache.get(key)
            if data is not None:
                return _deserialize(data)
        except Exception as e:
            logger.debug("Redis get failed for %s: %s", key, e)

    return _local_fallback.get(key)


def set_cached_tables(datasource_id: int, tables: list, ttl: int = 300):
    """Cache table metadata for a datasource."""
    key = f"tables:ds{datasource_id}"
    cache = _get_cache()

    if cache:
        try:
            cache.set(key, _serialize(tables), ttl=ttl)
        except Exception as e:
            logger.debug("Redis set failed for %s: %s", key, e)

    _local_fallback[key] = tables


def get_cached_rag_result(cache_key: str) -> Optional[dict]:
    """Get cached RAG retrieval result."""
    key = f"rag:{cache_key}"
    cache = _get_cache()

    if cache:
        try:
            data = cache.get(key)
            if data is not None:
                return _deserialize(data)
        except Exception:
            pass

    return _local_fallback.get(key)


def set_cached_rag_result(cache_key: str, result: dict, ttl: int = 120):
    """Cache RAG retrieval result (shorter TTL since it's query-specific)."""
    key = f"rag:{cache_key}"
    cache = _get_cache()

    if cache:
        try:
            cache.set(key, _serialize(result), ttl=ttl)
        except Exception:
            pass

    _local_fallback[key] = result


def invalidate_datasource(datasource_id: int):
    """Invalidate all cached data for a datasource (call after metadata sync)."""
    cache = _get_cache()

    for prefix in ["columns", "tables"]:
        key = f"{prefix}:ds{datasource_id}"
        if cache:
            try:
                cache.delete(key)
            except Exception:
                pass
        _local_fallback.pop(key, None)

    logger.info("Invalidated metadata cache for datasource %d", datasource_id)
