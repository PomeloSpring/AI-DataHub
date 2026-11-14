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
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
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


# ── 凭据装配（防 NOAUTH 把缓存静默降级成"无缓存"）──────────────────────
# 背景：现网 REDIS_URL=redis://host:port/0 不带 userinfo，密码只在 REDIS_PASSWORD，
# 所有 from_url(REDIS_URL) 通道（缓存/Celery broker/图谱锁/监控探测）被服务端拒
# Authentication required，缓存侧只留一行 WARNING，定时任务整体停摆。

_URL = "redis://127.0.0.1:6399/0"


def _clean_redis_env(monkeypatch):
    for k in ("REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def test_password_component_is_injected_into_url(monkeypatch):
    from backend.common.redis_url import effective_redis_url

    _clean_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", _URL)
    monkeypatch.setenv("REDIS_PASSWORD", "SG2LVQOQoTjzkOrd")
    assert effective_redis_url() == "redis://:SG2LVQOQoTjzkOrd@127.0.0.1:6399/0"


def test_special_chars_in_password_are_url_encoded(monkeypatch):
    from backend.common.redis_url import effective_redis_url

    _clean_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", _URL)
    monkeypatch.setenv("REDIS_PASSWORD", "p@ss/w:1")
    assembled = effective_redis_url()
    # 不转义会把密码里的 @ 当 userinfo 分隔符，解析出的 host/密码全错
    assert assembled == "redis://:p%40ss%2Fw%3A1@127.0.0.1:6399/0"
    import redis as _redis
    kwargs = _redis.Redis.from_url(assembled).connection_pool.connection_kwargs
    assert kwargs["password"] == "p@ss/w:1" and kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 6399 and kwargs["db"] == 0


def test_credentials_already_in_url_are_not_overwritten(monkeypatch):
    from backend.common.redis_url import effective_redis_url

    _clean_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://:urlpw@127.0.0.1:6399/0")
    monkeypatch.setenv("REDIS_PASSWORD", "otherpw")
    assert effective_redis_url() == "redis://:urlpw@127.0.0.1:6399/0"  # URL 为准


def test_no_password_falls_back_to分项_assembly(monkeypatch):
    from backend.common.redis_url import effective_redis_url

    _clean_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_HOST", "r-internal")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_DB", "2")
    assert effective_redis_url() == "redis://r-internal:6380/2"


def test_factory_url_carries_credentials(monkeypatch):
    """缓存工厂不得再交出无凭据的串（NOAUTH 静默降级的入口）。"""
    from backend.common.cache import factory as f

    _clean_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", _URL)
    monkeypatch.setenv("REDIS_PASSWORD", "pw1")
    assert f._redis_url() == "redis://:pw1@127.0.0.1:6399/0"
    f._instances.pop("probe-cred", None)
    try:
        c = f.get_cache("probe-cred")
        assert c._url == "redis://:pw1@127.0.0.1:6399/0"
    finally:
        f._instances.pop("probe-cred", None)


def test_redis_cache_honours_password_alongside_url(monkeypatch):
    """RedisCache 传了 url 也必须用上 password（历史上 url 分支直接把 password 丢了）。"""
    import redis as _redis

    seen = {}

    def _fake_from_url(url, **kwargs):
        seen["url"] = url
        return _DummyCli()

    monkeypatch.setattr(_redis.Redis, "from_url", staticmethod(_fake_from_url), raising=False)
    c = RedisCache(prefix="chatbi:x", url=_URL, password="pw2")
    c._get_redis()
    assert seen["url"] == "redis://:pw2@127.0.0.1:6399/0"


class _DummyCli:
    def ping(self):
        return True


def test_connect_log_and_monitor_do_not_leak_password(caplog, monkeypatch):
    """连接日志与监控 detail 只出地址，不出凭据（护栏 §7）。"""
    import redis as _redis

    from backend.common.redis_url import describe_redis_target, redact_redis_url

    url = "redis://:secretpw@127.0.0.1:6399/0"
    assert redact_redis_url(url) == "redis://:***@127.0.0.1:6399/0"
    assert describe_redis_target(url) == "127.0.0.1:6399/0"
    assert "secretpw" not in describe_redis_target(url)

    monkeypatch.setattr(_redis.Redis, "from_url",
                       staticmethod(lambda u, **kw: _DummyCli()), raising=False)
    with caplog.at_level("INFO"):
        RedisCache(prefix="chatbi:y", url=url)._get_redis()
    assert "secretpw" not in caplog.text


def test_celery_broker_url_is_assembled_with_credentials(monkeypatch):
    """Celery broker/result backend 必须拿装配后的串，否则 worker 连不上队列。"""
    from backend.common import config as cfg
    from backend.modules.flow.tasks import celery_app

    assert celery_app.REDIS_URL is cfg.REDIS_URL
    assert cfg.REDIS_URL == effective_redis_url_for_test()


def effective_redis_url_for_test() -> str:
    from backend.common.redis_url import effective_redis_url

    return effective_redis_url()
