"""决策建议的 Chat 出口回归（计划 §出口 1）。

锁的是"随本轮结果出建议"这条链路的四个失败方式：

1. **补取数**：turn 模式为了"看起来完整"偷偷再打一次库 → 首答被拖慢且没人知道；
2. **静默**：本轮没取全却回 `no_hit`（等于把"没能判断"说成"数据没问题"）；
3. **回放重算**：打开老会话时逐回合重新取数（慢，且口径已变 → 递出"当时的假建议"）；
4. **双口径**：前端自己判断要不要显示建议卡（服务端 already 判过一遍）。

全部离线跑：语义层执行口被换成"敢被调用就抛"的哨兵，取数次数用断言直接钉住。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.decision import dictionary as dic
from backend.modules.decision import engine, outlet, service

_INDEX = {
    "metric": [dic.DictHit("metric", "activation_rate", "equipment", "dict_exact",
                           aliases=["激活率"])],
    "dimension": [dic.DictHit("dimension", "site", "equipment", "dict_exact")],
}


def _tree_row(**kw):
    row = {"id": 1, "tree_key": "activation-risk", "name": "设备激活风险",
           "status": "active", "version": 3, "is_active": 1, "workspace_id": 301,
           "required_metrics": ["activation_rate"], "apply_sites": [],
           "tree_doc": {"root": {"type": "branch", "label": "激活风险判定", "op": "all", "children": [
               {"type": "leaf", "label": "激活率过低", "advice_key": "adv_low_activation",
                "when": {"metric": "activation_rate", "comparator": "<", "value": 0.6,
                         "time_window": "7d"}}]}}}
    row.update(kw)
    return row


def _semantic_tool_call(columns, rows):
    return {"step": 1, "tool": "datahub__run_semantic_query", "error": None,
            "result": json.dumps({"columns": [{"name": c} for c in columns], "rows": rows},
                                 ensure_ascii=False)}


def _identity(**kw):
    base = {"user_id": 7, "username": "alice", "role": "analyst", "workspace_id": 301,
            "datasource_id": 12, "site_code": "cn", "conversation_id": 501,
            "message_uuid": "msg-abc"}
    base.update(kw)
    return base


@pytest.fixture
def no_fetch(monkeypatch):
    """语义层执行口一旦被告知"补取数"就直接抛 —— turn 模式的铁证。"""
    def _boom(*a, **k):
        raise AssertionError("turn 模式不得补取数（会拖慢首答且没人知道）")

    monkeypatch.setattr("backend.semantics.execute.execute", _boom)
    monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: _INDEX)
    monkeypatch.setattr(service, "execute_query",
                        lambda sql, params=None, fetchone=False:
                        [{"advice_key": "adv_low_activation", "action": "复核激活口径",
                          "rationale": "", "severity": "high", "kb_refs": []}]
                        if "adh_decision_advice" in sql else [])
    monkeypatch.setattr(service, "execute_write", lambda sql, params=None: 1)
    monkeypatch.setattr(service, "active_trees", lambda ws=0: [_tree_row()])
    monkeypatch.setattr(service, "get_tree", lambda key: {"name": "设备激活风险"})
    monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_perm_codes",
                        lambda uid, ws=0: ["decision:read"])


# ── 1. 只用本轮结果 ─────────────────────────────────────────────

class TestTurnUsesOnlyThisTurnData:
    def test_hit_from_tool_result_without_extra_query(self, no_fetch):
        payload = {"reply": "激活率偏低", "tool_calls": [
            _semantic_tool_call(["site", "activation_rate"], [["上海仓", 0.41]])]}
        out = outlet.evaluate_turn(payload, _identity())
        assert out["skipped_reason"] == ""
        item = out["items"][0]
        assert item["verdict"] == "hit"
        assert item["hit_path"] == ["激活风险判定", "激活率过低"]
        assert item["evidence"][0]["measured"] == 0.41
        assert item["advice"][0]["action"] == "复核激活口径"

    def test_no_data_turn_is_irrelevant_and_stays_quiet(self, no_fetch):
        """问答/操作说明类回合与决策无关：**不下发任何文案**。

        不是“没评成”，是“本就不需要评”。给一句“本轮不做判定”会把“没这个必要”
        说成“功能没跑”，还会反过来教模型每轮都提决策（用户 2026-11-12 明确不要）。
        前端 items 与 reason 都空 → 整块不渲染。
        """
        out = outlet.evaluate_turn({"reply": "你好"}, _identity())
        assert out["items"] == []
        assert out["skipped_reason"] == "", "无关回合必须回空原因（= 不出现区块）"

    def test_unusable_result_still_speaks_up(self, no_fetch):
        """本该有取数结果但不可用（展示截断）= 真没评成，必须显式。"""
        payload = {"reply": "激活率偏低", "tool_calls": [
            {"step": 1, "tool": "datahub__run_semantic_query", "error": None,
             "result": '{"columns": [{"name": "activation_rate"}], "rows": [[0.4…'}]}
        out = outlet.evaluate_turn(payload, _identity())
        assert out["items"] == []
        assert "截断" in out["skipped_reason"], "结果不可用不能退成“与本轮无关”"

    def test_error_turn_is_not_evaluated(self, no_fetch):
        out = outlet.evaluate_turn({"error": "模型超时", "tool_calls": [
            _semantic_tool_call(["activation_rate"], [[0.41]])]}, _identity())
        assert out["items"] == [] and "执行未完成" in out["skipped_reason"]

    def test_irrelevant_tree_is_not_dragged_in(self, no_fetch):
        """本轮没碰到这棵树的任何指标 → 不评估（否则每轮都弹"没法判断"的噪声卡）。"""
        payload = {"tool_calls": [_semantic_tool_call(["order_cnt"], [[10]])]}
        out = outlet.evaluate_turn(payload, _identity())
        assert out["items"] == []

    def test_truncated_tool_result_is_never_guessed(self, no_fetch):
        """展示截断过的结果不能拿来判定：宁可不判，也不拿半截 JSON 猜一个数。"""
        call = {"tool": "datahub__run_semantic_query", "error": None,
                "result": '{"columns": [{"name": "activation_rate"}], "rows": [[0.4'}
        out = outlet.evaluate_turn({"tool_calls": [call]}, _identity())
        assert out["items"] == []
        assert "截断" in out["skipped_reason"]


# ── 2. partial 必须点名，不折叠成 no_hit ─────────────────────────

class TestPartialIsLoud:
    def test_missing_metric_marks_partial(self, no_fetch, monkeypatch):
        row = _tree_row(required_metrics=["activation_rate", "calibration_count"])
        row["tree_doc"]["root"]["children"].append(
            {"type": "leaf", "label": "校准间隔过长", "advice_key": "adv_recalibrate",
             "when": {"metric": "calibration_count", "comparator": "<", "value": 3}})
        index = {"metric": list(_INDEX["metric"]) + [
            dic.DictHit("metric", "calibration_count", "equipment", "dict_exact")],
            "dimension": list(_INDEX["dimension"])}
        monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: index)
        monkeypatch.setattr(service, "active_trees", lambda ws=0: [row])
        payload = {"tool_calls": [_semantic_tool_call(["activation_rate"], [[0.41]])]}
        out = outlet.evaluate_turn(payload, _identity())
        item = out["items"][0]
        assert item["verdict"] == "partial"
        assert item["missing_metrics"] == ["calibration_count"]
        assert "未完整评估" in item["reason"]

    def test_multi_row_detail_without_dimension_is_flagged(self, no_fetch):
        """本轮是多行明细而条件没指定维度取值：按首行判定必须写明是预览值。"""
        payload = {"tool_calls": [_semantic_tool_call(
            ["site", "activation_rate"], [["上海仓", 0.41], ["北京仓", 0.77]])]}
        out = outlet.evaluate_turn(payload, _identity())
        note = out["items"][0]["evidence"][0].get("note", "")
        assert "首行" in note and "完整评估" in note


# ── 3. 权限与可见性 ─────────────────────────────────────────────

class TestPermissionGate:
    def test_role_without_perm_gets_explicit_reason(self, monkeypatch):
        monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_perm_codes",
                            lambda uid, ws=0: [])
        monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: _INDEX)
        called = []
        monkeypatch.setattr(service, "active_trees",
                            lambda ws=0: called.append(1) or [])
        payload = {"tool_calls": [_semantic_tool_call(["activation_rate"], [[0.41]])]}
        out = outlet.evaluate_turn(payload, _identity())
        assert "decision:read" in out["skipped_reason"]
        assert not called, "无读权限时连候选树都不该去取"

    def test_admin_bypasses_perm_lookup(self, monkeypatch):
        monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_perm_codes",
                            lambda uid, ws=0: (_ for _ in ()).throw(AssertionError("admin 不该查码")))
        assert outlet.can_read(_identity(role="admin")) is True

    def test_perm_read_failure_is_fail_closed(self, monkeypatch):
        monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_perm_codes",
                            lambda uid, ws=0: (_ for _ in ()).throw(RuntimeError("db down")))
        assert outlet.can_read(_identity()) is False


# ── 4. done 事件与回放同源 ───────────────────────────────────────

class TestDoneAndReplayShareOneSource:
    def test_advice_key_absent_when_identity_missing(self):
        """错误/中止回合不带身份：不补键（前端无键不渲染），而不是补一句空原因。"""
        out = outlet.with_decision_advice({"reply": "x"}, None)
        assert "decision_advice" not in out

    def test_plain_qa_turn_carries_no_advice_block(self, no_fetch):
        """普通问答回合：items 与 reason 都空，前端拿到的就是“没这块”，不是一句不做判定。"""
        out = outlet.with_decision_advice({"reply": "官网地址是什么"}, _identity())
        block = out["decision_advice"]
        assert block["items"] == [] and block["skipped_reason"] == ""

    def test_advice_attached_to_done_payload(self, no_fetch, monkeypatch):
        monkeypatch.setattr(service, "active_trees", lambda ws=0: [_tree_row()])
        payload = {"reply": "激活率偏低",
                   "tool_calls": [_semantic_tool_call(["activation_rate"], [[0.41]])]}
        out = outlet.with_decision_advice(payload, _identity())
        assert out["decision_advice"]["items"][0]["verdict"] == "hit"
        assert out["reply"] == "激活率偏低"          # 原字段一个不动（只增不改）

    def test_replay_reads_instances_and_never_recomputes(self, monkeypatch):
        """回放只读已落的实例：重算要重新取数（拖慢老会话），且口径可能已变。"""
        seen = {}

        def fake_query(sql, params=None, fetchone=False):
            seen["sql"] = sql
            return [{"id": 9, "tree_key": "activation-risk", "tree_version": 3,
                     "trigger_type": "chat_turn", "conversation_id": 501,
                     "message_uuid": "msg-abc", "user_id": 7, "workspace_id": 301,
                     "site_code": "cn", "evidence": "[]", "hit_path": '["激活风险判定"]',
                     "advice_snapshot": "[]", "verdict": "hit", "verdict_reason": "",
                     "elapsed_ms": 12, "created_at": "2026-11-12 10:00:00"}]

        monkeypatch.setattr(service, "execute_query", fake_query)
        monkeypatch.setattr(service, "get_tree", lambda key: {"name": "设备激活风险"})
        monkeypatch.setattr(engine, "evaluate_tree",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("回放不得重算")))
        out = outlet.advice_for_messages(501, ["msg-abc", ""])
        assert list(out) == ["msg-abc"]
        assert out["msg-abc"][0]["tree_name"] == "设备激活风险"
        assert "message_uuid = %s" in seen["sql"]

    def test_missing_uuid_is_not_queried(self, monkeypatch):
        monkeypatch.setattr(service, "execute_query",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该打库")))
        assert outlet.advice_for_messages(501, ["", None]) == {}
