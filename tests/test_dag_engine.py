"""DAG 引擎离线回归：不连接数据库、消息队列或真实数据源。

覆盖：图校验（环/key 重复/悬空边/节点配置）、拓扑规划（失败跳过/状态聚合）、
执行核心（run_key 幂等、节点认领竞态只执行一次、失败重试、失败传播、
对外错误脱敏、显式失败不假成功）。
"""

import pytest

from backend.modules.flow.dag.dag_validator import (
    DagValidationError, validate_graph, validate_run_params,
)
from backend.modules.flow.dag import dag_planner as planner
from backend.modules.flow.dag import dag_executor


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


# ── 物化节点校验（保存/运行前 fail-loud，不留到执行期才炸） ──────────

def _materialize(**config):
    base = {"dataset": "订单明细", "target_datasource": "dw",
            "target_table": "dwd_order_detail", "write_mode": "overwrite"}
    base.update(config)
    return {"key": "m1", "type": "materialize", "config": base}


def test_validate_materialize_requires_dataset_and_target():
    for missing, hint in (("dataset", "缺少数据集"), ("target_datasource", "缺少目标数据源"),
                          ("target_table", "目标表名")):
        node = _materialize(**{missing: ""})
        with pytest.raises(DagValidationError, match=hint):
            validate_graph({"nodes": [node], "edges": []}, check_datasources=False)


def test_validate_materialize_rejects_bad_mode_and_budget():
    # 写入模式只允许三档（sync 的 full/incremental 不得混入物化）
    with pytest.raises(DagValidationError, match="写入模式不合法"):
        validate_graph({"nodes": [_materialize(write_mode="incremental")], "edges": []},
                       check_datasources=False)
    # 预算越界即拒：行数上限不得超过编译器硬顶，秒数不得超一天
    with pytest.raises(DagValidationError, match="行数预算"):
        validate_graph({"nodes": [_materialize(budget={"max_rows": 10 ** 9})],
                        "edges": []}, check_datasources=False)
    with pytest.raises(DagValidationError, match="超时预算"):
        validate_graph({"nodes": [_materialize(budget={"max_seconds": 0})],
                        "edges": []}, check_datasources=False)
    with pytest.raises(DagValidationError, match="批量写入行数"):
        validate_graph({"nodes": [_materialize(batch_size=50000)], "edges": []},
                       check_datasources=False)


def test_validate_materialize_partition_requires_date_arg_and_column():
    base = _materialize(write_mode="partition_overwrite")
    with pytest.raises(DagValidationError, match="日期参数名"):
        validate_graph({"nodes": [base], "edges": []}, check_datasources=False)
    node = _materialize(write_mode="partition_overwrite",
                        partition={"date_arg": "dt", "column": ""})
    with pytest.raises(DagValidationError, match="分区日期列名"):
        validate_graph({"nodes": [node], "edges": []}, check_datasources=False)
    # Doris 分区名模板必须带日期占位，否则每天都写同一个分区（错数）
    node = _materialize(write_mode="partition_overwrite",
                        partition={"date_arg": "dt", "column": "dt",
                                   "target_partition": "pstatic"})
    with pytest.raises(DagValidationError, match="日期占位"):
        validate_graph({"nodes": [node], "edges": []}, check_datasources=False)


def test_validate_materialize_partition_accepts_full_config():
    node = _materialize(write_mode="partition_overwrite",
                        partition={"date_arg": "dt", "column": "dt",
                                   "target_partition": "p{yyyyMMdd}", "lookback_days": 3},
                        budget={"max_rows": 100000, "max_seconds": 600}, batch_size=2000)
    assert validate_graph({"nodes": [node], "edges": []}, check_datasources=False)["nodes"]


# ── 节点结果摘要（落 result_json 前必须脱敏：不落 SQL/凭据/列明细） ──

def test_result_summary_whitelists_only_platform_metrics():
    summary = dag_executor._result_summary({
        "dataset": "订单明细", "target": "dw.dwd_order_detail",
        "write_mode": "partition_overwrite",
        "partitions": [{"date": "2026-11-21", "rows_written": 10, "reconciled": True}],
        "reconciled": True, "strategy": "rename_swap",
        "budget": {"max_rows": 100}, "columns": ["id", "amount", "secret"],
        # 以下均不得外泄（护栏 §7）
        "sql": "SELECT * FROM secret_table", "password": "p@ss",
        "host": "10.0.0.1", "rows": [{"id": 1}],
    })
    assert summary["dataset"] == "订单明细"
    assert summary["reconciled"] is True
    assert summary["strategy"] == "rename_swap"
    assert summary["partitions"][0]["date"] == "2026-11-21"
    # 列清单只回数量，不回列名
    assert summary["column_count"] == 3
    for leaked in ("sql", "password", "host", "rows", "columns"):
        assert leaked not in summary, leaked


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
        self.node_results = []
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
                        elapsed_ms=0, error_code="", error_message="", result=None):
        self.finished_nodes.append((node_run_id, status, error_code, error_message))
        # 结果摘要（物化分区/对账/写入策略）也要能断言：执行日志页靠它展示
        self.node_results.append(result)
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


# ── 运行参数（物化时间窗口）：校验 + 注入节点 context ───────────────


def _windowed_workflow() -> dict:
    """带日期分区物化节点的工作流（参数有落点）。"""
    node = {"key": "m1", "type": "materialize", "config": {
        "dataset": "ds", "target_datasource": "doris", "target_table": "t",
        "write_mode": "partition_overwrite",
        "partition": {"column": "dt", "date_arg": "dt", "lookback_days": 2},
    }}
    return _workflow(_graph([node], []))


def test_validate_run_params_normalizes_and_keeps_empty():
    wf = _windowed_workflow()
    assert validate_run_params(wf, {}) == {}
    assert validate_run_params(wf, None) == {}
    assert validate_run_params(
        wf, {"start_date": " 2026-01-01 ", "end_date": "2026-01-03"}
    ) == {"start_date": "2026-01-01", "end_date": "2026-01-03"}


def test_validate_run_params_rejects_unknown_key():
    """未知参数必须拒：静默忽略 = “指定了起始日”却按回看窗口跑（假初始化）。"""
    with pytest.raises(DagValidationError, match="不支持的运行参数"):
        validate_run_params(_windowed_workflow(), {"from": "2026-01-01"})


def test_validate_run_params_rejects_bad_date_and_reversed_range():
    with pytest.raises(DagValidationError, match="YYYY-MM-DD"):
        validate_run_params(_windowed_workflow(), {"start_date": "20260101"})
    with pytest.raises(DagValidationError, match="晚于结束日期"):
        validate_run_params(_windowed_workflow(),
                            {"start_date": "2026-02-01", "end_date": "2026-01-01"})


def test_validate_run_params_requires_windowed_materialize_node():
    """没有日期分区物化节点时参数无处生效，必须当场拒绝。"""
    wf = _workflow(_graph([_control("a")], []))
    with pytest.raises(DagValidationError, match="没有按日期分区的物化节点"):
        validate_run_params(wf, {"start_date": "2026-01-01"})


def test_validate_run_params_normalizes_grains():
    """grains 只接受 day/month/year 子集；去重并去空白，其余交给执行期跳过。"""
    wf = _windowed_workflow()
    assert validate_run_params(wf, {"grains": [" Month ", "year", "year"]}) == {
        "grains": ["month", "year"]}
    assert validate_run_params(wf, {"grains": ["day"]}) == {"grains": ["day"]}


def test_validate_run_params_rejects_bad_grains():
    """未知粒度/空列表必须拒：静默忽略会把“只跑月/年档”跑成全部粒度。"""
    wf = _windowed_workflow()
    with pytest.raises(DagValidationError, match="未知物化粒度"):
        validate_run_params(wf, {"grains": ["week"]})
    with pytest.raises(DagValidationError, match="非空列表"):
        validate_run_params(wf, {"grains": []})


def test_execute_run_injects_run_params_into_node_context(monkeypatch):
    """运行参数必须落到节点 context（物化执行器从这里读窗口）。"""
    graph = _graph([_control("a")], [])
    fake = FakeDagService(_workflow(graph))
    fake.run["params_json"] = {"start_date": "2026-01-01", "end_date": "2026-01-02"}
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    seen: list = []
    monkeypatch.setattr(dag_executor, "run_node",
                        lambda node, ctx: seen.append(dict(ctx)) or {"rows_read": 0, "rows_written": 0})

    result = dag_executor.execute_run_core(100, "test-worker")

    assert result["status"] == "success"
    assert seen and seen[0]["run_params"] == {
        "start_date": "2026-01-01", "end_date": "2026-01-02"}


def test_execute_run_without_params_passes_empty_dict(monkeypatch):
    """日常调度（无参数）仍能跑：context 里是空 dict，不是缺失或 None。"""
    graph = _graph([_control("a")], [])
    fake = FakeDagService(_workflow(graph))
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    seen: list = []
    monkeypatch.setattr(dag_executor, "run_node",
                        lambda node, ctx: seen.append(dict(ctx)) or {"rows_read": 0, "rows_written": 0})

    dag_executor.execute_run_core(100, "test-worker")

    assert seen[0]["run_params"] == {}


def test_execute_run_marks_param_skipped_nodes_as_skipped(monkeypatch):
    """运行参数跳过的节点记 skipped（不记 success），统计落 nodes_skipped。"""
    graph = _graph([_control("a")], [])
    fake = FakeDagService(_workflow(graph))
    fake.run["params_json"] = {"grains": ["month"]}
    monkeypatch.setattr(dag_executor, "dag_service", fake)
    monkeypatch.setattr(dag_executor, "_resolve_owner", lambda wf: {"user_id": 42, "username": "u"})
    monkeypatch.setattr(dag_executor, "run_node", lambda node, ctx: {
        "rows_read": 0, "rows_written": 0, "skipped": True,
        "window": {"mode": "skipped",
                   "reason": "本次运行只选择了 ['month'] 粒度（未包含 day）"}})

    result = dag_executor.execute_run_core(100, "test-worker")

    assert result["status"] == "success"
    assert fake.finished_nodes[-1][1] == "skipped"
    assert result["stats"]["nodes_skipped"] == 1
    assert result["stats"]["nodes_failed"] == 0


# ── 运行页统计与节点明细同源（禁止 stats_json 缺失时显示 0/15） ──────────


def test_aggregate_run_stats_counts_latest_attempt_only(monkeypatch):
    """统计按每个节点最新 attempt 的终态累加：重试成功不得再计一次失败。"""
    from backend.modules.flow.dag import dag_service as svc

    rows = [
        {"run_id": 1, "node_key": "cn_day", "status": "skipped", "rows_read": 0, "rows_written": 0},
        {"run_id": 1, "node_key": "cn_month", "status": "success", "rows_read": 8309, "rows_written": 8309},
        {"run_id": 1, "node_key": "cn_year", "status": "success", "rows_read": 902, "rows_written": 902},
        {"run_id": 1, "node_key": "eu_day", "status": "failed", "rows_read": 0, "rows_written": 0},
        {"run_id": 1, "node_key": "eu_month", "status": "cancelled", "rows_read": 0, "rows_written": 0},
    ]
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: rows)

    stats = svc.dag_service.aggregate_run_stats(1, total_nodes=15)

    assert stats["nodes_total"] == 15          # 总数取编排节点数，不是已执行数
    assert stats["nodes_success"] == 2
    assert stats["nodes_failed"] == 1          # cancelled 不算失败
    assert stats["nodes_skipped"] == 1
    assert stats["nodes_cancelled"] == 1
    assert stats["rows_written"] == 9211


def test_list_runs_merges_node_derived_stats(monkeypatch):
    """运行历史清单与详情同源：stats_json 为空（被中止/在跑）时行数也来自节点终态。"""
    from backend.modules.flow.dag import dag_service as svc

    def _fake_query(sql, params=None, fetchone=False):
        if "COUNT(*) AS total" in sql:
            return {"total": 2}
        if "adh_dag_runs r" in sql:
            return [
                {"id": 11, "workflow_id": 7, "stats_json": None, "params_json": None},
                {"id": 12, "workflow_id": 7, "stats_json": '{"nodes_total": 15}',
                 "params_json": "{}"},
            ]
        assert "run_id IN (%s,%s)" in sql          # 整页一次查，不 N+1
        return [
            {"run_id": 11, "node_key": "a", "status": "success",
             "rows_read": 100, "rows_written": 100},
            {"run_id": 11, "node_key": "b", "status": "failed",
             "rows_read": 0, "rows_written": 0},
            {"run_id": 12, "node_key": "c", "status": "skipped",
             "rows_read": 0, "rows_written": 0},
        ]

    monkeypatch.setattr(svc, "execute_query", _fake_query)

    out = svc.dag_service.list_runs(page=1, size=20)

    first = out["items"][0]["stats_json"]
    assert first["nodes_success"] == 1 and first["nodes_failed"] == 1
    assert first["rows_written"] == 100
    second = out["items"][1]["stats_json"]
    assert second["nodes_total"] == 15           # 编排节点数不被已执行数覆盖
    assert second["nodes_skipped"] == 1


def test_list_workflows_derives_last_status_from_runs(monkeypatch):
    """物化任务清单的「上次状态」以运行实例为准：workflows.last_status 只是过期缓存。"""
    from backend.modules.flow.dag import dag_service as svc

    empty_graph = '{"nodes": [], "edges": []}'

    def _fake_query(sql, params=None, fetchone=False):
        if "COUNT(*) AS total FROM adh_dag_workflows" in sql:
            return {"total": 2}
        if "FROM adh_dag_workflows" in sql:
            return [
                {"id": 7, "name": "case", "graph_json": empty_graph,
                 "last_status": "success", "last_run_at": "2026-11-24T09:00:00"},
                {"id": 8, "name": "order", "graph_json": empty_graph,
                 "last_status": "failed", "last_run_at": "2026-11-24T09:00:00"},
            ]
        if "status IN ('queued','running')" in sql:
            return [{"workflow_id": 8, "id": 12, "status": "running",
                     "started_at": "2026-11-24T12:20:00",
                     "created_at": "2026-11-24T12:20:00", "finished_at": None}]
        return [{"workflow_id": 7, "id": 11, "status": "cancelled",
                 "started_at": "2026-11-24T10:49:05",
                 "created_at": "2026-11-24T10:49:00",
                 "finished_at": "2026-11-24T11:24:32"}]

    monkeypatch.setattr(svc, "execute_query", _fake_query)

    out = svc.dag_service.list_workflows(page=1, size=20)
    items = {i["name"]: i for i in out["items"]}

    assert items["case"]["last_status"] == "cancelled"   # 旧缓存 success 被真实运行覆盖
    assert items["case"]["last_run_at"] == "2026-11-24T10:49:05"  # 与运行历史「开始」同口径
    assert items["order"]["last_status"] == "running"    # 在跑的优先于更早的终态


def test_run_detail_derives_stats_from_node_runs(monkeypatch):
    """运行详情接口以节点终态为准：stats_json 为空（被中止/在跑）也必须有真实统计。"""
    from backend.modules.flow.api import dag as dag_api

    class _Svc:
        def get_run(self, run_id):
            return {"id": run_id, "workflow_id": 7, "status": "failed", "stats_json": {}}

        def get_workflow(self, workflow_id):
            return {"id": workflow_id, "graph_json": {"nodes": [_control("a"), _control("b")], "edges": []}}

        def list_node_runs(self, run_id):
            return [{"id": 1, "node_key": "a", "node_type": "control", "status": "success",
                     "attempt": 1, "rows_read": 5, "rows_written": 5, "node_config": {}, "result_json": {}}]

        def aggregate_run_stats(self, run_id, total_nodes=0):
            return {"nodes_total": total_nodes, "nodes_success": 1, "nodes_failed": 0,
                    "nodes_skipped": 0, "nodes_cancelled": 0,
                    "rows_read": 5, "rows_written": 5}

    monkeypatch.setattr(dag_api, "dag_service", _Svc())

    detail = dag_api.get_run(1)

    assert detail["run"]["stats_json"]["nodes_success"] == 1
    assert detail["run"]["stats_json"]["nodes_total"] == 2
    assert detail["nodes"][0]["rows_written"] == 5


# ── 卡死回收 / 取消收尾（SQL 契约，防“列表永远运行中”） ──────────────


def _patch_writes(monkeypatch, results):
    """拦截 dag_service.execute_write，记录 SQL 并按序返回影响行数。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = []

    def fake_write(sql, params=None):
        calls.append((" ".join(sql.split()), params))
        idx = len(calls) - 1
        return results[idx] if idx < len(results) else 1

    monkeypatch.setattr(dag_svc_mod, "execute_write", fake_write)
    return calls


def test_cancel_run_closes_running_nodes(monkeypatch):
    """取消 run 必须同批收尾节点，否则节点永久停在 running。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_writes(monkeypatch, [1, 1])

    assert dag_svc_mod.dag_service.cancel_run(77) is True

    assert len(calls) == 2, calls
    run_sql, run_params = calls[0]
    node_sql, node_params = calls[1]
    assert "UPDATE adh_dag_runs" in run_sql and "status='cancelled'" in run_sql
    assert "UPDATE adh_dag_node_runs" in node_sql
    assert "status='cancelled'" in node_sql
    assert "run_id=%s AND status IN ('queued','running')" in node_sql
    assert node_params[2] == 77


def test_cancel_run_does_not_touch_nodes_when_run_already_terminal(monkeypatch):
    """run 已终态（更新 0 行）时不得改节点，保持终态保护语义。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_writes(monkeypatch, [0])

    assert dag_svc_mod.dag_service.cancel_run(78) is False
    assert len(calls) == 1


def test_cleanup_stale_runs_judges_by_lease_and_collects_orphans(monkeypatch):
    """卡死判据：running 看租约（长跑不被 started_at 阈值误杀）+ queued 排队超时 + 孤儿节点。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_writes(monkeypatch, [2, 1, 3])

    out = dag_svc_mod.dag_service.cleanup_stale_runs(10)

    assert out == {"dag_runs": 2, "dag_queued_runs": 1, "dag_node_runs": 3}
    run_sql = calls[0][0]
    queued_sql = calls[1][0]
    node_sql = calls[2][0]
    # 正常长跑：租约由执行循环续，不能用「开始超过 N 分钟」判死
    assert "lease_expires_at <= %s" in run_sql
    assert "OR started_at <= %s" not in run_sql
    # queued 没有租约，只能按入队时间收口（否则永远显示「排队中」）
    assert "status='queued'" in queued_sql and "QUEUE_TIMEOUT" in queued_sql
    assert "COALESCE(created_at, started_at) <= %s" in queued_sql
    # 终态 run 下的残留节点必须被孤儿判据收口
    assert "status NOT IN ('queued','running')" in node_sql
    assert "status IN ('queued','running')" in node_sql


def test_cleanup_does_not_touch_freshly_queued_run(monkeypatch):
    """刚入队的排队实例不得被排队超时判据误杀（阈值只对积压生效）。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_writes(monkeypatch, [0, 0, 0])

    dag_svc_mod.dag_service.cleanup_stale_runs(10)

    # 阈值必须明显大于单个物化任务的正常耗时（≤ 8 分钟），给顺序排队留余量
    assert dag_svc_mod.QUEUE_STALL_MINUTES >= 20, dag_svc_mod.QUEUE_STALL_MINUTES
    queue_params = calls[1][1]
    assert queue_params[2] is not None and len(str(queue_params[2])) == 19


def test_claim_run_lease_is_capped_to_one_hour(monkeypatch):
    """整轮预算（本项目 8h）不得直接当租约，否则卡死 run 8 小时无人回收。"""
    import datetime as _dt

    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_writes(monkeypatch, [1])

    assert dag_svc_mod.dag_service.claim_run(79, "worker-a", 28800) is True

    lease_str = calls[0][1][1]
    lease = _dt.datetime.strptime(lease_str, "%Y-%m-%d %H:%M:%S")
    remaining = (lease - _dt.datetime.now()).total_seconds()
    assert 3500 <= remaining <= 3600, remaining


# ── 心跳续租 + 残留实例「读时收口」 ────────────────────────────────


def test_run_heartbeat_lease_is_short_enough(monkeypatch):
    """心跳租约必须远短于整轮预算：执行体死掉后用户等的是分钟，不是小时。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    interval = dag_svc_mod.RUN_HEARTBEAT_INTERVAL_SECONDS
    lease = dag_svc_mod.RUN_HEARTBEAT_LEASE_SECONDS
    # 续租间隔 < 租约（留一轮容错），且租约 ≤ 300s（失联判死不超过 5 分钟）
    assert 0 < interval < lease <= 300, (interval, lease)


def test_is_stale_run_judges_by_lease_only():
    """失联判据只看租约：长跑靠心跳续租，不能被「开始超过 N 分钟」误判。"""
    import datetime as _dt

    from backend.modules.flow.dag.dag_service import is_stale_run

    now = _dt.datetime.now()

    def fmt(delta_seconds):
        return (now + _dt.timedelta(seconds=delta_seconds)).strftime("%Y-%m-%d %H:%M:%S")

    # 租约已过期 → 执行体失联
    assert is_stale_run({"status": "running", "lease_expires_at": fmt(-5)}) is True
    # 心跳仍在续租 → 不算失联（即使已跑了 5 小时）
    assert is_stale_run({
        "status": "running",
        "started_at": (now - _dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
        "lease_expires_at": fmt(120),
    }) is False
    # 终态永不标记失联
    assert is_stale_run({"status": "success", "lease_expires_at": fmt(-3600)}) is False
    # 无租约的历史行按 10 分钟兜底（只兜底，不是主判据）
    assert is_stale_run({"status": "running", "started_at": fmt(-11 * 60)}) is True
    assert is_stale_run({"status": "running", "started_at": fmt(-2 * 60)}) is False
    # queued 从未被认领、没有租约：按入队时间判队列堵塞，阈值与收口同源
    from backend.modules.flow.dag.dag_service import QUEUE_STALL_MINUTES

    assert is_stale_run({
        "status": "queued",
        "created_at": (now - _dt.timedelta(minutes=QUEUE_STALL_MINUTES + 1)).strftime("%Y-%m-%d %H:%M:%S"),
    }) is True
    assert is_stale_run({"status": "queued", "created_at": fmt(-60)}) is False


def test_list_runs_sweeps_stale_leases_before_showing_phantom_running(monkeypatch):
    """清单里出现租约过期的实例时先收口再重取，不留「永远运行中」的幻影。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    pages = [
        [{"id": 1, "status": "running", "is_stale": 1, "stats_json": None, "params_json": None}],
        [{"id": 1, "status": "timeout", "is_stale": 0, "stats_json": None, "params_json": None}],
    ]
    statements = []

    def fake_query(sql, params=None, fetchone=False):
        statements.append(" ".join(sql.split()))
        if fetchone:
            return {"total": 1}
        return pages.pop(0)

    monkeypatch.setattr(dag_svc_mod, "execute_query", fake_query)
    monkeypatch.setattr(dag_svc_mod.dag_service, "aggregate_stats_for_runs",
                        lambda run_ids, total_nodes=None: {})
    swept = []
    monkeypatch.setattr(
        dag_svc_mod.dag_service, "cleanup_stale_runs",
        lambda *a, **k: swept.append(1) or {"dag_runs": 1, "dag_node_runs": 1})

    out = dag_svc_mod.dag_service.list_runs(page=1, size=20)

    assert len(swept) == 1, "发现失联实例必须先按租约收口"
    assert out["items"][0]["status"] == "timeout", "重取后不得再显示运行中"
    assert out["total"] == 1
    # 失联判据在库里算（NOW()），不拿应用机时钟与 DB 时间比
    assert "lease_expires_at <= NOW()" in statements[1], statements[1]


def test_list_runs_skips_sweep_when_no_stale_row(monkeypatch):
    """没有失联实例时不得每次读清单都触发一次全表收口写。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    monkeypatch.setattr(
        dag_svc_mod, "execute_query",
        lambda sql, params=None, fetchone=False: (
            {"total": 1} if fetchone else
            [{"id": 1, "status": "success", "is_stale": 0,
              "stats_json": None, "params_json": None}]))
    monkeypatch.setattr(dag_svc_mod.dag_service, "aggregate_stats_for_runs",
                        lambda run_ids, total_nodes=None: {})
    swept = []
    monkeypatch.setattr(dag_svc_mod.dag_service, "cleanup_stale_runs",
                        lambda *a, **k: swept.append(1) or {})

    out = dag_svc_mod.dag_service.list_runs(page=1, size=20)

    assert swept == []
    assert out["items"][0]["status"] == "success"


# ── 运行记录删除（终态保护 + 子表清理） ─────────────────────────────


def _patch_run_lookup(monkeypatch, run_row):
    """拦截 dag_service.execute_query：get_run 走 fetchone，列表查询返回空。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    monkeypatch.setattr(
        dag_svc_mod, "execute_query",
        lambda sql, params=None, fetchone=False: run_row if fetchone else [],
    )


def test_delete_run_rejects_active_run(monkeypatch):
    """排队/执行中的实例不能删：执行体仍持租约，会继续写节点行（删了又冒出来）。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    _patch_run_lookup(monkeypatch, {"id": 5, "status": "running"})
    calls = _patch_writes(monkeypatch, [])

    with pytest.raises(ValueError, match="请先停止"):
        dag_svc_mod.dag_service.delete_run(5)
    assert calls == []


def test_delete_run_clears_node_runs_before_run(monkeypatch):
    """节点明细无外键，必须按 run_id 显式清掉，否则留下指向已删 run 的孤儿行。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    _patch_run_lookup(monkeypatch, {"id": 5, "status": "success"})
    calls = _patch_writes(monkeypatch, [3, 1])

    assert dag_svc_mod.dag_service.delete_run(5) is True
    assert "DELETE FROM adh_dag_node_runs WHERE run_id = %s" in calls[0][0]
    assert calls[0][1] == (5,)
    assert "DELETE FROM adh_dag_runs WHERE id = %s" in calls[1][0]
    assert calls[1][1] == (5,)


def test_delete_run_missing_returns_false(monkeypatch):
    """实例不存在时返回 False（接口层翻 404），不得默默清节点。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    _patch_run_lookup(monkeypatch, None)
    calls = _patch_writes(monkeypatch, [])

    assert dag_svc_mod.dag_service.delete_run(404) is False
    assert calls == []


def test_delete_run_api_turns_active_run_into_409(monkeypatch):
    """服务层的终态保护要翻成可读 409，前端才能原样提示“先停止再删除”。"""
    from fastapi import HTTPException

    from backend.modules.flow.api import dag as dag_api

    class _Svc:
        def get_run(self, run_id):
            return {"id": run_id, "workflow_id": 7, "status": "queued"}

        def delete_run(self, run_id):
            raise ValueError("运行实例仍在排队/执行中，请先停止再删除")

    monkeypatch.setattr(dag_api, "dag_service", _Svc())

    with pytest.raises(HTTPException) as exc:
        dag_api.delete_run(1)
    assert exc.value.status_code == 409
    assert "停止" in exc.value.detail


# ── 运行记录筛选（全部下推 SQL） ──────────────────────────────────


def _patch_reads(monkeypatch, total=0, rows=None):
    """拦截 dag_service.execute_query，记录 SQL 与参数（COUNT 走 fetchone）。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = []

    def fake_query(sql, params=None, fetchone=False):
        calls.append((" ".join(sql.split()), list(params or [])))
        if fetchone:
            return {"total": total}
        return rows if rows is not None else []

    monkeypatch.setattr(dag_svc_mod, "execute_query", fake_query)
    return calls


def test_list_runs_filters_are_pushed_down_with_single_predicate(monkeypatch):
    """筛选（任务/状态/触发方式/时间窗/关键字）必须全部下推 SQL，且 COUNT 与清单同谓词。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_reads(monkeypatch, total=3)

    out = dag_svc_mod.dag_service.list_runs(
        workflow_id=7, status="failed", trigger_type="cron",
        start_from="2026-11-01", start_to="2026-11-25", q="50%", page=2, size=10)

    assert out["total"] == 3
    count_sql, count_params = calls[0]
    list_sql, list_params = calls[1]
    assert count_sql.startswith("SELECT COUNT(*)")
    # 时间维度按开始时间判定，未启动的用创建时间兜底（含首尾当日）
    for sql in (count_sql, list_sql):
        assert "r.workflow_id = %s" in sql
        assert "r.status = %s" in sql
        assert "r.trigger_type = %s" in sql
        assert "COALESCE(r.started_at, r.created_at) >= %s" in sql
        assert "COALESCE(r.started_at, r.created_at) <= %s" in sql
        assert "w.name LIKE %s" in sql
    # 深分页要有唯一 tiebreaker，否则相邻页会重行/漏行
    assert list_sql.endswith("ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s")
    assert count_params == [7, "failed", "cron", "2026-11-01 00:00:00",
                            "2026-11-25 23:59:59", "%50\\%%"]
    assert list_params == count_params + [10, 10]


def test_list_runs_rejects_bad_date(monkeypatch):
    """日期格式不对要显式拒（静默忽略会被读成“这段时间没有运行”）。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod

    calls = _patch_reads(monkeypatch)

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        dag_svc_mod.dag_service.list_runs(start_from="2026/11/01")
    assert calls == []


# ── 运行记录批量操作（逐条复用单条实现 + 逐条回报） ──────────────


def _batch_request(user):
    """构造带服务端身份的最小 Request（身份只信服务端，不从请求体取）。"""
    from starlette.requests import Request

    request = Request({"type": "http", "method": "POST", "path": "/api/dag/runs",
                       "headers": []})
    request.state.current_user = user
    return request


def _patch_batch_service(monkeypatch, statuses, workflow=None):
    """假 service：按 run_id 给实例状态，delete_run 对活动实例抛 ValueError。"""
    from backend.modules.flow.dag import dag_service as dag_svc_mod
    from backend.modules.flow.api import dag as dag_api

    calls = {"deleted": [], "stopped": []}
    wf = workflow or {"id": 7, "name": "客户360物化", "owner_id": 1, "workspace_id": 0}

    class _Svc:
        def get_run(self, run_id):
            if run_id not in statuses:
                return None
            return {"id": run_id, "workflow_id": 7, "status": statuses[run_id]}

        def get_workflow(self, workflow_id):
            return dict(wf)

        def delete_run(self, run_id):
            if statuses[run_id] in ("queued", "running"):
                raise ValueError("运行实例仍在排队/执行中，请先停止再删除")
            calls["deleted"].append(run_id)
            return True

        def cancel_run(self, run_id):
            if statuses[run_id] not in ("queued", "running"):
                return False
            calls["stopped"].append(run_id)
            return True

    monkeypatch.setattr(dag_api, "dag_service", _Svc())
    monkeypatch.setattr(dag_api, "authorize_resource_scope", lambda user, ws: int(ws or 0))
    assert dag_svc_mod is not None
    return calls


def test_batch_delete_reports_per_item(monkeypatch):
    """一条能删、一条运行中：逐条回报，不整批回滚，拒因原样转述。"""
    from backend.modules.flow.api import dag as dag_api

    calls = _patch_batch_service(monkeypatch, {1: "success", 2: "running"})
    user = {"user_id": 1, "role": "analyst", "username": "u"}

    out = dag_api.batch_delete_runs(dag_api.BatchRunIds(ids=[1, 2]), _batch_request(user))

    assert calls["deleted"] == [1]
    assert out["succeeded_count"] == 1 and out["failed_count"] == 1
    assert out["failed"][0]["id"] == 2
    assert "停止" in out["failed"][0]["reason"]
    assert out["failed"][0]["workflow_name"] == "客户360物化"
    assert "已删除 1 条" in out["message"] and "未处理" in out["message"]


def test_batch_delete_rejects_foreign_run(monkeypatch):
    """非 admin 不得删别人任务的实例（批量入口无路径参数，归属必须在循环里补）。"""
    from backend.modules.flow.api import dag as dag_api

    calls = _patch_batch_service(
        monkeypatch, {1: "success"},
        workflow={"id": 7, "name": "他人任务", "owner_id": 99, "workspace_id": 0})
    user = {"user_id": 1, "role": "analyst", "username": "u"}

    out = dag_api.batch_delete_runs(dag_api.BatchRunIds(ids=[1]), _batch_request(user))

    assert calls["deleted"] == []
    assert out["succeeded_count"] == 0
    assert "不存在" in out["failed"][0]["reason"]
    assert "本次没有改动生效" in out["message"]


def test_batch_stop_marks_finished_as_skipped(monkeypatch):
    """已结束的实例没有可停的东西：如实标本次未处理，不当作成功。"""
    from backend.modules.flow.api import dag as dag_api

    calls = _patch_batch_service(monkeypatch, {1: "success", 2: "running"})
    user = {"user_id": 1, "role": "admin", "username": "u"}

    out = dag_api.batch_stop_runs(dag_api.BatchRunIds(ids=[1, 2]), _batch_request(user))

    assert calls["stopped"] == [2]
    assert out["succeeded_count"] == 1 and out["failed_count"] == 1
    assert "无需停止" in out["failed"][0]["reason"]


def test_batch_runs_reject_empty_and_over_limit(monkeypatch):
    """空勾选与超上限直接 400（不许“点了但什么都没发生”）。"""
    from fastapi import HTTPException

    from backend.modules.flow.api import dag as dag_api

    _patch_batch_service(monkeypatch, {})
    user = {"user_id": 1, "role": "admin", "username": "u"}

    with pytest.raises(HTTPException) as empty:
        dag_api.batch_delete_runs(dag_api.BatchRunIds(ids=[]), _batch_request(user))
    assert empty.value.status_code == 400

    too_many = list(range(1, dag_api.MAX_BATCH_RUNS + 2))
    with pytest.raises(HTTPException) as over:
        dag_api.batch_delete_runs(dag_api.BatchRunIds(ids=too_many), _batch_request(user))
    assert over.value.status_code == 400
    assert str(dag_api.MAX_BATCH_RUNS) in over.value.detail