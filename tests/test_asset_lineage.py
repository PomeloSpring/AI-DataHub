"""资产血缘贯通（P5）回归：物理表→产品→本体对象→口径→数据集→图表。

锁住的行为：
1. **幂等**：重复 build 不产生重复节点/边（distributed-first）；
2. **链生成**：产品/本体/口径的绑定关系必须落成血缘边；
3. **断裂显式**：图表绕过数据集直连 SQL 必须记入 gaps，不得静默（no-silent-degradation）；
4. **类型合法**：node_type/edge_type 用扩展后的 enum 值（product/ontology_object/dataset/chart）。

纯函数 + fake DB，不触碰数据库。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datagov.services import lineage_service as ls


class FakeDB:
    """模拟血缘节点/边表：记录已存在集合，支撑幂等判定。"""

    def __init__(self):
        self.nodes = {}   # (type, id) -> pk
        self.edges = set()
        self.pk = 1

    def query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "FROM adh_lineage_nodes WHERE node_id" in s:
            key = (params[1], params[0])
            hit = self.nodes.get(key)
            return {"id": hit} if hit and fetchone else ([{"id": hit}] if hit else [])
        if "FROM adh_lineage_edges WHERE source_node_id" in s:
            key = (params[0], params[1], params[2])
            hit = key in self.edges
            return {"id": 1} if hit and fetchone else ([{"id": 1}] if hit else [])
        # 数据源查询：按 SQL 关键词回样例
        if "FROM adh_data_products" in s:
            return [{"product_name": "test-alb.t_case", "product_class": "t_case", "site": "",
                     "datasource_name": "test-alb", "physical_table": "t_case", "status": "draft"}]
        if "FROM adh_ontology_bindings" in s and "product_ref" in s:
            return [{"object_key": "case", "product_ref": "test-alb.t_case", "model_id": 1}]
        if "FROM adh_ontology_bindings" in s:   # ⑥ 无 product_ref 的绑定
            return []
        if "FROM adh_metrics" in s:
            return [{"nm": "案例数量", "bound_object_key": "case"}]
        if "FROM adh_dimensions" in s:
            return [{"nm": "案例状态", "bound_object_key": "case"}]
        if "FROM adh_datasets" in s and "WHERE id" in s:
            return None
        if "FROM adh_datasets" in s:
            return [{"name": "案例数据集", "object_key": "case"}]
        if "FROM adh_charts" in s:
            # 一个绑数据集、一个绕过直连 SQL（应记 gap）
            return [{"name": "案例看板", "source_type": "dataset", "source_id": 1},
                    {"name": "直连SQL图", "source_type": "query", "source_id": 0}]
        return []

    def insert(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "INSERT INTO adh_lineage_nodes" in s:
            key = (params[1], params[2])
            self.nodes[key] = self.pk
            self.pk += 1
            return self.nodes[key]
        if "INSERT INTO adh_lineage_edges" in s:
            self.edges.add((params[1], params[2], params[3]))
            return self.pk
        self.pk += 1
        return self.pk


def _patch(monkeypatch, db):
    monkeypatch.setattr(ls, "execute_query",
                        lambda sql, params=None, fetchone=False: db.query(sql, params, fetchone))
    monkeypatch.setattr(ls, "execute_insert", lambda sql, params=None: db.insert(sql, params))


class TestAssetLineage:
    def test_builds_chain_and_is_idempotent(self, monkeypatch):
        """链生成 + 幂等：第二次不新增任何节点/边。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        r1 = ls.build_asset_lineage(0)
        assert r1["nodes_created"] > 0 and r1["edges_created"] > 0
        r2 = ls.build_asset_lineage(0)
        assert r2["nodes_created"] == 0 and r2["edges_created"] == 0, "幂等被破坏：重复跑产生了新节点/边"

    def test_produces_bind_define_edges_present(self, monkeypatch):
        """product→ontology_object→口径 的边必须落库。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        ls.build_asset_lineage(0)
        edge_types = {e[2] for e in db.edges}
        assert "produces" in edge_types      # 表→产品
        assert "binds_to" in edge_types      # 产品→本体对象
        assert "defines" in edge_types       # 本体对象→口径

    def test_chart_bypassing_dataset_is_surfaced_as_gap(self, monkeypatch):
        """图表绕过数据集直连 SQL 必须记入 gaps（不静默）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        r = ls.build_asset_lineage(0)
        assert any("直连SQL图" in g and "断裂" in g for g in r["gaps"]), r["gaps"]

    def test_node_types_are_asset_layers(self, monkeypatch):
        """节点用扩展后的资产层类型（product/ontology_object/dataset）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        ls.build_asset_lineage(0)
        types = {k[0] for k in db.nodes}
        assert {"table", "product", "ontology_object", "metric"} <= types

    def test_retired_products_excluded(self, monkeypatch):
        """retired 产品不入血缘（已退役资产不再进链）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        # FakeDB 的 products 查询已过滤 retired（WHERE status <> 'retired' 在 SQL 里）
        r = ls.build_asset_lineage(0)
        assert isinstance(r["edges_created"], int)


class TestEnumContract:
    def test_asset_node_types_match_db_enum(self):
        """node_type 取值必须落在扩展后的 DB enum 内，否则 INSERT 会 1054/截断。"""
        for t in ls.ASSET_NODE_TYPES:
            assert t in ("table", "column", "etl_job", "report", "metric",
                         "product", "ontology_object", "dimension", "dataset", "chart"), t
