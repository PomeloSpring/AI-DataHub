"""Discovery Client — agent-side execution-layer discovery with caching (Phase 3.3).

Wraps the execution-layer registry so agent pipelines can ask "which healthy
execution layers can do X?" without hitting the DB on every request. Results
are cached for 30 seconds.

Usage:
    from services.datamind.execution.discovery_client import get_discovery_client

    client = get_discovery_client()
    layers = client.discover(capabilities=["nl2sql"])
    layer = client.select(preferred_type="cli", capabilities=["mcp"])
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: Discovery cache TTL (seconds).
DISCOVERY_CACHE_TTL = float(os.getenv("EXEC_DISCOVERY_CACHE_TTL", "30"))


class DiscoveryClient:
    """Capability-based execution-layer discovery (cached)."""

    def __init__(self, cache_ttl: float = DISCOVERY_CACHE_TTL):
        self._ttl = cache_ttl
        self._cache: Optional[tuple[float, list[dict]]] = None

    def clear_cache(self):
        self._cache = None

    def _load_healthy_layers(self) -> list[dict]:
        now = time.time()
        if self._cache and (now - self._cache[0]) <= self._ttl:
            return self._cache[1]
        try:
            from services.datamind.execution import service as exec_service

            layers = exec_service.discover_layers()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[Discovery] load failed: %s", e)
            layers = self._cache[1] if self._cache else []
        self._cache = (now, layers)
        return layers

    def discover(self, capabilities: Optional[list[str]] = None) -> list[dict]:
        """Return healthy layers that advertise *all* the requested capabilities.

        Args:
            capabilities: list of capability tags; empty/None → all healthy layers.
        """
        layers = self._load_healthy_layers()
        if not capabilities:
            return layers
        wanted = set(capabilities)
        return [l for l in layers if wanted.issubset(set(l.get("capabilities") or []))]

    def select(
        self,
        preferred_type: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
    ) -> Optional[dict]:
        """Pick a single healthy layer, preferring an exact type match.

        Falls back to any capability-satisfying layer when no layer matches
        *preferred_type*. Returns None when nothing is available.
        """
        candidates = self.discover(capabilities)
        if not candidates:
            return None
        if preferred_type:
            for l in candidates:
                if l.get("layer_type") == preferred_type:
                    return l
        return candidates[0]


_client: Optional[DiscoveryClient] = None


def get_discovery_client() -> DiscoveryClient:
    """Global singleton discovery client."""
    global _client
    if _client is None:
        _client = DiscoveryClient()
    return _client
