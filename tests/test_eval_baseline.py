"""Golden-question 评测的 pytest 门禁包装(阶段出口: 正确率不得回退)。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.eval.runner import run, load_cases  # noqa: E402

# 基线门槛: 当前用例全部可被确定性编译层正确解析。
# 未来若引入解析回退(如错误地把 fuzzy 当命中), 此门槛会拦住回归。
_ACCURACY_FLOOR = 1.0


class TestEvalBaseline:
    def test_case_count_min(self):
        assert len(load_cases()) >= 40, "评测集应 ≥40 条"

    def test_accuracy_floor(self):
        rep = run()
        assert rep["accuracy"] >= _ACCURACY_FLOOR, (
            f"accuracy {rep['accuracy']:.1%} < floor {_ACCURACY_FLOOR:.0%}; "
            f"failures={[f['id'] for f in rep['failures']]}")

    def test_no_physical_leak_in_unresolved_hints(self):
        # 未解析用例的候选回抛文案不得含物理列名(已由 no_leak tag 覆盖, 此处兜底)
        rep = run()
        leak = [f for f in rep["failures"] if "leaked physical" in f["reason"]]
        assert not leak, f"物理细节泄露: {leak}"
