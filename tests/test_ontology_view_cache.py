"""本体只读视图缓存的失效口径（正文变/字典变/TTL/LRU）。

缓存是性能层，但**绝不能返回过期视图**——这些用例锁的就是“数据变动即失效”。
"""
import pytest

from backend.modules.catalog.services import view_cache


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=None):
        self._sql = sql

    def fetchone(self):
        return self._rows.pop(0)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._rows)


@pytest.fixture(autouse=True)
def _clean():
    view_cache.clear()
    yield
    view_cache.clear()


def test_hit_after_put_with_same_signature():
    key = (1, "internal", "json")
    sig = view_cache.signature('{"a":1}', "m:1:x|d:2:y")
    view_cache.put(key, sig, "TEXT")
    assert view_cache.get(key, sig) == "TEXT"


def test_content_change_invalidates():
    key = (1, "internal", "md")
    sig1 = view_cache.signature('{"a":1}', "fp")
    sig2 = view_cache.signature('{"a":2}', "fp")
    view_cache.put(key, sig1, "OLD")
    assert view_cache.get(key, sig2) is None


def test_dict_fingerprint_change_invalidates():
    """字典（adh_metrics/adh_dimensions）变动后，同一份正文也必须重算。"""
    key = (1, "internal", "json")
    sig1 = view_cache.signature('{"a":1}', "adh_metrics:10:t1|adh_dimensions:5:t1")
    sig2 = view_cache.signature('{"a":1}', "adh_metrics:11:t2|adh_dimensions:5:t1")
    view_cache.put(key, sig1, "OLD")
    assert view_cache.get(key, sig2) is None


def test_ttl_expiry(monkeypatch):
    key = (1, "internal", "yaml")
    sig = view_cache.signature("{}", "fp")
    view_cache.put(key, sig, "TEXT")
    monkeypatch.setattr(view_cache, "TTL_SEC", -1.0)
    assert view_cache.get(key, sig) is None


def test_lru_eviction(monkeypatch):
    monkeypatch.setattr(view_cache, "MAX_ENTRIES", 2)
    sig = view_cache.signature("{}", "fp")
    for i in range(3):
        view_cache.put((i, "internal", "json"), sig, f"T{i}")
    assert view_cache.get((0, "internal", "json"), sig) is None
    assert view_cache.get((2, "internal", "json"), sig) == "T2"


def test_dict_fingerprint_reads_both_tables(monkeypatch):
    rows = [{"mc": 12, "mu": "2026-11-19 10:00:00", "dc": 7, "du": "2026-11-19 09:00:00"}]
    monkeypatch.setattr(view_cache, "get_metadata_conn", lambda: _FakeConn(list(rows)))
    fp = view_cache.dict_fingerprint()
    assert "adh_metrics:12:2026-11-19 10:00:00" in fp
    assert "adh_dimensions:7:2026-11-19 09:00:00" in fp


def test_dict_fingerprint_raises_when_db_unavailable(monkeypatch):
    """指纹取不到时抛异常（调用方据此跳过缓存），不得返回“看起来没变”的假指纹。"""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(view_cache, "get_metadata_conn", _boom)
    with pytest.raises(RuntimeError):
        view_cache.dict_fingerprint()
