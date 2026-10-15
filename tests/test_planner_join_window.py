"""B2: planner 跨对象 JOIN 编译与结构化窗口（内聚外窗两层）回归。

锁定的行为：
1. 限定业务名(对象名.业务名)经 links 解链 JOIN(≤2 跳)，join 表达式表名→别名改写；
2. 无关联路径回抛告警并跳过该名字，绝不硬造 join（宁缺勿错）；
3. 结构化窗口编译为「内层聚合 + 外层窗口」两层结构，配置非法 fail-loud 空 SQL；
4. 单对象路径零回归（不出现 JOIN）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.eval.adapters.compile import _patched_resolver_catalog
from backend.eval.fixtures import demo_semantics as demo
from backend.semantics import planner
from backend.semantics.models import SemanticQuery


def _plan(intent: dict):
    q = SemanticQuery.model_validate(intent)
    binding = demo.binding_for(1, q.object)
    with _patched_resolver_catalog():
        return planner.plan(q, binding)


class TestCrossObjectJoin:
    def test_qualified_dim_compiles_join(self):
        p = _plan({"object": "case", "metrics": ["案例数量"],
                   "dimensions": ["客户.行业"], "limit": 10})
        assert p.sql, p.warnings
        assert "LEFT JOIN t_customer AS t1" in p.sql
        assert "t_case_records.company_name = t1.name" in p.sql   # join 表达式表名→别名
        assert "t1.`industry`" in p.sql                           # 列引用带表别名
        assert "客户_行业" in p.sql                                 # 限定名别名'.'→'_'
        assert p.provenance["resolution_sources"]["客户.行业"] == "dict_exact"

    def test_two_hops_chain(self):
        p = _plan({"object": "case", "metrics": ["案例数量"],
                   "dimensions": ["地区.区域名"], "limit": 10})
        assert p.sql, p.warnings
        assert "LEFT JOIN t_customer AS t1" in p.sql
        assert "LEFT JOIN t_region AS t2" in p.sql
        assert "t1.region_code = t2.region_code" in p.sql

    def test_no_path_is_rejected_not_guessed(self):
        p = _plan({"object": "case", "metrics": ["案例数量"],
                   "dimensions": ["订单.金额"], "limit": 10})
        assert p.sql                                   # 主指标照常编译
        assert any("无关联路径" in w for w in p.warnings)
        assert "JOIN" not in p.sql                     # 绝不硬造 join

    def test_single_object_path_unchanged(self):
        p = _plan({"object": "case", "metrics": ["总费用"],
                   "dimensions": ["就诊医院"], "limit": 10})
        assert p.sql
        assert "JOIN" not in p.sql
        assert "AS `总费用`" in p.sql

    def test_qualified_metric_formula_is_qualified(self):
        p = _plan({"object": "case", "metrics": ["客户.客户数"],
                   "dimensions": ["就诊医院"], "limit": 10})
        assert p.sql, p.warnings
        # 公式文本中的裸列名补表别名(保持公式原书写形态, 不强制加引号)
        assert "COUNT(DISTINCT t1.name)" in p.sql
        assert "LEFT JOIN t_customer AS t1" in p.sql

    def test_join_qualifier_written_as_display_name_is_rewritten(self):
        """建模常以对象显示名(Case/Customer)书写 links.join, 而非物理表名;
        改写必须同时识别显示名(与物理表名/对象 key 按 identity 归一), 不得把
        Case./Customer. 残留进 ON 子句(否则被数据源拒 Unknown column 并误报数据源不可用)。"""
        orig = list(demo.LINKS["edges"].get("case") or [])
        try:
            demo.LINKS["edges"]["case"] = [("customer", "Case.company_name = Customer.name")]
            p = _plan({"object": "case", "metrics": ["案例数量"],
                       "dimensions": ["客户.行业"], "limit": 10})
            assert p.sql, p.warnings
            assert "LEFT JOIN t_customer AS t1" in p.sql
            assert "t_case_records.company_name = t1.name" in p.sql
            assert "Case." not in p.sql and "Customer." not in p.sql   # 显示名限定已全部改写
        finally:
            demo.LINKS["edges"]["case"] = orig

    def test_unresolvable_join_qualifier_is_skipped_not_guessed(self):
        """join 条件引用了未参与本次关联/未建模的对象(Membership)时, 不得硬造 JOIN
        生成非法 SQL, 而应跳过该关联 + 显式告警(no-silent-degradation); 主指标照常编译。"""
        orig = list(demo.LINKS["edges"].get("case") or [])
        try:
            demo.LINKS["edges"]["case"] = [("customer", "t_case_records.user_code = Membership.user_code")]
            p = _plan({"object": "case", "metrics": ["案例数量"],
                       "dimensions": ["客户.行业"], "limit": 10})
            assert p.sql                                   # 主指标(案例数量)仍可编译
            assert any("无法解析" in w or "跳过" in w for w in p.warnings)
            assert "JOIN" not in p.sql                     # 关联不可用: 不硬造, 不留悬空别名
        finally:
            demo.LINKS["edges"]["case"] = orig

    def test_multihop_arrow_placeholder_is_skipped_not_guessed(self):
        """建模把多跳路径塑进单条 link 的 join(用 → 连接), 不是合法单条 ON 条件;
        必须显式跳过该关联并告警, 不得将 → 直出进 SQL。"""
        orig = list(demo.LINKS["edges"].get("case") or [])
        try:
            demo.LINKS["edges"]["case"] = [
                ("customer", "t_case_records.user_code = User.user_code → User.code = t_customer.code")]
            p = _plan({"object": "case", "metrics": ["案例数量"],
                       "dimensions": ["客户.行业"], "limit": 10})
            assert p.sql
            assert "→" not in p.sql
            assert any("跳过" in w for w in p.warnings)
        finally:
            demo.LINKS["edges"]["case"] = orig


class TestStructuredWindow:
    def test_rank_two_level(self):
        p = _plan({"object": "case", "metrics": ["案例数量"],
                   "dimensions": ["就诊医院"],
                   "order": [{"by": "案例数量", "desc": True}],
                   "window": {"type": "rank"}, "limit": 10})
        assert p.sql, p.warnings
        assert "RANK() OVER" in p.sql
        inner, _, outer = p.sql.partition(") AS _agg")
        assert "GROUP BY" in inner                     # 内层聚合
        assert "ORDER BY `案例数量` DESC" in outer      # 外层排序/限流
        assert "LIMIT 10" in outer

    def test_rolling_window(self):
        p = _plan({"object": "case", "metrics": ["总费用"],
                   "dimensions": ["创建日期"], "time_grain": "day",
                   "window": {"type": "rolling", "metric": "总费用",
                              "params": {"preceding": 3}}, "limit": 10})
        assert p.sql, p.warnings
        assert "SUM(`总费用`)" in p.sql
        assert "ROWS BETWEEN 3 PRECEDING AND CURRENT ROW" in p.sql
        assert "_rolling" in p.sql

    def test_period_compare(self):
        p = _plan({"object": "case", "metrics": ["总费用"],
                   "dimensions": ["创建日期"], "time_grain": "day",
                   "window": {"type": "period_compare", "metric": "总费用",
                              "params": {"lag": 1}}, "limit": 10})
        assert p.sql, p.warnings
        assert "LAG(`总费用`, 1)" in p.sql
        assert "_prev" in p.sql and "_pct" in p.sql

    def test_share(self):
        p = _plan({"object": "case", "metrics": ["总费用"],
                   "dimensions": ["就诊医院"],
                   "window": {"type": "share", "metric": "总费用"}, "limit": 10})
        assert p.sql, p.warnings
        assert "/ NULLIF(SUM(" in p.sql
        assert "_share" in p.sql

    def test_rolling_without_time_fails_loud(self):
        p = _plan({"object": "case", "metrics": ["总费用"],
                   "dimensions": ["就诊医院"],
                   "window": {"type": "rolling", "metric": "总费用"}})
        assert p.sql == ""                             # fail-loud: 不静默忽略窗口
        assert any("window.rolling 需要时间维度" in w for w in p.warnings)

    def test_window_metric_unresolved_fails_loud(self):
        p = _plan({"object": "case", "metrics": ["案例数量"],
                   "dimensions": ["就诊医院"],
                   "window": {"type": "share", "metric": "不存在"}})
        assert p.sql == ""
        assert any("需要已解析的目标指标" in w for w in p.warnings)
