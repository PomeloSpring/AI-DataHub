"""
TTL Cache — 统一 Redis 分布式缓存的兼容外壳。

历史：曾为进程内 LRU+TTL（多实例各存各的、重启即丢、跨实例失效不生效）。
现改为基于 services.shared.common.cache (RedisCache) 的**同名同接口**实现：
- 缓存状态跨实例共享、进程重启不丢（符合分布式优先）。
- 保留 get/set/invalidate/invalidate_prefix/get_or_set/stats 接口，调用点无需改动。
- Redis 不可用时透明降级为“无缓存”（get 返回 None→回源、set 记 warning），
  不返回错误值、也不静默切回本地缓存（缓存是性能旁路，缺失不影响正确性）。

注：不再按 maxsize 做 LRU 逐出，改由逐 key TTL 过期回收（键空间有界：按数据源/用户/菜单等维度）。
"""

import logging

from services.shared.common.cache.factory import get_cache

logger = logging.getLogger(__name__)


class TTLCache:
    """Redis 支撑的 TTL 缓存（保持既有 API）。"""

    def __init__(self, name: str = "default", maxsize: int = 256, ttl: int = 300):
        """
        Args:
            name: 缓存名称（作为 Redis 命名空间前缀 + 统计标签）
            maxsize: 兼容旧签名，保留于 stats 展示；不再用于 LRU 逐出（改用 TTL 回收）
            ttl: 默认过期时间（秒）
        """
        self._name = name
        self._maxsize = maxsize
        self._ttl = ttl
        self._c = get_cache(name, default_ttl=ttl)

    def get(self, key: str):
        """获取缓存值，未命中或已过期返回 None（Redis 异常亦返回 None→调用方回源）。"""
        value = self._c.get(key)
        self._c.incr("_stats:hit" if value is not None else "_stats:miss")
        return value

    def set(self, key: str, value, ttl: int = None):
        """设置缓存值。ttl 为 None 时使用默认值。"""
        self._c.set(key, value, ttl=ttl if ttl is not None else self._ttl)

    def invalidate(self, key: str = None):
        """清除缓存。key=None 清除该命名空间全部（含统计计数）。"""
        if key is None:
            self._c.clear()
        else:
            self._c.delete(key)

    def invalidate_prefix(self, prefix: str):
        """清除所有以 prefix 开头的缓存条目。"""
        self._c.clear_pattern(f"{prefix}*")

    def get_or_set(self, key: str, factory, ttl: int = None):
        """获取缓存值，未命中时调用 factory() 生成并缓存（None 不缓存，避免污染）。"""
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        if value is not None:
            self.set(key, value, ttl=ttl)
        return value

    def stats(self) -> dict:
        """返回缓存统计信息（hits/misses 走 Redis 计数器，跨实例累计）。"""
        hits = self._c.get_counter("_stats:hit")
        misses = self._c.get_counter("_stats:miss")
        total = hits + misses
        try:
            size = self._c.size()
        except Exception:  # noqa: BLE001 — 统计不可用不致命
            size = 0
        return {
            "name": self._name,
            "backend": "redis",
            "size": size,
            "maxsize": self._maxsize,
            "ttl": self._ttl,
            "hits": hits,
            "misses": misses,
            "hit_rate": f"{hits / total * 100:.1f}%" if total > 0 else "N/A",
        }


# ── 全局缓存实例（Redis 命名空间 chatbi:<name>）────────────────────────

# 数据源配置：变化很少，缓存 5 分钟
datasource_cache = TTLCache(name="datasource", maxsize=64, ttl=300)

# 菜单树：变化很少，缓存 1 分钟
menu_cache = TTLCache(name="menu", maxsize=16, ttl=60)

# Dashboard 数据：变化较少，缓存 1 分钟
dashboard_cache = TTLCache(name="dashboard", maxsize=128, ttl=60)

# 品牌设置：变化很少，缓存 5 分钟
brand_cache = TTLCache(name="brand", maxsize=8, ttl=300)

# 表元数据：变化很少，缓存 10 分钟
metadata_cache = TTLCache(name="metadata", maxsize=256, ttl=600)
