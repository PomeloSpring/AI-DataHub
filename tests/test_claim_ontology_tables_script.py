"""claim_ontology_tables_as_products 脚本回归（activate 硬门禁 P2.3 配套）。

锁住的行为：
1. --dry-run 只报清单，不调 register_product；
2. 认领登记 payload 合规：product_name=<ds>.<table>、producer_kind=manual、
   producer_ref 溯源模型 id（责任归属可追）；
3. 源本体 doc 缺 datasource_name 时经 **save_draft 级联**回填（不绕派生表）；
4. 登记后仍缺的表必须如实报 gate_missing（fail-loud，不假报通过）。

纯 monkeypatch，不触碰数据库。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.scripts import claim_ontology_tables_as_products as claim


@pytest.fixture
def env(monkeypatch):
    """搭一个 source 模型 + 全未登记的 fake 环境；返回记录器 dict。"""
    rec = {"registered": [], "saved": [], "find_results": {}}

    doc = {"objects": [{"key": "a", "primary_table": "t_a"},
                       {"key": "b", "primary_table": "t_b"}]}
    model = {"id": 42, "name": "仓", "kind": "source", "datasource_id": 5,
             "status": "draft", "json_content": json.dumps(doc)}

    monkeypatch.setattr(claim.ontology_service, "get_model", lambda mid: dict(model))

    def fake_save(mid, json_content, name=None):
        rec["saved"].append(json_content)
        m = dict(model)
        m["json_content"] = json_content
        return m
    monkeypatch.setattr(claim.ontology_service, "save_draft", fake_save)

    monkeypatch.setattr(
        claim.ontology_service, "find_unregistered_tables",
        lambda d: [t for t in ("t_a", "t_b")
                   if not rec["find_results"].get(t)])

    monkeypatch.setattr(
        claim.dps, "find_by_table",
        lambda ds, table: {"product_name": f"{ds}.{table}"}
        if rec["find_results"].get(table) else None)

    def fake_register(payload, columns=None, created_by="", on_breaking="defer"):
        rec["registered"].append(payload)
        rec["find_results"][payload["physical_table"]] = True
        return {"product": payload, "schema_changed": False,
                "version_record": None, "warnings": [], "pending_approval": False}
    monkeypatch.setattr(claim.dps, "register_product", fake_register)

    monkeypatch.setattr(
        claim, "execute_query",
        lambda sql, params=None, fetchone=False: (
            {"name": "Doris"} if fetchone else
            [{"column_name": "c1", "data_type": "text", "column_comment": ""}]))
    return rec


class TestClaimScript:
    def test_dry_run_registers_nothing(self, env, monkeypatch):
        r = claim.claim_model(42, dry_run=True)
        assert env["registered"] == []
        assert r["claimed"] == ["Doris.t_a", "Doris.t_b"]
        assert r["backfilled_datasource_name"] is False

    def test_claim_payload_traceable(self, env):
        r = claim.claim_model(42)
        assert [p["product_name"] for p in env["registered"]] == \
            ["Doris.t_a", "Doris.t_b"]
        for p in env["registered"]:
            assert p["producer_kind"] == "manual"
            assert p["producer_ref"] == "claim:ontology_model:42"
            assert p["datasource_name"] == "Doris"
        assert r["gate_missing"] == []

    def test_backfills_doc_datasource_name_via_save_draft(self, env):
        """doc 无 datasource_name（生成链路历史缺陷）→ 级联回填，不绕派生表。"""
        r = claim.claim_model(42)
        assert r["backfilled_datasource_name"] is True
        assert len(env["saved"]) == 1
        saved = json.loads(env["saved"][0])
        assert saved["datasource_name"] == "Doris"

    def test_gate_missing_reported_not_silenced(self, env, monkeypatch):
        """登记后仍查不到产品 → 如实报 gate_missing，不假报通过。"""
        monkeypatch.setattr(claim.ontology_service, "find_unregistered_tables",
                            lambda d: ["t_a"])
        r = claim.claim_model(42)
        assert r["gate_missing"] == ["t_a"]
