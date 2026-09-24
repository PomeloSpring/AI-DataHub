"""execute_sql 治理硬门禁: 生成的 SQL 必须先过权限审核, 严禁直连数据源执行。

锁定不变量(护栏 §1/§6):
1. execute_sql 一律经 execute_query_with_permission(带身份)执行, 不裸连;
2. 权限审核拒绝(PermissionError)时在到达数据源前即中止, 返回错误不外泄;
3. 源码级守卫: query_tools 的 execute_sql 不得直接引用 execute_query/get_connection/cur.execute,
   防止后续被改成绕过治理的裸执行通道。
"""
import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import services.datamind.nl2sql.sql.query_executor as qexec
import services.datamind.execution.sdk_tools.query_tools as qt
from services.datamind.execution.sdk_tools import context as ctxmod
from services.datamind.execution.sdk_tools.query_tools import execute_sql

QUERY_TOOLS = Path(__file__).resolve().parents[1] / "services/datamind/execution/sdk_tools/query_tools.py"


def _set_ctx():
    return ctxmod.set_execution_context(
        SimpleNamespace(user_id=42, username="alice", workspace_id=7, datasource_id=8))


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_execute_sql_routes_through_governed_executor(monkeypatch):
    """正常查询也必须走 execute_query_with_permission, 且透传身份/工作空间。"""
    captured = {}

    def fake_exec(sql, datasource_id, query_type, user_context, workspace_id):
        captured.update(sql=sql, ds=datasource_id, qt=query_type,
                        user=user_context, ws=workspace_id)
        return pd.DataFrame([{"a": 1}]), 5, 1

    monkeypatch.setattr(qexec, "execute_query_with_permission", fake_exec)
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(execute_sql({"sql": "SELECT a FROM t LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert "isError" not in result
    # 关键: 走的是受治理入口, 且携带服务端身份(user_id/workspace), 非裸连
    assert captured["user"] == {"user_id": 42, "username": "alice"}
    assert captured["ws"] == 7
    assert captured["qt"] == "sql"
    assert _payload(result)["row_count"] == 1


def test_permission_denial_blocks_before_datasource(monkeypatch):
    """权限审核拒绝时, 不得把 SQL 下发数据源; 返回错误且不落数据行。"""
    calls = {"exec": 0}

    def deny(sql, *a, **k):
        calls["exec"] += 1
        raise PermissionError("无权访问表 orders")

    # execute_query_with_permission 内部若真去连库会调用 execute_query; 这里两者都监控
    monkeypatch.setattr(qexec, "execute_query_with_permission", deny)
    monkeypatch.setattr(qexec, "execute_query", lambda *a, **k: calls.__setitem__("raw", calls.get("raw", 0) + 1))
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(execute_sql({"sql": "SELECT * FROM orders LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert result.get("isError") is True
    assert "无权访问" in _payload(result)["error"]
    assert "raw" not in calls  # 从未走裸执行


@pytest.mark.parametrize("sql", [
    "DROP TABLE users",
    "DELETE FROM orders",
    "UPDATE t SET a=1",
    "SELECT 1; DROP TABLE x",
])
def test_non_readonly_rejected_before_execution(monkeypatch, sql):
    """非只读/多语句在安全校验阶段即拒, 不进 execute_query_with_permission。"""
    called = {"n": 0}
    monkeypatch.setattr(qexec, "execute_query_with_permission",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(execute_sql({"sql": sql}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)
    assert result.get("isError") is True
    assert called["n"] == 0


def test_handler_has_no_bypass_reference():
    """源码守卫: execute_sql 只能引用受治理入口, 不得出现直连数据源的符号。"""
    src = QUERY_TOOLS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "execute_sql")
    body = ast.unparse(fn)
    assert "execute_query_with_permission" in body, "execute_sql 必须经统一治理入口"
    # 禁止绕过治理的直连符号
    assert "get_connection" not in body, "execute_sql 不得直连数据源"
    assert not re.search(r"\bexecute_query\b(?!_with_permission)", body), "execute_sql 不得调用未治理的 execute_query"
