"""图表部分更新与复制契约的离线回归(不触真库)。

覆盖计划「后端回归」要点:
  - 仅更新 config/position 不得把名称、SQL、缓存、语义字段写成空值。
  - 空更新 / 相同值更新不误报 404(对象存在即成功)。
  - 语义图表复制保留 semantic_query / query_source / filters(含设计), 不降级为 raw_sql。
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

dash = importlib.import_module("backend.modules.viz.services.dashboard_service")


class FakeCursor:
    def __init__(self, log, rowcount=1, fetchone_result=None, fetchall_result=None):
        self._log = log
        self.rowcount = rowcount
        self._fetchone = fetchone_result
        self._fetchall = fetchall_result or []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))
        # UPDATE/INSERT 默认成功; SELECT id ... 命中由 fetchone 决定
        self.rowcount = self._log[-1][1] if False else self.rowcount

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return self._fetchall


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _install(monkeypatch, log, *, rowcount=1, fetchone_result=None, fetchall_result=None,
             stored_row=None):
    cur = FakeCursor(log, rowcount=rowcount, fetchone_result=fetchone_result, fetchall_result=fetchall_result)
    conn = FakeConn(cur)
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: conn)
    # 闸门回读与缓存口径签名判定都走模块级 execute_query（离线用例不得碰真库）。
    # stored_row=None = 库里无此图 → 口径签名判定走“无法判定→清缓存”的保守分支。
    monkeypatch.setattr(dash, "execute_query", lambda *a, **k: stored_row)
    return conn


def _row(**over):
    """库里旧行的最小口径形状（source_type=template 免数据集闸门）。"""
    row = {"name": "C", "chart_type": "bar", "source_type": "template", "source_id": None,
           "sql_query": "", "query_source": "raw_sql", "semantic_query": None, "config": "{}"}
    row.update(over)
    return row


def test_update_chart_only_visual_preserves_query_and_cache(monkeypatch):
    log = []
    _install(monkeypatch, log, rowcount=1, stored_row=_row())
    ok = dash.chart_service.update_chart(10, 5, {
        "config": {"vis": {"version": 1}}, "position": {"x": 0, "y": 0, "w": 4, "h": 3},
    })
    assert ok is True
    sql, params = log[0]
    # 只写入 config/position/updated_at, 绝不出现 name/sql_query/data_cache/semantic_query 赋值
    assert "SET" in sql
    assert "`name`=" not in sql and "`sql_query`=" not in sql
    # 缓存保留（含字面量 data_cache=NULL 清缓存写法）：只改样式必须套新样式用旧数据
    assert "data_cache" not in sql
    assert "`semantic_query`=" not in sql
    assert "`config`=%s" in sql and "`position`=%s" in sql
    # config/position 被序列化, 未提交的字段不进入 params
    assert json.loads(params[0]) == {"vis": {"version": 1}}


def test_update_chart_cache_key_ignores_style_but_tracks_query(monkeypatch):
    """缓存键只吃取数口径：样式差异同键（旧缓存不失配），口径差异异键。"""
    base = {"id": 5, "source_type": "dataset", "source_id": 9,
            "config": {"limit": 100}, "semantic_query": None}
    styled = dict(base, config={"limit": 100, "vis": {"color": "#f00"}, "title": "x"})
    assert dash._chart_cache_key(base) == dash._chart_cache_key(styled)
    # 口径键（limit/filters/time_window）变化必须换键 → 读侧失配才不会拿旧数据充数
    assert dash._chart_cache_key(base) != dash._chart_cache_key(dict(base, config={"limit": 500}))
    assert dash._chart_cache_key(base) != dash._chart_cache_key(
        dict(base, config={"limit": 100, "filters": [{"col": "a"}]}))
    assert dash._chart_cache_key(base) != dash._chart_cache_key(
        dict(base, config={"limit": 100, "time_window": "7d"}))


def test_update_chart_query_change_clears_cache(monkeypatch):
    """口径键变更必须清缓存（宁清勿错数）：改 limit 后旧分页数据不得当新口径结果。"""
    log = []
    _install(monkeypatch, log, rowcount=1, stored_row=_row(config=json.dumps({"limit": 100})))
    dash.chart_service.update_chart(10, 5, {"config": {"limit": 500}})
    sql = log[0][0]
    assert "data_cache=NULL" in sql and "data_cache_key=NULL" in sql


def test_update_chart_chart_type_change_clears_cache(monkeypatch):
    """图型变更必须清缓存：表值图的 20 行分页缓存当全量趋势是错数。"""
    log = []
    _install(monkeypatch, log, rowcount=1, stored_row=_row(chart_type="table_value"))
    dash.chart_service.update_chart(10, 5, {"chart_type": "line"})
    assert "data_cache=NULL" in log[0][0]


def test_update_chart_unreadable_old_row_clears_cache(monkeypatch):
    """回读不到旧行 = 无法判定口径是否变化 → 保守清缓存（fail-safe，宁清勿错数）。"""
    log = []
    _install(monkeypatch, log, rowcount=1, stored_row=None)
    dash.chart_service.update_chart(10, 5, {"config": {"vis": {"version": 2}}})
    assert "data_cache=NULL" in log[0][0]


def test_update_chart_rename_without_query_fields_keeps_cache(monkeypatch):
    """改名/挪位不带任何口径字段：不回读也不清缓存（零额外库读）。"""
    log = []
    _install(monkeypatch, log, rowcount=1, stored_row=None)
    dash.chart_service.update_chart(10, 5, {"name": "新标题", "position": {"x": 1, "y": 1, "w": 2, "h": 2}})
    sql = log[0][0]
    assert "data_cache" not in sql
    # 回读只经 execute_query（已被 fake，不进 log）：log 里不得出现口径 SELECT
    assert not [e for e in log if e[0].startswith("SELECT chart_type")]


def test_update_chart_rejects_empty_required_metadata(monkeypatch):
    log = []
    _install(monkeypatch, log)
    import pytest
    with pytest.raises(ValueError):
        dash.chart_service.update_chart(10, 5, {"name": ""})


def test_update_chart_noop_update_exists_returns_true(monkeypatch):
    log = []
    _install(monkeypatch, log, fetchone_result={"id": 5})
    # 空提交: 无 SET, 走存在性检查, 存在 -> True
    assert dash.chart_service.update_chart(10, 5, {}) is True


def test_update_chart_missing_returns_false(monkeypatch):
    log = []
    _install(monkeypatch, log, rowcount=0, fetchone_result=None)
    # 提交了字段但 rowcount=0 且对象不存在 -> False (上层据此 404)
    assert dash.chart_service.update_chart(10, 5, {"position": {"x": 1, "y": 1, "w": 2, "h": 2}}) is False


def test_vis_library_accepts_theme_pack_category(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    assert "theme_pack" in vls.CATEGORIES
    captured = {}
    monkeypatch.setattr(vls, "execute_insert", lambda sql, params: (captured.update(params=params), 77)[1])
    monkeypatch.setattr(vls, "execute_query", lambda *a, **k: None)
    cid = vls.vis_library_service.create_component(
        {"name": "P", "category": "theme_pack",
         "style_config": {"mode": "dark", "palette": ["#22d3ee"], "charts": {"default": {"gradient": True}}}},
        user_id=1, is_admin=True,
    )
    assert cid == 77
    import json as _json
    sc = _json.loads(captured["params"][4])
    assert sc["charts"]["default"]["gradient"] is True


def test_copy_dashboard_preserves_semantic_and_design(monkeypatch):
    log = []
    src_dash = {
        "id": 1, "name": "S", "description": "", "layout": "[]",
        "filters": json.dumps({"design": {"version": 1}}), "params": "[]",
        "status": "enabled", "workspace_id": 0, "carousel_interval": 10,
    }
    src_chart = {
        "name": "C", "chart_type": "bar", "sql_query": "SELECT secret",
        "config": "{}", "position": "{}", "source_type": "semantic", "source_id": None,
        "data_cache": None, "semantic_query": json.dumps({"object": "x"}), "query_source": "semantic",
    }
    # 依次(仅 SELECT 分支消费): SELECT dashboards(源) -> SELECT charts(源) -> SELECT dashboards(新) -> SELECT charts(新)
    seq = iter([
        (src_dash, []),            # first fetchone: src dashboard
        (None, [src_chart]),       # fetchall: charts
        ({"id": 2, "name": "S (副本)", "description": "", "layout": "[]",
         "filters": src_dash["filters"], "params": "[]", "status": "designing",
         "workspace_id": 0, "carousel_interval": 10, "created_at": None, "updated_at": None},
         []),                       # fetchone: new dashboard
        (None, [dict(src_chart, id=99, dashboard_id=2)]),  # fetchall: new charts
    ])

    class SeqCursor(FakeCursor):
        def __init__(self, log):
            super().__init__(log)

        def execute(self, sql, params=None):
            self._log.append((" ".join(sql.split()), params))
            s = sql.upper()
            if "SELECT * FROM ADH_DASHBOARDS" in s:
                fo, fa = next(seq)
                self._one, self._all = fo, []
            elif "SELECT * FROM ADH_CHARTS" in s:
                fo, fa = next(seq)
                self._one, self._all = fo, fa
            else:
                self._one, self._all = None, []
            self.rowcount = 1

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._all

    cur = SeqCursor(log)
    dash._normalize_dashboard = lambda d: d
    dash._normalize_chart = lambda c: c
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: FakeConn(cur))
    monkeypatch.setattr(dash, "_invalidate_dashboard_cache", lambda *a, **k: None)

    result = dash.dashboard_service.copy_dashboard(1, user_id=7)
    # INSERT chart 携带原始 semantic_query / query_source, 未降级
    insert_calls = [c for c in log if c[0].startswith("INSERT INTO adh_charts")]
    assert insert_calls, "应复制图表"
    params = insert_calls[0][1]
    assert json.loads(params[10]) == {"object": "x"}   # semantic_query
    assert params[11] == "semantic"                     # query_source
    # 复制的 filters 保留设计配置
    dash_insert = [c for c in log if c[0].startswith("INSERT INTO adh_dashboards")][0]
    assert json.loads(dash_insert[1][4])["design"]["version"] == 1
    assert result is not None
