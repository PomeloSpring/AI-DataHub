"""定时执行离线回归：不连接数据库、消息队列或真实数据源。"""
import asyncio
from unittest.mock import Mock

import pytest

from services.dataflow.tasks import executor


@pytest.fixture
def scheduled_waker(monkeypatch):
    import copy
    from services.shared.common import db, auth
    from services.datamind.execution import resource_guard
    from services.datamind.execution.sdk_tools import external_tools
    state = {"waker": {"id": 1, "waker_key": "sales", "name": "销售分析", "is_default": 1,
             "system_prompt": "你是销售分析", "datasource_ids": [], "knowledge_base_ids": [],
             "mcp_server_ids": [], "tools": {"mcp": {"semantic": ["get_metrics", "run_semantic_query"]}}},
             "sources": [8], "kbs": [4], "enabled": True,
             "server": {"id": 9, "name": "srv", "transport": "sse", "url": "http://test.invalid",
                        "tools_config": [{"name": "propose_semantic_intent"}]}, "queries": []}
    def query(sql, params=(), fetchone=False):
        state["queries"].append((sql, params))
        if "adh_workspace_wakers" in sql:
            return [copy.deepcopy(state["waker"])] if state["enabled"] else []
        if "adh_workspace_datasources" in sql:
            return [{"datasource_id": i} for i in state["sources"]]
        if "adh_mcp_servers" in sql:
            assert "workspace_id" not in sql and params == (9,)
            return copy.deepcopy(state["server"])
        if "adh_knowledge_bases" in sql:
            assert "workspace_ids" not in sql
            return [{"id": i} for i in params if i in state["kbs"]]
        raise AssertionError(sql)
    monkeypatch.setattr(db, "execute_query", query)
    monkeypatch.setattr(resource_guard, "execute_query", query)
    monkeypatch.setattr(external_tools, "execute_query", query)
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda uid, ws: {
        "user_id": uid, "workspace_id": ws, "role": "admin", "username": "创建者"})
    return state


def _grant_mcp(state):
    state["waker"]["mcp_server_ids"] = [9]
    state["waker"]["tools"]["external"] = {"9": ["propose_semantic_intent"]}


@pytest.mark.parametrize("results,required,report_ok,expected", [
    ([], False, False, "failed"),
    ([{"status": "failed"}], False, False, "failed"),
    ([{"status": "success"}, {"status": "failed"}], False, False, "partial"),
    ([{"status": "success"}], True, False, "failed"),
    ([{"status": "success"}], True, True, "success"),
    ([{"status": "success"}], False, False, "success"),
])
def test_truthful_status(results, required, report_ok, expected):
    assert executor.execution_status(results, required, report_ok) == expected


@pytest.mark.parametrize("owner", [0, -1, None])
def test_missing_owner_never_queries(owner, monkeypatch):
    from services.shared.common import auth
    lookup = Mock()
    monkeypatch.setattr(auth, "get_user_by_id", lookup)
    with pytest.raises(PermissionError):
        auth.resolve_execution_owner(owner, 3)
    lookup.assert_not_called()


def test_disabled_owner_rejected(monkeypatch):
    from services.shared.common import auth
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid: {"status": "disabled"})
    with pytest.raises(PermissionError):
        auth.resolve_execution_owner(7, 3)


def test_sql_uses_governance_and_outer_limit(monkeypatch):
    from services.dataviz.services import governed_query
    governed = Mock(return_value={"columns": ["数量"], "rows": [{"数量": 4}], "row_count": 1})
    monkeypatch.setattr(governed_query, "governed_execute", governed)
    out = executor._execute_sql_on_datasource("SELECT 'limit' AS label", 8,
                                             {"user_id": 7, "username": "owner", "workspace_id": 3})
    assert out["rows"] == [{"数量": 4}]
    assert "LIMIT 1000" in governed.call_args.args[0]
    assert governed.call_args.args[1:] == (8, 7, 3, "owner")


def test_write_sql_never_reaches_governance(monkeypatch):
    from services.dataviz.services import governed_query
    governed = Mock()
    monkeypatch.setattr(governed_query, "governed_execute", governed)
    with pytest.raises(PermissionError):
        executor._execute_sql_on_datasource("SELECT 1; DELETE FROM orders", 8,
                                            {"user_id": 7, "workspace_id": 3})
    governed.assert_not_called()


def test_log_projection_drops_legacy_tokens():
    from services.dataflow.api.scheduled import _safe_log
    out = _safe_log({"id": 1, "report_access_token": "secret", "access_token": "old",
                     "result_data": [{"sql": "secret"}], "error_message": "host password"})
    assert out == {"id": 1}


@pytest.fixture
def runtime(monkeypatch):
    from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
    task = {"id": 1, "owner_id": 7, "workspace_id": 3, "is_active": True,
            "task_type": "query", "timeout_seconds": 30, "max_retries": 0,
            "task_config": {"datasource_id": 8, "questions": [{"title": "统计", "sql": "SELECT 1"}]}}
    log = {"id": 2, "scheduled_task_id": 1, "status": "queued", "run_key": "run-1"}
    monkeypatch.setattr(service, "get_task", lambda tid: task)
    monkeypatch.setattr(service, "create_log", lambda *a, **kw: 2)
    monkeypatch.setattr(service, "get_log", lambda lid: dict(log))
    def claim(*a, **kw):
        if log["status"] != "queued":
            return False
        log["status"] = "running"
        return True
    monkeypatch.setattr(service, "claim_log", claim)
    def finish(lid, **kw):
        if log["status"] != "running":
            return False
        log.update(kw)
        return True
    monkeypatch.setattr(service, "finish_log", finish)
    monkeypatch.setattr(service, "update_log", lambda lid, **kw: log.update(kw))
    monkeypatch.setattr(service, "claim_notification", lambda lid: False)
    monkeypatch.setattr(executor, "_resolve_owner", lambda t: {"user_id": 7, "workspace_id": 3})
    monkeypatch.setattr(executor, "_check_datasource", lambda *a: None)
    monkeypatch.setattr(executor, "_collect_lineage", lambda *a: None)
    from services.dataviz.services import report_service
    monkeypatch.setattr(report_service, "_snapshot", lambda *a: {"sources": [], "policy_digest": "test"})
    monkeypatch.setattr(report_service, "_verify_snapshot", lambda *a: None)
    return task, log


@pytest.mark.parametrize("adapter", ["sync", "async", "celery"])
def test_adapters_share_status_and_run_key(adapter, runtime, monkeypatch):
    task, log = runtime
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", Mock(side_effect=RuntimeError("host password SQL")))
    if adapter == "sync":
        out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    elif adapter == "async":
        out = asyncio.run(executor.execute_scheduled_task_async(1, run_key="run-1"))
    else:
        out = executor.execute_scheduled_task.run(1, "manual", "run-1")
    assert out["status"] == log["status"] == "failed"
    assert "host password" not in str(log)
    assert log["questions_failed"] == 1


def test_duplicate_delivery_does_not_execute_again(runtime, monkeypatch):
    task, log = runtime
    query = Mock(return_value={"columns": [], "rows": [], "row_count": 0})
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", query)
    executor.execute_scheduled_task_sync(1, run_key="run-1")
    executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert query.call_count == 1


def test_cancellation_not_overwritten_by_completion(runtime, monkeypatch):
    task, log = runtime
    def query(*a):
        log["status"] = "cancelled"
        return {"columns": [], "rows": [], "row_count": 0}
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", query)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == log["status"] == "cancelled"


def test_required_report_failure_is_task_failure(runtime, monkeypatch):
    task, log = runtime
    task["report_template_key"] = "1"
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", lambda *a: {"columns": [], "rows": []})
    monkeypatch.setattr(executor, "_generate_report", Mock(side_effect=RuntimeError("private error")))
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "failed"
    assert log["stage_error_code"] == "REPORT_FAILED"


def test_sql_retry_reuses_log_and_never_repeats_report(runtime, monkeypatch):
    task, log = runtime
    task["max_retries"] = 1
    query = Mock(side_effect=[RuntimeError("transient"), {"rows": [], "columns": []}])
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", query)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "success" and query.call_count == 2


def test_expired_deadline_never_overwrites_timeout(runtime, monkeypatch):
    task, log = runtime
    clock = [1.0]
    monkeypatch.setattr(executor.time, "monotonic", lambda: clock[0])
    def query(*args):
        clock[0] = 100.0
        return {"rows": [], "columns": []}
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", query)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == log["status"] == "timeout"


def test_notification_contains_no_business_content(monkeypatch):
    from services.dataflow.tasks.notification import notification_sender
    from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
    monkeypatch.setattr(service, "get_channel", lambda cid: {"workspace_id": 3, "is_active": True})
    monkeypatch.setattr(service, "update_channel_test_status", lambda *a: None)
    send = Mock()
    monkeypatch.setattr(notification_sender, "send", send)
    executor._send_notification({"channel_id": 1, "workspace_id": 3, "name": "敏感任务名"},
                                [{"status": "failed", "title": "患者", "rows": [[999]]}],
                                report_content="敏感正文")
    content = send.call_args.args[1]
    assert "failed" in content
    assert not any(term in content for term in ("敏感", "患者", "999"))


def test_beat_generates_run_before_publish(monkeypatch):
    from celery.beat import Scheduler, ScheduleEntry
    from services.dataflow.tasks.beat_schedule import DatabaseScheduler
    from services.dataflow.tasks.celery_app import app
    from services.dataflow.services.scheduled_task_service import scheduled_task_service as service
    calls = []
    monkeypatch.setattr(service, "get_task", lambda tid: {"id": tid, "owner_id": 7, "is_active": 1, "workspace_id": 3})
    monkeypatch.setattr(service, "create_log", lambda *a, **kw: calls.append(("log", kw["run_key"])))
    monkeypatch.setattr(Scheduler, "apply_async", lambda self, entry, *a, **kw: calls.append(("publish", entry.kwargs["run_key"])))
    scheduler = DatabaseScheduler(app=app, lazy=True)
    entry = ScheduleEntry(name="task", task="services.dataflow.tasks.executor.execute_scheduled_task", args=(1,), app=app)
    scheduler.apply_async(entry)
    assert calls[0][0] == "log" and calls[1][0] == "publish"
    assert calls[0][1] == calls[1][1]


@pytest.mark.parametrize("call,code", [(None, "NO_QUERY_RESULT"), ("execute_sql", "TOOL_NOT_ALLOWED"),
                                      ("needs_clarification", "NEEDS_CLARIFICATION")])
def test_agent_never_fakes_completion_or_bypasses_tools(monkeypatch, call, code, scheduled_waker):
    from services.datamind.execution.scheduled_analysis import analyze_question
    from services.shared.common.llm import llm_client
    response = {"tool_uses": [{"id": "1", "name": call, "input": {}}] if call else [], "text": "已完成"}
    monkeypatch.setattr(llm_client, "generate_with_tools", lambda *a: response)
    result = asyncio.run(analyze_question("统计订单", {"datasource_id": 8, "waker_key": "sales"}, {"user_id": 7, "workspace_id": 3}))
    assert result["status"] == "failed" and result["error_code"] == code


def test_agent_injects_trusted_identity(monkeypatch, scheduled_waker):
    from services.datamind.execution.scheduled_analysis import analyze_question
    from services.shared.common.llm import llm_client
    from services.dataviz.services import report_service
    responses = iter([{"tool_uses": [{"id": "1", "name": "run_semantic_query", "input": {"intent": {"object": "订单"}}}]}, {"tool_uses": []}])
    monkeypatch.setattr(llm_client, "generate_with_tools", lambda *a: next(responses))
    execute = Mock(return_value={"status": "success", "rows": [], "columns": []})
    monkeypatch.setattr(report_service, "execute_semantic_source", execute)
    identity = {"user_id": 7, "workspace_id": 3, "username": "创建者", "role": "admin"}
    out = asyncio.run(analyze_question("统计订单", {"datasource_id": 8, "waker_key": "sales"}, identity))
    assert out["status"] == "success"
    assert execute.call_args.args[1:] == (identity, 8)


# ── 时区 Beat：墙钟分钟匹配 + DST 安全运行键 ──────────────

def test_wallclock_run_key_uses_task_timezone():
    from datetime import datetime, timezone
    from services.dataflow.tasks.beat_schedule import WallClockCrontab
    now = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)  # 00:00 EDT / 12:00 CST
    assert WallClockCrontab("0 0 * * *", "America/New_York").run_key(5, now=now) == \
        "cron:5:America/New_York:202609210000"
    assert WallClockCrontab("0 0 * * *", "Asia/Shanghai").run_key(5, now=now) == \
        "cron:5:Asia/Shanghai:202609211200"


def test_wallclock_is_due_matches_local_not_utc(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from services.dataflow.tasks.beat_schedule import WallClockCrontab
    cron = WallClockCrontab("30 9 * * *", "America/New_York")
    local = datetime(2026, 3, 10, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(cron, "now", lambda: local)
    due, _ = cron.is_due(local - timedelta(minutes=5))
    assert due
    # 同一 UTC 瞬间在别处不是 09:30 → 不触发
    monkeypatch.setattr(cron, "now", lambda: local.astimezone(ZoneInfo("UTC")))
    off = datetime(2026, 3, 10, 10, 30, tzinfo=ZoneInfo("America/New_York"))
    due2, _ = cron.is_due(off - timedelta(minutes=5))
    assert not due2


def test_wallclock_no_backfill_after_downtime(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from services.dataflow.tasks.beat_schedule import WallClockCrontab
    cron = WallClockCrontab("0 * * * *", "Asia/Shanghai")
    now = datetime(2026, 9, 21, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(cron, "now", lambda: now)
    # 上次运行是昨天：当前 15:30 不匹配整点 → 不补跑
    due, _ = cron.is_due(now - timedelta(days=1))
    assert not due


# ── Beat 短租约：丢锁/故障暂停派发，不双发 ─────────────────

class _FakeLock:
    def __init__(self, fail=False, lost=False):
        self.fail, self.lost = fail, lost
        self.acquired = self.reacquired = self.released = 0
    def acquire(self, blocking=False):
        if self.fail:
            import redis
            raise redis.exceptions.RedisError("down")
        self.acquired += 1
        return True
    def reacquire(self):
        if self.lost:
            import redis
            raise redis.exceptions.LockNotOwnedError()
        self.reacquired += 1
    def release(self):
        self.released += 1


def _lease(lock):
    from services.dataflow.tasks.celery_app import BeatLease
    class Client:
        def lock(self, *a, **k):
            return lock
    return BeatLease(client=Client())


def test_beat_lease_renew_and_release():
    lock = _FakeLock()
    lease = _lease(lock)
    assert lease.renew() and lease.held
    assert lease.renew() and lock.reacquired == 1
    lease.close()
    assert not lease.held and lock.released == 1


def test_beat_lease_redis_failure_pauses_dispatch():
    lease = _lease(_FakeLock(fail=True))
    assert not lease.renew() and not lease.held


def test_beat_lease_lost_lock_reacquires():
    lock = _FakeLock(lost=True)
    lease = _lease(lock)
    lease.held = True  # 以为持有，实际已丢
    assert lease.renew() and lock.acquired == 1


def test_beat_tick_pauses_without_lease(monkeypatch):
    from celery.beat import Scheduler
    from services.dataflow.tasks.beat_schedule import DatabaseScheduler
    from services.dataflow.tasks.celery_app import app
    monkeypatch.setattr(Scheduler, "__init__", lambda self, *a, **k: None)
    sched = DatabaseScheduler.__new__(DatabaseScheduler)
    sched._lease = _lease(_FakeLock(fail=True))
    monkeypatch.setattr(Scheduler, "tick", lambda self: (_ for _ in ()).throw(AssertionError("不应派发")))
    assert sched.tick() == 1.0


# ── MCP/Agent 工具范围：只提议意图，取数留在治理链 ─────────

def test_agent_datasource_scope_enforced(scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    scheduled_waker["waker"]["datasource_ids"] = [1, 2]
    with pytest.raises(PermissionError, match="Waker"):
        sa.load_profile({"waker_key": "sales", "datasource_id": 8}, {"workspace_id": 3, "user_id": 7})


def test_agent_prompt_scoped_but_allowed(scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    profile = sa.load_profile({"waker_key": "sales", "datasource_id": 8}, {"workspace_id": 3, "user_id": 7})
    assert profile["policy"].waker["system_prompt"] == "你是销售分析"
    assert profile["servers"] == []


def test_mcp_requires_explicit_intent_contract(scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    _grant_mcp(scheduled_waker)
    scheduled_waker["server"]["tools_config"] = [{"name": "other"}]
    with pytest.raises(PermissionError, match="契约工具"):
        sa.load_profile({"waker_key": "sales", "datasource_id": 8}, {"workspace_id": 3, "user_id": 7})


def test_mcp_stdio_transport_explicitly_unavailable(scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    _grant_mcp(scheduled_waker)
    scheduled_waker["server"]["transport"] = "stdio"
    p = sa.load_profile({"waker_key": "sales", "datasource_id": 8}, {"workspace_id": 3, "user_id": 7})
    assert p["servers"] == []
    assert "HTTP 意图提议契约" in p["unavailable"]["mcp__external_9__propose_semantic_intent"]


def test_mcp_intent_tool_accepted_without_workspace_binding(scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    _grant_mcp(scheduled_waker)
    p = sa.load_profile({"waker_key": "sales", "datasource_id": 8}, {"workspace_id": 3, "user_id": 7})
    assert p["servers"][0]["id"] == 9


@pytest.mark.parametrize("legacy", [{}, {"agent_name": "sales"}, {"mcp_server_id": 9},
                                    {"waker_key": "sales", "mcp_server_ids": []}])
def test_old_task_requires_explicit_waker(legacy, scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    with pytest.raises(sa.ScheduledScopeError) as exc:
        sa.load_profile({"datasource_id": 8, **legacy}, {"workspace_id": 3, "user_id": 7})
    assert exc.value.code == "WAKER_REQUIRED"
    assert scheduled_waker["queries"] == []


@pytest.mark.parametrize("revoke", ["waker", "tool", "datasource", "mcp", "knowledge"])
def test_independent_workers_observe_revocation(scheduled_waker, revoke):
    from services.datamind.execution import scheduled_analysis as sa
    _grant_mcp(scheduled_waker)
    scheduled_waker["waker"]["knowledge_base_ids"] = [4]
    scheduled_waker["waker"]["tools"]["mcp"]["semantic"].append("knowledge_search")
    config = {"waker_key": "sales", "datasource_id": 8}
    identity = {"workspace_id": 3, "user_id": 7}
    first, second = sa.load_profile(config, identity), sa.load_profile(config, identity)
    assert first["context"] is not second["context"]
    if revoke == "waker": scheduled_waker["enabled"] = False
    if revoke == "tool": scheduled_waker["waker"]["tools"]["mcp"]["semantic"].remove("run_semantic_query")
    if revoke == "datasource": scheduled_waker["sources"] = []
    if revoke == "mcp": scheduled_waker["server"] = None
    if revoke == "knowledge": scheduled_waker["kbs"] = []
    for _ in (first, second):
        with pytest.raises(PermissionError):
            sa.load_profile(config, identity)


def test_revocation_during_llm_never_reaches_query(monkeypatch, scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    from services.shared.common.llm import llm_client
    from services.dataviz.services import report_service
    def revoke(*args):
        scheduled_waker["enabled"] = False
        return {"tool_uses": [{"id": "1", "name": "run_semantic_query", "input": {"intent": {"object": "订单"}}}]}
    monkeypatch.setattr(llm_client, "generate_with_tools", revoke)
    query = Mock()
    monkeypatch.setattr(report_service, "execute_semantic_source", query)
    out = asyncio.run(sa.analyze_question("统计", {"waker_key": "sales", "datasource_id": 8}, {"user_id": 7, "workspace_id": 3}))
    assert out["status"] == "failed" and not out.get("retryable")
    query.assert_not_called()


def test_legacy_worker_stops_before_llm(runtime, monkeypatch):
    task, log = runtime
    task["task_type"] = "agent"
    query = Mock()
    monkeypatch.setattr(executor, "_execute_agent_mode", query)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "failed" and log["stage_error_code"] == "WAKER_REQUIRED"
    query.assert_not_called()


@pytest.mark.parametrize("payload", [
    '{"intent":{"object":"订单","sql":"SELECT 1"}}',
    '{"rows":[[1]]}',
    '{"intent":{"object":"订单","datasource_id":5}}',
    '{"intent":{"object":"订单"},"extra":1}',
])
def test_propose_intent_rejects_bad_contract(monkeypatch, payload):
    from services.datamind.execution import scheduled_analysis as sa
    import services.shared.mcp_client.client as mcpmod
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def connect(self): return True
        async def call_tool(self, name, args): return payload
        async def disconnect(self): pass
    monkeypatch.setattr(mcpmod, "MCPClient", FakeClient)
    with pytest.raises(ValueError):
        asyncio.run(sa.propose_intent({"id": 9, "name": "srv", "transport": "sse", "url": "http://x"}, "统计"))


def test_propose_intent_accepts_clean_intent(monkeypatch):
    from services.datamind.execution import scheduled_analysis as sa
    import services.shared.mcp_client.client as mcpmod
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def connect(self): return True
        async def call_tool(self, name, args):
            assert args == {"question": "统计订单金额"}
            return '{"intent":{"object":"订单","metrics":["金额"],"limit":10}}'
        async def disconnect(self): pass
    monkeypatch.setattr(mcpmod, "MCPClient", FakeClient)
    intent = asyncio.run(sa.propose_intent({"id": 9, "name": "srv", "transport": "sse", "url": "http://x"}, "统计订单金额"))
    assert intent == {"object": "订单", "metrics": ["金额"], "limit": 10}


def test_mcp_unavailable_is_retryable_not_success(monkeypatch, scheduled_waker):
    from services.datamind.execution import scheduled_analysis as sa
    from services.shared.common.llm import llm_client
    import services.shared.mcp_client.client as mcpmod
    _grant_mcp(scheduled_waker)
    monkeypatch.setattr(llm_client, "generate_with_tools",
                        lambda *a: {"tool_uses": [{"id": "1", "name": "propose_semantic_intent_1", "input": {}}]})
    class DeadClient:
        def __init__(self, *a, **k): pass
        async def connect(self): return False
        async def disconnect(self): pass
    monkeypatch.setattr(mcpmod, "MCPClient", DeadClient)
    out = asyncio.run(sa.analyze_question("统计", {"datasource_id": 8, "waker_key": "sales"}, {"user_id": 7, "workspace_id": 3}))
    assert out["status"] == "failed" and out["error_code"] == "MCP_UNAVAILABLE" and out["retryable"]


# ── Agent 有限重试：瞬时故障重试，需人工处理不重试 ──────────

def _agent_task(**cfg):
    import threading, time as _t
    base = {"datasource_id": 8, "waker_key": "sales", "questions": [{"title": "t", "question": "统计"}]}
    base.update(cfg)
    return {"task_config": base, "_identity": {"user_id": 7, "workspace_id": 3},
            "_deadline": _t.monotonic() + 60, "_log_id": 1, "_stop_event": threading.Event(),
            "max_retries": base.pop("max_retries", 0)}


def test_agent_retries_transient_llm_then_succeeds(monkeypatch, scheduled_waker):
    from services.dataviz.services import report_service
    from services.shared.common.llm import llm_client
    monkeypatch.setattr(executor, "_check_running", lambda task: None)
    state = {"calls": 0}
    def gen(messages, tools):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("LLM 瞬时不可用")
        if state["calls"] == 2:
            return {"tool_uses": [{"id": "1", "name": "run_semantic_query", "input": {"intent": {"object": "订单"}}}]}
        return {"tool_uses": []}
    monkeypatch.setattr(llm_client, "generate_with_tools", gen)
    monkeypatch.setattr(report_service, "execute_semantic_source", lambda *a: {"status": "success", "rows": [], "columns": []})
    out = executor._execute_agent_mode(_agent_task(max_retries=2))
    assert out[0]["status"] == "success" and out[0]["attempts"] == 2


def test_agent_non_retryable_not_repeated(monkeypatch, scheduled_waker):
    from services.dataviz.services import report_service
    from services.shared.common.llm import llm_client
    monkeypatch.setattr(executor, "_check_running", lambda task: None)
    calls = {"n": 0}
    def execute(*a):
        calls["n"] += 1
        raise ValueError("bad intent")
    monkeypatch.setattr(report_service, "execute_semantic_source", execute)
    monkeypatch.setattr(llm_client, "generate_with_tools",
                        lambda *a: {"tool_uses": [{"id": "1", "name": "run_semantic_query", "input": {"intent": {}}}]})
    out = executor._execute_agent_mode(_agent_task(max_retries=3))
    assert out[0]["status"] == "failed" and out[0]["error_code"] == "QUERY_REJECTED"
    assert calls["n"] == 1  # 需人工处理，不盲目重试


# ── 取消：阻塞取数期间取消尽快返回，不发布迟到结果 ─────

def test_guarded_call_stops_on_cancel_event():
    import threading, time as _t
    from services.shared.common.task_runtime import guarded_call, RunInterrupted
    stop, started = threading.Event(), threading.Event()
    def slow():
        started.set()
        _t.sleep(5)
        return "late-result"
    def cancel_soon():
        started.wait(2)
        stop.set()
    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(RunInterrupted):
        guarded_call(slow, check=lambda: None, stop_event=stop)


def test_cancelled_run_discards_late_result(runtime, monkeypatch):
    import threading, time as _t
    task, log = runtime
    released = threading.Event()
    def slow_query(*a):
        released.wait(3)
        return {"columns": [], "rows": [], "row_count": 0}
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", slow_query)
    threading.Thread(target=lambda: (_t.sleep(0.15), log.__setitem__("status", "cancelled")), daemon=True).start()
    threading.Thread(target=lambda: (_t.sleep(0.6), released.set()), daemon=True).start()
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == log["status"] == "cancelled"
    assert log.get("questions_succeeded", 0) == 0
