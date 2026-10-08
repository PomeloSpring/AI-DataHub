"""统一 Redis 缓存后端回归：TTLCache 行为一致性 + 无本地后端（离线，假缓存后端）。"""
import pytest

from backend.common.cache import factory
from backend.common.cache.redis_cache import RedisCache
from backend.common import ttl_cache as tc


class _FakeBackend:
    """内存实现 CacheBackend 接口，用于验证 TTLCache 语义（不触真实 Redis）。"""
    def __init__(self):
        self.store = {}
        self.counters = {}
    def get(self, key): return self.store.get(key)
    def set(self, key, value, ttl=None): self.store[key] = value
    def delete(self, key): self.store.pop(key, None)
    def exists(self, key): return key in self.store
    def clear_pattern(self, pattern):
        pre = pattern[:-1] if pattern.endswith("*") else pattern
        for k in [x for x in list(self.store) if x.startswith(pre)]:
            self.store.pop(k, None)
    def clear(self): self.store.clear(); self.counters.clear()
    def size(self): return len(self.store)
    def incr(self, key, amount=1): self.counters[key] = self.counters.get(key, 0) + amount; return self.counters[key]
    def get_counter(self, key): return self.counters.get(key, 0)


@pytest.fixture
def fake_cache(monkeypatch):
    made = {}
    def _get_cache(prefix, default_ttl=300):
        made.setdefault(prefix, _FakeBackend())
        return made[prefix]
    monkeypatch.setattr(tc, "get_cache", _get_cache)
    return tc.TTLCache(name="probe", maxsize=8, ttl=60), made["probe"]


def test_get_or_set_computes_once_then_hits(fake_cache):
    cache, backend = fake_cache
    calls = {"n": 0}
    def factory_():
        calls["n"] += 1
        return {"v": 42}
    assert cache.get_or_set("k", factory_) == {"v": 42}
    assert cache.get_or_set("k", factory_) == {"v": 42}
    assert calls["n"] == 1  # 第二次命中缓存, 不再回源


def test_none_result_not_cached(fake_cache):
    cache, _ = fake_cache
    calls = {"n": 0}
    def factory_():
        calls["n"] += 1
        return None
    assert cache.get_or_set("k", factory_) is None
    assert cache.get_or_set("k", factory_) is None
    assert calls["n"] == 2  # None 不入缓存, 每次回源


def test_invalidate_and_prefix(fake_cache):
    cache, backend = fake_cache
    cache.set("dash:1:a", {"x": 1})
    cache.set("dash:1:b", {"y": 2})
    cache.set("dash:2:a", {"z": 3})
    cache.invalidate_prefix("dash:1:")
    assert backend.get("dash:1:a") is None and backend.get("dash:2:a") == {"z": 3}
    cache.invalidate("dash:2:a")
    assert backend.get("dash:2:a") is None


def test_stats_reports_redis_and_counters(fake_cache):
    cache, _ = fake_cache
    cache.get("miss1")            # 未命中 → miss
    cache.set("k", {"a": 1})
    cache.get("k")               # 命中 → hit
    st = cache.stats()
    assert st["backend"] == "redis"
    assert st["hits"] == 1 and st["misses"] == 1
    assert st["name"] == "probe"


# ── 后端不再有本地缓存 ────────────────────────────────────────────

def test_factory_returns_redis_only(monkeypatch):
    assert RedisCache is not None
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")  # 未监听, 仅构造不连接
    factory._instances.pop("probe-f", None)
    c = factory.get_cache("probe-f")
    assert isinstance(c, RedisCache)
    assert factory.get_cache("probe-f") is c  # 单例
    assert c._url == "redis://127.0.0.1:6399/0"  # 由 REDIS_URL 注入


def test_local_backend_removed():
    # 本地缓存后端应已从包中移除
    from backend.common import cache
    assert not hasattr(cache, "LocalCache")
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.common.cache.local")


def test_redis_cache_degrades_to_no_cache_when_down(monkeypatch):
    # Redis 不可达时 get 返回 None(回源), 不抛异常、不返回错误值
    c = RedisCache(prefix="chatbi:probe", url="redis://127.0.0.1:6399/0")
    monkeypatch.setattr(c, "_get_redis", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    assert c.get("k") is None
    c.set("k", {"a": 1})  # 吞异常, 不抛
    assert c.get_counter("x") == 0
