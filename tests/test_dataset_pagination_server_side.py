"""数据集服务端分页（表值图真分页）的离线回归（不触真库/真集群）。

2026-11-11 契约（本文件锁定的口径）：
  - 分页只接受 `page_size`（每页行数，服务端硬上限 100）与 `page_num`（页号，从 1 起）；
    **不接受 offset**，偏移量由服务端算 —— 让调用方决定扫到第几行等于第二取数口径。
  - 行总数一律由服务端从数据集编译产物生成；旧的 `count_sql`（前端可任意递交一条统计
    SQL）是第二取数通道，已废除。
  - 是否真的按页取由**数据集**的 `paginable` 决定（明细行集 vs 统计汇总），
    不是图表开关：图表曾经有 `enableServerPagination` 开关，现网 13 张表值图全关着，
    于是"共 100 行"挂在 446 万行的明细表上 —— 那是伪分页，本批连同开关一起下线。
  - SQL 通道走 `LIMIT n OFFSET m`（可随机跳页，paging.can_jump=true）；
    ES 通道走 `/_sql/translate` + `search_after` 游标（集群无 PIT ⇒ 只能顺序往后翻，
    paging.can_jump=false）；语义链（七闸门）没有 OFFSET/COUNT 形状 ⇒ 显式拒绝。
  - 计数失败不得静默去掉总数（前端会把缺 total 读成真实行数），必须带原因报错。
  - 不分页（统计型数据集）时行数撞到 `_MAX_LIMIT` 上限要显式标 `truncated`，
    不得拿"共 N 行"冒充全量。
  - `paging.stable_order` 回显行序确定性：正文最外层没有 ORDER BY 时 OFFSET 的行序
    由执行引擎自定（活库实测：RUM 明细第 2/3 页出过同一批行）。服务端不替正文猜
    排序列（那是口径），但必须把"翻页可能重行/漏行"递到前端说出来。
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ds_svc = importlib.import_module("backend.modules.viz.services.dataset_service")
gq = importlib.import_module("backend.core.governed_query")
es_pagination = importlib.import_module("backend.modules.viz.services.es_pagination")
es_client = importlib.import_module("backend.core.es_client")

IDENT = {"user_id": 7, "username": "analyst01", "role": "analyst", "workspace_id": 1}

BASE_SQL = "SELECT dt, sales FROM (\nSELECT * FROM t LIMIT 5000\n) AS _ds_base LIMIT 200"

# 明细型数据集（真分页可翻）；paginable 就是这一位，缺省按不分页处理
DETAIL_DS = {"id": 33, "name": "ds", "datasource_id": 5, "paginable": 1}


# ── 分页参数解析：只认 page_size/page_num，页大小硬上限 100 ──────────────────


class TestResolvePage:
    def test_absent_means_not_paged(self):
        assert es_pagination.resolve_page({}) is None
        assert es_pagination.resolve_page({"limit": 500}) is None

    def test_page_size_is_clamped_to_100(self):
        """档位之外（手改请求体/旧配置残留的 500 行大页）一律收到 100，不放开扫行规模。"""
        assert es_pagination.resolve_page({"page_size": 999999}) == (100, 1)
        assert es_pagination.resolve_page({"page_size": 500, "page_num": 3}) == (100, 3)

    def test_three_tiers_pass_through(self):
        for n in (20, 50, 100):
            assert es_pagination.resolve_page({"page_size": n, "page_num": 2}) == (n, 2)

    @pytest.mark.parametrize("params", [
        {"page_size": 0}, {"page_size": -5}, {"page_size": 20, "page_num": 0},
        {"page_size": 20, "page_num": -1}, {"page_size": "abc"}, {"page_size": 20, "page_num": "x"},
    ])
    def test_illegal_values_rejected_not_silently_fixed(self, params):
        """给了但非法 = 报错。静默改成首页/1 页会把前端的 bug 吃成“看起来能用的错页”。"""
        with pytest.raises(ValueError):
            es_pagination.resolve_page(params)

    def test_absent_page_num_defaults_to_first_page(self):
        assert es_pagination.resolve_page({"page_size": 20}) == (20, 1)


class TestPagingGateIsDatasetLevel:
    def test_non_paginable_dataset_ignores_page_params(self):
        """统计展示型数据集：分页参数不生效（整页返回），不是报错也不是假翻页。"""
        assert ds_svc._paging_request({"paginable": 0}, {"page_size": 20, "page_num": 3}) is None

    def test_paginable_dataset_resolves_page(self):
        assert ds_svc._paging_request(DETAIL_DS, {"page_size": 50, "page_num": 2}) == (50, 2)

    def test_missing_column_defaults_to_unpaged(self):
        """未应用迁移的库没有该列：按不分页处理（原行为），不炸取数主链路。"""
        assert ds_svc._paging_request({}, {"page_size": 20}) is None


# ── SQL 通道：LIMIT/OFFSET + COUNT ─────────────────────────────────────────


class TestApplyServerPagination:
    def test_first_page_offsets_from_zero_and_still_counts(self, monkeypatch):
        """首页也要总数：分页条要算总页数，少了它就把首页读成全量。"""
        monkeypatch.setattr(
            gq, "governed_execute",
            lambda *a, **k: {"columns": ["cnt"], "rows": [{"cnt": 137}], "row_count": 1})
        sql, total, stable = ds_svc._apply_server_pagination(BASE_SQL, 20, 1, {"id": 1}, IDENT)
        assert sql.endswith("LIMIT 20 OFFSET 0")
        assert "LIMIT 200" not in sql                  # 外层编译行限被页大小替换
        assert "COUNT" not in sql                       # 分页语句自身不带聚合
        assert total == 137
        assert stable is False                          # 正文没有顶层 ORDER BY → 行序不确定

    def test_deeper_page_offsets_by_page_num(self, monkeypatch):
        seen = {}

        def fake_execute(sql, datasource_id, user_id, workspace_id, username):
            seen["sql"] = sql
            return {"columns": ["cnt"], "rows": [{"cnt": 137}], "row_count": 1}

        monkeypatch.setattr(gq, "governed_execute", fake_execute)
        sql, total, _stable = ds_svc._apply_server_pagination(BASE_SQL, 20, 3, DETAIL_DS, IDENT)
        assert sql.endswith("LIMIT 20 OFFSET 40")      # 偏移由 (page_num-1)*page_size 算出
        assert total == 137
        # 统计语句由服务端生成：剥掉尾部行限后包 COUNT，且经治理执行口（不旁路）
        assert seen["sql"].startswith("SELECT COUNT(*) AS cnt FROM (")
        assert "LIMIT 200" not in seen["sql"] and "LIMIT 20" not in seen["sql"]

    def test_count_failure_is_reported_not_silently_dropped(self, monkeypatch):
        """计数报错不得退成"没有总数"：前端会把缺 total 读成真实行数（错读比报错难查）。

        文案也不能断言单一成因（真实成因可能是源/表不可用），否则把系统侧故障误报成用户口径写错。
        """
        def boom(*a, **k):
            raise RuntimeError("syntax near 'WITH'")

        monkeypatch.setattr(gq, "governed_execute", boom)
        with pytest.raises(ValueError) as exc:
            ds_svc._apply_server_pagination(BASE_SQL, 20, 2, {"id": 1}, IDENT)
        msg = str(exc.value)
        assert "行数统计失败" in msg
        assert "可能" in msg and "不可用" in msg, msg      # 不单向归因
        assert "支持分页" in msg                          # 给出可行的下一步（统计型该关开关）
        assert "WITH" not in msg                          # 原始报错不外泄（护栏 §7）

    def test_no_identity_rejected_as_is(self, monkeypatch):
        from backend.core.governed_query import NoIdentityError

        def boom(*a, **k):
            raise NoIdentityError("缺少可信用户身份")

        monkeypatch.setattr(gq, "governed_execute", boom)
        with pytest.raises(NoIdentityError):
            ds_svc._apply_server_pagination(BASE_SQL, 20, 2, {"id": 1}, IDENT)


class TestTopLevelOrderBy:
    """行序确定性只看**最外层**的 ORDER BY：窗口函数/子查询里的排序不算。

    判错了就是拿“看着能翻、其实会重行”的表冒充真分页（活库实测：
    RUM 明细正文无 ORDER BY，第 2 页与第 3 页出现过同一批行）。
    """

    @pytest.mark.parametrize("sql, want", [
        ("SELECT a FROM t ORDER BY a DESC", True),
        ("SELECT a FROM t ORDER\n  BY a", True),
        ("SELECT a FROM t GROUP BY a", False),
        ("SELECT a FROM (SELECT a FROM t ORDER BY a LIMIT 10) AS x", False),
        ("SELECT ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t", False),
        # 字符串/注释里出现的 ORDER BY 不是排序子句
        ("SELECT 'ORDER BY x' AS a FROM t", False),
        ("SELECT a FROM t -- ORDER BY a\n", False),
        ("SELECT a FROM t /* ORDER BY a */", False),
        ("SELECT `order by` FROM t", False),
        ("SELECT a FROM t WHERE b = 'x' ORDER BY a", True),
    ])
    def test_detection(self, sql, want):
        assert ds_svc._has_top_level_order_by(sql) is want

    def test_unordered_body_is_reported_in_paging_block(self, monkeypatch):
        """正文有顶层排序 → paging.stable_order=true（前端不再弹“可能重行”提醒）。"""
        captured = {}
        ordered = BASE_SQL[: -len(" LIMIT 200")] + " ORDER BY dt DESC"
        _stub_sql_channel(monkeypatch, captured, base_sql=ordered)
        out = ds_svc._query_sql(DETAIL_DS, {"page_size": 20, "page_num": 2}, [], IDENT)
        assert out["paging"]["stable_order"] is True
        assert any(s.endswith("LIMIT 20 OFFSET 20") for s in captured["sqls"])
        assert not any("LIMIT 200" in s for s in captured["sqls"])   # 编译行限仍被页大小替换


def _stub_sql_channel(monkeypatch, captured, *, base_sql=None, row_count=1):
    class Compiled:
        sql = base_sql or BASE_SQL
        warnings = []
        header_map = {}
        resolved_tables = []

    def fake_execute(sql, datasource_id, user_id, workspace_id, username):
        captured.setdefault("sqls", []).append(sql)
        if "COUNT(*)" in sql:
            return {"columns": ["cnt"], "rows": [{"cnt": 42}], "row_count": 1}
        rows = [{"dt": f"10-{i:02d}", "sales": i} for i in range(row_count)]
        return {"columns": ["dt", "sales"], "rows": rows, "row_count": len(rows)}

    monkeypatch.setattr("backend.semantics.dataset_compiler.compile_dataset",
                        lambda *a, **k: Compiled())
    monkeypatch.setattr(ds_svc, "_dataset_db_type", lambda ds: "mysql")
    monkeypatch.setattr(gq, "governed_execute", fake_execute)


def test_query_sql_paginable_returns_paging_block(monkeypatch):
    captured = {}
    _stub_sql_channel(monkeypatch, captured)
    out = ds_svc._query_sql(DETAIL_DS, {"page_size": 20, "page_num": 3}, [], IDENT)
    assert out["total"] == 42
    assert any(s.endswith("LIMIT 20 OFFSET 40") for s in captured["sqls"])
    assert out["execution_mode"] == "sql"
    # 前端据此渲染页码：SQL 通道可随机跳页；stable_order=False 是本样例的真实形状
    # （正文没顶层 ORDER BY，相邻页会重行）—— 必须随 paging 一起到前端，不能默默翻
    assert out["paging"] == {"mode": "offset", "page_size": 20, "page_num": 3,
                             "total": 42, "can_jump": True, "stable_order": False}


def test_query_sql_over_large_page_size_is_clamped(monkeypatch):
    captured = {}
    _stub_sql_channel(monkeypatch, captured)
    out = ds_svc._query_sql(DETAIL_DS, {"page_size": 5000, "page_num": 1}, [], IDENT)
    assert out["paging"]["page_size"] == 100
    assert any(s.endswith("LIMIT 100 OFFSET 0") for s in captured["sqls"])


def test_query_sql_non_paginable_has_no_paging_block(monkeypatch):
    """统计型数据集：不套页限、不回 paging，整页结果原样返回。"""
    captured = {}
    _stub_sql_channel(monkeypatch, captured)
    ds = {**DETAIL_DS, "paginable": 0}
    out = ds_svc._query_sql(ds, {"page_size": 20, "page_num": 3}, [], IDENT)
    assert "paging" not in out and "total" not in out
    assert not any("OFFSET" in s.upper() for s in captured["sqls"])
    assert not any("COUNT" in s.upper() for s in captured["sqls"])
    # 2 行 < 正文 LIMIT 200 → 服务端必须说“本次结果就是全部”，
    # 否则前端无法判断缓存状态，只能每次进看板都补一次重查（存量图转圈的根因）
    assert out["unpaged"] is True and "truncated" not in out


def test_query_sql_non_paginable_at_body_limit_is_not_complete(monkeypatch):
    """行数正好撞到正文 LIMIT：不能宣称完整（unpaged），必须标 truncated。

    撞线时引擎只是按上限截了返回，把 200 行当全量就是拿部分冒充整体。
    """
    captured = {}
    _stub_sql_channel(monkeypatch, captured, row_count=200)
    out = ds_svc._query_sql({**DETAIL_DS, "paginable": 0}, {}, [], IDENT)
    assert out["truncated"] is True and "unpaged" not in out


@pytest.mark.parametrize("sql, want", [
    (BASE_SQL, 200),                                    # 只认最外层尾部那条 LIMIT
    ("SELECT a FROM t LIMIT 20 OFFSET 40", 20),
    ("SELECT a FROM t", ds_svc._MAX_LIMIT),             # 没有行限就按全局上限算
])
def test_sql_row_cap_reads_outermost_trailing_limit(sql, want):
    assert ds_svc._sql_row_cap(sql) == want


# ── ES 通道：translate → search_after 游标（只能顺序翻） ────────────────────

ES_DETAIL_SQL = ('SELECT "warningTime.keyword" AS warning_time, "level.keyword" AS level '
                 'FROM "logs-*" WHERE isRelease = 1 ORDER BY warning_time DESC LIMIT 500')


def _stub_es(monkeypatch, *, dsl=None, page=None, run=None, render=None):
    monkeypatch.setattr(ds_svc, "require_es_channel", lambda: None)
    monkeypatch.setattr(ds_svc, "_es_default_window", lambda ds, args: (args, {}))
    # 默认固定渲染结果；传 `render` 可模拟滚动窗逐次漂移（现网缺陷回归用）
    monkeypatch.setattr(ds_svc, "_render_es_body", render or (lambda ds, args: ES_DETAIL_SQL))
    monkeypatch.setattr(ds_svc, "_es_audit", lambda *a, **k: None)
    monkeypatch.setattr(es_client, "parse_select", lambda sql: (sql, 500))
    if dsl is not None:
        monkeypatch.setattr(es_client, "translate_select", lambda sql: dsl)
    if page is not None:
        monkeypatch.setattr(es_pagination, "query_page", page)
    if run is not None:
        monkeypatch.setattr(es_client, "run_sql", run)


ES_FLAT_DSL = {
    "query": {"term": {"isRelease": {"value": 1}}},
    "fields": [{"field": "warningTime.keyword"}, {"field": "level.keyword"}],
    "sort": [{"warningTime.keyword": {"order": "desc"}}],
    "size": 500, "track_total_hits": -1, "_source": False,
}


def test_es_dataset_pages_via_cursor_mode(monkeypatch):
    def fake_query_page(pattern, sql, *, page_size, page_num, context_key):
        return {"columns": ["warning_time", "level"],
                "rows": [["2026-11-11 10:00:00", "error"]],
                "row_count": 1, "total": 4468044,
                "page_size": page_size, "page_num": page_num, "paging_mode": "cursor",
                "sql": sql}

    _stub_es(monkeypatch, page=fake_query_page)
    out = ds_svc._query_es({"id": 33, "paginable": 1, "es_pattern": "logs-*"},
                           {"page_size": 20, "page_num": 2}, IDENT)
    assert out["total"] == 4468044
    assert out["columns"] == ["warning_time", "level"]
    # 集群无 PIT ⇒ search_after 只能顺序往后翻：前端据此只给上/下页按钮，不给页码
    assert out["paging"] == {"mode": "cursor", "page_size": 20, "page_num": 2,
                             "total": 4468044, "can_jump": False, "stable_order": True}


def test_es_paging_context_survives_default_window_drift(monkeypatch):
    """回归（活库实测踩过）：服务端按当前时刻补的滚动窗会让每页渲染出不同正文。

    旧实现拿**渲染后**的正文算游标签名 ⇒ 两次请求隔几秒签名就变了，
    第 2 页永远找不到第 1 页留下的游标，整张 ES 明细表只能停在首页。
    翻页上下文必须只认不随时刻变化的口径（正文模板 + 调用方给的参 + 页大小）。
    """
    keys, sqls = [], []
    tick = iter(range(100))

    def drifting_render(ds, args):
        return ES_DETAIL_SQL.replace("isRelease = 1", f"isRelease = {next(tick)}")

    def fake_query_page(pattern, sql, *, page_size, page_num, context_key):
        keys.append(context_key)
        sqls.append(sql)
        return {"columns": ["warning_time", "level"], "rows": [["t", "e"]], "row_count": 1,
                "total": 3, "page_size": page_size, "page_num": page_num,
                "paging_mode": "cursor", "sql": ES_DETAIL_SQL}

    _stub_es(monkeypatch, page=fake_query_page, render=drifting_render)
    ds = {"id": 33, "paginable": 1, "es_pattern": "logs-*", "sql_query": ES_DETAIL_SQL}
    for n in (1, 2, 3):
        ds_svc._query_es(ds, {"page_size": 20, "page_num": n}, IDENT)
    assert len(set(keys)) == 1        # 三页共用同一翻页上下文
    assert len(set(sqls)) == 3        # 渲染确实每次都不同（缺陷源头还在，只是不再影响口径）


def test_es_dataset_non_paginable_still_uses_run_sql(monkeypatch):
    seen = {}

    def fake_run_sql(sql, fetch_size=0):
        seen["fetch_size"] = fetch_size
        return {"columns": [{"name": "warning_time"}, {"name": "level"}],
                "rows": [["t", "e"]], "indexes": ["logs-2026.11"]}

    _stub_es(monkeypatch, run=fake_run_sql)
    out = ds_svc._query_es({"id": 33, "paginable": 0, "es_pattern": "logs-*"},
                           {"limit": 200}, IDENT)
    assert seen["fetch_size"] == 200
    assert "paging" not in out
    assert out["execution_mode"] == "es"
    assert out["unpaged"] is True and "truncated" not in out   # 1 行 < 200 → 完整


def test_es_dataset_marks_truncated_at_row_ceiling(monkeypatch):
    """不分页且行数撞到上限：必须标 truncated，前端要提醒"可能还有更多"。

    拿"共 N 行"挂在 446 万行的明细表上就是本次要根治的伪分页缺陷形态。
    """
    def fake_run_sql(sql, fetch_size=0):
        return {"columns": [{"name": "a"}], "rows": [[i] for i in range(fetch_size)],
                "indexes": []}

    _stub_es(monkeypatch, run=fake_run_sql)
    out = ds_svc._query_es({"id": 33, "paginable": 0, "es_pattern": "logs-*"},
                           {"limit": 1000}, IDENT)
    assert out["truncated"] is True and out["row_count"] == 1000
    assert "unpaged" not in out        # 撞线时两条事实互斥，不得既说完整又说可能还有更多


def test_es_dataset_paginable_but_unpageable_shape_fails_loud(monkeypatch):
    """标了支持分页、正文却是聚合统计：显式拒绝并指路，不回"空结果"充数。"""
    def boom(*a, **k):
        raise es_pagination.EsPagingUnsupported("该 ES 正文是聚合统计结果")

    _stub_es(monkeypatch, dsl=ES_FLAT_DSL, page=boom)
    with pytest.raises(ValueError) as exc:
        ds_svc._query_es({"id": 33, "paginable": 1, "es_pattern": "logs-*"},
                         {"page_size": 20}, IDENT)
    assert "聚合统计" in str(exc.value)


def test_es_dataset_rejects_runtime_filters_even_when_paginated(monkeypatch):
    _stub_es(monkeypatch, dsl=ES_FLAT_DSL)
    with pytest.raises(ValueError) as exc:
        ds_svc._query_es({"id": 33, "paginable": 1, "es_pattern": "logs-*"},
                         {"page_size": 20, "filters": [{"field": "a", "value": 1}]}, IDENT)
    assert "不支持运行时筛选器" in str(exc.value)


def test_es_dataset_requires_identity_for_paging(monkeypatch):
    _stub_es(monkeypatch, dsl=ES_FLAT_DSL)
    from backend.core.governed_query import NoIdentityError
    with pytest.raises(NoIdentityError):
        ds_svc._query_es({"id": 33, "paginable": 1, "es_pattern": "logs-*"},
                         {"page_size": 20}, {"user_id": 0})


# ── ES 游标分页本体（形状门 + 顺序游标） ────────────────────────────────────


class TestEsSelectItems:
    @pytest.mark.parametrize("sql, expected", [
        ('SELECT "a.keyword" AS x, b FROM "t" LIMIT 5', [("a.keyword", "x"), ("b", "b")]),
        ('SELECT [a.b] AS x FROM "t" LIMIT 5', [("a.b", "x")]),
        ('SELECT "a" x FROM "t" LIMIT 5', [("a", "x")]),
    ])
    def test_plain_field_projections(self, sql, expected):
        assert es_client.select_items(sql) == expected

    @pytest.mark.parametrize("sql", [
        'SELECT COUNT(*) AS c FROM "t" LIMIT 5',
        'SELECT DISTINCT "a" FROM "t" LIMIT 5',
        'SELECT * FROM "t" LIMIT 5',
        'SELECT CONCAT("a", ",", "b") AS x FROM "t" LIMIT 5',
    ])
    def test_non_field_projections_rejected(self, sql):
        """表达式/聚合/星号投影不能映射回列名 —— 宁拒不错（列名与值错行=错数）。"""
        with pytest.raises(es_client.EsError):
            es_client.select_items(sql)


class TestEsShapeGate:
    @pytest.mark.parametrize("dsl, needle", [
        ({**ES_FLAT_DSL, "aggregations": {"x": {}}}, "聚合统计"),
        ({**ES_FLAT_DSL, "fields": []}, "没有可映射的字段投影"),
        ({**ES_FLAT_DSL, "fields": [{"script": {"lang": "painless"}}]}, "表达式"),
        ({"query": {}, "fields": [{"field": "other"}], "sort": []}, "对不上"),
    ])
    def test_unsupported_shapes_rejected_with_actionable_text(self, dsl, needle):
        items = es_client.select_items(ES_DETAIL_SQL)
        with pytest.raises(es_pagination.EsPagingUnsupported) as exc:
            es_pagination._validate_shape(dsl, items)
        assert needle in str(exc.value)

    def test_flat_detail_passes(self):
        es_pagination._validate_shape(ES_FLAT_DSL, es_client.select_items(ES_DETAIL_SQL))


class TestEsCursorOrdering:
    """search_after 只能顺序往后翻：跳页必须显式拒绝，不"从头快进"冒充跳页。"""

    def setup_method(self):
        es_pagination.reset_cursors()

    @staticmethod
    def _key(page_size=2, args=None, body=ES_DETAIL_SQL):
        return es_pagination.paging_key("logs-*", body, args or {}, page_size)

    def _fake_search(self, captured):
        def fake(pattern, dsl, *, size, search_after=None):
            captured.setdefault("cursors", []).append(search_after)
            rows = [{"warningTime.keyword": f"t{i}", "level.keyword": "e"} for i in range(size)]
            return {"rows": rows, "total": 100, "row_count": len(rows),
                    "last_sort": [f"t{size - 1}", size - 1], "has_more": True}
        return fake

    def test_page_one_needs_no_cursor_then_next_page_reuses_it(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        key = self._key()
        first = es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=1,
                                        context_key=key)
        assert seen["cursors"] == [None]
        assert first["columns"] == ["warning_time", "level"]      # 业务别名，不是 ES 字段路径
        assert first["rows"][0] == ["t0", "e"]                    # 与 run_sql 同形（按位值数组）
        assert first["paging_mode"] == "cursor"
        second = es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=2,
                                         context_key=key)
        assert seen["cursors"][1] is not None                     # 第 2 页带上了第 1 页的尾游标
        assert second["page_num"] == 2

    def test_window_drift_reuses_first_page_body(self, monkeypatch):
        """未传时间参数时服务端按“当前时刻”补滚动窗，逐页渲染出的正文会漂。

        翻页上下文必须按**不漂的签名**走，且后续页得复用首页确立的那份正文：
        否则每页查的不是同一个集合（新数据插进排序头部 = 漏行/重行）。
        现网实测踩过：第 2 页永远报“最多可翻到第 1 页”。
        """
        seen = {}

        def fake_translate(sql):
            seen.setdefault("translated", []).append(sql)
            return ES_FLAT_DSL

        drifted = ES_DETAIL_SQL.replace("isRelease = 1", "isRelease = 1 AND warning_time <= '10:00:03'")
        monkeypatch.setattr(es_client, "translate_select", fake_translate)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        key = self._key()
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=1, context_key=key)
        out = es_pagination.query_page("logs-*", drifted, page_size=2, page_num=2, context_key=key)
        assert seen["translated"] == [ES_DETAIL_SQL, ES_DETAIL_SQL]   # 第二页仍按首页口径检索
        assert out["sql"] == ES_DETAIL_SQL                            # 回显实际用的正文

    def test_jump_over_uncached_page_is_rejected(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        key = self._key()
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=1, context_key=key)
        with pytest.raises(es_pagination.EsPagingUnsupported) as exc:
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=7, context_key=key)
        # 游标只到第 2 页：告知可达边界而不是默默快进（那会把一次点击变成几十次全索引检索）
        assert "顺序翻页" in str(exc.value) and "第 2 页" in str(exc.value)

    def test_back_to_visited_page_works(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        key = self._key()
        for n in (1, 2, 3):
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=n, context_key=key)
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=2, context_key=key)
        assert len(seen["cursors"]) == 4          # 回翻不新增快进（游标已在缓存）

    def test_back_to_page_one_reopens_the_run(self, monkeypatch):
        """回第 1 页 = 重新起翻：陈旧的后页游标一律作废（不能拿着旧窗口继续往后翻）。"""
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        key = self._key()
        for n in (1, 2, 3):
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=n, context_key=key)
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=1, context_key=key)
        with pytest.raises(es_pagination.EsPagingUnsupported):
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=3, context_key=key)

    def test_expired_context_asks_for_page_one(self, monkeypatch):
        """没取过首页就要第 2 页（服务重启/上下文过期）：明说“先回第 1 页”，不回空表。"""
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        with pytest.raises(es_pagination.EsPagingUnsupported) as exc:
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=2,
                                    context_key=self._key())
        assert "第 1 页" in str(exc.value)

    def test_page_size_change_switches_cursor_namespace(self, monkeypatch):
        """换页大小等于换查询：旧游标不能复用，否则会拿到错位的一页。"""
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=20, page_num=1,
                                context_key=self._key(page_size=20))
        with pytest.raises(es_pagination.EsPagingUnsupported):
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=50, page_num=3,
                                    context_key=self._key(page_size=50))

    def test_filter_change_switches_cursor_namespace(self, monkeypatch):
        """调用方换了筛选参（args）= 换口径：不能拿旧集合的游标继续翻。"""
        seen = {}
        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", self._fake_search(seen))
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=1,
                                context_key=self._key(args={"site": "SH"}))
        with pytest.raises(es_pagination.EsPagingUnsupported):
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=2, page_num=2,
                                    context_key=self._key(args={"site": "BJ"}))

    def test_paging_key_ignores_values_that_drift_with_time(self):
        """签名只认正文模板与调用方给的参：服务端补的窗口不得参与（否则逐页必失效）。"""
        assert es_pagination.paging_key("logs-*", ES_DETAIL_SQL, {}, 2) == \
            es_pagination.paging_key("logs-*", ES_DETAIL_SQL, {}, 2)
        assert es_pagination.paging_key("logs-*", ES_DETAIL_SQL, {}, 2) != \
            es_pagination.paging_key("logs-*", ES_DETAIL_SQL, {"a": 1}, 2)
        # 参的顺序不影响签名
        assert es_pagination.paging_key("logs-*", ES_DETAIL_SQL, {"a": 1, "b": 2}, 2) == \
            es_pagination.paging_key("logs-*", ES_DETAIL_SQL, {"b": 2, "a": 1}, 2)

    def test_last_sort_dropped_when_page_not_full(self, monkeypatch):
        """本页没取满 = 已到末尾：不能留"下一页游标"，否则回翻后再点下一页会翻出空页。"""
        def short(pattern, dsl, *, size, search_after=None):
            return {"rows": [{"warningTime.keyword": "t", "level.keyword": "e"}],
                    "total": 21, "row_count": 1, "last_sort": ["t", 0], "has_more": False}

        monkeypatch.setattr(es_client, "translate_select", lambda sql: ES_FLAT_DSL)
        monkeypatch.setattr(es_client, "search_page", short)
        key = self._key(page_size=20)
        es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=20, page_num=1, context_key=key)
        with pytest.raises(es_pagination.EsPagingUnsupported):
            es_pagination.query_page("logs-*", ES_DETAIL_SQL, page_size=20, page_num=2, context_key=key)


# ── 语义通道：没有 OFFSET/COUNT 形状，显式拒绝 ──────────────────────────────


def test_semantic_dataset_rejects_paging():
    with pytest.raises(ValueError) as exc:
        ds_svc._query_semantic({"id": 1, "object_key": "order", "paginable": 1},
                               {"page_size": 20, "page_num": 2}, [], IDENT)
    assert "不支持按页取数" in str(exc.value) and "支持分页" in str(exc.value)


def test_semantic_dataset_without_paginable_is_untouched(monkeypatch):
    """未标支持分页时不得因分页参数报错（统计型语义数据集照旧整页返回）。

    门控只拦“数据集说了能分页、通道却翻不了”这一种错配；正常语义链该原样走下去。
    拿 sentinel 打断在 parse_intent，不碰真库也不拿下游行为当断言对象。
    """
    reached = {}

    def sentinel(*a, **k):
        reached["hit"] = True
        raise RuntimeError("downstream-of-the-guard")

    monkeypatch.setattr("backend.semantics.intent.parse_intent", sentinel)
    with pytest.raises(RuntimeError):
        ds_svc._query_semantic({"id": 1, "object_key": "order"},
                               {"page_size": 20}, [], IDENT)
    assert reached.get("hit") is True          # 分页门没拦，确实走到了语义链


# ── 旁路护栏：不接受调用方递交的统计 SQL / offset ────────────────────────────


def test_query_dataset_ignores_client_supplied_count_sql():
    """`count_sql` 不再出现在数据集入口的参数口径里（旧前端字段已被 API 层移除）。"""
    import inspect

    assert "count_sql" not in inspect.signature(ds_svc.query_dataset).parameters
    src = inspect.getsource(ds_svc._query_sql)
    assert 'params.get("count_sql")' not in src
    assert "count_sql=" not in src        # 不由外部供给，只由服务端编译产物生成


def test_paging_contract_has_no_offset_entry_point():
    """入口只有 page_size/page_num 两个业务参数；offset 不得成为对外契约的一部分。"""
    import inspect

    from backend.modules.viz.api import chart as chart_api
    from backend.modules.viz.api import dashboard as dash_api

    assert set(chart_api.ChartRefreshRequest.model_fields) == {"params", "page_size", "page_num"}
    assert set(dash_api.ChartRefreshRequest.model_fields) == {"params", "page_size", "page_num"}
    assert "page_offset" not in inspect.getsource(ds_svc._paging_request)
    assert "page_offset" not in inspect.getsource(ds_svc._query_sql)


def test_row_ceiling_is_one_thousand():
    """不分页时单次返回行数上限 1000（2026-11-11 拍板，由 2000 收紧）。"""
    assert ds_svc._MAX_LIMIT == 1000
    assert es_pagination.PAGE_SIZE_MAX == 100
    assert es_pagination.PAGE_SIZE_DEFAULT == 20
