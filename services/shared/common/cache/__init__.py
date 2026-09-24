"""Cache Layer — 统一 Redis 分布式缓存（已移除本地内存后端）。

Usage:
    from services.shared.common.cache import get_cache

    cache = get_cache("datasource")
    cache.set("key", value, ttl=300)
    result = cache.get("key")

Configuration:
    REDIS_URL=redis://localhost:6379/0   # 优先
    REDIS_HOST/REDIS_PORT/REDIS_DB/REDIS_PASSWORD  # 无 REDIS_URL 时回退
"""

from services.shared.common.cache.base import CacheBackend
from services.shared.common.cache.redis_cache import RedisCache
from services.shared.common.cache.factory import get_cache, clear_all_caches, get_cache_stats

__all__ = [
    "CacheBackend",
    "RedisCache",
    "get_cache",
    "clear_all_caches",
    "get_cache_stats",
]
