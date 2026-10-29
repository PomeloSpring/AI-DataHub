"""统一大图(unified)视图 · 分域组装 / 去重 / 主干优先回归锁(离线, 无 SPARQL)。

背景: 前端不再拆分「本体总览/表关系/业务知识/血缘桥接」四视图, 改消费后端
`unified` 单图。锁定的契约:
- 业务/源域: 本体总览 + 业务知识 + 血缘桥接(仅对象绑定的表), **不含**全量物理表
  关系视图 —— 否则会把不相关表灌进预算、把列截没导致 hasColumn 边全悬空
  (历史缺陷: "200 表 / 0 边");
- 系统域(ds=-1): 系统元数据以表/列为主体, 保留表关系视图;
- 节点/边按 id 去重; 排序 = (类型优先级, 连接度降序), 保证 nodes[:limit] 截断时
  高连接语义枢纽与桥接骨架不被剪掉;
- get_graph_data(GraphType.UNIFIED) 末尾剪掉端点缺失的悬空边。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.semhub.graph.graph_service import GraphService  # noqa: E402
from backend.models.graph import GraphType, GraphNode, GraphEdge  # noqa: E402

SYSTEM_DS = -1  # oxigraph_store.SYSTEM_DATASOURCE_ID


def _node(nid, ntype, label=""):
    return GraphNode(id=nid, label=ntype, properties={"label": label or nid})


def _edge(src, tgt, rel):
    return GraphEdge(id=f"{src}-{rel}-{tgt}", source=src, target=tgt, type=rel, properties={})


def _svc_with_views(overview, business, table, lineage):
    """构造只 stub 四个分视图 builder 的 GraphService(不触网)。"""
    svc = GraphService.__new__(GraphService)
    svc._store = type("_S", (), {"graph_uri": staticmethod(lambda ds: f"adh:ds:{ds}")})()
    svc._client = type("_C", (), {"query": staticmethod(lambda q: [])})()
    svc._get_stats = lambda ds: type("_St", (), {})()
    svc._get_ontology_overview_graph = lambda g, ds, lim: overview
    svc._get_business_knowledge_graph = lambda g, ds, lim: business
    svc._get_table_relation_graph = lambda g, ds, lim: table
    svc._get_data_lineage_graph = lambda g, ds, lim: lineage
    return svc


def test_business_scope_uses_lineage_not_raw_table_relation():
    """业务/源域: 物理层只走血缘桥接(对象绑定的表), 不灌全量物理表关系视图。"""
    overview = ([_node("adh:obj:A", "Object"), _node("adh:obj:B", "Object")],
                [_edge("adh:obj:A", "adh:obj:B", "关联")])
    business = ([_node("adh:metric:M", "Metric"), _node("adh:obj:A", "Object")],  # obj:A 重复
                [_edge("adh:metric:M", "adh:obj:A", "defines"),
                 _edge("adh:obj:A", "adh:obj:B", "关联")])  # 边重复
    table = ([_node("adh:table:RAW", "Table")],
             [_edge("adh:table:RAW", "adh:column:c1", "hasColumn")])  # 全量物理表(应排除)
    lineage = ([_node("adh:obj:A", "Object"), _node("adh:table:BOUND", "Table")],
               [_edge("adh:obj:A", "adh:table:BOUND", "boundTo")])

    nodes, edges = _svc_with_views(overview, business, table, lineage)._get_unified_graph(
        "adh:ds:0", 0, 200)

    ids = [n.id for n in nodes]
    assert "adh:table:RAW" not in ids, "业务域不应合并全量物理表关系视图"
    assert "adh:table:BOUND" in ids, "业务域应保留血缘桥接的绑定表"
    assert ids.count("adh:obj:A") == 1, "重复节点未去重"
    eids = [e.id for e in edges]
    assert eids.count("adh:obj:A-关联-adh:obj:B") == 1, "重复边未去重"
    assert set(ids) == {"adh:obj:A", "adh:obj:B", "adh:metric:M", "adh:table:BOUND"}


def test_system_scope_keeps_table_relation_and_skips_lineage():
    """系统域(ds=-1): 表/列即主体, 保留表关系视图, 不走血缘桥接。"""
    overview = ([_node("adh:obj:A", "Object")], [])
    business = ([], [])
    table = ([_node("adh:table:RAW", "Table")], [])
    lineage = ([_node("adh:table:BOUND", "Table")], [])

    nodes, _ = _svc_with_views(overview, business, table, lineage)._get_unified_graph(
        "adh:ds:-1", SYSTEM_DS, 200)

    ids = {n.id for n in nodes}
    assert "adh:table:RAW" in ids, "系统域应保留表关系视图"
    assert "adh:table:BOUND" not in ids, "系统域不应走血缘桥接"


def test_orders_semantic_before_physical_and_hub_first():
    """排序: 语义主干排在物理叶子前; 同类型内高连接度(hub)优先, 抗截断。"""
    overview = ([_node("adh:obj:HUB", "Object"), _node("adh:obj:LEAF", "Object")],
                [_edge("adh:obj:HUB", "adh:table:T", "boundTo")])  # HUB 度更高
    business = ([_node("adh:metric:M", "Metric"), _node("adh:term:T1", "Term")], [])
    table = ([], [])  # 业务域不调用
    lineage = ([_node("adh:table:T", "Table"), _node("adh:column:C", "Column")],
               [_edge("adh:table:T", "adh:column:C", "hasColumn")])

    nodes, _ = _svc_with_views(overview, business, table, lineage)._get_unified_graph(
        "adh:ds:0", 0, 200)

    order = [n.id for n in nodes]
    labels = [n.label for n in nodes]
    # 语义全部排在物理之前
    assert labels.index("Column") > labels.index("Object")
    assert labels.index("Table") > labels.index("Metric")
    # 同类型内 hub(度大) 先于 leaf
    assert order.index("adh:obj:HUB") < order.index("adh:obj:LEAF")


def test_get_graph_data_unified_prunes_dangling_edges():
    """端点被截断丢弃的悬空边, get_graph_data 末尾必须剪掉。"""
    overview = ([_node("adh:obj:A", "Object")], [])
    business = ([], [])
    table = ([], [])
    lineage = ([_node("adh:table:T", "Table")],
               [_edge("adh:table:T", "adh:column:GONE", "hasColumn")])  # 端点缺失
    svc = _svc_with_views(overview, business, table, lineage)

    data = svc.get_graph_data(graph_type=GraphType.UNIFIED, datasource_id=0, limit=200)
    kept_ids = {n.id for n in data.nodes}
    assert "adh:column:GONE" not in kept_ids
    assert all(e.source in kept_ids and e.target in kept_ids for e in data.edges), "存在悬空边"


def test_unified_strips_properties_by_default_toggle_restores():
    """图面克制(graph-visualization §3): 属性(Property)明细默认不平铺(超级 hub 图会挤成一团),
    仅 include_properties=True 时展开。"""
    overview = ([_node("adh:obj:A", "Object"),
                 _node("adh:prop:A.x", "Property"), _node("adh:prop:A.y", "Property")],
                [_edge("adh:obj:A", "adh:prop:A.x", "属性"),
                 _edge("adh:obj:A", "adh:prop:A.y", "属性")])
    svc = _svc_with_views(overview, ([], []), ([], []), ([], []))

    d_nodes, _ = svc._get_unified_graph("adh:ds:0", 0, 200)
    assert all(n.label != "Property" for n in d_nodes), "默认不应平铺属性明细节点"
    # 属性边此时悬空(端点已剔除), 由 get_graph_data 末尾统一剪枝
    data = svc.get_graph_data(graph_type=GraphType.UNIFIED, datasource_id=0, limit=200)
    assert all(n.label != "Property" for n in data.nodes)
    assert all(e.type != "属性" for e in data.edges), "悬空属性边应被剪掉"

    w_nodes, w_edges = svc._get_unified_graph("adh:ds:0", 0, 200, include_properties=True)
    assert {n.id for n in w_nodes} >= {"adh:prop:A.x", "adh:prop:A.y"}, "开关开应恢复属性节点"
    assert any(e.type == "属性" for e in w_edges), "开关开应保留属性边"


# ── 按选中模型聚焦(_filter_graph_to_model): 选谁看谁，不被其他业务模型串味 ──

def _stub_get_model(monkeypatch, model):
    import backend.modules.catalog.services.ontology_service as osvc
    monkeypatch.setattr(osvc, "get_model", lambda mid: model)


def test_filter_graph_to_model_keeps_own_objects_and_one_hop_neighbors(monkeypatch):
    """本模型对象 + 其直连邻居(指标/绑定表)保留；其他模型对象及其专属邻居剔除。"""
    _stub_get_model(monkeypatch, {"id": 1, "kind": "business", "objects": [{"key": "A"}]})
    nodes = [_node("adh:obj:A", "Object"), _node("adh:obj:OTHER", "Object"),
             _node("adh:metric:M", "Metric"), _node("adh:table:T", "Table")]
    edges = [_edge("adh:obj:A", "adh:metric:M", "hasMetric"),
             _edge("adh:obj:A", "adh:table:T", "boundTo"),
             _edge("adh:obj:OTHER", "adh:metric:M", "hasMetric")]  # 别的模型也连到 M
    svc = GraphService.__new__(GraphService)

    kept_nodes, kept_edges = svc._filter_graph_to_model(nodes, edges, 1)
    ids = {n.id for n in kept_nodes}
    assert "adh:obj:A" in ids, "本模型对象应保留"
    assert "adh:metric:M" in ids and "adh:table:T" in ids, "本模型一跳邻居应保留"
    assert "adh:obj:OTHER" not in ids, "其他模型对象不得串味"
    assert all(e.source != "adh:obj:OTHER" and e.target != "adh:obj:OTHER" for e in kept_edges)


def test_filter_graph_to_model_empty_when_model_not_in_graph(monkeypatch):
    """本模型对象一个都不在图里(未重建) → 返回空，让前端如实显示“需重建”，不掺别模型。"""
    _stub_get_model(monkeypatch, {"id": 9, "kind": "business", "objects": [{"key": "rum_event"}]})
    nodes = [_node("adh:obj:user", "Object")]  # 只有别的模型
    edges = []
    svc = GraphService.__new__(GraphService)
    kept_nodes, kept_edges = svc._filter_graph_to_model(nodes, edges, 9)
    assert kept_nodes == [] and kept_edges == []


def test_get_graph_data_no_model_id_returns_full_graph(monkeypatch):
    """不传 model_id=全图（独立知识图谱页/检索口径不变）。"""
    from backend.models.graph import GraphStats
    called = {"filter": False}
    svc = _svc_with_views(
        ([_node("adh:obj:A", "Object"), _node("adh:obj:B", "Object")], []),
        ([], []), ([], []), ([], []))
    svc._get_stats = lambda ds: GraphStats()
    orig = svc._filter_graph_to_model
    svc._filter_graph_to_model = lambda *a, **k: called.__setitem__("filter", True) or orig(*a, **k)
    data = svc.get_graph_data(graph_type=GraphType.UNIFIED, datasource_id=0, limit=200)
    assert called["filter"] is False, "未传 model_id 不应走按模型过滤"
    assert {n.id for n in data.nodes} == {"adh:obj:A", "adh:obj:B"}
