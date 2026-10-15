"""前置闸门：请求过的字段被静默丢弃必须拒绝执行（禁止把问题换成另一个再作答）。

缺陷原型（L2 真实语义评测集实测抓到）:
    用户问「按状态分布看案例数量」，`状态分布` 解析不通 → planner 把它**丢掉**并
    产出一条不带任何分组的总量 SQL；旧 `_gate_preflight` 只在「一个字段都没解析出来」
    时才拦，部分丢弃照放行。于是系统看起来成功，实际回答了另一个问题。

    这比报错危害更大：`no-silent-degradation` §1 与 `ontology-modeling` §7
    （包含式模糊只回抛候选、绝不静默绑定）都要求显式暴露。口径同时与
    `viz.dataset_service.compile_from_semantic` 的「未解析即拒」对齐。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.semantics.gates import _dropped_terms, _gate_preflight  # noqa: E402


def _q(**over):
    base = {"object": "case", "metrics": ["案例数量"], "dimensions": ["状态分布"],
            "filters": [], "order": [], "limit": 10, "time_column": None,
            "user_id": 7, "workspace_id": 0, "datasource_id": 1, "dry_run": False}
    base.update(over)
    return SimpleNamespace(**base)


def _binding():
    return SimpleNamespace(sync_state="synced", bind_kind="primary",
                           physical_table="t_case_records", datasource_id=1,
                           access=SimpleNamespace(permission_tokens=[]))


def _planned(*, sql="SELECT COUNT(*) AS `案例数量` FROM t LIMIT 10",
             unresolved=None, warnings=None, provenance_extra=None):
    prov = {"unresolved_terms": unresolved or [], "requested_metrics": ["案例数量"]}
    prov.update(provenance_extra or {})
    return SimpleNamespace(sql=sql, dialect="mysql", resolved_tables=["t_case_records"],
                           warnings=warnings or [], provenance=prov)


class TestDroppedTermsExtraction:
    def test_structured_terms_are_normalized_with_kind_and_candidates(self):
        planned = _planned(unresolved=[
            {"term": "状态分布", "type": "dimension", "reason": "fuzzy_rejected",
             "candidates": ["案例状态", "拆牙状态", "x", "y", "z", "w"]},
        ])
        got = _dropped_terms(planned)
        assert got == [{"name": "状态分布", "kind": "dimension",
                        "reason": "fuzzy_rejected",
                        # 候选截断到 5 条：回抛给模型/用户的清单要可操作，不是清单轰炸
                        "candidates": ["案例状态", "拆牙状态", "x", "y", "z"]}], got

    def test_legacy_plain_string_rows_still_count_as_dropped(self):
        assert _dropped_terms(_planned(unresolved=["未知词"]))[0]["name"] == "未知词"

    def test_blank_term_is_ignored(self):
        assert _dropped_terms(_planned(unresolved=[{"term": "  "}, None, ""])) == []

    def test_missing_provenance_returns_empty_not_crash(self):
        """拿不到信号时不得"一律拒"把主链路堵死——但也绝不把它当成已验证。"""
        bare = SimpleNamespace(sql="SELECT 1", dialect="mysql", resolved_tables=[],
                               warnings=[], provenance=None)
        assert _dropped_terms(bare) == []


class TestPreflightRefusesSilentDrop:
    def test_partial_drop_is_refused_even_when_metrics_resolved(self):
        """核心回归点：解析成功了一部分，也不能拿去掉条件的结果作答。"""
        o = _gate_preflight(_q(), _binding(),
                            _planned(unresolved=[{"term": "状态分布", "type": "dimension",
                                                  "reason": "unresolved", "candidates": []}]))
        assert o.passed is False
        assert "状态分布" in o.reason and "dimension" in o.reason
        assert o.detail["unresolved"][0]["name"] == "状态分布"

    def test_refusal_carries_business_name_candidates_only(self):
        """回抛的必须是业务名（护栏 §7：不得含物理表/列名）。"""
        o = _gate_preflight(_q(), _binding(), _planned(
            unresolved=[{"term": "状态分布", "type": "dimension", "reason": "unresolved",
                         "candidates": ["案例状态"]}],
            provenance_extra={"available_dimensions": ["案例状态", "就诊医院"]}))
        blob = o.reason + str(o.detail)
        for phys in ("t_case_records", "case_status", "company_name", "create_time"):
            assert phys not in blob, f"拒绝文案泄露物理细节: {phys}"

    def test_dry_run_is_not_exempt(self):
        """dry_run 正是"先看形状对不对"，静默丢条件在预览阶段同样必须拦。"""
        o = _gate_preflight(_q(dry_run=True), _binding(),
                            _planned(unresolved=[{"term": "利润率", "type": "metric"}]))
        assert o.passed is False

    def test_clean_plan_still_passes(self):
        o = _gate_preflight(_q(dimensions=[]), _binding(), _planned(unresolved=[]))
        assert o.passed is True, o.reason

    def test_empty_sql_still_refused_first(self):
        """护栏已拒的空 SQL 不能被新分支盖掉原因。"""
        o = _gate_preflight(_q(), _binding(), _planned(sql=""))
        assert o.passed is False and "护栏拒绝" in o.reason
