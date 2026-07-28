"""Redis Cache — distributed cache using Redis.

Requires redis-py package: pip install redis

Suitable for multi-process or distributed deployments.
"""

import json
import logging
from typing import Any, Optional

from backend.common.cache.base import CacheBackend

logger = logging.getLogger(__name__)


class RedisCache(CacheBackend):
    """Redis-based distributed cache.

    Args:
        prefix: Key prefix for namespacing
        default_ttl: Default TTL in seconds (0 = no expiration)
        host: Redis host
        port: Redis port
        db: Redis database number
        password: Redis password (optional)
    """

    def __init__(
        self,
        prefix: str = "",
        default_ttl: int = 0,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str = None,
    ):
        self.prefix = prefix
        self.default_ttl = default_ttl
        self._redis = None
        self._host = host
        self._port = port
        self._db = db
        self._password = password

    def _get_redis(self):
        """Lazy initialization of Redis connection."""
        if self._redis is None:
            try:
                import redis
                self._redis = redis.Redis(
                    host=self._host,
                    port=self._port,
                    db=self._db,
                    password=self._password,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                )
                # Test connection
                self._redis.ping()
                logger.info("Redis cache connected: %s:%s/%s", self._host, self._port, self._db)
            except ImportError:
                raise ImportError("redis package not installed. Run: pip install redis")
            except Exception as e:
                logger.error("Failed to connect to Redis: %s", e)
                raise
        return self._redis

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self.prefix}:{key}" if self.prefix else key

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if not found or expired."""
        full_key = self._make_key(key)
        try:
            data = self._get_redis().get(full_key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning("Redis get failed for %s: %s", key, e)
            return None

    def set(self, key: str, value: Any, ttl: int = None) -> None:
        """Set a value in cache with optional TTL (seconds)."""
        full_key = self._make_key(key)
        ttl = ttl if ttl is not None else self.default_ttl
        try:
            data = json.dumps(value, ensure_ascii=False, default=str)
            if ttl > 0:
                self._get_redis().setex(full_key, ttl, data)
            else:
                self._get_redis().set(full_key, data)
        except Exception as e:
            logger.warning("Redis set failed for %s: %s", key, e)

    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        full_key = self._make_key(key)
        try:
            self._get_redis().delete(full_key)
        except Exception as e:
            logger.warning("Redis delete failed for %s: %s", key, e)

    def exists(self, key: str) -> bool:
        """Check if a key exists in cache."""
        full_key = self._make_key(key)
        try:
            return bool(self._get_redis().exists(full_key))
        except Exception as e:
            logger.warning("Redis exists failed for %s: %s", key, e)
            return False

    def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern. Returns number of keys deleted."""
        full_pattern = self._make_key(pattern)
        count = 0
        try:
            # Use SCAN to find matching keys (more efficient than KEYS)
            cursor = 0
            while True:
                cursor, keys = self._get_redis().scan(
                    cursor=cursor, match=full_pattern, count=100
                )
                if keys:
                    count += self._get_redis().delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("Redis clear_pattern failed for %s: %s", pattern, e)
        return count

    def clear(self) -> None:
        """Clear all cached data (only keys with our prefix)."""
        if self.prefix:
            self.clear_pattern("*")
        else:
            try:
                self._get_redis().flushdb()
            except Exception as e:
                logger.warning("Redis clear failed: %s", e)

    def size(self) -> int:
        """Return approximate number of cached items."""
        try:
            if self.prefix:
                # Count keys with our prefix
                count = 0
                cursor = 0
                while True:
                    cursor, keys = self._get_redis().scan(
                        cursor=cursor, match=f"{self.prefix}:*", count=100
                    )
                    count += len(keys)
                    if cursor == 0:
                        break
                return count
            else:
                return self._get_redis().dbsize()
        except Exception as e:
            logger.warning("Redis size failed: %s", e)
            return 0
