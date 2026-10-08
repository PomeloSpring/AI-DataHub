"""T9 稳定性单测: qmind CLI 熔断状态机 + 同步对账漂移判定(离线, 无 CLI/DB)。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.mind.rag import qmind_retriever as qr
from backend.modules.catalog.services import ontology_kb_sync as ks


@pytest.fixture(autouse=True)
def reset_breaker(monkeypatch):
    # 测试替身模拟 Redis 的服务端 TTL；生产代码不再接受本地时钟裁决。
    class RedisClock:
        now = 0
        fails = 0
        until = 0

        def exists(self, key):
            return self.now < self.until

        def eval(self, script, count, fail_key, open_key, success, threshold, cooldown):
            assert "redis.call('INCR'" in script and count == 2
            if success == '1':
                self.fails = self.until = 0
            else:
                self.fails += 1
                if self.fails >= threshold:
                    self.until, self.fails = self.now + cooldown, 0
    clock = RedisClock()
    monkeypatch.setattr(qr, '_breaker_client', lambda: clock)
    return clock


class TestCircuitBreaker:
    def test_opens_after_threshold_failures(self, reset_breaker):
        for _ in range(qr._CB_FAIL_THRESHOLD):
            qr._breaker_record(False, now=1000.0)
        assert not qr._breaker_allowed(now=1000.0)
        reset_breaker.now += qr._CB_COOLDOWN_SEC + 1
        assert qr._breaker_allowed()

    def test_success_resets(self, reset_breaker):
        qr._breaker_record(False, now=1.0)
        qr._breaker_record(False, now=2.0)
        qr._breaker_record(True, now=3.0)
        assert reset_breaker.fails == 0
        qr._breaker_record(False, now=4.0)
        assert qr._breaker_allowed(now=4.0)  # 1 次失败未达阈值

    def test_half_open_recovery(self, reset_breaker):
        for _ in range(qr._CB_FAIL_THRESHOLD):
            qr._breaker_record(False, now=100.0)
        assert not qr._breaker_allowed(now=100.0)
        reset_breaker.now += qr._CB_COOLDOWN_SEC + 0.5
        assert qr._breaker_allowed()
        qr._breaker_record(True, now=100.0 + qr._CB_COOLDOWN_SEC + 1)
        assert reset_breaker.until == 0


class TestReconcile:
    def _fake_exec(self, rows):
        calls = []

        def fake(sql, params=None, fetchone=False):
            calls.append(sql)
            if "adh_ontology_models" in sql:
                return rows
            return []
        return fake, calls

    def test_drift_triggers_resync(self, monkeypatch):
        rows = [
            {"id": 1, "updated_at": _DT("2026-09-20T10:00:00"),
             "synced_version": "2026-09-20T10:00:00", "status": "success"},   # 一致 → 不推
            {"id": 2, "updated_at": _DT("2026-09-21T08:00:00"),
             "synced_version": "2026-09-20T10:00:00", "status": "success"},   # 漂移 → 推
            {"id": 3, "updated_at": _DT("2026-09-22T08:00:00"),
             "synced_version": "2026-09-22T08:00:00", "status": "failed"},    # 上次失败 → 推
            {"id": 4, "updated_at": _DT("2026-09-22T09:00:00"),
             "synced_version": None, "status": None},                          # 从未同步 → 推
        ]
        fake, _ = self._fake_exec(rows)
        monkeypatch.setattr("backend.common.db.execute_query", fake)
        monkeypatch.setattr(ks, "sync_targets", lambda: [{"id": 9, "name": "kb", "notebook_id": "nb"}])
        pushed = []
        monkeypatch.setattr(ks, "sync_model_to_qmind",
                            lambda mid: pushed.append(mid) or {"synced": 1, "targets": ["kb"]})

        rep = ks.reconcile()
        assert rep["checked"] == 4 and rep["resynced"] == 3
        assert pushed == [2, 3, 4]

    def test_no_global_targets_still_reconciles_per_model(self, monkeypatch):
        # 全局 sync_ontology 库为空不再短路整轮对账: 业务模型按自身 kb_id 逐一 sync_model_to_qmind。
        rows = [{"id": 7, "updated_at": _DT("2026-09-22T09:00:00"),
                 "synced_version": None, "status": None}]
        fake, _ = self._fake_exec(rows)
        monkeypatch.setattr("backend.common.db.execute_query", fake)
        monkeypatch.setattr(ks, "sync_targets", lambda: [])   # 系统全局无库
        pushed = []
        monkeypatch.setattr(ks, "sync_model_to_qmind",
                            lambda mid: pushed.append(mid) or {"synced": 1, "targets": ["kb"]})
        rep = ks.reconcile()
        assert rep["resynced"] == 1 and pushed == [7]


def _DT(s):
    from datetime import datetime
    return datetime.fromisoformat(s)
