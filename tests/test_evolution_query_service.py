"""进化中心统一查询服务契约：角色域、真分页、安全 DTO 与资源目录。"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from backend.modules.catalog.services import evolution_query_service as queries
from backend.modules.catalog.services import evolution_service as evo
from backend.modules.catalog.services import knowledge_update_service as kus


def _ctx(*, datasources=("prd",), ids=(5,), workspace_id=22) -> queries.EvolutionContext:
    options = tuple({"id": ds_id, "name": name, "db_type": "mysql"}
                    for name, ds_id in zip(datasources, ids))
    return queries.EvolutionContext(
        user_id=11,
        workspace_id=workspace_id,
        actor="alice",
        channel="http",
        allowed_datasources=tuple(ids),
        datasource_names=tuple(datasources),
        datasource_options=options,
    )


def _candidate_row(**overrides) -> dict:
    row = {
        "id": 7,
        "term": "订单状态",
        "kind": "alias",
        "source": "parse_fail",
        "datasource_name": "prd",
        "target_type": "dimension",
        "target_ref": "状态",
        "classify_reason": "存在近似候选",
        "evidence": {},
        "hit_count": 3,
        "status": "pending",
        "converted_ref": "",
        "converted_kind": "",
        "decided_by": "",
        "decided_at": None,
        "decision_note": "",
        "decision_version": 2,
        "last_checked_at": None,
        "created_at": None,
        "updated_at": None,
    }
    row.update(overrides)
    return row


def test_context_uses_role_service_as_only_datasource_source(monkeypatch):
    from backend.core.role_service import role_service

    role_calls = []

    def allowed(user_id, workspace_id):
        role_calls.append((user_id, workspace_id))
        return [5, 5, 0]

    monkeypatch.setattr(role_service, "get_user_allowed_datasources", allowed)
    monkeypatch.setattr(queries, "_q", lambda sql, params=(): [
        {"id": 5, "name": "prd", "db_type": "mysql"},
    ])

    ctx = queries.EvolutionContext.create(
        user_id=11, workspace_id=22, actor="alice", channel="http")

    assert role_calls == [(11, 22)]
    assert ctx.allowed_datasources == (5,)
    assert ctx.datasource_id_map == {"prd": 5}
    assert queries.datasource_options(ctx) == [{"name": "prd", "db_type": "mysql"}]
    assert "allowed_datasources" not in inspect.signature(
        queries.EvolutionContext.create).parameters


@pytest.mark.parametrize("kwargs,code", [
    ({"user_id": 0, "workspace_id": 22, "channel": "http"}, "NO_IDENTITY"),
    ({"user_id": 11, "workspace_id": 0, "channel": "http"}, "WORKSPACE_REQUIRED"),
    ({"user_id": 11, "workspace_id": 22, "channel": "body"}, "QUERY_INVALID"),
])
def test_context_rejects_untrusted_identity_workspace_or_channel(monkeypatch, kwargs, code):
    from backend.core.role_service import role_service

    monkeypatch.setattr(role_service, "get_user_allowed_datasources", lambda *_: [])
    with pytest.raises(queries.EvolutionQueryError) as exc:
        queries.EvolutionContext.create(**kwargs)
    assert exc.value.code == code


def test_empty_authorization_is_closed_and_unknown_datasource_is_rejected(monkeypatch):
    ctx = _ctx(datasources=(), ids=())
    calls = []

    def fake_q(sql, params=()):
        calls.append((sql, params))
        return [{"total": 0}] if "COUNT(*)" in sql else []

    monkeypatch.setattr(queries, "_q", fake_q)
    out = queries.list_candidates(ctx)
    assert out == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert all("1 = 0" in sql for sql, _ in calls)

    with pytest.raises(queries.EvolutionQueryError) as exc:
        queries.list_candidates(_ctx(), datasource_name="secret")
    assert exc.value.code == "DATASOURCE_FORBIDDEN"


def test_candidate_count_and_items_share_filters_and_server_pagination(monkeypatch):
    calls = []
    rows = [
        _candidate_row(),
        _candidate_row(id=8, term="已收编", status="converted"),
    ]

    def fake_q(sql, params=()):
        calls.append((sql, tuple(params)))
        if "COUNT(*) AS total" in sql:
            return [{"total": 73}]
        return rows

    suggestions = []
    monkeypatch.setattr(queries, "_q", fake_q)
    monkeypatch.setattr(evo, "suggest_action", lambda dto: suggestions.append(dto["id"]) or {"tier": "observe"})

    out = queries.list_candidates(
        _ctx(), status="pending", kind="alias", queue="build", source="parse_fail",
        datasource_name="prd", q="订单", page=2, page_size=999)

    assert out["total"] == 73 and len(out["items"]) == 2
    assert out["page"] == 2 and out["page_size"] == 50
    assert suggestions == [7], "历史/已处置候选不得再附带处置建议"
    assert "suggestion" not in out["items"][1]
    count_sql, count_params = calls[0]
    item_sql, item_params = calls[1]
    assert "COUNT(*)" in count_sql
    assert "LIMIT %s OFFSET %s" in item_sql
    assert item_params[-2:] == (50, 50)
    assert count_params == item_params[:-2]
    assert count_params == ("prd", "pending", "alias", "build", "parse_fail", "prd",
                            "%订单%", "%订单%", "%订单%")
    order = item_sql.split("ORDER BY", 1)[1].split(" LIMIT", 1)[0]
    assert "hit_count DESC" in order and order.rstrip().endswith("id ASC")


@pytest.mark.parametrize("page,page_size", [(0, 20), (1, 0), ("bad", 20), (1, "bad")])
def test_pagination_rejects_invalid_values(monkeypatch, page, page_size):
    monkeypatch.setattr(queries, "_q", lambda *_: pytest.fail("非法分页不得访问数据库"))
    with pytest.raises(queries.EvolutionQueryError) as exc:
        queries.list_candidates(_ctx(), page=page, page_size=page_size)
    assert exc.value.status_code == 400


def test_candidate_dto_and_ai_projection_strip_physical_and_internal_fields():
    row = _candidate_row(
        target_type="object",
        target_ref="case_internal",
        target_table="pc_case",
        physical_table="pc_case",
        datasource_id=5,
        evidence={
            "question": "订单状态有哪些？",
            "candidates": ["订单状态", 55],
            "target_table": "pc_case",
            "column_name": "status",
            "datasource_id": 5,
            "host": "10.0.0.1",
        },
    )
    dto = queries._candidate_dto(row)
    assert dto["evidence"] == {
        "question": "订单状态有哪些？",
        "candidates": ["订单状态"],
    }
    for key in ("target_table", "physical_table", "datasource_id", "column_name", "host"):
        assert key not in dto
        assert key not in dto["evidence"]

    dto["suggestion"] = {
        "suggested_refs": ["case_internal"],
        "prefilled": {"target_type": "object", "target_ref": "case_internal"},
    }
    ai = queries.candidate_for_ai(dto)
    for key in ("id", "decision_version", "expected_version", "target_ref"):
        assert key not in ai
    assert ai["suggestion"]["suggested_refs"] == []
    assert "target_ref" not in ai["suggestion"]["prefilled"]


def test_invalid_evidence_is_marked_instead_of_silently_dropped():
    dto = queries._candidate_dto(_candidate_row(evidence="not-json"))
    assert dto["evidence"] == {"evidence_error": "invalid_json"}


def test_proposals_paginate_without_list_body_and_detail_keeps_content(monkeypatch):
    calls = []
    row = {
        "id": 12,
        "source": "chat",
        "target_type": "kb_doc",
        "title": "返利口径说明",
        "rationale": "需要补充",
        "target_hint": "业务知识库",
        "datasource_name": "prd",
        "status": "pending",
        "applied_ref": "",
        "decided_by": "",
        "decided_at": None,
        "decided_note": "",
        "decision_version": 3,
        "origin_conversation_id": 99,
        "origin_message_uuid": "m-1",
        "created_at": None,
        "updated_at": None,
        "content_preview": "短摘要",
        "content": "完整正文" * 100,
        "physical_table": "secret_table",
    }

    def fake_q(sql, params=()):
        calls.append((sql, tuple(params)))
        if "COUNT(*)" in sql:
            return [{"total": 21}]
        return [row]

    monkeypatch.setattr(queries, "_q", fake_q)
    out = queries.list_proposals(_ctx(), q="返利", page=2, page_size=5)
    assert out["total"] == 21 and out["page_size"] == 5
    assert out["items"][0]["content_preview"] == "短摘要"
    assert "content" not in out["items"][0]
    assert "physical_table" not in out["items"][0]
    assert calls[1][1][-2:] == (5, 5)
    assert "created_at DESC, id DESC" in calls[1][0]

    detail = queries.get_proposal(_ctx(), 12)
    assert detail["content"] == "完整正文" * 100
    ai = queries.proposal_for_ai(detail)
    assert "id" not in ai and "expected_version" not in ai
    assert "origin_conversation_id" not in ai


def test_object_options_apply_model_scope_and_object_site_allowlist(monkeypatch):
    calls = []
    model = {
        "id": 3,
        "name": "交易本体",
        "kind": "business",
        "datasource_id": 0,
        "deploy_sites": json.dumps([{"site_code": "cn", "datasource": "prd"}]),
        "object_site_rules": json.dumps({"case": ["cn"], "ticket": ["us"], "empty": []}),
    }
    objects = [
        {"object_key": "case", "display_name": "案例", "model_id": 3},
        {"object_key": "ticket", "display_name": "工单", "model_id": 3},
        {"object_key": "empty", "display_name": "空对象", "model_id": 3},
        {"object_key": "general", "display_name": "通用对象", "model_id": 3},
    ]

    def fake_q(sql, params=()):
        calls.append((sql, tuple(params)))
        return [model] if "FROM adh_ontology_models" in sql else objects

    monkeypatch.setattr(queries, "_q", fake_q)
    out = queries.object_options(_ctx(), "prd")
    assert [r["key"] for r in out] == ["case", "general"]
    assert out[0]["display_name"] == "案例"
    assert "datasource_id" not in out[0] and "deploy_sites" not in out[0]
    assert "COALESCE(kind, '') <> 'system'" in calls[0][0]


def test_property_options_require_authorized_object_and_return_business_names(monkeypatch):
    monkeypatch.setattr(queries, "object_options", lambda ctx, name: [{"key": "case"}])
    captured = []

    def fake_properties(**kwargs):
        captured.append(kwargs)
        return [{"property": "案例状态"}]

    monkeypatch.setattr(evo, "property_options", fake_properties)
    assert queries.property_options(
        _ctx(), bound_object_key="case", datasource_name="prd") == [{"property": "案例状态"}]
    assert captured == [{"bound_object_key": "case", "datasource_name": "prd"}]

    with pytest.raises(queries.EvolutionQueryError) as exc:
        queries.property_options(
            _ctx(), bound_object_key="other", datasource_name="prd")
    assert exc.value.code == "OBJECT_FORBIDDEN"


def test_target_options_select_business_names_and_authorized_datasource(monkeypatch):
    calls = []

    def fake_q(sql, params=()):
        calls.append((sql, tuple(params)))
        return [{"name": "案例数量"}, {"name": "案例状态"}]

    monkeypatch.setattr(queries, "_q", fake_q)
    out = queries.target_options(_ctx(), target_type="metric", datasource_name="prd")
    assert out == [
        {"key": "案例数量", "display_name": "案例数量"},
        {"key": "案例状态", "display_name": "案例状态"},
    ]
    assert calls[0][0].startswith("SELECT name FROM adh_metrics")
    assert "datasource_id = %s OR datasource_id = 0" in calls[0][0]
    assert calls[0][1] == (5,)


def test_digest_job_invalid_result_is_explicit_failure(monkeypatch):
    monkeypatch.setattr(queries, "_q", lambda *_: [{
        "id": 9,
        "status": "failed",
        "actor": "alice",
        "created_at": None,
        "started_at": None,
        "finished_at": None,
        "error_code": "DIGEST_FAILED",
        "error_message": "归纳失败",
        "result_json": "not-json",
    }])
    with pytest.raises(queries.EvolutionQueryError) as exc:
        queries.get_digest_job(_ctx(), 9)
    assert exc.value.code == "DIGEST_JOB_INVALID"
    assert exc.value.status_code == 500


def test_overview_shares_role_scope_and_includes_proposal_counts(monkeypatch):
    calls = []

    def fake_q(sql, params=()):
        calls.append((sql, tuple(params)))
        if "FROM adh_evolution_candidates" in sql and "GROUP BY kind" in sql:
            return [{"kind": "alias", "n": 2}]
        if "FROM adh_evolution_candidates" in sql and "GROUP BY source" in sql:
            return [{"source": "parse_fail", "n": 2}]
        if "FROM adh_evolution_candidates" in sql and "GROUP BY status" in sql:
            return [{"status": "converted", "n": 1}]
        if "FROM adh_knowledge_update_proposals" in sql:
            return [{"status": "pending", "n": 4}, {"status": "applied", "n": 2}]
        if "FROM adh_message_feedback" in sql:
            return [{"up": 3, "down": 1, "pending_negative": 1}]
        if "FROM adh_evolution_digest_jobs" in sql:
            return [{
                "id": 6, "status": "succeeded", "actor": "alice",
                "created_at": None, "started_at": None, "finished_at": None,
                "error_code": "", "error_message": "",
                "result_json": json.dumps({"created": 2}),
            }]
        pytest.fail(f"未预期的查询: {sql}")

    monkeypatch.setattr(queries, "_q", fake_q)
    out = queries.overview(_ctx())
    assert out["pending_by_kind"] == {"alias": 2}
    assert out["pending_by_source"] == {"parse_fail": 2}
    assert out["outcome"] == {"converted": 1}
    assert out["proposal_counts"]["pending"] == 4
    assert out["proposal_counts"]["applied"] == 2
    assert set(out["proposal_counts"]) == set(kus.STATUSES)
    assert out["recent_digest_job"]["requested_by"] == "alice"
    assert out["recent_digest_job"]["result"] == {"created": 2}
    scoped = [(sql, params) for sql, params in calls
              if "adh_evolution_candidates" in sql or "adh_knowledge_update_proposals" in sql]
    assert scoped and all(params == ("prd",) for _, params in scoped)


def test_evolution_api_pagination_parameters_are_bounded():
    from fastapi import FastAPI
    from fastapi.openapi.utils import get_openapi

    from backend.modules.catalog.api import evolution as api

    app = FastAPI()
    app.include_router(api.router)
    schema = get_openapi(title="evolution-test", version="1", routes=app.routes)
    for path in ("/candidates", "/proposals"):
        params = {p["name"]: p["schema"]
                  for p in schema["paths"][path]["get"]["parameters"]}
        assert params["page"]["minimum"] == 1
        assert params["page_size"]["maximum"] == queries.MAX_PAGE_SIZE


def test_refactor_migration_is_idempotent_and_has_no_hardcoded_database():
    sql = (Path(__file__).resolve().parents[1]
           / "docker/mysql/evolution_center_refactor_migration.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS adh_evolution_decisions" in sql
    assert "CREATE TABLE IF NOT EXISTS adh_evolution_digest_jobs" in sql
    assert "UNIQUE KEY uk_evo_decision_idem" in sql
    assert "decision_version" in sql and "decision_note" in sql
    assert "DROP PROCEDURE IF EXISTS" in sql
    assert not any(line.strip().upper().startswith("USE ") for line in sql.splitlines())
