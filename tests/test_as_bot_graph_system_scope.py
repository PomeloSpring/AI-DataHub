"""AS-BOT 系统能力本体图谱 · 系统域隔离回归锁(离线, 假 DB/SPARQL)。

背景(违反即缺陷): AS-BOT 是系统 AS-BOT, 其本体图谱(表关系/业务知识/本体总览)只允许
消费**系统本体**(datasource_id IS NULL/0), 严禁串入业务本体(如 test-alb, datasource_id>0)。
旧 bug: 图谱读/建借用 ds:0("聚合全部"), 导致 test-alb 元数据/模型灌进 AS-BOT 视图。

锁定的契约:
- graph_uri(SYSTEM_DATASOURCE_ID) 是独立命名图 ds:-1, 不等于聚合图 ds:0;
- 建图 loader 在系统域只按 `datasource_id = 0 OR datasource_id IS NULL` 过滤(params 不含业务 id, 不出现"无过滤"的聚合分支);
- _merge_ontology_models 系统域只取 (datasource_id IS NULL OR =0) 的 active 模型, 排除业务模型;
- GraphService.get_graph_data(datasource_id=-1) 只查 ds:-1, 绝不查 ds:0/业务图;
- 查询接口 _effective_ds(system_scope=True) → SYSTEM_DATASOURCE_ID。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from services.datamind.rag.graph_rag.graph_builder import (  # noqa: E402
    GraphBuilder, _ds_scope,
)
from services.datamind.rag.graph_rag.oxigraph_store import (  # noqa: E402
    SYSTEM_DATASOURCE_ID, OxigraphStore, is_system_scope,
)
from services.semhub.graph.graph_service import GraphService  # noqa: E402
from services.semhub.api.graph import _effective_ds  # noqa: E402
from services.shared.common.rdf.namespaces import ADH_NS  # noqa: E402


# ── 假 DB 连接: 捕获 (sql, params) ──────────────────────────────────
class _FakeCursor:
    def __init__(self, sink, rows=None):
        self._sink = sink
        self._rows = rows or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sink.append((" ".join(sql.split()), list(params or [])))

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, sink, rows=None):
        self._sink = sink
        self._rows = rows or []

    def cursor(self):
        return _FakeCursor(self._sink, self._rows)

    def close(self):
        pass


@pytest.fixture
def capture(monkeypatch):
    sink: list = []

    class _ConnMod:
        @staticmethod
        def get_metadata_conn():
            return _FakeConn(sink)

    import services.shared.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", _ConnMod.get_metadata_conn)
    return sink


# ── 命名图隔离 ───────────────────────────────────────────────────────
def test_system_graph_uri_is_distinct_from_aggregate():
    sys_g = OxigraphStore.graph_uri(SYSTEM_DATASOURCE_ID)
    agg_g = OxigraphStore.graph_uri(0)
    assert sys_g == f"{ADH_NS}ds:-1"
    assert sys_g != agg_g                       # 系统图 ≠ 聚合图 ds:0
    assert is_system_scope(SYSTEM_DATASOURCE_ID) and not is_system_scope(0)


# ── 建图 loader 数据源过滤三态 ─────────────────────────────────────
def test_ds_scope_system_only_filters_null_or_zero():
    clause, params = _ds_scope(SYSTEM_DATASOURCE_ID)
    assert clause == "AND (datasource_id = 0 OR datasource_id IS NULL)"
    assert params == []                          # 不绑定任何业务数据源 id


def test_ds_scope_business_and_aggregate_unchanged():
    assert _ds_scope(5) == ("AND (datasource_id = %s OR datasource_id = 0)", [5])
    assert _ds_scope(0) == ("", [])              # 聚合语义保持"全部"(ChatBI 主链路不受影响)


def test_load_tables_system_scope_excludes_business(capture):
    GraphBuilder(store=OxigraphStore.__new__(OxigraphStore))._load_tables(SYSTEM_DATASOURCE_ID)
    sql, params = capture[-1]
    assert "datasource_id = 0 OR datasource_id IS NULL" in sql
    assert "adh_table_info" in sql
    assert params == []
    # 绝不能出现业务聚合的"无过滤"分支, 也不得出现 = %s 的业务绑定
    assert "WHERE is_active = 1 AND (" in sql


def test_merge_ontology_models_system_scope_only_system_models(capture):
    b = GraphBuilder(store=_RecordingStore())
    n = b._merge_ontology_models(SYSTEM_DATASOURCE_ID)
    sql, params = capture[-1]
    assert n == 0                                 # 无模型返回(空)
    assert "adh_ontology_models" in sql
    # 判别口径用 kind（x3/x4 归属改造）：业务本体跨源 ds_id=0，旧的
    # `datasource_id=0` 判系统会把业务本体也拉进系统图。系统域只收 kind='system'。
    assert "kind = 'system'" in sql
    assert "status = 'active'" in sql
    assert params == []
    # 关键: 系统域只含系统本体，不得含业务/源本体(那会把 test-alb/业务对象 merge 进来)
    assert "kind = 'business'" not in sql and "kind = 'source'" not in sql


# ── 查询只命中 ds:-1 ───────────────────────────────────────────────
class _RecordingStore:
    """记录写入 load_turtle 的 datasource_id, 并暴露 graph_uri。"""
    def __init__(self):
        self.loaded = []

    def graph_uri(self, ds):
        return f"{ADH_NS}ds:{ds}"

    def load_turtle(self, turtle, datasource_id=0):
        self.loaded.append(datasource_id)

    def clear_graph(self, ds):
        pass

    def count_triples(self, ds):
        return 0


class _CapturingClient:
    def __init__(self):
        self.queries = []

    def query(self, sparql):
        self.queries.append(sparql)
        return []


def test_get_graph_data_system_scope_queries_only_minus1():
    svc = GraphService.__new__(GraphService)
    store = _RecordingStore()
    client = _CapturingClient()
    svc._store = store
    svc._client = client
    from services.shared.models.graph import GraphType
    for gt in (GraphType.TABLE_RELATION, GraphType.BUSINESS_KNOWLEDGE, GraphType.ONTOLOGY_OVERVIEW):
        client.queries.clear()
        svc.get_graph_data(graph_type=gt, datasource_id=SYSTEM_DATASOURCE_ID)
        assert client.queries, f"{gt.value} 未发起任何 SPARQL"
        joined = "\n".join(client.queries)
        assert "ds:-1" in joined
        assert "ds:0>" not in joined and f"{ADH_NS}ds:0" not in joined  # 不查聚合图
        assert "ds:5" not in joined                                     # 不查业务图


def test_effective_ds_maps_system_scope_to_sentinel():
    assert _effective_ds(0, True) == SYSTEM_DATASOURCE_ID
    assert _effective_ds(7, False) == 7          # 业务数据源原样透传
    assert _effective_ds(0, False) == 0


# ── 字典/术语/模板/数据源节点的系统域归属(串入即缺陷) ────────────────
def test_load_terms_system_scope_skipped(capture):
    """业务术语是行业黑话缓冲区, 系统域图一律不收录。"""
    b = GraphBuilder(store=OxigraphStore.__new__(OxigraphStore))
    assert b._load_terms(SYSTEM_DATASOURCE_ID) == []
    assert capture == []                          # 不发任何 SQL, 也不按 ds=0 误捞全局术语


def test_load_metrics_system_scope_filters_by_bound_object_keys(capture, monkeypatch):
    """字典行 datasource_id 恒为 0: 系统域只能按绑定对象∈系统模型对象筛, 不得按 datasource 列筛。"""
    from services.datamind.rag.graph_rag import graph_builder as gb
    monkeypatch.setattr(gb, "_system_object_keys", lambda: ["as_bot_approval", "as_bot"])
    b = GraphBuilder(store=OxigraphStore.__new__(OxigraphStore))
    b._load_metrics(SYSTEM_DATASOURCE_ID)
    sql, params = capture[-1]
    assert "bound_object_key IN (%s,%s)" in sql
    assert params == ["as_bot_approval", "as_bot"]
    # 关键: 绝不再出现"datasource_id = 0 OR IS NULL"这种对字典等于没筛的旧条件
    assert "datasource_id = 0 OR" not in sql


def test_load_metrics_system_scope_without_keys_loads_nothing(capture, monkeypatch):
    """无系统模型对象时宁缺勿错: 不查库、不收任何指标(未绑定指标如 GMV 永远不进系统图)。"""
    from services.datamind.rag.graph_rag import graph_builder as gb
    monkeypatch.setattr(gb, "_system_object_keys", lambda: [])
    b = GraphBuilder(store=OxigraphStore.__new__(OxigraphStore))
    assert b._load_metrics(SYSTEM_DATASOURCE_ID) == []
    assert capture == []


def test_load_sql_templates_system_scope_skipped(capture):
    """SQL 模板含业务表语句, 系统域图不收录。"""
    b = GraphBuilder(store=OxigraphStore.__new__(OxigraphStore))
    assert b._load_sql_templates(SYSTEM_DATASOURCE_ID) == []
    assert capture == []


def test_build_system_scope_skips_datasource_nodes(capture, monkeypatch):
    """系统域建图不得枚举业务数据源(test-alb 名称不得进系统图)。"""
    b = GraphBuilder(store=_RecordingStore())
    called = {"ds": False}

    def _boom():
        called["ds"] = True
        return [{"id": 1780478236183, "name": "test-alb", "db_type": "mysql"}]

    monkeypatch.setattr(b, "_load_tables", lambda ds: [])
    monkeypatch.setattr(b, "_load_columns", lambda ds: [])
    monkeypatch.setattr(b, "_load_terms", lambda ds: [])
    monkeypatch.setattr(b, "_load_metrics", lambda ds: [])
    monkeypatch.setattr(b, "_load_join_relations", lambda ds: [])
    monkeypatch.setattr(b, "_load_sql_templates", lambda ds: [])
    monkeypatch.setattr(b, "_load_datasources", _boom)
    stats = b.build_from_metadata(SYSTEM_DATASOURCE_ID)
    assert stats["success"] and stats["datasources"] == 0
    assert called["ds"] is False


def test_system_object_keys_parses_json_extract(monkeypatch):
    """对象 key 来自系统域 active 模型的 json_content, 业务模型不参与。"""
    from services.datamind.rag.graph_rag import graph_builder as gb
    sink: list = []

    class _Conn:
        def cursor(self):
            return _FakeCursor(sink, rows=[{"objs": '["as_bot", "as_bot_approval", "as_bot"]'}])
        def close(self):
            pass

    import services.shared.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _Conn())
    keys = gb._system_object_keys()
    assert keys == ["as_bot", "as_bot_approval"]   # 去重+排序(sorted)
    sql, params = sink[0]
    assert "datasource_id IS NULL OR datasource_id = 0" in sql
    assert "status = 'active'" in sql
    assert params == []
