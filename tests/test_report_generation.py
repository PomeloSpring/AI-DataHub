"""报告生成入口、真实状态及数据来源的离线回归。"""
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from services.dataviz.services import report_service as reports


@pytest.mark.parametrize("source", [{}, {"intent": {}, "dataset_id": 1}, {"saved_query_id": 0},
    {"intent": {"object": "订单", "sql": "SELECT 1"}}])
def test_submission_rejects_missing_or_ambiguous_source(source):
    with pytest.raises(ValidationError):
        reports.ReportSubmission(workspace_id=3, **source)


def test_source_is_required_before_dispatch(monkeypatch):
    create = Mock()
    monkeypatch.setattr(reports, "create_report", create)
    with pytest.raises(ValidationError):
        reports.submit_report({"workspace_id": 3}, {"user_id": 7}, Mock())
    create.assert_not_called()


def test_fact_table_serializes_dict_values_and_escapes_html():
    content = reports.render_fact_report("报告", [{"status": "success", "title": "统计",
        "columns": ["physical_column"], "rows": [{"physical_column": "<script>x</script>|值"}]}])
    assert "physical_column" not in content
    assert "<script>" not in content
    assert "&lt;script&gt;" in content
    assert "仅展示" in content


def test_empty_results_not_invented_as_success():
    with pytest.raises(ValueError):
        reports.render_fact_report("失败", [{"status": "failed", "error": "private host"}])


@pytest.fixture
def generation(monkeypatch):
    row = {"id": 1, "owner_id": 7, "workspace_id": 3, "run_key": "r1", "title": "报告",
           "analysis_source": {"intent": {"object": "订单"}}, "generation_status": "queued"}
    updates = []
    monkeypatch.setattr(reports, "load_report", lambda rid: dict(row))
    monkeypatch.setattr(reports, "claim_report", lambda rid: row["generation_status"] == "queued")
    def update(rid, **values):
        updates.append(values)
        row.update(values)
        return True
    monkeypatch.setattr(reports, "finish_report", update)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda *a: {"user_id": 7, "workspace_id": 3})
    return row, updates


def test_query_failure_has_no_success_body(generation, monkeypatch):
    row, updates = generation
    monkeypatch.setattr(reports, "execute_report_source", Mock(side_effect=RuntimeError("host password")))
    reports.run_report(1)
    assert row["generation_status"] == "failed"
    assert not row.get("content")
    assert "host password" not in str(updates)


def test_fact_only_report_is_explicitly_degraded(generation, monkeypatch):
    row, updates = generation
    monkeypatch.setattr(reports, "execute_report_source", lambda *a: {
        "columns": ["count"], "rows": [{"count": 3}], "status": "success", "_security_context": {}})
    reports.run_report(1)
    assert row["generation_status"] == "degraded"
    assert "3" in row["content"]


def test_report_dispatch_failure_persisted(monkeypatch):
    monkeypatch.setattr(reports, "authorize_workspace", lambda *a: 3)
    monkeypatch.setattr(reports, "create_report", lambda **kw: {"id": 1})
    monkeypatch.setattr(reports, "dispatch_report", Mock(side_effect=RuntimeError("broker host")))
    finish = Mock()
    monkeypatch.setattr(reports, "finish_report", finish)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        reports.submit_report({"workspace_id": 3, "intent": {"object": "订单"}}, {"user_id": 7}, Mock())
    assert exc.value.status_code == 503
    assert finish.call_args.kwargs["generation_status"] == "failed"


def test_generate_api_persists_then_dispatches_and_returns_202(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.dataviz.api.report import router
    from services.shared.common.auth import get_current_user
    app = FastAPI()
    app.include_router(router, prefix="/api/reports")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": 7}
    monkeypatch.setattr(reports, "authorize_workspace", lambda *a: 3)
    monkeypatch.setattr("services.dataviz.api.report.authorize_workspace", lambda *a: 3)
    calls = []
    monkeypatch.setattr(reports, "create_report", lambda **kw: calls.append("persist") or {"id": 1})
    monkeypatch.setattr(reports, "dispatch_report", lambda *a: calls.append("dispatch"))
    with TestClient(app) as client:
        response = client.post("/api/reports/generate", json={"workspace_id": 3, "intent": {"object": "订单"}})
    assert response.status_code == 202
    assert response.json()["report_id"] == 1 and response.json()["run_id"]
    assert calls == ["persist", "dispatch"]


def test_saved_query_is_owner_and_workspace_scoped(monkeypatch):
    from services.shared.common import db
    select = Mock(return_value=None)
    monkeypatch.setattr(db, "execute_query", select)
    execute = Mock()
    monkeypatch.setattr(reports, "governed_execute", execute)
    with pytest.raises(PermissionError):
        reports.execute_report_source({"saved_query_id": 11}, {"user_id": 7, "workspace_id": 3})
    assert select.call_args.args[1] == (11, 7, 3)
    execute.assert_not_called()


def test_semantic_source_rejects_identity_override():
    with pytest.raises(ValueError):
        reports.execute_semantic_source({"object": "订单", "datasource_id": 123}, {"user_id": 7, "workspace_id": 3})


def test_dataset_scope_change_invalidates_report(monkeypatch):
    from services.dataviz.services import report_access, dataset_service
    from services.shared.common import auth
    dataset = {"id": 1, "status": "active", "visibility": "private", "owner_id": 7}
    monkeypatch.setattr(dataset_service, "get_dataset", lambda did: dataset)
    scopes = []
    monkeypatch.setattr(dataset_service, "_scope_filters", lambda *a: scopes)
    identity = {"user_id": 7, "workspace_id": 3}
    digest = report_access.dataset_snapshot(1, identity)
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda *a: identity)
    monkeypatch.setattr(report_access, "policy_snapshot", lambda *a: {"policy_digest": "p1"})
    row = {"owner_id": 7, "workspace_id": 3, "security_context": {"version": 1, "policy_digest": "p1",
            "sources": [], "dataset_id": 1, "dataset_digest": digest}}
    assert report_access.snapshot_is_current(row)
    scopes.append({"field": "region", "op": "eq", "value": "华东"})
    assert not report_access.snapshot_is_current(row)


def test_cancelled_run_cannot_expose_saved_report(monkeypatch):
    from services.dataviz.services import report_access
    from services.shared.common import db
    monkeypatch.setattr(db, "execute_query", lambda *a, **kw: {"status": "cancelled"})
    row = {"log_id": 1, "owner_id": 7, "security_context": {"version": 1, "policy_digest": "p1"}}
    assert not report_access.snapshot_is_current(row)


def test_pending_report_never_serves_body_to_owner(monkeypatch):
    from services.dataviz.services import report_access
    monkeypatch.setattr(report_access, "snapshot_is_current", lambda *a: True)
    assert not report_access.can_access_report({"generation_status": "running", "owner_id": 7}, {"user_id": 7})
