"""Cache Factory — 统一 Redis 分布式缓存。

不再提供进程内本地缓存后端：缓存状态必须跨实例共享(分布式优先)。
Redis 不可用时，RedisCache 的 get/set 会透明降级为“无缓存”(回源计算),
不会返回错误值、也不会静默切回本地缓存。

Configuration (in .env / 服务环境):
    REDIS_URL=redis://localhost:6379/0    # 优先
    REDIS_HOST/REDIS_PORT/REDIS_DB/REDIS_PASSWORD  # 无 REDIS_URL 时回退
"""

import os
import logging
from typing import Dict

from backend.common.cache.base import CacheBackend
from backend.common.cache.redis_cache import RedisCache

logger = logging.getLogger(__name__)

# Cache instances (singleton per prefix)
_instances: Dict[str, CacheBackend] = {}


def _redis_url() -> str:
    url = os.getenv("REDIS_URL")
    if url:
        return url
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    db = os.getenv("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


def get_cache(prefix: str, default_ttl: int = 300) -> CacheBackend:
    """Get or create a Redis-backed cache instance with the given prefix.

    Args:
        prefix: Cache prefix for namespacing (e.g., "rag", "datasource")
        default_ttl: Default TTL in seconds (default: 5 minutes)

    Returns:
        RedisCache 实例(命名前缀 chatbi:<prefix>); Redis 不可用时操作降级为无缓存。
    """
    if prefix in _instances:
        return _instances[prefix]

    cache = RedisCache(
        prefix=f"chatbi:{prefix}",
        default_ttl=default_ttl,
        url=_redis_url(),
        password=os.getenv("REDIS_PASSWORD") or None,
    )
    logger.info("Created Redis cache: prefix=%s, ttl=%s", prefix, default_ttl)
    _instances[prefix] = cache
    return cache


def clear_all_caches() -> None:
    """Clear all cache instances."""
    for prefix, cache in _instances.items():
        try:
            cache.clear()
            logger.info("Cleared cache: %s", prefix)
        except Exception as e:
            logger.warning("Failed to clear cache %s: %s", prefix, e)


def get_cache_stats() -> dict:
    """Get statistics for all cache instances."""
    stats = {}
    for prefix, cache in _instances.items():
        stats[prefix] = {
            "backend": type(cache).__name__,
            "size": cache.size(),
        }
    return stats
