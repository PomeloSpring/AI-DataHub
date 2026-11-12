"""execute_sql 多站扇出（一次调用内服务端并发）契约。

背景（会话 1783069459151）：模型为了拿 5 站的同口径数字，在一轮里连发 15~20 次
`execute_sql`，而 SDK/MCP 工具分发层对同轮并行调用是**串行**执行的 —— 探针
`backend/scripts/probe_tool_concurrency.py` 实测：并发墙钟 ≈ 单条最大(7.3s)，
串行墙钟 ≈ Σ 单条(15.2s)。所以"让模型多发几条"只会把耗时线性叠进 300s 回合上限。
解法是把扇出下沉到服务端：一次调用带 `sites`/`datasources` 清单，服务端并发跑完
分站返回。锁定契约：
1. 走扇出分支时按目标分桶返回，且并发执行（墙钟 ≈ max，不 ≈ Σ）；
2. 每个目标都经统一治理入口（execute_query_with_permission，带服务端身份与解析后的源）；
3. 单目标失败 = 该桶显式 error（部分成功对用户可见），不静默丢站、不整体假成功；
4. 源名解析失败的目标不参与执行，全部失败才整体报错（fail-loud）；
5. 每个目标各自落查询审计（护栏 §9）；
6. 输出不含内部 id/host（护栏 §7 黑盒口径不因扇出而放松）。
"""
import asyncio
import json
import time

import pandas as pd
import pytest

import backend.core.query_executor as qexec
import backend.modules.mind.execution.sdk_tools.query_tools as qt
from backend.modules.mind.execution.sdk_tools import context as ctxmod

SOURCE_IDS = {"sls-a": 101, "sls-b": 102, "sls-c": 103}
SQL = ("SELECT appCode, COUNT(*) AS n FROM observability_rum "
       "WHERE __time__ >= to_unixtime(now()) - 3600 GROUP BY appCode LIMIT 10")


@pytest.fixture()
def spy(monkeypatch):
    """治理入口打桩：按源返回可区分结果，并记录调用；sleep 用来夹出真并发。"""
    calls = []
    audits = []
    state = {"fail_ds": None, "sleep": 0.25}

    def fake_exec(sql, datasource_id, user_context, workspace_id, database="", federated_names=None):
        import time as _t
        _t.sleep(state["sleep"])
        calls.append({"ds": datasource_id, "user": user_context, "ws": workspace_id, "sql": sql})
        if state["fail_ds"] == datasource_id:
            raise RuntimeError("engine down")
        # 行的值刻意不等于源 id：本文件的黑盒用例断言"返回体里不出现内部 id"，
        # 若拿 id 当业务值会自证失败（历史误报源）。
        return pd.DataFrame([{"appCode": "X", "n": 7}]), 10, 1

    monkeypatch.setattr(qexec, "execute_query_with_permission", fake_exec)
    monkeypatch.setattr(qexec, "log_audit", lambda **kw: audits.append(kw))
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "")

    from backend.modules.mind.execution import resource_guard as guard
    from backend.core import site_service

    def resolve_by_name(name, available):
        ds_id = SOURCE_IDS.get(str(name))
        if ds_id is None or int(ds_id) not in [int(i) for i in (available or [])]:
            raise guard.ResourceScopeError("数据源不在授权范围内（可用数据源: sls-a, sls-b, sls-c）")
        return ds_id

    monkeypatch.setattr(guard, "_resolve_authorized_source", resolve_by_name)
    monkeypatch.setattr(site_service, "resolve_site_to_source",
                        lambda site, available, **kw: resolve_by_name(site, available))
    return {"calls": calls, "audits": audits, "state": state}


def _ctx(user_id=42):
    from backend.modules.mind.execution.models import ExecutionContext
    return ctxmod.set_execution_context(ExecutionContext(
        user_id=user_id, username="alice", user_role="analyst", workspace_id=7, datasource_id=0,
        extra={"available_datasource_ids": sorted(SOURCE_IDS.values()),
               "question": "5 站 app 使用率"}))


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_fanout_runs_concurrently_in_one_call(spy):
    """3 个目标各 0.25s：并发应 ≈0.3s，串行会 ≈0.75s。扇出的意义就在于此。"""
    token = _ctx()
    try:
        t0 = time.monotonic()
        result = asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["sls-a", "sls-b", "sls-c"]}))
        wall = time.monotonic() - t0
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    payload = _payload(result)
    assert "isError" not in result
    assert payload["batch"] is True and payload["ok_count"] == 3
    assert sorted(r["target"] for r in payload["results"]) == ["sls-a", "sls-b", "sls-c"]
    assert wall < 0.7, f"扇出未并发（墙钟 {wall:.2f}s ≈ Σ 单条），退化成串行多发"
    assert {c["ds"] for c in spy["calls"]} == {101, 102, 103}


def test_fanout_each_target_uses_governed_entry_with_server_identity(spy):
    """每个目标都必须经统一取数入口 + 服务端身份，源 id 由服务端解析（LLM 不持有 id）。"""
    token = _ctx()
    try:
        result = asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["sls-a", "sls-b"]}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert "isError" not in result
    assert len(spy["calls"]) == 2
    for c in spy["calls"]:
        assert c["user"] == {"user_id": 42, "username": "alice"}
        assert c["ws"] == 7
        assert c["sql"] == SQL
    rows = {r["target"]: r["row_count"] for r in _payload(result)["results"]}
    assert rows == {"sls-a": 1, "sls-b": 1}


def test_fanout_partial_failure_is_visible_not_dropped(spy):
    """一站执行失败：该桶给显式 error 且计数可见，其余站结果照常返回；绝不拿别站数充数。"""
    spy["state"]["fail_ds"] = 102
    token = _ctx()
    try:
        result = asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["sls-a", "sls-b", "sls-c"]}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    payload = _payload(result)
    assert "isError" not in result, "部分成功应返回可用结果，同时显式暴露失败站"
    assert payload["ok_count"] == 2 and payload["failed_count"] == 1
    by_target = {r["target"]: r for r in payload["results"]}
    assert "error" in by_target["sls-b"]
    assert by_target["sls-a"]["row_count"] == 1
    assert "分别呈现" in payload["guidance"]


def test_fanout_unauthorized_name_not_executed_and_all_failed_is_error(spy):
    """源名解析失败：该目标不执行（不猜源），并把候选名回给模型；全部失败才整体报错。"""
    token = _ctx()
    try:
        partial = asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["sls-a", "ghost"]}))
        allbad = asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["ghost1", "ghost2"]}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    p = _payload(partial)
    assert {c["ds"] for c in spy["calls"]} == {101}, "未授权源名不得被执行"
    assert p["failed_count"] == 1 and p["ok_count"] == 1
    ghost = [r for r in p["results"] if r["target"] == "ghost"][0]
    assert "可用数据源" in ghost["error"], ghost
    assert allbad.get("isError") is True


def test_fanout_lands_one_query_audit_per_target(spy):
    """扇出不减审计：每个目标各落一条 query_type='sql' 的审计（护栏 §9）。"""
    token = _ctx()
    try:
        asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["sls-a", "sls-b"]}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert len(spy["audits"]) == 2, spy["audits"]
    assert {a["datasource_id"] for a in spy["audits"]} == {101, 102}
    assert all(a["query_type"] == "sql" and a["question"] == "5 站 app 使用率" for a in spy["audits"])


def test_fanout_output_keeps_datasource_blackbox(spy):
    """扇出返回体仍是业务面：不得出现 datasource_id/host/账号（护栏 §7）。"""
    token = _ctx()
    try:
        result = asyncio.run(qt.execute_sql({"sql": SQL, "datasources": ["sls-a", "sls-b"]}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    dumped = json.dumps(_payload(result), ensure_ascii=False)
    for leak in ("datasource_id", "host", "password", "101", "102"):
        assert leak not in dumped, dumped[:200]


def test_fanout_carries_sls_time_window_notice_per_target(spy, monkeypatch):
    """提醒按目标各自给：SLS 源没写 __time__ 谓词的桶要带默认窗警告。"""
    monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "sls")
    token = _ctx()
    try:
        result = asyncio.run(qt.execute_sql(
            {"sql": "SELECT appCode FROM observability_rum LIMIT 10", "datasources": ["sls-a"]}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    entry = _payload(result)["results"][0]
    assert any("默认时间窗" in w for w in entry.get("warnings") or []), entry
