"""评测工程能力的 pytest 门禁（阶段出口：正确率不得回退）。

用例已改为 **DB 管理**（``adh_eval_cases``，页面可增删改查），因此本门禁依赖元数据库；
连不上时会**显式失败**而不是静默跳过 —— 门禁失效却报绿比没有门禁更危险。

门禁命令已从 ``python -m tests.eval.runner`` 迁到::

    venv/bin/python -m backend.eval.runner

（评测核心放在 backend/eval，供生产侧正向导入；不放 tests/ 供反向依赖。）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from backend.eval import store  # noqa: E402
from backend.eval.compare import compare_runs  # noqa: E402
from backend.eval.contract import CaseContractError, normalize_case  # noqa: E402
from backend.eval.gate import (  # noqa: E402
    CAPABILITIES, evaluate_buckets, evaluate_gate, gate_verdict, check_policy, GateError,
)
from backend.eval.runner import run_suite  # noqa: E402

# 基线门槛: 语义层编译集应全部可被确定性编译层正确解析。
# 未来若引入解析回退(如错误地把 fuzzy 当命中), 此门槛会拦住回归。
_ACCURACY_FLOOR = 1.0
_MIN_COMPILE_CASES = 40


def _compile_report():
    """跑语义层编译集（不落库），DB 不可用时把原因说清楚再失败。"""
    try:
        return run_suite("compile", persist=False)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"评测库不可用，门禁无法执行（不做静默跳过）: {type(exc).__name__}: {exc}")


class TestEvalBaseline:
    def test_case_count_min(self):
        rows = store.list_cases(suite="compile", active_only=True)
        assert len(rows) >= _MIN_COMPILE_CASES, f"语义层编译集应 ≥{_MIN_COMPILE_CASES} 条，实际 {len(rows)}"

    def test_accuracy_floor(self):
        rep = _compile_report()
        assert rep["accuracy"] >= _ACCURACY_FLOOR, (
            f"accuracy {rep['accuracy']:.1%} < floor {_ACCURACY_FLOOR:.0%}; "
            f"failures={[(f['case_key'], f['reason']) for f in rep['failures']]}")

    def test_no_invalid_cases(self):
        """用例本身不合法必须暴露（不能静默跳过充数）。"""
        rep = _compile_report()
        assert not rep["invalid_cases"], f"存在不合法用例: {rep['invalid_cases']}"

    def test_no_physical_leak_in_unresolved_hints(self):
        # 未解析用例的候选回抛文案不得含物理列名(已由 no_leak tag 覆盖, 此处兜底)
        rep = _compile_report()
        leak = [f for f in rep["failures"] if "物理" in f["reason"] and "隐藏" in f["reason"]]
        assert not leak, f"物理细节泄露: {leak}"


class TestRetrievalSuiteCoversRealChain:
    """锁住 pitfall：检索评测必须**真跑**检索策略，不得退回离线编译充数。

    历史缺陷：早期 runner 只 import planner/intent，完全不经过
    datamind/rag/strategies/graphrag.py，导致"eval 全绿"被误当成
    GraphRAG 有效性的证据。此处断言分桶里真的出现了策略名。
    """

    def test_retrieval_reports_strategy_bucket(self):
        rep = run_suite("retrieval", persist=False)
        assert rep["total"] > 0, "检索评测集不应为空"
        src = rep["by_source"]
        assert src, "检索评测必须产出来源分桶（否则说明没真跑检索链路）"
        known = {"graphrag", "ontology_traversal", "bm25", "hybrid", "local_hybrid_fallback", "qmind_hit"}
        assert known & set(src), (
            f"分桶里没有任何检索策略名，说明没走到真实检索链路: {sorted(src)}")

    def test_compile_bucket_is_not_retrieval_source(self):
        """两个分桶语义不同：compile 的是解析档位，不得被当成检索来源。"""
        rep = run_suite("compile", persist=False)
        assert rep["sources_label"].startswith("解析命中档位"), rep["sources_label"]


class TestLLMSuiteDeterministic:
    """LLM 功能评测的 passed 必须来自确定性断言，不由 LLM 评分决定。"""

    def test_llm_suite_runs_and_is_deterministic(self):
        rep = run_suite("llm", persist=False)
        assert rep["total"] > 0, "LLM 功能评测集不应为空"
        for r in rep["failures"]:
            assert r["reason"], f"失败用例必须给可诊断原因: {r['case_key']}"

    def test_score_never_affects_pass(self):
        """把 score 置为 0 也不得改变 passed（防止日后有人拿评分当判定）。"""
        from backend.eval.contract import check
        observed = {"tool_calls": [{"name": "list_reports", "args": {}, "is_error": False,
                                    "is_write": False, "text": "报表标题: 季度分析"}],
                    "answer": "已列出报表", "user_confirmed": True}
        expected = {"calls_include": ["list_reports"], "no_leak": True}
        ok, _ = check("llm", observed, expected)
        assert ok is True


def _policy(suite="retrieval", capability="", floor=0.9, min_cases=10, enabled=1):
    return {"suite": suite, "capability": capability, "floor_accuracy": floor,
            "min_cases": min_cases, "enabled": enabled}


def _scored(n_total, n_pass, capability="recall"):
    rows = []
    for i in range(n_total):
        rows.append({"case_key": f"c{i}", "capability": capability,
                     "passed": i < n_pass})
    return rows


class TestGateAbsoluteFloor:
    """钉住本次的真实事故：长期坏掉的用例不能因为“没回退”就报可以发布。

    实测过：retrieval 集 5/14（35.7%）却 baseline_ok=1，precheck 输出绿字
    “各评测集均未低于基线，可以发布”。原因在 compare_runs 只判相对回退。
    下列用例把“相对口径过了、绝对口径必须不过”固定下来。
    """

    def test_stably_broken_case_must_fail_floor_even_without_regression(self):
        """基线也坏、当前也坏：相对不产生回退，但绝对下限必须拦住。"""
        current = _scored(14, 5)
        baseline = _scored(14, 5)          # 一模一坏，历史长期如此
        cmp = compare_runs(current, baseline)
        assert cmp["baseline_ok"] is True, "相对口径确实看不出来（这就是假绿灯的成因）"

        res = evaluate_gate("retrieval", current, [_policy()])
        assert res["floor_ok"] is False
        assert any("正确率" in b["reason"] for b in res["breaches"]), res["breaches"]

    def test_min_cases_blocks_retiring_hard_cases(self):
        """把难用例退役掉使分母瘦下来：即使剩下全通过，也不得算覆盖度达标。"""
        res = evaluate_gate("retrieval", _scored(6, 6), [_policy(min_cases=10)])
        assert res["floor_ok"] is False
        assert any("覆盖度下界" in b["reason"] for b in res["breaches"]), res["breaches"]

    def test_missing_policy_means_gate_failure_not_pass(self):
        """未配门禁 = 门禁失效，不得当“通过”。"""
        res = evaluate_gate("retrieval", _scored(10, 10), [])
        assert res["floor_ok"] is False
        assert "未配置" in res["breaches"][0]["reason"]

    def test_capability_bucket_catches_what_the_average_hides(self):
        """整体平均能过 90%，但 governance 全挂——能力级下限不得被平均掩盖。"""
        scored = _scored(40, 40, "recall") + _scored(4, 0, "governance")
        overall = evaluate_gate("retrieval", scored, [_policy(min_cases=10)])
        assert overall["floor_ok"] is True, "整体桶 40/44=91% 达下限（平均看不出问题）"

        both = evaluate_gate("retrieval", scored,
                             [_policy(min_cases=10),
                              _policy(capability="governance", floor=1.0, min_cases=3)])
        assert both["floor_ok"] is False
        assert [b["capability"] for b in both["breaches"]] == ["governance"], both["breaches"]

    def test_evaluate_buckets_is_the_recompute_entry(self):
        """precheck 靠重算而非信存列：同样分桶结果两次算得一致结论。"""
        buckets = {"": {"passed": 5, "total": 14}, "recall": {"passed": 5, "total": 14}}
        first = evaluate_buckets("retrieval", buckets, [_policy()])
        second = evaluate_buckets("retrieval", buckets, [_policy()])
        assert first["floor_ok"] is False and first == second

    def test_verdict_matrix(self):
        assert gate_verdict(True, True) == "ok"
        assert gate_verdict(False, True) == "below_floor"
        assert gate_verdict(True, False) == "regression"
        assert gate_verdict(False, False) == "both"

    def test_check_policy_rejects_silly_thresholds(self):
        for bad in ({"suite": "llm", "floor_accuracy": 1.5},
                    {"suite": "llm", "floor_accuracy": "abc"},
                    {"suite": "llm", "capability": "nope", "floor_accuracy": 1.0},
                    {"suite": "llm", "min_cases": -1},
                    {"floor_accuracy": 1.0}):
            with pytest.raises(GateError):
                check_policy(bad)


class TestCaseStatusSemantics:
    """三态语义必须硬：retired 不入分母但可见；退役/缺陷没原因就拒收。"""

    def _row(self, **over):
        row = {"case_key": "k", "suite": "compile", "question": "q",
               "capability": "resolution", "case_status": "active", "status_note": "",
               "payload": {}, "expected": {}, "tags": "", "is_active": 1, "id": 1}
        row.update(over)
        return row

    def test_retired_not_scored_active_and_defect_scored(self):
        assert normalize_case(self._row()).scored is True
        assert normalize_case(self._row(case_status="known_defect",
                                        status_note="图种子抽取契约缺失")).scored is True
        assert normalize_case(self._row(case_status="retired",
                                        status_note="针对已废弃形态")).scored is False

    def test_non_active_status_without_reason_is_rejected(self):
        for st in ("retired", "known_defect"):
            with pytest.raises(CaseContractError, match="status_note"):
                normalize_case(self._row(case_status=st, status_note=""))

    def test_capability_is_required(self):
        """未登记契约组直接拒收：拿空值混过去会让覆盖矩阵把所有格误报为已覆盖。"""
        for bad in ("", "not_a_capability"):
            with pytest.raises(CaseContractError, match="能力契约"):
                normalize_case(self._row(capability=bad))

    def test_every_capability_has_a_label(self):
        from backend.eval.gate import CAPABILITY_LABELS
        assert set(CAPABILITY_LABELS) == set(CAPABILITIES), "契约组与中文标签不一致"


class TestRunnerDenominatorWiring:
    """runner 的分母/退役/门禁装配。

    这里用**桩适配器**而不是真跑被测链路：要验的是“retired 不进分母、退役可见、
    双口径各自落字段”这套管线，不是平台当前能力对不对。真跑评测集只会把
    被测链路的波动混进门禁逻辑的回归里，出问题时分不清是哪个坏了。
    """

    class _StubAdapter:
        @staticmethod
        def run_case(case):
            # 带 intent_rejected 期望的用例判失败（契约层会给出可诊断原因）
            return {"bound": True, "sql": "SELECT 1 LIMIT 10",
                    "warnings": [], "sources": {}, "intent_rejected": False}

    @staticmethod
    def _row(key, cap, status, note="", suite="compile"):
        expected = {"intent_rejected": True} if key.startswith("bad") else {}
        return {"id": abs(hash(key)) % 10000, "case_key": key, "suite": suite,
                "question": f"{key} q", "capability": cap, "case_status": status,
                "status_note": note, "tags": "", "payload": {}, "expected": expected,
                "note": "", "is_active": 1}

    def test_retired_excluded_but_visible_and_gate_reported(self, monkeypatch):
        from backend.eval import runner

        rows = ([self._row(f"good{i}", "resolution", "active") for i in range(6)]
                + [self._row(f"bad{i}", "governance", "known_defect",
                             note="图种子抽取契约缺失") for i in range(2)]
                + [self._row(f"gone{i}", "routing", "retired",
                             note="针对已废弃形态") for i in range(3)])
        monkeypatch.setattr(runner, "_adapter", lambda suite: self._StubAdapter)
        monkeypatch.setattr(store, "list_cases", lambda **kw: rows)
        monkeypatch.setattr(store, "list_gate_policies",
                            lambda: [_policy(suite="compile", floor=1.0, min_cases=5)])

        rep = runner.run_suite("compile", persist=False)

        # 分母 = active + known_defect，retired 不跟
        assert rep["total"] == 8, rep["total"]
        assert rep["active_count"] == 8
        assert rep["known_defect_count"] == 2
        assert len(rep["retired"]) == 3
        assert all(r["note"] for r in rep["retired"]), "退役必须带原因"
        # 2 条失败全部来自 known_defect：它们继续拖累下限，而不是被“退役”洗掉
        assert {f["case_key"] for f in rep["failures"]} == {"bad0", "bad1"}
        assert rep["accuracy"] == 0.75
        assert rep["floor_ok"] is False, "known_defect 占分母 25%，1.0 下限必须拦住"
        assert rep["release_ok"] is False
        assert rep["gate_verdict"] in ("below_floor", "both"), rep["gate_verdict"]
        assert rep["by_capability"]["governance"]["total"] == 2
        # 3 条 routing 全在 retired 里：它们不得出现在分母，也不得让人以为 routing 已被覆盖
        assert "routing" not in rep["by_capability"], rep["by_capability"]

    def test_coverage_floor_blocks_thinning_the_suite(self, monkeypatch):
        """把分母做小（退役/删用例）即使全通过也不得算达标。"""
        from backend.eval import runner
        rows = [self._row(f"good{i}", "resolution", "active") for i in range(3)] \
            + [self._row(f"gone{i}", "routing", "retired", note="形态已废弃") for i in range(5)]
        monkeypatch.setattr(runner, "_adapter", lambda suite: self._StubAdapter)
        monkeypatch.setattr(store, "list_cases", lambda **kw: rows)
        monkeypatch.setattr(store, "list_gate_policies",
                            lambda: [_policy(suite="compile", floor=1.0, min_cases=5)])

        rep = runner.run_suite("compile", persist=False)
        assert rep["accuracy"] == 1.0, "剩下 3 条全过，通过率好看"
        assert rep["baseline_ok"] is True, "无基线时相对口径也不拦"
        assert rep["floor_ok"] is False
        assert any("覆盖度下界" in b["reason"] for b in rep["gate"]["breaches"])
        assert rep["release_ok"] is False
