"""SLS 默认时间窗提醒（no-silent-degradation §1：静默截断必须显式暴露）。

背景（会话 1783069459151 实测 + backend/scripts/probe_sls_default_time_window.py 取证）：
SLS(PG 协议) 端点在 SQL 未写 `__time__` 比较谓词时只扫描引擎默认窗（实测≈最近 15 分钟），
而返回体的 `row_count` / `truncated:false` 完全看不出被截断 —— 同一条
`SELECT COUNT(*) FROM observability_rum` 无谓词 37 行、加 `__time__` 一年 2200 万+ 行。
模型若把默认窗当全量答出去就是错数。锁定：
1. SLS 源 + 有 FROM + 无 `__time__` 谓词 → execute_sql 返回体带 warnings 提醒；
2. 已写 `__time__` 比较谓词 → 不提醒（不打扰正常查询）；
3. 只在投影里出现 `MIN("__time__")` → 仍提醒（投影不构成谓词）；
4. 非 SLS 源 / 无 FROM 的常量探测 → 不提醒（避免噪音）；
5. check_sql 同一口径：提醒进 warnings 且 verdict 降为 warn；
6. 只做提醒，绝不自动注入时间窗（隐式改口径同样违规），也不外泄内部标识。
"""
import json

import pandas as pd
import pytest

import backend.core.query_executor as qexec
import backend.modules.mind.execution.sdk_tools.query_tools as qt
from backend.modules.mind.execution.sdk_tools import context as ctxmod

NO_WINDOW_SQL = "SELECT appCode, COUNT(*) AS n FROM observability_rum GROUP BY appCode LIMIT 100"
WITH_WINDOW_SQL = (
    "SELECT appCode, COUNT(*) AS n FROM observability_rum "
    "WHERE __time__ >= to_unixtime(now()) - 31536000 GROUP BY appCode LIMIT 100")
QUOTED_WINDOW_SQL = (
    'SELECT COUNT(*) AS n FROM observability_rum '
    'WHERE "__time__" > to_unixtime(current_timestamp) - 31536000 LIMIT 10')
PROJECTION_ONLY_SQL = 'SELECT MIN("__time__") AS t_min, MAX("__time__") AS t_max FROM observability_rum LIMIT 10'
CONST_SQL = "SELECT 1 AS one"

KEY_PHRASE = "默认时间窗"


def _set_ctx(user_id: int = 42):
    from backend.modules.mind.execution.models import ExecutionContext
    return ctxmod.set_execution_context(ExecutionContext(
        user_id=user_id, username="alice", workspace_id=7, datasource_id=8,
        extra={"question": "近一年 app 使用情况"}))


def _payload(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture()
def fake_df():
    return pd.DataFrame([{"appCode": "AS Connect", "n": 37}])


def _patch_engine(monkeypatch, fake_df):
    monkeypatch.setattr(qexec, "execute_query_with_permission",
                        lambda sql, datasource_id, user_context, workspace_id, **k: (fake_df, 12, 1))
    monkeypatch.setattr(qexec, "log_audit", lambda **kw: None)
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "sls")


def test_sls_without_time_predicate_warns(monkeypatch, fake_df):
    """SLS 源没写 __time__ 谓词：结果必须显式提醒默认窗，不能静默返回 37 行充数。"""
    _patch_engine(monkeypatch, fake_df)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(qt.execute_sql({"sql": NO_WINDOW_SQL}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    payload = _payload(result)
    assert "isError" not in result
    assert payload["row_count"] == 1
    warnings = payload.get("warnings") or []
    assert any(KEY_PHRASE in w and "15 分钟" in w for w in warnings), warnings
    assert any("__time__" in w for w in warnings), "提醒必须给出可操作的修法"


def test_sls_with_time_predicate_is_silent(monkeypatch, fake_df):
    """已显式给时间窗（含带引号/别名写法）：不打扰，warnings 不出。"""
    _patch_engine(monkeypatch, fake_df)
    for sql in (WITH_WINDOW_SQL, QUOTED_WINDOW_SQL):
        token = _set_ctx()
        try:
            result = __import__("asyncio").run(qt.execute_sql({"sql": sql}))
        finally:
            ctxmod.ExecutionContextVar.reset(token)
        payload = _payload(result)
        assert not payload.get("warnings"), f"不该提醒: {sql[:40]}"


def test_time_column_in_projection_only_still_warns(monkeypatch, fake_df):
    """`MIN("__time__")` 是投影不是谓词 —— 扫描窗仍是默认窗，必须提醒。"""
    _patch_engine(monkeypatch, fake_df)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(qt.execute_sql({"sql": PROJECTION_ONLY_SQL}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)
    assert any(KEY_PHRASE in w for w in (_payload(result).get("warnings") or []))


def test_non_sls_source_and_const_select_not_noticed(monkeypatch, fake_df):
    """MySQL/Doris 源与无 FROM 的常量探测不该出现 SLS 窗提醒（避免噪音）。"""
    token = _set_ctx()
    try:
        monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "mysql")
        result = __import__("asyncio").run(qt.execute_sql({"sql": NO_WINDOW_SQL}))
        assert not _payload(result).get("warnings")

        monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "sls")
        result = __import__("asyncio").run(qt.execute_sql({"sql": CONST_SQL}))
        assert not _payload(result).get("warnings")
    finally:
        ctxmod.ExecutionContextVar.reset(token)


def test_notice_never_leaks_internal_identifiers(monkeypatch, fake_df):
    """提醒文案是下发给 LLM 的：不得带 datasource_id/host/账号（护栏 §7）。"""
    _patch_engine(monkeypatch, fake_df)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(qt.execute_sql({"sql": NO_WINDOW_SQL}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)
    dumped = json.dumps(_payload(result), ensure_ascii=False)
    for leak in ("datasource_id", "host", "password", "1791617929234"):
        assert leak not in dumped, dumped[:200]


def test_check_sql_downgrades_verdict(monkeypatch):
    """预检同口径：缺 __time__ 谓词的 SLS SQL verdict=warn 且给出提醒。"""
    from backend.core.enforcer import PermissionResult, permission_enforcer

    monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "sls")
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    monkeypatch.setattr(qt, "_resolve_datasource_arg", lambda args, ctx, table_hint="": 8)
    monkeypatch.setattr(permission_enforcer, "_extract_tables", lambda sql: ["observability_rum"])
    monkeypatch.setattr(permission_enforcer, "_policy_table", lambda t, ds: t)
    monkeypatch.setattr(permission_enforcer, "check_access", lambda *a, **k: PermissionResult())
    monkeypatch.setattr(qt, "_governed_columns_in_scope", lambda sql, ds, tables: set())

    token = _set_ctx()
    try:
        warned = __import__("asyncio").run(qt.check_sql({"sql": NO_WINDOW_SQL}))
        clean = __import__("asyncio").run(qt.check_sql({"sql": WITH_WINDOW_SQL}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    w = _payload(warned)
    assert w["verdict"] == "warn" and any(KEY_PHRASE in x for x in w["warnings"])
    assert _payload(clean)["verdict"] == "ok"


# ── db_type 取数与缓存（提醒的判定源）─────────────────────────────

class _FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value


class _FakeCursor:
    def __init__(self, sink, row):
        self.sink, self.row = sink, row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def fetchone(self):
        return self.row


class _FakeConn:
    def __init__(self, sink, row):
        self.sink, self.row = sink, row

    def cursor(self):
        return _FakeCursor(self.sink, self.row)

    def close(self):
        pass


def test_datasource_db_type_reads_once_and_caches(monkeypatch):
    """db_type 只回源一次并进缓存：提醒判定不得给每次取数加一趟元库往返。"""
    import backend.common.ttl_cache as ttl
    from backend.common.db import metadata_db

    cache = _FakeCache()
    sink = []
    monkeypatch.setattr(ttl, "datasource_cache", cache)
    monkeypatch.setattr(metadata_db, "get_metadata_conn",
                        lambda: _FakeConn(sink, {"db_type": "SLS"}))

    assert qt._datasource_db_type(123) == "sls"
    assert qt._datasource_db_type(123) == "sls"
    assert len(sink) == 1, "第二次应命中缓存"
    assert cache.store.get("dbtype:123") == "sls"


def test_datasource_db_type_lookup_failure_degrades_to_no_notice(monkeypatch):
    """元库不可用时不提醒也不炸主链路（判定源缺失 = 少一条提示，绝不改变取数结果）。"""
    from backend.common.db import metadata_db

    def boom():
        raise RuntimeError("metadata db down")

    monkeypatch.setattr(metadata_db, "get_metadata_conn", boom)
    assert qt._datasource_db_type(999) == ""
    assert qt._sls_time_window_notice(NO_WINDOW_SQL, 999) == ""
