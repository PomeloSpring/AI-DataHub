"""血缘图接口（GET /lineage/graph）的数据源筛选契约。

锁住三件事：
1. **数据源筛选 = 表 + 正向可达资产**：只筛表节点会把下游链整段截掉，图上只剩孤立表；
2. **datasources 按全量节点算**：筛选后仍要能切回其他源（否则下拉里只剩当前源）；
3. **前端契约字段**：name 必有、metadata 是对象（DB 存 JSON 字符串）、边带 source_id/target_id。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.gov.api import lineage as api  # noqa: E402

NODES = [
    {"id": 1, "node_type": "table", "node_id": "prd-cn.t_case", "node_name": "t_case", "datasource_id": 7},
    {"id": 2, "node_type": "product", "node_id": "prd-cn.t_case", "node_name": "prd-cn.t_case", "datasource_id": 0},
    {"id": 3, "node_type": "ontology_object", "node_id": "case", "node_name": "case", "datasource_id": 0},
    {"id": 4, "node_type": "metric", "node_id": "案例数量", "node_name": "案例数量", "datasource_id": 0},
    {"id": 5, "node_type": "table", "node_id": "prd-en.t_case", "node_name": "t_case", "datasource_id": 8},
    {"id": 6, "node_type": "dataset", "node_id": "英文案例集", "node_name": "英文案例集", "datasource_id": 0},
    {"id": 7, "node_type": "chart", "node_id": "chart:9", "node_name": "案例趋势图", "datasource_id": 0,
     "metadata": json.dumps({"chart_type": "line"}, ensure_ascii=False)},
    {"id": 8, "node_type": "column", "node_id": "prd-cn.t_case.case_id", "node_name": "case_id", "datasource_id": 7},
]

EDGES = [
    {"id": 11, "source_node_id": 1, "target_node_id": 2, "edge_type": "produces"},
    {"id": 12, "source_node_id": 2, "target_node_id": 3, "edge_type": "binds_to"},
    {"id": 13, "source_node_id": 3, "target_node_id": 4, "edge_type": "defines"},
    {"id": 14, "source_node_id": 5, "target_node_id": 6, "edge_type": "derive"},
    {"id": 15, "source_node_id": 6, "target_node_id": 7, "edge_type": "visualizes"},
]


def _patch(monkeypatch):
    def fake_query(sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "FROM adh_lineage_nodes" in s:
            return [dict(n) for n in NODES]
        if "FROM adh_lineage_edges" in s:
            return [dict(e) for e in EDGES]
        return []

    monkeypatch.setattr(api, "execute_query", fake_query)


def test_full_graph_carries_frontend_contract(monkeypatch):
    _patch(monkeypatch)
    g = api.get_lineage_graph(workspace_id=0, datasource="", node_types="")
    assert len(g["nodes"]) == len(NODES) and len(g["edges"]) == len(EDGES)
    assert all(n["name"] for n in g["nodes"]), "节点必须有可读 name"
    chart = next(n for n in g["nodes"] if n["node_type"] == "chart")
    assert chart["metadata"] == {"chart_type": "line"}, "metadata 必须解析成对象"
    edge = g["edges"][0]
    assert edge["source_id"] == edge["source_node_id"] and edge["target_id"] == edge["target_node_id"]
    assert g["datasources"] == ["prd-cn", "prd-en"]


def test_datasource_filter_keeps_downstream_chain(monkeypatch):
    """选一个源：该源的表 + 正向可达的产品/本体对象/指标都要在，别源节点不在。"""
    _patch(monkeypatch)
    g = api.get_lineage_graph(workspace_id=0, datasource="prd-cn", node_types="")
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {1, 2, 3, 4}, ids
    assert {e["edge_type"] for e in g["edges"]} == {"produces", "binds_to", "defines"}


def test_datasource_filter_keeps_full_datasource_list(monkeypatch):
    """datasources 按全量节点算：筛完 prd-cn 仍能看到 prd-en（否则切不回去）。"""
    _patch(monkeypatch)
    g = api.get_lineage_graph(workspace_id=0, datasource="prd-cn", node_types="")
    assert g["datasources"] == ["prd-cn", "prd-en"]


def test_unknown_datasource_returns_empty_not_error(monkeypatch):
    """筛一个没有血缘的源：返回空图（前端显示「该数据源暂无血缘」），不抛错。"""
    _patch(monkeypatch)
    g = api.get_lineage_graph(workspace_id=0, datasource="no-such-source", node_types="")
    assert g["nodes"] == [] and g["edges"] == []
    assert g["datasources"] == ["prd-cn", "prd-en"]


def test_node_types_filter_also_trims_edges(monkeypatch):
    _patch(monkeypatch)
    g = api.get_lineage_graph(workspace_id=0, datasource="", node_types="table,product")
    assert {n["node_type"] for n in g["nodes"]} == {"table", "product"}
    # 只留 table/product 后，指向本体对象/指标的边必须一并消失（不留悬挂边）
    assert {e["edge_type"] for e in g["edges"]} == {"produces"}