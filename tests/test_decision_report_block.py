"""报告侧「决策建议」区块（计划 §四出口 4）的回归。

锁四件事：

1. **读实例不重算**：报告可能在几天后归档，重算拿的是新口径/新数据，存进报告的
   就不是"当时那条建议"（与 Chat 回放同一条例外、同一份来源 `adh_decision_runs`）；
2. **只含业务名**：区块里出现指标业务名/阈值/实测值/建议正文，绝不出现物理表列名、
   `datasource_id`、SQL —— 报告正文随后还会再过一道 `outbound_guard`；
3. **不静默**：没实例 → 空串（该轮确实没建议）；读取失败 → 显式一句说明，不能把
   "没读到"当成"没建议"（no-silent-degradation §1）；
4. **分层契约**：viz 不开 → decision 的 L2 横向 import，一律经 `core/task_runtime` 回调表。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.core import task_runtime
from backend.modules.decision import outlet, service


_RUNS = [{
    "id": 9, "tree_key": "accuracy-risk", "tree_name": "设备精度风险判定", "tree_version": 3,
    "trigger_type": "chat_turn", "verdict": "hit", "verdict_reason": "",
    "hit_path": ["精度风险判定", "校准超差"],
    "advice_snapshot": [{"advice_key": "adv_recalibrate", "action": "安排一次现场校准",
                         "rationale": "连续 7 日偏差高于标称", "severity": "high",
                         "kb_refs": ["calibration-sop"]}],
    "evidence": [{"metric": "扫描精度偏差均值", "comparator": ">=", "threshold": 0.05,
                  "measured": 0.08, "unit": "mm"},
                 {"metric": "在线率", "comparator": ">=", "threshold": 0.9,
                  "measured": None, "judged": False, "note": "取数被语义层拒绝"}],
}]


# ── 1. 区块渲染 ───────────────────────────────────────────────

class TestReportBlockMarkdown:
    def test_hit_renders_path_advice_and_evidence(self, monkeypatch):
        monkeypatch.setattr(service, "find_turn_runs", lambda cid, uuid: [dict(_RUNS[0])])
        md = outlet.report_block_markdown(7, "m-1")
        assert md.startswith("## 决策建议")
        assert "设备精度风险判定" in md and "命中" in md
        assert "精度风险判定 → 校准超差" in md
        assert "安排一次现场校准" in md and "严重级：高" in md
        assert "连续 7 日偏差高于标称" in md
        assert "calibration-sop" in md
        assert "扫描精度偏差均值 >= 0.05 mm，实测 0.08 mm" in md
        assert "快照" in md, "要写明是当时那条，不是当前口径"

    def test_unjudged_evidence_says_it_was_not_judged(self, monkeypatch):
        monkeypatch.setattr(service, "find_turn_runs", lambda cid, uuid: [dict(_RUNS[0])])
        md = outlet.report_block_markdown(7, "m-1")
        assert "在线率 未能判定（取数被语义层拒绝）" in md

    def test_non_hit_keeps_reason(self, monkeypatch):
        row = dict(_RUNS[0], verdict="partial", verdict_reason="本轮取数未覆盖这些指标：['在线率']")
        monkeypatch.setattr(service, "find_turn_runs", lambda cid, uuid: [row])
        md = outlet.report_block_markdown(7, "m-1")
        assert "未完整评估" in md
        assert "本轮取数未覆盖这些指标" in md

    def test_no_run_is_empty_not_a_fabricated_section(self, monkeypatch):
        monkeypatch.setattr(service, "find_turn_runs", lambda cid, uuid: [])
        assert outlet.report_block_markdown(7, "m-1") == ""

    def test_read_failure_is_stated_loudly(self, monkeypatch):
        def boom(cid, uuid):
            raise RuntimeError("库连接断了")

        monkeypatch.setattr(service, "find_turn_runs", boom)
        md = outlet.report_block_markdown(7, "m-1")
        assert "未能随正文归档" in md and "RuntimeError" in md

    def test_block_carries_no_physical_identifiers(self, monkeypatch):
        """区块里的脏字段（物理名/id）就是归档时要炸的东西 —— 用 outbound_guard 自查。"""
        from backend.modules.mind.execution import outbound_guard

        dirty = [dict(_RUNS[0], evidence=[dict(_RUNS[0]["evidence"][0],
                                               physical_table="adh_scan_log", datasource_id=12)])]
        monkeypatch.setattr(service, "find_turn_runs", lambda cid, uuid: dirty)
        md = outlet.report_block_markdown(7, "m-1")
        # 渲染层根本不读这些键，所以它们不会出现在正文里
        assert not outbound_guard.find_sensitive_hits(md), "报告建议区块泄露了内部信息"


# ── 2. 报告正文接线（草稿与直接归档同一口径）──────────────────

class TestReportWiring:
    def test_render_appends_block_after_result_table(self):
        from backend.modules.viz.services import report_service as reports

        md = reports.render_conversation_report(
            answer="本轮精度偏差偏高",
            result={"columns": [{"name": "指标"}], "rows": [["扫描精度偏差均值"]]},
            advice_block="## 决策建议\n\n- **设备精度风险判定**：命中")
        assert md.index("查询结果") < md.index("## 决策建议")
        assert "安排" not in md  # 区块内容原样来自 decision 侧，不在这儿二次加工

    def test_empty_block_adds_nothing(self):
        from backend.modules.viz.services import report_service as reports

        md = reports.render_conversation_report(answer="只有正文", advice_block="  ")
        assert "决策建议" not in md

    def test_block_goes_through_callback_table_not_direct_import(self):
        """分层契约 §0：viz 不得直接 import decision；跨模块只走回调表白名单。"""
        assert "decision.report_block" in task_runtime._CALLBACKS
        src = os.path.join(os.path.dirname(__file__), "..",
                           "backend", "modules", "viz", "services", "report_service.py")
        code = open(src, encoding="utf-8").read()
        assert not re.search(r"^\s*(?:from|import)\s+backend\.modules\.decision",
                             code, re.M), "report_service 里出现了 viz→decision 的 L2 横向 import"

    def test_decision_advice_block_uses_registered_callback(self, monkeypatch):
        from backend.modules.viz.services import report_service as reports

        seen = {}

        def fake_call(name, *args, **kwargs):
            seen["name"], seen["args"] = name, args
            return "## 决策建议\n\n- 命中"

        monkeypatch.setattr(task_runtime, "call", fake_call)
        md = reports.decision_advice_block(7, "m-1")
        assert seen["name"] == "decision.report_block" and seen["args"] == (7, "m-1")
        assert md.startswith("## 决策建议")

    def test_missing_callback_does_not_break_archiving_but_says_so(self, monkeypatch):
        from backend.modules.viz.services import report_service as reports

        def boom(name, *a, **k):
            raise KeyError(name)

        monkeypatch.setattr(task_runtime, "call", boom)
        md = reports.decision_advice_block(7, "m-1")
        assert "未能随正文归档" in md, "读失败必须可见，不能沉默地少一段"
        assert reports.decision_advice_block(0, "m-1") == "", "没有会话标识时无需读取也无需报错"


# ── 3. 严重级/结论的服务端文案只有一处 ────────────────────────

class TestSharedLabels:
    def test_labels_are_single_source(self):
        from backend.modules.decision import models

        assert models.SEVERITY_LABELS == {"low": "低", "medium": "中",
                                          "high": "高", "critical": "紧急"}
        assert set(models.VERDICT_LABELS) == {"hit", "no_hit", "partial", "skipped"}

    @pytest.mark.parametrize("verdict,text", [
        ("hit", "命中"), ("no_hit", "未命中"), ("partial", "未完整评估"), ("skipped", "未评估")])
    def test_every_verdict_prints_a_label(self, monkeypatch, verdict, text):
        row = dict(_RUNS[0], verdict=verdict, verdict_reason=f"{verdict} 的原因")
        monkeypatch.setattr(service, "find_turn_runs", lambda cid, uuid: [row])
        md = outlet.report_block_markdown(7, "m-1")
        assert text in md
        # 非 hit 必须带原因（“没建议”与“没能给建议”是两件事）；hit 无原因可说
        assert (("说明：" in md) == (verdict != "hit"))
