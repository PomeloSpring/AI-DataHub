"""看板参数 ↔ 数据集入参 的接线契约（2026-11-06 归一批次）。

锁五件事：
1. **翻译只在唯一一处**：`_dataset_bound_args` → `semantics.param_plan.bound_args`，
   服务层不得再留第二套"同名才透传"的实现。
2. **两条绑定通道**：逻辑参数键（新装配的控件）与同名变量键（存量看板写法）都要生效——
   砍掉后者会把"今天能生效"变成"静默不生效"，那是制造新缺陷。
3. **没给值 / 只给半截要看得见**：`unbound_params`（业务名 + partial）随刷新结果出去。
4. **清单接口给得出摘要、给不出正文**：`param_summary` 是编辑器装配的数据来源，
   而 `variables/sql_query/sql_bodies` 属护栏 §7，算完必须就地剥掉，且清单往返不随行数增长。
5. 图表取数不再把看板业务参数当**选源路由**（`site` 双消费收口）。
"""

import importlib
import inspect
from contextlib import ExitStack
import json
import unittest
from unittest import mock

from backend.semantics import param_plan

dash = importlib.import_module("backend.modules.viz.services.dashboard_service")
ds_svc = importlib.import_module("backend.modules.viz.services.dataset_service")

# 数据集把窗口写成 start_time/end_time + days 回退，站点写成 site（现网最常见的形状）
_VARS = json.dumps({
    "start_time": {"type": "string", "default": ""},
    "end_time": {"type": "string", "default": ""},
    "days": {"type": "int", "default": 30},
    "site": {"type": "string", "default": ""},
})
_SQL = ("SELECT lvl, COUNT(*) AS qty FROM t WHERE isRelease = 1 "
        "AND warning_time >= COALESCE(NULLIF(${start_time}, ''), "
        "DATE_SUB(NOW(), INTERVAL ${days} DAY)) "
        "AND warning_time <= COALESCE(NULLIF(${end_time}, ''), NOW()) "
        "AND (${site} = '' OR site = ${site}) GROUP BY lvl")


def _dataset_row(**over) -> dict:
    row = {"id": 901, "name": "告警按级别汇总", "status": "active", "source_type": "sql",
           "variables": _VARS, "sql_query": _SQL, "sql_bodies": None, "datasource_id": 0,
           "available_datasources": None, "field_config": None, "object_key": "",
           "es_pattern": "", "description": "", "chart_type": "", "chart_preset": None,
           "origin": "manual", "owner_id": 1, "created_at": "", "updated_at": ""}
    row.update(over)
    return row


def _bound(values: dict) -> tuple[dict, dict]:
    with mock.patch.object(ds_svc, "get_dataset", return_value=_dataset_row()):
        return dash._dataset_bound_args(901, values)


class TestBindingChannels(unittest.TestCase):
    def test_logical_key_binds_through_binder(self):
        """新装配的控件写逻辑键 `time_window`：区间拆成两个端点变量。"""
        args, diag = _bound({"time_window": {"start": "2026-10-01", "end": "2026-10-06"}})
        self.assertEqual(args["start_time"], "2026-10-01")
        self.assertEqual(args["end_time"], "2026-10-06")
        # 端点齐了也要把相对天数一起派生：多方言正文可能只用其中一种形状，
        # 两者不同口径就会在不同引擎上跑出不同的窗口
        self.assertEqual(args["days"], 6)
        self.assertEqual([u["key"] for u in diag["unbound"]], ["site"])

    def test_legacy_variable_named_params_still_bind(self):
        """存量看板控件写的是变量名（start_time/end_time），必须照常生效，不得静默降级。"""
        args, diag = _bound({"start_time": "2026-10-01", "end_time": "2026-10-31"})
        self.assertEqual(args["start_time"], "2026-10-01")
        self.assertEqual(args["end_time"], "2026-10-31")
        self.assertEqual([u["key"] for u in diag["unbound"]], ["site"])

    def test_other_datasets_spelling_is_not_silently_accepted(self):
        """别的数据集的叫法（date_start/date_end）在这里不生效——但必须**报出来**，
        不能像旧实现那样静默丢掉后按默认窗口出数。"""
        args, diag = _bound({"date_start": "2026-10-01", "date_end": "2026-10-31"})
        self.assertEqual(args, {})
        tw = next(u for u in diag["unbound"] if u["key"] == param_plan.TIME_WINDOW_KEY)
        self.assertFalse(tw["partial"])
        self.assertEqual(diag["skipped"], 2)

    def test_half_window_is_reported_as_partial(self):
        """只给一端：另一端按数据集默认值凑，口径不完整，必须显式可见。"""
        args, diag = _bound({"start_time": "2026-10-01"})
        self.assertEqual(args, {"start_time": "2026-10-01"})
        tw = next(u for u in diag["unbound"] if u["key"] == param_plan.TIME_WINDOW_KEY)
        self.assertTrue(tw["partial"])
        self.assertEqual(tw["vars"], ["end_time"])    # days 只是回退写法，不算口径缺口

    def test_relative_only_window_backfills_endpoints(self):
        args, _ = _bound({"time_window": {"days": 14}})
        self.assertTrue(args["start_time"] and args["end_time"])
        self.assertEqual(args["days"], 14)

    def test_business_site_binds_and_is_not_routing(self):
        """`site` 是业务筛选变量：值进 args，不再被当成"路由到哪个站点的数据源"。"""
        args, diag = _bound({"time_window": {"days": 7}, "site": "cn"})
        self.assertEqual(args["site"], "cn")
        self.assertEqual(diag["unbound"], [])

    def test_context_keys_are_never_business_params(self):
        _args, diag = _bound({"limit": 500, "filters": [], "page_limit": 50,
                              "page_offset": 0, "dimensions": [], "measures": []})
        self.assertEqual({u["key"] for u in diag["unbound"]},
                         {param_plan.TIME_WINDOW_KEY, "site"})

    def test_missing_dataset_returns_empty_binding_without_crash(self):
        with mock.patch.object(ds_svc, "get_dataset", return_value=None):
            args, diag = dash._dataset_bound_args(999, {"time_window": {"days": 7}})
        self.assertEqual((args, diag["unbound"], diag["skipped"]), ({}, [], 0))

    def test_bad_window_value_fails_loud(self):
        with self.assertRaises(ValueError) as ctx:
            _bound({"time_window": {"start": "上周", "end": "2026-10-31"}})
        self.assertIn("不是可识别的日期", str(ctx.exception))


class TestListParamSummary(unittest.TestCase):
    """清单行要带装配用的摘要，且不带走一句正文（护栏 §7）。"""

    def test_summary_present_sql_stripped_single_roundtrip(self):
        calls: list[str] = []
        identity = {"user_id": 1, "role": "admin", "workspace_id": 1}
        catalog = {"prd-cn": {"id": 11, "name": "prd-cn", "db_type": "mysql", "site_code": "cn"}}

        def fake_execute(sql, params=None, **kw):
            calls.append(sql)
            return [_dataset_row()]

        usable = ([{"id": 11, "name": "prd-cn"}], [])
        with ExitStack() as stack:
            for ctx in (
                mock.patch.object(ds_svc, "execute_query", side_effect=fake_execute),
                mock.patch.object(ds_svc, "_load_datasource_catalog", return_value=catalog),
                mock.patch.object(ds_svc, "_allowed_source_ids", return_value={11}),
                mock.patch.object(ds_svc, "usable_scope", return_value=usable),
                mock.patch.object(ds_svc, "usable_sources", return_value=usable),
                mock.patch.object(ds_svc, "_datasource_name_by_id", return_value="prd-cn"),
                mock.patch.object(ds_svc, "resolve_fields", return_value=[]),
            ):
                stack.enter_context(ctx)
            out = ds_svc.list_datasets(identity)

        self.assertEqual(len(out), 1)
        row = out[0]
        keys = {s["key"] for s in row["param_summary"]}
        self.assertEqual(keys, {param_plan.TIME_WINDOW_KEY, "site"})
        tw = next(s for s in row["param_summary"] if s["key"] == param_plan.TIME_WINDOW_KEY)
        self.assertEqual(tw["control"], "widget_daterange")
        self.assertEqual(tw["fallback_days"], 30)        # 控件没给值时它会退成"近 30 天"
        for leak in ("sql_query", "sql_bodies", "variables"):
            self.assertNotIn(leak, row)
        self.assertNotIn("SELECT", json.dumps(row["param_summary"], ensure_ascii=False))
        self.assertEqual(len(calls), 1, f"清单往返必须恒定：{calls}")


class TestMoatStillHolds(unittest.TestCase):
    """收口之后不留第二套实现，也不得把路由权交给控件值。"""

    def test_old_same_name_helper_is_retired(self):
        self.assertFalse(hasattr(dash, "_dataset_declared_args"))
        self.assertTrue(hasattr(dash, "_dataset_bound_args"))

    def test_refresh_does_not_read_routing_from_business_params(self):
        src = inspect.getsource(dash.ChartService._refresh_dataset_chart)
        # 站点路由永远不得从看板业务参数取（控件值换执行库 = 静默改口径）
        self.assertNotIn('ds_params["site"]', src)
        self.assertNotIn('params.get("site")', src)
        # 数据源路由（2026-11-12 看板级选源）只可来自图表设计期钉选 config.datasource（业务名），
        # 绝不可来自业务 params（与控件值正交，不开"控件下拉静默换执行库"的后门）
        self.assertIn('cfg.get("datasource")', src)
        self.assertNotIn('params.get("datasource")', src)
        self.assertIn("_dataset_bound_args", src)
        # 翻译只在一处：服务层不得自己再算一遍形状
        self.assertNotIn("param_plan", src)

    def test_unbound_diagnostic_reaches_the_chart_response(self):
        src = inspect.getsource(dash.ChartService._refresh_dataset_chart)
        self.assertIn("unbound_params", src)
        self.assertIn("param_notes", src)
        self.assertIn("default_window", src)      # 服务端补的默认窗也不能只停在取数层


class TestServerSideDefaultWindowIsVisible(unittest.TestCase):
    """时间参数留空时服务端会补滚动窗（ES SQL 拿不到本地当前时刻）。

    补默认值本身是需求要求的行为，但“按近 30 天出数”必须让查看者看见：
    否则用户以为“没筛 = 全量”，实际拿的是默认窗口（no-silent-degradation）。
    """

    class _Cur:
        def execute(self, *a, **k):
            return None

    def _refresh(self, result, notes=None):
        chart = {"id": 7, "name": "异常上报·CASE 明细", "source_type": "dataset",
                 "source_id": 33, "config": "{}", "data_cache": None}
        diag = {"unbound": [], "notes": notes or [], "partial": [], "skipped": 0}
        with mock.patch.object(dash, "_dataset_bound_args", return_value=({}, diag)), \
             mock.patch.object(ds_svc, "query_dataset", return_value=result), \
             mock.patch("backend.common.auth.get_user_by_id", return_value={"user_role": "admin"}):
            return dash.ChartService()._refresh_dataset_chart(
                chart, {}, 1, 0, "admin", self._Cur())

    def test_default_window_is_echoed_and_explained(self):
        out = self._refresh({
            "columns": ["warning_time"], "rows": [{"warning_time": "2026-11-10 15:30:54"}],
            "row_count": 1, "truncated": False, "total": 1,
            "default_window": {"start_time": "2026-10-11 15:31:17", "end_time": "2026-11-10 15:31:17",
                               "days": 30, "reason": "未传时间参数，服务端按数据集默认窗口补齐"},
        })
        self.assertEqual(out["default_window"]["days"], 30)
        note = "\n".join(out.get("param_notes") or [])
        self.assertIn("近 30 天", note)
        self.assertIn("2026-10-11 15:31:17", note)
        # 窗口值属业务时间范围，不得把 SQL/物理细节带进响应（护栏 §7）
        for leak in ("sql", "base_sql", "secured_sql", "datasource_id", "catalog_ref", "physical_table"):
            self.assertNotIn(leak, out)

    def test_no_default_window_adds_no_note(self):
        out = self._refresh({"columns": [], "rows": [], "row_count": 0, "truncated": False, "total": 0})
        self.assertNotIn("default_window", out)
        self.assertNotIn("param_notes", out)


class TestChartCacheKeepsFirstPageOnly(unittest.TestCase):
    """`data_cache` 只存默认首页：大页/全量刷新不得把首屏预览写成第二份取数结果。

    现网实测缺陷（2026-11-10）：「操作记录流水明细」200 行宽文本把 TEXT 列（65,535
    字节）顶到 65,506 字节，下一次刷新报 MySQL 1406 Data too long —— 取数已经
    成功、整张图却显“刷新失败”，而且能不能刷出来取决于本批文本长度（随机故障）。
    """

    class _Cur:
        def __init__(self):
            self.writes = []

        def execute(self, sql, params=None):
            if "data_cache" in sql:
                self.writes.append(params[0])

    def _refresh(self, chart, result):
        cur = self._Cur()
        diag = {"unbound": [], "notes": [], "partial": [], "skipped": 0}
        with mock.patch.object(dash, "_dataset_bound_args", return_value=({}, diag)), \
             mock.patch.object(ds_svc, "query_dataset", return_value=result), \
             mock.patch("backend.common.auth.get_user_by_id", return_value={"user_role": "admin"}):
            out = dash.ChartService()._refresh_dataset_chart(chart, {}, 1, 0, "admin", cur)
        return out, cur

    @staticmethod
    def _table_chart(**cfg_over):
        # 表值图现在只有一个分页旋钮 pageSize（enableServerPagination/pageLimit/maxRows 已下线）
        cfg = {"pageSize": 20}
        cfg.update(cfg_over)
        return {"id": 7, "name": "操作记录流水明细", "chart_type": "table_value",
                "source_type": "dataset", "source_id": 38, "config": json.dumps(cfg),
                "data_cache": None}

    def test_big_page_response_full_cache_first_page(self):
        rows = [{"n": i} for i in range(500)]
        out, cur = self._refresh(self._table_chart(),
                                 {"columns": ["n"], "rows": rows, "row_count": 500, "total": 5000})
        self.assertEqual(len(out["rows"]), 500)          # 本次响应按用户要的大页给足
        cached = json.loads(cur.writes[0])
        self.assertEqual(len(cached["rows"]), 20)         # 缓存只留默认首页 pageSize
        self.assertEqual(cached["columns"], ["n"])
        self.assertEqual(cached["rows"][0]["n"], 0)       # 留的是首页那几行，不是尾部
        # 分页事实随首屏缓存落库：少了它首屏会把首页当成全量（旧伪分页的错读形态）
        self.assertEqual(cached["total"], 5000)

    def test_configured_pageSize_is_the_cap(self):
        rows = [{"n": i} for i in range(200)]
        _out, cur = self._refresh(self._table_chart(pageSize=50),
                                  {"columns": ["n"], "rows": rows, "row_count": 200})
        self.assertEqual(len(json.loads(cur.writes[0])["rows"]), 50)

    def test_oversized_pageSize_cap_is_clamped_to_100(self):
        """旧配置残留的 500 行大页不能把缓存又顶回 1406（与取数层同一硬上限）。"""
        rows = [{"n": i} for i in range(500)]
        _out, cur = self._refresh(self._table_chart(pageSize=500),
                                  {"columns": ["n"], "rows": rows, "row_count": 500})
        self.assertEqual(len(json.loads(cur.writes[0])["rows"]), 100)

    def test_paging_block_is_carried_into_cache(self):
        """服务端回的分页块要随首屏缓存一起写：渲染面据此判断能不能跳页。"""
        paging = {"mode": "cursor", "page_size": 20, "page_num": 1,
                  "total": 4468044, "can_jump": False}
        _out, cur = self._refresh(self._table_chart(),
                                  {"columns": ["n"], "rows": [{"n": 1}], "row_count": 1,
                                   "total": 4468044, "paging": paging})
        self.assertEqual(json.loads(cur.writes[0])["paging"], paging)

    def test_non_table_chart_keeps_all_rows(self):
        # 曲线/饼图是聚合行，首屏要的是整条形状，不能跟着表值图一起被削成 20 行
        chart = dict(self._table_chart(), chart_type="line", name="近30天趋势")
        rows = [{"n": i} for i in range(200)]
        _out, cur = self._refresh(chart, {"columns": ["n"], "rows": rows, "row_count": 200})
        self.assertEqual(len(json.loads(cur.writes[0])["rows"]), 200)

    def test_wide_text_rows_stay_inside_column_budget(self):
        rows = [{"detail": "操" * 8000} for _ in range(20)]   # 每行 ~24KB UTF-8
        chart = self._table_chart()
        _out, cur = self._refresh(chart, {"columns": ["detail"], "rows": rows, "row_count": 20})
        text = cur.writes[0]
        self.assertLessEqual(len(text.encode("utf-8")), dash.DATA_CACHE_MAX_BYTES)
        self.assertGreaterEqual(len(json.loads(text)["rows"]), 1)   # 退行而不是写半行

    def test_single_oversize_row_fails_loud(self):
        rows = [{"detail": "x" * 200000}]
        chart = self._table_chart()
        with self.assertRaises(ValueError) as ctx:
            self._refresh(chart, {"columns": ["detail"], "rows": rows, "row_count": 1})
        msg = str(ctx.exception)
        self.assertIn("操作记录流水明细", msg)          # 指名是哪张图，不报裸 1406
        self.assertIn("列", msg)

    def test_both_write_channels_share_one_implementation(self):
        # 数据集与声明式语义两条通道都只能经 `_chart_cache_json`，不得再留第二套行序/字节算法
        for fn in (dash._cache_chart_result, dash.ChartService._refresh_dataset_chart):
            self.assertIn("_chart_cache_json", inspect.getsource(fn))


if __name__ == "__main__":
    unittest.main()
