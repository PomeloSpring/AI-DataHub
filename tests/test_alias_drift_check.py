"""派生物漂移巡检的口径回归（scripts/check_alias_drift.py）。

这个脚本首版连错三次，每次都会产出**看起来很像真的错误结论**，比没脚本更危险：
  错 1  查 Oxigraph 默认图/单个空图 → 拿到 0 条 → 误报"图谱滞后 217 条"
        （真相：三元组都在 named graph `ds:<datasource_id>` 里，altLabel 实有 79 条）
  错 2  图谱 IRI 不剥前缀 → `obj:case_file` 与 `case_file` 被判成"同名指向不同对象"，
        一次报 101 条假漂移
  错 3  拿字典的指标/维度别名去比对象的 altLabel → 误报"图谱滞后 116 条"
        （真相：指标在图谱里用 nameCn/nameEn，不是 altLabel）

故本文件只锁**口径**，不测连通性。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_alias_drift as cad


# ── 错 2：IRI 归一 ────────────────────────────────────────────────

class TestIriOwner:
    def test_strips_namespace_and_prefix(self):
        assert cad._iri_owner("http://ai-datahub.org/ontology/obj:case_file") == "case_file"
        assert cad._iri_owner("http://ai-datahub.org/ontology/obj:ai_conversation") == "ai_conversation"

    def test_plain_key_is_untouched(self):
        assert cad._iri_owner("case_file") == "case_file"

    def test_empty_is_safe(self):
        assert cad._iri_owner("") == ""
        assert cad._iri_owner(None) == ""


# ── 错 1：图谱查询必须遍历 named graph ─────────────────────────────

class _FakeClient:
    def __init__(self):
        self.sparql = None

    def query(self, sparql):
        self.sparql = sparql
        return []


class TestGraphQueryTargetsNamedGraph:
    def test_default_scans_all_named_graphs(self):
        """不传 datasource_id 时必须 `GRAPH ?g` 遍历，不能只查某一个图。"""
        fake = _FakeClient()
        _, err = cad.collect_graph_altlabels(0, client=fake)
        assert err is None
        assert "GRAPH ?g" in fake.sparql
        assert "skos:altLabel" in fake.sparql

    def test_datasource_scoped_uses_that_graph(self):
        fake = _FakeClient()
        cad.collect_graph_altlabels(1780478236183, client=fake)
        assert "GRAPH <" in fake.sparql
        assert "ds:1780478236183" in fake.sparql

    def test_graph_failure_is_reported_not_swallowed(self):
        """图谱不可用必须显式返回错误，不得静默当"无漂移"。"""
        class _Boom:
            def query(self, _s):
                raise RuntimeError("oxigraph down")

        out, err = cad.collect_graph_altlabels(0, client=_Boom())
        assert out == {}
        assert err is not None and "oxigraph down" in err

    def test_does_not_import_graph_rag(self):
        """锁住隔离：不得 import `backend.modules.mind.rag.graph_rag`。

        该包 __init__ 会连带加载 GraphRetriever 并缓存 OxigraphStore 实例，
        测试注入的假 client 就会焊进共享单例。实测后果：eval 检索链路
        `AttributeError: '_FakeClient' has no attribute 'health'`，
        `by_source` 变空（检索分桶消失）。
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(cad))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "graph_rag" not in node.module, f"不得 import {node.module}"
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "graph_rag" not in a.name, f"不得 import {a.name}"

    def test_graph_uri_matches_store_convention(self):
        """自建的 _graph_uri 必须与 OxigraphStore.graph_uri 同格式（此处只锁格式）。"""
        assert cad._graph_uri(0) == "http://ai-datahub.org/ontology/ds:0"
        assert cad._graph_uri(1780478236183) == "http://ai-datahub.org/ontology/ds:1780478236183"


# ── 错 3：分层比对口径 ────────────────────────────────────────────

class TestLayeredDiff:
    def _mk(self, alias, *owners):
        """alias=别名原文；owners=它指向的对象 key（缺省即自身）。"""
        return {"alias": alias, "owners": set(owners or (alias,)), "graphs": set()}

    def test_same_alias_same_owner_is_not_conflict(self):
        """对象层与图谱指向同一对象 → 不是漂移（曾因 IRI 前缀误报 101 条）。"""
        obj = {"case_file": self._mk("case_file")}
        graph = {"case_file": self._mk("case_file")}
        r = cad.diff(obj, graph, {}, {})
        assert r["obj_vs_graph_conflict"] == []
        assert r["graph_only"] == []
        assert r["missing_in_graph"] == []

    def test_same_alias_different_owner_is_conflict(self):
        obj = {"订单": self._mk("订单", "order")}
        graph = {"订单": self._mk("订单", "dc_case_record")}
        r = cad.diff(obj, graph, {}, {})
        assert len(r["obj_vs_graph_conflict"]) == 1
        assert "order" in r["obj_vs_graph_conflict"][0]

    def test_dict_only_alias_is_NOT_a_drift(self):
        """指标/维度有自己的名字，不进对象 aliases —— 这是正常的，不是漂移。"""
        obj = {"case_file": self._mk("case_file")}
        graph = {"case_file": self._mk("case_file")}
        dic = {"ai_conversation_count": self._mk("ai_conversation_count"),
               "AI会话数": self._mk("AI会话数", "ai_conversation_count")}
        r = cad.diff(obj, graph, dic, {})
        assert r["dict_vs_obj_conflict"] == []
        assert r["dict_alias_count"] == 2
        assert r["missing_in_graph"] == []

    def test_dict_and_obj_same_alias_different_owner_is_conflict(self):
        obj = {"订单": self._mk("订单", "order")}
        graph = {"订单": self._mk("订单", "order")}
        dic = {"订单": self._mk("订单", "dc_case_record")}
        r = cad.diff(obj, graph, dic, {})
        assert len(r["dict_vs_obj_conflict"]) == 1

    def test_terms_pending_is_progress_not_drift(self):
        obj = {"case_file": self._mk("case_file")}
        graph = {"case_file": self._mk("case_file")}
        terms = {"del_flag": self._mk("del_flag"), "hospital": self._mk("hospital")}
        r = cad.diff(obj, graph, {}, terms)
        assert sorted(r["terms_not_adopted"]) == ["del_flag", "hospital"]
        assert r["obj_vs_graph_conflict"] == []

    def test_graph_lag_is_tracked_separately(self):
        """对象有、图谱无 = 图谱滞后（重建图谱可收敛），与"真漂移"分开报。"""
        obj = {"case_file": self._mk("case_file")}
        r = cad.diff(obj, {}, {}, {})
        assert r["missing_in_graph"] == ["'case_file' → ['case_file']"]
        assert r["obj_vs_graph_conflict"] == []
