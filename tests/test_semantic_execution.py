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


# ── SLS(PG 协议) 方言保真：治理回写不得用 mysql 破坏 Trino/Presto 形状 SQL ──

# 典型 SLS 漏斗查询形状：双引号标识符 + ARRAY[] + UNNEST(...) AS t(col) + 已有外层 LIMIT
_SLS_SQL = (
    'WITH f AS (SELECT "pcId", window_funnel(1800, "timestamp", '
    'ARRAY["event_name" = \'/a/\', "event_name" = \'/b/\']) AS level '
    'FROM observability_rum WHERE "__time__" >= to_unixtime(now() - interval \'3\' month) '
    'GROUP BY "pcId"), '
    'e AS (SELECT "pcId", "level", t.level_index FROM f '
    'CROSS JOIN UNNEST(sequence(1, "level")) AS t(level_index)) '
    'SELECT "level_index", count(1) AS user_count FROM e '
    'GROUP BY "level_index" ORDER BY "level_index" LIMIT 1000'
)

# date_add('month',-3,now())/IF/UNNEST：mysql 方言 sqlglot 解不动，presto/trino/postgres 可以
_SLS_FN_SQL = (
    "WITH f AS (SELECT \"pcId\", window_funnel(1800, \"timestamp\", "
    "ARRAY[\"event_name\" = '/a/']) AS level FROM ("
    "SELECT \"timestamp\", \"pcId\" FROM observability_rum "
    "WHERE \"__time__\" >= to_unixtime(date_add('month', -3, now()))"
    ") AS base GROUP BY \"pcId\") "
    "SELECT IF(count(1) > 0, 1, 0) AS ok FROM f GROUP BY \"pcId\" LIMIT 100"
)


def test_dialect_of_db_type_maps_sls_to_postgres():
    from backend.semantics.sql_guard import dialect_of_db_type
    assert dialect_of_db_type("sls") == "postgres"      # SLS 走 PG 协议 → postgres 族
    assert dialect_of_db_type("postgres") == "postgres"
    assert dialect_of_db_type("doris") == "mysql"
    assert dialect_of_db_type("mysql") == "mysql"
    assert dialect_of_db_type("") == "mysql"            # 未知保守回落 mysql


def test_bounded_query_keeps_sls_sql_verbatim_when_limit_compliant():
    """huge 源已带合规外层 LIMIT 时原样返回，绝不 sqlglot 重生成（防 mysql 生成器破坏 SLS 形状）。"""
    eng = SemanticEngine(pool=SessionPool())
    out, need = eng.bounded_sql(_SLS_SQL, {"size_class": "huge"}, dialect="postgres", max_rows=1000)
    assert need
    assert out == _SLS_SQL                      # 已 ≤ cap 且带外层 LIMIT → 不动
    assert '"pcId"' in out and 'ARRAY[' in out  # 标识符/数组未被破坏


def test_inject_filters_preserves_sls_identifiers_with_postgres_dialect():
    from backend.semantics.sql_guard import inject_filters
    out, applied = inject_filters(_SLS_SQL, {"observability_rum": "tenant_id = 42"}, dialect="postgres")
    assert applied == ["observability_rum"]
    assert '"pcId"' in out and 'ARRAY[' in out  # 未被 mysql 生成器破坏
    assert "'pcId'" not in out                  # 标识符没退化成字符串字面量


def test_inject_filters_mysql_dialect_silently_corrupts_sls_shape():
    """反向锁定：同一 SQL 走 mysql 方言不报错而是静默破坏（历史根因）。"""
    from backend.semantics.sql_guard import inject_filters
    out, applied = inject_filters(_SLS_SQL, {"observability_rum": "tenant_id = 42"}, dialect="mysql")
    assert applied == ["observability_rum"]
    assert '"pcId"' not in out and "'pcId'" in out  # 标识符退化为字符串字面量（脏）


def test_validate_sql_accepts_sls_presto_shape():
    """合法 SLS/Presto 只读查询不得因 mysql 方言解不动而被误拒（历史上报“仅允许单条”）。"""
    from backend.core.query_executor import validate_sql
    assert validate_sql(_SLS_FN_SQL, require_limit=False) == (True, "")
    assert validate_sql(_SLS_FN_SQL, require_limit=True)[0] is True


def test_validate_sql_still_rejects_write_and_multi():
    """方言宽容不得弱化安全：DDL/DML/多语句仍拒（fail-closed）。"""
    from backend.core.query_executor import validate_sql
    for bad in ("DROP TABLE t", "INSERT INTO t VALUES (1)", "DELETE FROM t",
                "UPDATE t SET a=1", "SELECT 1; DROP TABLE t"):
        assert validate_sql(bad, require_limit=False)[0] is False, bad


def test_validate_sql_surfaces_parse_reason():
    """全方言都解不动时，拒绝文案必须带真实解析原因（不再笼统“仅允许单条”）。"""
    from backend.core.query_executor import validate_sql
    ok, msg = validate_sql("SELECT FROM ((( 未闭合", require_limit=False)
    assert ok is False
    assert "解析失败" in msg and msg != "仅允许单条 SELECT/WITH 只读查询"


def test_parse_readonly_prefers_given_dialect_then_falls_back():
    from backend.semantics.sql_guard import parse_readonly
    assert parse_readonly(_SLS_FN_SQL, "mysql") is not None   # mysql 解不动→回退 presto/trino
    assert parse_readonly(_SLS_FN_SQL, "presto") is not None


def test_playground_ast_dialect_tolerant_for_sls(monkeypatch):
    """Playground AST/血缘静态分析：sls 数据源的 date_add/IF/UNNEST 形状不得因 mysql 方言报
    'parse failed: INTERVAL expression expected'（按目标源方言/回退族解析）。"""
    import backend.common.db as cdb
    monkeypatch.setattr(cdb, "get_datasource_by_id",
                        lambda i: {"id": i, "db_type": "sls"}, raising=False)
    from backend.modules.semhub.api import playground as pg
    assert pg._resolve_dialect({"datasource_id": 7}) == "postgres"
    tree, used = pg._parse_any(_SLS_FN_SQL, pg._resolve_dialect({"datasource_id": 7}))
    assert tree is not None and used == "postgres"
    # 旧行为对照：mysql 硬解会抛错（即截图里的 parse failed）
    import sqlglot
    with pytest.raises(Exception):
        sqlglot.parse_one(_SLS_FN_SQL, dialect="mysql")


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


def test_mysql_connector_dict_cursor_rows():
    """工厂连接为 DictCursor（fetchall 返回 dict 行）：值必须按列名取，不得把键当值。"""
    cur = _FakeCursor(["案例数量"], [{"案例数量": 7}])
    conn = _FakeConn(cur)
    c = MySQLConnector(datasource_id=7, conn_factory=lambda: conn)
    table = c.execute_pushdown("SELECT COUNT(*) AS `案例数量` FROM t LIMIT 10")
    assert table.to_pydict() == {"案例数量": [7]}  # 值为 7，而非列名


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
    from backend.semantics.execution.connectors.sls import SlsConnector

    assert isinstance(get_connector("postgres"), PostgresConnector)
    assert isinstance(get_connector("postgresql"), PostgresConnector)
    # SLS 不支持 prepared statement，不能走 ADBC PostgresConnector，必须走 psycopg2 SlsConnector
    assert isinstance(get_connector("sls"), SlsConnector)
    assert not isinstance(get_connector("sls"), PostgresConnector)


def test_sls_connector_uses_simple_protocol():
    """SLS 连接器走 psycopg2（无参数 execute = simple 协议），不得依赖 ADBC prepared statement。"""
    from backend.semantics.execution.connectors.sls import SlsConnector

    calls = {}

    class _FakeCur:
        description = [("a",), ("b",)]

        def execute(self, sql, *args, **kwargs):
            calls["sql"] = sql
            calls["args"] = args  # 必须为空：有参数则 psycopg2 走扩展协议，SLS 会拒

        def fetchall(self):
            return [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]  # RealDictCursor 行

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

        def close(self):
            calls["closed"] = True

    c = SlsConnector(conn_factory=lambda: _FakeConn())
    table = c.execute_pushdown("SELECT a, b FROM t")
    assert calls["args"] == ()  # 无绑定参数 → simple 协议
    assert table.to_pydict() == {"a": [1, 2], "b": ["x", "y"]}
    assert calls.get("closed")


def test_postgres_connector_statement_timeout_via_uri(monkeypatch):
    """ADBC postgresql 驱动只从 URI 解析连接参数：statement_timeout 必须经 URI
    查询参数 options 下发，不得放进 db_kwargs/conn_kwargs（否则报 Unknown option）。"""
    import adbc_driver_postgresql.dbapi as pg_adbc
    from backend.semantics.execution.connectors.postgres import PostgresConnector

    captured = {}

    def _fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return _FakePgConn(pa.table({"a": [1]}))

    monkeypatch.setattr(pg_adbc, "connect", _fake_connect)
    c = PostgresConnector(config={
        "host": "h", "port": 5432, "user": "u", "password": "p", "database": "alliedstar-prd",
    })
    c.execute_pushdown("SELECT 1", timeout_sec=30)

    uri = captured["uri"]
    kw = captured["kwargs"]
    assert "?options=" in uri and "statement_timeout" in uri
    # 驱动拒的两种传法都不得出现
    for d in ("db_kwargs", "conn_kwargs"):
        assert "options" not in (kw.get(d) or {})


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
