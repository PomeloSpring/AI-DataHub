"""单向投影巡检器（backend/scripts/check_alias_projection.py）口径回归。

锁住的历史误报教训（原 check_alias_drift 连错三次的根因）：
  ① 图谱查询必须遍历命名图（GRAPH ?g），查默认图会拿 0 条误报"图谱滞后"；
  ② IRI 必须剥命名空间/前缀再比对（obj:case_file 与 case_file 同 owner）；
  ③ 图查询失败必须显式报告，不得吞掉后当作"无漂移"（no-silent-degradation）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "scripts"))

import check_alias_projection as cap_


# ── 教训②：IRI 归一 ────────────────────────────────────────────────

class TestIriOwner:
    def test_strips_namespace_and_prefix(self):
        assert cap_._iri_owner("http://ai-datahub.org/ontology/obj:case_file") == "case_file"
        assert cap_._iri_owner("http://ai-datahub.org/ontology/obj:ai_conversation") == "ai_conversation"

    def test_plain_key_is_untouched(self):
        assert cap_._iri_owner("case_file") == "case_file"

    def test_empty_is_safe(self):
        assert cap_._iri_owner("") == ""
        assert cap_._iri_owner(None) == ""


# ── 教训①：图谱查询必须遍历命名图 ─────────────────────────────────

class _FakeClient:
    def __init__(self, rows=None, boom=False):
        self.sparql = None
        self._rows = rows or []
        self._boom = boom

    def query(self, sparql):
        self.sparql = sparql
        if self._boom:
            raise RuntimeError("oxigraph down")
        return self._rows


class TestGraphQueryTargetsNamedGraphs:
    def test_scans_all_named_graphs(self):
        client = _FakeClient()
        cap_._fetch_altlabels(client)
        assert "GRAPH ?g" in client.sparql          # 不是查默认图/单个空图

    def test_rows_normalized_by_owner(self):
        client = _FakeClient(rows=[
            {"g": "http://ai-datahub.org/ontology/ds:1",
             "s": "http://ai-datahub.org/ontology/obj:case_file", "o": "案例文件"},
        ])
        out = cap_._fetch_altlabels(client)
        assert out == {"case_file": {"案例文件"}}

    def test_graph_failure_is_reported_not_swallowed(self):
        report = cap_.check(client=_FakeClient(boom=True))
        assert report["graph_error"]                # 教训③：显式暴露
        assert report["missing_in_graph"] == {}     # 失败时不得产出误导性"滞后"结论


class TestOneWayDiff:
    def test_missing_and_stale(self):
        client = _FakeClient(rows=[
            {"g": "g1", "s": "http://x/obj:order", "o": "订单"},
            {"g": "g1", "s": "http://x/obj:order", "o": "幽灵别名"},
        ])

        def fake_src():
            return {"order": {"订单", "交易单"}}

        orig = cap_._source_object_aliases
        cap_._source_object_aliases = fake_src
        try:
            report = cap_.check(client=client)
        finally:
            cap_._source_object_aliases = orig
        assert report["graph_error"] == ""
        assert report["missing_in_graph"] == {"order": ["交易单"]}   # 源有图无
        assert report["stale_in_graph"] == {"order": ["幽灵别名"]}  # 图有源无
