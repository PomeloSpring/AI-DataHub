"""图谱重建 /api/graph/sync 的并发互斥与 fail-loud 语义 —— 离线单测(不触碰 Redis/Oxigraph)。

背景: graph_builder.build_from_metadata 是 clear_graph(ds) + 批量写入的**非原子**操作,
两个重建同时跑会互相擦除(一方清空另一方刚写的数据)。按 distributed-first §1, 互斥必须落
共享层(Redis), 进程内标志在多副本下各自为政; 按 no-silent-degradation, 锁不可用时不得
"拿不到锁就先裸跑" —— 宁可不成图, 不可把已有图清坏。

覆盖:
  - 正常路径: 抢到锁 → 执行重建 → 释放锁, 锁 key 按数据源粒度。
  - 并发占用: 锁被他人持有 → 409 带可读原因, 且**绝不调用**重建。
  - Redis 故障: 取锁抛 RedisError → 503 带原因, 且**绝不调用**重建(不裸跑)。
  - 系统域路由: system_scope=True → 锁与重建都落在系统图 ds:-1, 不误清业务图。
  - 释放失败: 仅靠 TTL 过期收敛, 不影响已成功的重建结果(旁路 no-op, 不打断主链路)。

注: 真实 main.py 另挂了 api_permission 中间件, 本用例只验证 sync 端点自身的锁语义,
故用裸 FastAPI 装配 router(与既有测试的装配惯例一致)。
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import redis

from services.semhub.api import graph


class _FakeLock:
    def __init__(self, acquired=True, release_error=None):
        self._acquired = acquired
        self._release_error = release_error
        self.released = False

    def acquire(self, blocking=False):
        return self._acquired

    def release(self):
        self.released = True
        if self._release_error:
            raise self._release_error


class _FakeRedis:
    def __init__(self, lock):
        self._lock = lock
        self.lock_keys = []
        self.lock_kwargs = {}

    def lock(self, key, **kwargs):
        self.lock_keys.append(key)
        self.lock_kwargs = kwargs
        return self._lock


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(graph.router, prefix="/api/graph")
    return TestClient(app)


@pytest.fixture
def service(monkeypatch):
    """替掉真实 GraphService, 记录重建是否被执行及入参。"""
    import services.semhub.graph.graph_service as gs_mod

    inst = MagicMock()
    inst.sync_from_metadata.return_value = {
        "success": True, "tables": 3, "columns": 20, "relations": 2,
        "message": "图谱构建完成：3 表、20 字段",
    }
    monkeypatch.setattr(gs_mod, "GraphService", lambda: inst)
    return inst


def _stub_redis(monkeypatch, fake):
    monkeypatch.setattr(graph.redis, "from_url", lambda url, **kwargs: fake)


def _stub_redis_error(monkeypatch, exc):
    def _raise(url, **kwargs):
        raise exc
    monkeypatch.setattr(graph.redis, "from_url", _raise)


def test_sync_acquires_lock_runs_and_releases(client, service, monkeypatch):
    lock = _FakeLock(acquired=True)
    fake = _FakeRedis(lock)
    _stub_redis(monkeypatch, fake)

    resp = client.post("/api/graph/sync", params={"datasource_id": 1780478236183})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # 锁按数据源粒度, 不同 ds 互不阻塞
    assert fake.lock_keys == [f"adh_graph_sync:ds:{1780478236183}"]
    # 租约必须覆盖最坏重建耗时, 不得是短 TTL(提前过期 = 并发互踩重新打开)
    assert fake.lock_kwargs["timeout"] == graph._GRAPH_SYNC_LOCK_TTL
    assert fake.lock_kwargs["blocking"] is False
    service.sync_from_metadata.assert_called_once_with(1780478236183)
    assert lock.released is True


def test_sync_conflict_returns_409_and_never_runs_rebuild(client, service, monkeypatch):
    """已被占 → 409, 且**不执行**重建: 两个 clear+rebuild 同时跑会互相擦除。"""
    lock = _FakeLock(acquired=False)
    fake = _FakeRedis(lock)
    _stub_redis(monkeypatch, fake)

    resp = client.post("/api/graph/sync", params={"datasource_id": 1})

    assert resp.status_code == 409
    assert "正在生成中" in resp.json()["detail"]
    service.sync_from_metadata.assert_not_called()
    assert lock.released is False   # 没抢到锁不得释放别人的锁


def test_sync_lock_unavailable_returns_503_and_never_runs_bare(client, service, monkeypatch):
    """Redis 故障 → 显式 503 并中止, 严禁降级为"无锁裸跑"(no-silent-degradation)。"""
    _stub_redis_error(monkeypatch, redis.exceptions.RedisError("Connection refused"))

    resp = client.post("/api/graph/sync", params={"datasource_id": 1})

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "互斥锁不可用" in detail and "Connection refused" in detail
    service.sync_from_metadata.assert_not_called()


def test_system_scope_locks_and_rebuilds_system_graph_only(client, service, monkeypatch):
    """系统域(AS-BOT)重建必须落在 ds:-1 系统图, 锁与执行同一口径, 不得误清业务图。"""
    lock = _FakeLock(acquired=True)
    fake = _FakeRedis(lock)
    _stub_redis(monkeypatch, fake)

    resp = client.post("/api/graph/sync",
                       params={"datasource_id": 1, "system_scope": True})

    assert resp.status_code == 200
    from services.datamind.rag.graph_rag.oxigraph_store import SYSTEM_DATASOURCE_ID
    assert fake.lock_keys == [f"adh_graph_sync:ds:{SYSTEM_DATASOURCE_ID}"]
    service.sync_from_metadata.assert_called_once_with(SYSTEM_DATASOURCE_ID)


def test_release_failure_does_not_break_success(client, service, monkeypatch):
    """释放失败属旁路: 靠 TTL 过期收敛, 不得把已成功的重建翻成错误。"""
    lock = _FakeLock(acquired=True,
                     release_error=redis.exceptions.RedisError("connection closed"))
    fake = _FakeRedis(lock)
    _stub_redis(monkeypatch, fake)

    resp = client.post("/api/graph/sync", params={"datasource_id": 1})

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_rebuild_failure_releases_lock_and_returns_500(client, service, monkeypatch):
    """重建自身报错 → 500 且锁必须释放(否则要等满 TTL 才能重试)。"""
    lock = _FakeLock(acquired=True)
    fake = _FakeRedis(lock)
    _stub_redis(monkeypatch, fake)
    service.sync_from_metadata.side_effect = RuntimeError("Oxigraph unreachable")

    resp = client.post("/api/graph/sync", params={"datasource_id": 1})

    assert resp.status_code == 500
    assert "Oxigraph unreachable" in resp.json()["detail"]
    assert lock.released is True
