"""DAG 引擎离线回归：不连接数据库、消息队列或真实数据源。

覆盖：图校验（环/key 重复/悬空边/节点配置）、拓扑规划（失败跳过/状态聚合）、
执行核心（run_key 幂等、节点认领竞态只执行一次、失败重试、失败传播、
对外错误脱敏、显式失败不假成功）。
"""

import pytest

from services.dataflow.dag.dag_validator import validate_graph, DagValidationError
from services.dataflow.dag import dag_planner as planner
from services.dataflow.dag import dag_executor


def _graph(nodes, edges):
    return {"nodes": nodes, "edges": [{"from": a, "to": b} for a, b in edges]}


def _control(key):
    return {"key": key, "type": "control", "config": {"action": "pass"}}


# ── 校验器 ────────────────────────────────────────────────────────


def test_validate_rejects_cycle():
    with pytest.raises(DagValidationError, match="循环依赖"):
        validate_graph(_graph([_control("a"), _control("b")], [("a", "b"), ("b", "a")]),
                       check_datasources=False)


def test_validate_rejects_duplicate_keys():
    with pytest.raises(DagValidationError, match="key 重复"):
        validate_graph({"nodes": [_control("a"), _control("a")], "edges": []},
                       check_datasources=False)


def test_validate_rejects_dangling_edge():
    with pytest.raises(DagValidationError, match="不存在的节点"):
        validate_graph(_graph([_control("a")], [("a", "ghost")]), check_datasources=False)


def test_validate_sync_requires_incremental_column():
    node = {"key": "s1", "type": "sync", "config": {
        "source_datasource": "x", "source_table": "t1",
        "target_datasource": "y", "target_table": "t2",
        "sync_mode": "incremental",
    }}
    with pytest.raises(DagValidationError, match="增量列"):
        validate_graph({"nodes": [node], "edges": []}, check_datasources=False)


def test_validate_sql_task_rejects_ddl():
    node = {"key": "q1", "type": "sql_task", "config": {
        "source_datasource": "x", "sql": "DROP TABLE users",
    }}
    with pytest.raises(DagValidationError):
        validate_graph({"nodes": [node], "edges": []},
                       check_datasources=False, check_udfs=False)


def test_validate_rejects_unknown_node_type():
    node = {"key": "z", "type": "python_script", "config": {}}
    with pytest.raises(DagValidationError, match="未知节点类型"):
        validate_graph({"nodes": [node], "edges": []}, check_datasources=False)


def test_validate_sync_transform_sql_with_source_placeholder():
    """转换 SQL 的 {{source}} 占位必须在校验时先代入再解析（否则误报语法错误）。"""
    node = {"key": "s1", "type": "sync", "config": {
        "source_datasource": "x", "source_table": "users",
        "target_datasource": "y", "target_table": "dwd_users",
        "sync_mode": "full",
        "transform_sql": "SELECT id, name FROM {{source}}",
    }}
    assert validate_graph({"nodes": [node], "edges": []},
                          check_datasources=False, check_udfs=False)


def test_validate_sync_transform_sql_must_reference_source():
    node = {"key": "s1", "type": "sync", "config": {
        "source_datasource": "x", "source_table": "users",
        "target_datasource": "y", "target_table": "dwd_users",
        "sync_mode": "full",
        "transform_sql": "SELECT 1 AS x",
    }}
    with pytest.raises(DagValidationError, match="未引用源表"):
        validate_graph({"nodes": [node], "edges": []},
                       check_datasources=False, check_udfs=False)


def test_validate_accepts_diamond():
    graph = _graph([_control("a"), _control("b"), _control("c"), _control("d")],
                   [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    assert validate_graph(graph, check_datasources=False)["nodes"]


# ── 规划器 ────────────────────────────────────────────────────────


def test_planner_ready_and_skip_propagation():
    graph = _graph([_control("a"), _control("b"), _control("c")], [("a", "b"), ("a", "c")])
    assert planner.ready_nodes(graph, {}) == ["a"]
    status = {"a": "failed"}
    skipped = planner.propagate_skips(graph, status)
    assert sorted(skipped) == ["b", "c"]
    assert status["b"] == "skipped"
    assert planner.run_status_aggregate(status) == "failed"


def test_planner_partial_when_some_success():
    status = {"a": "success", "b": "failed", "c": "skipped"}
    assert planner.run_status_aggregate(status) == "partial"
    assert planner.run_status_aggregate({"a": "success", "b": "success"}) == "success"
    assert planner.run_status_aggregate({"a": "timeout"}) == "failed"


def test_planner_skips_downstream_of_skipped():
    graph = _graph([_control("a"), _control("b"), _control("c")], [("a", "b"), ("b", "c")])
    status = {"a": "success", "b": "skipped"}
    planner.propagate_skips(graph, status)
    assert status["c"] == "skipped"


# ── 执行核心（打桩 dag_service，离线） ─────────────────────────────


class FakeDagService:
    """内存版 dag_service 桩：记录调用，支持注入 claim 竞争与节点失败。"""

    def __init__(self, workflow, claim_run=True, claim_node=True, fail_nodes=None):
        self.workflow = workflow
        self.claim_run_ok = claim_run
        self.claim_node_ok = claim_node
        self.fail_nodes = set(fail_nodes or [])
        self.run = {"id": 100, "workflow_id": 1, "status": "queued"}
        self.finished_runs = []
        self.node_runs = []
        self.finished_nodes = []
        self.skipped = []
        self.attempts = {}

    def get_run(self, run_id):
        return dict(self.run)

    def get_workflow(self, wf_id):
        return self.workflow

    def claim_run(self, run_id, worker_id, timeout):
        return self.claim_run_ok

    def renew_run_lease(self, run_id, timeout):
        return True

    def finish_run(self, run_id, status, error_code="", error_message="", stats=None):
        self.finished_runs.append((run_id, status, error_code, error_message))
        self.run["status"] = status
        return True

    def mark_run_stats(self, wf_id, status):
        pass

    def create_node_run(self, run_id, wf_id, key, ntype, config, attempt=1):
        self.attempts[key] = self.attempts.get(key, 0) + 1
        node_run_id = 1000 + len(self.node_runs)
        self.node_runs.append((node_run_id, key, ntype, attempt))
        return node_run_id

    def claim_node_run(self, node_run_id, worker_id, timeout):
        return self.claim_node_ok

    def finish_node_run(self, node_run_id, status, rows_read=0, rows_written=0,
                        elapsed_ms=0, error_code="", error_message=""):
        self.finished_nodes.append((node_run_id, status, error_code, error_message))
        return True

    def skip_node_run(self, run_id, key, ntype, reason, attempt=1):
        self.skipped.append((key, reason))
        return 9000 + len(self.skipped)


def _workflow(graph, max_retries=0):
    return {"id": 1, "name": "wf", "graph_json": graph, "owner_id": 42,
            "workspace_id": 0, "timeout_seconds": 600, "max_retries": max_retries}


def test_execute_run_success(monkeypatch):
    graph = _graph([_control("a"), _control("b")], [("a", "b")])
    fake = FakeDagService(_workflow(graph))
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    result = dag_executor.execute_run_core(100, "test-worker")
    assert result["status"] == "success"
    assert fake.finished_runs[-1][1] == "success"
    assert result["stats"]["nodes_success"] == 2


def test_execute_run_node_failure_marks_failed_not_fake_success(monkeypatch):
    """节点失败必须显式 failed/部分成功，绝不假成功（no-silent-degradation）。"""
    graph = _graph([_control("a"), _control("b")], [("a", "b")])
    fake = FakeDagService(_workflow(graph), fail_nodes={"a"})
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    monkeypatch.setattr(dag_executor, "run_node",
                        lambda node, ctx: (_ for _ in ()).throw(RuntimeError("boom with host=10.0.0.1")))
    result = dag_executor.execute_run_core(100, "test-worker")
    assert result["status"] == "failed"
    # 对外错误脱敏：原始报错（含 host）不外泄，回通用文案
    _, status, code, message = fake.finished_runs[-1]
    assert status == "failed"
    assert "10.0.0.1" not in message
    # 下游节点跳过并记录原因
    assert any(key == "b" for key, _reason in fake.skipped)


def test_execute_run_retries_then_fails(monkeypatch):
    graph = _graph([_control("a")], [])
    fake = FakeDagService(_workflow(graph, max_retries=2))
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    monkeypatch.setattr(dag_executor, "run_node",
                        lambda node, ctx: (_ for _ in ()).throw(ValueError("bad config")))
    result = dag_executor.execute_run_core(100, "test-worker")
    assert result["status"] == "failed"
    # max_retries=2 → 总尝试 3 次
    assert fake.attempts["a"] == 3
    # ValueError 属可诊断错误，原文对外（但同样不含凭据）
    assert "bad config" in fake.finished_nodes[-1][3]


def test_execute_run_claim_race_returns_duplicate(monkeypatch):
    """两实例并发只执行一次：认领失败直接返回 duplicate，不重复执行节点。"""
    graph = _graph([_control("a")], [])
    fake = FakeDagService(_workflow(graph), claim_run=False)
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    result = dag_executor.execute_run_core(100, "worker-2")
    assert result.get("duplicate") is True
    assert fake.node_runs == []


def test_execute_run_unclaimed_owner_rejected(monkeypatch):
    graph = _graph([_control("a")], [])
    fake = FakeDagService(_workflow(graph))
    fake.workflow = {**_workflow(graph), "owner_id": 0}
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    result = dag_executor.execute_run_core(100, "test-worker")
    assert result["status"] == "failed"
    assert result["error_code"] == "CONFIG_INVALID"
    assert "OWNER_REQUIRED" in fake.finished_runs[-1][3]


def test_execute_run_invalid_graph_fails_loud(monkeypatch):
    fake = FakeDagService(_workflow({"nodes": [], "edges": []}))
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    result = dag_executor.execute_run_core(100, "test-worker")
    assert result["status"] == "failed"
    assert result["error_code"] == "GRAPH_INVALID"


def test_execute_run_cancelled_stops(monkeypatch):
    """运行中收到 cancelled → 中止并落终态，不继续执行剩余节点。"""
    graph = _graph([_control("a"), _control("b")], [])

    class CancelAfterFirst(FakeDagService):
        def finish_node_run(self, node_run_id, status, **kwargs):
            super().finish_node_run(node_run_id, status, **kwargs)
            self.run["status"] = "cancelled"
            return True

    fake = CancelAfterFirst(_workflow(graph))
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    result = dag_executor.execute_run_core(100, "test-worker")
    assert result["status"] == "cancelled"
