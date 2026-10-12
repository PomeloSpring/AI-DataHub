"""C1/C2/C3: Oxigraph 关系图读取层（link_graph）口径回归。

锁定：
1. 实体对齐只认 obj: 类 IRI（table:/link_ 等不入 names/edges）；
2. 关系邻接取 reified adh:Link 的 joinExpr（run_semantic_query join 链的图源）；
3. 图不可达/查询失败一律 RuntimeError（fail-loud，绝不静默空图）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.semantics.graph_rag import link_graph


class _FakeClient:
    def __init__(self, labels=(), alts=(), links=(), links2=(), boom=None):
        self.labels, self.alts = list(labels), list(alts)
        self.links, self.links2 = list(links), list(links2)
        self.boom = boom
        self.queries = []

    def query(self, sparql):
        self.queries.append(sparql)
        if self.boom:
            raise RuntimeError(self.boom)
        if "altLabel" in sparql:
            return self.alts
        if "OPTIONAL" in sparql:
            return self.links2          # object_links（带 OPTIONAL 的关系上下文查询）
        if "adh:Link" in sparql:
            return self.links           # load_link_graph（joinExpr 邻接）
        return self.labels              # rdfs:label


OBJ = "http://ai-datahub.org/ontology/obj:"


class TestLoadLinkGraph:
    def test_aligns_names_and_edges(self, monkeypatch):
        fake = _FakeClient(
            labels=[{"s": f"{OBJ}customer", "o": "客户"}],
            alts=[{"s": f"{OBJ}customer", "o": "客户公司"}],
            links=[{"f": f"{OBJ}case", "t": f"{OBJ}customer",
                    "j": "t_case_records.company_name = t_customer.name"}],
        )
        monkeypatch.setattr(link_graph, "_client", lambda: fake)
        g = link_graph.load_link_graph()
        assert all("GRAPH ?g" in q for q in fake.queries)   # 命名图扫描纪律(查默认图必空)
        assert g["names"]["客户"] == "customer"
        assert g["names"]["客户公司"] == "customer"
        assert g["names"]["customer"] == "customer"
        assert g["edges"] == {
            "case": [("customer", "t_case_records.company_name = t_customer.name")]}

    def test_non_object_iris_ignored(self, monkeypatch):
        fake = _FakeClient(
            labels=[{"s": "http://ai-datahub.org/ontology/table:t_case", "o": "t_case"},
                    {"s": "http://ai-datahub.org/ontology/obj:case", "o": "案例"}],
            links=[{"f": "http://ai-datahub.org/ontology/link_x", "t": f"{OBJ}case",
                    "j": "x = y"}],
        )
        monkeypatch.setattr(link_graph, "_client", lambda: fake)
        g = link_graph.load_link_graph()
        assert set(g["names"].values()) == {"case"}   # 只有 obj: 入实体对齐
        assert "t_case" not in g["names"] and "link_x" not in g["names"]
        assert g["edges"] == {}                        # link_ 端点不入邻接

    def test_graph_down_fails_loud(self, monkeypatch):
        def _boom():
            raise RuntimeError("知识图谱(Oxigraph)不可达")
        monkeypatch.setattr(link_graph, "_client", _boom)
        with pytest.raises(RuntimeError):
            link_graph.load_link_graph()

    def test_query_failure_fails_loud(self, monkeypatch):
        fake = _FakeClient(boom="oxigraph down")
        monkeypatch.setattr(link_graph, "_client", lambda: fake)
        with pytest.raises(RuntimeError) as e:
            link_graph.load_link_graph()
        assert "fail-loud" in str(e.value)


class TestObjectLinks:
    def test_filters_by_keys_and_keeps_join(self, monkeypatch):
        fake = _FakeClient(links2=[
            {"f": f"{OBJ}case", "t": f"{OBJ}customer", "ty": "belongs_to",
             "c": "N:1", "j": "t_case_records.company_name = t_customer.name"},
            {"f": f"{OBJ}order", "t": f"{OBJ}product", "ty": "references",
             "c": "N:M", "j": "t_order.product_id = t_product.id"},
        ])
        monkeypatch.setattr(link_graph, "_client", lambda: fake)
        rels = link_graph.object_links(["customer"])
        assert len(rels) == 1 and rels[0]["source"] == "case"
        assert rels[0]["cardinality"] == "N:1"
        assert rels[0]["join"]                              # join 保留（调用方负责剥离）
        assert len(link_graph.object_links([])) == 2        # 空过滤 = 全量
