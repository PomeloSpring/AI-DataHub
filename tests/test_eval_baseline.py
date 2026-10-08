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
