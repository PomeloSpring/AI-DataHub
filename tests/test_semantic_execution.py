"""Phase 6.2 语义执行引擎层 —— 版本锁 / 会话分桶 / 护栏 §5 执行前强制 / 远程下推。

- datafusion 锁版本（adapter 单点收口，不符即 fail-loud）；
- SessionContext 按 policy 指纹分桶池化（LRU 有界、视图登记跨桶重建复放）；
- 护栏 §5：force_limit/huge/raw_source → bounded_query 自动补/收紧 LIMIT 后再校验；
- 远程下推：MySQL 协议源库执行拉回 Arrow，重复列名无损消歧（护栏 §8）。
"""

import datafusion
import pyarrow as pa
import pytest

from backend.semantics.execution import adapter, get_engine, get_session_pool
from backend.semantics.execution.connectors import get_connector
from backend.semantics.execution.connectors.base import rows_to_arrow, to_pushdown_sql
from backend.semantics.execution.connectors.mysql import MySQLConnector
from backend.semantics.execution.engine import SemanticEngine
from backend.semantics.execution.session import SessionPool, policy_fingerprint
from backend.semantics.models import Guardrail

# ── 版本锁（薄适配单点收口） ─────────────────────────────────────


def test_adapter_version_pin():
    assert adapter.check_version() == adapter.PINNED_DATAFUSION == "54.1.0"
    ctx = adapter.new_session()
    assert ctx is not None


def test_adapter_version_mismatch_fails_loud(monkeypatch):
    monkeypatch.setattr(datafusion, "__version__", "99.0.0")
    with pytest.raises(adapter.DataFusionVersionError):
        adapter.check_version()


# ── policy 指纹分桶会话池 ────────────────────────────────────────


def test_policy_fingerprint_stable_and_discriminative():
    assert policy_fingerprint({"a": 1, "b": 2}) == policy_fingerprint({"b": 2, "a": 1})
    assert policy_fingerprint({"a": 1}) != policy_fingerprint({"a": 2})
    assert policy_fingerprint({"a": 1}, extra="u42") != policy_fingerprint({"a": 1}, extra="u43")
    assert policy_fingerprint(None) == policy_fingerprint({})


def test_session_pool_buckets_by_fingerprint():
    pool = SessionPool(max_buckets=4)
    a1 = pool.get("fp-a")
    a2 = pool.get("fp-a")
    b = pool.get("fp-b")
    assert a1 is a2 and b is not a1
    assert pool.size() == 2


def test_session_pool_lru_eviction_keeps_view_registry():
    pool = SessionPool(max_buckets=2)
    pool.register_view("fp-a", "v_t", "SELECT 1 AS t")
    pool.get("fp-b")
    pool.get("fp-c")  # 触发逐出最久未用 fp-a
    assert pool.size() == 2
    # 逐出后重建：视图登记仍在并复放到新会话
    ctx = pool.get("fp-a")
    assert pool.view_names("fp-a") == ["v_t"]
    assert ctx.sql("SELECT count(*) FROM v_t").to_pydict()["count(*)"] == [1]
    # 显式 drop 才清登记
    pool.drop("fp-a")
    assert pool.view_names("fp-a") == []


def test_get_engine_singleton():
    assert get_engine() is get_engine()
    assert isinstance(get_engine(), SemanticEngine)
    assert get_session_pool() is get_session_pool()


# ── 护栏 §5 执行前强制 ──────────────────────────────────────────


def test_bounded_sql_force_limit():
    eng = SemanticEngine(pool=SessionPool())
    sql, need = eng.bounded_sql("SELECT a FROM t", {"force_limit": True})
    assert need and sql.upper().endswith("LIMIT 1000")

    sql, need = eng.bounded_sql("SELECT a FROM t LIMIT 5", {"force_limit": True})
    assert need and sql.upper().endswith("LIMIT 5")  # 更严的既有 LIMIT 保留

    sql, need = eng.bounded_sql("SELECT a FROM t LIMIT 5000", {"force_limit": True}, max_rows=200)
    assert need and sql.upper().endswith("LIMIT 200")  # 收紧到上限

    sql, need = eng.bounded_sql("SELECT a FROM t", None)
    assert not need and sql == "SELECT a FROM t"  # 无触发条件不限流

    _, need = eng.bounded_sql("SELECT a FROM t", {"size_class": "huge"})
    assert need
    _, need = eng.bounded_sql("SELECT a FROM t", {"query_mode": "raw_source"})
    assert need


def test_validate_rejects_dangerous_sql():
    eng = SemanticEngine(pool=SessionPool())
    with pytest.raises(PermissionError):
        eng.validate("DROP TABLE t")
    with pytest.raises(PermissionError):
        eng.validate("SELECT 1; SELECT 2")
    eng.validate("SELECT 1 LIMIT 1", require_limit=True)  # 合法


def test_execute_pushdown_enforces_guardrail_before_execution(monkeypatch):
    captured = {}

    class _FakeConnector:
        db_type = "mysql"

        def execute_pushdown(self, sql, *, timeout_sec=None, max_rows=None):
            captured.update(sql=sql, timeout_sec=timeout_sec, max_rows=max_rows)
            return pa.table({"a": [1]})

    monkeypatch.setattr("backend.semantics.execution.engine.get_connector",
                        lambda db_type, datasource_id=0, **kw: _FakeConnector())
    eng = SemanticEngine(pool=SessionPool())
    table = eng.execute_pushdown(
        "SELECT a FROM t", datasource_id=7, db_type="mysql",
        guardrail={"force_limit": True, "timeout_sec": 9, "max_rows": 50},
    )
    assert captured["sql"].upper().endswith("LIMIT 50")  # 限流在下推前已强制
    assert captured["timeout_sec"] == 9 and captured["max_rows"] == 50
    assert table.num_rows == 1


# ── 远程下推连接器 ──────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns]
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.executed = sql

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        return self._rows[:n]


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.closed = False

    def cursor(self):
        return self._cur

    def close(self):
        self.closed = True


def test_mysql_connector_pushdown_pulls_arrow():
    cur = _FakeCursor(["id", "name"], [(1, "a"), (2, "b"), (3, "c")])
    conn = _FakeConn(cur)
    c = MySQLConnector(datasource_id=7, conn_factory=lambda: conn)
    table = c.execute_pushdown("SELECT id, name FROM t", max_rows=2)
    assert table.column_names == ["id", "name"]
    assert table.num_rows == 2 and conn.closed  # max_rows 截断 + 连接关闭


def test_rows_to_arrow_dedups_repeated_columns():
    table = rows_to_arrow(["a", "a"], [(1, 2)])
    assert table.column_names == ["a", "a__1"]  # 护栏 §8 无损消歧口径


def test_to_pushdown_sql_dialect_routing():
    assert to_pushdown_sql("SELECT 1", "mysql") == "SELECT 1"
    assert to_pushdown_sql("SELECT 1", "doris") == "SELECT 1"
    # 内嵌 DataFusion 执行转 DataFusion 方言（DATE_SUB → interval）
    out = to_pushdown_sql("SELECT DATE_SUB(NOW(), INTERVAL 1 DAY)", "datafusion")
    assert "INTERVAL" in out and "DATE_SUB" not in out


def test_connector_factory_fail_loud():
    with pytest.raises(ValueError):
        get_connector("oracle")  # 未知协议 fail-loud，不静默降级


# ── Phase 7.2 切流分派 ────────────────────────────────────────


def test_execute_query_switch_to_semantic_engine(monkeypatch):
    """执行载体走 semantics.execution 远程下推（dataengine 退役后唯一载体）。"""
    from backend.core import query_executor

    monkeypatch.setattr(
        "backend.common.db.datasource_db.get_datasource_by_id",
        lambda ds: {"db_type": "mysql", "host": "h", "port": 3306,
                    "user": "u", "password": "p", "database": "d"},
    )
    captured = {}

    def fake_pushdown(self, sql, **kw):
        captured.update(sql=sql, **kw)
        return pa.table({"a": [1, 2]})

    monkeypatch.setattr(
        "backend.semantics.execution.engine.SemanticEngine.execute_pushdown", fake_pushdown)
    df, elapsed_ms, row_count = query_executor.execute_query(
        "SELECT a FROM t LIMIT 2", 7, user_id=0)
    assert row_count == 2 and list(df.columns) == ["a"]
    assert captured["datasource_id"] == 7 and captured["db_type"] == "mysql"


def test_execute_query_federated_via_semantic_engine(monkeypatch):
    """federated 跨源联邦由新引擎 execute_federated 承载。"""
    from backend.core import query_executor

    monkeypatch.setattr(
        "backend.common.db.datasource_db.get_datasource_by_id",
        lambda ds: {"db_type": "mysql", "host": "h", "port": 3306,
                    "user": "u", "password": "p", "database": "d"},
    )
    captured = {}
    monkeypatch.setattr(
        "backend.semantics.execution.engine.SemanticEngine.execute_federated",
        lambda self, sql, sources, **kw: captured.update(sources=sources) or pa.table({"x": [1]}),
    )
    df, _, row_count = query_executor.execute_query(
        "SELECT x FROM aux.dim_t LIMIT 5", 7, user_id=0,
        federated=[{"name": "aux", "config": {"db_type": "mysql", "host": "h"}}])
    assert row_count == 1
    assert captured["sources"][1]["name"] == "aux"  # 辅源携带连接配置直传


# ── Postgres/ADBC 连接器 ───────────────────────────────────────


class _FakePgCursor:
    def __init__(self, table):
        self._t = table

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.executed = sql

    def fetch_arrow_table(self):
        return self._t


class _FakePgConn:
    def __init__(self, table):
        self._cur = _FakePgCursor(table)
        self.closed = False

    def cursor(self):
        return self._cur

    def close(self):
        self.closed = True


def test_postgres_connector_fetch_arrow():
    from backend.semantics.execution.connectors.postgres import PostgresConnector

    conn = _FakePgConn(pa.table({"a": [1, 2]}))
    c = PostgresConnector(conn_factory=lambda: conn)
    table = c.execute_pushdown("SELECT a FROM t")
    assert table.to_pydict() == {"a": [1, 2]} and conn.closed


def test_connector_factory_postgres():
    from backend.semantics.execution.connectors.postgres import PostgresConnector

    assert isinstance(get_connector("postgres"), PostgresConnector)
    assert isinstance(get_connector("postgresql"), PostgresConnector)


# ── 结果缓存（性能旁路） ──────────────────────────────────────


def test_execute_pushdown_result_cache(monkeypatch):
    eng = SemanticEngine(pool=SessionPool())
    calls = {"n": 0}

    class _Fake:
        def execute_pushdown(self, sql, **kw):
            calls["n"] += 1
            return pa.table({"a": [1]})

    monkeypatch.setattr("backend.semantics.execution.engine.get_connector", lambda *a, **kw: _Fake())

    class _MemCache:
        def __init__(self):
            self.data = {}

        def get(self, key):
            return self.data.get(key)

        def set(self, key, value, ttl=None):
            self.data[key] = value

    monkeypatch.setattr("backend.semantics.execution.cache.result_cache", _MemCache())
    t1 = eng.execute_pushdown("SELECT a FROM t LIMIT 1", datasource_id=7, use_cache=True)
    t2 = eng.execute_pushdown("SELECT a FROM t LIMIT 1", datasource_id=7, use_cache=True)
    assert calls["n"] == 1  # 第二次命中缓存
    assert t2.to_pydict() == t1.to_pydict() == {"a": [1]}

    eng.execute_pushdown("SELECT a FROM t LIMIT 1", datasource_id=7)  # 默认不缓存
    assert calls["n"] == 2


# ── 跨源联邦 ──────────────────────────────────────────────────


def test_execute_federated_joins_across_sources(monkeypatch):
    """各源下推拉回 Arrow + DataFusion 本地联邦；限定名 `<name>.<table>` 改写扁平注册名。"""
    eng = SemanticEngine(pool=SessionPool())
    tables = {
        7: pa.table({"id": [1, 2], "region_id": [10, 20]}),
        99: pa.table({"region_id": [10], "region": ["north"]}),
    }

    class _Fake:
        def __init__(self, table):
            self._t = table

        def execute_pushdown(self, sql, **kw):
            return self._t

    monkeypatch.setattr(
        "backend.semantics.execution.engine.get_connector",
        lambda db_type, datasource_id=0, config=None, **kw: _Fake(tables[datasource_id]),
    )
    out = eng.execute_federated(
        "SELECT t.id, d.region FROM t JOIN dim.t_region d ON t.region_id = d.region_id LIMIT 10",
        sources=[
            {"name": "", "datasource_id": 7, "db_type": "mysql", "tables": ["t"]},
            {"name": "dim", "datasource_id": 99, "db_type": "mysql", "tables": ["t_region"]},
        ],
    )
    assert out.to_pydict() == {"id": [1], "region": ["north"]}


def test_federated_tables_derived_from_sql_when_missing(monkeypatch):
    """source 缺 tables 时从 SQL 限定引用推导（federated 条目只有连接配置）。"""
    eng = SemanticEngine(pool=SessionPool())
    pulled = []

    class _Fake:
        def execute_pushdown(self, sql, **kw):
            pulled.append(sql)
            return pa.table({"x": [1]})

    monkeypatch.setattr("backend.semantics.execution.engine.get_connector", lambda *a, **kw: _Fake())
    eng.execute_federated(
        "SELECT x FROM aux.dim_t LIMIT 5",
        sources=[{"name": "aux", "datasource_id": 0, "db_type": "mysql",
                  "config": {"host": "h"}, "tables": []}],
    )
    assert any("dim_t" in s for s in pulled)  # 从 SQL 推导出 dim_t 并拉回
