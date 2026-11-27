"""P3 能力流程 API 契约（/api/capability-flows，capability:run 保护）。

锁：路由面（runs CRUD + cancel + 人工复核）、Idempotency-Key 传递、
分页参数界、错误码到 HTTP 的映射（403/404/409/422/503），
以及**编排权限不是执行权限**的语义（服务层双闸 FlowError 映射 403）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient

from backend.modules.platform.api import capability_flows as api
from backend.modules.platform.services import capability_flow_service as flows


def _client(monkeypatch, *, workspace=3):
    app = FastAPI()
    app.include_router(api.router)

    async def _user():
        return {"user_id": 7, "username": "alice"}

    async def _ws():
        return workspace

    app.dependency_overrides[api.get_current_user] = _user
    app.dependency_overrides[api._workspace] = _ws
    return TestClient(app)


def test_routes_cover_runs_lifecycle():
    paths = {getattr(r, "path", "") for r in api.router.routes}
    assert {"/runs", "/runs/{run_id}", "/runs/{run_id}/cancel",
            "/runs/{run_id}/resume"} <= paths


def test_pagination_bounds_locked_in_openapi():
    app = FastAPI()
    app.include_router(api.router)
    schema = get_openapi(title="flows", version="1", routes=app.routes)
    params = {p["name"]: p["schema"]
              for p in schema["paths"]["/runs"]["get"]["parameters"]}
    assert params["page"]["minimum"] == 1
    assert params["page_size"]["maximum"] == 50


def test_create_run_requires_and_forwards_idempotency_key(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {"run_id": 1, "scenario": kwargs["scenario_key"], "status": "waiting_human",
                "current_step": 3, "steps": [], "output": {}, "input": {}}

    monkeypatch.setattr(flows, "create_run", fake_create)
    client = _client(monkeypatch)
    resp = client.post("/runs", headers={"Idempotency-Key": "ui:1"},
                       json={"scenario": "ops_content_publish",
                             "input": {"title": "t", "body": "b"}})
    assert resp.status_code == 200 and resp.json()["run_id"] == 1
    assert captured["idempotency_key"] == "ui:1"
    assert captured["user_id"] == 7 and captured["workspace_id"] == 3
    assert captured["input_payload"] == {"title": "t", "body": "b"}


def test_permission_denied_maps_to_403(monkeypatch):
    def deny(**kwargs):
        raise flows.FlowError("PERMISSION_DENIED",
                              "角色未授予「capability:run」权限，无法执行「运行能力流程」")

    monkeypatch.setattr(flows, "create_run", deny)
    client = _client(monkeypatch)
    resp = client.post("/runs", headers={"Idempotency-Key": "k"},
                       json={"scenario": "ops_content_publish", "input": {}})
    assert resp.status_code == 403
    assert "capability:run" in resp.json()["detail"]


def test_disabled_and_not_allowlisted_map_to_503(monkeypatch):
    def disabled(**kwargs):
        raise flows.FlowError("FLOW_DISABLED", "能力流程自动执行未开启")

    monkeypatch.setattr(flows, "create_run", disabled)
    client = _client(monkeypatch)
    resp = client.post("/runs", headers={"Idempotency-Key": "k"},
                       json={"scenario": "ops_content_publish", "input": {}})
    assert resp.status_code == 503


def test_conflict_and_not_found_mapping(monkeypatch):
    def terminal(run_id, **kwargs):
        raise flows.FlowError("RUN_TERMINAL", "运行已是终态，不可取消")

    monkeypatch.setattr(flows, "cancel_run", terminal)
    client = _client(monkeypatch)
    assert client.post("/runs/1/cancel").status_code == 409

    monkeypatch.setattr(flows, "resume_human_step",
                        lambda run_id, **kw: (_ for _ in ()).throw(
                            flows.FlowError("HUMAN_PENDING", "尚未发布")))
    assert client.post("/runs/1/resume").status_code == 409

    monkeypatch.setattr(flows, "get_run", lambda run_id: None)
    assert client.get("/runs/999").status_code == 404


def test_list_and_detail_return_safe_shapes(monkeypatch):
    monkeypatch.setattr(flows, "list_runs",
                        lambda **kw: {"items": [{"run_id": 1, "status": "waiting_human"}],
                                      "total": 1, "page": 1, "page_size": 20})
    monkeypatch.setattr(flows, "get_run",
                        lambda run_id: {"run_id": 1, "status": "waiting_human",
                                        "steps": [{"step_no": 3, "channel": "human",
                                                   "status": "waiting_human"}],
                                        "events": []})
    client = _client(monkeypatch)
    listing = client.get("/runs?page=1&page_size=20")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    detail = client.get("/runs/1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["steps"][0]["status"] == "waiting_human"
    # 安全 DTO：不回内部自增 id 与正文/凭据
    assert "id" not in body and "input_json" not in body
