"""本体总览取数契约单测(离线, 假 SPARQL client): 治"边被剪成孤立圆点"的回归锁。

契约:
- 边端点必在节点集内(随边取回, 不允许悬空边被上层剪光);
- 孤点对象(无 Link)也入图;
- join 物理表达式绝不出现在返回的节点/边属性里(数据源黑盒护栏 §7);
- 边携 link 名字与基数, 节点携 label/object_key/aliases。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.semhub.graph.graph_service import GraphService
from backend.common.rdf.namespaces import ADH_NS


class FakeStore:
    @staticmethod
    def graph_uri(ds):
        return f"{ADH_NS}ds:{ds}"


class FakeClient:
    def query(self, sparql):
        if "owl:Class" in sparql:
            return [
                {"iri": f"{ADH_NS}obj:case", "label": "案例", "comment": "就诊案例",
                 "pt": "t_case", "qm": "materialized", "sc": "large", "st": "bound"},
                {"iri": f"{ADH_NS}obj:patient", "label": "患者", "comment": "",
                 "pt": "", "qm": "", "sc": "", "st": ""},   # 无 Link 的孤点对象
            ]
        if "skos:altLabel" in sparql:
            return [{"iri": f"{ADH_NS}obj:case", "alt": "工单"},
                    {"iri": f"{ADH_NS}obj:case", "alt": "就诊案例"}]
        if "adh:Link" in sparql:
            return [{
                "from": f"{ADH_NS}obj:case", "fromL": "案例",
                "to": f"{ADH_NS}obj:patient", "toL": "患者",
                "lname": "涉及", "ltype": "references", "lcard": "N:1",
            }]
        return []


def _svc():
    s = GraphService.__new__(GraphService)
    s._store = FakeStore()
    s._client = FakeClient()
    return s


class TestOntologyOverview:
    def setup_method(self):
        self.nodes, self.edges = _svc()._get_ontology_overview_graph(
            FakeStore.graph_uri(0), 0, 200)

    def test_all_object_nodes_present_including_isolated(self):
        keys = {n.properties.get("object_key") for n in self.nodes}
        assert keys == {"case", "patient"}          # 孤点患者也入图
        assert all(n.label == "Object" for n in self.nodes)

    def test_edge_endpoints_never_dangling(self):
        ids = {n.id for n in self.nodes}
        for e in self.edges:
            assert e.source in ids and e.target in ids   # 剪枝后无悬空边的前提

    def test_edge_carries_link_name_and_cardinality(self):
        assert len(self.edges) == 1
        e = self.edges[0]
        assert e.type == "涉及"
        assert e.properties.get("cardinality") == "N:1"

    def test_node_props_label_aliases_binding(self):
        case = next(n for n in self.nodes if n.properties["object_key"] == "case")
        assert case.properties["label"] == "案例"
        assert "工单" in case.properties["aliases"]
        assert case.properties["binding"]["sync_state"] == "bound"

    def test_join_expr_never_leaks(self):
        # join 物理表达式不落进总览响应(护栏 §7); primary_table 属管理端可见的绑定徽标, 另议
        for n in self.nodes:
            assert "joinExpr" not in n.properties and "join" not in n.properties
        for e in self.edges:
            assert "joinExpr" not in e.properties and "join" not in e.properties


class _TypeAwareClient:
    """按 FILTER(?ntype = adh:X) 分类型返回的假 client。

    锁历史缺陷：单条 `IN(6 类型)+LIMIT 200` 时 Oxigraph 返回顺序里维度先占满预算，
    指标被整体截掉，业务全景图只剩对象+维度。
    """

    DIMS = 200
    METRICS = 5

    def query(self, sparql):
        if "owl:Class" in sparql:
            return [{"iri": f"{ADH_NS}obj:case", "label": "案例"}]
        if "skos:altLabel" in sparql or "adh:Link" in sparql:
            return []
        ntype = _type_of(sparql)
        if ntype == "Dimension":
            return [{"iri": f"{ADH_NS}dimension:d{i}", "ntype": f"{ADH_NS}Dimension",
                     "label": f"维度{i}"} for i in range(self.DIMS)]
        if ntype == "Metric":
            return [{"iri": f"{ADH_NS}metric:m{i}", "ntype": f"{ADH_NS}Metric",
                     "label": f"指标{i}"} for i in range(self.METRICS)]
        return []


def _type_of(sparql: str) -> str:
    for t in ("Action", "Property", "Metric", "Dimension", "Rule", "Scenario"):
        if f"?ntype = adh:{t}" in sparql:
            return t
    return ""


def test_metrics_not_starved_by_dimensions():
    """维度体量远大于指标时，指标不得被挤出总览（按类型取 + 主干优先排序）。"""
    from collections import Counter

    svc = GraphService.__new__(GraphService)
    svc._store = FakeStore()
    svc._client = _TypeAwareClient()
    nodes, _ = svc._get_ontology_overview_graph(FakeStore.graph_uri(0), 0, 200)

    labels = Counter(n.label for n in nodes)
    assert labels["Metric"] == _TypeAwareClient.METRICS
    assert labels["Dimension"] == _TypeAwareClient.DIMS
    # 截断保护：对象与指标排在维度之前，调用方 nodes[:limit] 砍维度不砍指标
    assert all(n.label in ("Object", "Metric")
               for n in nodes[:1 + _TypeAwareClient.METRICS])
