"""字典 target_table 回填/生成路径的分类与等价改写单测（不依赖元数据库）。

覆盖两处同口径逻辑：
- ontology_service._metric_safe_target：仅"合法单表聚合"才派生 target_table；
- scripts.backfill_dict_target_table._rewrite_inline_where：内联 WHERE → CASE 等价改写。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services.ontology_service import _metric_safe_target
from backend.modules.catalog.services.metrics_service import _derive_target_table
from backend.scripts.backfill_dict_target_table import _rewrite_inline_where, _split_agg_arg


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


class TestMetricsServiceDerive:
    def test_valid_agg_binds_unique_table(self):
        cur = _FakeCur([{"physical_table": "t_case_records"}])
        assert _derive_target_table(cur, "case", "COUNT(*)") == "t_case_records"

    def test_inline_where_rejected(self):
        cur = _FakeCur([{"physical_table": "t_case_records"}])
        assert _derive_target_table(cur, "case", "COUNT(*) WHERE x = 1") == ""

    def test_ambiguous_tables_rejected(self):
        cur = _FakeCur([{"physical_table": "t_a"}, {"physical_table": "t_b"}])
        assert _derive_target_table(cur, "case", "COUNT(*)") == ""

    def test_no_bound_object_rejected(self):
        cur = _FakeCur([])
        assert _derive_target_table(cur, "", "COUNT(*)") == ""


class TestMetricSafeTarget:
    def test_plain_count_binds_table(self):
        assert _metric_safe_target("COUNT(*)", "t_product_equipment") == "t_product_equipment"

    def test_sum_ratio_binds_table(self):
        assert _metric_safe_target("SUM(occupied_space_size) / 1073741824.0", "t_case_records") \
            == "t_case_records"

    def test_inline_where_not_bound(self):
        # 内联 WHERE 属需改写项，生成路径不直接激活（避免坏 SQL）
        assert _metric_safe_target("COUNT(*) WHERE status = 1", "t_user") == ""

    def test_boolean_flag_not_bound(self):
        assert _metric_safe_target("status = 1", "t_user") == ""

    def test_scalar_no_agg_not_bound(self):
        assert _metric_safe_target("total_amount / 100.0", "t_order") == ""

    def test_cross_table_not_bound(self):
        assert _metric_safe_target("COUNT(Case.id)", "t_product_equipment") == ""

    def test_subquery_not_bound(self):
        assert _metric_safe_target("SELECT COUNT(*) FROM t_x", "t_x") == ""

    def test_no_primary_table_not_bound(self):
        assert _metric_safe_target("COUNT(*)", "") == ""


class TestInlineWhereRewrite:
    def test_count_star(self):
        out = _rewrite_inline_where("COUNT(*) WHERE status >= 1 AND status <= 4")
        assert out == "COUNT(CASE WHEN status >= 1 AND status <= 4 THEN 1 END)"

    def test_avg_nested_paren(self):
        out = _rewrite_inline_where(
            "AVG(TIMESTAMPDIFF(HOUR, create_time, update_time)) WHERE case_status = 5")
        assert out == ("AVG(CASE WHEN case_status = 5 THEN "
                       "TIMESTAMPDIFF(HOUR, create_time, update_time) END)")

    def test_sum_uses_else_zero(self):
        out = _rewrite_inline_where("SUM(has_order) WHERE del_flag = 0")
        assert out == "SUM(CASE WHEN del_flag = 0 THEN has_order ELSE 0 END)"

    def test_no_where_returns_none(self):
        assert _rewrite_inline_where("COUNT(*)") is None

    def test_split_agg_arg_balanced(self):
        assert _split_agg_arg("COUNT(*)") == ("COUNT", "*")
        assert _split_agg_arg("AVG(TIMESTAMPDIFF(HOUR, a, b))") == \
            ("AVG", "TIMESTAMPDIFF(HOUR, a, b)")
        # 顶层闭合不在串尾 → 拒绝（FUNC(...) 后还有内容）
        assert _split_agg_arg("COUNT(*) + 1") is None
