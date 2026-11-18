"""Elasticsearch 语义编译回归（planner/dialect 的 ES 分支 + 产物形态门禁 + 脱敏）。

引擎能力边界全部来自实机取证（2026-11-18，`cloud-platform-es` 四张运行日志索引，
探针见本文件各用例 docstring）；本文件把结论钉成门禁，防后续改动把 ES 写回
"语法能过、结果全错"或"直接 400"的形态。

关键事实（实机）：
- ES SQL 是 Calcite 子集：双引号标识符、必须显式 LIMIT、无 JOIN/CTE/派生表/OFFSET/窗口函数；
- 业务时间列是 **text/keyword**（`operationTime.keyword` = '2026-11-18 14:12:30'），
  可直接字符串比较与定长切片；`@timestamp` 在本源因跨滚动索引 date/keyword 类型冲突不可查；
- 时间桶只能 `SUBSTRING(CAST(col AS VARCHAR), 1, n)`（无 DATE_FORMAT/to_char），
  周桶 `DATE_TRUNC('WEEK', ...)`，季度粒度不可表达；
- 高基数列（serialNumber 等）可 `= / LIKE` 过滤与 `COUNT(DISTINCT)`，不适合 GROUP BY。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.semantics import dialect as sem_dialect  # noqa: E402
from backend.semantics.es_dialect import es_rejection  # noqa: E402
from backend.semantics.models import (  # noqa: E402
    Guardrail, ResolvedBinding, SemanticFilter, SemanticQuery,
)
from backend.semantics.planner import _ColumnResolver, plan  # noqa: E402
from backend.semantics.security.masking import mask_expr  # noqa: E402

# ── 内存元数据（照 cloud-platform-es 真实形状取样）─────────────────────────
PHYS_ES = {
    "operationTime.keyword": {"data_type": "keyword", "business_desc": "操作时间"},
    "action.keyword": {"data_type": "keyword", "business_desc": "操作类型"},
    "serialNumber.keyword": {"data_type": "keyword", "business_desc": "设备序列号"},
    "level.keyword": {"data_type": "keyword", "business_desc": "告警级别"},
    # datetime 型列（本源 @timestamp 因类型冲突不可用，仍要覆盖 date 分支渲染）
    "@timestamp": {"data_type": "date", "business_desc": "事件时间"},
}
DIMS_ES = {
    "设备操作时间": {"name": "设备操作时间", "name_en": "es_op_time",
                 "target_column": "operationTime.keyword", "category": "时间",
                 "aliases": ["云平台操作时间"], "value_labels": {}, "labels": {}},
    "操作类型": {"name": "操作类型", "name_en": "es_op_action",
              "target_column": "action.keyword", "category": "分类",
              "aliases": [], "value_labels": {}, "labels": {}},
    "操作设备序列号": {"name": "操作设备序列号", "name_en": "es_op_device",
                 "target_column": "serialNumber.keyword", "category": "设备",
                 "aliases": [], "value_labels": {}, "labels": {}},
    "原生事件时间": {"name": "原生事件时间", "name_en": "es_ts",
                "target_column": "@timestamp", "category": "时间",
                "aliases": [], "value_labels": {}, "labels": {}},
}
METRICS_ES = {
    "设备操作次数": {"name": "设备操作次数", "name_en": "es_equip_operation_count",
                "formula": "COUNT(*)", "agg_type": "COUNT", "default_agg": "COUNT",
                "unit": "次", "description": "", "aliases": [], "formula_dialects": None},
    "设备操作设备数": {"name": "设备操作设备数", "name_en": "es_equip_operation_devices",
                 "formula": 'COUNT(DISTINCT "serialNumber.keyword")',
                 "agg_type": "COUNT_DISTINCT", "default_agg": "COUNT_DISTINCT",
                 "unit": "台", "description": "", "aliases": [], "formula_dialects": None},
    "扫描操作次数": {"name": "扫描操作次数", "name_en": "es_equip_scan_count",
                "formula": 'SUM(CASE WHEN "action.keyword" = \'scan\' THEN 1 ELSE 0 END)',
                "agg_type": "SUM", "default_agg": "SUM",
                "unit": "次", "description": "", "aliases": [], "formula_dialects": None},
}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(_ColumnResolver, "_load_phys", lambda self: dict(PHYS_ES))
    monkeypatch.setattr(_ColumnResolver, "_load_dims", lambda self: dict(DIMS_ES))
    monkeypatch.setattr(_ColumnResolver, "_load_metrics", lambda self: dict(METRICS_ES))


def _binding(db_type="es", force_limit=True):
    return ResolvedBinding(
        object_key="cloud_es_equipment_operation_log", datasource_id=7,
        datasource_name="cloud-platform-es", physical_table="t_equipment_operation_log",
        db_type=db_type,
        guardrail=Guardrail(query_mode="raw_source", size_class="huge",
                            allow_full_scan=True, force_limit=force_limit))


def _resolver(binding=None, dialect="es"):
    return _ColumnResolver(binding or _binding(), dialect=dialect)


# ── 方言族登记（不登记就会被 mysql 生成器静默改写成因错数）─────────────────

class TestEsDialectRegistration:
    def test_es_registered_in_compile_family(self):
        # 对象编译族必须收 es，否则走 mysql 生成器 => `"appCode.keyword"` 被渲染成
        # 字符串字面量（语法能过、结果全错），所以这条是硬门。
        assert "es" in sem_dialect.COMPILE_FAMILY_OF_DBTYPE
        assert sem_dialect.compile_family_of("elasticsearch") == "es"

    def test_es_engine_name(self):
        assert sem_dialect.engine_of("es") == "es"
        assert sem_dialect.engine_of("elasticsearch") == "es"

    def test_plan_does_not_fall_back_to_mysql_for_es(self, patched):
        # `is_known_db_type` 是**数据集正文**方言族的判定（ES 正文透传出本表），
        # 故它对 es 返回 False 是设计如此；真正要锁的是：planner 不因此出
        # "unknown db_type" 回退告警（回退到 mysql 就是静默错数）。
        assert sem_dialect.is_known_db_type("es") is False
        p = plan(SemanticQuery(object="cloud_es_equipment_operation_log",
                               metrics=["设备操作次数"]), _binding())
        assert p.sql and not any("unknown db_type" in w for w in p.warnings)

    def test_es_identifier_uses_double_quote(self):
        assert sem_dialect.quote_alias("action.keyword", "es") == '"action.keyword"'
        assert sem_dialect.quote_ident("t_equipment_operation_log", "es") == \
            '"t_equipment_operation_log"'

    def test_es_not_in_dataset_body_family(self):
        # ES 数据集走正文透传；把它登进正文方言族会让正文被二次包裹编译
        assert "es" not in sem_dialect.FAMILY_OF_DBTYPE


# ── 列引用 / 公式：带点列整体加双引号，字面量不得被改写 ───────────────────

class TestEsColumnQuoting:
    def test_dotted_keyword_column_quoted_whole(self, patched):
        r = _resolver()
        assert r._col("action.keyword") == '"action.keyword"'

    def test_count_distinct_formula_keeps_quotes(self, patched):
        r = _resolver()
        expr, _agg, _unit, _uns = r.resolve_measure("设备操作设备数")
        assert expr == 'COUNT(DISTINCT "serialNumber.keyword")'

    def test_conditional_formula_keeps_code_literal(self, patched):
        # 'scan' 是码值不是列名：加引号会把条件改坏（CASE WHEN "x" = '"scan"' 恒假）
        r = _resolver()
        expr, _agg, _unit, _uns = r.resolve_measure("扫描操作次数")
        assert expr == 'SUM(CASE WHEN "action.keyword" = \'scan\' THEN 1 ELSE 0 END)'

    def test_projection_alias_uses_double_quote(self, patched):
        r = _resolver()
        assert sem_dialect.quote_alias("设备操作次数", "es") == '"设备操作次数"'


# ── 时间谓词：边界在服务端算好（ES 无 NOW()/INTERVAL）─────────────────────

class TestEsTimePredicates:
    def test_text_time_column_compares_raw_string(self, patched):
        cond = _resolver().render_time_window("7d", "设备操作时间", [])
        assert cond.startswith('"operationTime.keyword" >= \'')
        # text 列不得包 CAST（包了索引条件下沉失效，2.8 亿文档直接超时）
        assert "CAST(\"operationTime.keyword\"" not in cond
        # ES 没有 DATE_SUB/INTERVAL，不能用引擎时钟
        assert "DATE_SUB" not in cond and "INTERVAL" not in cond

    def test_bound_is_server_computed_datetime_string(self, patched):
        cond = _resolver().render_time_window("24h", "设备操作时间", [])
        value = cond.split(">= ", 1)[1].strip("'")
        assert len(value) == 19 and value[4] == "-" and value[13] == ":"

    def test_date_typed_column_casts_bound_to_timestamp(self, patched):
        # date 型列与裸字符串比较实测 400 -> 边界必须 CAST(... AS TIMESTAMP)
        cond = _resolver().render_time_window("7d", "原生事件时间", [])
        assert ">= CAST('" in cond and "AS TIMESTAMP)" in cond

    def test_calendar_range_is_left_closed_right_open(self, patched):
        cond = _resolver().render_time_range("last_week", "设备操作时间", [])
        assert ' >= ' in cond and ' < ' in cond
        assert cond.count("'") == 4  # 两个边界各一对引号

    def test_absolute_date_end_takes_next_day(self, patched):
        cond = _resolver().render_time_bounds("2026-11-01", "2026-11-17", "设备操作时间", [])
        assert ">= '2026-11-01 00:00:00'" in cond
        assert "< '2026-11-18 00:00:00'" in cond

    def test_invalid_window_is_reported_not_ignored(self, patched):
        warns = []
        assert _resolver().render_time_window("7 天", "设备操作时间", warns) is None
        assert any("invalid time_window" in w for w in warns)


# ── 时间桶：定长字符串切片（无 DATE_FORMAT/to_char）────────────────────────

class TestEsTimeBuckets:
    @pytest.mark.parametrize("grain,n", [("year", 4), ("month", 7), ("day", 10),
                                         ("hour", 13), ("minute", 16), ("second", 19)])
    def test_substr_length_per_grain(self, patched, grain, n):
        expr, role = _resolver().resolve_dimension("设备操作时间", time_grain=grain)
        assert role == "time"
        assert expr == f'SUBSTRING(CAST("operationTime.keyword" AS VARCHAR), 1, {n})'

    def test_week_bucket_uses_date_trunc(self, patched):
        expr, _role = _resolver().resolve_dimension("设备操作时间", time_grain="week")
        assert expr == ('SUBSTRING(CAST(DATE_TRUNC(\'WEEK\', '
                        'CAST("operationTime.keyword" AS TIMESTAMP)) AS VARCHAR), 1, 10)')

    def test_quarter_is_not_silently_faked(self, patched):
        # 未取证粒度返回空表达式（调用方按"不可解析"显式回绝），不得返回日桶/原列充数
        expr, _role = _resolver().resolve_dimension("设备操作时间", time_grain="quarter")
        assert expr is None

    def test_no_grain_keeps_raw_column(self, patched):
        expr, _role = _resolver().resolve_dimension("设备操作时间")
        assert expr == '"operationTime.keyword"'


# ── 完整计划：LIMIT 补齐 + 形态过白名单 ───────────────────────────────────

class TestEsPlanShape:
    def test_plan_has_limit_and_quoted_table(self, patched):
        q = SemanticQuery(object="cloud_es_equipment_operation_log",
                          metrics=["设备操作次数"], dimensions=["操作类型"])
        p = plan(q, _binding())
        assert p.sql, p.warnings
        assert 'FROM "t_equipment_operation_log"' in p.sql
        assert p.sql.strip().endswith("LIMIT 1000")

    def test_plan_sql_passes_es_rejection(self, patched):
        q = SemanticQuery(object="cloud_es_equipment_operation_log",
                          metrics=["设备操作设备数"], dimensions=["操作类型"],
                          filters=[SemanticFilter(dim="操作设备序列号", op="is_not_null")])
        p = plan(q, _binding())
        assert p.sql and es_rejection(p.sql) is None

    def test_limit_defaulted_even_without_force_limit(self, patched):
        # ES 引擎硬要求 LIMIT：未开 force_limit 也必须补，且要留痕
        q = SemanticQuery(object="cloud_es_equipment_operation_log", metrics=["设备操作次数"])
        p = plan(q, _binding(force_limit=False))
        assert "LIMIT 1000" in p.sql
        assert any("必须带 LIMIT" in w for w in p.warnings)

    def test_high_cardinality_dim_can_be_filtered(self, patched):
        q = SemanticQuery(object="cloud_es_equipment_operation_log",
                          metrics=["设备操作次数"],
                          filters=[SemanticFilter(dim="操作设备序列号", op="eq",
                                                  value="SN123456")])
        p = plan(q, _binding())
        assert '"serialNumber.keyword" = \'SN123456\'' in p.sql


# ── 不可表达形态：编译期 fail-loud（不降级、不换形状凑合）──────────────────

class TestEsUnsupportedShapes:
    def test_quarter_grain_rejected(self, patched):
        q = SemanticQuery(object="cloud_es_equipment_operation_log",
                          metrics=["设备操作次数"], dimensions=["设备操作时间"],
                          time_grain="quarter")
        p = plan(q, _binding())
        assert p.sql == ""
        assert any("季度" in w for w in p.warnings)

    def test_window_function_rejected(self, patched):
        from backend.semantics.models import SemanticWindow
        q = SemanticQuery(object="cloud_es_equipment_operation_log",
                          metrics=["设备操作次数"], dimensions=["操作类型"],
                          window=SemanticWindow(type="rank"))
        p = plan(q, _binding())
        assert p.sql == ""
        assert any("窗口函数" in w for w in p.warnings)


# ── 产物形态白名单（纵深防御：任何入口写出 ES 不认的 SQL 都必须被拦）────────

class TestEsRejectionGuard:
    @pytest.mark.parametrize("sql,reason", [
        ("SELECT SUBSTR(a, 1, 2) FROM t LIMIT 1", "SUBSTR"),
        ("SELECT MD5(a) FROM t LIMIT 1", "哈希"),
        ("SELECT a || b FROM t LIMIT 1", "||"),
        ("SELECT `a` FROM t LIMIT 1", "反引号"),
        ("SELECT a FROM t LIMIT 1 OFFSET 10", "OFFSET"),
        ("SELECT a FROM t1 JOIN t2 ON t1.id = t2.id LIMIT 1", "JOIN"),
        ("WITH x AS (SELECT 1) SELECT * FROM x LIMIT 1", "WITH"),
        ("SELECT a FROM (SELECT a FROM t) LIMIT 1", "子查询"),
    ])
    def test_rejected_shapes(self, sql, reason):
        got = es_rejection(sql)
        assert got and reason in got

    def test_missing_limit_rejected(self):
        assert "LIMIT" in (es_rejection("SELECT a FROM t") or "")

    def test_legal_shape_passes(self):
        sql = ('SELECT SUBSTRING(CAST("operationTime.keyword" AS VARCHAR), 1, 10) AS "设备操作时间", '
               'COUNT(*) AS "设备操作次数" FROM "t_equipment_operation_log" '
               'WHERE "operationTime.keyword" >= \'2026-11-11 00:00:00\' '
               'GROUP BY SUBSTRING(CAST("operationTime.keyword" AS VARCHAR), 1, 10) LIMIT 1000')
        assert es_rejection(sql) is None

    def test_quoted_literal_with_keyword_not_flagged(self):
        # 码值/别名里出现 'join'/'with' 是业务内容，不能误伤
        sql = 'SELECT a FROM t WHERE x = \'join with substr\' LIMIT 1'
        assert es_rejection(sql) is None


# ── 脱敏表达式：ES 只能用 SUBSTRING 三参 + CONCAT 两参 ────────────────────

class TestEsMasking:
    def test_full_mask_is_null(self):
        assert mask_expr('"x.keyword"', "full", "es") == "NULL"

    def test_partial_mask_uses_three_arg_substring(self):
        out = mask_expr('"x.keyword"', "partial", "es")
        assert "SUBSTRING(" in out and "SUBSTR(" not in out
        assert out.count("SUBSTRING(") == 2  # 首 2 + 尾 2

    def test_hash_mask_fails_loud(self):
        # ES 无 MD5/SHA/TO_HEX（实机 400）：不得静默放行原列，也不得换成别的形状
        with pytest.raises(ValueError) as exc:
            mask_expr('"x.keyword"', "hash", "es")
        assert "哈希" in str(exc.value)
