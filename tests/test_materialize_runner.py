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
                      "timeout_sec": kw.get("timeout_sec"),
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
        lambda sql, ds_id, ident, timeout_sec=None: (["id"], [{"id": i} for i in range(11)]))
    calls = _writer_spy(monkeypatch)

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == "BUDGET_EXCEEDED"
    assert "不会静默截断" in str(err.value)
    assert calls == [], "超预算必须一行都不写"


def test_fetch_timeout_follows_remaining_budget(monkeypatch):
    """单期读超时必须随总预算下发（不继承交互式 30s 天花板）。

    年档/全量窗口的 UNION 多分支扫描必然超过 30s：若沿用交互默认，物化永远
    只能报“数据源暂不可用，稍后重试”。预算即拒的口径要求把剩余秒数作为读超时。
    """
    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql",
                        lambda ds, args, max_rows: ("SELECT 1", [], max_rows))
    seen: list = []

    def _fetch(sql, ds_id, ident, timeout_sec=None):
        seen.append(timeout_sec)
        return ["id"], [{"id": 1}]

    monkeypatch.setattr(mr, "_fetch", _fetch)
    _writer_spy(monkeypatch)

    mr.run_materialize_node(_config(budget={"max_rows": 10, "max_seconds": 600}), _context())

    assert seen and seen[0] is not None
    assert 590 <= int(seen[0]) <= 600, "读超时应为剩余总预算（秒）"


def test_write_timeout_follows_remaining_budget(monkeypatch):
    """写侧读超时必须同样随总预算下发，不继承交互式 30s（与读侧同一条线）。

    Doris staging + OVERWRITE 大分区在 30s 内完不成，会被客户端判成 2013「连接
    丢失」，而服务端 DDL 已落地；随后整轮写入都撞 `already exists`（day 档实测）。
    """
    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql",
                        lambda ds, args, max_rows: ("SELECT 1", [], max_rows))
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["id"], [{"id": 1}]))
    calls = _writer_spy(monkeypatch)

    mr.run_materialize_node(_config(budget={"max_rows": 10, "max_seconds": 600}),
                            _context())

    assert calls, "应发生一次写入"
    got = calls[0]["timeout_sec"]
    assert got is not None, "写侧不得回落交互式 30s 默认"
    assert 590 <= int(got) <= 600, "写超时应为剩余总预算（秒）"


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
                        lambda sql, ds_id, ident, timeout_sec=None: (["id"], [{"id": 1}, {"id": 2}]))
    calls = _writer_spy(monkeypatch, exc=RuntimeError(message))

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == expected
    assert calls and calls[0]["target"] == "doris_main.dwh.ods_order_daily"


def test_write_gate_busy_keeps_target_busy_code(monkeypatch):
    """写闸门排队超预算 → TARGET_BUSY（不是 TARGET_WRITE_FAILED）。

    两者对外含义不同：TARGET_BUSY = 并发档位已满、目标本身没问题（提示错开调度/
    调档），写成 TARGET_WRITE_FAILED 会把人引向“目标引擎故障”排查方向。
    """
    from backend.modules.flow.dag.write_gate import WriteBusy

    _patch_common(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql",
                        lambda ds, args, max_rows: ("SELECT 1", [], max_rows))
    monkeypatch.setattr(
        mr, "_fetch",
        lambda sql, ds_id, ident, timeout_sec=None: (["id"], [{"id": 1}]))
    _writer_spy(monkeypatch, exc=WriteBusy(
        "目标数据源 'doris_main' 的物化写入已达并发上限 2 档（排队超时）"))

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == "TARGET_BUSY"
    assert "并发上限" in str(err.value), "闸门原文（档位/处置建议）必须透传，不被通用文案盖掉"


def test_partition_overwrite_expands_days_and_reports_reconciliation(monkeypatch):
    """回看 N 天 = N 个分区；日期参数逐日渲染，汇总带对账与写入策略。"""
    _patch_common(monkeypatch)
    seen_args: list = []

    def _compile(ds, args, max_rows):
        seen_args.append(dict(args))
        return "SELECT 1", [], max_rows

    monkeypatch.setattr(mr, "_compile_sql", _compile)
    monkeypatch.setattr(mr, "_fetch",
                        lambda sql, ds_id, ident, timeout_sec=None: (["id"], [{"id": 1}, {"id": 2}]))
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


def test_run_dates_month_and_year_grains():
    """月/年档按「月初/年初」展开：三档各自分区键，互不覆盖。"""
    months = mr._run_dates(3, "month")
    assert [d.day for d in months] == [1, 1, 1]
    years = mr._run_dates(2, "year")
    assert all(d.month == 1 and d.day == 1 for d in years)
    assert years[1].year - years[0].year == 1
    assert mr._run_dates(1, "month")[-1] == mr._today().replace(day=1)
    assert mr._run_dates(1, "year")[-1] == date(mr._today().year, 1, 1)


@pytest.mark.parametrize("grain, lookback", [("day", 367), ("month", 121), ("year", 31)])
def test_grain_lookback_limit_is_per_grain(grain, lookback):
    """回看上限按粒度分别夹（天/月/年），越界不静默夹取。"""
    with pytest.raises(ValueError):
        mr._run_dates(lookback, grain)


def test_partition_overwrite_month_grain_writes_month_partitions(monkeypatch):
    """月档回看 2 格 = 2 个月初分区；日期参数渲染为月初 iso。"""
    _patch_common(monkeypatch)
    seen_args: list = []

    def _compile(ds, args, max_rows):
        seen_args.append(dict(args))
        return "SELECT 1", [], max_rows

    monkeypatch.setattr(mr, "_compile_sql", _compile)
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["id"], [{"id": 1}]))
    calls = _writer_spy(monkeypatch)

    result = mr.run_materialize_node(
        _config(write_mode="partition_overwrite",
                partition={"column": "dt", "date_arg": "dt",
                           "target_partition": "p{yyyyMMdd}", "grain": "month",
                           "lookback_days": 2}),
        _context())

    expected = [d.isoformat() for d in mr._run_dates(2, "month")]
    assert [p["date"] for p in result["partitions"]] == expected
    assert [a["dt"] for a in seen_args] == expected
    assert all(d.endswith("-01") for d in expected)
    assert all(c["partition"]["target_partition"].endswith("01") for c in calls)


def test_invalid_grain_rejected_with_config_invalid(monkeypatch):
    """非法粒度必须 fail-loud（不得回落到按天跑）。"""
    _patch_common(monkeypatch)
    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(
            _config(write_mode="partition_overwrite",
                    partition={"column": "dt", "date_arg": "dt",
                               "grain": "week", "lookback_days": 1}),
            _context())
    assert err.value.error_code == "CONFIG_INVALID"


# ── 敏感列硬门（data-sync-engineering §5：block 列编译期拒绝，不是剔除后继续）──
def _stub_source_type(monkeypatch, db_type: str = "mysql") -> None:
    monkeypatch.setattr(
        "backend.common.db.datasource_db.get_datasource_by_id",
        lambda ds_id: {"id": ds_id, "db_type": db_type})


def test_blocked_column_in_materialize_sql_is_rejected_before_fetch(monkeypatch):
    """物化口径引用 block 列 → 取数前就拒跑（剔除后继续会静默改分组/口径）。"""
    _patch_common(monkeypatch)
    _stub_source_type(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql", lambda ds, args, max_rows: (
        "SELECT id_no, COUNT(*) AS c FROM t_patient GROUP BY id_no",
        ["t_patient"], max_rows))
    monkeypatch.setattr(mr, "_blocked_columns", lambda *a, **k: {"id_no"})
    fetched: list = []
    monkeypatch.setattr(
        mr, "_fetch", lambda *a, **k: fetched.append(1) or (["c"], []))
    calls = _writer_spy(monkeypatch)

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == "SENSITIVE_COLUMN_BLOCKED"
    assert "id_no" in str(err.value)
    assert fetched == [] and calls == [], "拒跑必须发生在取数与写入之前"


def test_wildcard_projection_with_blocked_source_is_rejected(monkeypatch):
    """`SELECT *` + 源表有 block 列 → 无法证明没带进来，拒跑（不猜列）。"""
    _patch_common(monkeypatch)
    _stub_source_type(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql", lambda ds, args, max_rows: (
        "SELECT * FROM t_patient", ["t_patient"], max_rows))
    monkeypatch.setattr(mr, "_blocked_columns", lambda *a, **k: {"id_no"})
    calls = _writer_spy(monkeypatch)

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(), _context())

    assert err.value.error_code == "SENSITIVE_COLUMN_BLOCKED"
    assert calls == []


def test_count_star_is_not_treated_as_wildcard_projection(monkeypatch):
    """`COUNT(*)` 的星在函数内，不是拉全表列：未被引用的 block 列不得误拒。"""
    _patch_common(monkeypatch)
    _stub_source_type(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql", lambda ds, args, max_rows: (
        "SELECT COUNT(*) AS c FROM t_patient", ["t_patient"], max_rows))
    monkeypatch.setattr(mr, "_blocked_columns", lambda *a, **k: {"id_no"})
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["c"], [{"c": 1}]))
    calls = _writer_spy(monkeypatch)

    result = mr.run_materialize_node(_config(), _context())

    assert result["rows_written"] == 1 and len(calls) == 1


def test_star_over_dataset_body_is_not_a_physical_wildcard(monkeypatch):
    """编译器的 `SELECT * FROM (正文)` 包装不得误判：列由内层决定（内层单独检查）。"""
    _patch_common(monkeypatch)
    _stub_source_type(monkeypatch)
    monkeypatch.setattr(mr, "_compile_sql", lambda ds, args, max_rows: (
        "SELECT * FROM (SELECT id, COUNT(*) AS c FROM t_patient GROUP BY id) AS _ds_base",
        ["t_patient"], max_rows))
    monkeypatch.setattr(mr, "_blocked_columns", lambda *a, **k: {"id_no"})
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["id", "c"], [{"id": 1, "c": 1}]))
    calls = _writer_spy(monkeypatch)

    result = mr.run_materialize_node(_config(), _context())

    assert result["rows_written"] == 1 and len(calls) == 1


# ── 时间窗口 / 历史初始化 / 水位线（运行参数） ───────────────────────


def _partition_config(**over) -> dict:
    cfg = _config(write_mode="partition_overwrite")
    cfg["partition"] = {"column": "dt", "date_arg": "dt",
                        "target_partition": "p{yyyyMMdd}", "lookback_days": 2}
    cfg["partition"].update(over)
    return cfg


def _stub_window_pipeline(monkeypatch) -> tuple:
    """打桩编译/取数/写入，返回 (逐期渲染参数列表, 写入调用列表)。"""
    _patch_common(monkeypatch)
    seen_args: list = []

    def _compile(ds, args, max_rows):
        seen_args.append(dict(args))
        return "SELECT 1", [], max_rows

    monkeypatch.setattr(mr, "_compile_sql", _compile)
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["id"], [{"id": 1}]))
    calls = _writer_spy(monkeypatch)
    return seen_args, calls


def test_window_dates_explicit_range_is_history_window():
    """显式区间 = 历史初始化：从 start 逐日到 end，与“从今天往回数”无关。"""
    cells = mr.window_dates(
        "day", start=date(2026, 1, 1), end=date(2026, 1, 5))
    assert [d.isoformat() for d in cells] == [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]


def test_window_dates_aligns_month_and_year_grains():
    """月/年档的区间按格起点对齐（月→月初、年→年初），不得混入月中日期。"""
    months = mr.window_dates("month", start=date(2024, 3, 17), end=date(2024, 5, 2))
    assert [d.isoformat() for d in months] == ["2024-03-01", "2024-04-01", "2024-05-01"]
    years = mr.window_dates("year", start=date(2022, 7, 9), end=date(2024, 2, 1))
    assert [d.isoformat() for d in years] == ["2022-01-01", "2023-01-01", "2024-01-01"]


def test_window_dates_resume_starts_after_last_success():
    """续跑：从「上次成功位置」的下一格开始（成功格不重刷）。"""
    cells = mr.window_dates("day", end=date(2026, 1, 5), floor=date(2026, 1, 3))
    assert [d.isoformat() for d in cells] == ["2026-01-04", "2026-01-05"]
    # 已到终点 = 没有待刷新的格（显式空列表，而不是跑一个无日期的整表覆盖）
    assert mr.window_dates("day", end=date(2026, 1, 5),
                           floor=date(2026, 1, 5)) == []


def test_window_dates_resume_without_watermark_is_rejected():
    """只给终点且没有成功位置：必须拒跑，不能静默猜一个历史起点。"""
    with pytest.raises(ValueError, match="没有可续跑的成功位置"):
        mr.window_dates("day", end=date(2026, 1, 5))


def test_window_cells_limit_rejects_instead_of_truncating():
    """超量窗口直接报错要求分段（静默夹取 = 只跑了尾巴却报“历史已初始化”）。"""
    with pytest.raises(ValueError, match="分段执行"):
        mr.window_dates("day", start=date(2000, 1, 1), end=mr._today())


def test_run_materialize_uses_run_params_window(monkeypatch):
    """运行参数（start/end）进入物化节点：实际刷的分区就是该区间，不看 lookback。"""
    seen_args, calls = _stub_window_pipeline(monkeypatch)
    ctx = {**_context(), "watermark_key": "dag:1:cn_day",
           "run_params": {"start_date": "2026-01-01", "end_date": "2026-01-03"}}

    result = mr.run_materialize_node(_partition_config(), ctx)

    expected = ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert [p["date"] for p in result["partitions"]] == expected
    assert [a["dt"] for a in seen_args] == expected
    assert result["window"]["mode"] == "history"
    assert result["window"]["cells"] == 3
    assert result["last_committed_date"] == "2026-01-03"


def test_run_materialize_advances_watermark_to_last_committed(monkeypatch):
    """成功后把「最后成功位置」写成窗口最后一格（物化节点也要有水位线）。"""
    _stub_window_pipeline(monkeypatch)
    from backend.modules.flow.dag.dag_service import dag_service as real_dag_service

    advanced: list = []
    monkeypatch.setattr(real_dag_service, "advance_watermark",
                        lambda key, value, rows=0: advanced.append((key, value, rows)) or True)
    ctx = {**_context(), "watermark_key": "dag:1:cn_day",
           "run_params": {"start_date": "2026-02-01", "end_date": "2026-02-02"}}

    mr.run_materialize_node(_partition_config(), ctx)

    assert advanced == [("dag:1:cn_day", "2026-02-02", 2)]


def test_run_materialize_resume_past_end_writes_nothing(monkeypatch):
    """续跑已到终点：零写入并显式回报 skipped，不得退回整表覆盖。"""
    seen_args, calls = _stub_window_pipeline(monkeypatch)
    from backend.modules.flow.dag.dag_service import dag_service as real_dag_service

    monkeypatch.setattr(real_dag_service, "get_watermark", lambda key: "2026-01-03")
    ctx = {**_context(), "watermark_key": "dag:1:cn_day",
           "run_params": {"end_date": "2026-01-03"}}

    result = mr.run_materialize_node(_partition_config(), ctx)

    assert calls == [] and seen_args == []
    assert result["partitions"] == [] and result["rows_written"] == 0
    assert result["window"]["skipped"] is True
    assert "没有待刷新的格" in result["window"]["reason"]


def test_run_materialize_watermark_failure_is_loud(monkeypatch):
    """水位线写失败必须显式报错（数据已落库也要告知），不伪装成“全绿”。"""
    _stub_window_pipeline(monkeypatch)
    from backend.modules.flow.dag.dag_service import dag_service as real_dag_service

    def _boom(key, value, rows=0):
        raise RuntimeError("meta db down")

    monkeypatch.setattr(real_dag_service, "advance_watermark", _boom)
    ctx = {**_context(), "watermark_key": "dag:1:cn_day",
           "run_params": {"start_date": "2026-03-01", "end_date": "2026-03-01"}}

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_partition_config(), ctx)

    assert err.value.error_code == "WATERMARK_FAILED"
    assert "数据已落库" in str(err.value)


def test_run_materialize_skips_grains_not_selected(monkeypatch):
    """本次运行未选择的粒度：零写入 + 显式 skipped 原因（历史初始化只跑月/年档）。"""
    seen_args, calls = _stub_window_pipeline(monkeypatch)
    ctx = {**_context(), "watermark_key": "dag:1:cn_day",
           "run_params": {"start_date": "2021-01-01", "grains": ["month", "year"]}}

    result = mr.run_materialize_node(_partition_config(grain="day"), ctx)

    assert calls == [] and seen_args == []
    assert result["partitions"] == [] and result["rows_written"] == 0
    assert result["window"]["skipped"] is True
    assert "未包含 day" in result["window"]["reason"]


# ── 整表覆盖 + 多站合并（方案 B：9 张无分区表 / 3 节点每域）──────────────
def _window_config(**over) -> dict:
    cfg = _config(write_mode="overwrite")
    cfg["window"] = {"grain": "day", "lookback_days": 3}
    cfg.update(over)
    return cfg


def _stub_window_overwrite(monkeypatch, declared=None) -> tuple:
    """整表覆盖打桩：返回 (逐站编译参数, 写入调用)。"""
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        mr, "_declared_params",
        lambda ds: set(declared if declared is not None
                       else {"window_start", "window_end", "region"}))
    seen: list = []

    def _compile(ds, args, max_rows):
        seen.append(dict(args))
        return "SELECT 1", [], max_rows

    monkeypatch.setattr(mr, "_compile_sql", _compile)
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: (["id"], [{"id": 1}]))
    return seen, _writer_spy(monkeypatch)


def test_overwrite_window_requires_declared_params(monkeypatch):
    """数据集未声明 window_start/window_end：整表覆盖无处传窗口，必须拒跑。"""
    seen, calls = _stub_window_overwrite(
        monkeypatch, declared={"dt", "region"})

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_window_config(), _context())

    assert err.value.error_code == "DATASET_PARAM_MISSING"
    assert "window_start" in str(err.value)
    assert seen == [] and calls == []


def test_overwrite_without_window_config_rejects_run_params(monkeypatch):
    """整表覆盖但节点没声明 window：时间窗口无处生效，不得静默跑全表。"""
    seen, calls = _stub_window_overwrite(monkeypatch)
    ctx = {**_context(), "run_params": {"start_date": "2026-01-01",
                                       "end_date": "2026-01-02"}}

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_config(write_mode="overwrite"), ctx)

    assert err.value.error_code == "CONFIG_INVALID"
    assert seen == [] and calls == []


def test_multi_site_requires_site_args(monkeypatch):
    """多站合并缺 site_args：各站会写成同一站（静默错数），必须拒跑。"""
    seen, calls = _stub_window_overwrite(monkeypatch)

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(_window_config(sites=["cn", "eu"]), _context())

    assert err.value.error_code == "CONFIG_INCOMPLETE"
    assert "site_args" in str(err.value)
    assert seen == [] and calls == []


def test_multi_site_rejects_missing_site_entry(monkeypatch):
    """site_args 少给一个站：该站会沿用别站参数，必须指名拒跑。"""
    _stub_window_overwrite(monkeypatch)
    cfg = _window_config(sites=["cn", "eu"],
                         site_args={"cn": {"region": "cn"}})

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(cfg, _context())

    assert err.value.error_code == "CONFIG_INCOMPLETE"
    assert "eu" in str(err.value)


def test_multi_site_rejects_partition_overwrite(monkeypatch):
    """多站 + 分区覆盖：后跑的站会取掉前站的分区，必须拒。"""
    _stub_window_overwrite(monkeypatch)
    cfg = _partition_config()
    cfg["sites"] = ["cn", "eu"]
    cfg["site_args"] = {"cn": {"region": "cn"}, "eu": {"region": "eu"}}

    with pytest.raises(mr.MaterializeError) as err:
        mr.run_materialize_node(cfg, _context())

    assert err.value.error_code == "CONFIG_INVALID"
    assert "整表覆盖" in str(err.value)


def test_multi_site_injects_per_site_args_and_writes_once(monkeypatch):
    """每站拿到自己的 region，合并后只写一次（整表覆盖）。"""
    seen, calls = _stub_window_overwrite(monkeypatch)
    cfg = _window_config(
        sites=["cn", "eu"],
        site_args={"cn": {"region": "cn"},
                   "eu": {"region": "eu"}},
        budget={"max_rows": 100, "max_seconds": 30})

    result = mr.run_materialize_node(cfg, _context())

    # 站点参数随站变化（否则 5 站数据会全标成一站）
    assert [a["region"] for a in seen] == ["cn", "eu"]
    # 站点标识只用 region：不再有 region_id 这类内部编码列
    assert all("region_id" not in a for a in seen)
    # 窗口参数对每站一致：同一份口径，只是站不同
    assert len({(a["window_start"], a["window_end"]) for a in seen}) == 1
    # 合并后只提交一次，且是整表覆盖、不带分区参数
    assert len(calls) == 1
    assert calls[0]["write_mode"] == "overwrite"
    assert calls[0]["partition"] is None
    assert calls[0]["rows"] == 2


# ── 导出 SQL（与执行期同一条编译链）───────────────────────
def _stub_export(monkeypatch, declared=None) -> list:
    """导出只编译、不取数不写库；返回逐站编译参数。"""
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        mr, "_declared_params",
        lambda ds: set(declared if declared is not None
                       else {"window_start", "window_end", "region"}))
    seen: list = []

    def _compile(ds, args, max_rows):
        seen.append(dict(args))
        # 真实编译器返回值已把参数渲染成字面量，这里同样落字面量
        return (f"SELECT * FROM (\n  SELECT '{args.get('region', '')}' AS region,\n"
                f"         COUNT(*) AS metric_value\n  FROM t_case\n"
                f"  WHERE create_time >= '{args.get('window_start', '')}'\n"
                f"    AND create_time < '{args.get('window_end', '')}'\n"
                f"  GROUP BY 1\n) AS _ds_base"), ["t_case"], max_rows

    monkeypatch.setattr(mr, "_compile_sql", _compile)
    monkeypatch.setattr(mr, "_fetch", lambda *a, **k: pytest.fail("导出不得取数"))
    monkeypatch.setattr(mr, "_write", lambda *a, **k: pytest.fail("导出不得写库"))
    return seen


def test_export_sql_emits_insert_per_site_with_literal_window(monkeypatch):
    """导出的不是占位注释：每站一条 INSERT + 同源 SELECT，窗口/站点都是字面量。"""
    seen = _stub_export(monkeypatch)
    cfg = _window_config(
        sites=["cn", "eu"],
        site_args={"cn": {"region": "cn"}, "eu": {"region": "eu"}},
        target_table="dws_case_metric_d")

    sql = "\n".join(mr.export_materialize_sql(
        cfg, {"user_id": 7},
        {"start_date": "2026-01-01", "end_date": "2026-01-03"}))

    assert sql.count("INSERT INTO `dws_case_metric_d`") == 2   # 5 站 = 5 段
    assert [a["region"] for a in seen] == ["cn", "eu"]       # 逐站展开
    assert "'2026-01-01'" in sql and "'2026-01-04'" in sql    # 窗口已落字面量
    assert "region_id" not in sql                              # 站点标识只用 region
    assert ":window_start" not in sql                          # 不得留模板占位符


def test_export_sql_fails_loud_when_site_args_missing(monkeypatch):
    """site_args 缺站：导出必须拒，而不是导出一份漏站的“完整脚本”。"""
    seen = _stub_export(monkeypatch)
    cfg = _window_config(sites=["cn", "eu"], site_args={"cn": {"region": "cn"}})

    with pytest.raises(mr.MaterializeError) as err:
        mr.export_materialize_sql(cfg, {"user_id": 7})

    assert err.value.error_code == "CONFIG_INCOMPLETE"
    assert seen == []


def test_export_sql_rejects_dataset_without_window_params(monkeypatch):
    """数据集未声明 window 参数：导出不得静默丢掉窗口（那样导出的 SQL 跑出来是全表）。"""
    _stub_export(monkeypatch, declared={"region"})

    with pytest.raises(mr.MaterializeError) as err:
        mr.export_materialize_sql(_window_config(), {"user_id": 7})

    assert err.value.error_code == "DATASET_PARAM_MISSING"