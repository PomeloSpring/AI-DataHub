"""SQL⇄DAG 双向转换离线回归。

覆盖：多语句拆解依赖构建、INSERT/CTAS 目标提取、歧义产出报错、
非法定位报错、导出拓扑序、导出不含凭据（no_leak）。
"""

import pytest

from backend.modules.flow.dag.sql_translator import sql_to_dag, dag_to_sql
from backend.modules.flow.dag.dag_validator import DagValidationError


def test_sql_to_dag_builds_dependencies():
    script = (
        "CREATE TABLE agg AS SELECT id, amount FROM orders WHERE status = 'paid';\n"
        "INSERT INTO doris_ds.dwh.agg SELECT id, amount FROM agg;\n"
        "SELECT * FROM agg LIMIT 10;"
    )
    graph = sql_to_dag(script, default_datasource="mysql_biz")
    assert len(graph["nodes"]) == 3
    edges = {(e["from"], e["to"]) for e in graph["edges"]}
    assert ("step_1", "step_2") in edges
    assert ("step_1", "step_3") in edges


def test_sql_to_dag_extracts_target_config():
    graph = sql_to_dag("INSERT INTO doris_ds.dwh.agg SELECT 1 AS a", default_datasource="src")
    node = graph["nodes"][0]
    assert node["config"]["target"] == {
        "datasource": "doris_ds", "table": "agg", "write_mode": "overwrite"}
    assert node["config"]["sql"].upper().startswith("SELECT")


def test_sql_to_dag_rejects_ambiguous_producer():
    with pytest.raises(DagValidationError, match="歧义"):
        sql_to_dag("INSERT INTO t SELECT 1; INSERT INTO t SELECT 2;", default_datasource="x")


def test_sql_to_dag_rejects_write_statements():
    with pytest.raises(DagValidationError):
        sql_to_dag("DELETE FROM t", default_datasource="x")


def test_sql_to_dag_rejects_empty():
    with pytest.raises(DagValidationError):
        sql_to_dag("   ", default_datasource="x")


def test_dag_to_sql_topological_order_and_comments():
    graph = {
        "nodes": [
            {"key": "b", "name": "下游", "type": "sql_task",
             "config": {"source_datasource": "s", "sql": "SELECT * FROM t1 LIMIT 1"}},
            {"key": "a", "name": "上游", "type": "sql_task",
             "config": {"source_datasource": "s", "sql": "SELECT 1 AS x",
                        "target": {"datasource": "s", "table": "t1", "write_mode": "overwrite"}}},
        ],
        "edges": [{"from": "a", "to": "b"}],
    }
    out = dag_to_sql(graph)
    assert out.index("[a]") < out.index("[b]")
    assert "INSERT INTO `s`.`t1`" in out
    assert "依赖: a" in out


def test_dag_to_sql_sync_node_rendered_as_insert_select():
    graph = {"nodes": [{"key": "s1", "name": "同步", "type": "sync", "config": {
        "source_datasource": "src", "source_table": "orders",
        "target_datasource": "dst", "target_table": "dwd_orders",
        "sync_mode": "incremental", "incremental_column": "updated_at",
    }}], "edges": []}
    out = dag_to_sql(graph)
    assert "INSERT INTO `dwd_orders`" in out
    assert ":watermark" in out


def test_dag_to_sql_no_credential_leak():
    """导出脚本不得含连接凭据/主机（护栏 §7）。"""
    graph = {"nodes": [{"key": "q", "type": "sql_task", "config": {
        "source_datasource": "mysql_biz", "sql": "SELECT 1 AS a LIMIT 1",
    }}], "edges": []}
    out = dag_to_sql(graph).lower()
    for leak in ("password", "host", "secret", "token", "@"):
        assert leak not in out


def test_dag_to_sql_rejects_cycle():
    graph = {
        "nodes": [
            {"key": "a", "type": "sql_task", "config": {"sql": "SELECT 1", "source_datasource": "s"}},
            {"key": "b", "type": "sql_task", "config": {"sql": "SELECT 1", "source_datasource": "s"}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    with pytest.raises(DagValidationError):
        dag_to_sql(graph)
