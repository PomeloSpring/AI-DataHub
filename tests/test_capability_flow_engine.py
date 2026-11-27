"""P3 能力流程引擎契约（capability_flow_service）。

锁 plan P3-6 清单中的引擎侧：

1. auto 执行三条件缺一即拒（显式声明 / 白名单 / 特性开关），数据工程动作进不了流程；
2. 创建前预检全部自动步骤权限，缺任一即拒（不建半截运行）；
3. 重复幂等键返回同一 run（replayed），不重复执行；
4. 条件更新 + 租约语义：单步只被一个 worker 抢占一次；
5. 人工步骤前绝不调发布动作；人工步骤停 waiting_human；
6. 发布后**服务端复核**（读内容服务事实）才放行，不信任客户端"已完成"；
7. 重试仅限显式瞬态错误；权限/Schema 错不重试；
8. 失败/取消/事件账可追踪；取消不删已建业务数据（无补偿器，留可诊断半态）。
"""

from __future__ import annotations

import json

import pytest

from backend.modules.mind.execution import capability_runtime as rt
from backend.modules.platform.services import capability_flow_service as flows


# ── 流程三表内存模拟器（按 SQL 动词/表路由；条件更新语义与真库同形）────────

class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rowcount = 0
        self.lastrowid = 0
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        p = tuple(params or ())
        self.db["calls"].append((s, p))
        self._rows, self.rowcount = [], 0
        if s.startswith("INSERT INTO adh_capability_flow_runs"):
            key = (p[0], p[3], p[8])
            if key in self.db["idem_keys"]:
                raise Exception("Duplicate entry ... for key uk_cap_flow_run_idem")
            self.db["idem_keys"].add(key)
            rid = self.db["next_run"]
            self.db["next_run"] += 1
            self.db["runs"][rid] = {
                "id": rid, "scenario_key": p[0], "scenario_title": p[1],
                "ontology_fingerprint": p[2], "initiated_by": p[3],
                "initiated_by_name": p[4], "workspace_id": p[5], "status": p[6],
                "current_step": 0, "input_json": p[7],
                "idempotency_key": p[8], "output_json": None, "lease_owner": "",
                "lease_until": None, "error_code": "", "error_message": "",
                "created_at": "2026-11-27 10:00:00", "finished_at": None,
            }
            self.rowcount, self.lastrowid = 1, rid
            return
        if s.startswith("INSERT INTO adh_capability_flow_steps"):
            sid = self.db["next_step"]
            self.db["next_step"] += 1
            self.db["steps"].append({
                "id": sid, "run_id": p[0], "step_no": p[1], "action_key": p[2],
                "action_label": p[3], "channel": p[4], "status": p[5],
                "input_json": p[6], "output_ref": "", "perm_code": p[7],
                "executor_ref": p[8], "attempts": 0, "lease_owner": "",
                "lease_until": None, "error_code": "", "error_message": "",
                "started_at": None, "finished_at": None,
            })
            self.rowcount, self.lastrowid = 1, sid
            return
        if s.startswith("INSERT INTO adh_capability_flow_events"):
            self.db["events"].append({"run_id": p[0], "step_no": p[1],
                                      "event_type": p[2], "detail": p[3], "actor": p[4]})
            self.rowcount = 1
            return
        if "FROM adh_capability_flow_runs WHERE id=" in s:
            row = self.db["runs"].get(p[0])
            self._rows = [dict(row)] if row else []
            return
        if "FROM adh_capability_flow_runs WHERE scenario_key=" in s:
            hits = [r for r in self.db["runs"].values()
                    if (r["scenario_key"], r["initiated_by"], r["idempotency_key"])
                    == (p[0], p[1], p[2])]
            self._rows = [dict(r) for r in hits]
            return
        if "COUNT(*) AS n FROM adh_capability_flow_runs" in s:
            self._rows = [{"n": len(self.db["runs"])}]
            return
        if "FROM adh_capability_flow_runs" in s and "LIMIT" in s:
            rows = sorted(self.db["runs"].values(),
                          key=lambda r: r["id"], reverse=True)
            self._rows = [dict(r) for r in rows]
            return
        if "FROM adh_capability_flow_steps WHERE run_id=" in s:
            rows = sorted((r for r in self.db["steps"] if r["run_id"] == p[0]),
                          key=lambda r: r["step_no"])
            self._rows = [dict(r) for r in rows]
            return
        if "FROM adh_capability_flow_events WHERE run_id=" in s:
            self._rows = [dict(e) for e in self.db["events"] if e["run_id"] == p[0]]
            return
        # ── UPDATE 路由（与 capability_flow_service 的 SQL 形态严格对齐）────
        if s.startswith("UPDATE adh_capability_flow_steps SET status='running'"):
            # claim_step: params=(worker, lease, id, worker)
            row = self.db["step_by_id"](p[2])
            ok = bool(row) and row["status"] == "pending" and (
                not row.get("lease_until") or row.get("lease_owner") == p[3])
            if ok:
                row.update(status="running", lease_owner=p[0], lease_until="lease",
                           attempts=int(row.get("attempts") or 0) + 1)
            self.rowcount = 1 if ok else 0
            return
        if s.startswith("UPDATE adh_capability_flow_steps SET status=%s, output_ref=%s"):
            # 步骤成功: params=(succeeded, output_ref, id)
            row = self.db["step_by_id"](p[2])
            row.update(status=p[0], output_ref=p[1], lease_owner="", lease_until=None)
            self.rowcount = 1
            return
        if s.startswith("UPDATE adh_capability_flow_steps SET status=%s, lease_owner='', "
                        "lease_until=NULL, error_code=%s"):
            # 瞬态重试重置: params=(pending, code, msg, id)
            row = self.db["step_by_id"](p[3])
            row.update(status=p[0], lease_owner="", lease_until=None,
                       error_code=p[1], error_message=p[2])
            self.rowcount = 1
            return
        if s.startswith("UPDATE adh_capability_flow_steps SET status=%s, error_code=%s"):
            # _fail_step: params=(failed, code, msg, id)
            row = self.db["step_by_id"](p[3])
            row.update(status=p[0], error_code=p[1], error_message=p[2],
                       lease_owner="", lease_until=None)
            self.rowcount = 1
            return
        if s.startswith("UPDATE adh_capability_flow_steps SET status=%s, finished_at=NOW()"):
            # resume 人工步骤: params=(succeeded, id, waiting_human)
            row = self.db["step_by_id"](p[1])
            ok = bool(row) and row["status"] == p[2]
            if ok:
                row.update(status=p[0], finished_at="now")
            self.rowcount = 1 if ok else 0
            return
        if s.startswith("UPDATE adh_capability_flow_steps SET status=%s WHERE id="):
            # 人工停等: params=(waiting_human, id, pending)
            row = self.db["step_by_id"](p[1])
            ok = bool(row) and row["status"] == p[2]
            if ok:
                row.update(status=p[0])
            self.rowcount = 1 if ok else 0
            return
        if s.startswith("UPDATE adh_capability_flow_steps SET status='cancelled'"):
            for r in self.db["steps"]:
                if r["run_id"] == p[0] and r["status"] in ("pending", "running", "waiting_human"):
                    r["status"] = "cancelled"
            self.rowcount = 1
            return
        if s.startswith("UPDATE adh_capability_flow_runs SET lease_owner="):
            # claim_run: params=(worker, lease, id, worker)
            row = self.db["runs"].get(p[2])
            ok = bool(row) and row["status"] in ("pending", "running") and (
                not row.get("lease_until") or row.get("lease_owner") == p[3])
            if ok:
                row["lease_owner"], row["lease_until"] = p[0], "lease"
            self.rowcount = 1 if ok else 0
            return
        if s.startswith("UPDATE adh_capability_flow_runs SET current_step=%s, output_json=%s"):
            # 步骤成功后持久化上下文: params=(step_no, ctx_json, id)
            row = self.db["runs"][p[2]]
            row.update(current_step=p[0], output_json=p[1])
            self.rowcount = 1
            return
        if s.startswith("UPDATE adh_capability_flow_runs SET status=%s, current_step=%s, "
                        "lease_owner=''"):
            # 人工停等 run: params=(waiting_human, step_no, id)
            row = self.db["runs"][p[2]]
            row.update(status=p[0], current_step=p[1], lease_owner="", lease_until=None)
            self.rowcount = 1
            return
        if s.startswith("UPDATE adh_capability_flow_runs SET status=%s, error_code=%s"):
            # _fail_step run: params=(failed, code, msg, id)；仅 pending/running 可改
            row = self.db["runs"].get(p[3])
            ok = bool(row) and row["status"] in ("pending", "running")
            if ok:
                row.update(status=p[0], error_code=p[1], error_message=p[2])
            self.rowcount = 1 if ok else 0
            return
        if "finished_at=NOW(), output_json=%s WHERE id=%s" in s:
            # 运行成功: params=(succeeded, ctx_json, id)
            row = self.db["runs"][p[2]]
            row.update(status=p[0], output_json=p[1], lease_owner="", lease_until=None,
                       finished_at="now")
            self.rowcount = 1
            return
        if "error_code='CANCELLED'" in s:
            # 取消: params=(cancelled, id)；终态不可再改
            row = self.db["runs"].get(p[1])
            ok = bool(row) and row["status"] not in ("succeeded", "failed", "cancelled")
            if ok:
                row.update(status=p[0], finished_at="now", error_code="CANCELLED",
                           error_message="运行被取消；已创建的业务数据不自动删除（动作无补偿器声明），半态可诊断")
            self.rowcount = 1 if ok else 0
            return
        if s.startswith("UPDATE adh_capability_flow_runs SET status=%s, lease_owner=''"):
            # 终局（失败判定）: params=(failed, id)；仅 pending/running 可改
            row = self.db["runs"].get(p[1])
            ok = bool(row) and row["status"] in ("pending", "running")
            if ok:
                row.update(status=p[0], lease_owner="", lease_until=None, finished_at="now")
            self.rowcount = 1 if ok else 0
            return
        if s.startswith("UPDATE adh_capability_flow_runs SET status=%s WHERE id="):
            # resume 后置回 running: params=(running, id)
            row = self.db["runs"][p[1]]
            row.update(status=p[0])
            self.rowcount = 1
            return
        raise AssertionError(f"FakeFlowDB 未预期的 SQL: {s}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def begin(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _install_db(monkeypatch) -> dict:
    db = {"calls": [], "runs": {}, "steps": [], "events": [], "idem_keys": set(),
          "next_run": 1, "next_step": 1}
    db["step_by_id"] = lambda sid: next((r for r in db["steps"] if r["id"] == sid), None)
    monkeypatch.setattr(flows, "_conn", lambda: _FakeConn(db))
    monkeypatch.setattr(flows, "_q", lambda sql, params=(): _run_q(db, sql, params))
    monkeypatch.setattr(flows, "_w", lambda sql, params=(): _run_w(db, sql, params))
    return db


def _run_q(db, sql, params):
    cur = _FakeCursor(db)
    cur.execute(sql, params)
    return cur.fetchall()


def _run_w(db, sql, params):
    cur = _FakeCursor(db)
    cur.execute(sql, params)
    return cur.rowcount


# ── 场景/契约夹具（真实 ops_content_publish 形态的最小复刻）────────────────

def _contract(*, sync_in_flow=False):
    def act(key, channel="ai", read_only=False, perm="article:manage", auto=True):
        return {"action_key": key, "label": key, "channel": channel,
                "read_only": read_only, "perm_code": perm,
                "required_perm_codes": [perm], "auto_executable": auto,
                "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
                "executor_ref": f"function:{key}", "timeout_ms": 1000,
                "idempotency": "none", "error_codes": []}

    actions = {
        "content_draft.create": act("content_draft.create"),
        "content_review.submit": act("content_review.submit"),
        "content_review.publish": act("content_review.publish", channel="human",
                                      perm="article:publish", auto=False),
    }
    if sync_in_flow:
        actions["sync.run"] = act("sync.run", perm="sync:execute", auto=False)
    flow = [
        {"order": 1, "action": "content_draft.create",
         "input_bindings": {"title": "input.title", "body": "input.body"},
         "output_bindings": {"article.slug": "output.slug"},
         "retry": {"max_attempts": 2}, "idempotency_key": "draft:{input.title}"},
        {"order": 2, "action": "content_review.submit",
         "input_bindings": {"slug": "ctx.article.slug"},
         "output_bindings": {"review.status": "output.status"},
         "retry": {"max_attempts": 1}},
        {"order": 3, "action": "content_review.publish",
         "input_bindings": {"ident": "ctx.article.slug"},
         "retry": {"max_attempts": 0}},
    ]
    if sync_in_flow:
        flow.append({"order": 4, "action": "sync.run", "input_bindings": {}})
    scenario = {
        "key": "ops_content_publish", "title": "内容草稿到发布",
        "execution": {"mode": "auto", "enabled": True,
                      "input_schema": {"type": "object",
                                       "properties": {"title": {"type": "string"},
                                                      "body": {"type": "string"}},
                                       "required": ["title", "body"]},
                      "allowlist": [f["action"] for f in flow]},
        "flow": flow,
    }
    doc = {"contract_digest": "fp-1", "objects": [{"key": "x", "actions": []}],
           "scenarios": [scenario]}
    return rt.ActiveContract(model_id=7, model_name="AS-BOT 系统能力本体",
                             fingerprint="fp-1", doc=doc, actions=actions)


def _install_contract(monkeypatch, **kwargs):
    contract = _contract(**kwargs)
    monkeypatch.setattr(rt, "load_active_contract", lambda: contract)
    monkeypatch.setattr(flows, "_load_scenario",
                        lambda key: ({"key": key,
                                      "title": "内容草稿到发布",
                                      **contract.doc["scenarios"][0]}, contract))
    monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_ai_perms",
                        lambda *a, **k: {
                            "article:manage": {"ai_access": "write", "label": "", "ai_note": ""},
                            "article:publish": {"ai_access": "none", "label": "", "ai_note": "人工"},
                        })
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)


CTX_ARGS = dict(user_id=7, username="alice", workspace_id=3)


def _enable(monkeypatch):
    monkeypatch.setenv("CAPABILITY_FLOW_AUTO_ENABLED", "1")
    monkeypatch.setenv("CAPABILITY_FLOW_SCENARIOS", "ops_content_publish")


# ═══════════════════════════════════════════════════════════════════════════
# 1. auto 三条件 + 数据工程动作边界
# ═══════════════════════════════════════════════════════════════════════════

def test_auto_three_conditions_each_required(monkeypatch):
    _install_db(monkeypatch)
    _install_contract(monkeypatch)
    monkeypatch.delenv("CAPABILITY_FLOW_AUTO_ENABLED", raising=False)
    with pytest.raises(flows.FlowError) as exc:
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": "t", "body": "b"},
                         idempotency_key="k", **CTX_ARGS)
    assert exc.value.code == "FLOW_DISABLED"

    _enable(monkeypatch)
    monkeypatch.setenv("CAPABILITY_FLOW_SCENARIOS", "other")
    with pytest.raises(flows.FlowError) as exc2:
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": "t", "body": "b"},
                         idempotency_key="k", **CTX_ARGS)
    assert exc2.value.code == "SCENARIO_NOT_ALLOWLISTED"

    monkeypatch.setenv("CAPABILITY_FLOW_SCENARIOS", "ops_content_publish")
    monkeypatch.setattr(flows, "_load_scenario",
                        lambda key: ({**_contract().doc["scenarios"][0],
                                      "execution": {"mode": "plan"}}, _contract()))
    with pytest.raises(flows.FlowError) as exc3:
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": "t", "body": "b"},
                         idempotency_key="k", **CTX_ARGS)
    assert exc3.value.code == "SCENARIO_NOT_AUTO"


def test_data_engineering_action_never_enters_flow(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch, sync_in_flow=True)
    _enable(monkeypatch)
    with pytest.raises(flows.FlowError) as exc:
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": "t", "body": "b"},
                         idempotency_key="k", **CTX_ARGS)
    assert exc.value.code in ("ACTION_NOT_EXECUTABLE", "ACTION_NOT_ALLOWLISTED")
    assert not db["runs"], "数据工程动作不得进入流程，也不建半截运行"


def test_input_schema_rejected_before_any_write(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    with pytest.raises(flows.FlowError) as exc:
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": "缺 body"}, idempotency_key="k",
                         **CTX_ARGS)
    assert exc.value.code == "SCHEMA_INVALID"
    assert not db["runs"]


def test_permission_preflight_denies_without_half_run(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_ai_perms",
                        lambda *a, **k: {})  # 什么码都没有
    with pytest.raises(flows.FlowError) as exc:
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": "t", "body": "b"},
                         idempotency_key="k", **CTX_ARGS)
    assert exc.value.code == "PERMISSION_DENIED"
    assert "content_draft.create" in str(exc.value)
    assert not db["runs"], "缺任一步权限即拒，不创建半截运行"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 推进：绑定求值 / 人工停等 / 绝不提前发布
# ═══════════════════════════════════════════════════════════════════════════

def _stub_actions(monkeypatch, results=None, fail=None):
    """桩 execute_action：记录调用；results 按 action 返回 payload；fail 注入错误。"""
    calls = []
    results = results or {}

    def _exec(action_key, args, ctx=None, idempotency_key="", **kw):
        calls.append({"action": action_key, "args": args, "idem": idempotency_key})
        if fail and fail.get(action_key):
            code, msg = fail[action_key]
            raise rt.CapabilityRuntimeError(code, msg)
        payload = results.get(action_key) or {"ok": True}
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    monkeypatch.setattr(rt, "execute_action", _exec)
    return calls


def test_advances_auto_steps_and_stops_at_human_before_publish(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    calls = _stub_actions(monkeypatch, results={
        "content_draft.create": {"slug": "notice-1"},
        "content_review.submit": {"status": "submitted"},
    })

    run = flows.create_run(scenario_key="ops_content_publish",
                           input_payload={"title": "公告", "body": "正文"},
                           idempotency_key="k1", **CTX_ARGS)

    # 步骤 1/2 自动执行且绑定求值正确；步骤 3 人工停等
    assert [c["action"] for c in calls] == ["content_draft.create", "content_review.submit"]
    assert calls[0]["args"] == {"title": "公告", "body": "正文"}
    assert calls[1]["args"] == {"slug": "notice-1"}, "ctx.article.slug 经 output_bindings 传递"
    assert run["status"] == "waiting_human" and run["current_step"] == 3
    statuses = [s["status"] for s in run["steps"]]
    assert statuses == ["succeeded", "succeeded", "waiting_human"]
    # 人工步骤前绝不调发布动作（AI 不得代做）
    assert all(c["action"] != "content_review.publish" for c in calls)
    # 上下文持久化 + 事件可追踪
    assert json.loads(db["runs"][1]["output_json"])["article.slug"] == "notice-1"
    kinds = [e["event_type"] for e in db["events"]]
    assert "created" in kinds and "step_succeeded" in kinds and "waiting_human" in kinds
    # 幂等键模板求值（draft:{input.title} → draft:公告）
    assert calls[0]["idem"] == "draft:公告"


def test_idempotent_create_returns_same_run(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    _stub_actions(monkeypatch, results={"content_draft.create": {"slug": "s1"}})
    first = flows.create_run(scenario_key="ops_content_publish",
                             input_payload={"title": "t", "body": "b"},
                             idempotency_key="same", **CTX_ARGS)
    second = flows.create_run(scenario_key="ops_content_publish",
                              input_payload={"title": "t", "body": "b"},
                              idempotency_key="same", **CTX_ARGS)
    assert second["replayed"] is True and second["run_id"] == first["run_id"]
    assert len(db["runs"]) == 1, "同幂等键只建一条运行"


def test_step_claim_is_single_winner(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    _stub_actions(monkeypatch, results={"content_draft.create": {"slug": "s1"}})
    flows.create_run(scenario_key="ops_content_publish",
                     input_payload={"title": "t", "body": "b"},
                     idempotency_key="k", **CTX_ARGS)
    step = db["steps"][0]
    step.update(status="pending", lease_owner="", lease_until=None)  # 复位成可抢
    assert flows._claim_step(step["id"], "worker-a") is True
    # 同一步骤第二次抢占必败（条件更新 + 租约语义：单步只执行一次）
    assert flows._claim_step(step["id"], "worker-b") is False
    # 非 pending 状态（已成功/停等）同样不可抢
    step.update(status="succeeded", lease_owner="", lease_until=None)
    assert flows._claim_step(step["id"], "worker-c") is False


# ═══════════════════════════════════════════════════════════════════════════
# 3. 人工复核：读内容服务事实源，不信任客户端
# ═══════════════════════════════════════════════════════════════════════════

def test_resume_requires_real_publish_fact(monkeypatch):
    _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    _stub_actions(monkeypatch, results={"content_draft.create": {"slug": "notice-1"}})
    run = flows.create_run(scenario_key="ops_content_publish",
                           input_payload={"title": "t", "body": "b"},
                           idempotency_key="k", **CTX_ARGS)
    assert run["status"] == "waiting_human"

    from backend.modules.platform.services import content_service
    # 未发布：复核必须拒（不信任客户端"已完成"）
    monkeypatch.setattr(content_service, "get_article",
                        lambda **kw: {"live_revision_id": 0, "status": "review",
                                      "slug": kw.get("slug")})
    monkeypatch.setattr(content_service, "STATUS_ARCHIVED", "archived")
    with pytest.raises(flows.FlowError) as exc:
        flows.resume_human_step(run["run_id"], actor="alice")
    assert exc.value.code == "HUMAN_PENDING"
    assert flows.get_run(run["run_id"])["steps"][2]["status"] == "waiting_human"

    # 真实发布后复核通过：人工步 succeeded、run succeeded
    monkeypatch.setattr(content_service, "get_article",
                        lambda **kw: {"live_revision_id": 9, "status": "published",
                                      "slug": kw.get("slug")})
    done = flows.resume_human_step(run["run_id"], actor="alice")
    assert done["steps"][2]["status"] == "succeeded"
    assert done["status"] == "succeeded"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 重试边界 / 失败 / 取消
# ═══════════════════════════════════════════════════════════════════════════

def test_transient_error_retries_but_permission_error_does_not(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    # 第一次 TIMEOUT（瞬态，max_attempts=2），第二次成功
    state = {"n": 0}
    calls = []

    def _exec(action_key, args, ctx=None, idempotency_key="", **kw):
        calls.append(action_key)
        if action_key == "content_draft.create":
            state["n"] += 1
            if state["n"] == 1:
                raise rt.CapabilityRuntimeError("TIMEOUT", "超时")
        return {"content": [{"type": "text", "text": json.dumps({"slug": "s1"})}]}

    monkeypatch.setattr(rt, "execute_action", _exec)
    run = flows.create_run(scenario_key="ops_content_publish",
                           input_payload={"title": "t", "body": "b"},
                           idempotency_key="k", **CTX_ARGS)
    assert run["status"] == "waiting_human"
    assert calls.count("content_draft.create") == 2, "瞬态错误按 retry 重试"
    assert any(e["event_type"] == "step_retry" for e in db["events"])

    # 非瞬态（PERMISSION_DENIED）不重试：直接 failed
    _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _stub_actions(monkeypatch, fail={"content_draft.create": ("PERMISSION_DENIED", "无权")})
    run2 = flows.create_run(scenario_key="ops_content_publish",
                            input_payload={"title": "t", "body": "b"},
                            idempotency_key="k2", **CTX_ARGS)
    assert run2["status"] == "failed" and run2["error_code"] == "PERMISSION_DENIED"
    assert run2["steps"][0]["status"] == "failed"
    assert run2["steps"][0]["attempts"] == 1, "权限错误不得重试"


def test_cancel_keeps_half_state_diagnosable(monkeypatch):
    db = _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    _stub_actions(monkeypatch, results={"content_draft.create": {"slug": "s1"}})
    run = flows.create_run(scenario_key="ops_content_publish",
                           input_payload={"title": "t", "body": "b"},
                           idempotency_key="k", **CTX_ARGS)
    cancelled = flows.cancel_run(run["run_id"], actor="alice", user_id=7, workspace_id=3)
    assert cancelled["status"] == "cancelled"
    assert "不自动删除" in cancelled["error_message"], "半态留可诊断，不做假回滚"
    assert any(e["event_type"] == "cancelled" for e in db["events"])
    # 终态不可再取消
    with pytest.raises(flows.FlowError) as exc:
        flows.cancel_run(run["run_id"], actor="alice", user_id=7, workspace_id=3)
    assert exc.value.code == "RUN_TERMINAL"


def test_list_runs_paginates_server_side(monkeypatch):
    _install_db(monkeypatch)
    _install_contract(monkeypatch)
    _enable(monkeypatch)
    _stub_actions(monkeypatch, results={"content_draft.create": {"slug": "s"}})
    for i in range(3):
        flows.create_run(scenario_key="ops_content_publish",
                         input_payload={"title": f"t{i}", "body": "b"},
                         idempotency_key=f"k{i}", **CTX_ARGS)
    out = flows.list_runs(page=1, page_size=2)
    assert out["total"] == 3 and out["page"] == 1 and out["page_size"] == 2
    assert out["items"], "信封字段由服务端给出（total 来自 COUNT）"
    with pytest.raises(flows.FlowError):
        flows.list_runs(page=1, page_size=2, status="bogus")
