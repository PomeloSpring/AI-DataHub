"""M2/M3/M4 建模位（行为/规则/场景）校验与上云脱敏回归。

五层口径：M1 对象 / M2 行为(objects[].actions[]) / M3 规则(rules[]) /
M4 场景(scenarios[]) / M_Metric 指标。行为/规则/场景全部是 canonical 内嵌声明，
AS_BOT 动作注册与角色动作矩阵是其派生投影（单一可编辑源）。

锁住的行为：
1. 引用一致性（§4 宁阻断勿悬空）：场景/权限规则引用不存在的对象/行为/规则 → error；
2. action_key 全局唯一（它是审批通道注册键）；risk/level/type 枚举越界 → error；
3. 写动作缺风险声明/审批要求 → warn（建模建议，不阻断）；
4. 旧模型无这三层字段 → 零影响（存量兼容）；
5. 上云脱敏：只出 label/effect/statement/title/goal，params_schema、
   enforcement（可能含过滤表达式/物理列）、eval_seed 不上云（护栏 §7）。

纯函数，不触碰数据库。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services.ontology_service import (  # noqa: E402
    check_doc_quality,
    find_m234_issues,
    to_cloud_md,
    validate_actions,
    validate_rules,
    validate_scenarios,
)


def _doc(**kw):
    base = {"domain": "测试域", "objects": [
        {"key": "case", "display_name": "案例", "properties": [], "links": []},
        {"key": "user", "display_name": "用户", "properties": [], "links": []},
    ]}
    base.update(kw)
    return base


class TestValidateActions:
    def test_action_key_unique_across_objects(self):
        """action_key 是审批通道注册键，跨对象重复必须 error。"""
        doc = _doc()
        doc["objects"][0]["actions"] = [{"key": "case.close", "label": "关闭"}]
        doc["objects"][1]["actions"] = [{"key": "case.close", "label": "又关闭"}]
        errs = [i for i in validate_actions(doc) if i["severity"] == "error"]
        assert any("case.close" in i["message"] and "重复" in i["message"] for i in errs)

    def test_params_schema_must_be_object(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [
            {"key": "case.close", "label": "关闭", "params_schema": ["bad"]}]
        errs = [i for i in validate_actions(doc) if i["severity"] == "error"]
        assert any("params_schema" in i["message"] for i in errs)

    def test_risk_enum_enforced(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [
            {"key": "case.close", "label": "关闭", "risk": "extreme"}]
        errs = [i for i in validate_actions(doc) if i["severity"] == "error"]
        assert any("risk" in i["message"] for i in errs)

    def test_write_action_without_risk_is_warn_not_error(self):
        """写动作缺风险声明是建模建议（warn），不阻断保存。"""
        doc = _doc()
        doc["objects"][0]["actions"] = [{"key": "case.close", "label": "关闭"}]
        issues = validate_actions(doc)
        assert issues and all(i["severity"] == "warn" for i in issues)
        assert any("risk" in i["message"] for i in issues)
        assert any("requires_approval" in i["message"] for i in issues)

    def test_read_only_action_needs_no_risk(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [
            {"key": "case.trend", "label": "趋势分析", "read_only": True}]
        assert validate_actions(doc) == []

    def test_clean_write_action_passes(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [
            {"key": "case.close", "label": "关闭案例",
             "risk": "high", "requires_approval": True,
             "params_schema": {"required": ["case_id"]}, "effect": "状态→已关闭"}]
        assert validate_actions(doc) == []


class TestValidateRules:
    def test_type_enum_enforced(self):
        doc = _doc(rules=[{"key": "r1", "type": "unknown", "enforcement": {}}])
        errs = [i for i in validate_rules(doc) if i["severity"] == "error"]
        assert any("type" in i["message"] for i in errs)

    def test_permission_rule_requires_level_subject_action(self):
        doc = _doc(rules=[{"key": "r1", "type": "permission", "enforcement": {}}])
        errs = [i for i in validate_rules(doc) if i["severity"] == "error"]
        msgs = " ".join(i["message"] for i in errs)
        assert "level" in msgs and "subject" in msgs and "action" in msgs

    def test_dangling_action_ref_is_error(self):
        """权限规则授权给不存在的行为 = 悬空引用，宁阻断勿悬空（§4）。"""
        doc = _doc(rules=[{
            "key": "r1", "type": "permission",
            "enforcement": {"subject": "role:admin", "action": "no.such", "level": "allow"}}])
        errs = [i for i in validate_rules(doc) if i["severity"] == "error"]
        assert any("不存在的行为" in i["message"] for i in errs)

    def test_wildcard_action_allowed(self):
        doc = _doc(rules=[{
            "key": "r_admin_all", "type": "permission", "statement": "admin 全放行",
            "enforcement": {"subject": "role:admin", "action": "*", "level": "allow"}}])
        assert validate_rules(doc) == []

    def test_action_ref_resolves_against_declared_actions(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [
            {"key": "case.close", "label": "关闭", "risk": "high", "requires_approval": True}]
        doc["rules"] = [{
            "key": "r1", "type": "permission", "statement": "仅 admin 可关单",
            "enforcement": {"subject": "role:admin", "action": "case.close", "level": "allow"}}]
        assert validate_rules(doc) == []

    def test_metric_constraint_needs_enforcement(self):
        doc = _doc(rules=[{"key": "m1", "type": "metric_constraint", "statement": "排除删除"}])
        errs = [i for i in validate_rules(doc) if i["severity"] == "error"]
        assert any("enforcement" in i["message"] for i in errs)

    def test_statement_missing_is_warn(self):
        doc = _doc(rules=[{"key": "q1", "type": "quality",
                           "enforcement": {"check": "not_null"}}])
        issues = validate_rules(doc)
        assert issues and all(i["severity"] == "warn" for i in issues)


class TestValidateScenarios:
    def test_dangling_object_ref_is_error(self):
        doc = _doc(scenarios=[{"key": "s1", "title": "巡检", "uses_objects": ["ghost"]}])
        errs = [i for i in validate_scenarios(doc) if i["severity"] == "error"]
        assert any("ghost" in i["message"] and "对象" in i["message"] for i in errs)

    def test_dangling_action_and_rule_ref_is_error(self):
        doc = _doc(scenarios=[{"key": "s1", "title": "巡检",
                               "uses_actions": ["a.x"], "uses_rules": ["r.x"]}])
        msgs = " ".join(i["message"] for i in validate_scenarios(doc) if i["severity"] == "error")
        assert "行为" in msgs and "规则" in msgs

    def test_valid_refs_pass(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [
            {"key": "case.close", "label": "关闭", "risk": "high", "requires_approval": True}]
        doc["rules"] = [{"key": "r1", "type": "quality", "enforcement": {"check": "x"}}]
        doc["scenarios"] = [{"key": "s1", "title": "关单", "goal": "处理超时案例",
                             "uses_objects": ["case"], "uses_actions": ["case.close"],
                             "uses_rules": ["r1"]}]
        assert validate_scenarios(doc) == []

    def test_missing_title_is_warn(self):
        doc = _doc(scenarios=[{"key": "s1", "uses_objects": ["case"]}])
        issues = validate_scenarios(doc)
        assert issues and all(i["severity"] == "warn" for i in issues)


class TestFindM234Issues:
    def test_legacy_doc_without_m234_is_clean(self):
        """旧模型无 actions/rules/scenarios 字段 → 存量零影响。"""
        assert find_m234_issues(_doc()) == []

    def test_empty_collections_are_clean(self):
        assert find_m234_issues(_doc(objects=[{"key": "case", "actions": []}],
                                     rules=[], scenarios=[])) == []

    def test_quality_check_merges_m234(self):
        doc = _doc()
        doc["objects"][0]["actions"] = [{"key": "a", "label": "x", "risk": "bad"}]
        q = check_doc_quality(doc)
        assert "action" in q["by_category"]

    def test_m234_errors_count_toward_quality_errors(self):
        doc = _doc(scenarios=[{"key": "s1", "title": "t", "uses_objects": ["ghost"]}])
        q = check_doc_quality(doc)
        assert q["errors"] >= 1


class TestCloudMdRedaction:
    """上云脱敏（护栏 §7）：M2/M3/M4 渲染白名单。"""

    def _doc(self):
        return _doc(
            objects=[{
                "key": "case", "display_name": "案例", "properties": [],
                "actions": [{"key": "case.close", "label": "关闭案例",
                             "effect": "状态→已关闭", "risk": "high",
                             "requires_approval": True,
                             "params_schema": {"required": ["case_id", "SECRET_PARAM"]}}],
            }],
            rules=[{
                "key": "r1", "type": "business_constraint",
                "statement": "案例类口径一律排除已删除记录",
                "source": "业务约定",
                "enforcement": {"filter": "del_flag = 0 AND secret_col > 1"}}],
            scenarios=[{
                "key": "s1", "title": "超时案例处理", "goal": "清理积压",
                "uses_objects": ["case"], "eval_seed": ["内部评测问题EVAL_SEED_XYZ"]}])

    def test_renders_whitelisted_content(self):
        md = to_cloud_md(self._doc())
        assert "关闭案例" in md and "状态→已关闭" in md          # action label/effect
        assert "案例类口径一律排除已删除记录" in md                 # rule statement
        assert "超时案例处理" in md and "清理积压" in md           # scenario title/goal

    def test_strips_params_schema_enforcement_eval_seed(self):
        md = to_cloud_md(self._doc())
        assert "SECRET_PARAM" not in md        # params_schema 不上云
        assert "del_flag" not in md            # enforcement.filter 不上云（可能含物理列）
        assert "secret_col" not in md
        assert "EVAL_SEED_XYZ" not in md       # eval_seed 是内部建模信息

    def test_action_marked_readonly(self):
        d = self._doc()
        d["objects"][0]["actions"][0]["read_only"] = True
        assert "（只读）" in to_cloud_md(d)


class TestRdfSemanticExpansion:
    """图谱知识图谱化：M2/M3/M4 语义单元入图（节点+连线），而非只有对象间 link。"""

    def _doc(self):
        return _doc(
            objects=[{
                "key": "case", "display_name": "案例",
                "properties": [{"name": "案例状态", "column": "t.case_status", "type": "int"}],
                "metrics": [{"name": "案例数量", "description": "记录数"}],
                "actions": [{"key": "case.trend", "label": "趋势分析",
                             "read_only": True, "effect": "看走势"}],
            }],
            rules=[{"key": "rule_exclude_deleted", "type": "business_constraint",
                     "statement": "排除已删除",
                     "enforcement": {"filter": "del_flag = 0", "applies_to": ["case"]}}],
            scenarios=[{"key": "ana_case_trend", "title": "案例趋势",
                        "uses_objects": ["case"], "uses_actions": ["case.trend"],
                        "uses_rules": ["rule_exclude_deleted"]}])

    def test_emits_semantic_nodes_and_edges(self):
        from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle
        ttl = ontology_json_to_turtle(self._doc(), 0)
        # 节点类型声明
        assert "a adh:Action" in ttl and "a adh:Property" in ttl
        assert "a adh:Rule" in ttl and "a adh:Scenario" in ttl
        # 对象为中心的连线
        assert "adh:hasAction adh:act:case_trend" in ttl
        assert "adh:hasProperty adh:prop:case_case_status" in ttl
        assert "adh:hasMetric adh:objmetric:case_案例数量" in ttl
        # 规则作用域 / 场景引用边
        assert "adh:governs adh:obj:case" in ttl
        assert "adh:usesObject adh:obj:case" in ttl
        assert "adh:usesAction adh:act:case_trend" in ttl
        assert "adh:usesRule adh:rule:rule_exclude_deleted" in ttl

    def test_rule_enforcement_filter_not_in_label(self):
        """规则 label 用 statement（人话），enforcement.filter 不进 rdfs:label。"""
        from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle
        ttl = ontology_json_to_turtle(self._doc(), 0)
        for line in ttl.splitlines():
            if "rule_exclude_deleted" in line and "rdfs:label" in line:
                assert "del_flag" not in line

    def test_legacy_doc_without_m234_emits_nothing_new(self):
        """旧模型（无 actions/rules/scenarios）→ 无语义展开节点，存量零影响。"""
        from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle
        ttl = ontology_json_to_turtle({"objects": [{"key": "a", "display_name": "甲"}]}, 0)
        assert "adh:Action" not in ttl and "adh:Rule" not in ttl and "adh:Scenario" not in ttl
