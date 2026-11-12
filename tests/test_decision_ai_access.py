"""决策建议的 AI 侧接入（计划 §六 / P4）回归：提示词承诺与评测用例都要能落地。

锁四件事：

1. **只读就是只读**：`decision.*` 动作全部 `read_only=True` 且挂在 `decision:read` 上，
   清单里不存在任何改口径/发布的动作（不是"隐藏"，是根本没登记）；
2. **提示词不空承诺**：决策段里点名的工具名必须真的登记在 `FUNCTION_ACTION_SPECS`，
   且该段只在会话确实有功能能力工具时注入（与 `has_execute_sql` 同一防矛盾思路）；
3. **评测用例合法且不空转**：每条都过 `normalize_case` 契约，按声明模式跑适配器后
   确定性断言全部通过 —— 同时改掉回答里的实测值/改成硬凑建议时必须**判不过**
   （绿灯要能变红，才是真在把门）；
4. **不越层**：用例 suite/capability 都在既有白名单里，不新造分层口径。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.eval import contract
from backend.eval.adapters import llm as llm_adapter
from backend.eval.gate import CAPABILITIES
from backend.modules.mind.execution import function_tools, prompt_composer
from backend.scripts import seed_decision_eval_cases as seed


def _run(definition: dict) -> tuple[bool, str]:
    case = contract.normalize_case({**definition, "suite": seed.SUITE, "is_active": 1})
    observed = llm_adapter.run_case(case)
    return contract.check(case.suite, observed, case.expected)


# ── 1. AI 侧只有读动作 ────────────────────────────────────────

class TestReadOnlyActions:
    def test_decision_actions_exist_and_are_read_only(self):
        specs = {k: v for k, v in function_tools.FUNCTION_ACTION_SPECS.items()
                 if k.startswith("decision.")}
        assert set(specs) == {"decision.list", "decision.evaluate"}
        for key, spec in specs.items():
            assert spec["read_only"] is True, f"{key} 被登记成了写动作"
            assert spec["perm_code"] == "decision:read", f"{key} 挂在了别的权限码上"

    def test_no_write_action_is_registered_at_all(self):
        """改口径/发布不给 AI：不是把工具藏起来，是清单里根本没有这个名字。"""
        names = set(function_tools.FUNCTION_ACTION_SPECS)
        for banned in ("decision.save", "decision.update", "decision.publish",
                       "decision.delete", "decision.retire"):
            assert banned not in names

        tool_names = {v["tool_name"] for v in function_tools.FUNCTION_ACTION_SPECS.values()}
        for banned in ("save_decision_tree", "publish_decision_tree", "delete_decision_tree"):
            assert banned not in tool_names

    def test_ai_access_for_write_perms_is_none(self):
        """注册迁移里 manage/publish 的 ai_access 必须是 none（一处口径，不靠 handler 兜）。"""
        sql = open(os.path.join(os.path.dirname(__file__), "..", "docker", "mysql",
                                "decision_tree_migration.sql"), encoding="utf-8").read()
        for code in ("decision:manage", "decision:publish"):
            row = re.search(rf"'{code}'[^;]*", sql)
            assert row, f"迁移里没登记 {code}"
            assert re.search(r"'none'", row.group(0)), f"{code} 的 ai_access 不是 none"


# ── 2. 提示词段与实际能力一致 ─────────────────────────────────

class TestPromptRules:
    def test_named_tools_are_real(self):
        text = prompt_composer.DECISION_ADVICE_RULES
        tool_names = {v["tool_name"] for v in function_tools.FUNCTION_ACTION_SPECS.values()}
        for named in re.findall(r"`([a-z_]+)`", text):
            if named in ("list_decision_trees", "run_decision_evaluation", "list_platform_functions"):
                assert named in tool_names, f"提示词承诺了不存在的动作 {named}"

    def test_rules_cover_the_four_promises(self):
        """计划 §六 的四条口径都要在段里，缺一条就是提示词没兜住行为。"""
        text = prompt_composer.DECISION_ADVICE_RULES
        for needle in ("判定路径", "实测值", "暂无已发布的决策树覆盖", "不开放给 AI"):
            assert needle in text, f"决策段缺口径「{needle}」"

    def test_section_is_injected_only_with_function_tools(self):
        src = open(prompt_composer.__file__, encoding="utf-8").read()
        block = src.split("if has_function_tools:", 1)[1].split("\n    # 数据源黑盒", 1)[0]
        assert "sections.append(DECISION_ADVICE_RULES)" in block, \
            "决策段没有跟着功能能力工具注入（常驻注入会承诺没有的能力）"


# ── 3. 评测用例：契约合法 + 真能判 ────────────────────────────

class TestEvalCases:
    def test_all_cases_pass_contract_and_checker(self):
        assert seed.CASES, "用例定义不能空着"
        keys = [c["case_key"] for c in seed.CASES]
        assert len(keys) == len(set(keys)), "case_key 重复会让 create_case 抛冲突"
        for definition in seed.CASES:
            ok, reason = _run(definition)
            assert ok, f"{definition['case_key']} 判不过：{reason}"

    def test_capabilities_are_registered_groups(self):
        for definition in seed.CASES:
            assert definition["capability"] in CAPABILITIES

    def test_suite_is_llm_and_payload_mode_supported(self):
        # suite 是适配器路由键：这一批全走 llm（工具轨迹/出站脱敏），不新造分层
        assert seed.SUITE == "llm"
        for definition in seed.CASES:
            assert "suite" not in definition, "suite 由脚本统一给，定义里写两套会漂"
            assert (definition["payload"].get("mode") or "trace") in ("trace", "handler", "live")

    @pytest.mark.parametrize("mutate,why", [
        (lambda c: c["payload"].__setitem__("answer", "建议立刻停机检修，风险很高。"),
         "丢了实测值/阈值也判过 = 用例空转"),
        (lambda c: c["payload"]["trace"].pop(),
         "没调决策动作也判过 = 用例空转"),
        (lambda c: c["payload"]["trace"].__setitem__(
            -1, {"name": "run_decision_evaluation", "is_error": False, "is_write": False,
                 "text": "FROM t_case_records WHERE del_flag=0"}),
         "出站带物理表列没被判失败 = 泄露把门失效"),
    ])
    def test_checker_rejects_degraded_recordings(self, mutate, why):
        import copy

        definition = copy.deepcopy(seed.CASES[0])
        mutate(definition)
        ok, _reason = _run(definition)
        assert not ok, why

    def test_no_coverage_case_rejects_made_up_advice(self):
        """无覆盖那条：把"没有建议"说成"数据正常"必须判不过。"""
        import copy

        definition = copy.deepcopy(seed.CASES[1])
        definition["payload"]["answer"] = "该主题数据正常，没有风险。"
        ok, reason = _run(definition)
        assert not ok and reason

    def test_write_case_requires_the_write_action_to_be_absent(self):
        import copy

        definition = copy.deepcopy(seed.CASES[2])
        definition["payload"]["trace"].append(
            {"name": "publish_decision_tree", "is_error": False, "is_write": True, "text": "ok"})
        definition["payload"]["user_confirmed"] = False
        ok, reason = _run(definition)
        assert not ok and "publish_decision_tree" in reason


# ── 4. 分层与口径：不在评测侧另写判断 ─────────────────────────

class TestNoSecondJudgment:
    def test_cases_do_not_assert_physical_shape(self):
        """用例只断言业务口径与动作轨迹，不在评测层重跑一次条件比较。"""
        for definition in seed.CASES:
            assert set(definition["expected"]) <= contract.EXPECTED_KEYS["llm"]

    def test_dry_run_does_not_touch_db(self, monkeypatch):
        calls = []

        def boom(*a, **k):
            calls.append(a)
            raise AssertionError("dry-run 不该碰库")

        monkeypatch.setattr(llm_adapter, "LLM_CALLER", boom)
        assert seed.apply_or_dry(False) == len(seed.CASES)
        assert not calls
