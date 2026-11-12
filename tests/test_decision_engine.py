"""决策树确定性求值引擎回归（离线，无 DB）。

锁四件事：
  1. **可复现**：同一棵树 + 同一组数值 → 同一结论（决策建议必须能被复盘）；
  2. **宁缺勿错**：指标业务名在字典确认不了就整棵树不出建议（不回抛半截结论），
     且原因/候选可操作（ontology-modeling §7 模糊只回抛候选）；
  3. **turn 模式不补取数**：本轮没取到的指标记 missing → verdict='partial' 并说明，
     绝不为了"看起来完整"去偷偷多打一次库（那会把首答拖慢还没人说清）；
  4. **草案不参与评估**：两段式第一段（draft）永不被消费。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.decision import dictionary as dic
from backend.modules.decision import engine
from backend.modules.decision.engine import EvalContext


# ── 夹具：一棵"设备激活率 + 在线状态"的树，字典离线可控 ────────────

METRICS = {
    # 带中文别名：现实字典就是"英文名 + 业务别名"的形状，条件里写哪个都必须能命中同一口径
    "activation_rate": dic.DictHit("metric", "activation_rate", "equipment", "dict_exact",
                                   aliases=["激活率"]),
    "activated_devices": dic.DictHit("metric", "activated_devices", "equipment", "dict_exact"),
    "近7天案例数": dic.DictHit("metric", "近7天案例数", "case", "dict_exact"),
}
DIMS = {
    "online_status": dic.DictHit("dimension", "online_status", "equipment", "dict_exact",
                                 aliases=["在线状态"]),
}


def _index():
    return {"metric": list(METRICS.values()), "dimension": list(DIMS.values())}


def _leaf(label, metric, comparator, value, **kw):
    advice = kw.pop("advice_key", "adv_low_activation")
    return {"type": "leaf", "label": label,
            "when": {"metric": metric, "comparator": comparator, "value": value, **kw},
            "advice_key": advice}


def _doc(*leaves):
    return {"root": {"type": "branch", "label": "激活风险判定", "op": "all",
                     "children": list(leaves)}}


def _tree(status="active", doc=None, sites=None, version=3):
    return {"id": 1, "tree_key": "activation-risk", "name": "设备激活风险",
            "status": status, "version": version, "is_active": 1,
            "tree_doc": doc if doc is not None else _doc(
                _leaf("激活率过低", "activation_rate", "<", 0.6, time_window="7d")),
            "required_metrics": ["activation_rate"],
            "apply_sites": sites or []}


def _full_ctx(**kw):
    base = dict(mode="full", user_id=7, username="alice", role="analyst",
                workspace_id=301, datasource_id=12, site_code="cn", dictionary=_index())
    base.update(kw)
    return EvalContext(**base)


# ── 1. 确定性求值 ────────────────────────────────────────────────

class TestDeterministicEvaluation:
    def _outcome_with(self, monkeypatch, measured, doc=None, status="active"):
        calls = []

        def fake_execute(query, policy):
            calls.append(query)
            return _result(["activation_rate"], [[measured]])

        monkeypatch.setattr("backend.semantics.execute.execute", fake_execute)
        tree = _tree(status=status, doc=doc)
        ctx = _full_ctx()
        return engine.evaluate_tree(tree, ctx), calls

    def test_same_input_same_conclusion(self, monkeypatch):
        """同树同数两次求值必须完全一致（无随机、无 LLM、无时钟依赖的判定部分）。"""
        first, _ = self._outcome_with(monkeypatch, 0.42)
        second, _ = self._outcome_with(monkeypatch, 0.42)
        assert first.verdict == second.verdict == "hit"
        assert first.hit_path == second.hit_path == ["激活风险判定", "激活率过低"]
        assert first.advice_keys == second.advice_keys == ["adv_low_activation"]

    def test_evidence_is_business_level_only(self, monkeypatch):
        """依据只回业务名/阈值/实测值，物理与内部标识一个都不出现（护栏 §7）。"""
        outcome, _ = self._outcome_with(monkeypatch, 0.42)
        row = outcome.evidence[0]
        assert row["metric"] == "activation_rate"
        assert row["comparator"] == "<"
        assert row["threshold"] == 0.6
        assert row["measured"] == 0.42
        assert row["time_window"] == "7d"
        joined = str(outcome.model_dump())
        for banned in ("datasource_id", "physical_table", "catalog_ref", "SELECT", "equipment_"):
            assert banned not in joined

    def test_query_goes_through_semantic_layer_only(self, monkeypatch):
        """取数必须落语义层声明式意图（object + metrics），不拼 SQL。"""
        _, calls = self._outcome_with(monkeypatch, 0.42)
        assert len(calls) == 1
        q = calls[0]
        assert q.object == "equipment"
        assert q.metrics == ["activation_rate"]
        assert q.time_window == "7d"
        assert not hasattr(q, "sql")

    @pytest.mark.parametrize("measured,comparator,threshold,expected", [
        (0.42, "<", 0.6, True),
        (0.6, "<", 0.6, False),
        (0.6, "<=", 0.6, True),
        (120, ">", 100, True),
        ("98%", ">", 100, False),        # 带单位的百分数字符串按数值比，不当字符串比
        ("98%", ">", 90, True),
        (5, "between", [1, 10], True),
        (5, "between", [10, 1], True),   # 阈值顺序写反也按区间理解，不判成"永不成立"
        ("offline", "=", "offline", True),
        (1, "in", [1, 2, 3], True),
        (9, "not_in", [1, 2, 3], True),
        (None, ">", 0, False),           # 无值不当"成立"
        ("文本", ">", 0, False),          # 不可比 → 不成立（并留 note）
    ])
    def test_comparator_matrix(self, monkeypatch, measured, comparator, threshold, expected):
        doc = _doc(_leaf("条件", "activation_rate", comparator, threshold))
        outcome, _ = self._outcome_with(monkeypatch, measured, doc=doc)
        assert (outcome.verdict == "hit") is expected

    def test_non_numeric_measurement_is_not_silently_true(self, monkeypatch):
        """实测值不可比时 verdict 不得是 hit，且依据里要能看出为什么。"""
        doc = _doc(_leaf("条件", "activation_rate", ">", 0.6))
        outcome, _ = self._outcome_with(monkeypatch, "无数据")
        assert outcome.verdict == "skipped"
        assert outcome.evidence[0]["measured"] == "无数据"
        assert outcome.evidence[0].get("note")
        assert outcome.evidence[0]["judged"] is False

    def test_unjudgeable_condition_is_not_reported_as_no_risk(self, monkeypatch):
        """判不了 ≠ 没风险（§三 禁止静默降级）：一条条件都判定不了的树必须落 skipped，
        原因里要点名是哪个条件、为什么，不得说成“已逐条判定，没有条件成立”。"""
        outcome, _ = self._outcome_with(monkeypatch, 0.42, doc=_doc(
            _leaf("条件", "activation_rate", ">", "不是数字的阈值")))
        assert outcome.verdict == "skipped"
        assert "没能判定" in outcome.reason and "没有风险" in outcome.reason
        assert "activation_rate" in outcome.reason

    def test_partly_judged_tree_is_partial(self, monkeypatch):
        """一条判成、一条取数被打回 → partial（不是 no_hit，也不是 hit），并点名没判的那条。"""
        def fake_execute(query, policy):
            if "activated_devices" in (query.metrics or []):
                raise RuntimeError("上游炸了")
            return _result(["activation_rate"], [[0.9]])

        monkeypatch.setattr("backend.semantics.execute.execute", fake_execute)
        doc = _doc(_leaf("激活率过高", "activation_rate", "<", 0.6),
                   _leaf("已激活台数", "activated_devices", ">", 10))
        outcome = engine.evaluate_tree(_tree(doc=doc), _full_ctx())
        assert outcome.verdict == "partial"
        assert "已激活台数" in outcome.reason and "activated_devices" in outcome.reason
        assert any(e["metric"] == "activation_rate" and "judged" not in e for e in outcome.evidence)

    def test_hit_path_lists_shared_branch_once(self, monkeypatch):
        """op=any 下两个叶子都命中：面包屑是「根 → 叶一 → 叶二」，不把根名重复两遍
        （写成「根/叶一/根/叶二」会让卡片路径看起来绕回了根，没人能一眼读懂）。"""
        def fake_execute(query, policy):
            metric = (query.metrics or [""])[0]
            return _result([metric], [[42 if metric == "activated_devices" else 0.42]])

        monkeypatch.setattr("backend.semantics.execute.execute", fake_execute)
        doc = {"root": {"type": "branch", "label": "激活风险判定", "op": "any", "children": [
            _leaf("激活率过低", "activation_rate", "<", 0.6),
            _leaf("已激活台数异常", "activated_devices", ">", 10)]}}
        outcome = engine.evaluate_tree(_tree(doc=doc), _full_ctx())
        assert outcome.verdict == "hit"
        assert outcome.hit_path == ["激活风险判定", "激活率过低", "已激活台数异常"]


# ── 2. 宁缺勿错：字典确认不了就不给建议 ───────────────────────────

class TestDictionaryGate:
    def test_fuzzy_metric_name_is_rejected_not_bound(self, monkeypatch):
        """包含式模糊只回抛候选：「案例」不能错绑到「近7天案例数」就出建议。"""
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: _result(["x"], [[1]]))
        doc = _doc(_leaf("条件", "案例", ">", 0))      # 字典里只有「近7天案例数」包含它
        outcome = engine.evaluate_tree(_tree(doc=doc), _full_ctx())
        assert outcome.verdict == "skipped"
        assert "无法在字典中确认" in outcome.reason
        unresolved = outcome.unresolved[0]
        assert unresolved["reason"] == "fuzzy_rejected"
        assert unresolved["candidates"] == ["近7天案例数"]

    def test_alias_hit_is_bound_to_authoritative_name(self, monkeypatch):
        """写别名要能命中，且命中后发给语义层的是字典权威名（不拿别名去编译）。"""
        calls = []
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda q, p: calls.append(q) or _result(["activation_rate"], [[0.1]]))
        doc = _doc(_leaf("条件", "激活率", "<", 0.6))
        outcome = engine.evaluate_tree(_tree(doc=doc), _full_ctx())
        assert outcome.verdict == "hit"
        assert calls[0].metrics == ["activation_rate"]

    def test_unknown_metric_never_reaches_datasource(self, monkeypatch):
        """口径核对不上时一个取数请求都不该发出去（省一次打库，也不给假依据）。"""
        called = []
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: called.append(1) or _result(["x"], [[1]]))
        doc = _doc(_leaf("条件", "不存在的指标", ">", 0))
        outcome = engine.evaluate_tree(_tree(doc=doc), _full_ctx())
        assert outcome.verdict == "skipped" and called == []

    def test_metric_without_object_is_reported_not_guessed(self, monkeypatch):
        """字典行没有归属对象 = 口径不可追溯，不能猜一张表去取数。"""
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: _result(["x"], [[1]]))
        idx = _index()
        idx["metric"].append(dic.DictHit("metric", "orphan_metric", "", "dict_exact"))
        doc = _doc(_leaf("条件", "orphan_metric", ">", 0))
        outcome = engine.evaluate_tree(_tree(doc=doc), _full_ctx(dictionary=idx))
        assert outcome.verdict == "skipped"
        assert outcome.unresolved[0]["reason"] == "no_object"

    def test_dictionary_load_failure_is_explicit(self, monkeypatch):
        """字典读不到必须显式报错，不能当成"没有指标所以没建议"（§1 不静默）。"""
        def boom(*a, **k):
            raise RuntimeError("元数据库连不上")
        monkeypatch.setattr(dic, "load_dictionary", boom)
        ctx = EvalContext(mode="full", user_id=1, workspace_id=1, datasource_id=12, dictionary=None)
        outcome = engine.evaluate_tree(_tree(), ctx)
        assert outcome.verdict == "skipped"
        assert "字典读取失败" in outcome.reason


# ── 3. turn 模式：只用本轮结果，不补取数 ──────────────────────────

class TestTurnMode:
    def _no_fetch(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("turn 模式不得取数（会把首答拖慢，且口径与本轮不一致）")
        monkeypatch.setattr("backend.semantics.execute.execute", boom)

    def test_hit_from_turn_result(self, monkeypatch):
        self._no_fetch(monkeypatch)
        ctx = EvalContext(mode="turn", user_id=7, workspace_id=301, dictionary=_index(),
                          turn_columns=["online_status", "activation_rate"],
                          turn_rows=[["online", 0.91], ["offline", 0.33]])
        doc = _doc(_leaf("离线设备激活率过低", "激活率", "<", 0.6,
                         dimension="在线状态", dimension_value="offline"))
        outcome = engine.evaluate_tree(_tree(doc=doc), ctx)
        assert outcome.verdict == "hit"
        assert outcome.evidence[0]["measured"] == 0.33
        # 依据回写的是字典权威名（不是用户写的那个别名），否则无法与指标口径对账
        assert outcome.evidence[0]["metric"] == "激活率"

    def test_metric_absent_becomes_partial_not_silent(self, monkeypatch):
        """本轮没取到这个指标 → partial + 点名缺哪个，并留出"完整评估"的口子。"""
        self._no_fetch(monkeypatch)
        ctx = EvalContext(mode="turn", user_id=7, workspace_id=301, dictionary=_index(),
                          turn_columns=["案例数"], turn_rows=[[12]])
        outcome = engine.evaluate_tree(_tree(), ctx)
        assert outcome.verdict == "partial"
        assert "activation_rate" in outcome.missing_metrics
        assert "未完整评估" in outcome.reason

    def test_empty_turn_result_is_partial(self, monkeypatch):
        self._no_fetch(monkeypatch)
        ctx = EvalContext(mode="turn", dictionary=_index(), turn_columns=[], turn_rows=[])
        outcome = engine.evaluate_tree(_tree(), ctx)
        assert outcome.verdict == "partial" and outcome.missing_metrics

    def test_multi_row_without_dimension_is_flagged(self, monkeypatch):
        """多维明细 + 条件没指定维度取值 → 按首行判定必须显式标注（不冒充整体口径）。"""
        self._no_fetch(monkeypatch)
        ctx = EvalContext(mode="turn", dictionary=_index(),
                          turn_columns=["在线状态", "activation_rate"],
                          turn_rows=[["online", 0.91], ["offline", 0.33]])
        outcome = engine.evaluate_tree(_tree(), ctx)
        assert outcome.verdict == "no_hit"          # 0.91 不 < 0.6
        assert "首行预览" in outcome.evidence[0]["note"]

    def test_record_shape_columns_are_dicts(self, monkeypatch):
        """会话消息里行常是 dict（records 形状），不能因为形状差异取不到值。"""
        self._no_fetch(monkeypatch)
        ctx = EvalContext(mode="turn", dictionary=_index(),
                          turn_columns=["activation_rate"],
                          turn_rows=[{"activation_rate": 0.1}])
        outcome = engine.evaluate_tree(_tree(), ctx)
        assert outcome.verdict == "hit" and outcome.evidence[0]["measured"] == 0.1


# ── 4. 生命周期与站点门 ───────────────────────────────────────────

class TestLifecycleAndSiteGate:
    def test_draft_never_evaluates(self, monkeypatch):
        """两段式第一段：草案即使写好了也不被消费（发布才生效）。"""
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: _result(["activation_rate"], [[0.1]]))
        outcome = engine.evaluate_tree(_tree(status="draft"), _full_ctx())
        assert outcome.verdict == "skipped"
        assert "只有已发布" in outcome.reason

    def test_retired_tree_skipped_with_reason(self, monkeypatch):
        outcome = engine.evaluate_tree(_tree(status="retired"), _full_ctx())
        assert outcome.verdict == "skipped" and "retired" in outcome.reason

    def test_site_not_applicable_is_skipped_not_hit(self, monkeypatch):
        """树只在新加坡站生效，本次在中国站 → 显式说明"未评估"，不是"没有风险"。"""
        ctx = _full_ctx(site_code="cn")
        outcome = engine.evaluate_tree(_tree(sites=[{"site_code": "sg", "datasource": "prd-sg"}]), ctx)
        assert outcome.verdict == "skipped"
        assert "['sg']" in outcome.reason and "cn" in outcome.reason

    def test_site_declared_matches_runs_normally(self, monkeypatch):
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: _result(["activation_rate"], [[0.1]]))
        outcome = engine.evaluate_tree(_tree(sites=[{"site_code": "cn"}]), _full_ctx())
        assert outcome.verdict == "hit"

    def test_no_site_declared_but_session_site_undecided(self, monkeypatch):
        ctx = _full_ctx(site_code="")
        outcome = engine.evaluate_tree(_tree(sites=[{"site_code": "cn"}]), ctx)
        assert outcome.verdict == "skipped" and "没有确定生效站点" in outcome.reason


# ── 5. 契约与损坏数据 ─────────────────────────────────────────────

class TestContract:
    def test_broken_json_doc_reports_not_empty_suggestion(self):
        doc, err = engine.load_doc("{not json")
        assert doc is None and "tree_doc" in err

    def test_unknown_field_rejected(self):
        """extra=forbid：有人塞 sql 字段必须拒收（语义层不吃裸 SQL 的同一条线）。"""
        doc, err = engine.load_doc({"root": {"type": "branch", "label": "r", "op": "all",
                                             "children": [{"type": "leaf", "label": "x",
                                                           "when": {"metric": "m", "comparator": ">",
                                                                    "value": 1, "sql": "SELECT 1"},
                                                           "advice_key": "a"}]}})
        assert doc is None and "Extra inputs" in err or "sql" in err

    def test_expression_like_metric_name_rejected(self):
        """条件里写聚合表达式 / 带引号的物理名 = 绕过字典口径，契约层直接拒。"""
        from pydantic import ValidationError
        from backend.modules.decision.models import DecisionCondition
        with pytest.raises(ValidationError):
            DecisionCondition(metric="COUNT(case_id)", comparator=">", value=1)
        with pytest.raises(ValidationError):
            DecisionCondition(metric="activation_rate", comparator=">", value=1,
                              dimension="`ds`")
        with pytest.raises(ValidationError):
            DecisionCondition(metric="datasource_id=12", comparator=">", value=1)

    def test_empty_advice_key_rejected(self):
        from pydantic import ValidationError
        from backend.modules.decision.models import DecisionLeaf
        with pytest.raises(ValidationError):
            DecisionLeaf(label="x", when={"metric": "m", "comparator": ">", "value": 1},
                         advice_key="")

    def test_collect_condition_names_dedupes(self):
        from backend.modules.decision.models import DecisionTreeDoc, collect_condition_names
        doc = DecisionTreeDoc.model_validate(_doc(
            _leaf("a", "activation_rate", "<", 0.6),
            _leaf("b", "activation_rate", ">", 0.9, dimension="online_status"),
            _leaf("c", "activated_devices", ">", 10, threshold_ref="activation_rate"),
        ))
        metrics, dims = collect_condition_names(doc)
        assert metrics == ["activation_rate", "activated_devices"]
        assert dims == ["online_status"]


# ── 6. 单棵树取数上限（防把配置错误的树当压测工具）────────────────

class TestFetchBudget:
    def test_fetch_budget_caps_queries(self, monkeypatch):
        calls = []
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda q, p: calls.append(q) or _result(["activation_rate"], [[0.1]]))
        leaves = [_leaf(f"条件{i}", "activation_rate", ">", 0) for i in range(40)]
        ctx = _full_ctx()
        outcome = engine.evaluate_tree(_tree(doc=_doc(*leaves)), ctx)
        assert len(calls) <= engine.MAX_FULL_EVAL_LEAVES + 1
        assert outcome.verdict in ("hit", "partial")


# ── 帮助：造一个 SemanticResult 形状的对象 ─────────────────────────

def _result(names, rows):
    class Col:
        def __init__(self, n):
            self.name = n

    class R:
        def __init__(self):
            self.columns = [Col(n) for n in names]
            self.rows = rows
    return R()
