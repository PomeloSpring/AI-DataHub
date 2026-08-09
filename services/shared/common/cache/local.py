"""Local Cache — in-memory cache with TTL and LRU eviction.

Thread-safe implementation using a dictionary with time-based expiration.
Suitable for single-process deployments or development environments.
"""

import json
import time
import threading
import logging
from typing import Any, Optional
from collections import OrderedDict

from services.shared.common.cache.base import CacheBackend

logger = logging.getLogger(__name__)


class LocalCache(CacheBackend):
    """In-memory cache with TTL and LRU eviction.

    Args:
        prefix: Key prefix for namespacing
        max_size: Maximum number of items (0 = unlimited)
        default_ttl: Default TTL in seconds (0 = no expiration)
    """

    def __init__(self, prefix: str = "", max_size: int = 1000, default_ttl: int = 0):
        self.prefix = prefix
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.RLock()

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self.prefix}:{key}" if self.prefix else key

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if not found or expired."""
        full_key = self._make_key(key)

        with self._lock:
            if full_key not in self._cache:
                return None

            value, expires_at = self._cache[full_key]

            # Check expiration
            if expires_at > 0 and time.time() > expires_at:
                del self._cache[full_key]
                return None

            # Move to end (LRU)
            self._cache.move_to_end(full_key)
            return value

    def set(self, key: str, value: Any, ttl: int = None) -> None:
        """Set a value in cache with optional TTL (seconds)."""
        full_key = self._make_key(key)
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + ttl if ttl > 0 else 0

        with self._lock:
            # Evict if at capacity
            if self.max_size > 0 and len(self._cache) >= self.max_size:
                if full_key not in self._cache:
                    # Remove oldest item
                    self._cache.popitem(last=False)

            self._cache[full_key] = (value, expires_at)
            self._cache.move_to_end(full_key)

    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        full_key = self._make_key(key)
        with self._lock:
            self._cache.pop(full_key, None)

    def exists(self, key: str) -> bool:
        """Check if a key exists in cache."""
        return self.get(key) is not None

    def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern. Returns number of keys deleted."""
        import fnmatch
        full_pattern = self._make_key(pattern)
        count = 0

        with self._lock:
            keys_to_delete = [
                k for k in self._cache.keys()
                if fnmatch.fnmatch(k, full_pattern)
            ]
            for k in keys_to_delete:
                del self._cache[k]
                count += 1

        return count

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Return approximate number of cached items."""
        return len(self._cache)

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns number of items removed."""
        now = time.time()
        count = 0

        with self._lock:
            keys_to_delete = [
                k for k, (_, expires_at) in self._cache.items()
                if expires_at > 0 and now > expires_at
            ]
            for k in keys_to_delete:
                del self._cache[k]
                count += 1

        return count
