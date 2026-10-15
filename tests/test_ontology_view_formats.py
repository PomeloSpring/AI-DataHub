"""本体结构化查看器：hydrate 读时投影 + Palantir 只读转换 + /view 端点。

核心为纯函数测试；涉及字典/DB 处用 monkeypatch 打桩，不触碰真实库。
对应需求：
- hydrate_doc：metrics 名字引用 → 带口径对象、补 dimensions；
- to_palantir：与 palantir_to_canonical 往返守恒（对象/属性/链接/指标名）；
- GET /models/{id}/view：format×as 组合返回合法文本；
- KB 辅助生成：_schema_text 注入 Business knowledge 段、_build_task_message 标注。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import ontology_service as o
from backend.modules.catalog.services.ontology_yaml_import import palantir_to_canonical
from backend.modules.catalog.api import ontology as ontology_api


def _sample_canonical():
    return {
        "domain": "零售",
        "description": "订单域",
        "objects": [
            {
                "key": "order", "display_name": "订单", "aliases": ["单"],
                "description": "订单对象", "primary_table": "orders",
                "source_ref": "db.public.orders",
                "properties": [
                    {"column": "orders.status", "name": "订单状态", "type": "string",
                     "is_key": False, "description": "状态", "enum": {"0": "待支付", "1": "已支付"}},
                ],
                "links": [
                    {"target": "customer", "type": "belongs_to",
                     "join": "orders.customer_id = customers.id", "cardinality": "N:1",
                     "description": "属于客户"},
                ],
                "metrics": ["total_revenue"],
            },
            {
                "key": "customer", "display_name": "客户", "aliases": [],
                "description": "客户对象", "primary_table": "customers",
                "source_ref": "db.public.customers",
                "properties": [{"column": "customers.id", "name": "客户ID", "type": "int", "is_key": True}],
                "links": [], "metrics": [],
            },
        ],
    }


# ── hydrate_doc：字典口径注入（metrics/dimensions） ──────────────────

class TestHydrateDoc:
    def _patch_dicts(self, monkeypatch):
        def fake_load(keys, table, cols):
            if table == "adh_metrics":
                return {"order": [{
                    "name": "总营收", "name_en": "total_revenue", "formula": "SUM(orders.amount)",
                    "agg_type": "sum", "unit": "元", "description": "累计金额", "certified": 1,
                    "bound_object_key": "order",
                }]}
            if table == "adh_dimensions":
                return {"order": [{
                    "name": "下单日期", "name_en": "order_date", "target_column": "orders.order_date",
                    "category": "时间", "aliases": '["下单时间"]', "value_labels": None,
                    "description": "按天", "bound_object_key": "order",
                }]}
            return {}
        monkeypatch.setattr(o, "_load_dict_by_object", fake_load)

    def test_metrics_name_ref_upgraded_to_formula_object(self, monkeypatch):
        self._patch_dicts(monkeypatch)
        out = o.hydrate_doc(_sample_canonical(), datasource_id=1)
        order = next(ob for ob in out["objects"] if ob["key"] == "order")
        assert order["metrics"], "metrics 应被 hydrate"
        m = order["metrics"][0]
        assert isinstance(m, dict) and m.get("formula") == "SUM(orders.amount)"
        assert m.get("agg_type") == "sum" and m.get("certified") is True

    def test_dimensions_added_from_dict(self, monkeypatch):
        self._patch_dicts(monkeypatch)
        out = o.hydrate_doc(_sample_canonical(), datasource_id=1)
        order = next(ob for ob in out["objects"] if ob["key"] == "order")
        dims = order["dimensions"]
        assert dims and dims[0]["name"] == "下单日期"
        assert dims[0]["is_time"] is True
        assert dims[0]["aliases"] == ["下单时间"]

    def test_does_not_mutate_input_doc(self, monkeypatch):
        self._patch_dicts(monkeypatch)
        src = _sample_canonical()
        o.hydrate_doc(src, datasource_id=1)
        # 存储 canonical 不变：metrics 仍是名字引用、无 dimensions 冗余
        assert src["objects"][0]["metrics"] == ["total_revenue"]
        assert "dimensions" not in src["objects"][0]


# ── to_palantir：与 palantir_to_canonical 往返守恒 ────────────────────

class TestToPalantir:
    def test_structure_and_counts_conserved(self):
        doc = _sample_canonical()
        pal = o.to_palantir(doc)
        assert len(pal["object_types"]) == len(doc["objects"])
        # 每个 object_type 的 api_name 即 canonical key
        api_names = {ot["api_name"] for ot in pal["object_types"]}
        assert api_names == {"order", "customer"}
        order = next(ot for ot in pal["object_types"] if ot["api_name"] == "order")
        # enum → values
        assert order["properties"][0]["values"] == {"0": "待支付", "1": "已支付"}
        # link cardinality 反向映射 N:1 → many_to_one，target 用业务名
        lt = order["link_types"][0]
        assert lt["cardinality"] == "many_to_one"
        assert lt["target"] == "客户"

    def test_roundtrip_via_importer(self):
        doc = _sample_canonical()
        pal = o.to_palantir(doc)
        back = palantir_to_canonical([pal], datasource_id=0)
        keys_back = {ob["key"] for ob in back["objects"]}
        assert keys_back == {"order", "customer"}
        # 链接名守恒（belongs_to）
        order = next(ob for ob in back["objects"] if ob["key"] == "order")
        assert {lk["type"] for lk in order["links"]} == {"belongs_to"}


# ── GET /models/{id}/view：format×as 组合返回合法文本 ─────────────────

class TestViewEndpoint:
    def _setup(self, monkeypatch):
        model = {"id": 1, "datasource_id": 1, "json_content": json.dumps(_sample_canonical()),
                 "kind": "source", "status": "active"}
        monkeypatch.setattr(o, "get_model", lambda mid: model)
        monkeypatch.setattr(o, "can_view_model", lambda m, a: True)
        monkeypatch.setattr(o, "_load_dict_by_object", lambda keys, table, cols: {})
        monkeypatch.setattr(ontology_api, "_allowed_source_ids", lambda user: {1})

    def test_internal_json_yaml_md(self, monkeypatch):
        self._setup(monkeypatch)
        for as_ in ("json", "yaml", "md"):
            res = ontology_api.view_model(1, fmt="internal", as_=as_, user={"user_id": 1})
            assert res["format"] == "internal" and res["as"] == as_
            assert res["text"], f"as={as_} 应返回非空文本"
        # json 合法且含对象
        assert "objects" in json.loads(
            ontology_api.view_model(1, fmt="internal", as_="json", user={"user_id": 1})["text"])

    def test_palantir_json_yaml_md(self, monkeypatch):
        self._setup(monkeypatch)
        for as_ in ("json", "yaml", "md"):
            res = ontology_api.view_model(1, fmt="palantir", as_=as_, user={"user_id": 1})
            assert res["format"] == "palantir" and res["text"]
        pal = json.loads(ontology_api.view_model(1, fmt="palantir", as_="json", user={"user_id": 1})["text"])
        assert "object_types" in pal

    def test_invalid_format_or_as(self, monkeypatch):
        self._setup(monkeypatch)
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            ontology_api.view_model(1, fmt="ossie", as_="json", user={"user_id": 1})
        with pytest.raises(HTTPException):
            ontology_api.view_model(1, fmt="internal", as_="csv", user={"user_id": 1})


# ── MD 渲染 hydrated metrics/dimensions + 上云脱敏 ────────────────────

class TestMdRendering:
    def _hydrated(self):
        return {
            "domain": "零售", "objects": [{
                "key": "order", "display_name": "订单", "primary_table": "orders",
                "properties": [{"column": "orders.status", "name": "订单状态", "type": "string",
                               "enum": {"0": "待支付"}}],
                "metrics": [{"name": "总营收", "display_name": "总营收",
                             "formula": "SUM(orders.amount)", "agg_type": "sum", "unit": "元"}],
                "dimensions": [{"name": "下单日期", "display_name": "下单日期",
                               "target_column": "orders.order_date", "is_time": True,
                               "value_labels": {}}],
            }],
        }

    def test_internal_md_shows_formula_and_dimension(self):
        md = o.to_md(self._hydrated())
        assert "SUM(orders.amount)" in md
        assert "下单日期" in md and "orders.order_date" in md

    def test_cloud_md_redacts_physical(self):
        # 上云白名单：不泄露 formula 物理列 / target_column
        cloud = o.to_cloud_md(self._hydrated())
        assert "SUM(orders.amount)" not in cloud
        assert "orders.order_date" not in cloud
        assert "总营收" in cloud and "下单日期" in cloud


# ── KB 辅助生成：素材段注入 + 任务消息标注 ───────────────────────────

class TestKbGeneration:
    def test_schema_text_includes_kb_section(self):
        batch = [{"table_name": "orders", "columns": []}]
        meta = {"relations": [], "terms": [], "metrics": [], "dimensions": []}
        txt = o._schema_text(batch, meta, kb_text="订单与客户属于关系")
        assert "Business knowledge (from selected KB)" in txt
        assert "订单与客户属于关系" in txt

    def test_schema_text_without_kb_has_no_section(self):
        batch = [{"table_name": "orders", "columns": []}]
        meta = {"relations": [], "terms": [], "metrics": [], "dimensions": []}
        txt = o._schema_text(batch, meta)
        assert "Business knowledge" not in txt

    def test_task_message_marks_kb(self):
        assert "业务知识库" in ontology_api._build_task_message("订单库", 3, kb_count=2)
        assert "业务知识库" not in ontology_api._build_task_message("订单库", 3, kb_count=0)

    def test_build_generation_batches_passes_kb_ids(self, monkeypatch):
        monkeypatch.setattr(o, "_collect_metadata", lambda ds: {
            "tables": [{"table_name": "orders", "domain_tag": "d", "columns": []}],
            "relations": [], "terms": [], "metrics": [], "dimensions": []})
        captured = {}

        def fake_retrieve(batches, datasource_id, kb_ids):
            captured["kb_ids"] = kb_ids
            return ["KB片段"] * len(batches)
        monkeypatch.setattr(o, "_retrieve_kb_texts", fake_retrieve)
        out = o.build_generation_batches(1, kb_ids=[4, 5])
        assert captured["kb_ids"] == [4, 5]
        assert "Business knowledge" in out[0]
