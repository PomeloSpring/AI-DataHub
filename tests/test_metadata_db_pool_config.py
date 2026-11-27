"""元数据库连接池配置：可配置、配错显式报错、池等待超时真生效。

锁三件事（缺一条就会退回"改配置不生效/静默回退/无限挂起"）：
1. 池大小与超时从 backend/.env 解析，未配置用默认值；
2. 非法值/越界组合抛 ValueError（fail-loud），不静默回退默认；
3. _TimeoutPooledDB 的等待超时真接进 DBUtils 的 _wait_lock（历史缺陷：
   POOL_TIMEOUT 定义了却从未传给池，池满 = Condition.wait() 无限挂起）。
"""
import threading

import pytest
from dbutils.pooled_db import TooManyConnectionsError

from backend.common.db import metadata_db
from backend.common.db.metadata_db import _env_float, _env_int, _pool_settings

POOL_KEYS = (
    "METADATA_DB_POOL_MIN_CACHED",
    "METADATA_DB_POOL_MAX_CACHED",
    "METADATA_DB_POOL_MAX_CONNECTIONS",
    "METADATA_DB_POOL_TIMEOUT",
    "METADATA_DB_CONNECT_TIMEOUT",
    "METADATA_DB_READ_TIMEOUT",
    "METADATA_DB_WRITE_TIMEOUT",
)


@pytest.fixture()
def clean_env(monkeypatch):
    """用例从"全部未配置"起步，逐项验证默认值与覆盖。"""
    for key in POOL_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class TestPoolSettingsDefaults:
    def test_all_defaults_when_unconfigured(self, clean_env):
        cfg = _pool_settings()
        assert cfg["mincached"] == 1
        assert cfg["maxcached"] == 3
        assert cfg["maxconnections"] == 8
        assert cfg["pool_timeout"] == 10.0
        assert cfg["connect_timeout"] == 5.0
        assert cfg["read_timeout"] == 5.0
        assert cfg["write_timeout"] == 15.0

    def test_env_overrides_default(self, clean_env):
        clean_env.setenv("METADATA_DB_POOL_MIN_CACHED", "2")
        clean_env.setenv("METADATA_DB_POOL_MAX_CACHED", "5")
        clean_env.setenv("METADATA_DB_POOL_MAX_CONNECTIONS", "16")
        clean_env.setenv("METADATA_DB_POOL_TIMEOUT", "30")
        clean_env.setenv("METADATA_DB_CONNECT_TIMEOUT", "3")
        clean_env.setenv("METADATA_DB_READ_TIMEOUT", "8")
        clean_env.setenv("METADATA_DB_WRITE_TIMEOUT", "20")
        cfg = _pool_settings()
        assert cfg["mincached"] == 2
        assert cfg["maxcached"] == 5
        assert cfg["maxconnections"] == 16
        assert cfg["pool_timeout"] == 30.0
        assert cfg["connect_timeout"] == 3.0
        assert cfg["read_timeout"] == 8.0
        assert cfg["write_timeout"] == 20.0

    def test_zero_pool_timeout_is_honored_not_treated_as_missing(self, clean_env):
        """0 是显式配置（不限时等待），不得被当成"未配置"吃回默认 10。"""
        clean_env.setenv("METADATA_DB_POOL_TIMEOUT", "0")
        assert _pool_settings()["pool_timeout"] == 0.0


class TestPoolSettingsFailLoud:
    @pytest.mark.parametrize("key,value", [
        ("METADATA_DB_POOL_MIN_CACHED", "abc"),
        ("METADATA_DB_POOL_MAX_CONNECTIONS", "8.5"),
        ("METADATA_DB_POOL_TIMEOUT", "soon"),
        ("METADATA_DB_READ_TIMEOUT", "x"),
    ])
    def test_non_numeric_value_raises(self, clean_env, key, value):
        clean_env.setenv(key, value)
        with pytest.raises(ValueError, match=key):
            _pool_settings()

    @pytest.mark.parametrize("key,value", [
        ("METADATA_DB_POOL_MIN_CACHED", "-1"),
        ("METADATA_DB_POOL_MAX_CACHED", "-2"),
        ("METADATA_DB_POOL_MAX_CONNECTIONS", "0"),
        ("METADATA_DB_POOL_TIMEOUT", "-3"),
        ("METADATA_DB_CONNECT_TIMEOUT", "0"),
        ("METADATA_DB_READ_TIMEOUT", "-0.5"),
        ("METADATA_DB_WRITE_TIMEOUT", "0"),
    ])
    def test_out_of_range_value_raises(self, clean_env, key, value):
        clean_env.setenv(key, value)
        with pytest.raises(ValueError, match=key.split("=")[0] if "=" in key else key):
            _pool_settings()

    def test_min_greater_than_max_connections_raises(self, clean_env):
        """DBUtils 会静默抬高 maxconnections——必须在配置侧显式拒绝，防配置与生效值分叉。"""
        clean_env.setenv("METADATA_DB_POOL_MIN_CACHED", "9")
        clean_env.setenv("METADATA_DB_POOL_MAX_CONNECTIONS", "8")
        with pytest.raises(ValueError, match="METADATA_DB_POOL_MIN_CACHED"):
            _pool_settings()

    def test_max_cached_greater_than_max_connections_raises(self, clean_env):
        clean_env.setenv("METADATA_DB_POOL_MAX_CACHED", "9")
        clean_env.setenv("METADATA_DB_POOL_MAX_CONNECTIONS", "8")
        with pytest.raises(ValueError, match="METADATA_DB_POOL_MAX_CACHED"):
            _pool_settings()

    def test_env_int_env_float_helpers(self, monkeypatch):
        monkeypatch.setenv("T_INT", "42")
        monkeypatch.setenv("T_FLOAT", "1.5")
        monkeypatch.setenv("T_BAD", "not-a-number")
        monkeypatch.delenv("T_INT_MISSING", raising=False)
        assert _env_int("T_INT", 1) == 42
        assert _env_float("T_FLOAT", 0.0) == 1.5
        assert _env_int("T_INT_MISSING", 7) == 7
        with pytest.raises(ValueError, match="T_BAD"):
            _env_int("T_BAD", 1)
        with pytest.raises(ValueError, match="T_BAD"):
            _env_float("T_BAD", 1.0)


class TestTimeoutWiredIntoWaitLock:
    def _pool(self, pool_timeout: float):
        # mincached=0：构造阶段不建真实连接，无需数据库即可测等待逻辑。
        return metadata_db._TimeoutPooledDB(
            creator=None,
            mincached=0,
            maxconnections=1,
            blocking=True,
            pool_timeout=pool_timeout,
        )

    def test_wait_times_out_with_diagnosable_error(self):
        pool = self._pool(0.05)
        # DBUtils 的约定：_wait_lock 总在持有 self._lock 时被 connection() 调用。
        with pool._lock:
            with pytest.raises(TooManyConnectionsError, match="METADATA_DB_POOL_MAX_CONNECTIONS"):
                pool._wait_lock()

    def test_wait_returns_when_notified_before_deadline(self):
        pool = self._pool(5.0)

        def _notify():
            with pool._lock:
                pool._lock.notify()

        # 先持锁再启动 timer：保证 notify 只会落在 wait() 等待期间（否则通知会丢失）。
        pool._lock.acquire()
        t = threading.Timer(0.05, _notify)
        t.start()
        try:
            pool._wait_lock()  # 收到通知即返回，不抛
        finally:
            t.cancel()
            pool._lock.release()

    def test_pool_timeout_not_forwarded_to_creator(self):
        """pool_timeout 必须被子类拦截，透传给 pymysql 会 TypeError。"""
        pool = self._pool(1.0)
        assert pool._pool_timeout == 1.0
        assert "pool_timeout" not in pool._kwargs

    def test_zero_timeout_keeps_legacy_infinite_wait_path(self):
        """0 = 旧的不限时等待分支（显式配置，非默认）。"""
        pool = self._pool(0.0)
        assert pool._pool_timeout == 0.0
        # 该分支调 Condition.wait() 无参——用通知线程证明它走的是无限等待而非超时抛错。
        pool._lock.acquire()
        t = threading.Timer(0.05, lambda: (pool._lock.acquire(), pool._lock.notify(), pool._lock.release()))
        t.start()
        try:
            pool._wait_lock()
        finally:
            t.cancel()
            pool._lock.release()
