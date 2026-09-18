"""语义层多方言 / 时间窗 / 函数模板指标 / SQL 模板绑定 —— 离线单测。

全部使用假数据注入(monkeypatch), 不触碰真实数据库:
  - planner 双方言快照编译(time_grain / time_window / 引用符 / filters / 表引用)
  - resolve_measure 的 formula_dialects 优先与缺方言 unsupported
  - SQL 模板渲染(required 缺失 / 未声明参数 / enum / date / 注入片段 / limit 钳制)
  - binding_resolver 透传 bind_kind / template_ref / db_type
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.shared.semantics import planner
from services.shared.semantics import binding_resolver
from services.shared.semantics.models import (
    Guardrail, ResolvedBinding, SemanticFilter, SemanticQuery,
)


# ── 测试夹具: 无 DB 的列/指标/维度 ──────────────────────────────

_PHYS = {"create_date", "status", "amount"}

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
    monkeypatch.setattr(planner._ColumnResolver, "physical_columns", lambda self: _PHYS)
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
        assert planner._dialect_of("elasticsearch") == "mysql"
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
        monkeypatch.setattr(planner._ColumnResolver, "physical_columns", lambda self: _PHYS)
        monkeypatch.setattr(planner._ColumnResolver, "_load_metrics", lambda self: metrics)
        monkeypatch.setattr(planner._ColumnResolver, "_load_dims", lambda self: _DIMS)
        planned = planner.plan(
            SemanticQuery(object="case", metrics=["funnel"]), b)
        assert any("不支持" in w or "未配置" in w for w in planned.warnings), planned.warnings


# ── SQL 模板渲染 ────────────────────────────────────────────────

def _tpl_row(sql, variables, dialect="generic"):
    return {
        "template_id": "tpl-1", "template_name": "demo",
        "sql_template": sql, "variables": variables, "tpl_dialect": dialect,
    }


def _tpl_binding(guardrail=None, template_ref="tpl-1"):
    return _bind(
        "mysql", bind_kind="sql_template", template_ref=template_ref,
        physical_table="", guardrail=guardrail or Guardrail(),
    )


class TestTemplateRendering:
    _SQL = "SELECT * FROM events WHERE status = ${status} AND dt >= ${dt} AND id IN (${ids})"
    _VARS = [
        {"name": "status", "type": "string"},
        {"name": "dt", "type": "date", "required": True},
        {"name": "ids", "type": "list", "item_type": "int"},
    ]

    def _patch(self, monkeypatch, row):
        monkeypatch.setattr(planner, "_load_template_row", lambda ref, w: row)

    def test_success_render(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        q = SemanticQuery(object="case", params={
            "status": "open", "dt": "2024-01-01", "ids": [1, 2, 3],
        })
        planned = planner.plan(q, _tpl_binding())
        assert planned.sql.startswith("SELECT * FROM (")
        assert "status = 'open'" in planned.sql
        assert "dt >= '2024-01-01'" in planned.sql
        assert "id IN (1, 2, 3)" in planned.sql
        assert planned.provenance.get("template_ref") == "tpl-1"

    def test_missing_required_rejected(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        q = SemanticQuery(object="case", params={"status": "open", "ids": [1]})
        planned = planner.plan(q, _tpl_binding())
        assert planned.sql == ""
        assert any("dt" in w for w in planned.warnings)

    def test_undeclared_param_rejected(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        q = SemanticQuery(object="case", params={
            "status": "open", "dt": "2024-01-01", "ids": [1], "evil": "x",
        })
        planned = planner.plan(q, _tpl_binding())
        assert planned.sql == ""
        assert any("evil" in w for w in planned.warnings)

    def test_enum_value_enforced(self, monkeypatch):
        sql = "SELECT * FROM t WHERE c = ${col}"
        vars_ = [{"name": "col", "type": "enum", "values": ["a", "b"]}]
        self._patch(monkeypatch, _tpl_row(sql, vars_))
        ok = planner.plan(SemanticQuery(object="case", params={"col": "a"}), _tpl_binding())
        assert "c = 'a'" in ok.sql
        bad = planner.plan(SemanticQuery(object="case", params={"col": "zz"}), _tpl_binding())
        assert bad.sql == ""

    def test_date_format_enforced(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        q = SemanticQuery(object="case", params={
            "status": "open", "dt": "2024/01/01", "ids": [1],
        })
        planned = planner.plan(q, _tpl_binding())
        assert planned.sql == ""

    def test_string_injection_rejected(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        q = SemanticQuery(object="case", params={
            "status": "open; DROP TABLE x", "dt": "2024-01-01", "ids": [1],
        })
        planned = planner.plan(q, _tpl_binding())
        assert planned.sql == ""
        assert any("非法片段" in w for w in planned.warnings)

    def test_comment_injection_rejected(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        q = SemanticQuery(object="case", params={
            "status": "open ---x", "dt": "2024-01-01", "ids": [1],
        })
        assert planner.plan(q, _tpl_binding()).sql == ""

    def test_dialect_mismatch_rejected(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS, dialect="postgres"))
        q = SemanticQuery(object="case", params={
            "status": "open", "dt": "2024-01-01", "ids": [1],
        })
        planned = planner.plan(q, _tpl_binding())   # binding mysql 方言
        assert planned.sql == ""
        assert any("不匹配" in w for w in planned.warnings)

    def test_limit_clamped_by_guardrail(self, monkeypatch):
        self._patch(monkeypatch, _tpl_row(self._SQL, self._VARS))
        g = Guardrail(max_rows=50)
        q = SemanticQuery(object="case", limit=9999, params={
            "status": "open", "dt": "2024-01-01", "ids": [1],
        })
        planned = planner.plan(q, _tpl_binding(guardrail=g))
        assert "LIMIT 50" in planned.sql
        assert any("clamp" in w or "max_rows" in w for w in planned.warnings)

    def test_missing_template_ref_rejected(self, monkeypatch):
        q = SemanticQuery(object="case", params={})
        planned = planner.plan(q, _tpl_binding(template_ref=""))
        assert planned.sql == ""
        assert any("template_ref" in w for w in planned.warnings)


# ── binding_resolver 透传 db_type / template_ref ────────────────

class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "adh_ontology_bindings" in s:
            self._row = self.conn.binding_row
        elif s.startswith("SELECT id FROM adh_datasources WHERE name"):
            self._row = {"id": self.conn.live_id}
        elif s.startswith("SELECT db_type FROM adh_datasources"):
            self._row = {"db_type": self.conn.db_type}
        elif s.startswith("SELECT name FROM adh_datasources WHERE id"):
            self._row = {"name": self.conn.ds_name}
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, binding_row, live_id, db_type, ds_name):
        self.binding_row = binding_row
        self.live_id = live_id
        self.db_type = db_type
        self.ds_name = ds_name

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        pass


class TestBindingResolverPassthrough:
    def _run(self, row, live_id, db_type):
        conn = _FakeConn(row, live_id, db_type, ds_name=row.get("datasource_name", ""))
        return binding_resolver._resolve_from_bindings(
            conn, row["object_key"], row.get("datasource_id", 0),
            row.get("datasource_name", ""), None, [])

    def test_sql_template_passthrough(self):
        row = {
            "object_key": "MyTpl", "model_id": 1, "datasource_id": 7,
            "datasource_name": "pgds", "physical_table": "", "catalog_ref": "",
            "bind_kind": "sql_template", "template_ref": "tpl-9",
            "sync_state": "bound",
        }
        b = self._run(row, live_id=42, db_type="postgresql")
        assert b.bind_kind == "sql_template"
        assert b.template_ref == "tpl-9"
        assert b.db_type == "postgresql"
        assert b.datasource_id == 42          # name->实时 id

    def test_primary_mysql_default_dbtype(self):
        row = {
            "object_key": "case", "model_id": None, "datasource_id": 7,
            "datasource_name": "mysqlds", "physical_table": "t_case",
            "catalog_ref": "", "bind_kind": "primary", "template_ref": None,
            "sync_state": "bound",
        }
        b = self._run(row, live_id=7, db_type="mysql")
        assert b.bind_kind == "primary"
        assert b.template_ref == ""
        assert b.db_type == "mysql"
        assert b.physical_table == "t_case"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
