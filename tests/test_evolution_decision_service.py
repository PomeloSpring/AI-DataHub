"""进化中心统一决策服务契约（P1-4）：状态机、版本仲裁、幂等账本与领域写入协调。

锁定六件事：

1. **双闸**：能力面 evolution:manage + 动作面域码，少一道即拒，拒绝原因可解释；
2. **状态机只前进一步**：仅 pending + 匹配版本可处置，条件 UPDATE 仲裁，并发单赢，
   冲突 409 STALE_DECISION 不覆盖既有结论；
3. **幂等账本**：同键同请求重放返回首次结果、不重复写领域资产；同键异请求显式冲突；
   校验/预检失败不入账（允许同键修正后重试）；
4. **领域写入纪律**：别名收编走 metrics_service.update_*（不直写 UPDATE 字典表）、
   转术语走 term_service.create_term（禁止裸 INSERT）、草稿走 metrics_service.create_*
   保持 is_active=0、对象别名走 ontology_service.merge_object_scalars 只落草案；
5. **显式半态**：领域写成功但候选状态冲突 → DOMAIN_WRITTEN_DECISION_CONFLICT + 落点，
   不回滚成“什么都没发生”；
6. **安全投影**：结果不含内部 id、版本与物理列名；applied_ref/note 必填同口径。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.modules.catalog.services import evolution_decision_service as decide
from backend.modules.catalog.services import evolution_query_service as queries


# ── 可断言的假连接：按 SQL 动词/表名路由到内存状态 ──────────────────────────

class _FakeCursor:
    def __init__(self, state):
        self.state = state
        self.rowcount = 0
        self.lastrowid = 0
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        params = tuple(params or ())
        self.state["calls"].append((s, params))
        self._rows = []
        self.rowcount = 0
        if s.startswith("INSERT INTO adh_evolution_decisions"):
            self.state["ledger_inserts"].append(params)
            self.rowcount = 1
            return
        if s.startswith("INSERT INTO adh_evolution_digest_jobs"):
            self.state["job_inserts"].append(params)
            self.rowcount = 1
            self.lastrowid = 7
            return
        if s.startswith("UPDATE adh_evolution_candidates"):
            self.state["candidate_updates"].append((s, params))
            self.rowcount = int(self.state.get("affected", 1))
            return
        if s.startswith("UPDATE adh_knowledge_update_proposals"):
            self.state["proposal_updates"].append((s, params))
            self.rowcount = int(self.state.get("affected", 1))
            return
        if s.startswith("UPDATE adh_evolution_digest_jobs"):
            self.state["job_updates"].append((s, params))
            self.rowcount = 1
            return
        if "FROM adh_evolution_decisions" in s:
            self._rows = list(self.state.get("ledger_rows") or [])
            return
        if "FROM adh_evolution_candidates" in s:
            row = self.state.get("candidate_row")
            self._rows = [dict(row)] if row else []
            return
        if "FROM adh_knowledge_update_proposals" in s:
            row = self.state.get("proposal_row")
            self._rows = [dict(row)] if row else []
            return
        if "FROM adh_evolution_digest_jobs" in s:
            self._rows = list(self.state.get("job_rows") or [])
            return
        if "SELECT id FROM adh_metrics" in s or "SELECT id FROM adh_dimensions" in s:
            self._rows = list(self.state.get("dict_rows") or [])
            return
        if "SELECT aliases FROM adh_metrics" in s or "SELECT aliases FROM adh_dimensions" in s:
            self._rows = list(self.state.get("dict_alias_rows") or [])
            return
        raise AssertionError(f"未预期的 SQL: {s}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, state):
        self.state = state
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self.state)

    def begin(self):
        self.state["begins"] += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _install_db(monkeypatch, **overrides) -> dict:
    state = {"calls": [], "ledger_inserts": [], "job_inserts": [],
             "candidate_updates": [], "proposal_updates": [], "job_updates": [],
             "begins": 0, "affected": 1, "ledger_rows": [], "job_rows": [],
             "candidate_row": None, "proposal_row": None,
             "dict_rows": [], "dict_alias_rows": []}
    state.update(overrides)
    monkeypatch.setattr(decide, "_conn", lambda: _FakeConn(state))
    monkeypatch.setattr(decide, "_audit", lambda *a, **k: None)
    return state


def _ctx() -> queries.EvolutionContext:
    return queries.EvolutionContext(
        user_id=11, workspace_id=22, actor="alice", channel="http",
        allowed_datasources=(5,), datasource_names=("prd",),
        datasource_options=({"id": 5, "name": "prd", "db_type": "mysql"},))


def _allow_perms(monkeypatch, deny_code: str = ""):
    calls = []

    def _require(user_id, workspace_id, code, label=""):
        calls.append(code)
        if code == deny_code:
            raise PermissionError(f"角色未授予「{code}」权限")

    monkeypatch.setattr("backend.core.perm_link.require_write_perm", _require)
    return calls


def _candidate_row(**overrides) -> dict:
    row = {"id": 7, "term": "订单状态", "kind": "alias", "source": "parse_fail",
           "datasource_name": "prd", "target_type": "dimension", "target_ref": "状态",
           "classify_reason": "r", "evidence": {}, "hit_count": 2, "status": "pending",
           "converted_ref": "", "converted_kind": "", "decided_by": "", "decided_at": None,
           "decision_note": "", "decision_version": 3, "last_checked_at": None,
           "created_at": None, "updated_at": None}
    row.update(overrides)
    return row


# ═══════════════════════════════════════════════════════════════════════════
# 1. 入参与双闸
# ═══════════════════════════════════════════════════════════════════════════

def test_unknown_action_and_missing_idempotency_key_are_explicit():
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(_ctx(), candidate_id=1, action_key="evolution_candidate.??",
                                expected_version=1, idempotency_key="k")
    assert exc.value.code == "ACTION_NOT_FOUND" and exc.value.status_code == 400

    with pytest.raises(decide.EvolutionDecisionError) as exc2:
        decide.decide_candidate(_ctx(), candidate_id=1,
                                action_key="evolution_candidate.reject",
                                expected_version=1, note="n", idempotency_key="")
    assert exc2.value.code == "IDEMPOTENCY_KEY_REQUIRED"


def test_dual_gate_rejects_when_either_perm_missing(monkeypatch):
    state = _install_db(monkeypatch, candidate_row=_candidate_row())
    # 能力面缺 → 拒，且不触达库
    calls = _allow_perms(monkeypatch, deny_code="evolution:manage")
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
            expected_version=3, idempotency_key="k1")
    assert exc.value.code == "PERMISSION_DENIED" and exc.value.status_code == 403
    assert not state["calls"], "权限被拒时不得触达数据库"

    # 能力面过、域码缺 → 也拒（双闸不是单闸）
    _allow_perms(monkeypatch, deny_code="ontology:save")
    with pytest.raises(decide.EvolutionDecisionError) as exc2:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
            expected_version=3, idempotency_key="k2")
    assert exc2.value.code == "PERMISSION_DENIED"
    assert calls == ["evolution:manage"]


@pytest.mark.parametrize("action,kwargs,needle", [
    ("evolution_candidate.reject", {"note": ""}, "必须说明理由"),
    ("evolution_candidate.adopt", {"input_data": {"target_type": "table", "target": "x"}},
     "target_type"),
    ("evolution_candidate.adopt", {"input_data": {"target_type": "metric", "target": ""}},
     "必须指明收编目标"),
    ("evolution_metric.create", {"input_data": {}}, "必须指明归属对象"),
    ("evolution_dimension.create",
     {"input_data": {"object": "case"}}, "必须指明对象的业务属性名"),
])
def test_candidate_input_validation_is_loud(action, kwargs, needle, monkeypatch):
    _install_db(monkeypatch)
    _allow_perms(monkeypatch)
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(_ctx(), candidate_id=7, action_key=action,
                                expected_version=3, idempotency_key="k", **kwargs)
    assert needle in str(exc.value)
    assert exc.value.status_code == 422


def test_proposal_applied_requires_ref_and_reject_requires_note(monkeypatch):
    _install_db(monkeypatch)
    _allow_perms(monkeypatch)
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_proposal(_ctx(), proposal_id=2, action_key="evolution_proposal.applied",
                               expected_version=1, applied_ref="", idempotency_key="k")
    assert exc.value.code == "APPLIED_REF_REQUIRED" and exc.value.status_code == 422

    with pytest.raises(decide.EvolutionDecisionError) as exc2:
        decide.decide_proposal(_ctx(), proposal_id=2, action_key="evolution_proposal.reject",
                               expected_version=1, note="", idempotency_key="k")
    assert "必须说明理由" in str(exc2.value)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 状态机与版本仲裁
# ═══════════════════════════════════════════════════════════════════════════

def test_stale_version_conflicts_before_domain_write(monkeypatch):
    state = _install_db(monkeypatch, candidate_row=_candidate_row(decision_version=5))
    _allow_perms(monkeypatch)
    created = []

    monkeypatch.setitem(decide._DOMAIN_WRITERS, "evolution_candidate.to_term",
                        lambda *a, **k: created.append(1) or {"domain_kind": "term",
                                                              "domain_ref": "x"})
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
            expected_version=3, idempotency_key="k")
    assert exc.value.code == "STALE_DECISION" and exc.value.status_code == 409
    assert not created, "版本冲突时不得做领域写入"
    assert not state["candidate_updates"], "版本冲突不得改状态"
    conflict = state["ledger_inserts"]
    assert conflict and "STALE_DECISION" in conflict[0]
    assert "conflict" in conflict[0]


def test_non_pending_candidate_cannot_be_redecided(monkeypatch):
    _install_db(monkeypatch, candidate_row=_candidate_row(status="converted",
                                                          decision_version=3))
    _allow_perms(monkeypatch)
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
            expected_version=3, idempotency_key="k")
    assert exc.value.code == "STALE_DECISION"
    assert "只有待处置" in str(exc.value)


def test_atomic_decision_updates_with_version_and_writes_ledger(monkeypatch):
    state = _install_db(monkeypatch, candidate_row=_candidate_row())
    perm_calls = _allow_perms(monkeypatch)
    monkeypatch.setitem(decide._DOMAIN_WRITERS, "evolution_candidate.to_term",
                        lambda *a, **k: {"domain_kind": "term", "domain_ref": "订单状态",
                                         "created": True, "message": "已转术语"})

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
        expected_version=3, note="", input_data={"description": "d"},
        idempotency_key="subject:3:to_term")

    assert result["changed"] is True and result["status"] == "converted"
    assert result["decision_version"] == 4 and result["domain_ref"] == "订单状态"
    assert perm_calls == ["evolution:manage", "ontology:save"]
    sql, params = state["candidate_updates"][0]
    assert "decision_version = decision_version + 1" in sql
    assert "AND status = 'pending' AND decision_version = %s" in sql
    assert 3 in params
    assert len(state["ledger_inserts"]) == 1 and "succeeded" in state["ledger_inserts"][0]
    assert "id" not in result, "结果不得下发内部 id"


def test_reject_records_decision_note(monkeypatch):
    state = _install_db(monkeypatch, candidate_row=_candidate_row())
    _allow_perms(monkeypatch)
    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.reject",
        expected_version=3, note="不是真实业务叫法", idempotency_key="k")
    assert result["status"] == "rejected"
    sql, params = state["candidate_updates"][0]
    assert "decision_note" in sql and "不是真实业务叫法" in params


def test_domain_written_decision_conflict_is_explicit_half_state(monkeypatch):
    """跨服务草稿：领域写成功但状态被并发改掉 → 显式半态 + 落点，不假回滚。"""
    state = _install_db(monkeypatch,
                        candidate_row=_candidate_row(decision_version=99),
                        affected=0)
    _allow_perms(monkeypatch)
    drafted = []
    monkeypatch.setitem(decide._DOMAIN_WRITERS, "evolution_metric.create",
                        lambda *a, **k: drafted.append(1) or {
                            "domain_kind": "metric_draft", "domain_ref": "订单量",
                            "is_active": 0, "needs_completion": ["formula"],
                            "message": "草稿已建"})

    # 阶段一预检会发现版本不匹配（decision_version=99 ≠ 3）→ 先走 STALE；
    # 构造“预检通过、终检冲突”的真实半态：第一次读到 3，锁内读到 99
    reads = {"n": 0}

    def _row(conn, ctx, cid, for_update=True):
        reads["n"] += 1
        if for_update and reads["n"] >= 2:
            return _candidate_row(decision_version=99)
        return _candidate_row(decision_version=3)

    monkeypatch.setattr(queries, "candidate_row_for_decision", _row)

    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_metric.create",
            expected_version=3,
            input_data={"object": "案例", "formula": "COUNT(1)"},
            idempotency_key="k")
    assert exc.value.code == "DOMAIN_WRITTEN_DECISION_CONFLICT"
    assert exc.value.status_code == 409 and exc.value.domain_ref == "订单量"
    assert drafted, "领域写入已发生，不得假装什么都没发生"
    assert "人工核对" in str(exc.value)
    conflict = [p for p in state["ledger_inserts"] if "DOMAIN_WRITTEN_DECISION_CONFLICT" in p]
    assert conflict, "半态必须入账本供人工核对"
    assert "订单量" in json.dumps(conflict, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════
# 3. 幂等账本
# ═══════════════════════════════════════════════════════════════════════════

def _ledger_row(action="evolution_candidate.to_term", version=3, status="succeeded",
                result=None, req_hash=""):
    raw = json.dumps(result or {"changed": True, "status": "converted"},
                     ensure_ascii=False)
    return (7, action, req_hash, version, status, "", "", raw)


def test_idempotent_replay_returns_first_result_without_second_domain_write(monkeypatch):
    req_hash = decide.request_hash("evolution_candidate.to_term", 3, "", {"description": "d"})
    state = _install_db(
        monkeypatch, candidate_row=_candidate_row(),
        ledger_rows=[dict(zip(
            ("id", "action_key", "request_hash", "expected_version", "result_status",
             "domain_ref", "error_code", "result_json"),
            _ledger_row(req_hash=req_hash, result={"changed": True, "status": "converted",
                                                   "domain_ref": "订单状态"})))])
    _allow_perms(monkeypatch)
    domain_calls = []
    monkeypatch.setitem(decide._DOMAIN_WRITERS, "evolution_candidate.to_term",
                        lambda *a, **k: domain_calls.append(1) or {})

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
        expected_version=3, input_data={"description": "d"}, idempotency_key="same-key")

    assert result["replayed"] is True and result["domain_ref"] == "订单状态"
    assert not domain_calls, "重放不得重复写领域资产"
    assert not state["candidate_updates"], "重放不得再改状态"
    assert not state["ledger_inserts"], "重放不再入账"


def test_same_key_different_request_is_explicit_conflict(monkeypatch):
    req_hash = decide.request_hash("evolution_candidate.to_term", 3, "", {})
    state = _install_db(
        monkeypatch, candidate_row=_candidate_row(),
        ledger_rows=[dict(zip(
            ("id", "action_key", "request_hash", "expected_version", "result_status",
             "domain_ref", "error_code", "result_json"),
            _ledger_row(req_hash=req_hash)))])
    _allow_perms(monkeypatch)
    monkeypatch.setitem(decide._DOMAIN_WRITERS, "evolution_candidate.to_term",
                        lambda *a, **k: {})

    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
            expected_version=3, input_data={"description": "改了内容"},
            idempotency_key="same-key")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT" and exc.value.status_code == 409
    assert not state["candidate_updates"]


def test_conflicted_first_result_replays_same_conflict(monkeypatch):
    req_hash = decide.request_hash("evolution_candidate.reject", 3, "n", {})
    row = dict(zip(
        ("id", "action_key", "request_hash", "expected_version", "result_status",
         "domain_ref", "error_code", "result_json"),
        (7, "evolution_candidate.reject", req_hash, 3, "conflict", "",
         "STALE_DECISION", json.dumps({"message": "已被并发决策修改"}))))
    state = _install_db(monkeypatch, candidate_row=_candidate_row(),
                        ledger_rows=[row])
    _allow_perms(monkeypatch)
    with pytest.raises(decide.EvolutionDecisionError) as exc:
        decide.decide_candidate(
            _ctx(), candidate_id=7, action_key="evolution_candidate.reject",
            expected_version=3, note="n", idempotency_key="same-key")
    assert exc.value.code == "STALE_DECISION"
    assert "并发" in str(exc.value)
    assert not state["ledger_inserts"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. 领域写入纪律（P1-5）
# ═══════════════════════════════════════════════════════════════════════════

def test_dict_alias_adoption_uses_metrics_service_not_raw_update(monkeypatch):
    state = _install_db(
        monkeypatch, candidate_row=_candidate_row(),
        dict_rows=[{"id": 42}],
        dict_alias_rows=[{"aliases": json.dumps(["旧别名"])}])
    _allow_perms(monkeypatch)
    captured = {}
    from backend.modules.catalog.services import metrics_service

    monkeypatch.setattr(queries, "target_options",
                        lambda ctx, **kw: [{"key": "案例数", "display_name": "案例数"}])

    def fake_update(metric_id, data, connection=None):
        captured.update({"id": metric_id, "data": data, "connection": connection})
        return True

    monkeypatch.setattr(metrics_service, "update_metric", fake_update)
    monkeypatch.setattr(metrics_service, "_sync_kb_after_dict_write", lambda ds: None)

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.adopt",
        expected_version=3,
        input_data={"target_type": "metric", "target": "案例数"},
        idempotency_key="k")

    assert result["domain_ref"] == "案例数"
    assert captured["id"] == 42
    assert captured["data"]["aliases"] == ["旧别名", "订单状态"], "别名取并集，不删既有"
    assert captured["connection"] is not None, "字典/候选必须同事务（atomic 路径）"
    writes = [c for c in state["calls"] if "UPDATE adh_metrics" in c[0]]
    assert not writes, "不得直写 UPDATE 字典表，必须走 metrics_service"


def test_to_term_uses_term_service_with_datasource_bucket(monkeypatch):
    state = _install_db(monkeypatch, candidate_row=_candidate_row(datasource_name="prd"))
    _allow_perms(monkeypatch)
    captured = {}
    from backend.modules.catalog.services import term_service as term_svc

    def fake_create(data, connection=None, return_existing=False):
        captured.update({"data": data, "connection": connection,
                         "return_existing": return_existing})
        return {"success": True, "id": 1, "created": True}

    monkeypatch.setattr(term_svc.TermService, "create_term", staticmethod(fake_create))
    monkeypatch.setattr(term_svc, "_clear_term_cache", lambda ds: None)

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.to_term",
        expected_version=3, input_data={"description": "黑话"}, idempotency_key="k")

    assert result["created"] is True
    assert captured["data"]["term_cn"] == "订单状态"
    assert captured["data"]["datasource_id"] == 5, "术语必须按数据源分桶"
    assert captured["return_existing"] is True, "同源同词不得重复建"
    inserts = [c for c in state["calls"] if "INSERT INTO adh_business_terms" in c[0]]
    assert not inserts, "禁止裸 INSERT 术语表"


def test_metric_draft_keeps_inactive_and_hides_physical_fields(monkeypatch):
    _install_db(monkeypatch,
                candidate_row=_candidate_row(kind="new_metric", datasource_name="prd"))
    _allow_perms(monkeypatch)
    captured = {}
    from backend.modules.catalog.services import metrics_service

    def fake_create(data, connection=None):
        captured.update(data)
        return {"id": 9}

    monkeypatch.setattr(metrics_service, "create_metric", fake_create)
    monkeypatch.setattr(decide, "_resolve_object_key",
                        lambda ctx, row, want: {"key": "case", "display_name": "案例"})

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_metric.create",
        expected_version=3,
        input_data={"object": "案例", "agg_type": "SUM"}, idempotency_key="k")

    assert captured["is_active"] == 0, "草稿不得直接启用"
    assert captured["bound_object_key"] == "case"
    assert result["domain_ref"] == "订单状态"
    assert result["needs_completion"] == ["formula"], "草稿要说清还缺什么"
    for key in ("id", "target_table", "target_column", "datasource_id"):
        assert key not in result, f"物理/内部字段不得出现在决策结果里: {key}"


def test_dimension_draft_maps_business_property_and_hides_column(monkeypatch):
    _install_db(monkeypatch,
                candidate_row=_candidate_row(kind="new_dimension", datasource_name="prd"))
    _allow_perms(monkeypatch)
    captured = {}
    from backend.modules.catalog.services import metrics_service

    def fake_create(data, connection=None):
        captured.update(data)
        return {"id": 9}

    monkeypatch.setattr(metrics_service, "create_dimension", fake_create)
    monkeypatch.setattr(decide, "_resolve_object_key",
                        lambda ctx, row, want: {"key": "case", "display_name": "案例"})
    monkeypatch.setattr(decide, "_resolve_property_name",
                        lambda ctx, row, key, want: "客户等级")
    from backend.modules.catalog.services import evolution_service as evo
    monkeypatch.setattr(evo, "resolve_property_column",
                        lambda **kw: {"target_table": "pc_case",
                                      "target_column": "customer_level",
                                      "datasource_id": 5})

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_dimension.create",
        expected_version=3,
        input_data={"object": "案例", "property": "客户等级"}, idempotency_key="k")

    assert captured["target_column"] == "customer_level", "物理列由服务端映射"
    assert captured["is_active"] == 0
    assert result["property"] == "客户等级"
    assert "customer_level" not in json.dumps(result, ensure_ascii=False)


def test_adopt_missing_dict_target_guides_to_draft_action(monkeypatch):
    """目标字典行不存在：不报错不静默，回「建草稿」指引，且不动状态与账本。"""
    state = _install_db(monkeypatch, candidate_row=_candidate_row(),
                        dict_rows=[])
    _allow_perms(monkeypatch)
    monkeypatch.setattr(queries, "target_options",
                        lambda ctx, **kw: [{"key": "不存在的指标", "display_name": "不存在的指标"}])

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.adopt",
        expected_version=3,
        input_data={"target_type": "metric", "target": "不存在的指标"},
        idempotency_key="k")

    assert result["changed"] is False and result["reason"] == "target_missing"
    assert result["next_action"] == "new_metric"
    assert not state["candidate_updates"] and not state["ledger_inserts"]


def test_object_alias_adoption_is_draft_only_via_ontology_service(monkeypatch):
    _install_db(monkeypatch,
                candidate_row=_candidate_row(kind="alias", datasource_name="prd",
                                             target_type="object"))
    _allow_perms(monkeypatch)
    captured = {}
    from backend.modules.catalog.services import ontology_service

    def fake_merge(model_id, object_key, patch, created_by=""):
        captured.update({"model_id": model_id, "object_key": object_key,
                         "patch": patch, "created_by": created_by})
        return {"mode": "updated", "changed_fields": ["aliases"]}

    monkeypatch.setattr(ontology_service, "merge_object_scalars", fake_merge)
    monkeypatch.setattr(decide, "_resolve_target_ref",
                        lambda ctx, row, ttype, target: "case")
    monkeypatch.setattr(decide, "_resolve_object_key",
                        lambda ctx, row, want: {"key": "case", "model_id": 3,
                                                "display_name": "案例"})

    result = decide.decide_candidate(
        _ctx(), candidate_id=7, action_key="evolution_candidate.adopt",
        expected_version=3,
        input_data={"target_type": "object", "target": "案例"}, idempotency_key="k")

    assert captured["patch"] == {"aliases": ["订单状态"]}, "别名并集由服务层合并"
    assert captured["created_by"] == "alice"
    assert result["domain_kind"] == "object_alias" and result["domain_ref"] == "case"
    assert "草案" in result["message"], "对象别名只落草案，发布是独立动作"


# ═══════════════════════════════════════════════════════════════════════════
# 5. 提议决策与 AI 业务名通道
# ═══════════════════════════════════════════════════════════════════════════

def test_proposal_decision_records_applied_ref_and_version(monkeypatch):
    state = _install_db(monkeypatch, proposal_row={
        "id": 2, "title": "补口径", "target_type": "kb_doc", "status": "pending",
        "decision_version": 1, "content": "正文"})
    _allow_perms(monkeypatch)

    result = decide.decide_proposal(
        _ctx(), proposal_id=2, action_key="evolution_proposal.applied",
        expected_version=1, applied_ref="业务知识库 · 返利口径", idempotency_key="k")

    assert result["changed"] is True and result["status"] == "applied"
    assert result["decision_version"] == 2
    sql, params = state["proposal_updates"][0]
    assert "applied_ref = %s" in sql and "decision_version = decision_version + 1" in sql
    assert "业务知识库 · 返利口径" in params
    assert "id" not in result


def test_by_name_resolves_current_version_and_keeps_internal_id_server_side(monkeypatch):
    _install_db(monkeypatch, candidate_row=_candidate_row())
    _allow_perms(monkeypatch)
    captured = {}

    monkeypatch.setattr(queries, "resolve_candidate",
                        lambda ctx, **kw: {"id": 7, "term": kw["term"],
                                           "expected_version": 3, "status": "pending"})
    monkeypatch.setattr(decide, "_domain_to_term",
                        lambda *a, **k: {"domain_kind": "term", "domain_ref": "订单状态",
                                         "message": "ok"})

    def _spy(ctx, **kwargs):
        captured.update(kwargs)
        return {"changed": True, "term": "订单状态", "status": "converted",
                "decision_version": 4, "domain_ref": "订单状态", "message": ""}

    monkeypatch.setattr(decide, "decide_candidate", _spy)
    result = decide.decide_candidate_by_name(
        _ctx(), term="订单状态", action_key="evolution_candidate.to_term",
        input_data={"description": ""})

    assert captured["candidate_id"] == 7, "内部 id 只在服务端解析出"
    assert captured["expected_version"] == 3, "版本由服务端按业务名解析"
    assert captured["idempotency_key"] == "订单状态:3:evolution_candidate.to_term"
    assert result["changed"] is True


def test_digest_job_runs_with_explicit_failure_state(monkeypatch):
    """归纳失败必须落 failed + 可读原因，不得吞成「没有新待办」。"""
    state = _install_db(monkeypatch)
    _allow_perms(monkeypatch)
    from backend.modules.catalog.services import evolution_service as evo
    monkeypatch.setattr(evo, "run_digest",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("upstream down")))

    job = decide.submit_digest_job(_ctx(), run_inline=True)

    assert job["status"] == "failed" and job["error_code"] == "DIGEST_FAILED"
    assert "RuntimeError" in job["error_message"] and "upstream down" in job["error_message"]
    statuses = [s for s, _ in state["job_updates"]]
    assert any("'running'" in s for s in statuses), "必须先落 running"
    assert any("'failed'" in s for s in statuses), "失败必须显式落库"


def test_digest_job_success_records_summary(monkeypatch):
    state = _install_db(monkeypatch)
    _allow_perms(monkeypatch)
    from backend.modules.catalog.services import evolution_service as evo
    monkeypatch.setattr(evo, "run_digest", lambda **kw: {
        "summary": "反馈行 0→候选 0", "pending_by_kind": {"alias": 1},
        "feedback": {}, "recheck": {}, "verification": {}})

    job = decide.submit_digest_job(_ctx(), run_inline=True)
    assert job["status"] == "succeeded"
    assert job["result"]["summary"] == "反馈行 0→候选 0"
    assert state["job_inserts"], "每次提交都留 job 行"
