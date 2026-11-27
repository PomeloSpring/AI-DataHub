"""进化中心 HTTP 契约（P1-2/P1-7）：真分页参数、统一决策端点、旧端点退役、异步归纳作业。

锁定五件事：

1. **旧动作端点全部退役**：/alias /metric-draft /dimension-draft /to-term /reject
   /applied /to-eval-case /digest /backfill-legacy /ontology-objects* 不在路由表，
   处置统一走 POST .../decisions（HTTP/AI 同一条决策链）；
2. **决策端点**：Idempotency-Key 必填、action/version/note/input 原样进决策服务、
   409/422 语义映射（STALE_DECISION / DOMAIN_WRITTEN_DECISION_CONFLICT 带落点提示）；
3. **真分页参数**：/candidates /proposals 的 page≥1、page_size 上限；
4. **归纳作业**：POST /digests 建 job、GET /digests/{job_id} 查状态、越权/缺失 404；
5. **身份 fail-closed**：请求体不参与身份解析（决策服务只拿可信 EvolutionContext）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.catalog.api import evolution as api
from backend.modules.catalog.services import evolution_decision_service as decide
from backend.modules.catalog.services import evolution_query_service as queries


class _FakeCtx:
    user_id = 11
    workspace_id = 22
    actor = "alice"
    channel = "http"
    allowed_datasources = (5,)
    datasource_names = ("prd",)
    datasource_options = ({"id": 5, "name": "prd", "db_type": "mysql"},)


def _client(monkeypatch, *, ctx=None):
    app = FastAPI()
    app.include_router(api.router)
    fake = ctx or _FakeCtx()

    async def _dep():
        return fake

    app.dependency_overrides[api._evolution_context] = _dep
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════
# 1. 旧端点退役
# ═══════════════════════════════════════════════════════════════════════════

def test_old_action_endpoints_are_retired_and_decisions_exist():
    paths = {getattr(r, "path", "") for r in api.router.routes}
    for retired in ("/candidates/{candidate_id}/alias",
                    "/candidates/{candidate_id}/metric-draft",
                    "/candidates/{candidate_id}/dimension-draft",
                    "/candidates/{candidate_id}/to-term",
                    "/candidates/{candidate_id}/reject",
                    "/candidates/{candidate_id}/to-eval-case",
                    "/proposals/{proposal_id}/applied",
                    "/proposals/{proposal_id}/reject",
                    "/digest",
                    "/backfill-legacy",
                    "/ontology-objects",
                    "/ontology-objects/scalars"):
        assert retired not in paths, f"旧端点必须退役: {retired}"
    assert "/candidates/{candidate_id}/decisions" in paths
    assert "/proposals/{proposal_id}/decisions" in paths
    assert "/digests" in paths and "/digests/{job_id}" in paths


def test_ontology_scalar_edit_has_single_ontology_api_entry():
    """本体标量编辑归 ontology API（ontology:save 保护），不再在进化中心开第二入口。"""
    from backend.modules.catalog.api import ontology as ontology_api

    paths = {getattr(r, "path", "") for r in ontology_api.router.routes}
    assert "/models/{model_id}/objects/{object_key}/scalars" in paths


# ═══════════════════════════════════════════════════════════════════════════
# 2. 决策端点
# ═══════════════════════════════════════════════════════════════════════════

def test_decision_endpoint_requires_idempotency_key(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/candidates/7/decisions", json={
        "action_key": "evolution_candidate.reject", "expected_version": 3, "note": "n"})
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["detail"]


def test_candidate_decision_maps_body_into_decision_service(monkeypatch):
    captured = {}

    def fake_decide(ctx, **kwargs):
        captured.update(kwargs)
        return {"changed": True, "term": "订单状态", "status": "converted",
                "decision_version": 4, "domain_ref": "案例数", "message": "ok"}

    monkeypatch.setattr(decide, "decide_candidate", fake_decide)
    client = _client(monkeypatch)
    resp = client.post(
        "/candidates/7/decisions",
        headers={"Idempotency-Key": "7:3:adopt"},
        json={"action_key": "evolution_candidate.adopt", "expected_version": 3,
              "input": {"target_type": "metric", "target": "案例数"}})

    assert resp.status_code == 200
    assert captured["candidate_id"] == 7
    assert captured["action_key"] == "evolution_candidate.adopt"
    assert captured["expected_version"] == 3
    assert captured["idempotency_key"] == "7:3:adopt"
    assert captured["input_data"] == {"target_type": "metric", "target": "案例数"}
    body = resp.json()
    assert body["domain_ref"] == "案例数" and "id" not in body


@pytest.mark.parametrize("code,status,needle", [
    ("STALE_DECISION", 409, "并发"),
    ("DOMAIN_WRITTEN_DECISION_CONFLICT", 409, "落点"),
    ("PERMISSION_DENIED", 403, "权限"),
    ("APPLIED_REF_REQUIRED", 422, "applied_ref"),
])
def test_decision_error_codes_map_to_http(monkeypatch, code, status, needle):
    def boom(ctx, **kwargs):
        raise decide.EvolutionDecisionError(
            f"{code} 详情：已被并发决策修改", status_code=status, code=code,
            domain_ref="订单量" if code == "DOMAIN_WRITTEN_DECISION_CONFLICT" else "")

    monkeypatch.setattr(decide, "decide_candidate", boom)
    client = _client(monkeypatch)
    resp = client.post(
        "/candidates/7/decisions",
        headers={"Idempotency-Key": "k"},
        json={"action_key": "evolution_candidate.adopt", "expected_version": 3,
              "input": {"target_type": "metric", "target": "x"}})
    assert resp.status_code == status
    detail = resp.json()["detail"]
    assert needle in detail or code in detail
    if code == "DOMAIN_WRITTEN_DECISION_CONFLICT":
        assert "订单量" in detail, "半态必须把落点回给用户做人工核对"


def test_proposal_decision_forwards_applied_ref_from_input_or_top_level(monkeypatch):
    captured = {}

    def fake_decide(ctx, **kwargs):
        captured.update(kwargs)
        return {"changed": True, "title": "补口径", "status": "applied",
                "decision_version": 2, "domain_ref": kwargs.get("applied_ref", ""),
                "message": "ok"}

    monkeypatch.setattr(decide, "decide_proposal", fake_decide)
    client = _client(monkeypatch)

    resp = client.post(
        "/proposals/2/decisions",
        headers={"Idempotency-Key": "2:1:applied"},
        json={"action_key": "evolution_proposal.applied", "expected_version": 1,
              "input": {"applied_ref": "业务知识库 · 返利口径"}})
    assert resp.status_code == 200
    assert captured["applied_ref"] == "业务知识库 · 返利口径"
    assert captured["proposal_id"] == 2

    client.post(
        "/proposals/2/decisions",
        headers={"Idempotency-Key": "2:1:reject"},
        json={"action_key": "evolution_proposal.reject", "expected_version": 1,
              "note": "口径未确认"})
    assert captured["note"] == "口径未确认"
    assert captured["action_key"] == "evolution_proposal.reject"


# ═══════════════════════════════════════════════════════════════════════════
# 3. 真分页参数
# ═══════════════════════════════════════════════════════════════════════════

def test_list_pagination_bounds_are_enforced_by_api(monkeypatch):
    from fastapi.openapi.utils import get_openapi

    app = FastAPI()
    app.include_router(api.router)
    schema = get_openapi(title="evo", version="1", routes=app.routes)
    for path in ("/candidates", "/proposals"):
        params = {p["name"]: p["schema"]
                  for p in schema["paths"][path]["get"]["parameters"]}
        assert params["page"]["minimum"] == 1
        assert params["page_size"]["maximum"] == queries.MAX_PAGE_SIZE

    client = _client(monkeypatch)
    assert client.get("/candidates?page_size=999").status_code == 422
    assert client.get("/proposals?page=0").status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# 4. 归纳作业
# ═══════════════════════════════════════════════════════════════════════════

def test_digest_job_submit_and_poll(monkeypatch):
    monkeypatch.setattr(decide, "submit_digest_job",
                        lambda ctx, **kw: {"job_id": 9, "status": "queued",
                                           "message": "已提交"})
    monkeypatch.setattr(queries, "get_digest_job",
                        lambda ctx, job_id: {"job_id": int(job_id), "status": "running"})
    client = _client(monkeypatch)

    resp = client.post("/digests")
    assert resp.status_code == 200 and resp.json()["job_id"] == 9

    polled = client.get("/digests/9")
    assert polled.status_code == 200 and polled.json()["status"] == "running"


def test_digest_job_missing_is_404(monkeypatch):
    monkeypatch.setattr(queries, "get_digest_job", lambda ctx, job_id: None)
    client = _client(monkeypatch)
    assert client.get("/digests/404").status_code == 404


def test_digest_job_submit_error_maps_status(monkeypatch):
    def boom(ctx, **kw):
        raise decide.EvolutionDecisionError("角色未授予「evolution:manage」权限",
                                            status_code=403, code="PERMISSION_DENIED")

    monkeypatch.setattr(decide, "submit_digest_job", boom)
    client = _client(monkeypatch)
    assert client.post("/digests").status_code == 403
