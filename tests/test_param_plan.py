"""`backend/semantics/param_plan.py` 的契约测试。

要锁死两件事：
1. **归一判据是通用的**：只看正文的谓词形状与类型证据，不看字段名。
   所以有一条源码级护栏：代码里不得出现业务参数名清单（改一条用例就说明又写了白名单）。
2. **判不出就不猜**：形状/值类型不确定 → pending，不自动生成控件；值与形状不匹配 → 显式报错，
   不得回退到数据集默认值出一个"看着正常其实没筛"的数（no-silent-degradation）。
"""

import ast
import io
import os
import unittest

from backend.semantics import param_plan as pp

MODULE_SRC = os.path.join(os.path.dirname(__file__), "..",
                          "backend", "semantics", "param_plan.py")


def _row(variables, sql):
    """最小数据集行：推断器只读 variables / sql_query / sql_bodies。"""
    return {"variables": variables, "sql_query": sql, "sql_bodies": None}


# 现网三种写法的样本（正文形状才是判据，名字随便换）
_WINDOW_PAIR = ("WHERE is_release = 1 AND event_time >= COALESCE(NULLIF(${start_time}, ''), "
                "DATE_SUB(NOW(), INTERVAL ${days} DAY)) AND event_time <= COALESCE(NULLIF(${end_time}, ''), NOW()) "
                "AND (${site} = '' OR site = ${site})")
_WINDOW_VARS = ('{"start_time": {"type": "string", "default": ""},'
                ' "end_time": {"type": "string", "default": ""},'
                ' "days": {"type": "int", "default": 30},'
                ' "site": {"type": "string", "default": ""}}')


def _plan_of(vars_, sql):
    row = _row(vars_, sql)
    return pp.logical_plan(row["variables"], pp._bodies_of(row))


def _group(plan, key):
    return next(g for g in plan if g["key"] == key)


class TestNoFieldNameWhitelist(unittest.TestCase):
    """归一规则不得长成"字段名清单"——这是"以后还有别的字段"能不能自动的前提。"""

    def test_code_contains_no_business_param_names(self):
        tree = ast.parse(io.open(MODULE_SRC, encoding="utf-8").read())
        for node in ast.walk(tree):       # 剥掉 docstring：注释/文档里举例说明判据不算白名单
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
        code = ast.unparse(tree)          # unparse 同时丢掉所有注释
        for banned in ("start_time", "end_time", "date_start", "date_end",
                       "start_date", "end_date", "region", "appmanager", "serial_number"):
            self.assertNotIn(banned, code,
                             f"param_plan 里出现了业务参数名 {banned}：说明归一又退回白名单写法")

    def test_renamed_time_columns_still_normalize(self):
        """同一形状换个名字（甚至换成中文名列）仍应识别为时间窗口——证明判据是形状。"""
        sql = ("WHERE begin_ts >= COALESCE(NULLIF(${from_stamp}, ''), DATE_SUB(NOW(), "
               "INTERVAL ${lookback} DAY)) AND end_ts <= COALESCE(NULLIF(${to_stamp}, ''), NOW())")
        vars_ = ('{"from_stamp": {"type": "string", "default": ""},'
                 ' "to_stamp": {"type": "string", "default": ""},'
                 ' "lookback": {"type": "int", "default": 14}}')
        plan = _plan_of(vars_, sql)
        g = _group(plan, pp.TIME_WINDOW_KEY)
        self.assertEqual(g["kind"], pp.KIND_TIME_WINDOW)
        self.assertEqual(g["value_type"], "date_range")
        self.assertEqual(g["control"], "widget_daterange")
        self.assertEqual({t["var"] for t in g["targets"]}, {"from_stamp", "to_stamp", "lookback"})

    def test_new_scalar_field_gets_a_control_without_code_change(self):
        """新业务字段（"术式"）自动落单值筛选，不需要往判定表里加名字。"""
        plan = _plan_of('{"procedure_type": {"type": "string", "default": ""}}',
                        "WHERE (${procedure_type} = '' OR procedure_type = ${procedure_type})")
        g = _group(plan, "procedure_type")
        self.assertEqual((g["kind"], g["value_type"], g["control"]),
                         ("scalar", "string", "widget_text"))


class TestRoleShapes(unittest.TestCase):
    def test_predicate_split_ignores_nested_and(self):
        sql = ("WHERE a >= COALESCE(NULLIF(${s}, ''), DATE_SUB(NOW(), INTERVAL ${d} DAY)) "
               "AND (x = ${v} OR y = ${w})")
        preds = pp._split_predicates(sql)
        self.assertEqual(len(preds), 2)

    def test_comparison_across_and_is_not_an_endpoint(self):
        """括号内的复合谓词不能让 `<=` 隔空命中后面的无关参数（实测误判过 appmanager）。"""
        sql = ("WHERE t <= COALESCE(NULLIF(${end_time}, ''), NOW()) "
               "AND (${appmanager} = '' OR appmanager = ${appmanager})")
        self.assertEqual(pp._role_in_predicate(sql, "end_time"), "range_end")
        self.assertEqual(pp._role_in_predicate(sql, "appmanager"), "optional_eq")

    def test_interval_len_is_window_not_endpoint(self):
        sql = "WHERE t >= DATE_SUB(NOW(), INTERVAL ${days} DAY)"
        self.assertEqual(pp._role_in_predicate(sql, "days"), "window_len")

    def test_offset_inside_arithmetic_is_window_len(self):
        """参与算术的占位符是**偏移量**，不是区间端点（实测 ds=7 把它当端点，
        把日期串下发给声明 int 的入参，被编译层类型校验当场拒住）。"""
        secs = "WHERE __time__ >= to_unixtime(now()) - ${days} * 86400"
        self.assertEqual(pp._role_in_predicate(secs, "days"), "window_len")
        anchor = "WHERE event_time >= now() - ${lookback}"
        self.assertEqual(pp._role_in_predicate(anchor, "lookback"), "window_len")
        # 没有算术的普通端点比较不得被这条规则抢走
        plain = "WHERE event_time >= ${start_time}"
        self.assertEqual(pp._role_in_predicate(plain, "start_time"), "range_start")
        # 非时间的算术（阈值换算）不算相对窗口，交回原判定
        money = "WHERE amount > ${rate} * 100"
        self.assertNotEqual(pp._role_in_predicate(money, "rate"), "window_len")

    def test_cast_wrapper_is_transparent(self):
        sql = "SELECT window_funnel( CAST(${window_sec} AS BIGINT), ts, e1 ) FROM t"
        self.assertEqual(pp._role_in_predicate(sql, "window_sec"), "func_arg")

    def test_null_shell_is_a_filter_not_a_function_argument(self):
        """`= NULLIF(${x}, '')` 是可空等值筛选；括号属值壳，不得当成口径常数的实参位。"""
        sql = ("WHERE (NULLIF(${collection_task_status}, '') IS NULL "
               "OR collection_task_status = NULLIF(${collection_task_status}, ''))")
        self.assertEqual(pp._role_in_predicate(sql, "collection_task_status"), "optional_eq")

    def test_evidence_is_column_side_not_the_param_name_itself(self):
        """参数名里带 time 但正文比的是编码列 → 不算时间窗口（避免自证）。"""
        preds = pp._predicates_of([("WHERE (${runtime_bucket} = '' "
                                    "OR bucket = ${runtime_bucket})")], "runtime_bucket")
        self.assertFalse(pp._is_time_evidence(preds))

    def test_undeclared_placeholder_is_not_planned(self):
        """正文里出现但没声明的占位符不归入计划（编译层会拒，计划不替它造控件）。"""
        plan = _plan_of('{"site": {"type": "string"}}', "WHERE ${site} = '' OR s=${site}")
        self.assertEqual([g["key"] for g in plan], ["site"])


class TestLogicalPlan(unittest.TestCase):
    def test_days_and_endpoints_belong_to_one_logical_window(self):
        """同一谓词里的 start/end(区间) 与 days(相对窗口) 归为一个逻辑参数，不看类型齐不齐。"""
        plan = _plan_of(_WINDOW_VARS, _WINDOW_PAIR)
        g = _group(plan, pp.TIME_WINDOW_KEY)
        self.assertEqual(g["kind"], pp.KIND_TIME_WINDOW)
        self.assertEqual({t["role"] for t in g["targets"]},
                         {"range_start", "range_end", "window_len"})
        self.assertEqual(g["fallback_days"], 30)          # 端点留空时数据集自身的回退窗口
        self.assertEqual(g["suggested_label"], "统计时间窗口")
        self.assertEqual(g["conflicts"], [])              # 端点 ''/days 30 不是"默认值分歧"
        self.assertEqual(g["label"], pp.TIME_WINDOW_KEY)  # 未声明 label 时回落 key（弹窗里改判）

    def test_site_stays_a_business_filter_separate_from_routing(self):
        plan = _plan_of(_WINDOW_VARS, _WINDOW_PAIR)
        g = _group(plan, "site")
        self.assertEqual((g["kind"], g["control"]), ("scalar", "widget_text"))

    def test_dash_key_wins_over_shape_inference(self):
        """人工归一（region ↔ site 这类异名同义）落库后就是权威，形状推断不得盖过它。"""
        vars_ = ('{"region": {"type": "string", "default": "", "dash_key": "region", '
                 '"label": "站点"}, "site": {"type": "string", "dash_key": "region"}}')
        sql = ("WHERE (${region} = '' OR region = ${region}) "
               "AND (${site} = '' OR site = ${site})")
        plan = _plan_of(vars_, sql)
        self.assertEqual(len(plan), 1)
        g = plan[0]
        self.assertEqual((g["key"], g["label"]), ("region", "站点"))
        self.assertEqual({t["var"] for t in g["targets"]}, {"region", "site"})

    def test_non_time_pair_does_not_steal_the_time_window_key(self):
        """min/max 这类非时间区间另起键名，不得冒充统计时间窗口。"""
        vars_ = '{"min_price": {"type": "number"}, "max_price": {"type": "number"}}'
        plan = _plan_of(vars_, "WHERE price >= ${min_price} AND price <= ${max_price}")
        self.assertNotIn(pp.TIME_WINDOW_KEY, [g["key"] for g in plan])
        g = plan[0]
        self.assertEqual((g["kind"], g["control"], g["pending"]), ("range", "", True))

    def test_unreferenced_declaration_gets_no_control(self):
        plan = _plan_of('{"ghost": {"type": "string"}}', "WHERE site = ${site}")
        g = _group(plan, "ghost")
        self.assertFalse(g["referenced"])
        self.assertEqual(g["control"], "")
        self.assertTrue(any("未引用" in c for c in g["conflicts"]))

    def test_function_argument_becomes_constant_without_control(self):
        plan = _plan_of('{"window_sec": {"type": "int", "default": 1800}}',
                        "SELECT window_funnel(${window_sec}, ts, e) FROM t")
        g = _group(plan, "window_sec")
        self.assertEqual((g["kind"], g["control"], g["pending"]), ("constant", "", False))

    def test_explicit_value_type_override(self):
        """声明里写 value_type 就以此为准（人明确表达的意图优先于形状推断）。"""
        plan = _plan_of('{"channel": {"type": "string", "value_type": "enum", '
                        '"values": ["ios", "android"]}}',
                        "WHERE (${channel} = '' OR channel = ${channel})")
        g = _group(plan, "channel")
        self.assertEqual((g["value_type"], g["control"]), ("enum", "widget_select"))
        self.assertEqual(g["options"], ["ios", "android"])

    def test_preset_shape_from_real_daterange_widget(self):
        """widget_daterange 实际会写 {preset, start, end}，端点已算好就按端点落。"""
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        args, _ = pp.bound_args(row, {"time_window": {"preset": "7d", "start": "2026-10-30",
                                                     "end": "2026-11-06"}})
        self.assertEqual(args, {"start_time": "2026-10-30", "end_time": "2026-11-06"})

    def test_legacy_alias_keys_are_not_accepted(self):
        """逻辑参数的键名只有 start/end/days/preset：`start_date` 之类是数据集变量名，
        在这里开后门就等于把字段名白名单写回代码。"""
        with self.assertRaises(ValueError):
            pp.parse_window_value({"start_date": "2026-10-01", "end_date": "2026-10-31"},
                                  label="统计时间窗口")

    def test_illegal_dash_key_falls_back_to_shape_with_visible_warning(self):
        """非法逻辑参数名不得污染看板 params 命名空间：退回形状推断，并把原因显式说出来。"""
        plan = _plan_of('{"start_time": {"type": "string", "dash_key": "统计 区间"},'
                        ' "end_time": {"type": "string"}}',
                        "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        g = _group(plan, pp.TIME_WINDOW_KEY)
        self.assertEqual(g["kind"], pp.KIND_TIME_WINDOW)
        self.assertTrue(any("不合法" in c for c in g["conflicts"]), g["conflicts"])
        args, _ = pp.bound_args(_row('{"start_time": {"type": "string", "dash_key": "统计 区间"},'
                                     ' "end_time": {"type": "string"}}',
                                     "WHERE event_time >= ${start_time} AND event_time <= ${end_time}"),
                                {"time_window": {"start": "2026-10-01", "end": "2026-10-02"}})
        self.assertEqual(args["start_time"], "2026-10-01")

    def test_empty_variables_yield_empty_plan(self):
        self.assertEqual(pp.logical_plan(None), [])
        self.assertEqual(pp.logical_plan("{}"), [])
        self.assertEqual(pp.logical_plan("{坏掉的 JSON"), [])


class TestBoundArgs(unittest.TestCase):
    def test_pair_writes_both_endpoints_per_declared_type(self):
        row = _row('{"start_date": {"type": "date"}, "end_date": {"type": "date"}}',
                   "WHERE stat_date >= ${start_date} AND stat_date <= ${end_date}")
        args, diag = pp.bound_args(row, {"time_window": {"start": "2026-11-01",
                                                        "end": "2026-11-06"}})
        self.assertEqual(args, {"start_date": "2026-11-01", "end_date": "2026-11-06"})
        self.assertEqual(diag["unbound"], [])

    def test_datetime_targets_get_day_bounds(self):
        row = _row('{"start_time": {"type": "datetime"}, "end_time": {"type": "datetime"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        args, _ = pp.bound_args(row, {"time_window": "2026-11-01,2026-11-03"})
        self.assertEqual(args, {"start_time": "2026-11-01 00:00:00",
                               "end_time": "2026-11-03 23:59:59"})

    def test_relative_window_backfills_endpoints(self):
        """近 N 天预设对"只有端点"的数据集要算出端点，而不是让它按默认空值出全量。"""
        from datetime import date, timedelta

        row = _row('{"start_time": {"type": "string", "default": ""},'
                   ' "end_time": {"type": "string", "default": ""}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        args, diag = pp.bound_args(row, {"time_window": {"days": 7}})
        self.assertEqual(args["end_time"], date.today().strftime("%Y-%m-%d"))
        self.assertEqual(args["start_time"],
                         (date.today() - timedelta(days=6)).strftime("%Y-%m-%d"))
        self.assertEqual(diag["unbound"], [])

    def test_absolute_range_derives_days_for_relative_body(self):
        row = _row('{"days": {"type": "int", "default": 30}}',
                   "WHERE t >= DATE_SUB(NOW(), INTERVAL ${days} DAY)")
        args, diag = pp.bound_args(row, {"time_window": {"start": "2026-10-01",
                                                        "end": "2026-10-31"}})
        self.assertEqual(args, {"days": 31})                       # 含首尾
        self.assertTrue(any("相对窗口" in n for n in diag["notes"]))   # 派生事实要说出来

    def test_epoch_seconds_body_gets_day_count_not_a_date_string(self):
        """只声明一个天数入参、正文用秒差的数据集：日期区间必须换算成整天数。"""
        row = _row('{"days": {"type": "int", "default": 30}}',
                   "WHERE \"app.env\" = 'prod' AND __time__ >= to_unixtime(now()) - ${days} * 86400")
        args, diag = pp.bound_args(row, {"time_window": {"start": "2026-11-04",
                                                         "end": "2026-11-05"}})
        self.assertEqual(args, {"days": 2})
        self.assertEqual(diag["unbound"], [])
        # 只给一端 → 换算不出整天数，报错而不是拿默认 30 天凑（no-silent-degradation）
        with self.assertRaises(ValueError) as ctx:
            pp.bound_args(row, {"time_window": {"start": "2026-11-04"}})
        self.assertIn("相对窗口", str(ctx.exception))

    def test_half_given_range_is_refused_not_defaulted(self):
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        with self.assertRaises(ValueError) as ctx:
            pp.bound_args(row, {"time_window": {"start": "2026-10-01"}})
        self.assertIn("只给了起始侧", str(ctx.exception))

    def test_unparseable_window_value_is_refused(self):
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        with self.assertRaises(ValueError) as ctx:
            pp.bound_args(row, {"time_window": {"start": "上周", "end": ""}})
        self.assertIn("不是可识别的日期", str(ctx.exception))

    def test_reversed_range_is_refused(self):
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        with self.assertRaises(ValueError) as ctx:
            pp.bound_args(row, {"time_window": {"start": "2026-10-10", "end": "2026-10-01"}})
        self.assertIn("起始日期晚于结束日期", str(ctx.exception))

    def test_scalar_and_multi_target_binding(self):
        vars_ = '{"region": {"type": "string", "dash_key": "site_region", "label": "站点"},'\
                ' "site": {"type": "string", "dash_key": "site_region"}}'
        row = _row(vars_, "WHERE (${region} = '' OR region = ${region}) "\
                          "AND (${site} = '' OR site = ${site})")
        args, diag = pp.bound_args(row, {"site_region": "cn"})
        self.assertEqual(args, {"region": "cn", "site": "cn"})
        self.assertTrue(any("写入 2 个入参" in n for n in diag["notes"]))

    def test_unbound_param_is_reported_not_silently_defaulted(self):
        """看板没给值 → 数据集会按自身默认出数，这件事必须让前端看得见。"""
        row = _row('{"site": {"type": "string", "default": "", "label": "站点"},'\
                   ' "os": {"type": "string", "default": ""}}',
                   "WHERE (${site} = '' OR s = ${site}) AND (${os} = '' OR o = ${os})")
        args, diag = pp.bound_args(row, {"os": "android"})
        self.assertEqual(args, {"os": "android"})
        self.assertEqual([u["key"] for u in diag["unbound"]], ["site"])
        self.assertEqual(diag["unbound"][0]["label"], "站点")

    def test_constant_and_foreign_keys_are_ignored(self):
        row = _row('{"window_sec": {"type": "int"}, "site": {"type": "string"}}',
                   "SELECT window_funnel(${window_sec}, ts) FROM t")
        args, diag = pp.bound_args(row, {"window_sec": 900, "unrelated": "x"})
        self.assertEqual(args, {})
        self.assertEqual(diag["skipped"], 1)         # 本图不接的值不算异常，只计数
        # 口径常数与"正文没引用的声明"都不报未绑定，否则图卡角标全是噪音
        self.assertEqual(diag["unbound"], [])

    def test_empty_string_value_counts_as_unbound(self):
        row = _row('{"site": {"type": "string"}}', "WHERE (${site} = '' OR s = ${site})")
        _args, diag = pp.bound_args(row, {"site": "   "})
        self.assertEqual([u["key"] for u in diag["unbound"]], ["site"])

    def test_cleared_range_object_is_unbound_not_an_error(self):
        """日期区间控件清空后下发 {start:'', end:''}：这是"没给值"，不是形态错误。

        活库复现（看板 111·异常上报·CASE 明细）：旧行为在这里直接抛
        "取值形态无法识别：['end','start']"，看板一打开就满屏"刷新失败"。
        正文普遍写成 `${x} = '' OR ...`，空值就是不参与检索。
        """
        row = _row('{"start_time": {"type": "string", "default": ""},'
                   ' "end_time": {"type": "string", "default": ""}}',
                   "WHERE (${start_time} = '' OR event_time >= ${start_time})"
                   " AND (${end_time} = '' OR event_time <= ${end_time})")
        for cleared in ({"start": "", "end": ""}, {"start": "", "end": "", "preset": ""},
                        {"start": None, "end": None, "days": None}):
            args, diag = pp.bound_args(row, {"time_window": cleared})
            self.assertEqual(args, {}, cleared)
            self.assertEqual([u["key"] for u in diag["unbound"]], ["time_window"], cleared)

    def test_unknown_window_keys_are_still_refused(self):
        """控件清空（已知键值为空）可以不报错，但不认识的键不能退成"没给值"。"""
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        with self.assertRaises(ValueError) as ctx:
            pp.bound_args(row, {"time_window": {"begin": "", "start": ""}})
        self.assertIn("无法识别", str(ctx.exception))

    def test_half_given_range_still_refused_after_empty_fix(self):
        """只给一端仍是口径不完整（真歧义），不能因为"空值不报错"而一并括掉。"""
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        with self.assertRaises(ValueError) as ctx:
            pp.bound_args(row, {"time_window": {"start": "2026-10-01", "end": ""}})
        self.assertIn("只给了起始侧", str(ctx.exception))

    def test_subday_window_says_it_lands_on_whole_days(self):
        """选了“近 1 小时”但正文端点按天落地 → 范围被放宽到整天，必须写进 notes。"""
        row = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                   "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        _args, diag = pp.bound_args(row, {"time_window": {"start": "2026-11-10 14:23",
                                                          "end": "2026-11-10 15:23"}})
        self.assertTrue(any("放宽到整天" in n for n in diag["notes"]), diag["notes"])
        # 只给日期（本来就是天粒度）不该弹这条说明
        _args, clean = pp.bound_args(row, {"time_window": {"start": "2026-11-01",
                                                           "end": "2026-11-05"}})
        self.assertFalse(any("放宽到整天" in n for n in clean["notes"]), clean["notes"])


class TestSiteValueDomain(unittest.TestCase):
    """值域=站点的入参：看板给的是站点，写进正文要换成该字段的实际取值。

    现网实测（不是假设）：新加坡站点 site_code=int，但业务表 `site` 列存 `global`、
    日志侧 `region` 列存 `en`；控件只有一个值，翻译只能发生在 bound_args 这一处。
    """

    SITES = {
        "cn": {"site_code": "cn", "display_name": "中国站点", "is_aggregate": 0,
               "field_values": {"site": "cn", "region": "cn"}},
        "int": {"site_code": "int", "display_name": "新加坡站点", "is_aggregate": 0,
                "field_values": {"site": "global", "region": "en"}},
        "global": {"site_code": "global", "display_name": "全部站点", "is_aggregate": 1,
                   "field_values": {}},
    }
    ALIASES = {"新加坡站点": "int", "int": "int", "全部站点": "global",
               "global": "global", "cn": "cn"}

    def _patch_site(self):
        from unittest import mock

        stack = mock.patch.multiple(
            "backend.core.site_service",
            normalize_site_ref=lambda ref: self.ALIASES.get(str(ref or "").strip(), ""),
            get_site=lambda code: self.SITES.get(str(code or "")),
            is_aggregate_site=lambda ref: bool(self.SITES.get(
                self.ALIASES.get(str(ref or "").strip(), ""), {}).get("is_aggregate")),
            site_field_value=lambda ref, field: (self.SITES.get(
                self.ALIASES.get(str(ref or "").strip(), ""), {})
                .get("field_values") or {}).get(str(field).lower()),
        )
        return stack

    _SITE_BODY = "WHERE is_release = 1 AND (${site} = '' OR site = ${site})"
    _SITE_VARS = '{"site": {"type": "string", "default": "", "value_domain": "site"}}'

    def test_site_code_translates_to_field_value(self):
        from unittest import mock

        row = _row(self._SITE_VARS, self._SITE_BODY)
        with self._patch_site():
            args, diag = pp.bound_args(row, {"site": "int"})
        self.assertEqual(args, {"site": "global"})            # 不再是筛不到的 'int'
        self.assertEqual(diag["unbound"], [])

    def test_same_site_differs_by_target_field(self):
        """同一站点在 region 字段上是 'en'：按目标变量名取映射，不是全局一个值。"""
        from unittest import mock

        row = _row('{"region": {"type": "string", "default": "", "value_domain": "site"}}',
                   "WHERE (${region} = '' OR region = ${region})")
        with self._patch_site():
            args, _diag = pp.bound_args(row, {"region": "int"})
        self.assertEqual(args, {"region": "en"})

    def test_business_name_input_translates_too(self):
        """存量控件可能直接把业务名当值下发（新加坡站点），同样要能翻译。"""
        from unittest import mock

        row = _row(self._SITE_VARS, self._SITE_BODY)
        with self._patch_site():
            args, _ = pp.bound_args(row, {"site": "新加坡站点"})
        self.assertEqual(args, {"site": "global"})

    def test_no_domain_declared_means_no_translation(self):
        """没登记值域就不翻译：普通业务值（如 'cn' 可能真是业务值）不得被当成站点改掉。"""
        from unittest import mock

        row = _row('{"site": {"type": "string", "default": ""}}', self._SITE_BODY)
        with self._patch_site():
            args, diag = pp.bound_args(row, {"site": "int"})
        self.assertEqual(args, {"site": "int"})
        self.assertEqual(diag["notes"], [])

    def test_aggregate_site_binds_nothing_and_says_so(self):
        """选「全部站点」= 不给值不参与筛选，不得编一个具体取值去筛。"""
        from unittest import mock

        row = _row(self._SITE_VARS, self._SITE_BODY)
        with self._patch_site():
            args, diag = pp.bound_args(row, {"site": "global"})
        self.assertEqual(args, {})
        self.assertEqual([u["key"] for u in diag["unbound"]], ["site"])
        notes = [n for n in diag["notes"] if "全部站点" in n]
        self.assertEqual(len(notes), 1, f"同一句提示不得重复上报：{diag['notes']}")

    def test_value_not_a_site_keeps_original_with_note(self):
        from unittest import mock

        row = _row(self._SITE_VARS, self._SITE_BODY)
        with self._patch_site():
            args, diag = pp.bound_args(row, {"site": "某某业务值"})
        self.assertEqual(args, {"site": "某某业务值"})
        self.assertTrue(any("不在站点清单" in n for n in diag["notes"]), diag["notes"])

    def test_missing_field_mapping_is_visible_not_guessed(self):
        """该站点在这个字段上没配映射：原值下发 + 说"可能筛不到数"，不拿站点代码猜。"""
        from unittest import mock

        row = _row('{"area": {"type": "string", "default": "", "value_domain": "site"}}',
                   "WHERE (${area} = '' OR area = ${area})")
        with self._patch_site():
            args, diag = pp.bound_args(row, {"area": "int"})
        self.assertEqual(args, {"area": "int"})
        self.assertTrue(any("没配取值映射" in n for n in diag["notes"]), diag["notes"])

    def test_empty_value_skips_translation(self):
        """清空 = 不给值：不参与筛选也不报"不是站点"（与上批空参数口径一致）。"""
        from unittest import mock

        row = _row(self._SITE_VARS, self._SITE_BODY)
        with self._patch_site():
            args, diag = pp.bound_args(row, {"site": ""})
        self.assertEqual(args, {})
        self.assertFalse(any("不在站点清单" in n for n in diag["notes"]), diag["notes"])

    def test_mapping_is_configuration_not_a_code_constant(self):
        """护栏：站点→字段取值不得 hardcode 在代码里（只能读「站点维护」页的配置）。

        只审**非 docstring 的字符串常量**（注释不进 AST，自然不入候选）：
        文档里写实测背景是鼓励的，但代码里出现具体站点取值就说明有人把映射写死了。
        """
        with open(MODULE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        doc_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant):
                doc_ids.add(id(node.body[0].value))
        literals = {c.value for c in ast.walk(tree)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    and id(c) not in doc_ids}
        for leaked in ("global", "cn", "eu", "uk", "jp"):
            self.assertNotIn(leaked, literals,
                             f"站点取值 {leaked!r} 被写死在代码里，应配在「系统管理 → 站点维护」")
        self.assertIn("VALUE_DOMAIN_SITE", src)


class TestMergeCatalog(unittest.TestCase):
    def test_same_key_across_datasets_normalizes_into_one_logical_param(self):
        """三种时间写法归一成一个逻辑参数——这就是"一个控件驱动全部数据集"的依据。"""
        a = _row('{"start_time": {"type": "string"}, "end_time": {"type": "string"}}',
                 "WHERE event_time >= ${start_time} AND event_time <= ${end_time}")
        b = _row('{"date_start": {"type": "date"}, "date_end": {"type": "date"}}',
                 "WHERE stat_date >= ${date_start} AND stat_date <= ${date_end}")
        catalog = pp.merge_logical_plans([
            (1, pp.logical_plan(a["variables"], pp._bodies_of(a))),
            (2, pp.logical_plan(b["variables"], pp._bodies_of(b))),
        ])
        g = next(c for c in catalog if c["key"] == pp.TIME_WINDOW_KEY)
        self.assertEqual(sorted(set(g["datasets"])), [1, 2])
        self.assertEqual({v["var"] for v in g["vars"]},
                         {"start_time", "end_time", "date_start", "date_end"})
        self.assertEqual(g["conflicts"], [])      # 异名同形状不算分歧

    def test_value_type_conflict_is_surfaced(self):
        a = _row('{"level": {"type": "string", "dash_key": "shared", "label": "级别"}}',
                 "WHERE (${level} = '' OR l = ${level})")
        b = _row('{"grade": {"type": "int", "dash_key": "shared", "label": "级别"}}',
                 "WHERE (${grade} = '' OR g = ${grade})")
        catalog = pp.merge_logical_plans([
            (1, pp.logical_plan(a["variables"], pp._bodies_of(a))),
            (2, pp.logical_plan(b["variables"], pp._bodies_of(b))),
        ])
        g = next(c for c in catalog if c["key"] == "shared")
        self.assertTrue(any("值类型分歧" in c for c in g["conflicts"]), g["conflicts"])

    def test_param_summary_is_business_readable_and_leak_free(self):
        row = _row(_WINDOW_VARS, _WINDOW_PAIR)
        row["name"] = "异常上报·按级别汇总"
        summary = pp.param_summary(row)
        keys = {s["key"] for s in summary}
        self.assertIn(pp.TIME_WINDOW_KEY, keys)
        blob = str(summary)
        # 清单要能驱动装配，但不得把正文/内部 id 顺带带出去（护栏 §7）
        for banned in ("SELECT", "adh_datasources", "datasource_id", "sql_query"):
            self.assertNotIn(banned, blob)


if __name__ == "__main__":
    unittest.main()
