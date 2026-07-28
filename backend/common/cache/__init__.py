"""Cache Layer — pluggable caching with local memory and Redis backends.

Usage:
    from backend.common.cache import get_cache

    cache = get_cache("rag")  # Uses configured backend
    cache.set("key", value, ttl=300)
    result = cache.get("key")

Configuration (in .env or system config):
    CACHE_BACKEND=local  # or "redis"
    REDIS_HOST=localhost
    REDIS_PORT=6379
    REDIS_DB=0
"""

from backend.common.cache.base import CacheBackend
from backend.common.cache.local import LocalCache
from backend.common.cache.redis_cache import RedisCache
from backend.common.cache.factory import get_cache, clear_all_caches, get_cache_stats

__all__ = [
    "CacheBackend",
    "LocalCache",
    "RedisCache",
    "get_cache",
    "clear_all_caches",
    "get_cache_stats",
]
