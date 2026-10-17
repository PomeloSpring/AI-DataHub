"""行为路径建模位（scenarios[].path）—— 校验 / 上云脱敏 / 图谱投影 / 检索（离线单测）。

路径是"有序步骤口径"，不是关联关系：
  - 校验层：步序必须从 1 连续、每步有业务名，否则 activate 硬阻断（宁阻断勿悬空）；
  - 上云层：只渲染业务名序列，dataset_ref/事件谓词/物理列一律不出（护栏 §7）；
  - 图谱层：投影为 adh:Path + reified adh:PathStep，且**不得**被 planner 当 JOIN 链；
  - 检索层：knowledge_search 附 paths[]，失败显式标注 path_resolution=failed。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.common.rdf import ontology_to_rdf
from backend.modules.catalog.services import ontology_service as osvc


def _doc(**over):
    doc = {
        "name": "demo_behavior", "domain": "行为分析",
        "objects": [{
            "key": "rum_event", "display_name": "埋点事件", "primary_table": "t_rum",
            "properties": [{"column": "event_name", "name": "事件名", "type": "varchar"}],
            "metrics": [], "dimensions": [], "links": [],
        }],
        "scenarios": [{
            "key": "path_item_pay", "title": "商品→支付关键路径",
            "goal": "衡量商品页到支付的层间转化",
            "uses_objects": ["rum_event"],
            "path": {
                "key": "path_item_pay", "title": "商品→支付",
                "entity": "设备ID", "event_time": "事件时间", "window_sec": 1800,
                "dataset": "路径漏斗", "query_object": "路径分析",
                "steps": [
                    {"order": 1, "key": "item", "name": "商品页", "event": "页面=商品详情"},
                    {"order": 2, "key": "cart", "name": "加购", "event": "操作=加入购物车"},
                    {"order": 3, "key": "pay", "name": "支付", "event": "操作=支付成功"},
                ],
            },
        }],
    }
    doc.update(over)
    return doc


def _scenario(doc):
    return doc["scenarios"][0]


# ── 1. 结构校验 ─────────────────────────────────────────────────

class TestPathValidation:
    def test_valid_path_has_no_errors(self):
        assert osvc.validate_paths(_doc()) == []

    def test_non_object_path_is_error(self):
        doc = _doc()
        _scenario(doc)["path"] = "商品 → 支付"
        issues = osvc.validate_paths(doc)
        assert any(i["severity"] == "error" and i["category"] == "path" for i in issues)

    def test_non_contiguous_order_is_error(self):
        doc = _doc()
        _scenario(doc)["path"]["steps"][1]["order"] = 5
        issues = osvc.validate_paths(doc)
        assert any("步序" in i["message"] and i["severity"] == "error" for i in issues), issues

    def test_missing_steps_is_error(self):
        doc = _doc()
        _scenario(doc)["path"]["steps"] = []
        assert any(i["severity"] == "error" for i in osvc.validate_paths(doc))

    def test_step_without_business_name_is_error(self):
        doc = _doc()
        _scenario(doc)["path"]["steps"][0].pop("name")
        _scenario(doc)["path"]["steps"][0].pop("key")
        assert any("业务名" in i["message"] for i in osvc.validate_paths(doc))

    def test_step_without_event_is_warn_only(self):
        doc = _doc()
        _scenario(doc)["path"]["steps"][0].pop("event")
        issues = osvc.validate_paths(doc)
        assert issues and all(i["severity"] == "warn" for i in issues), issues

    def test_bad_window_is_error(self):
        doc = _doc()
        _scenario(doc)["path"]["window_sec"] = 0
        assert any("window_sec" in i["message"] for i in osvc.validate_paths(doc))

    def test_duplicate_path_key_is_error(self):
        doc = _doc()
        dup = json.loads(json.dumps(_scenario(doc)))
        doc["scenarios"].append(dup)
        assert any("重复" in i["message"] for i in osvc.validate_paths(doc))

    def test_paths_are_part_of_m234_gate(self):
        """find_m234_issues 是 save_draft 告警 / activate 硬阻断的共用入口。"""
        doc = _doc()
        _scenario(doc)["path"]["steps"][1]["order"] = 9
        assert any(i["category"] == "path" for i in osvc.find_m234_issues(doc))

    def test_legacy_doc_without_path_is_unaffected(self):
        doc = _doc()
        _scenario(doc).pop("path")
        assert osvc.validate_paths(doc) == []


# ── 2. 上云脱敏 ────────────────────────────────────────────────

class TestPathCloudRedaction:
    def test_cloud_md_renders_business_steps(self):
        md = osvc.to_cloud_md(_doc())
        assert "商品页 → 加购 → 支付" in md
        assert "窗口 30 分钟" in md
        assert "按「设备ID」判定" in md

    def test_cloud_md_leaks_no_dataset_or_physical(self):
        md = osvc.to_cloud_md(_doc())
        for secret in ("tpl-path-funnel", "t_rum", "event_name", "query_object",
                       "路径漏斗", "页面=商品详情"):
            assert secret not in md, f"上云文档泄露: {secret}"

    def test_pathless_scenario_renders_unchanged(self):
        doc = _doc()
        _scenario(doc).pop("path")
        md = osvc.to_cloud_md(doc)
        assert "商品→支付关键路径" in md and "关键路径:" not in md


# ── 3. 图谱投影（且不得参与 JOIN 链）────────────────────────────

class TestPathGraphProjection:
    def test_turtle_emits_path_and_steps(self):
        turtle = ontology_to_rdf.ontology_json_to_turtle(_doc(), datasource_id=1)
        assert "a adh:Path" in turtle
        assert "a adh:PathStep" in turtle
        assert 'adh:stepOrder "2"' in turtle
        assert "adh:nextStep" in turtle
        assert "adh:hasStep" in turtle
        assert "adh:pathEntity" in turtle

    def test_step_links_to_object_class(self):
        doc = _doc()
        doc["scenarios"][0]["path"]["steps"][0]["object_key"] = "rum_event"
        turtle = ontology_to_rdf.ontology_json_to_turtle(doc, datasource_id=1)
        assert "adh:stepOn adh:obj:rum_event" in turtle

    def test_path_nodes_are_not_join_endpoints(self):
        """link_graph._iri_key 只认 obj: 前缀 —— path:/step: 不会被当 SQL 关系链。"""
        from backend.semantics.graph_rag.link_graph import _iri_key

        base = "http://ai-datahub.org/ontology/"
        assert _iri_key(base + "obj:rum_event") == "rum_event"
        assert _iri_key(base + "path:path_item_pay") == ""
        assert _iri_key(base + "step:path_item_pay_1") == ""

    def test_no_path_emits_nothing(self):
        doc = _doc()
        _scenario(doc).pop("path")
        turtle = ontology_to_rdf.ontology_json_to_turtle(doc, datasource_id=1)
        assert "adh:PathStep" not in turtle


# ── 4. 检索：paths[] 与降级可区分 ───────────────────────────────

_PATHS = [{
    "path_key": "path_item_pay", "title": "商品→支付", "scenario_key": "path_item_pay",
    "ontology": "demo_behavior", "steps": ["商品页", "加购", "支付"],
    "step_details": [{"order": 1, "name": "商品页", "key": "item", "event": "页面=商品详情",
                      "object_key": "rum_event"}],
    "related_objects": ["rum_event"], "entity": "设备ID", "event_time": "事件时间",
    "window_sec": 1800, "aliases": ["下单转化路径"],
    "query_object": "路径分析",
    "dataset_hint": {"dataset": "路径漏斗",
                     "params": {"start_date": "date", "window_sec": "int"},
                     "dimensions": ["步骤序号", "渠道"], "metrics": ["到达实体数"],
                     "certified": True},
}]


@pytest.fixture
def stub_paths(monkeypatch):
    from backend.modules.mind.rag import behavior_path as bp

    monkeypatch.setattr(bp, "collect_behavior_paths", lambda *a, **k: list(_PATHS))
    return bp


class TestPathRetrieval:
    def test_match_by_step_object_key(self, stub_paths):
        got = stub_paths.resolve_behavior_paths(object_keys=["rum_event"])
        assert got and got[0]["path_key"] == "path_item_pay"

    def test_match_by_business_keyword(self, stub_paths):
        assert stub_paths.resolve_behavior_paths(keywords=["下单转化路径"])
        assert stub_paths.resolve_behavior_paths(keywords=["商品 页"])

    def test_no_match_returns_empty_not_guess(self, stub_paths):
        assert stub_paths.resolve_behavior_paths(keywords=["完全无关的词"]) == []
        assert stub_paths.resolve_behavior_paths() == []

    def test_hint_carries_params_and_drill_columns(self, stub_paths):
        got = stub_paths.resolve_behavior_paths(keywords=["商品→支付"])
        hint = got[0]["dataset_hint"]
        assert hint["dataset"] == "路径漏斗"
        assert "window_sec" in hint["params"]
        assert "步骤序号" in hint["dimensions"] and "到达实体数" in hint["metrics"]

    def test_path_entry_builds_dataset_hint_from_dataset_service(self, monkeypatch):
        """canonical 只存数据集名，参数/可下钻列由数据集服务现场拼出（单一事实源）。"""
        from backend.modules.mind.rag import behavior_path as bp
        from backend.modules.viz.services import dataset_service

        monkeypatch.setattr(dataset_service, "get_dataset_by_name", lambda name: {
            "id": 1, "name": name, "variables": json.dumps({"start_date": {"type": "date"}}),
            "field_config": json.dumps([{"field": "lvl", "label": "步骤序号", "role": "dimension"},
                                        {"field": "uv", "label": "到达用户数", "role": "measure"}],
                                       ensure_ascii=False),
            "certified": 1,
        })
        entry = bp._path_entry({"key": "p1", "title": "T"}, {
            "key": "p1", "dataset": "路径漏斗",
            "steps": [{"order": 1, "name": "商品页"}, {"order": 2, "name": "支付"}],
        }, "demo_behavior")
        assert entry["dataset_hint"]["dataset"] == "路径漏斗"
        assert entry["dataset_hint"]["metrics"] == ["到达用户数"]
        assert "template_ref" not in entry and "template_hint" not in entry

    def test_canonical_read_failure_propagates(self, monkeypatch):
        """读取失败必须抛出，由调用方标注降级——不得把"读不动"当成"没有路径"。"""
        from backend.modules.mind.rag import behavior_path as bp

        def boom(*a, **k):
            raise RuntimeError("元库不可达")

        monkeypatch.setattr(bp, "collect_behavior_paths", boom)
        with pytest.raises(RuntimeError):
            bp.resolve_behavior_paths(keywords=["商品页"])


class TestKnowledgeSearchAttach:
    def _attach(self, result, keywords):
        from backend.modules.mind.execution.sdk_tools.semantic_tools import _attach_paths

        return _attach_paths(result, keywords=keywords)

    def test_paths_attached_with_ok_resolution(self, stub_paths, monkeypatch):
        out = self._attach({"hit_object_keys": ["rum_event"]}, ["商品页"])
        assert out["path_resolution"] == "ok"
        assert out["paths"][0]["dataset_hint"]["dataset"] == "路径漏斗"

    def test_failure_is_marked_not_silently_empty(self, monkeypatch):
        from backend.modules.mind.rag import behavior_path as bp

        def boom(*a, **k):
            raise RuntimeError("canonical 读取失败")

        monkeypatch.setattr(bp, "resolve_behavior_paths", boom)
        out = self._attach({"hit_object_keys": ["rum_event"]}, ["商品页"])
        assert out["paths"] == []
        assert str(out["path_resolution"]).startswith("failed:")
