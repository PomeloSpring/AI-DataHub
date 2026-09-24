"""系统监控·中间件检测：聚合口径与“未配置=skipped”（离线，探测函数打桩）。"""
import importlib

import pytest

mon = importlib.import_module("services.authservice.api.monitoring")


def _item(key, status, required=False, configured=True):
    return {"key": key, "name": key, "desc": "", "category": "存储", "required": required,
            "configured": configured, "status": status, "latency_ms": 1.0 if status == "healthy" else None,
            "detail": "", "message": ""}


def test_middleware_summary_and_required_down(monkeypatch):
    checks = [
        lambda: _item("mysql", "healthy", required=True),
        lambda: _item("redis", "down", required=True),      # 必需项故障
        lambda: _item("doris", "down"),                     # 可选故障
        lambda: _item("oxigraph", "skipped", configured=False),
        lambda: _item("object_storage", "healthy"),
    ]
    monkeypatch.setattr(mon, "_MIDDLEWARE_CHECKS", checks)
    res = mon.get_middleware_health(admin={"user_id": 1})
    s = res["summary"]
    assert s["total"] == 5
    assert s["healthy"] == 2
    assert s["down"] == 2            # redis + doris（不含 skipped）
    assert s["skipped"] == 1
    assert s["required_down"] == 1   # 仅 redis


def test_endpoint_registered_and_admin_gated():
    route = next(r for r in mon.router.routes if r.path == "/middleware")
    names: list[str] = []
    def collect(dep):
        for d in dep.dependencies:
            names.append(getattr(d.call, "__name__", ""))
            collect(d)
    collect(route.dependant)
    assert "require_admin" in names  # 仅管理员可调


def test_unconfigured_object_storage_is_skipped(monkeypatch):
    from services.shared.common import config as cfg
    monkeypatch.setattr(cfg, "OBJECT_STORAGE_ENDPOINT", "", raising=False)
    r = mon._check_object_storage()
    assert r["status"] == "skipped" and r["configured"] is False


def test_doris_not_in_middleware_registry():
    # Doris 不是平台必需中间件，已从全局监控移除
    assert not hasattr(mon, "_check_doris")
    assert all("doris" != getattr(f, "__name__", "") for f in mon._MIDDLEWARE_CHECKS)


def test_oxigraph_probe_never_raises(monkeypatch):
    from services.shared.common import config as cfg
    monkeypatch.setattr(cfg, "OXIGRAPH_URL", "http://127.0.0.1:59999", raising=False)
    r = mon._check_oxigraph()
    assert r["status"] in ("healthy", "down")  # 不可达 → down，不抛异常


class _FakeInspect:
    def __init__(self, result=None, raise_exc=False):
        self.result, self.raise_exc = result or {}, raise_exc
    def ping(self):
        if self.raise_exc:
            raise ConnectionError("broker down")
        return self.result


class _FakeControl:
    def __init__(self, result=None, raise_exc=False):
        self._r, self._e = result, raise_exc
    def inspect(self, timeout=None):
        return _FakeInspect(self._r, self._e)


class _FakeCelery:
    result = None
    raise_exc = False
    def __init__(self, *a, **k):
        self.control = _FakeControl(_FakeCelery.result, _FakeCelery.raise_exc)


def test_celery_workers_healthy_counts(monkeypatch):
    import celery
    _FakeCelery.result = {"celery@a": {"ok": "pong"}, "celery@b": {"ok": "pong"}}
    _FakeCelery.raise_exc = False
    monkeypatch.setattr(celery, "Celery", _FakeCelery)
    r = mon._check_celery_workers()
    assert r["status"] == "healthy" and r["detail"] == "2 个 worker 在线"


def test_celery_workers_down_when_none(monkeypatch):
    import celery
    _FakeCelery.result = {}; _FakeCelery.raise_exc = False
    monkeypatch.setattr(celery, "Celery", _FakeCelery)
    r = mon._check_celery_workers()
    assert r["status"] == "down" and r["required"] is False


def test_celery_workers_fail_safe_on_error(monkeypatch):
    import celery
    _FakeCelery.result = None; _FakeCelery.raise_exc = True
    monkeypatch.setattr(celery, "Celery", _FakeCelery)
    r = mon._check_celery_workers()
    assert r["status"] == "down" and "broker down" in r["message"]
