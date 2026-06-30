"""
TTL Cache — 通用带过期时间的 LRU 缓存。

用于缓存数据源配置、菜单树、Dashboard 数据等变化不频繁的数据。
支持按 key 过期、全局清除、缓存统计。
"""

import time
import threading
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    """带过期时间的线程安全 LRU 缓存。"""

    def __init__(self, name: str = "default", maxsize: int = 256, ttl: int = 300):
        """
        Args:
            name: 缓存名称，用于日志和统计
            maxsize: 最大缓存条目数
            ttl: 默认过期时间（秒）
        """
        self._name = name
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，返回 None 表示未命中或已过期。"""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            value, expire_at = self._cache[key]
            if time.time() >= expire_at:
                # 过期删除
                del self._cache[key]
                self._misses += 1
                return None

            # 命中，移到末尾
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int = None):
        """设置缓存值。ttl 为 None 时使用默认值。"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)

            expire_at = time.time() + (ttl if ttl is not None else self._ttl)
            self._cache[key] = (value, expire_at)

    def invalidate(self, key: str = None):
        """清除缓存。key=None 清除全部。"""
        with self._lock:
            if key is None:
                self._cache.clear()
            elif key in self._cache:
                del self._cache[key]

    def get_or_set(self, key: str, factory, ttl: int = None) -> Any:
        """获取缓存值，未命中时调用 factory() 生成并缓存。"""
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        self.set(key, value, ttl=ttl)
        return value

    def stats(self) -> dict:
        """返回缓存统计信息。"""
        with self._lock:
            total = self._hits + self._misses
            return {
                "name": self._name,
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "ttl": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{self._hits / total * 100:.1f}%" if total > 0 else "N/A",
            }


# ── 全局缓存实例 ────────────────────────────────────────────────────────

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
