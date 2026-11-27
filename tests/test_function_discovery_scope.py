"""发现清单能力域投影契约（AS-BOT 工具误选收敛第①步）。

锁四件事：

1. 清单项携带 ``domain``（系统能力 vs 数据分析等语义分组）与 ``scenarios``/``
   rules_hint``（从系统本体反查投影），LLM 第一跳按域缩小选择面；
2. ``domain`` 入参过滤只回该域能力面；
3. 场景/规则反查失败必须显式标注 ``context_error``，不得静默空清单装作"无约束"；
4. 全局规则（enforcement.action="*"）不逐动作下发（噪声）。
"""

from __future__ import annotations

import json

import pytest

from backend.modules.mind.execution import capability_contracts as contracts
from backend.modules.mind.execution import function_tools as ft


def _payload(result) -> dict:
    return json.loads(result["content"][0]["text"])


class _FakePolicy:
    function_names = ("list_reports", "list_datasets")
    unavailable = {}


def test_action_domain_mapping_is_deterministic():
    assert contracts.action_domain("ontology.save_model") == "modeling"
    assert contracts.action_domain("dataset.list") == "data"
    assert contracts.action_domain("report.generate") == "data"
    assert contracts.action_domain("content_draft.create") == "content"
    assert contracts.action_domain("evolution_candidate.adopt") == "governance"
    assert contracts.action_domain("capability.run_flow") == "operations"
    assert contracts.action_domain("no_such.thing") == "general"


def test_discovery_items_carry_domain_and_context(monkeypatch):
    monkeypatch.setattr(ft, "_live_function_policy",
                        lambda: (object(), _FakePolicy()))
    monkeypatch.setattr(contracts, "action_context_index", lambda refresh=False: {
        "report.list": {"scenarios": ["例行汇报"], "rules": ["报告发布前须过质量检核"]},
    })
    data = _payload(ft._list_platform_functions({}))
    by_fn = {i["function"]: i for i in data["functions"]}
    assert set(by_fn) == {"list_reports", "list_datasets"}
    assert by_fn["list_reports"]["domain"] == "data"
    assert by_fn["list_reports"]["scenarios"] == ["例行汇报"]
    assert by_fn["list_reports"]["rules_hint"] == ["报告发布前须过质量检核"]
    assert by_fn["list_datasets"]["scenarios"] == []
    # 分组排序：按 (domain, function)，LLM 扫清单即见域能聚块
    assert data["functions"] == sorted(
        data["functions"], key=lambda x: (x["domain"], x["function"]))


def test_domain_filter_narrows_to_one_capability_face(monkeypatch):
    monkeypatch.setattr(ft, "_live_function_policy",
                        lambda: (object(), _FakePolicy()))
    monkeypatch.setattr(contracts, "action_context_index", lambda refresh=False: {})
    all_items = _payload(ft._list_platform_functions({}))["functions"]
    assert {i["domain"] for i in all_items} == {"data"}
    only_data = _payload(ft._list_platform_functions({"domain": "data"}))["functions"]
    assert only_data == all_items
    none = _payload(ft._list_platform_functions({"domain": "content"}))["functions"]
    assert none == []


def test_context_index_failure_is_explicit_not_silent(monkeypatch):
    monkeypatch.setattr(ft, "_live_function_policy",
                        lambda: (object(), _FakePolicy()))

    def _boom(refresh=False):
        raise RuntimeError("系统本体不可用")

    monkeypatch.setattr(contracts, "action_context_index", _boom)
    data = _payload(ft._list_platform_functions({}))
    # 清单仍可用（域/参数不受影响），但缺失原因必须显式可诊断
    assert data["functions"], "清单本体不得因反查失败而消失"
    assert "系统本体不可用" in data.get("context_error", "")
    for item in data["functions"]:
        assert item["scenarios"] == [] and item["rules_hint"] == []


def test_global_rules_not_injected_per_action():
    """enforcement.action="*" 的全局规则不逐动作下发，只留动作专属约束。"""
    idx = contracts.action_context_index(refresh=True)
    assert idx, "系统本体应能构建反查索引"
    for action_key, ctx in idx.items():
        for rule in ctx["rules"]:
            assert "必须持对应权限码且 ai_access=write" not in rule, (
                f"{action_key} 泄入了全局规则")


def test_context_index_projects_scenarios_from_ontology():
    idx = contracts.action_context_index(refresh=True)
    # 试点场景 ops_content_publish（内容草稿到发布）的步骤动作必须出现在反查里
    assert "内容草稿到发布" in idx["content_draft.create"]["scenarios"], (
        "uses_actions 反查应把场景标题挂到对应动作上")
