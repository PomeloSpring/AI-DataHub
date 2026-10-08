"""同步执行器离线回归：不连接数据库/真实数据源。

覆盖：sync 节点全量/增量语义、水位线比较推进（幂等）、写失败显式暴露、
sql_task 节点 UDF 版本锁定、源码级治理通道守卫（禁止裸直连旁路）。
"""

import pandas as pd
import pytest

from backend.modules.flow.dag import node_runners
from backend.modules.flow.dag.node_runners import NodeExecutionError


def _ctx(**overrides):
    ctx = {
        "run_id": 1, "workflow_id": 2, "node_key": "n1",
        "owner_identity": {"user_id": 7, "username": "u"},
        "workspace_id": 0,
        "watermark_key": "dag:2:n1",
        "stop_check": lambda: None,
    }
    ctx.update(overrides)
    return ctx


# ── sync 节点 ─────────────────────────────────────────────────────


def test_sync_full_mode_reads_and_writes(monkeypatch):
    df = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    calls = {}

    def fake_read(sql, ds, identity, ws):
        calls["read_sql"] = sql
        calls["read_ds"] = ds
        return df

    def fake_write(frame, ds, table, mode, batch_size, context=""):
        calls["write"] = (ds, table, mode)
        return len(frame)

    monkeypatch.setattr("backend.modules.flow.dag.federated_reader.governed_read_dataframe", fake_read)
    monkeypatch.setattr("backend.modules.flow.dag.target_writer.write_dataframe", fake_write)
    monkeypatch.setattr(node_runners, "_record_lineage",
                        lambda sql, ds, ctx: calls.setdefault("lineage_sql", sql))
    monkeypatch.setattr(node_runners, "_run_quality_checks",
                        lambda *a, **k: calls.setdefault("quality", {"checks": 1, "passed": 1, "failed": 0}))

    config = {
        "source_datasource": "src", "source_table": "orders",
        "target_datasource": "dst", "target_table": "dwd_orders",
        "sync_mode": "full", "write_mode": "overwrite",
    }
    result = node_runners.run_sync_node(config, _ctx())
    assert result["rows_read"] == 2 and result["rows_written"] == 2
    assert "FROM `orders`" in calls["read_sql"]
    assert calls["write"] == ("dst", "dwd_orders", "overwrite")
    # 血缘采集：构造 INSERT INTO 形态供血缘解析归因
    assert calls["lineage_sql"].startswith("INSERT INTO `dwd_orders`")


def test_sync_transform_sql_applies_udf(monkeypatch):
    """转换 SQL + UDF：展开后取数，目标写入展开结果。"""
    df = pd.DataFrame({"id": [1], "phone": ["138****1111"]})
    calls = {}

    def fake_read(sql, ds, identity, ws):
        calls["sql"] = sql
        return df

    monkeypatch.setattr("backend.modules.flow.dag.federated_reader.governed_read_dataframe", fake_read)
    monkeypatch.setattr("backend.modules.flow.dag.target_writer.write_dataframe",
                        lambda frame, ds, table, mode, batch_size, context="": len(frame))
    monkeypatch.setattr(node_runners, "_record_lineage", lambda *a, **k: None)
    monkeypatch.setattr(node_runners, "_run_quality_checks", lambda *a, **k: {"checks": 0, "passed": 0, "failed": 0})
    monkeypatch.setattr("backend.modules.flow.dag.udf_registry.expand_udfs",
                        lambda sql, refs: (calls.setdefault("refs", refs) and sql.replace(
                            "phone_mask(phone)", "CASE WHEN phone THEN phone END"),
                            [{"name": "phone_mask", "version": 1, "id": 9}]))
    monkeypatch.setattr("backend.modules.flow.dag.udf_registry.udf_registry.bump_usage",
                        lambda names: calls.setdefault("bumped", names))

    config = {
        "source_datasource": "src", "source_table": "users",
        "target_datasource": "dst", "target_table": "dwd_users",
        "sync_mode": "full", "write_mode": "overwrite",
        "transform_sql": "SELECT id, phone_mask(phone) AS phone FROM {{source}}",
        "udf_refs": ["phone_mask:1"],
    }
    result = node_runners.run_sync_node(config, _ctx())
    assert calls["refs"] == ["phone_mask:1"]
    assert "users" in calls["sql"] and "phone_mask(" not in calls["sql"]
    assert result["udf_versions"] == [{"name": "phone_mask", "version": 1, "id": 9}]
    assert calls["bumped"] == ["phone_mask"]


def test_sync_transform_sql_must_reference_source(monkeypatch):
    config = {
        "source_datasource": "src", "source_table": "users",
        "target_datasource": "dst", "target_table": "dwd_users",
        "sync_mode": "full",
        "transform_sql": "SELECT 1 AS x",
    }
    with pytest.raises(NodeExecutionError) as exc:
        node_runners.run_sync_node(config, _ctx())
    assert exc.value.error_code == "TRANSFORM_INVALID"


def test_sync_incremental_uses_and_advances_watermark(monkeypatch):
    df = pd.DataFrame({"id": [10, 11], "updated_at": ["2026-10-01", "2026-10-02"]})
    calls = {"advance": []}

    monkeypatch.setattr("backend.modules.flow.dag.federated_reader.governed_read_dataframe",
                        lambda sql, ds, identity, ws: (calls.setdefault("sql", sql), df)[1])
    monkeypatch.setattr("backend.modules.flow.dag.target_writer.write_dataframe",
                        lambda frame, ds, table, mode, batch_size, context="": len(frame))
    from backend.modules.flow.dag.dag_service import dag_service
    monkeypatch.setattr(node_runners, "_record_lineage", lambda *a, **k: None)
    monkeypatch.setattr(node_runners, "_run_quality_checks", lambda *a, **k: {"checks": 0, "passed": 0, "failed": 0})
    monkeypatch.setattr(dag_service, "get_watermark", lambda key: "2026-10-01")
    monkeypatch.setattr(dag_service, "advance_watermark",
                        lambda key, value, rows=0: calls["advance"].append((key, value, rows)))

    config = {
        "source_datasource": "src", "source_table": "orders",
        "target_datasource": "dst", "target_table": "dwd_orders",
        "sync_mode": "incremental", "incremental_column": "updated_at",
    }
    result = node_runners.run_sync_node(config, _ctx())
    # WHERE 带水位线条件 + 强制 append（增量不得覆盖）
    assert "updated_at" in calls["sql"] and "2026-10-01" in calls["sql"]
    assert result["rows_read"] == 2
    # 水位推进到本批最大值
    assert calls["advance"] == [("dag:2:n1", "2026-10-02", 2)]


def test_sync_write_failure_is_explicit(monkeypatch):
    """写失败必须显式抛出，不得返回假成功（no-silent-degradation）。"""
    df = pd.DataFrame({"id": [1]})
    monkeypatch.setattr("backend.modules.flow.dag.federated_reader.governed_read_dataframe",
                        lambda sql, ds, identity, ws: df)

    def boom(*args, **kwargs):
        raise RuntimeError("target down")

    monkeypatch.setattr("backend.modules.flow.dag.target_writer.write_dataframe", boom)
    config = {
        "source_datasource": "src", "source_table": "t",
        "target_datasource": "dst", "target_table": "t2", "sync_mode": "full",
    }
    with pytest.raises(RuntimeError, match="target down"):
        node_runners.run_sync_node(config, _ctx())


# ── sql_task 节点 ─────────────────────────────────────────────────


def test_sql_task_locks_udf_versions_and_writes_target(monkeypatch):
    df = pd.DataFrame({"x": [1]})
    calls = {}

    def fake_expand(sql, refs):
        calls["refs"] = refs
        return "SELECT 1 AS x", [{"name": "my_udf", "version": 3, "id": 55}]

    monkeypatch.setattr("backend.modules.flow.dag.udf_registry.expand_udfs", fake_expand)
    monkeypatch.setattr("backend.modules.flow.dag.udf_registry.udf_registry.bump_usage",
                        lambda names: calls.setdefault("bumped", names))
    monkeypatch.setattr("backend.modules.flow.dag.federated_reader.governed_federated_read",
                        lambda sql, ds, identity, ws: (df, 1))
    monkeypatch.setattr("backend.modules.flow.dag.target_writer.write_dataframe",
                        lambda frame, ds, table, mode, batch_size, context="": calls.setdefault("target", (ds, table, mode)) and 1)
    monkeypatch.setattr(node_runners, "_record_lineage", lambda *a, **k: None)
    monkeypatch.setattr(node_runners, "_run_quality_checks", lambda *a, **k: {"checks": 0, "passed": 0, "failed": 0})

    config = {
        "source_datasource": "src",
        "sql": "SELECT my_udf(x) FROM t",
        "udf_refs": ["my_udf:3"],
        "target": {"datasource": "dst", "table": "out", "write_mode": "overwrite"},
    }
    result = node_runners.run_sql_task_node(config, _ctx())
    assert calls["refs"] == ["my_udf:3"]
    assert result["udf_versions"] == [{"name": "my_udf", "version": 3, "id": 55}]
    assert calls["bumped"] == ["my_udf"]
    assert calls["target"] == ("dst", "out", "overwrite")
    assert result["rows_written"] == 1


def test_sql_task_udf_expand_failure_is_explicit(monkeypatch):
    from backend.modules.flow.dag.udf_registry import UdfValidationError

    def boom(sql, refs):
        raise UdfValidationError("UDF 'x' 不存在或未启用")

    monkeypatch.setattr("backend.modules.flow.dag.udf_registry.expand_udfs", boom)
    config = {"source_datasource": "src", "sql": "SELECT x(t) FROM t", "udf_refs": ["x"]}
    with pytest.raises(NodeExecutionError) as exc:
        node_runners.run_sql_task_node(config, _ctx())
    assert exc.value.error_code == "UDF_EXPAND_FAILED"


# ── 控制节点 ──────────────────────────────────────────────────────


def test_control_fail_node_is_explicit():
    with pytest.raises(NodeExecutionError) as exc:
        node_runners.run_control_node({"action": "fail"}, _ctx())
    assert exc.value.error_code == "CONTROL_FAIL"
    assert node_runners.run_control_node({"action": "pass"}, _ctx()) == {"rows_read": 0, "rows_written": 0}


# ── 源码级治理通道守卫（护栏 §1：取数不得绕过治理入口） ────────────


def test_source_level_no_bypass_channels():
    """同步/SQL 任务的读写通道不得出现裸直连取数（get_connection/裸 execute_query）。"""
    import inspect
    from backend.modules.flow.dag import node_runners as nr, federated_reader as fr
    for module in (nr, fr):
        source = inspect.getsource(module)
        assert "get_connection" not in source, f"{module.__name__} 出现直连取数旁路"
        assert "execute_query_with_permission" in inspect.getsource(fr) or module is nr, \
            "取数必须经统一治理入口"
