"""Cache Factory — creates cache instances based on configuration.

Supports two backends:
- local: In-memory cache (default, no external dependencies)
- redis: Redis distributed cache (requires redis server)

Configuration (in .env):
    CACHE_BACKEND=local  # or "redis"
    REDIS_HOST=localhost
    REDIS_PORT=6379
    REDIS_DB=0
    REDIS_PASSWORD=  # optional
"""

import os
import logging
from typing import Dict

from backend.common.cache.base import CacheBackend
from backend.common.cache.local import LocalCache
from backend.common.cache.redis_cache import RedisCache

logger = logging.getLogger(__name__)

# Cache instances (singleton per prefix)
_instances: Dict[str, CacheBackend] = {}

# Default configuration
_DEFAULT_CONFIG = {
    "local": {
        "max_size": 1000,
    },
    "redis": {
        "host": "localhost",
        "port": 6379,
        "db": 0,
        "password": None,
    },
}


def _get_backend_type() -> str:
    """Get configured cache backend type."""
    return os.getenv("CACHE_BACKEND", "local").lower()


def _get_config() -> dict:
    """Get cache configuration from environment.

    Supports REDIS_URL (e.g. redis://:password@host:port/db) or individual vars.
    """
    backend = _get_backend_type()

    if backend == "redis":
        # Try REDIS_URL first (matches config.py REDIS_URL)
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            try:
                import urllib.parse
                parsed = urllib.parse.urlparse(redis_url)
                return {
                    "host": parsed.hostname or _DEFAULT_CONFIG["redis"]["host"],
                    "port": parsed.port or _DEFAULT_CONFIG["redis"]["port"],
                    "db": int(parsed.path.lstrip("/") or _DEFAULT_CONFIG["redis"]["db"]),
                    "password": parsed.password or _DEFAULT_CONFIG["redis"]["password"],
                }
            except Exception:
                pass

        # Fallback to individual vars
        return {
            "host": os.getenv("REDIS_HOST", _DEFAULT_CONFIG["redis"]["host"]),
            "port": int(os.getenv("REDIS_PORT", _DEFAULT_CONFIG["redis"]["port"])),
            "db": int(os.getenv("REDIS_DB", _DEFAULT_CONFIG["redis"]["db"])),
            "password": os.getenv("REDIS_PASSWORD", _DEFAULT_CONFIG["redis"]["password"]),
        }
    else:
        return {
            "max_size": int(os.getenv("CACHE_MAX_SIZE", _DEFAULT_CONFIG["local"]["max_size"])),
        }


def get_cache(prefix: str, default_ttl: int = 300) -> CacheBackend:
    """Get or create a cache instance with the given prefix.

    Args:
        prefix: Cache prefix for namespacing (e.g., "rag", "tables", "graph")
        default_ttl: Default TTL in seconds (default: 5 minutes)

    Returns:
        CacheBackend instance (LocalCache or RedisCache)
    """
    if prefix in _instances:
        return _instances[prefix]

    backend_type = _get_backend_type()
    config = _get_config()

    if backend_type == "redis":
        try:
            cache = RedisCache(
                prefix=f"chatbi:{prefix}",
                default_ttl=default_ttl,
                **config,
            )
            # Test connection
            cache.size()
            logger.info("Created Redis cache: prefix=%s, ttl=%s", prefix, default_ttl)
        except Exception as e:
            logger.warning("Redis unavailable (%s), falling back to local cache", e)
            cache = LocalCache(
                prefix=f"chatbi:{prefix}",
                default_ttl=default_ttl,
                max_size=config.get("max_size", 1000),
            )
            backend_type = "local"
    else:
        cache = LocalCache(
            prefix=f"chatbi:{prefix}",
            default_ttl=default_ttl,
            max_size=config.get("max_size", 1000),
        )
        logger.info("Created local cache: prefix=%s, ttl=%s, max_size=%s",
                     prefix, default_ttl, config.get("max_size", 1000))

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
