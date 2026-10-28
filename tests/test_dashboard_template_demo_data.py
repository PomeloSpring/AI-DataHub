"""样式模板示例数据（demo chart）的服务端口径离线回归(不触真库)。

仪表盘模板的定位是**图表样式展示**：图卡不携带取数口径，只带内置演示数值。锁定的契约：
  - 客户端能写入的图表数据缓存**只有**示例数据（config.demoData=true），
    形状/行数超限/夹带真实数据一律拒并回可诊断原因（护栏 §7）。
  - 样式图（未绑数据源）点「刷新数据」时不下发任何 SQL，而是原样回读示例数据 ——
    不能把用户的样式效果抹成空白，也不能静默失败。
  - 一旦写了 SQL / 切语义查询 / 前端显式声明转真实取数，同批清掉示例标记与演示缓存
    （不留旧的 demoSql 占位口径）。
  - 纯外观/布局修改不得把真实图表的 data_cache 清掉。
"""

import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

dash = importlib.import_module("backend.modules.viz.services.dashboard_service")
api = importlib.import_module("backend.modules.viz.api.dashboard")

DEMO_SQL = "SELECT dt, sales FROM dwd_demo ORDER BY dt"  # 旧版样式模板可能残留的占位口径
REAL_SQL = "SELECT dt, sales FROM dwd_order_real ORDER BY dt"  # 用户自己写的真实口径
SAMPLE = {"columns": ["dt", "sales"], "rows": [{"dt": "10-19", "sales": 182}, {"dt": "10-20", "sales": 168}]}


def demo_chart(**over):
    """样式展示图：无 SQL（模板不携带取数口径），只有示例缓存与 demoData 标记。"""
    chart = {
        "id": 21, "dashboard_id": 10, "workspace_id": 1, "source_type": "template", "source_id": None,
        "name": "销售趋势", "chart_type": "line", "sql_query": "", "data_cache": json.dumps(SAMPLE),
        "semantic_query": None, "query_source": "raw_sql",
        "config": json.dumps({"xCol": "dt", "yCol": "sales", "demoData": True}),
    }
    chart.update(over)
    return chart


class FakeCursor:
    """按 SQL 回放：读查询回 charts 行，写语句进 log 供断言。"""

    def __init__(self, log, charts=()):
        self._log = log
        self._charts = list(charts)
        self._pending = None
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self._log.append((normalized, params))
        if normalized.startswith("SELECT"):
            self._pending = "select"
        else:
            self._pending = "write"

    def fetchone(self):
        if self._pending != "select" or not self._charts:
            return None
        chart = self._charts[0]
        fields = self._log[-1][0].split("FROM")[0].replace("SELECT", "").strip()
        keys = [f.strip() for f in fields.split(",")]
        if "*" in keys:
            return dict(chart)
        return {k: chart.get(k) for k in keys}

    def fetchall(self):
        if self._pending != "select":
            return []
        return [dict(c) for c in self._charts]


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _install(monkeypatch, log, charts=()):
    conn = FakeConn(FakeCursor(log, charts))
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: conn)
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload", lambda *a, **k: None)
    return conn


def _sets(update_entry):
    """把 UPDATE 语句的 SET 列名与参数对齐成 dict（参数顺序与列名一致）。"""
    sql, params = update_entry
    cols = [seg.split("`")[1] for seg in sql.split("SET")[1].split("WHERE")[0].split(",") if "`" in seg]
    return dict(zip(cols, params))


def _writes(log):
    return [entry for entry in log if not entry[0].startswith("SELECT")]


# ── 写入闸门：客户端缓存只允许示例数据 ──────────────────────────────────────


def test_create_chart_stores_sample_data_with_demo_flag(monkeypatch):
    log = []
    _install(monkeypatch, log)
    dash.chart_service.create_chart(10, {
        "name": "销售趋势", "chart_type": "line", "sql_query": "",   # 样式模板不携带 SQL
        "position": {"x": 20, "y": 20, "w": 600, "h": 320},
        "config": {"xCol": "dt", "yCol": "sales", "demoData": True},
        "data_cache": SAMPLE, "source_type": "template",
    })
    insert = _writes(log)[0]
    stored = insert[1][9]  # data_cache 占位
    assert json.loads(stored) == SAMPLE
    assert json.loads(insert[1][5])["demoData"] is True
    assert insert[1][4] == ""


@pytest.mark.parametrize("cache,cfg,needle", [
    (SAMPLE, {"xCol": "dt"}, "仅允许模板示例数据"),
    ({"columns": "oops", "rows": []}, {"demoData": True}, "格式无效"),
    ({"columns": ["a"], "rows": [{"a": i} for i in range(2000)]}, {"demoData": True}, "行数超限"),
])
def test_create_chart_rejects_bad_sample_with_readable_reason(monkeypatch, cache, cfg, needle):
    log = []
    _install(monkeypatch, log)
    with pytest.raises(ValueError) as exc:
        dash.chart_service.create_chart(10, {"name": "x", "chart_type": "bar", "config": cfg, "data_cache": cache})
    assert needle in str(exc.value)
    # 入库前即拒（不开连接、不发写语句），失败原因带可诊断信息回给调用方
    assert log == []


def test_api_chart_create_model_carries_data_cache():
    body = api.ChartCreate(name="x", chart_type="bar", data_cache=SAMPLE)
    assert body.model_dump()["data_cache"] == SAMPLE
    assert api.ChartCreate(name="x", chart_type="bar").data_cache is None


# ── 刷新闸门：样式图（未绑源）不下发任何 SQL ───────────────────────────────────


def test_refresh_demo_chart_without_source_returns_sample_and_skips_execute(monkeypatch):
    log = []
    _install(monkeypatch, log, [demo_chart()])
    monkeypatch.setattr(dash, "check_dashboard_visible", lambda *a, **k: True)

    def boom(*a, **k):
        raise AssertionError("示例图的演示 SQL 不得下发数据源")

    monkeypatch.setattr(dash, "governed_execute", boom)
    out = dash.chart_service.refresh_chart(10, 21, user_id=1)
    assert out["demo"] is True
    assert out["columns"] == SAMPLE["columns"] and len(out["rows"]) == 2
    assert not _writes(log)


def test_refresh_chart_after_datasource_binding_and_real_sql_executes(monkeypatch):
    """已转真实取数的图（绑了源 + 写了口径）必须正常下发治理取数，不再走示例分支。"""
    log = []
    _install(monkeypatch, log, [demo_chart(
        sql_query=REAL_SQL, config=json.dumps({"datasource_id": 5, "demoData": True}))])
    monkeypatch.setattr(dash, "check_dashboard_visible", lambda *a, **k: True)
    called = {}

    def fake_execute(sql, datasource_id, *a, **k):
        called["sql"], called["ds"] = sql, datasource_id
        return {"columns": ["dt", "sales"], "rows": [{"dt": "10-19", "sales": 1}], "row_count": 1}

    monkeypatch.setattr(dash, "governed_execute", fake_execute)
    out = dash.chart_service.refresh_chart(10, 21, user_id=1)
    assert called["ds"] == 5 and "dwd_order_real" in called["sql"]
    assert out["rows"] and "demo" not in out


def test_refresh_all_charts_keeps_demo_samples_intact(monkeypatch):
    log = []
    _install(monkeypatch, log, [demo_chart(), demo_chart(id=22, name="品类构成", chart_type="pie", data_cache=None)])
    monkeypatch.setattr(dash, "check_dashboard_visible", lambda *a, **k: True)

    def boom(*a, **k):
        raise AssertionError("示例图的演示 SQL 不得下发数据源")

    monkeypatch.setattr(dash, "governed_execute", boom)
    out = dash.chart_service.refresh_all_charts(10, user_id=1)
    results = out["charts"]
    assert results[21]["demo"] is True and len(results[21]["rows"]) == 2
    assert results[22]["demo"] is True and results[22]["rows"] == []  # 无缓存也不报错不执行
    assert not _writes(log)


def test_api_chart_refresh_skips_execution_for_unbound_demo(monkeypatch):
    log = []
    conn = FakeConn(FakeCursor(log, [demo_chart()]))
    from backend.modules.viz.api import chart as chart_api
    monkeypatch.setattr(chart_api, "DBConnection", lambda: conn)

    def boom(*a, **k):
        raise AssertionError("示例图的演示 SQL 不得下发数据源")

    monkeypatch.setattr(chart_api, "governed_execute", boom, raising=False)
    out = chart_api.refresh_chart(21, chart_api.ChartRefreshRequest(), user={"user_id": 1, "username": "u"})
    assert out["demo"] is True and len(out["rows"]) == 2
    assert not [e for e in log if not e[0].startswith("SELECT")]


# ── 转真实取数：同批清示例标记与演示缓存 ────────────────────────────────────


def test_update_demo_chart_with_new_sql_clears_flag_and_cache(monkeypatch):
    """样式图写了真实口径 → 同批清示例标记/演示缓存，并抹掉旧版残留的 demoSql 占位口径。"""
    log = []
    _install(monkeypatch, log, [demo_chart(
        config=json.dumps({"xCol": "dt", "yCol": "sales", "demoData": True, "demoSql": DEMO_SQL}))])
    dash.chart_service.update_chart(10, 21, {"sql_query": REAL_SQL})
    sets = _sets(_writes(log)[-1])
    assert sets["data_cache"] is None
    assert "demoData" not in sets["config"] and "demoSql" not in sets["config"]
    assert sets["sql_query"] == REAL_SQL
    assert json.loads(sets["config"])["xCol"] == "dt"  # 字段映射保留，只清示例相关键


def test_update_demo_chart_declared_live_clears_without_extra_read(monkeypatch):
    """前端显式声明 demoData=false：不等 SQL 变化也要清掉示例标记与演示缓存。"""
    log = []
    _install(monkeypatch, log, [demo_chart()])
    dash.chart_service.update_chart(10, 21, {"config": {"xCol": "dt", "yCol": "sales", "demoData": False}})
    update = _writes(log)[-1]
    assert update[0].startswith("UPDATE adh_charts SET")
    sets = _sets(update)
    assert sets["data_cache"] is None
    assert "demoData" not in sets["config"] and "demoSql" not in sets["config"]
    assert json.loads(sets["config"])["xCol"] == "dt"


def test_appearance_update_on_real_chart_keeps_cache(monkeypatch):
    """无示例标记的存量图表改样式：不得顺手清空真实 data_cache。"""
    log = []
    _install(monkeypatch, log, [{**demo_chart(), "config": json.dumps({"xCol": "dt"})}])
    dash.chart_service.update_chart(10, 21, {"config": {"xCol": "dt", "yCol": "sales"}, "position": {"x": 1, "y": 2, "w": 3, "h": 4}})
    update = _writes(log)[-1]
    assert "data_cache" not in update[0]
    assert not [e for e in log if e[0].startswith("SELECT config, sql_query")]


def test_update_chart_invalidates_detail_payload(monkeypatch):
    """图表写入后必须失效共享详情载荷（否则读者最长等一个 TTL 才看到改动）。"""
    log = []
    _install(monkeypatch, log, [demo_chart()])
    invalidated = []
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload",
                        lambda dashboard_id=None, **k: invalidated.append(dashboard_id))
    assert dash.chart_service.update_chart(10, 21, {"sql_query": "SELECT real FROM t"}) is True
    assert invalidated == [10]


def test_batch_update_invalidates_detail_payload_once(monkeypatch):
    log = []
    _install(monkeypatch, log, [demo_chart(), demo_chart(id=22)])
    invalidated = []
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload",
                        lambda dashboard_id=None, **k: invalidated.append(dashboard_id))
    outcome = dash.chart_service.batch_save(10, [], {"21": {"name": "改名"}, "22": {"name": "改名2"}}, [])
    assert outcome["ok"] is True
    assert 10 in invalidated


# ── 判定函数本身的边界 ───────────────────────────────────────────────────────


def test_demo_predicates_boundaries():
    assert dash.is_demo_chart(demo_chart()) is True
    assert dash.is_demo_chart(demo_chart(config=json.dumps({"xCol": "dt"}))) is False
    # 样式图（示例标记 + 未绑源）：不下发任何 SQL，回读示例缓存
    assert dash.demo_sample_without_source(demo_chart()) is True
    assert dash.demo_sample_without_source(demo_chart(config=json.dumps({"demoData": True}))) is True
    # 历史脏数据：样式图带了占位 SQL 也不下发（宁可不出数，不把假想表打到数据源）
    assert dash.demo_sample_without_source(demo_chart(sql_query=DEMO_SQL)) is True
    # 绑了源 = 进入真实取数链路
    assert dash.demo_sample_without_source(
        demo_chart(sql_query=REAL_SQL, config=json.dumps({"demoData": True, "datasource_id": 3}))) is False
    # 无示例标记的普通图不受影响
    assert dash.demo_sample_without_source(
        demo_chart(sql_query=REAL_SQL, config=json.dumps({"xCol": "dt"}))) is False
