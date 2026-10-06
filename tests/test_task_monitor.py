"""任务监控离线回归：不连接数据库、消息队列或真实数据源。

覆盖：内置任务暂停生效与不可移除、队列实例停止的终态保护/幂等、
执行实例视图的状态归一与失败脱敏、进度落库守卫、admin 门控。
"""
import asyncio

import pytest

from services.dataflow.services import task_monitor_service as monitor
from services.shared.common import system_jobs
from services.dataflow.tasks import executor


# ── 系统内置任务 ─────────────────────────────────────────────────────


def test_system_job_pause_gates_reconcile(monkeypatch):
    """暂停内置任务后 reconcile_runs 直接跳过，不执行任何清理。"""
    called = {"cleanup": 0}
    monkeypatch.setattr(system_jobs, "is_job_active", lambda key: False)
    monkeypatch.setattr(system_jobs, "record_run", lambda *a, **k: called.__setitem__("cleanup", 1))
    result = executor.reconcile_runs()
    assert result == {"skipped": "paused"}
    assert called["cleanup"] == 0


def test_reconcile_records_run_state(monkeypatch):
    """未暂停时执行清理并把运行结果落 adh_system_jobs（可观测）。"""
    recorded = []
    monkeypatch.setattr(system_jobs, "is_job_active", lambda key: True)
    monkeypatch.setattr(system_jobs, "record_run", lambda *a, **k: recorded.append((a, k)))

    from services.dataflow.services import scheduled_task_service as sched
    from services.dataviz.services import report_service
    monkeypatch.setattr(sched.scheduled_task_service, "cleanup_stale_running_logs", lambda timeout_minutes=10: 2)
    monkeypatch.setattr(report_service, "cleanup_stale_reports", lambda: 1)

    result = executor.reconcile_runs()
    assert result == {"tasks": 2, "reports": 1}
    assert recorded and recorded[0][0][0] == "runs_reconcile"
    assert recorded[0][0][1] == "success"
    assert "2" in recorded[0][0][2]  # 结果摘要含清理数量


def test_reconcile_failure_recorded_and_reraised(monkeypatch):
    """对账失败必须记录 failed 并显式抛出，不得掩成成功。"""
    recorded = []
    monkeypatch.setattr(system_jobs, "is_job_active", lambda key: True)
    monkeypatch.setattr(system_jobs, "record_run", lambda *a, **k: recorded.append(a))
    from services.dataflow.services import scheduled_task_service as sched
    monkeypatch.setattr(sched.scheduled_task_service, "cleanup_stale_running_logs",
                        lambda timeout_minutes=10: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        executor.reconcile_runs()
    assert recorded and recorded[0][1] == "failed"
    assert "RuntimeError" in recorded[0][2]


def test_pause_rejects_unknown_job_key(monkeypatch):
    """未注册的内置任务键显式报错，不静默忽略。"""
    monkeypatch.setattr(system_jobs, "ensure_seeded", lambda: None)
    with pytest.raises(ValueError):
        system_jobs.set_paused("not_a_job", True, "admin")


def test_pause_writes_shared_state_not_process_memory(monkeypatch):
    """暂停开关写共享元库（distributed-first），SQL 里带 paused_at/paused_by。"""
    monkeypatch.setattr(system_jobs, "ensure_seeded", lambda: None)
    written = []
    monkeypatch.setattr(system_jobs, "execute_write", lambda sql, params=None: written.append((sql, params)) or 1)
    monkeypatch.setattr(system_jobs, "list_jobs", lambda: [{"job_key": "runs_reconcile", "is_active": 0}])
    row = system_jobs.set_paused("runs_reconcile", True, "ops")
    assert row["job_key"] == "runs_reconcile"
    sql = written[0][0]
    assert "adh_system_jobs" in sql and "paused_at" in sql and "paused_by" in sql


# ── 队列任务：停止/终态保护 ───────────────────────────────────────────


def test_stop_run_rejects_finished_instance(monkeypatch):
    """已结束的实例拒绝二次停止（终态保护），重复投递不产生重复副作用。"""
    from services.dataflow.services import scheduled_task_service as sched
    monkeypatch.setattr(sched.scheduled_task_service, "finish_log", lambda log_id, **kw: False)
    with pytest.raises(ValueError, match="已结束"):
        monitor.task_monitor_service.stop_run("scheduled", 1)


def test_stop_run_report_conditional_update(monkeypatch):
    """报表实例取消走条件更新（仅 queued/running 生效）。"""
    sqls = []
    monkeypatch.setattr(monitor, "execute_write", lambda sql, params=None: sqls.append((sql, params)) or 1)
    result = monitor.task_monitor_service.stop_run("report", 7)
    assert result["status"] == "cancelled"
    assert "generation_status IN ('queued','running')" in sqls[0][0]


def test_stop_run_unknown_kind_rejected():
    with pytest.raises(ValueError):
        monitor.task_monitor_service.stop_run("unknown", 1)


def test_stop_task_disables_and_cancels_runs(monkeypatch):
    """停止任务=停用定义+取消全部运行中实例。"""
    monkeypatch.setattr(monitor, "execute_query",
                        lambda sql, params=None: [{"id": 11}] if "SELECT id" in sql else [{"id": 11, "owner_id": 5}])
    writes = []
    monkeypatch.setattr(monitor, "execute_write", lambda sql, params=None: writes.append(sql) or 1)
    cancelled = []
    monkeypatch.setattr(monitor.task_monitor_service, "_cancel_scheduled_run",
                        lambda log_id: cancelled.append(log_id) or True)
    result = monitor.task_monitor_service.stop_task(11)
    assert result["disabled"] is True
    assert cancelled == [11]
    assert any("is_active=0" in sql for sql in writes)


def test_remove_task_cascades_logs(monkeypatch):
    """移除用户任务级联删除执行记录。"""
    monkeypatch.setattr(monitor, "execute_query", lambda sql, params=None: [{"id": 11}])
    writes = []
    monkeypatch.setattr(monitor, "execute_write", lambda sql, params=None: writes.append(sql) or 1)
    monkeypatch.setattr(monitor.task_monitor_service, "_cancel_task_runs", lambda task_id: 0)
    monitor.task_monitor_service.remove_task(11)
    assert any("adh_scheduled_logs" in sql for sql in writes)
    assert any("adh_scheduled_tasks" in sql for sql in writes)


# ── 队列任务：视图归一与脱敏 ──────────────────────────────────────────


def test_runs_list_normalizes_and_strips_raw_error(monkeypatch):
    """报表 ready/degraded 归一为 success/partial；原始 error_message 不出接口。"""
    union_row = {
        "kind": "report", "run_id": 1, "task_id": None, "task_name": "季度报告",
        "workspace_id": 0, "raw_status": "degraded", "normalized_status": "partial",
        "trigger_type": "report", "started_at": "2026-10-01 10:00:00", "finished_at": None,
        "elapsed_ms": None, "worker_id": None, "result_summary": "ok",
        "stage_error_code": "FACT_ONLY", "error_message": "host=10.0.0.5 password=secret",
        "done_count": None, "fail_count": None, "total_count": None,
        "rows_read": None, "rows_written": None, "is_stuck": 0,
    }

    def fake_query(sql, params=None):
        if sql.strip().startswith("SELECT COUNT"):
            return [{"total": 1}]
        return [dict(union_row)]

    monkeypatch.setattr(monitor, "execute_query", fake_query)
    result = monitor.task_monitor_service.list_runs()
    item = result["items"][0]
    assert item["normalized_status"] == "partial"
    assert "error_message" not in item
    assert "password" not in item["error_hint"] and "10.0.0.5" not in item["error_hint"]
    assert item["task_name"] == "季度报告"


def test_error_hint_redacts_connection_details():
    """失败提示必须过滤连接信息/报错栈，并抹掉内部数值 id（护栏 §7 + UI 规范）。"""
    hint = monitor._error_hint("QUERY_FAILED", "Traceback (most recent call last)\nhost=db.internal port=3306")
    assert "host" not in hint and "Traceback" not in hint and "3306" not in hint
    assert hint  # 仍给出可诊断提示
    assert "查询未完成" in monitor._error_hint("QUERY_FAILED", "")
    scrubbed = monitor._error_hint("", "upload to kb=4 failed(返回空)")
    assert "kb=4" not in scrubbed and "kb=?" in scrubbed


def test_runs_union_covers_three_queues():
    """队列视图必须覆盖定时执行/报表生成/同步执行三类实例。"""
    union = monitor.TaskMonitorService._runs_union()
    for table in ("adh_scheduled_logs", "adh_reports", "adh_sync_logs"):
        assert table in union
    assert union.count("UNION ALL") == 2


def test_list_runs_rejects_unknown_kind():
    with pytest.raises(ValueError):
        monitor.task_monitor_service.list_runs(kind="nope")


def test_list_runs_filters_by_task_id_for_detail_view(monkeypatch):
    """任务详情弹窗按 task_id 拉执行历史，过滤条件必须落到 SQL 参数。"""
    captured = {}

    def fake_query(sql, params=None):
        captured.setdefault("calls", []).append((sql, params))
        return [{"total": 0}] if sql.strip().startswith("SELECT COUNT") else []

    monkeypatch.setattr(monitor, "execute_query", fake_query)
    monitor.task_monitor_service.list_runs(task_id=123)
    for sql, params in captured["calls"]:
        assert "task_id = %s" in sql
        assert 123 in (params or ())


def test_kb_sync_query_uses_real_schema_columns(monkeypatch):
    """水位线表列名对齐运行时 schema（用 synced_at，防止 1054 列名不符）。"""
    captured = {}
    monkeypatch.setattr(monitor, "execute_query",
                        lambda sql, params=None: captured.update(sql=sql) or [])
    monitor.task_monitor_service.kb_sync_state()
    assert "synced_at" in captured["sql"]
    assert ".updated_at" not in captured["sql"]


# ── 进度落库守卫 ─────────────────────────────────────────────────────


def test_progress_update_only_touches_active_runs(monkeypatch):
    """逐题进度只写未结束实例，终态由 finish_log 保护。"""
    from services.shared.common import db
    from services.dataflow.services import scheduled_task_service as sched
    captured = {}
    monkeypatch.setattr(db, "execute_write", lambda sql, params=None: captured.update(sql=sql, params=params) or 1)
    assert sched.scheduled_task_service.update_progress(5, 2, 1) is True
    assert "status IN ('queued','running')" in captured["sql"]
    assert captured["params"] == (2, 1, 5)


def test_persist_progress_never_breaks_execution(monkeypatch):
    """进度落库是旁路观测：失败不得影响执行主链路。"""
    from services.dataflow.services import scheduled_task_service as sched
    monkeypatch.setattr(sched.scheduled_task_service, "update_progress",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    executor._persist_progress({"_log_id": 1, "_results": [{"status": "success"}]})  # 不抛即通过


# ── API 门控与异常映射 ───────────────────────────────────────────────


def test_monitor_api_rejects_non_admin(monkeypatch):
    """任务监控仅管理员可用，身份只信服务端解析。"""
    from services.dataflow.api import task_monitor as api

    class FakeState:
        current_user = None

    class FakeRequest:
        state = FakeState()
        headers = {}

    with pytest.raises(Exception) as exc:
        asyncio.run(api._monitor_access(FakeRequest()))
    assert getattr(exc.value, "status_code", None) == 401

    FakeRequest.state.current_user = {"user_id": 2, "role": "analyst", "username": "bob"}
    with pytest.raises(Exception) as exc:
        asyncio.run(api._monitor_access(FakeRequest()))
    assert getattr(exc.value, "status_code", None) == 403

    FakeRequest.state.current_user = {"user_id": 1, "role": "admin", "username": "root"}
    assert asyncio.run(api._monitor_access(FakeRequest()))["role"] == "admin"


def test_api_maps_missing_and_terminal_errors():
    """不存在→404，已结束/不可移除→409。"""
    from fastapi import HTTPException
    from services.dataflow.api import task_monitor as api

    def missing():
        raise ValueError("定时任务不存在")

    def terminal():
        raise ValueError("任务已结束，不能停止")

    for operation, expected in ((missing, 404), (terminal, 409)):
        with pytest.raises(HTTPException) as exc:
            api._call(operation)
        assert exc.value.status_code == expected
