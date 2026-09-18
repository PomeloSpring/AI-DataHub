"""planner 维度/指标解析增强单测: 别名索引、枚举 CASE、time_grain 范围、did-you-mean。

全部以内存字典 monkeypatch _ColumnResolver 的三个 _load_*, 不依赖元数据库。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.shared.semantics import planner
from services.shared.semantics.planner import _ColumnResolver, plan, _norm_name
from services.shared.semantics.models import (
    ResolvedBinding, SemanticQuery, SemanticFilter,
)


PHYS = {
    "create_time": {"data_type": "datetime", "business_desc": "创建时间"},
    "scan_time": {"data_type": "datetime", "business_desc": "扫描时间"},
    "case_status": {"data_type": "int", "business_desc": "案例状态"},
    "company_name": {"data_type": "varchar", "business_desc": "医院名称"},
    "patient_phone": {"data_type": "varchar", "business_desc": "患者手机号"},
    "del_flag": {"data_type": "int", "business_desc": ""},
}
DIMS = {
    "创建日期": {"name": "创建日期", "name_en": "create_date", "target_column": "create_time",
               "category": "时间", "aliases": ["创建时间", "case created at"], "value_labels": {}},
    "案例状态": {"name": "案例状态", "name_en": "case_status", "target_column": "case_status",
               "category": "状态", "aliases": [],
               "value_labels": {"0": "仅表单", "1": "需要处理", "3": "已上传"}},
    "就诊医院": {"name": "就诊医院", "name_en": "hospital", "target_column": "company_name",
               "category": "组织", "aliases": ["医院"], "value_labels": {}},
}
METRICS = {
    "案例数量": {"name": "案例数量", "name_en": "case_count", "formula": "COUNT(*)",
               "formula_dsl": None, "agg_type": "COUNT", "default_agg": "COUNT",
               "unit": "个", "description": "", "aliases": ["病例数", "total_cases"],
               "formula_dialects": None},
}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(_ColumnResolver, "_load_phys",
                        lambda self: dict(PHYS), raising=True)
    monkeypatch.setattr(_ColumnResolver, "_load_dims",
                        lambda self: dict(DIMS), raising=True)
    monkeypatch.setattr(_ColumnResolver, "_load_metrics",
                        lambda self: dict(METRICS), raising=True)


@pytest.fixture
def binding():
    return ResolvedBinding(object_key="case", datasource_id=1,
                           physical_table="t_case_records", db_name="stardb")


def _resolver(binding, dialect="mysql"):
    return _ColumnResolver(binding, dialect=dialect)


# ── 别名解析 ────────────────────────────────────────────────────

class TestAliasResolution:
    def test_dim_alias_hit(self, patched, binding):
        r = _resolver(binding)
        expr, role = r.resolve_dimension("创建时间", time_grain="day")
        assert expr == "DATE_FORMAT(`create_time`, '%Y-%m-%d')"
        assert role == "time"

    def test_dim_name_en_hit(self, patched, binding):
        r = _resolver(binding)
        expr, _ = r.resolve_dimension("case_status")
        assert "CASE" in expr  # 命中枚举字典行

    def test_phys_business_desc_hit(self, patched, binding):
        # "扫描时间" 不在字典, 但物理列 business_desc 可解析(不对外展示)
        r = _resolver(binding)
        expr, role = r.resolve_dimension("扫描时间", time_grain="day")
        assert expr == "DATE_FORMAT(`scan_time`, '%Y-%m-%d')"

    def test_normalization_width_case(self, patched, binding):
        assert _norm_name("ＣＲＥＡＴＥ_ＴＩＭＥ ") == "create_time"
        r = _resolver(binding)
        expr, _ = r.resolve_dimension(" CREATE_TIME ")
        assert expr == "`create_time`"

    def test_metric_alias_hit(self, patched, binding):
        r = _resolver(binding)
        expr, agg, unit, unsupport = r.resolve_measure("病例数")
        assert expr == "COUNT(*)" and agg == "COUNT" and not unsupport

    def test_ambiguous_fuzzy_rejected(self, patched, binding):
        # "医院" 同时是别名(就诊医院)与更长的其他键包含关系 → 精确命中优先, 不报错
        r = _resolver(binding)
        expr, _ = r.resolve_dimension("医院")
        assert expr == "`company_name`"


# ── time_grain 生效范围 ────────────────────────────────────────

class TestTimeGrainScope:
    def test_enum_dim_not_wrapped(self, patched, binding):
        r = _resolver(binding)
        expr, role = r.resolve_dimension("case_status", time_grain="day")
        assert "DATE_FORMAT" not in expr
        assert expr.startswith("CASE `case_status`")

    def test_plain_dim_not_wrapped(self, patched, binding):
        r = _resolver(binding)
        expr, role = r.resolve_dimension("就诊医院", time_grain="day")
        assert expr == "`company_name`" and role == "dimension"


# ── 枚举标签 CASE 与过滤反查 ───────────────────────────────────

class TestEnumLabels:
    def test_select_group_by_same_case_expr(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["案例数量"], dimensions=["案例状态"], limit=50)
        p = plan(q, binding)
        select_line = p.sql.splitlines()[0]
        assert "WHEN '3' THEN '已上传'" in select_line
        assert "ELSE CAST(`case_status` AS CHAR) END" in select_line
        group_line = [l for l in p.sql.splitlines() if l.startswith("GROUP BY")][0]
        # SELECT 中维度表达式与 GROUP BY 完全一致(用度量起始处切片, 避开 CAST 的 AS)
        sel_expr = select_line[len("SELECT "):select_line.index(", COUNT(")]
        sel_expr = sel_expr[:sel_expr.rindex(" AS ")]  # 去掉输出别名
        assert group_line[len("GROUP BY "):] == sel_expr

    def test_filter_label_to_code(self, patched, binding):
        r = _resolver(binding)
        cond = r.render_filter(SemanticFilter(dim="案例状态", op="eq", value="已上传"))
        assert cond == "`case_status` = '3'"

    def test_filter_raw_code_passthrough(self, patched, binding):
        r = _resolver(binding)
        cond = r.render_filter(SemanticFilter(dim="案例状态", op="in",
                                              values=["0", "1"]))
        assert cond == "`case_status` IN ('0', '1')"

    def test_filter_in_values_label(self, patched, binding):
        r = _resolver(binding)
        cond = r.render_filter(SemanticFilter(dim="案例状态", op="in",
                                              values=["仅表单", "已上传"]))
        assert cond == "`case_status` IN ('0', '3')"

    def test_pg_dialect_label_case(self, patched, binding):
        r = _resolver(binding, dialect="postgres")
        expr, _ = r.resolve_dimension("案例状态")
        assert 'CASE "case_status"' in expr
        assert 'ELSE "case_status"::text END' in expr
        assert "::text" in expr


# ── did-you-mean 提示安全 ──────────────────────────────────────

class TestDidYouMean:
    def test_unknown_dim_lists_dict_names_only(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["案例数量"],
                          dimensions=["不存在的维度"], limit=10)
        p = plan(q, binding)
        msg = [w for w in p.warnings if "无法解析维度" in w][0]
        assert "创建日期" in msg and "案例状态" in msg
        # 不暴露物理表/列
        assert "patient_phone" not in msg and "t_case_records" not in msg

    def test_suggest_dimensions_contains_aliases(self, patched, binding):
        r = _resolver(binding)
        s = " ".join(r.suggest_dimensions())
        assert "创建时间" in s  # 别名提示可见

    def test_available_dimensions_in_provenance(self, patched, binding):
        q = SemanticQuery(object="case", dimensions=["x"], limit=10)
        p = plan(q, binding)
        assert set(p.provenance["available_dimensions"]) >= {"创建日期", "案例状态", "就诊医院"}

    def test_unresolvable_filter_hint(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["案例数量"],
                          filters=[SemanticFilter(dim="薪资", op="gt", value=1)], limit=10)
        p = plan(q, binding)
        msg = [w for w in p.warnings if "无法解析过滤维度" in w][0]
        assert "salary" not in msg and "patient_phone" not in msg

    def test_metric_unresolved_hint(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["利润率"], limit=10)
        p = plan(q, binding)
        msg = [w for w in p.warnings if "无法解析指标" in w][0]
        assert "案例数量" in msg


# ── time_window 时间列定位 ─────────────────────────────────────

class TestTimeWindow:
    def test_explicit_time_column_alias(self, patched, binding):
        r = _resolver(binding)
        warns = []
        cond = r.render_time_window("7d", "创建时间", warns)
        assert "`create_time` >= DATE_SUB(NOW(), INTERVAL 7 DAY)" == cond
        assert not warns

    def test_dict_time_dim_fallback(self, patched, binding):
        r = _resolver(binding)
        cond = r.render_time_window("7d", None, [])
        assert "`create_time`" in cond  # 字典 category=时间 优先

    def test_missing_time_dim_hint_no_physical(self, patched, binding, monkeypatch):
        r = _resolver(binding)
        dims_no_time = {k: dict(v, category="属性") for k, v in DIMS.items()}
        monkeypatch.setattr(r, "_load_dims", lambda: dict(dims_no_time))
        monkeypatch.setattr(r, "_load_phys",
                            lambda: {"case_status": {"data_type": "int", "business_desc": ""},
                                     "del_flag": {"data_type": "int", "business_desc": ""}})
        warns = []
        cond = r.render_time_window("7d", None, warns)
        assert cond is None
        assert warns and "time_column" in warns[0]
        assert "t_case_records" not in warns[0]  # 不暴露物理表名
