"""决策建议定时预警（计划 §出口 3）的回归。

锁四件事：

1. **只有 hit 才发通知**：no_hit/partial/skipped 都不推（无命中不发垃圾消息），
   且 partial/skipped 的原因仍在实例里，不靠 IM 催；
2. **通知正文不含业务内容**：递给发送口的 results 只有 verdict，没有指标名/阈值/
   建议正文（与 test_message_template_notification 那条线合起来才完整）；
3. **调度源不冒充别的东西**：读 `adh_decision_trees.schedule_cron`，绝不往
   `adh_scheduled_tasks.task_type` 里塞新形态（既有产品决策）；
4. **run_key 幂等**：同一墙钟分钟重投不双发预警。

身份口径也在这里钉住：定时评估用发布人，能力实时继承；发布人停用/零授权
就显式说明"没评估"，不拿 admin 兜底（那等于绕开角色授权这一唯一裁决点）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.decision import engine, scheduler, service

_TREE = {
    "id": 77, "tree_key": "accuracy-risk", "name": "设备精度风险判定", "workspace_id": 7,
    "status": "active", "is_active": 1, "version": 3, "published_by": 21, "created_by": 21,
    "notify_channel_id": 9, "schedule_cron": "0 8 * * *", "timezone": "Asia/Shanghai",
    "apply_sites": [{"site_code": "cn", "datasource": "华东仓"}],
    "required_metrics": ["扫描精度偏差均值"],
    "tree_doc": {"root": {"type": "branch", "label": "精度风险判定", "op": "all", "children": [
        {"type": "leaf", "label": "校准超差", "advice_key": "adv_recalibrate",
         "when": {"metric": "扫描精度偏差均值", "comparator": ">=", "value": 0.05}}]}},
}


@pytest.fixture
def wired(monkeypatch):
    """把外部触点（DB / 语义层 / 通知发送口）换成可控桩，并记录被递出去的东西。"""
    sent: dict = {"notify_calls": [], "runtime": []}

    monkeypatch.setattr(service, "get_tree_by_id", lambda tid: dict(_TREE) if tid == 77 else None)
    monkeypatch.setattr(service, "record_run",
                        lambda row, outcome, ctx, persist=True: {
                            "id": 1, "tree_key": row.get("tree_key"), "tree_name": row.get("name"),
                            "tree_version": row.get("version"), "verdict": outcome.verdict,
                            "verdict_reason": outcome.reason, "hit_path": outcome.hit_path,
                            "advice_snapshot": [], "evidence": outcome.evidence,
                            "run_key": ctx.run_key, "created_at": "2026-11-12 08:00:00"})
    monkeypatch.setattr(service, "_advice_for",
                        lambda keys: [{"advice_key": k, "action": "安排现场校准",
                                       "rationale": "", "severity": "high", "kb_refs": []}
                                      for k in keys])
    from backend.modules.decision import dictionary as dic
    monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: {
        "metric": [dic.DictHit("metric", "扫描精度偏差均值", "equipment", "dict_exact")],
        "dimension": []})
    monkeypatch.setattr(engine, "MAX_FULL_EVAL_LEAVES", 8)

    from backend.common import auth
    from backend.core import site_service, role_service
    monkeypatch.setattr(auth, "get_user_by_id",
                        lambda uid: {"id": uid, "username": "publisher", "status": "active"})
    monkeypatch.setattr(role_service.role_service, "get_user_allowed_datasources",
                        lambda uid, ws=0: [12])
    monkeypatch.setattr(site_service, "authorized_sites",
                        lambda uid, ws=0: [{"site_code": "cn", "is_aggregate": 0},
                                           {"site_code": "sg", "is_aggregate": 0}])
    monkeypatch.setattr(site_service, "resolve_site_to_source", lambda ref, allowed: 12)

    from backend.core import task_runtime
    real_call = task_runtime.call

    def fake_call(name, *a, **k):
        sent["runtime"].append((name, a, k))
        if name == "task.send_notification":
            sent["notify_calls"].append({"task": a[0], "results": a[1], "link": k.get("report_link")})
            return "sent"
        if name == "task.scheduled_service":
            return type("S", (), {"get_channel": lambda self, cid: {
                "id": cid, "is_active": True, "workspace_id": 7}})()
        return real_call(name, *a, **k)

    monkeypatch.setattr(task_runtime, "call", fake_call)
    return sent


def _outcome_verdict(monkeypatch, measured=0.08):
    """让求值走完整路径：语义层执行口返回一个可控实测值。"""
    from backend.semantics import execute as sem_execute

    def fake_exec(query, policy):
        from backend.semantics.models import ResultColumn, SemanticResult
        return SemanticResult(columns=[ResultColumn(name="扫描精度偏差均值")],
                              rows=[[measured]])

    monkeypatch.setattr(sem_execute, "execute", fake_exec)


# ── 1. 命中才通知 ───────────────────────────────────────────────

class TestNotifyOnlyOnHit:
    def test_hit_sends_one_notification_with_metadata_only(self, wired, monkeypatch):
        _outcome_verdict(monkeypatch, measured=0.08)
        out = scheduler.run_scheduled(77, run_key="decisioncron:77:Asia/Shanghai:202611120800")
        assert out["hit_count"] == 1 and out["notified"] is True
        call = wired["notify_calls"][0]
        # 递给发送口的 results 只有 verdict：指标名/阈值/实测值/建议正文都不在里面
        assert all(set(r) == {"verdict"} for r in call["results"])
        blob = str(call["results"])
        for banned in ("扫描精度偏差均值", "0.08", "安排现场校准", "adv_recalibrate", "0.05"):
            assert banned not in blob
        # 链接指向决策中心实例页（由 decision:read 把关），不是某个内部 id 页
        assert call["link"].endswith("/ws/7/decisions")

    def test_no_hit_does_not_notify(self, wired, monkeypatch):
        _outcome_verdict(monkeypatch, measured=0.01)
        out = scheduler.run_scheduled(77, run_key="k1")
        assert out["hit_count"] == 0 and out["notified"] is False
        assert not wired["notify_calls"]
        assert "没有命中" in out["notify_skipped_reason"]

    def test_skipped_tree_does_not_notify_but_keeps_reason(self, wired, monkeypatch):
        """站点不适用等跳过原因要留在实例里，不推 IM（那是"判不了"不是"要处置"）。"""
        tree = dict(_TREE, apply_sites=[{"site_code": "eu", "datasource": ""}])
        monkeypatch.setattr(service, "get_tree_by_id", lambda tid: tree)
        _outcome_verdict(monkeypatch)
        out = scheduler.run_scheduled(77, run_key="k2")
        assert not wired["notify_calls"]
        assert "没有交集" in out["skipped"]

    def test_hit_without_channel_records_explicit_reason(self, wired, monkeypatch):
        tree = dict(_TREE, notify_channel_id=0)
        monkeypatch.setattr(service, "get_tree_by_id", lambda tid: tree)
        _outcome_verdict(monkeypatch, measured=0.09)
        out = scheduler.run_scheduled(77, run_key="k3")
        assert out["hit_count"] == 1 and out["notified"] is False
        assert "未配置通知渠道" in out["notify_skipped_reason"]


# ── 2. 身份 fail-closed，不拿 admin 兜底 ────────────────────────

class TestIdentity:
    def test_disabled_publisher_stops_before_query(self, wired, monkeypatch):
        from backend.common import auth
        monkeypatch.setattr(auth, "get_user_by_id",
                            lambda uid: {"id": uid, "username": "gone", "status": "disabled"})
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("停用身份不该取数")))
        out = scheduler.run_scheduled(77, run_key="k4")
        assert "发布人已停用" in out["skipped"]

    def test_zero_authorized_sources_is_explained_not_silently_empty(self, wired, monkeypatch):
        from backend.core.role_service import role_service
        monkeypatch.setattr(role_service, "get_user_allowed_datasources", lambda uid, ws=0: [])
        out = scheduler.run_scheduled(77, run_key="k5")
        assert "没有任何已授权数据源" in out["skipped"]

    def test_draft_tree_is_never_scheduled(self, wired, monkeypatch):
        monkeypatch.setattr(service, "get_tree_by_id",
                            lambda tid: dict(_TREE, status="draft"))
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("草案不该取数")))
        assert "不参与定时评估" in scheduler.run_scheduled(77, run_key="k6")["skipped"]


# ── 3. Beat 第五个调度源 ────────────────────────────────────────

class TestBeatSource:
    def test_scheduled_trees_reads_only_active_cron_rows(self, monkeypatch):
        seen = {}

        def fake_query(sql, params=None, fetchone=False):
            seen["sql"] = sql
            return [{"id": 1, "tree_key": "a", "schedule_cron": "0 8 * * *"}]

        monkeypatch.setattr(service, "execute_query", fake_query)
        rows = scheduler.scheduled_trees()
        assert rows and "status = 'active'" in seen["sql"] and "schedule_cron" in seen["sql"]
        assert "adh_scheduled_tasks" not in seen["sql"], "预警不得冒充定时任务表"

    def test_beat_schedule_contains_decision_entry_and_uses_scheduled_queue(self, monkeypatch):
        from backend.modules.flow.tasks import beat_schedule as bs

        monkeypatch.setattr(bs, "build_beat_schedule", bs.build_beat_schedule)
        calls = []

        def fake_call(name, *a, **k):
            if name == "decision.scheduled_trees":
                return [{"id": 5, "tree_key": "t", "schedule_cron": "0 8 * * *",
                         "timezone": "Asia/Shanghai"}]
            calls.append(name)
            return []

        import backend.core.task_runtime as tr
        monkeypatch.setattr(tr, "call", fake_call)
        monkeypatch.setattr("backend.modules.flow.services.scheduled_task_service.scheduled_task_service.list_active_tasks", lambda: [])
        monkeypatch.setattr("backend.modules.flow.dag.dag_service.dag_service.list_active_scheduled", lambda: [])
        monkeypatch.setattr("backend.common.db.execute_query", lambda *a, **k: [])

        schedule = bs.build_beat_schedule()
        entry = schedule["decision_tree_5"]
        assert entry["task"] == "backend.modules.flow.tasks.decision_tasks.evaluate_and_notify"
        assert entry["args"] == (5,) and entry["options"]["queue"] == "scheduled"

    def test_run_key_idempotency_guard_blocks_second_dispatch(self, monkeypatch):
        called = []
        monkeypatch.setattr(service, "execute_query",
                            lambda sql, params=None, fetchone=False: called.append(params) or (
                                {"ok": 1} if params and params[0] == "RUN1" else None))
        assert service.dispatched_by_run_key("RUN1") is True
        assert service.dispatched_by_run_key("RUN2") is False
        assert service.dispatched_by_run_key("") is False, "空键不该去查库，也不该判成已派发"
