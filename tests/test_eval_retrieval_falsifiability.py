"""L3 检索层断言的可证伪性（离线，无 DB）。

要拦的具体问题（本轮 L2/L3 实测抓到）:
    GraphRagStrategy 的 agentic grounding 已退役，现在只对**调用方给定的候选表**
    做 by-name 元数据回填；`keywords` 只是候选表内部的排序线索。因此不传
    `selected_tables`/`target_tables` 时，结果**结构上必然为空**。

    而旧检索用例大多长这样::

        payload  {"keywords": ["数据","分析"], "datasource_id": 0}
        expected {"max_results": 50}          # 或 {"hit": false}

    这两类断言在空结果上恒真 —— 于是"检索链是否真的在工作"完全无法从正确率看出：
    本轮 retrieval 集 5/6 通过，实际整条链对所有探针都返回 0 条。

本文件锁三件事：空结果不得被算作证明、`min_results` 可证伪、以及
元数据检索的物理表列不该被误判成泄露（那会让"链路越正确越像泄露"）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from backend.eval.contract import check_retrieval  # noqa: E402
from backend.eval.gate import POSITIVE_ASSERTIONS, weak_passes  # noqa: E402


def _items(*names):
    return [{"kind": "table", "name": n, "text": ""} for n in names]


class TestMinResultsIsFalsifiable:
    def test_zero_items_fail_min_results(self):
        """`min_results` 是给"命中类"用例提供可证伪下界的键。"""
        ok, reason = check_retrieval({"items": []}, {"min_results": 1})
        assert ok is False
        assert "空结果不能算通过" in reason

    def test_enough_items_pass(self):
        ok, _ = check_retrieval({"items": _items("orders", "users")}, {"min_results": 2})
        assert ok is True

    def test_max_results_alone_still_passes_on_empty(self):
        """保留旧行为（max_results 只卡上界），但下面会把它标成弱断言。"""
        ok, _ = check_retrieval({"items": []}, {"max_results": 50})
        assert ok is True


class TestPhysicalVsCredentialLeak:
    def test_no_physical_rejects_real_table_names(self):
        ok, reason = check_retrieval(
            {"items": _items("t_case_records")}, {"no_physical": True})
        assert ok is False and "physical_token" in reason

    def test_no_credential_leak_allows_legitimate_metadata(self):
        """元数据检索对可写 SQL 的消费方**就该**返回真实表列。

        用 no_physical 卡它会得出"检索越正确越像泄露"的错误结论；
        no_credential_leak 只卡凭据/地址/报错栈。
        """
        items = _items("t_case_records")
        ok, reason = check_retrieval({"items": items}, {"no_credential_leak": True})
        assert ok is True, reason

    def test_no_credential_leak_still_catches_connection_string(self):
        items = [{"kind": "table", "name": "orders",
                  "text": "jdbc:mysql://10.0.0.8:3306/stardb"}]
        ok, reason = check_retrieval({"items": items}, {"no_credential_leak": True})
        assert ok is False and "value_pattern" in reason


class TestVacuousPassDetection:
    """`weak_passes` 只作可诊断提醒：不判失败，但绝不能不报。"""

    def test_empty_result_with_only_negative_assertion_is_weak(self):
        results = [{"case_key": "ret_bounded", "capability": "recall", "passed": True,
                    "expected": {"max_results": 50},
                    "item_count": 0}]
        weak = weak_passes("retrieval", results)
        assert [w["case_key"] for w in weak] == ["ret_bounded"]
        assert "不能证明能力在用" in weak[0]["reason"]

    def test_positive_assertion_makes_a_pass_meaningful(self):
        results = [{"case_key": "ok_case", "capability": "recall", "passed": True,
                    "expected": {"hit": True}, "item_count": 12}]
        assert weak_passes("retrieval", results) == []

    def test_non_empty_result_is_not_weak(self):
        results = [{"case_key": "c", "capability": "recall", "passed": True,
                    "expected": {"max_results": 50}, "item_count": 68}]
        assert weak_passes("retrieval", results) == []

    def test_layer_without_item_count_is_not_judged(self):
        """没记录规模数的层不下结论 —— 宁可漏报也不误报成"你有弱用例"。"""
        results = [{"case_key": "c", "capability": "resolution", "passed": True,
                    "expected": {"error_contains": "x"}}]
        assert weak_passes("compile", results) == []

    def test_failed_cases_are_not_reported_as_weak(self):
        """失败就是失败，不该同时被算进"弱通过"里混淆两类问题。"""
        results = [{"case_key": "c", "capability": "recall", "passed": False,
                    "expected": {"max_results": 1}, "item_count": 0}]
        assert weak_passes("retrieval", results) == []

    def test_every_layer_declares_its_positive_keys(self):
        from backend.eval.contract import SUITES
        assert set(POSITIVE_ASSERTIONS) == set(SUITES), \
            "新增层必须同时声明正向断言键，否则该层的空结果不会被发现"
