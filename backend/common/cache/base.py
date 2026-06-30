"""Cache Backend — abstract base class for cache implementations."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class CacheBackend(ABC):
    """Abstract cache backend interface."""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if not found or expired."""

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int = None) -> None:
        """Set a value in cache with optional TTL (seconds)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a key from cache."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists in cache."""

    @abstractmethod
    def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern. Returns number of keys deleted."""

    @abstractmethod
    def clear(self) -> None:
        """Clear all cached data."""

    @abstractmethod
    def size(self) -> int:
        """Return approximate number of cached items."""

    def get_or_set(self, key: str, factory, ttl: int = None) -> Any:
        """Get from cache, or compute and cache if not found.

        Args:
            key: Cache key
            factory: Callable that returns the value to cache
            ttl: Optional TTL in seconds

        Returns:
            Cached or computed value
        """
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        self.set(key, value, ttl=ttl)
        return value
