"""SLS(日志服务/Presto) 核心取数路径的编译回归。

背景（会话 1783069459140 查不到数的根因链）：ARMS RUM 直查 logstore 时，物理列名
本身带点（`view.url`/`session.id`），且事件时间是 epoch 秒 bigint、投影别名不能是
中文。planner 过去只按 PG 族通用文法出 SQL，这些写法在 SLS 上不是报错就是错数。
引擎能力边界全部由实机探针取证（logs/probe_sls_core.py ~ probe_sls_core7.py，
均为 LIMIT 0/纯聚合，不取业务明细），本文件把探针结论钉成门禁。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.semantics.planner import (  # noqa: E402
    _ColumnResolver, _quote_dotted_columns, plan,
)
from backend.semantics.models import (  # noqa: E402
    ResolvedBinding, SemanticFilter, SemanticQuery,
)

# ── 内存元数据（照 logstore-rum 真实形状取样：带点列 + bigint 时间列）──────────
PHYS_SLS = {
    "event_type": {"data_type": "varchar", "business_desc": "事件类型"},
    "view.url": {"data_type": "varchar", "business_desc": "页面URL"},
    "view.name": {"data_type": "varchar", "business_desc": "页面名称"},
    "session.id": {"data_type": "varchar", "business_desc": "会话ID"},
    "user.id": {"data_type": "varchar", "business_desc": "用户ID"},
    "net.ip": {"data_type": "varchar", "business_desc": "客户端IP"},
    "resource.duration": {"data_type": "bigint", "business_desc": "资源耗时"},
    "resource.success": {"data_type": "int", "business_desc": "请求结果"},
    # 业务侧同名时间字段实测存 epoch 毫秒，元数据不声明单位
    "timestamp": {"data_type": "bigint", "business_desc": "事件时间"},
}
DIMS_SLS = {
    "页面URL": {"name": "页面URL", "name_en": "page_url", "target_column": "view.url",
               "category": "属性", "aliases": [], "value_labels": {}},
    # 业务 IP 维度：能不能下发由数据权限（adh_sensitive_fields）裁决，编译期写进 SQL
    "客户端IP": {"name": "客户端IP", "name_en": "client_ip", "target_column": "net.ip",
               "category": "属性", "aliases": [], "value_labels": {}},
    "事件类型": {"name": "事件类型", "name_en": "event_type", "target_column": "event_type",
               "category": "状态", "aliases": [], "value_labels": {}},
    # SLS 内置事件时间列是 epoch 秒，不在 information_schema 里（探针 C/R1 OK）
    "统计日期": {"name": "统计日期", "name_en": "stat_date", "target_column": "__time__",
               "category": "时间", "aliases": [], "value_labels": {}},
    "统计小时": {"name": "统计小时", "name_en": "stat_hour", "target_column": "__time__",
               "category": "时间", "aliases": [], "value_labels": {}},
    # 名字里没有粒度词的时间维 → 兜到秒级完整时间串（不能回裸 epoch）
    "上报时间": {"name": "上报时间", "name_en": "report_at", "target_column": "__time__",
               "category": "时间", "aliases": [], "value_labels": {}},
}
METRICS_SLS = {
    "页面浏览量": {"name": "页面浏览量", "name_en": "m_pv",
                "formula": "SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END)",
                "formula_dsl": None, "agg_type": "SUM", "default_agg": "SUM",
                "unit": "次", "description": "", "aliases": [], "formula_dialects": None},
    "会话数": {"name": "会话数", "name_en": "m_sessions",
             "formula": "COUNT(DISTINCT session.id)",
             "formula_dsl": None, "agg_type": "COUNT_DISTINCT", "default_agg": "COUNT_DISTINCT",
             "unit": "个", "description": "", "aliases": [], "formula_dialects": None},
    "人均会话数": {"name": "人均会话数", "name_en": "m_sess_per_user",
               "formula": "COUNT(DISTINCT session.id) / COUNT(DISTINCT user.id)",
               "formula_dsl": None, "agg_type": "NONE", "default_agg": "NONE",
               "unit": "个", "description": "", "aliases": [], "formula_dialects": None},
}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(_ColumnResolver, "_load_phys", lambda self: dict(PHYS_SLS))
    monkeypatch.setattr(_ColumnResolver, "_load_dims", lambda self: dict(DIMS_SLS))
    monkeypatch.setattr(_ColumnResolver, "_load_metrics", lambda self: dict(METRICS_SLS))


def _binding(db_type="sls"):
    return ResolvedBinding(object_key="rum_event", datasource_id=1,
                           physical_table="logstore-rum", db_type=db_type)


def _resolver(binding=None, dialect="postgres"):
    return _ColumnResolver(binding or _binding(), dialect=dialect)


# ── 带点物理列引用（探针 A/B ERR·OK、K/J、Q8/Q9）──────────────────────────

class TestDottedColumnQuoting:
    def test_dotted_column_is_quoted_whole(self, patched):
        r = _resolver()
        assert r._col("view.url") == '"view.url"'
        assert r._col("event_type") == '"event_type"'

    def test_dotted_column_in_join_side(self, patched):
        r = _ColumnResolver(_binding(), dialect="postgres", column_prefix="t1")
        assert r._col("view.url") == 't1."view.url"'

    def test_formula_dotted_columns_are_quoted(self, patched):
        r = _resolver()
        expr, _agg, _unit, _uns = r.resolve_measure("会话数")
        assert expr == 'COUNT(DISTINCT "session.id")'

    def test_multi_column_formula_all_rewritten(self, patched):
        r = _resolver()
        expr, _a, _u, _s = r.resolve_measure("人均会话数")
        assert expr == 'COUNT(DISTINCT "session.id") / COUNT(DISTINCT "user.id")'

    def test_string_literal_is_not_rewritten(self, patched):
        # 'view' 是码值不是列名，加引号会把条件改坏
        r = _resolver()
        expr, _a, _u, _s = r.resolve_measure("页面浏览量")
        assert expr == "SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END)"

    def test_already_quoted_segment_is_untouched(self, patched):
        out = _quote_dotted_columns(
            'SELECT "view.url" FROM t WHERE "view.url" LIKE \'a.b%\'', "postgres")
        assert out.count('"view.url"') == 2
        assert "'a.b%'" in out

    def test_longest_name_wins(self, patched):
        out = _quote_dotted_columns("AVG(view.scroll.max_scroll_height_time)", "postgres")
        assert out == 'AVG("view.scroll.max_scroll_height_time")'

    def test_quoting_does_not_need_column_metadata(self, monkeypatch):
        # 跳站部署：目标源可能尚未同步列元数据(实测 arms-prd-en 零登记行)，
        # 带点列仍必须加引号，否则 session.id 直报 Column cannot be resolved
        monkeypatch.setattr(_ColumnResolver, "_load_phys", lambda self: {})
        monkeypatch.setattr(_ColumnResolver, "_load_dims", lambda self: {})
        monkeypatch.setattr(_ColumnResolver, "_load_metrics", lambda self: dict(METRICS_SLS))
        r = _resolver()
        expr, _a, _u, _s = r.resolve_measure("会话数")
        assert expr == 'COUNT(DISTINCT "session.id")'

    def test_mysql_family_dotted_column_backticked(self, patched):
        r = _ColumnResolver(_binding(db_type="mysql"), dialect="mysql")
        # 非带点列引擎不进入改写分支（MySQL 列名不含点），行为不变
        assert r._col("create_time") == "`create_time`"

    def test_non_sls_engine_does_not_rewrite_formula(self, patched):
        r = _ColumnResolver(_binding(db_type="postgres"), dialect="postgres")
        expr, _a, _u, _s = r.resolve_measure("会话数")
        assert expr == "COUNT(DISTINCT session.id)"


# ── 时间谓词（探针 G/H、Q1/Q4、T3~T7、U4~U7）─────────────────────────────

class TestSlsTimePredicates:
    def test_window_uses_to_unixtime_and_string_interval(self, patched):
        r = _resolver()
        cond = r.render_time_window("30d", "统计日期", [])
        assert cond == ('"__time__" >= to_unixtime(now() - INTERVAL \'30 days\')')

    def test_window_unit_is_inside_string(self, patched):
        r = _resolver()
        cond = r.render_time_window("2w", "统计日期", [])
        assert "INTERVAL '2 weeks'" in cond
        assert "'2' WEEK" not in cond

    def test_calendar_range_uses_date_add_and_unixtime(self, patched):
        r = _resolver()
        cond = r.render_time_range("yesterday", "统计日期", [])
        assert "to_unixtime(date_add('day', -1, CURRENT_DATE))" in cond
        assert "CURRENT_DATE - 1" not in cond

    def test_calendar_range_month(self, patched):
        r = _resolver()
        cond = r.render_time_range("this_month", "统计日期", [])
        assert cond.startswith('"__time__" >= to_unixtime(date_trunc(\'month\', CURRENT_DATE))')

    def test_absolute_bounds_are_converted(self, patched):
        r = _resolver()
        cond = r.render_time_bounds("2026-01-01", "2026-01-31", "统计日期", [])
        assert "to_unixtime(timestamp '2026-01-01')" in cond
        assert ">= '2026-01-01'" not in cond

    def test_time_grain_bucket_uses_date_format(self, patched):
        r = _resolver()
        expr, role = r.resolve_dimension("统计日期", time_grain="day")
        assert role == "time"
        assert expr == 'date_format(from_unixtime("__time__"), \'%Y-%m-%d\')'

    def test_default_grain_follows_dim_business_name(self, patched):
        # 业务名自带粒度承诺：「统计小时」→ hour、「统计日期」→ day（裸 epoch 不可读）
        r = _resolver()
        assert r.resolve_dimension("统计小时")[0] == \
            'date_format(from_unixtime("__time__"), \'%Y-%m-%d %H\')'
        assert r.resolve_dimension("统计日期")[0] == \
            'date_format(from_unixtime("__time__"), \'%Y-%m-%d\')'

    def test_time_dim_without_grain_word_is_second_level(self, patched):
        r = _resolver()
        assert r.resolve_dimension("上报时间")[0] == \
            'date_format(from_unixtime("__time__"), \'%Y-%m-%d %H:%i:%s\')'

    def test_explicit_grain_beats_name_derived_default(self, patched):
        r = _resolver()
        assert r.resolve_dimension("统计日期", time_grain="month")[0] == \
            'date_format(from_unixtime("__time__"), \'%Y-%m\')'

    def test_filter_on_epoch_time_dim_buckets_by_value_shape(self, patched):
        # bigint 列与 '2026-10-01' 直接比较是类型错误；按值粒度分桶后作字符串比较
        r = _resolver()
        cond = r.render_filter(SemanticFilter(dim="统计日期", op="gte",
                                              value="2026-10-01"))
        assert cond == ("date_format(from_unixtime(\"__time__\"), '%Y-%m-%d') "
                        ">= '2026-10-01'")

    def test_filter_on_epoch_time_dim_with_datetime_value(self, patched):
        r = _resolver()
        cond = r.render_filter(SemanticFilter(
            dim="统计日期", op="gte", value="2026-10-01 08:00:00"))
        assert "'%Y-%m-%d %H:%i:%s') >= '2026-10-01 08:00:00'" in cond

    def test_filter_on_epoch_time_dim_numeric_value_kept_as_epoch(self, patched):
        r = _resolver()
        cond = r.render_filter(SemanticFilter(dim="统计日期", op="gt", value=1793245034))
        assert cond == '"__time__" > 1793245034'

    def test_time_grain_week_is_iso(self, patched):
        r = _resolver()
        expr, _ = r.resolve_dimension("统计日期", time_grain="week")
        assert "'%x-%v'" in expr

    def test_time_grain_quarter_uses_concat(self, patched):
        # Presto 无 %q(探针 T9 输出字面量 "2026-Qq")、|| 解析不通(探针 U1 ERR)
        r = _resolver()
        expr, _ = r.resolve_dimension("统计日期", time_grain="quarter")
        assert expr.startswith("CONCAT(date_format(") and "'-Q'" in expr

    def test_no_to_char_on_sls(self, patched):
        r = _resolver()
        for grain in ("day", "hour", "month", "year"):
            expr, _ = r.resolve_dimension("统计日期", time_grain=grain)
            assert "to_char" not in expr

    def test_unit_unknown_time_column_fails_loud(self, patched):
        # 业务 timestamp 列是 epoch 毫秒：不能当秒静默比较（那等于时间窗失效）
        r = _resolver()
        warns = []
        assert r.render_time_window("30d", "事件时间", warns) is None
        assert warns and "时间单位无法确定" in warns[0]

    def test_fail_loud_hint_does_not_leak_physical_names(self, patched):
        r = _resolver()
        warns = []
        r.render_time_window("30d", "事件时间", warns)
        hay = " ".join(warns)
        for leak in ("timestamp", "__time__", "logstore-rum", "bigint"):
            assert leak not in hay

    def test_pg_family_other_engine_unchanged(self, patched):
        r = _resolver(_binding(db_type="postgres"), dialect="postgres")
        assert r.render_time_window(
            "7d", "统计日期", []) == '"__time__" >= now() - INTERVAL \'7 days\''


# ── 投影别名 ASCII 槽位（探针 M/S15 ERR、Q7 OK）──────────────────────────

class TestSlsAsciiProjectionSlots:
    def test_chinese_aliases_replaced_by_slots(self, patched):
        q = SemanticQuery(object="rum_event", metrics=["页面浏览量"],
                          dimensions=["页面URL"], limit=10)
        p = plan(q, _binding())
        assert 'AS "页面URL"' not in p.sql and "AS `页面浏览量`" not in p.sql
        assert 'AS "o0"' in p.sql and 'AS "o1"' in p.sql
        assert p.output_header_map == {"o0": "页面URL", "o1": "页面浏览量"}

    def test_order_by_uses_slot(self, patched):
        from backend.semantics.models import SemanticOrder
        q = SemanticQuery(object="rum_event", metrics=["页面浏览量"],
                          dimensions=["页面URL"],
                          order=[SemanticOrder(by="页面浏览量", desc=True)], limit=10)
        p = plan(q, _binding())
        assert 'ORDER BY "o1" DESC' in p.sql
        assert "页面浏览量" not in p.sql

    def test_mysql_path_keeps_business_aliases(self, patched):
        q = SemanticQuery(object="rum_event", metrics=["页面浏览量"],
                          dimensions=["页面URL"], limit=10)
        p = plan(q, _binding(db_type="mysql"))
        assert p.output_header_map == {}
        assert "`页面URL`" in p.sql and "`页面浏览量`" in p.sql

    def test_filter_and_group_by_use_quoted_column(self, patched):
        q = SemanticQuery(
            object="rum_event", metrics=["会话数"],
            filters=[SemanticFilter(dim="页面URL", op="eq",
                                    value="https://allied-star.com/en/direct-ip")],
            time_window="30d", time_column="统计日期", limit=10)
        p = plan(q, _binding())
        assert 'GROUP BY' not in p.sql or '"session.id"' in p.sql
        assert 'WHERE "view.url" = ' in p.sql
        assert 'COUNT(DISTINCT "session.id")' in p.sql


class TestPlanContract:
    def test_dialect_and_route_on_sls(self, patched):
        q = SemanticQuery(object="rum_event", metrics=["会话数"], limit=5)
        p = plan(q, _binding())
        assert p.dialect == "postgres"
        # LogStore 名带连字符：不加引号会被引擎当减法（探针里全部 SQL 均用 "logstore-rum"）
        assert 'FROM "logstore-rum"' in p.sql

    def test_window_metric_resolves_to_slot(self, patched):
        from backend.semantics.models import SemanticWindow
        q = SemanticQuery(object="rum_event", metrics=["页面浏览量"],
                          dimensions=["统计日期"], time_grain="day",
                          window=SemanticWindow(type="rolling", params={"preceding": 2}),
                          limit=10)
        p = plan(q, _binding())
        assert p.sql, p.warnings
        assert "_rolling" in p.sql


# ── 列级安全策略编译进下推 SQL（护栏 §7：能不能查由数据权限裁决）────────────
#
# 历史缺陷：脱敏靠执行后 `apply_post_processing` 按 DataFrame 列名匹配，而 SLS 投影列名
# 是 ASCII 槽位 o0/o1…（业务名在 _to_result 才映回）→ 列名对不上，mask 静默不生效。
# 现在策略在 planner 编译期写进投影；不在出站层 hardcode 内容形态规则。

class TestColumnPolicyCompiledIntoSlsSql:
    def _q(self, **kw) -> SemanticQuery:
        base = dict(object="rum_event", metrics=["会话数"], dimensions=["客户端IP"],
                    time_window="7d", time_column="统计日期", limit=20)
        base.update(kw)
        return SemanticQuery(**base)

    def test_masked_dimension_wraps_projection_and_group_by(self, patched):
        p = plan(self._q(), _binding(), {"net.ip": "partial"})
        select_part, group_part = p.sql.split("GROUP BY")
        # 原值投影不再存在
        assert '"net.ip" AS "o0"' not in select_part
        # SELECT 与 GROUP BY 用同一份脱敏表达式（按脱敏值分组，不泄露 distinct 个数）
        masked = 'CASE WHEN LENGTH(CAST("net.ip" AS VARCHAR)) <= 4'
        assert masked in select_part and masked in group_part
        # 取尾必须 RIGHT：SUBSTR(x,-2) 在 SLS 不报错但返回整串（探针取证）= 不脱敏
        assert "RIGHT(CAST(\"net.ip\" AS VARCHAR), 2)" in select_part
        assert "SUBSTR(CAST(\"net.ip\" AS VARCHAR), -" not in p.sql

    def test_probe_tuned_time_predicate_is_not_rewritten(self, patched):
        """包 mask 不得把整句 SQL 过一遍 AST 重渲染（会把 now() 改成 CURRENT_TIMESTAMP，SLS 报错）。"""
        plain = plan(self._q(), _binding(), {})
        masked = plan(self._q(), _binding(), {"net.ip": "hash"})
        assert 'to_unixtime(now() - INTERVAL \'7 days\')' in masked.sql
        assert 'COUNT(DISTINCT "session.id")' in masked.sql
        # WHERE 子句逐字相同（GROUP BY 则必须跟着脱敏表达式走，见上一条用例）
        assert masked.sql.split("WHERE")[1].split("GROUP BY")[0] \
            == plain.sql.split("WHERE")[1].split("GROUP BY")[0]

    def test_aggregate_metric_is_not_masked(self, patched):
        """指标输出的是计数不是原值，包 mask 会静默改数（真不能查的列由 block 闸门硬拒）。"""
        p = plan(self._q(metrics=["会话数"], dimensions=["事件类型"]),
                 _binding(), {"session.id": "hash"})
        assert 'COUNT(DISTINCT "session.id")' in p.sql
        assert "MD5" not in p.sql

    def test_no_policy_compiles_unchanged_sql(self, patched):
        """无策略（显式 {}）时行为零变化：不引入任何包装开销。"""
        p = plan(self._q(), _binding(), {})
        assert '"net.ip" AS "o0"' in p.sql and "MD5" not in p.sql
        assert "CASE WHEN" not in p.sql
        assert p.provenance.get("column_policies_applied") is None

    def test_policy_source_is_data_permission(self, patched, monkeypatch):
        """缺省不传策略时，由数据权限（enforcer 敏感基线）解析后编译进 SQL。"""
        from backend.semantics import rls as sem_rls
        calls: list[dict] = []

        def fake_policies(**kw):
            calls.append(kw)
            return {"net.ip": "hash"}

        monkeypatch.setattr(sem_rls, "column_policies", fake_policies)
        p = plan(self._q(user_id=42, workspace_id=7), _binding())
        assert calls and calls[0]["user_id"] == 42 and calls[0]["workspace_id"] == 7
        assert calls[0]["table_names"] == ["logstore-rum"]
        assert 'SUBSTR(MD5(CAST("net.ip" AS VARCHAR)), 1, 16)' in p.sql
        # provenance 留诊断痕迹（不下发 LLM/前端）
        assert p.provenance["column_policies_applied"] == {"net.ip": "hash"}

    def test_policy_lookup_failure_refuses_to_compile_unmasked(self, patched, monkeypatch):
        """敏感基线读不到 = 不知道该怎么脱敏，宁不编译也不下发未脱敏明细（fail-loud）。"""
        from backend.semantics import rls as sem_rls

        def boom(**_kw):
            raise PermissionError("敏感数据策略暂不可用")

        monkeypatch.setattr(sem_rls, "column_policies", boom)
        p = plan(self._q(), _binding())
        assert p.sql == "" and p.secured_sql == ""
        assert any("列级安全策略不可用" in w for w in p.warnings)
