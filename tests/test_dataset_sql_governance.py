"""查询数据集 = 平台内唯一"可信任预制 SQL"通道 —— 注册校验 / 方言正文 / 下钻 / 治理（离线单测）。

覆盖三处此前无人守的门：
1. **注册侧**（`viz/services/dataset_registry`）：正文只读校验（拒 DDL/DML/多语句）、
   `sql_bodies` 方言键合法性（拒永不命中的死键）、参数声明可试渲染、
   `field_config` 度量表达式只允许引用已声明列；
2. **编译侧**（`semantics/dataset_compiler`）：engine → family → generic 取正文且
   **不回退其他方言**；静态提表进 `resolved_tables` 使预制 SQL 同样受 RLS 改写；
   声明字段后 `dimensions/measures/filters/time_grain` 在结果上再编译一层聚合；
3. **语义层绑定**（`planner._plan_dataset`）：数据集配了行级 scope 时拒绝作为对象绑定
   （scope 只在 query_dataset 通道按身份施加，静默绕过 = 少一层过滤）。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.viz.services import dataset_registry as reg
from backend.semantics import planner
from backend.semantics.dataset_compiler import DatasetCompileError, compile_dataset
from backend.semantics.models import Guardrail, ResolvedBinding, SemanticQuery

# ── 夹具：跨引擎漏斗数据集 ──────────────────────────────────────
# 分工：正文只负责"按实体判序"（两级聚合的内层，输出 实体×已达步序 明细），
# "去重计数 + 维度下钻"交给外层编译 —— 一条数据集服务所有步骤/渠道/日期切片。

_PER_ENTITY = ("SELECT uid, "
               "MIN(CASE WHEN event_name = 'item' THEN event_time END) AS t_item, "
               "MIN(CASE WHEN event_name = 'pay' THEN event_time END) AS t_pay, "
               "MAX(channel) AS channel, MAX(stat_date) AS stat_date "
               "FROM t_rum WHERE stat_date >= ${start_date} GROUP BY uid")
_LEVELS = ("SELECT uid, CASE WHEN t_pay IS NOT NULL AND t_pay >= t_item "
           "AND t_pay <= t_item + INTERVAL ${window_sec} SECOND THEN 2 ELSE 1 END AS lvl, "
           "channel, stat_date FROM (" + _PER_ENTITY + ") e WHERE t_item IS NOT NULL")


def _funnel_body(table: str = "t_rum") -> str:
    """可移植漏斗正文（MySQL 8 / PG / Doris 同构，不依赖引擎专有函数）。"""
    return ("SELECT e.uid AS entity, s.step_order AS step_order, "
            "e.channel AS channel, e.stat_date AS stat_date FROM ("
            + _LEVELS.replace("t_rum", table) + ") e "
            "JOIN (SELECT 1 AS step_order UNION ALL SELECT 2) s "
            "ON s.step_order <= e.lvl")


BODIES = {
    # generic = 可移植写法（MySQL/Doris/PG 同构），引擎键只放真正不同的实现
    "generic": _funnel_body(),
    "sls": ('SELECT e."pcId" AS entity, s.step_order AS step_order, '
            'e.channel AS channel, e.stat_date AS stat_date FROM ('
            'SELECT "pcId", CASE WHEN max(ts) > min(ts) THEN 2 ELSE 1 END AS lvl, '
            'max("channel") AS channel, max("stat_date") AS stat_date '
            'FROM observability_rum GROUP BY "pcId") e '
            'JOIN (SELECT 1 AS step_order UNION ALL SELECT 2) s '
            'ON s.step_order <= e.lvl'),
}

FIELDS = [
    {"field": "stat_date", "label": "统计日期", "role": "dimension", "is_time": True},
    {"field": "channel", "label": "渠道", "role": "dimension"},
    {"field": "step_order", "label": "步骤序号", "role": "dimension"},
    {"field": "entity", "label": "到达实体数", "role": "measure",
     "expr": "COUNT(DISTINCT entity)", "unit": "个"},
    {"field": "session_cnt", "label": "会话数", "role": "measure", "agg": "COUNT"},
]

VARIABLES = {
    "start_date": {"type": "date"},
    "window_sec": {"type": "int", "default": 1800},
    "channel": {"type": "enum", "values": ["App", "Web"]},
}


def _ds(**over):
    row = {
        "id": 9001, "name": "路径漏斗", "source_type": "sql", "object_key": "",
        "datasource_id": 0, "sql_query": _funnel_body(),
        "variables": json.dumps(VARIABLES),
        "sql_bodies": json.dumps(BODIES),
        "touched_tables": json.dumps(["t_rum"]),
        "field_config": json.dumps(FIELDS, ensure_ascii=False),
        "certified": 1,
    }
    row.update(over)
    return row


def _bind(db_type="mysql", **over):
    row = dict(object_key="path_funnel", datasource_id=7, physical_table="",
               db_name="stardb", db_type=db_type, bind_kind="dataset",
               dataset_ref="路径漏斗",
               guardrail=Guardrail(query_mode="raw_source", max_rows=5000))
    row.update(over)
    return ResolvedBinding(**row)


@pytest.fixture
def stub_dataset(monkeypatch):
    """把数据集装载换成内存行（不碰元库），返回可改写的 holder。"""
    holder = {"row": _ds()}
    monkeypatch.setattr(planner, "_load_dataset_row",
                        lambda ref: dict(holder["row"]) if ref else None)
    monkeypatch.setattr(planner, "_dataset_has_scopes", lambda _id: False)
    return holder


# ── 1. 注册校验 ─────────────────────────────────────────────────

class TestDatasetRegistration:
    def _base(self, **over):
        data = {"name": "路径漏斗", "sql_query": _funnel_body(),
                "variables": VARIABLES, "dialect": "generic"}
        data.update(over)
        return data

    def test_valid_definition_passes_and_extracts_tables(self):
        errors, stored = reg.validate_definition(
            self._base(sql_bodies=BODIES, field_config=FIELDS))
        assert errors == [], errors
        assert json.loads(stored["touched_tables"]) == ["observability_rum", "t_rum"]

    def test_write_sql_is_rejected(self):
        errors, _ = reg.validate_definition(
            self._base(sql_query="DELETE FROM t_rum WHERE 1=1"))
        assert errors and "只读" in errors[0]

    def test_multi_statement_is_rejected(self):
        errors, _ = reg.validate_definition(
            self._base(sql_query="SELECT 1 FROM t; SELECT 2 FROM t"))
        assert errors

    def test_dead_dialect_key_is_rejected(self):
        """拼错/未知的引擎键永不命中编译 —— 注册即拒（同 Doris 死键那类缺陷）。"""
        errors, _ = reg.validate_definition(self._base(sql_bodies={"dories": "SELECT a FROM t"}))
        assert any("永不命中" in e for e in errors), errors

    def test_bodies_without_generic_conflict_with_sql_query(self):
        errors, _ = reg.validate_definition(self._base(sql_bodies={"doris": "SELECT a FROM t"}))
        assert any("generic" in e for e in errors), errors

    def test_enum_without_values_rejected(self):
        errors, _ = reg.validate_definition(
            self._base(variables={"channel": {"type": "enum"}}))
        assert any("values" in e for e in errors), errors

    def test_bad_default_is_rejected(self):
        errors, _ = reg.validate_definition(
            self._base(variables={"start_date": {"type": "date", "default": "2024/01/01"}}))
        assert any("default" in e for e in errors), errors

    def test_measure_expr_cannot_reference_undeclared_column(self):
        cols = [{"field": "entity", "label": "到达实体数", "role": "measure",
                 "expr": "SUM(secret_col)"}]
        errors, _ = reg.validate_definition(self._base(field_config=cols))
        assert any("未声明列" in e for e in errors), errors

    def test_bad_role_and_agg_rejected(self):
        errors, _ = reg.validate_definition(self._base(field_config=[
            {"field": "a", "role": "extra"}, {"field": "b", "role": "measure", "agg": "MEDIAN"}]))
        assert any("role=" in e for e in errors), errors
        assert any("agg=" in e for e in errors), errors

    def test_engine_native_curried_function_rejected_with_hint(self):
        """Doris 柯里式 window_funnel(W,'strict')(ts,...) 治理解析器读不动。

        执行前会过同一道只读门，所以注册时必须拦下并给出可移植写法，
        而不是存下来等上线静默报错（no-silent-degradation）。
        """
        errors, _ = reg.validate_definition(self._base(
            sql_query="SELECT uid, window_funnel(1800, 'strict')(event_time, "
                      "event_name = 'item') AS lvl FROM t_rum GROUP BY uid"))
        assert any("只读校验" in e for e in errors), errors
        assert any("MIN(CASE WHEN" in e for e in errors), errors

    def test_bad_dialect_label_rejected(self):
        errors, _ = reg.validate_definition(self._base(dialect="oracle"))
        assert any("dialect=" in e for e in errors), errors


# ── 2. 编译器：方言正文与参数 ───────────────────────────────────

class TestCompilerBodiesAndParams:
    def test_generic_body_used_for_mysql_engine(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"}, db_type="mysql")
        assert "t_rum" in out.sql and "observability_rum" not in out.sql
        assert out.sql.endswith("LIMIT 200")

    def test_sls_engine_uses_its_own_body(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"}, db_type="sls")
        assert '"pcId" AS entity' in out.sql
        assert "observability_rum" in out.sql

    def test_missing_engine_and_generic_body_is_refused(self):
        """只注册了 doris 实现时，普通 MySQL 必须拒执行而不是下发 Doris 正文。"""
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(sql_bodies=json.dumps({"doris": "SELECT 1 AS a FROM t"}),
                                sql_query=""),
                            args={"start_date": "2024-01-01"}, db_type="mysql")
        assert "未提供" in str(e.value) and "方言实现" in str(e.value)

    def test_explicit_null_marks_unsupported(self):
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(sql_bodies=json.dumps({"mysql": None}), sql_query=""),
                            args={"start_date": "2024-01-01"}, db_type="mysql")
        assert "显式声明不支持" in str(e.value)

    def test_undeclared_arg_is_refused(self):
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(), args={"start_date": "2024-01-01", "uid": "abc"},
                            db_type="mysql")
        assert "未在数据集声明" in str(e.value)

    def test_missing_required_arg_is_refused(self):
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(sql_bodies=None), args={}, db_type="mysql")
        assert "缺少参数" in str(e.value)

    def test_enum_value_out_of_whitelist_is_refused(self):
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(), args={"start_date": "2024-01-01", "channel": "Hack"},
                            db_type="mysql")
        assert "必须为" in str(e.value)

    def test_injection_fragment_in_value_is_refused(self):
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(), args={"start_date": "2024-01-01", "channel": "x'; DROP TABLE t"},
                            db_type="mysql")
        assert str(e.value)

    def test_default_value_used_when_arg_omitted(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"}, db_type="mysql")
        assert "INTERVAL 1800 SECOND" in out.sql

    def test_inner_limit_backfill_applied(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"}, db_type="mysql")
        assert "LIMIT 5000" in out.sql          # 内层保底
        assert "LIMIT 200" in out.sql           # 外层默认

    def test_resolved_tables_from_registered_column(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"}, db_type="mysql")
        assert out.resolved_tables == ["t_rum"]

    def test_resolved_tables_backfilled_when_column_absent(self):
        """旧行无 touched_tables 时当场静态提表（不得当作"无表可治理"）。"""
        out = compile_dataset(_ds(touched_tables=None),
                              args={"start_date": "2024-01-01"}, db_type="mysql")
        assert "t_rum" in out.resolved_tables

    def test_missing_sql_is_refused(self):
        with pytest.raises(DatasetCompileError) as e:
            compile_dataset(_ds(sql_query="", sql_bodies=None), args={}, db_type="mysql")
        assert "缺少执行 SQL" in str(e.value)


# ── 3. 编译器：输出列下钻（可组合的 mini-MDL）──────────────────

class TestCompilerComposition:
    def test_dimensions_and_measures_compose(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              dimensions=["渠道"], measures=["到达实体数"], db_type="mysql")
        assert "COUNT(DISTINCT entity) AS `到达实体数`" in out.sql
        assert "`channel` AS `渠道`" in out.sql
        assert "GROUP BY `channel`" in out.sql
        assert out.resolution_sources["到达实体数"] == "dataset_field"

    def test_agg_based_measure_wraps_column(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              dimensions=["渠道"], measures=["会话数"], db_type="mysql")
        assert "COUNT(`session_cnt`) AS `会话数`" in out.sql

    def test_time_grain_compiles_on_declared_time_column(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              dimensions=["统计日期"], measures=["到达实体数"],
                              time_grain="month", db_type="mysql")
        assert "DATE_FORMAT(`stat_date`, '%Y-%m')" in out.sql

    def test_pg_quoting_for_pg_family(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              dimensions=["统计日期"], measures=["到达实体数"],
                              time_grain="month", db_type="postgres")
        assert 'to_char("stat_date", \'YYYY-MM\')' in out.sql
        assert 'COUNT(DISTINCT entity) AS "到达实体数"' in out.sql

    def test_filter_on_declared_field_pushed_to_outer_where(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              dimensions=["渠道"], measures=["到达实体数"],
                              filters=[{"field": "步骤序号", "op": "gte", "value": 2}],
                              db_type="mysql")
        assert "WHERE `step_order` >= 2" in out.sql

    def test_order_by_alias(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              dimensions=["渠道"], measures=["到达实体数"],
                              order=[{"by": "到达实体数", "desc": True}], db_type="mysql")
        assert "ORDER BY `到达实体数` DESC" in out.sql

    def test_undeclared_name_returns_candidates_not_guessed_column(self):
        out = compile_dataset(_ds(), args={"start_date": "2024-01-01"},
                              measures=["转化率提升幅度"], dimensions=["渠道"], db_type="mysql")
        assert any("无法解析指标" in w for w in out.warnings), out.warnings
        assert "转化率提升幅度" not in out.sql
        assert out.unresolved and out.unresolved[0]["reason"] == "dataset_field_unresolved"

    def test_semantic_only_fields_are_ignored(self):
        """from=semantic 的项描述的是本体字段，不是 SQL 输出列 → 不参与解析。"""
        fields = FIELDS + [{"field": "obj_only", "label": "语义字段", "role": "dimension",
                            "from": "semantic"}]
        out = compile_dataset(_ds(field_config=json.dumps(fields, ensure_ascii=False)),
                              args={"start_date": "2024-01-01"},
                              dimensions=["语义字段"], measures=["到达实体数"], db_type="mysql")
        assert any("无法解析维度" in w for w in out.warnings), out.warnings

    def test_special_char_alias_is_always_quoted(self):
        """业务名含 ()/% 时 AS 与 ORDER BY 必须包引号（别名是名字不是表达式）。"""
        fields = [
            {"field": "step_name", "label": "步骤名", "role": "dimension"},
            {"field": "user_count", "label": "层间转化率(%)", "role": "measure", "agg": "SUM"},
        ]
        out = compile_dataset(_ds(field_config=json.dumps(fields, ensure_ascii=False)),
                              args={"start_date": "2024-01-01"},
                              dimensions=["步骤名"], measures=["层间转化率(%)"],
                              order=[{"by": "层间转化率(%)", "desc": True}], db_type="mysql")
        assert "AS `层间转化率(%)`" in out.sql
        assert "ORDER BY `层间转化率(%)` DESC" in out.sql
        out_pg = compile_dataset(_ds(field_config=json.dumps(fields, ensure_ascii=False)),
                                 args={"start_date": "2024-01-01"},
                                 dimensions=["步骤名"], measures=["层间转化率(%)"],
                                 order=[{"by": "层间转化率(%)", "desc": True}], db_type="postgres")
        assert 'AS "层间转化率(%)"' in out_pg.sql

    def test_no_field_config_with_dims_warns_explicitly(self):
        """未声明字段却要求聚合：显式告警并回落明细，绝不猜列。"""
        out = compile_dataset(_ds(field_config=None), args={"start_date": "2024-01-01"},
                              dimensions=["渠道"], measures=["到达实体数"], db_type="mysql")
        assert out.sql.startswith("SELECT * FROM (")
        assert any("field_config" in w for w in out.warnings), out.warnings


# ── 4. 语义层绑定（planner._plan_dataset）──────────────────────

class TestPlannerDatasetBinding:
    def _query(self, **over):
        base = dict(object="path_funnel", params={"start_date": "2024-01-01"})
        base.update(over)
        return SemanticQuery(**base)

    def test_plan_produces_sql_and_tables(self, stub_dataset):
        planned = planner.plan(self._query(), _bind(db_type="doris"))
        assert planned.sql and "t_rum" in planned.sql
        assert planned.resolved_tables == ["t_rum"]
        assert planned.provenance["dataset_ref"] == "路径漏斗"
        assert planned.provenance["dataset_certified"] is True

    def test_missing_dataset_ref_is_refused(self, stub_dataset):
        planned = planner.plan(self._query(), _bind(dataset_ref=""))
        assert planned.sql == ""
        assert any("dataset_ref" in w for w in planned.warnings), planned.warnings

    def test_unknown_dataset_is_refused(self, monkeypatch):
        monkeypatch.setattr(planner, "_load_dataset_row", lambda ref: None)
        monkeypatch.setattr(planner, "_dataset_has_scopes", lambda _id: False)
        planned = planner.plan(self._query(), _bind())
        assert planned.sql == ""
        assert any("不存在或已停用" in w for w in planned.warnings), planned.warnings

    def test_dataset_with_row_scopes_cannot_be_bound(self, monkeypatch):
        """scope 只在 query_dataset 通道按身份施加；作为对象绑定会静默少一层过滤 → 拒。"""
        monkeypatch.setattr(planner, "_load_dataset_row", lambda ref: _ds())
        monkeypatch.setattr(planner, "_dataset_has_scopes", lambda _id: True)
        planned = planner.plan(self._query(), _bind())
        assert planned.sql == ""
        assert any("行级范围" in w for w in planned.warnings), planned.warnings

    def test_window_mode_on_dataset_is_refused(self, stub_dataset):
        planned = planner.plan(
            self._query(window={"type": "rank", "metric": "到达实体数"}), _bind())
        assert planned.sql == ""
        assert any("window 不支持数据集对象" in w for w in planned.warnings), planned.warnings

    def test_time_window_is_visible_warning_not_silent(self, stub_dataset):
        """intent 带 time_window 而数据集不认：必须显式告警（否则用户以为窗口生效了）。"""
        planned = planner.plan(self._query(time_window="7d"), _bind())
        assert any("忽略 time_window" in w for w in planned.warnings), planned.warnings

    def test_param_named_filter_is_not_duplicated_as_where(self, stub_dataset):
        planned = planner.plan(
            self._query(metrics=["到达实体数"], dimensions=["步骤序号"],
                        filters=[{"dim": "window_sec", "op": "eq", "value": 3600}]),
            _bind(db_type="mysql"))
        assert "INTERVAL 3600 SECOND" in planned.sql     # 作为参数渲染进正文
        outer = planned.sql.split(") AS _ds_base")[-1]
        assert "WHERE" not in outer                       # 外层不重复下推
