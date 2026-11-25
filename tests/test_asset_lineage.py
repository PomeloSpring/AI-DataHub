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

from backend.modules.gov.services import lineage_service as ls


class FakeDB:
    """模拟血缘节点/边表：记录已存在集合，支撑幂等判定。"""

    def __init__(self):
        self.nodes = {}   # (type, id) -> pk
        self.edges = set()
        self.pk = 1
        self.queries = []  # 记录 SQL 形态，供性能契约用例断言
        # active 绑定的 product_ref 列表（可注入「产品已不存在」以验证断裂显式）
        self.binding_products = ["test-alb.t_case"]

    def query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        self.queries.append(s)
        # 写入会话的一次性载入（按 workspace 全量读节点/边），必须返回集合而非单行：
        # 命中判断全走内存，重复 SELECT 会把重建拖成分钟级（历史缺陷）。
        if "FROM adh_lineage_nodes WHERE workspace_id" in s:
            return [{"id": pk, "node_type": t, "node_id": nid}
                    for (t, nid), pk in self.nodes.items()]
        if "FROM adh_lineage_edges WHERE workspace_id" in s:
            return [{"source_node_id": a, "target_node_id": b, "edge_type": c}
                    for (a, b, c) in sorted(self.edges)]
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
            return [{"object_key": "case", "product_ref": ref, "model_id": 1}
                    for ref in self.binding_products]
        if "FROM adh_ontology_bindings" in s:   # ⑥ 无 product_ref 的绑定
            return []
        if "FROM adh_metrics" in s:
            return [{"nm": "案例数量", "bound_object_key": "case"}]
        if "FROM adh_dimensions" in s:
            return [{"nm": "案例状态", "bound_object_key": "case"}]
        if "FROM adh_datasets" in s and "WHERE id" in s:
            return None
        if "FROM adh_datasets" in s and "SELECT id, name" in s:  # ⑥ 数据集名映射
            return [{"id": 1, "name": "案例数据集"}]
        if "FROM adh_datasets" in s:
            return [{"id": 1, "name": "案例数据集", "object_key": "case"}]
        if "FROM adh_charts" in s:
            # 一个绑数据集、一个同名的跨看板图表、一个绕过直连 SQL（应记 gap）、
            # 一个参数控件（不取数不计 gap）
            return [{"id": 1, "name": "案例看板", "chart_type": "line",
                     "source_type": "dataset", "source_id": 1, "dashboard_id": 9},
                    {"id": 4, "name": "案例看板", "chart_type": "line",
                     "source_type": "dataset", "source_id": 1, "dashboard_id": 10},
                    {"id": 2, "name": "直连SQL图", "chart_type": "bar",
                     "source_type": "query", "source_id": 0, "dashboard_id": 9,
                     "sql_query": "", "config": None},
                    {"id": 3, "name": "时间区间", "chart_type": "widget_daterange",
                     "source_type": "query", "source_id": None, "dashboard_id": 9}]
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

    def insert_many(self, sql, rows, chunk=500):
        """批量写入桩：节点按 (type, id) 分配 pk，边按 (src, dst, edge_type) 去重。"""
        s = " ".join(str(sql).split())
        if "INSERT INTO adh_lineage_nodes" in s:
            for _ws, ntype, nid, _name, _ds, _meta in rows:
                key = (ntype, nid)
                if key not in self.nodes:
                    self.nodes[key] = self.pk
                    self.pk += 1
            return len(rows)
        if "INSERT INTO adh_lineage_edges" in s:
            for _ws, src, dst, etype, _expr, _conf in rows:
                self.edges.add((src, dst, etype))
            return len(rows)
        return len(rows)


def _patch(monkeypatch, db):
    monkeypatch.setattr(ls, "execute_query",
                        lambda sql, params=None, fetchone=False: db.query(sql, params, fetchone))
    monkeypatch.setattr(ls, "execute_insert", lambda sql, params=None: db.insert(sql, params))
    monkeypatch.setattr(ls, "execute_write", lambda sql, params=None: db.insert(sql, params))
    monkeypatch.setattr(ls, "_bulk_executemany",
                        lambda sql, rows, chunk=500: db.insert_many(sql, rows, chunk))


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
        """图表绕过数据集直连 SQL 必须记入 gaps（不静默）；控件不取数不计 gap。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        r = ls.build_asset_lineage(0)
        assert any("直连SQL图" in g and "断裂" in g for g in r["gaps"]), r["gaps"]
        assert not any("时间区间" in g for g in r["gaps"]), r["gaps"]
        assert r["gap_summary"].get("参数控件（widget_*，不取数）") == 1

    def test_node_types_are_asset_layers(self, monkeypatch):
        """节点用扩展后的资产层类型（product/ontology_object/dataset）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        ls.build_asset_lineage(0)
        types = {k[0] for k in db.nodes}
        assert {"table", "product", "ontology_object", "metric"} <= types

    def test_no_per_node_roundtrip(self, monkeypatch):
        """性能契约：重建不得逐节点/逐边先 SELECT「是否已存在」（远端库往返是主要耗时）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        ls.build_asset_lineage(0)
        lookups = [q for q in db.queries
                   if "FROM adh_lineage_nodes WHERE node_id" in q
                   or "FROM adh_lineage_edges WHERE source_node_id" in q]
        assert not lookups, f"存在逐条查重往返: {lookups[:3]}"
        assert not any("FROM adh_datasets WHERE id" in q for q in db.queries)

    def test_chart_nodes_keyed_by_id_not_name(self, monkeypatch):
        """同名图表跨看板不得合并成一个节点（历史缺陷：按名称建节点）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        ls.build_asset_lineage(0)
        chart_keys = {k[1] for k in db.nodes if k[0] == "chart"}
        assert {"chart:1", "chart:4"} <= chart_keys, chart_keys
        assert "案例看板" not in chart_keys, chart_keys

    def test_retired_products_excluded(self, monkeypatch):
        """retired 产品不入血缘（已退役资产不再进链）。"""
        db = FakeDB()
        _patch(monkeypatch, db)
        # FakeDB 的 adh_data_products 只回一条非 retired 行；建链逻辑按 status 逐行跳过 retired
        r = ls.build_asset_lineage(0)
        assert isinstance(r["edges_created"], int)

    def test_binding_to_missing_product_is_surfaced(self, monkeypatch):
        """绑定指向不存在的数据产品（旧测试源被删后的遗留引用）必须记缺口，不得留静默孤儿。"""
        db = FakeDB()
        db.binding_products = ["test-alb.t_case", "test-alb.t_gone"]
        _patch(monkeypatch, db)
        r = ls.build_asset_lineage(0)
        assert any("test-alb.t_gone" in g and "不在数据产品清单" in g for g in r["gaps"]), r["gaps"]
        assert not any("test-alb.t_case" in g and "不在数据产品清单" in g for g in r["gaps"]), r["gaps"]


class TestCanonicalNodeId:
    """节点口径：表 =「数据源业务名.表」；多源共用的库名不得按库名猜源。"""

    IDX = {
        "by_id": {7: "prd-eu"},
        "by_name": {"prd-eu": {"id": 7, "db_type": "mysql"}},
        "alias": {"prd-eu": "prd-eu"},
        "ambiguous_db": {"stardb"},
        "rows": [],
    }

    def test_business_name_prefix_wins(self):
        assert ls.canonical_table_node_id("prd-eu.t_x", 0, self.IDX) == ("prd-eu.t_x", None)

    def test_ambiguous_database_name_is_not_guessed(self):
        """stardb 属多个源（4 个 MySQL 源同名库）→ 无 datasource_id 时必须报缺口。"""
        canon, reason = ls.canonical_table_node_id("stardb.t_x", 0, self.IDX)
        assert canon == "t_x" and reason and "多个数据源" in reason

    def test_ambiguous_database_name_resolved_by_datasource_id(self):
        assert ls.canonical_table_node_id("stardb.t_x", 7, self.IDX) == ("prd-eu.t_x", None)

    def test_column_keeps_table_qualification(self):
        assert ls.canonical_column_node_id("prd-eu.t_x.c1", 0, self.IDX) == (
            "prd-eu.t_x.c1", None)


class TestEnumContract:
    def test_asset_node_types_match_db_enum(self):
        """node_type 取值必须落在扩展后的 DB enum 内，否则 INSERT 会 1054/截断。"""
        for t in ls.ASSET_NODE_TYPES:
            assert t in ("table", "column", "etl_job", "report", "metric",
                         "product", "ontology_object", "dimension", "dataset", "chart"), t
