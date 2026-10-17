"""语义层多方言 / 时间窗 / 函数模板指标 —— 离线单测。

全部使用假数据注入(monkeypatch), 不触碰真实数据库:
  - planner 双方言快照编译(time_grain / time_window / 引用符 / filters / 表引用)
  - resolve_measure 的 formula_dialects 引擎优先与缺方言 unsupported
预制 SQL(数据集)的渲染/校验/编译用例见 `test_dataset_sql_governance.py`。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.semantics import planner
from backend.semantics.models import (
    Guardrail, ResolvedBinding, SemanticFilter, SemanticQuery,
)

# ── 测试夹具: 无 DB 的列/指标/维度 ──────────────────────────────

# 物理列元数据(离线夹具): _dim_index 实际读 _load_phys, 两个入口都要打桩
_PHYS = {
    "create_date": {"data_type": "datetime", "business_desc": "创建日期"},
    "status": {"data_type": "varchar", "business_desc": "状态"},
    "amount": {"data_type": "decimal", "business_desc": "金额"},
}

_METRICS = {
    "case_count": {
        "name": "case_count", "name_en": "", "formula": "COUNT(*)",
        "formula_dsl": None, "agg_type": "COUNT", "default_agg": None,
        "unit": "件", "formula_dialects": None,
    },
}

_DIMS = {
    "create_date": {
        "name": "create_date", "name_en": "",
        "target_column": "create_date", "category": "时间",
    },
}

def _bind(db_type="mysql", **kw):
    defaults = dict(
        object_key="case", datasource_id=7, physical_table="t_case",
        db_name="stardb", bind_kind="primary", db_type=db_type,
    )
    defaults.update(kw)
    return ResolvedBinding(**defaults)

def _patch_resolver(monkeypatch):
    monkeypatch.setattr(planner._ColumnResolver, "physical_columns", lambda self: set(_PHYS))
    monkeypatch.setattr(planner._ColumnResolver, "_load_phys", lambda self: dict(_PHYS))
    monkeypatch.setattr(planner._ColumnResolver, "_load_metrics", lambda self: _METRICS)
    monkeypatch.setattr(planner._ColumnResolver, "_load_dims", lambda self: _DIMS)

# ── 方言基础设施 ────────────────────────────────────────────────

class TestDialectSelection:
    def test_dialect_of_mapping(self):
        assert planner._dialect_of("mysql") == "mysql"
        assert planner._dialect_of("doris") == "mysql"
        assert planner._dialect_of("postgres") == "postgres"
        assert planner._dialect_of("postgresql") == "postgres"
        assert planner._dialect_of("pg") == "postgres"
        assert planner._dialect_of("sls") == "postgres"

    def test_dialect_of_unknown_falls_back(self):
        # 未知协议保守回落 mysql(planner 会另出 warning)
        assert planner._dialect_of("clickhouse") == "mysql"
        assert planner._dialect_of("") == "mysql"

    def test_quote_ident(self):
        assert planner._quote_ident("col", "mysql") == "`col`"
        assert planner._quote_ident("col", "postgres") == '"col"'
        # 表达式(非纯标识符)原样返回
        assert planner._quote_ident("COUNT(*)", "mysql") == "COUNT(*)"

# ── planner 双方言快照编译 ──────────────────────────────────────

class TestPlannerDialectCompilation:
    def _query(self, **kw):
        base = dict(
            object="case", metrics=["case_count"], dimensions=["create_date"],
            time_grain="day", filters=[SemanticFilter(dim="status", op="eq", value="open")],
            limit=10,
        )
        base.update(kw)
        return SemanticQuery(**base)

    def test_mysql_snapshot(self, monkeypatch):
        _patch_resolver(monkeypatch)
        planned = planner.plan(self._query(), _bind("mysql"))
        assert planned.dialect == "mysql"
        sql = planned.sql
        assert "DATE_FORMAT(`create_date`, '%Y-%m-%d') AS `create_date`" in sql
        assert "COUNT(*) AS `case_count`" in sql
        assert "FROM stardb.t_case" in sql           # MySQL 走 db.table 二段式
        assert "WHERE `status` = 'open'" in sql
        assert "GROUP BY DATE_FORMAT(`create_date`, '%Y-%m-%d')" in sql
        assert "LIMIT 10" in sql

    def test_postgres_snapshot(self, monkeypatch):
        _patch_resolver(monkeypatch)
        planned = planner.plan(self._query(), _bind("postgresql"))
        assert planned.dialect == "postgres"
        sql = planned.sql
        assert 'to_char("create_date", \'YYYY-MM-DD\') AS "create_date"' in sql
        assert '"status" = \'open\'' in sql
        assert 'FROM "t_case"' in sql                 # PG 用裸双引号表名(不拼 db.table)

    def test_time_grain_week_quarter_both_dialects(self, monkeypatch):
        _patch_resolver(monkeypatch)
        my = planner.plan(self._query(time_grain="week"), _bind("mysql"))
        assert "DATE_FORMAT(`create_date`, '%x-%v')" in my.sql
        myq = planner.plan(self._query(time_grain="quarter"), _bind("doris"))
        assert "DATE_FORMAT(`create_date`, '%Y-Q%q')" in myq.sql
        pg = planner.plan(self._query(time_grain="week"), _bind("postgresql"))
        assert 'to_char("create_date", \'IYYY-IW\')' in pg.sql
        pgq = planner.plan(self._query(time_grain="quarter"), _bind("postgresql"))
        assert 'to_char("create_date", \'YYYY-"Q"Q\')' in pgq.sql

# ── 相对时间窗 ──────────────────────────────────────────────────

class TestTimeWindow:
    def _q(self, window, col=None):
        kw = dict(object="case", metrics=["case_count"], time_window=window)
        if col:
            kw["time_column"] = col
        return SemanticQuery(**kw)

    def test_mysql_window_uses_date_sub(self, monkeypatch):
        _patch_resolver(monkeypatch)
        planned = planner.plan(self._q("7d"), _bind("mysql"))
        assert "`create_date` >= DATE_SUB(NOW(), INTERVAL 7 DAY)" in planned.sql

    def test_pg_window_uses_interval(self, monkeypatch):
        _patch_resolver(monkeypatch)
        planned = planner.plan(self._q("2w"), _bind("postgresql"))
        assert 'now() - INTERVAL \'2 weeks\'' in planned.sql

    def test_window_and_filter_combined(self, monkeypatch):
        _patch_resolver(monkeypatch)
        q = SemanticQuery(
            object="case", metrics=["case_count"], time_window="24h",
            filters=[SemanticFilter(dim="status", op="eq", value="open")],
        )
        planned = planner.plan(q, _bind("mysql"))
        assert "`status` = 'open'" in planned.sql
        assert "`create_date` >= DATE_SUB(NOW(), INTERVAL 24 HOUR)" in planned.sql
        # 两谓词以 AND 叠加
        where = planned.sql.split("WHERE", 1)[1].split("GROUP BY")[0]
        assert " AND " in where

    def test_explicit_time_column(self, monkeypatch):
        _patch_resolver(monkeypatch)
        planned = planner.plan(self._q("30m", col="status"), _bind("mysql"))
        assert "`status` >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)" in planned.sql

# ── 函数模板指标: formula_dialects ──────────────────────────────

class TestFormulaDialects:
    def _resolver(self, dialect, formula_dialects, formula="SUM(amount)"):
        b = _bind(dialect)
        r = planner._ColumnResolver(b, dialect=dialect)
        r._metrics = {
            "m": {
                "name": "m", "name_en": "", "formula": formula, "formula_dsl": None,
                "agg_type": "SUM", "default_agg": None, "unit": "",
                "formula_dialects": formula_dialects,
            },
        }
        return r

    def test_dialect_expr_takes_priority(self):
        r = self._resolver("doris" if False else "mysql",
                           {"mysql": "window_funnel(30,'strict')(a,b)=2"})
        expr, agg, unit, unsupported = r.resolve_measure("m")
        assert expr == "window_funnel(30,'strict')(a,b)=2"
        assert unsupported is False

    def test_explicit_null_marks_unsupported(self):
        # 声明了 mysql 但值为 null -> 显式不支持本方言
        r = self._resolver("mysql", {"mysql": None, "postgres": "x"})
        expr, agg, unit, unsupported = r.resolve_measure("m")
        assert expr is None
        assert unsupported is True

    def test_missing_dialect_key_falls_back_to_formula(self):
        # formula_dialects 里没有当前 dialect 键 -> 回落通用 formula
        r = self._resolver("postgres", {"mysql": "only_mysql_expr"}, formula="SUM(amount)")
        expr, agg, unit, unsupported = r.resolve_measure("m")
        assert expr == "SUM(amount)"
        assert unsupported is False

    def test_unsupported_warning_surfaces_in_plan(self, monkeypatch):
        b = _bind("mysql")
        metrics = {
            "funnel": {
                "name": "funnel", "name_en": "", "formula": "", "formula_dsl": None,
                "agg_type": "", "default_agg": None, "unit": "",
                "formula_dialects": {"doris": "window_funnel(30)(a,b)=2", "mysql": None},
            },
        }
        monkeypatch.setattr(planner._ColumnResolver, "physical_columns", lambda self: set(_PHYS))
        monkeypatch.setattr(planner._ColumnResolver, "_load_phys", lambda self: dict(_PHYS))
        monkeypatch.setattr(planner._ColumnResolver, "_load_metrics", lambda self: metrics)
        monkeypatch.setattr(planner._ColumnResolver, "_load_dims", lambda self: _DIMS)
        planned = planner.plan(
            SemanticQuery(object="case", metrics=["funnel"]), b)
        assert any("不支持" in w or "未配置" in w for w in planned.warnings), planned.warnings

class TestAliasQuoting:
    """业务名可能含 ()/% 等字符（如「层间转化率(%)」）：AS/ORDER BY 右侧是名字不是表达式，
    必须始终包引号 —— 走表达式透传分支会生成 `AS 层间转化率(%)` 这种非法 SQL。
    """

    def _plan(self, monkeypatch, name, db_type):
        b = _bind(db_type)
        metrics = {name: {"name": name, "name_en": "", "formula": "SUM(amount)",
                         "formula_dsl": None, "agg_type": "SUM", "default_agg": None,
                         "unit": "", "formula_dialects": None}}
        monkeypatch.setattr(planner._ColumnResolver, "physical_columns", lambda self: set(_PHYS))
        monkeypatch.setattr(planner._ColumnResolver, "_load_phys", lambda self: dict(_PHYS))
        monkeypatch.setattr(planner._ColumnResolver, "_load_metrics", lambda self: metrics)
        monkeypatch.setattr(planner._ColumnResolver, "_load_dims", lambda self: _DIMS)
        return planner.plan(
            SemanticQuery(object="case", metrics=[name],
                          order=[{"by": name, "desc": True}]), b)

    def test_mysql_alias_quoted(self, monkeypatch):
        planned = self._plan(monkeypatch, "层间转化率(%)", "mysql")
        assert "AS `层间转化率(%)`" in planned.sql
        assert "ORDER BY `层间转化率(%)` DESC" in planned.sql

    def test_pg_alias_quoted(self, monkeypatch):
        planned = self._plan(monkeypatch, "层间转化率(%)", "postgres")
        assert 'AS "层间转化率(%)"' in planned.sql
        assert 'ORDER BY "层间转化率(%)" DESC' in planned.sql

    def test_plain_chinese_alias_still_quoted(self, monkeypatch):
        planned = self._plan(monkeypatch, "到达用户数", "mysql")
        assert "AS `到达用户数`" in planned.sql


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

