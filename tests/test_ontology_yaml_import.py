"""Unit tests for Palantir YAML 导入归一化 + 本体 RDF 入图（可遍历 link + join）。

均为纯函数测试，不触碰数据库 / Oxigraph。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datacatalog.services.ontology_yaml_import import (
    parse_palantir_dir,
    palantir_to_canonical,
    _clean_table,
    _enum_list,
)
from services.shared.common.rdf.ontology_to_rdf import ontology_json_to_turtle

_ONTOLOGY_DIR = os.path.join(os.path.dirname(__file__), "..", "ontology")


def _by_key(doc, key):
    return next((o for o in doc["objects"] if o["key"] == key), None)


# ── 辅助函数 ──────────────────────────────────────────────────────

class TestHelpers:
    def test_clean_table_strips_db_prefix(self):
        assert _clean_table("stardb.t_user_customer") == "t_user_customer"
        assert _clean_table("bigdata.alliedstar.dwd_observability_rum") == "dwd_observability_rum"

    def test_clean_table_drops_log_sources(self):
        assert _clean_table("sls://alliedstar-prd/logstash") == ""

    def test_clean_table_strips_paren_noise(self):
        assert _clean_table("stardb.t_case_files (分表 0~15)") == "t_case_files"

    def test_enum_list(self):
        assert _enum_list({0: "未知", 1: "男"}) == ["0=未知", "1=男"]
        assert _enum_list("not-a-dict") == []


# ── YAML → canonical 归一化 ──────────────────────────────────────

class TestPalantirCanonical:
    def setup_class(self):
        self.doc = parse_palantir_dir(_ONTOLOGY_DIR, datasource_id=1)

    def test_objects_parsed(self):
        assert len(self.doc["objects"]) >= 20
        assert _by_key(self.doc, "user") is not None
        assert _by_key(self.doc, "company") is not None

    def test_property_column_and_key(self):
        user = _by_key(self.doc, "user")
        assert user["primary_table"] == "t_user_customer"
        codes = [p for p in user["properties"] if p["name"] == "user_code"]
        assert codes and codes[0]["column"] == "t_user_customer.user_code"
        assert codes[0]["is_key"] is True

    def test_property_enum_format(self):
        user = _by_key(self.doc, "user")
        sex = next(p for p in user["properties"] if p["name"] == "sex")
        assert "1=男" in sex["enum"]

    def test_links_resolved_and_join_present(self):
        company = _by_key(self.doc, "company")
        assert company["links"], "company 应含 link"
        targets = {lk["target"] for lk in company["links"]}
        assert "case" in targets  # Palantir Name Case → key case
        assert any("company_code" in (lk["join"] or "") for lk in company["links"])

    def test_cross_domain_link_merged(self):
        # 06-link-types 的 order→orderitem→product 应挂到 order/orderitem 上
        order = _by_key(self.doc, "order")
        assert order and order["links"]

    def test_domain_metric_attached_to_object(self):
        user = _by_key(self.doc, "user")
        names = {m["name"] for m in user["metrics"]}
        assert "active_users" in names  # identity.yaml metrics: object_type User

    def test_no_empty_object_without_name(self):
        for o in self.doc["objects"]:
            assert o["key"]


# ── canonical → RDF（可遍历 link + join）────────────────────────

class TestOntologyToRdf:
    def setup_class(self):
        doc = parse_palantir_dir(_ONTOLOGY_DIR, datasource_id=1)
        self.turtle = ontology_json_to_turtle(doc, datasource_id=1)

    def test_class_node_emitted(self):
        assert "adh:obj:user a owl:Class" in self.turtle

    def test_traversable_edge_emitted(self):
        assert "adh:linkedTo" in self.turtle

    def test_join_expr_survives(self):
        assert "adh:joinExpr" in self.turtle
        assert "company_code" in self.turtle  # join 原文进入 RDF

    def test_link_reification(self):
        assert "a adh:Link" in self.turtle
        assert "adh:linkFrom" in self.turtle and "adh:linkTo" in self.turtle

    def test_primary_table_and_column_mapping(self):
        assert "adh:primaryTable" in self.turtle
        assert "adh:mapsColumn" in self.turtle

    def test_legacy_schema_still_parses(self):
        legacy = {
            "name": "Legacy",
            "objects": [{
                "class": "Shipment", "label": "货运单",
                "relations": [{"type": "ships_via", "target": "Port", "label": "途经"}],
            }],
        }
        turtle = ontology_json_to_turtle(legacy, datasource_id=0)
        assert "adh:obj:Shipment a owl:Class" in turtle
        assert "adh:linkedTo" in turtle
