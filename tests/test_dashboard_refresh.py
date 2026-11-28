"""看板刷新服务端化：配置、TTL、单板互斥、异步执行与监控投影。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.modules.viz.services import dashboard_refresh_service as svc


@pytest.mark.parametrize("seconds", [5, 10, 31, 86461])
def test_fixed_interval_rejects_removed_or_unknown_slots(seconds):
    with pytest.raises(ValueError):
        svc._validate_config({"schedule_type": "interval", "interval_seconds": seconds})


@pytest.mark.parametrize("seconds", [30, 60, 300, 600, 1800, 3600, 86400, 604800, 2592000])
def test_fixed_interval_accepts_supported_slots(seconds):
    out = svc._validate_config({"schedule_type": "interval", "interval_seconds": seconds})
    assert out["interval_seconds"] == seconds
    assert out["cache_ttl_seconds"] == seconds
    assert out["is_active"] == 1


def test_cron_validation_reuses_platform_parser(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.core.task_runtime.call", lambda name, expr: calls.append((name, expr)))
    out = svc._validate_config({"schedule_type": "cron", "cron_expression": "0 9 * * 1-5"})
    assert out["cron_expression"] == "0 9 * * 1-5"
    assert calls == [("task.parse_cron", "0 9 * * 1-5")]


def test_cache_ttl_has_explicit_bounds():
    with pytest.raises(ValueError):
        svc._validate_config({"schedule_type": "disabled", "cache_ttl_seconds": 5})
    with pytest.raises(ValueError):
        svc._validate_config({"schedule_type": "disabled", "cache_ttl_seconds": 2592001})


def test_request_refresh_cache_hit_does_not_dispatch(monkeypatch):
    monkeypatch.setattr(svc, "assert_refresh_manageable", lambda *_: None)
    monkeypatch.setattr(svc, "_dashboard", lambda *_: {"id": 10, "params": []})
    monkeypatch.setattr(svc, "_ensure_config", lambda *_: {"cache_ttl_seconds": 60})
    monkeypatch.setattr(svc, "_active_run", lambda *_: None)
    monkeypatch.setattr(svc, "_cache_is_fresh", lambda *_: True)
    monkeypatch.setattr(svc, "resolve_execution_owner", lambda *_: {"user_id": 1, "role": "admin", "workspace_id": 0})
    monkeypatch.setattr(svc, "_security_scope_base", lambda *_: "scope")
    dispatched = []
    monkeypatch.setattr(svc, "_dispatch", lambda run: dispatched.append(run))
    out = svc.request_refresh(10, {"user_id": 1, "role": "admin"})
    assert out["cache_hit"] is True and out["run_id"] is None
    assert dispatched == []


def test_request_refresh_deduplicates_existing_active_run(monkeypatch):
    active = {"id": 7, "status": "running", "requested_by": 2}
    monkeypatch.setattr(svc, "assert_refresh_manageable", lambda *_: None)
    monkeypatch.setattr(svc, "_dashboard", lambda *_: {"id": 10, "params": []})
    monkeypatch.setattr(svc, "_ensure_config", lambda *_: {"cache_ttl_seconds": 60})
    monkeypatch.setattr(svc, "_active_run", lambda *_: active)
    dispatched = []
    monkeypatch.setattr(svc, "_dispatch", lambda run: dispatched.append(run))
    out = svc.request_refresh(10, {"user_id": 1, "role": "admin"})
    assert out["run_id"] == 7 and out["deduplicated"] is True
    assert dispatched == []


def test_request_refresh_creates_and_dispatches_once(monkeypatch):
    run = {"id": 8, "status": "queued", "requested_by": 1}
    monkeypatch.setattr(svc, "assert_refresh_manageable", lambda *_: None)
    monkeypatch.setattr(svc, "_dashboard", lambda *_: {"id": 10, "params": []})
    monkeypatch.setattr(svc, "_ensure_config", lambda *_: {"cache_ttl_seconds": 60, "version": 1})
    monkeypatch.setattr(svc, "_active_run", lambda *_: None)
    monkeypatch.setattr(svc, "_cache_is_fresh", lambda *_: False)
    monkeypatch.setattr(svc, "resolve_execution_owner", lambda *_: {"user_id": 1, "role": "admin", "workspace_id": 0})
    monkeypatch.setattr(svc, "_security_scope_base", lambda *_: "scope")
    monkeypatch.setattr(svc, "_create_run", lambda *_: run)
    dispatched = []
    monkeypatch.setattr(svc, "_dispatch", lambda item: dispatched.append(item))
    out = svc.request_refresh(10, {"user_id": 1, "role": "admin"})
    assert out["run_id"] == 8 and out["deduplicated"] is False
    assert dispatched == [run]


def test_claim_scheduled_run_skips_when_board_is_busy(monkeypatch):
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: {"dashboard_id": 10, "version": 1})
    monkeypatch.setattr(svc, "_active_run", lambda *_: {"id": 1, "status": "running"})
    assert svc.claim_scheduled_run(10, "cron-key", "cron") is None


class _Cursor:
    def __init__(self):
        self.rowcount = 1

    def execute(self, *args):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Conn:
    def cursor(self):
        return _Cursor()

    def commit(self):
        return None

    def close(self):
        return None


def test_execute_run_records_partial_and_never_claims_success(monkeypatch):
    run = {"id": 3, "dashboard_id": 10, "status": "queued"}
    monkeypatch.setattr(svc, "get_run", lambda *a, **k: run)
    monkeypatch.setattr(svc, "get_metadata_conn", lambda: _Conn())
    monkeypatch.setattr(svc, "_dashboard", lambda *_: {"id": 10, "workspace_id": 1, "params": []})
    monkeypatch.setattr(svc, "get_config", lambda *_: {"created_by": 1})
    monkeypatch.setattr(svc, "resolve_execution_owner", lambda *_: {"user_id": 1, "username": "u", "workspace_id": 1})
    monkeypatch.setattr(svc, "assert_refresh_manageable", lambda *_: None)
    monkeypatch.setattr(svc, "_security_scope_base", lambda *_: "scope")
    from backend.modules.viz.services.dashboard_service import chart_service
    monkeypatch.setattr(chart_service, "refresh_all_charts",
                        lambda *a, **k: {"charts": {1: {"rows": []}, 2: {"error": "失败"}}})
    finished = []
    monkeypatch.setattr(svc, "_finish_run", lambda *a, **k: finished.append((a, k)) or True)
    svc.execute_run(3, "worker-1")
    args, kwargs = finished[0]
    assert args[1] == "partial"
    assert kwargs["succeeded"] == 1 and kwargs["failed"] == 1


def test_decorate_payload_strips_cache_without_dataset_access(monkeypatch):
    payload = {"id": 10, "workspace_id": 1, "charts": [{
        "id": 1, "chart_type": "bar", "source_type": "dataset", "source_id": 9,
        "data_cache": {"columns": ["x"], "rows": [{"x": 1}]},
        "data_cache_at": datetime.now(), "data_cache_key": "expected",
    }]}
    monkeypatch.setattr(svc, "get_config", lambda *_: {"cache_ttl_seconds": 60})
    monkeypatch.setattr(svc, "assert_refresh_manageable", lambda *_: None)
    monkeypatch.setattr(svc, "_dashboard", lambda *_: {"workspace_id": 1, "params": []})
    monkeypatch.setattr(svc, "_cache_key", lambda *_: "expected")
    monkeypatch.setattr(svc, "_security_scope_base", lambda *_: "scope")
    import importlib
    dash_module = importlib.import_module("backend.modules.viz.services.dashboard_service")
    monkeypatch.setattr(dash_module, "_expected_cache_key", lambda *_: "expected")
    monkeypatch.setattr("backend.core.role_service.role_service.get_user_allowed_datasources", lambda *_: [])
    monkeypatch.setattr("backend.modules.viz.services.dataset_service.get_dataset", lambda *_: {"id": 9, "owner_id": 2, "visibility": "workspace"})
    monkeypatch.setattr("backend.modules.viz.services.dataset_service.dataset_visible_to", lambda *_: False)
    svc.decorate_dashboard_payload(payload, 1, "viewer", 1)
    assert payload["charts"][0]["data_cache"] is None
    assert payload["charts"][0]["cache_access_denied"] is True


def test_cache_freshness_uses_chart_identity_and_scope(monkeypatch):
    import importlib
    dash = importlib.import_module("backend.modules.viz.services.dashboard_service")
    now = datetime(2026, 11, 28, 4, 0, 0)
    chart = {"id": 1, "chart_type": "bar", "config": {}, "source_type": "dataset",
             "source_id": 9, "semantic_query": None, "data_cache": {"rows": []},
             "data_cache_at": now, "visibility": "workspace", "owner_id": 2}
    board_key = svc._cache_key(10, {})
    chart["data_cache_key"] = dash._expected_cache_key(chart, board_key, "scope")
    chart["data_cache_scope"] = "scope"
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: [chart])
    monkeypatch.setattr(svc, "_now", lambda: now + timedelta(seconds=10))
    assert svc._cache_is_fresh(10, {"cache_ttl_seconds": 60}, {"params": []}, "scope") is True


def test_claim_scheduled_run_skips_when_cache_is_fresh(monkeypatch):
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: {"dashboard_id": 10, "version": 1, "created_by": 1})
    monkeypatch.setattr(svc, "_active_run", lambda *_: None)
    monkeypatch.setattr(svc, "_dashboard", lambda *_: {"id": 10, "workspace_id": 0, "params": []})
    monkeypatch.setattr(svc, "resolve_execution_owner", lambda *_: {"user_id": 1, "role": "admin", "workspace_id": 0})
    monkeypatch.setattr(svc, "_security_scope_base", lambda *_: "scope")
    monkeypatch.setattr(svc, "_cache_is_fresh", lambda *_: True)
    assert svc.claim_scheduled_run(10, "cron-key", "cron") is None


def test_cancelled_run_fences_cache_write(monkeypatch):
    import importlib
    dash = importlib.import_module("backend.modules.viz.services.dashboard_service")

    class CancelledCursor:
        rowcount = 0
        def execute(self, *args):
            return None
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class CancelledConn:
        def cursor(self):
            return CancelledCursor()
        def commit(self):
            return None
        def close(self):
            return None

    monkeypatch.setattr(dash, "get_metadata_conn", lambda: CancelledConn())
    with pytest.raises(dash.ChartRefreshCancelled):
        dash._write_chart_cache({"id": 1, "name": "图", "chart_type": "bar", "config": {}},
                                ["x"], [{"x": 1}], cache_key="board", cache_scope="scope",
                                refresh_run_id=9)


def test_security_scope_changes_with_role_policy(monkeypatch):
    monkeypatch.setattr("backend.core.role_service.role_service.get_user_roles",
                        lambda *_: [{"id": 1}])
    rows = [{"kind": "perm", "a": "dashboard:read", "b": "", "c": ""}]
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: rows)
    first = svc._security_scope_base(1, "viewer", 1)
    rows[0]["a"] = "dashboard:manage"
    second = svc._security_scope_base(1, "viewer", 1)
    assert first != second


def test_dashboard_task_accepts_beat_trigger_kwarg():
    import inspect
    from backend.modules.flow.tasks.dashboard_tasks import refresh_dashboard
    assert "trigger_type" in inspect.signature(refresh_dashboard.run).parameters


def test_beat_schedule_contains_dashboard_refresh(monkeypatch):
    from backend.modules.flow.tasks import beat_schedule as beat
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service
    from backend.modules.flow.dag.dag_service import dag_service

    monkeypatch.setattr(scheduled_task_service, "list_active_tasks", lambda: [])
    monkeypatch.setattr(dag_service, "list_active_scheduled", lambda: [])
    monkeypatch.setattr("backend.common.db.execute_query", lambda *a, **k: [])

    def fake_call(name, *args):
        if name == "dashboard_refresh.configs":
            return [{"dashboard_id": 10, "is_active": True, "schedule_type": "cron",
                     "cron_expression": "0 9 * * *", "timezone": "Asia/Shanghai"}]
        return []

    monkeypatch.setattr("backend.core.task_runtime.call", fake_call)
    schedule = beat.build_beat_schedule()
    entry = schedule["dashboard_refresh_10"]
    assert entry["task"] == "backend.modules.flow.tasks.dashboard_tasks.refresh_dashboard"
    assert entry["options"]["queue"] == "dashboard_refresh"
    assert isinstance(entry["schedule"], beat.WallClockCrontab)
