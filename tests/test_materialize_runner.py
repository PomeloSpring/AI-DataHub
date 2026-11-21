"""物化节点执行器回归（data-sync-engineering §4 / §5）。

只跑纯逻辑层：数据集解析、目标授权、取数、原子写入全部打桩，
锁的是三条口径 ——
1. **预算即拒**：读到预算+1 行必须拒绝且一行都不写（静默截断 = 错数）；
2. **失败不降级**：写入异常按可诊断错误码抛出（对账不一致 / 目标写入失败）；
3. **分区按日展开、逐日对账**：回看 N 天 = N 个分区，日期参数与分区日期一一对应，
   汇总里的 reconciled/strategy 来自写入回报（平台侧对账结果，不是"跑通了"）。
"""
from datetime import date

import pytest

from backend.modules.flow.dag import materialize_runner as mr


def _config(**over) -> dict:
    cfg = {
        "dataset": "订单宽表",
        "target_datasource": "doris_main",
        "target_table": "dwh.ods_order_daily",
        "write_mode": "overwrite",
        "budget": {"max_rows": 10, "max_seconds": 30},
    }
    cfg.update(over)
    return cfg


def _context() -> dict:
    return {
        "owner_identity": {"user_id": 7, "username": "tester"},
        "workspace_id": 1,
        "node_key": "materialize_1",
        "run_id": 1001,
        "stop_check": lambda: None,
    }


def _patch_common(monkeypatch) -> None:
    """打桩「数据集 / 授权 / 路由 / 旁路（血缘·质量·产品登记）」。"""
    monkeypatch.setattr(
        mr, "_resolve_dataset",
        lambda name, identity: {"id": 1, "name": name, "sql_query": "SELECT 1",
                                "status": "active"})
    monkeypatch.setattr(mr, "_assert_target_authorized", lambda *a, **k: {"id": 1})
    monkeypatch.setattr(mr, "_declared_params", lambda ds: {"dt"})
    monkeypatch.setattr(
        "backend.modules.viz.services.dataset_service.resolve_effective_source",
        lambda ds, identity, site="": 5)
    monkeypatch.setattr(
        "backend.modules.viz.services.dataset_service.effective_source_name",
        lambda source_id: "ds_pg")
    monkeypatch.setattr("backend.modules.flow.dag.node_runners._record_lineage",
                        lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.flow.dag.node_runners._run_quality_checks",
                        lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.flow.dag.node_runners._register_product_after_write",
                        lambda *a, **k: {"registered": False})


def _writer_spy(monkeypatch, *, exc: Exception | None = None) -> list:
    """替换原子写入：记录调用；exc 给定则模拟引擎失败。"""
    calls: list = []

    def _writer(df, ds, table, **kw):
        calls.append({"rows": len(df), "target": f"{ds}.{table}",
                      "write_mode": kw.get("write_mode"),
                      "partition": kw.get("partition")})
        if exc is not None:
            raise exc
        return {"rows_written": len(df), "reconciled": True,
                "strategy": "rename_swap", "target_count": len(df)}

    monkeypatch.setattr(
        "backend.modules.flow.dag.target_writer.write_dataframe_atomic", _writer)
    return calls


def test_budget_exceeded_rejects_without_any_write(monkeypatch):
    """读到预算+1 行 = 还有更多：拒绝且不写半态（绝不截断后报成功）。"""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql",
                        lambda ds, args, max_rows: ("SELECT 1", [], max_rows))
    monkeypatch.setattr(
        mr, "_fetch",
        lambda sql, ds_id, ident: (["id"], [{"id": i} for i in range(11)]))
    calls = _writer_spy(monkeypatch)

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == "BUDGET_EXCEEDED"
    assert "不会静默截断" in str(err.value)
    assert calls == [], "超预算必须一行都不写"


def test_explicit_zero_budget_is_rejected_not_defaulted(monkeypatch):
    """显式配 0 的预算/批量必须报错，不得被 `or 默认值` 静默吃成默认口径。"""
    _patch_common(monkeypatch)
    fetched: list = []
    monkeypatch.setattr(mr, "_fetch",
                        lambda *a, **k: fetched.append(1) or (["id"], []))

    for budget, batch in (({"max_rows": 0, "max_seconds": 30}, None),
                          ({"max_rows": 10, "max_seconds": 0}, None),
                          ({"max_rows": 10, "max_seconds": 30}, 0),
                          ({"max_rows": 10, "max_seconds": 30}, 99999)):
        cfg = _config(budget=budget, **({} if batch is None else {"batch_size": batch}))
        with pytest.raises(mr.MaterializeError) as err:
            mr.run_materialize_node(cfg, _context())
        assert err.value.error_code == "BUDGET_INVALID", (budget, batch)
    assert fetched == [], "预算不合法时不得发起取数"


def test_lookback_out_of_range_is_rejected(monkeypatch):
    """回看天数越界不静默夹取（0 天写成 1 天会让“没回看”看起来像“回了”）"""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["id"], []))
    for days in (0, 400):
        with pytest.raises(mr.MaterializeError) as err:
            mr.run_materialize_node(
                _config(write_mode="partition_overwrite",
                        partition={"column": "dt", "date_arg": "dt",
                                   "lookback_days": days}),
                _context())
        assert err.value.error_code == "CONFIG_INVALID", days


def test_partition_date_arg_must_be_declared_by_dataset(monkeypatch):
    """分区日期参数必须由数据集声明（拼错就报错，不静默按全量跑）。"""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_declared_params", lambda ds: {"business_day"})
    fetched: list = []
    monkeypatch.setattr(mr, "_fetch",
                        lambda *a, **k: fetched.append(1) or (["id"], [{"id": 1}]))

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(
            _config(write_mode="partition_overwrite",
                    partition={"column": "dt", "date_arg": "dt", "lookback_days": 1}),
            _context())

    assert err.value.error_code == "DATASET_PARAM_MISSING"
    assert fetched == [], "参数不合法时不该发起取数"


@pytest.mark.parametrize("message, expected", [
    ("目标侧对账不一致: 源 10 行 / 目标 8 行", "RECONCILE_FAILED"),
    ("连接已断开", "TARGET_WRITE_FAILED"),
])
def test_write_failure_maps_to_diagnosable_code(monkeypatch, message, expected):
    """写入失败必须归类为可诊断错误码，不得被当成 CONFIG_INVALID 或「成功」。"""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql",
                        lambda ds, args, max_rows: ("SELECT 1", [], max_rows))
    monkeypatch.setattr(mr, "_fetch",
                        lambda sql, ds_id, ident: (["id"], [{"id": 1}, {"id": 2}]))
    calls = _writer_spy(monkeypatch, exc=RuntimeError(message))

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == expected
    assert calls and calls[0]["target"] == "doris_main.dwh.ods_order_daily"


def test_partition_overwrite_expands_days_and_reports_reconciliation(monkeypatch):
    """回看 N 天 = N 个分区；日期参数逐日渲染，汇总带对账与写入策略。"""
    _patch_common(monkeypatch)
    seen_args: list = []

    def _compile(ds, args, max_rows):
        seen_args.append(dict(args))
        return "SELECT 1", [], max_rows

    monkeypatch.setattr(mr, "_compile_sql", _compile)
    monkeypatch.setattr(mr, "_fetch",
                        lambda sql, ds_id, ident: (["id"], [{"id": 1}, {"id": 2}]))
    calls = _writer_spy(monkeypatch)

    result = mr.run_materialize_node(
        _config(write_mode="partition_overwrite",
                partition={"column": "dt", "date_arg": "dt",
                           "target_partition": "p{yyyyMMdd}", "lookback_days": 2}),
        _context())

    expected = [d.isoformat() for d in mr._run_dates(2)]
    assert [p["date"] for p in result["partitions"]] == expected
    assert [a["dt"] for a in seen_args] == expected
    assert [c["partition"]["value"] for c in calls] == expected
    assert calls[0]["partition"]["target_partition"].startswith("p")
    assert result["rows_read"] == result["rows_written"] == 2 * len(expected)
    assert result["reconciled"] is True
    assert result["strategy"] == "rename_swap"
    assert result["write_mode"] == "partition_overwrite"
    assert result["target"] == "doris_main.dwh.ods_order_daily"
    assert result["budget"] == {"max_rows": 10, "max_seconds": 30}
