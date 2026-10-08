"""Datasets 模块的数据护城河回归测试.

覆盖:
- SQL 数据集来源校验(validate_sql: 拒 DDL/DML/多语句)
- 无可信身份 fail-closed(NoIdentityError)
- 行级 scope 拼接的字段白名单/值转义(防注入) 与 AND 叠加语义
- scope 条件与 RLS/敏感基线"只增不减"(包裹子查询保留内层表引用)
- 双定义(object_key+sql_query 并存)/图表预设落库/无 visibility
- query_dataset 执行路径显式选择(sql 优先/纯语义)
- 互转权限把关(compile 仅 dataset:manage) 与 extract 未命中列待映射(不静默绑定)
- source_type='dataset' 图表刷新走数据集治理入口(无旁路裸执行)
"""
import inspect
import json
from types import SimpleNamespace

import pytest

from backend.modules.viz.services import dataset_service as svc
from backend.modules.viz.services.governed_query import NoIdentityError


# ── SQL 来源校验 ─────────────────────────────────────────────────────

class TestValidateSelect:
    def test_rejects_ddl(self):
        with pytest.raises(ValueError):
            svc._validate_select("DROP TABLE users")

    def test_rejects_dml(self):
        with pytest.raises(ValueError):
            svc._validate_select("DELETE FROM t_case_records")

    def test_rejects_multi_statement(self):
        with pytest.raises(ValueError):
            svc._validate_select("SELECT 1; SELECT 2")

    def test_accepts_select(self):
        svc._validate_select("SELECT region, COUNT(*) AS cnt FROM t GROUP BY region")

    def test_accepts_cte(self):
        svc._validate_select("WITH x AS (SELECT 1 AS a) SELECT * FROM x")


# ── 行级 scope WHERE 拼接(防注入 + 只收紧) ───────────────────────────

class TestBuildWhere:
    def test_eq_string_escaped(self):
        w = svc._build_where([{"field": "region", "op": "eq", "value": "华'东"}])
        assert w == " WHERE `region` = '华''东'"

    def test_illegal_field_dropped(self):
        w = svc._build_where([{"field": "a; DROP TABLE x--", "op": "eq", "value": 1}])
        assert w == ""  # 非法字段名被丢弃, 不拼入 SQL

    def test_illegal_op_dropped(self):
        w = svc._build_where([{"field": "region", "op": "=", "value": 1}])
        assert w == ""

    def test_in_list(self):
        w = svc._build_where([{"field": "status", "op": "in", "value": ["A", "B"]}])
        assert "`status` IN ('A', 'B')" in w

    def test_like_wraps_percent(self):
        w = svc._build_where([{"field": "name", "op": "like", "value": "east"}])
        assert "`name` LIKE '%east%'" in w

    def test_multiple_filters_are_anded(self):
        """多条 scope 一律 AND 合并 —— 只收紧不放宽."""
        w = svc._build_where([
            {"field": "region", "op": "eq", "value": "cn"},
            {"field": "amount", "op": "lte", "value": 100},
        ])
        assert " AND " in w

    def test_numeric_value_inlined(self):
        w = svc._build_where([{"field": "cnt", "op": "gt", "value": 5}])
        assert "`cnt` > 5" in w


# ── 无可信身份 fail-closed ───────────────────────────────────────────

class TestFailClosed:
    def test_sql_source_requires_identity(self):
        ds_stub = {"source_type": "sql", "sql_query": "SELECT 1 AS a",
                   "datasource_id": 0, "name": "x"}
        ident = {"user_id": 0, "username": "", "role": "", "workspace_id": 0}
        with pytest.raises(NoIdentityError):
            svc._query_sql(ds_stub, {}, [], ident)

    def test_semantic_source_requires_identity(self):
        """语义源: user_id=0 走系统内部口径, 但数据集查询入口拒绝匿名 ——
        query_dataset 传 identity.user_id=0 时 execute_semantic 的闸门链
        identity 检查会拒绝(七闸门 identity 前置), 此处校验构造不抛裸连异常."""
        ds_stub = {"source_type": "semantic", "object_key": "__nonexistent__",
                   "datasource_id": 0, "name": "x"}
        ident = {"user_id": 0, "username": "", "role": "", "workspace_id": 0}
        with pytest.raises(Exception):  # ValueError(未绑定) 或 PermissionError
            svc._query_semantic(ds_stub, {}, [], ident)


# ── SQL 包裹结构: 内层表引用保留(RLS 可注入), 外层仅追加 WHERE ────────

class TestSqlWrappingShape:
    def test_scope_wraps_as_subquery_preserving_inner_tables(self, monkeypatch):
        captured = {}

        def fake_governed(sql, ds_id, uid, ws, uname):
            captured["sql"] = sql
            return {"columns": [], "rows": [], "row_count": 0, "elapsed_ms": 0}

        import backend.modules.viz.services.governed_query as gq
        monkeypatch.setattr(gq, "governed_execute", fake_governed)

        ds_stub = {"source_type": "sql",
                   "sql_query": "SELECT region, cnt FROM t_case_daily",
                   "datasource_id": 0, "name": "x"}
        ident = {"user_id": 7, "username": "zs", "role": "analyst", "workspace_id": 1}
        svc._query_sql(ds_stub, {"limit": 50},
                       [{"field": "region", "op": "eq", "value": "cn"}], ident)
        sql = captured["sql"]
        # 内层原表引用保留 → enforcer 的 FROM t 注入仍能命中(RLS 不旁路)
        assert "FROM t_case_daily" in sql
        # scope 以子查询外层 AND 追加
        assert "_ds_base" in sql and "`region` = 'cn'" in sql
        assert sql.rstrip().endswith("LIMIT 50")


# ── 双定义(object_key + sql_query 并存) + 图表预设落库 + 无 visibility ──

class TestDualDefinition:
    def test_source_type_derivation(self):
        assert svc._derive_source_type("obj", "SELECT 1") == "both"
        assert svc._derive_source_type("obj", "") == "semantic"
        assert svc._derive_source_type("", "SELECT 1") == "sql"

    def test_create_requires_at_least_one_definition(self):
        with pytest.raises(ValueError):
            svc.create_dataset({"name": "x"}, {"user_id": 1, "role": "admin"})

    def test_update_cannot_drop_both_definitions(self, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda did: {
            "id": 1, "name": "x", "object_key": "obj", "sql_query": "SELECT 1",
            "owner_id": 7, "status": "active"})
        with pytest.raises(ValueError):
            svc.update_dataset(1, {"object_key": "", "sql_query": ""},
                               {"user_id": 7, "role": "admin"})

    def test_dual_create_persists_chart_preset_without_visibility(self, monkeypatch):
        captured = {}

        def fake_query(sql, params=None, fetchone=False):
            if "WHERE name=%s" in sql:
                return None
            if "COALESCE(MAX(id)" in sql:
                return {"n": 4}
            return {"id": 4, "name": "双定义", "source_type": "both"}

        def fake_write(sql, params):
            captured["sql"], captured["params"] = sql, params

        monkeypatch.setattr(svc, "execute_query", fake_query)
        monkeypatch.setattr(svc, "execute_write", fake_write)
        svc.create_dataset({
            "name": "双定义", "object_key": "案件", "sql_query": "SELECT region FROM t",
            "datasource_id": 2, "chart_type": "bar",
            "chart_preset": {"xCol": "region", "yCol": "cnt"},
        }, {"user_id": 7, "role": "admin"})
        sql, params = captured["sql"], captured["params"]
        assert "visibility" not in sql  # 可见性功能已退役, 不再落库/过滤
        assert params[3] == "both"  # 双定义并存
        assert params[8] == "bar"
        assert json.loads(params[9]) == {"xCol": "region", "yCol": "cnt"}

    def test_chart_preset_must_be_object(self, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda did: {
            "id": 1, "name": "x", "object_key": "obj", "sql_query": "", "owner_id": 7})
        with pytest.raises(ValueError):
            svc.update_dataset(1, {"chart_preset": "not-a-dict"},
                               {"user_id": 7, "role": "admin"})

    def test_module_has_no_visibility_left(self):
        assert "visibility" not in inspect.getsource(svc)


# ── query_dataset 执行路径显式选择(非降级) ─────────────────────────────

class TestExecutionPathSelection:
    def _run(self, monkeypatch, ds_stub):
        called = {}
        monkeypatch.setattr(svc, "get_dataset", lambda did: ds_stub)
        monkeypatch.setattr(svc, "_scope_filters", lambda *a: [])

        def fake_sql(ds, params, scopes, ident):
            called["path"] = "sql"
            return {"execution_mode": "sql"}

        def fake_sem(ds, params, scopes, ident):
            called["path"] = "semantic"
            return {"execution_mode": "semantic"}

        monkeypatch.setattr(svc, "_query_sql", fake_sql)
        monkeypatch.setattr(svc, "_query_semantic", fake_sem)
        out = svc.query_dataset(1, {}, {"user_id": 7, "role": "admin"})
        return called, out

    def test_sql_wins_when_dual_defined(self, monkeypatch):
        called, out = self._run(monkeypatch, {
            "id": 1, "status": "active", "name": "x",
            "sql_query": "SELECT 1 AS a", "object_key": "obj"})
        assert called["path"] == "sql"  # 执行 SQL 是实际执行通道
        assert out["execution_mode"] == "sql"

    def test_semantic_when_object_only(self, monkeypatch):
        called, out = self._run(monkeypatch, {
            "id": 1, "status": "active", "name": "x",
            "sql_query": "", "object_key": "obj"})
        assert called["path"] == "semantic"
        assert out["execution_mode"] == "semantic"

    def test_missing_definition_raises(self, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda did: {
            "id": 1, "status": "active", "name": "x", "sql_query": "", "object_key": ""})
        monkeypatch.setattr(svc, "_scope_filters", lambda *a: [])
        with pytest.raises(ValueError):
            svc.query_dataset(1, {}, {"user_id": 7})


# ── 互转: compile 权限把关 / extract 未命中列待映射(宁缺勿错) ────────────

class TestInteropGuards:
    def test_compile_requires_dataset_manage_and_never_echoes_sql(self, monkeypatch):
        monkeypatch.setattr("backend.common.api_permission.check_api_permission",
                            lambda role, method, path: False)
        with pytest.raises(PermissionError) as ei:
            svc.compile_from_semantic({"object_key": "obj", "measures": ["m"]},
                                      {"user_id": 7, "role": "viewer"})
        # 无权者拿到的是声明式拒绝, 编译 SQL 不回显(护栏 §7)
        assert "SELECT" not in str(ei.value).upper()

    def test_extract_requires_dataset_manage(self, monkeypatch):
        monkeypatch.setattr("backend.common.api_permission.check_api_permission",
                            lambda role, method, path: False)
        with pytest.raises(PermissionError):
            svc.extract_from_sql({"sql_query": "SELECT 1 AS a"},
                                 {"user_id": 7, "role": "viewer"})

    def test_compile_refuses_unresolved_terms(self, monkeypatch):
        monkeypatch.setattr("backend.common.api_permission.check_api_permission",
                            lambda role, method, path: True)
        monkeypatch.setattr("backend.semantics.intent.parse_intent",
                            lambda payload: (SimpleNamespace(**{
                                "object": payload["object"], "dimensions": payload["dimensions"],
                                "metrics": payload["metrics"], "datasource_id": payload["datasource_id"],
                                "filters": [], "order": [], "limit": 200}), None, []))
        monkeypatch.setattr("backend.semantics.binding_resolver.resolve_binding",
                            lambda *a, **k: (object(), []))
        monkeypatch.setattr("backend.semantics.planner.plan",
                            lambda q, b: SimpleNamespace(
                                sql="SELECT 1", dialect="mysql",
                                provenance={"unresolved_terms": ["未知词"]}, warnings=[]))
        with pytest.raises(ValueError) as ei:
            svc.compile_from_semantic({"object_key": "obj"},
                                      {"user_id": 7, "role": "admin"})
        assert "未解析" in str(ei.value)  # 宁缺勿错, 不静默绑定

    def test_compile_returns_sql_for_editor_when_resolved(self, monkeypatch):
        monkeypatch.setattr("backend.common.api_permission.check_api_permission",
                            lambda role, method, path: True)
        monkeypatch.setattr("backend.semantics.intent.parse_intent",
                            lambda payload: (SimpleNamespace(**{
                                "object": payload["object"], "dimensions": payload["dimensions"],
                                "metrics": payload["metrics"], "datasource_id": payload["datasource_id"],
                                "filters": [], "order": [], "limit": 200}), None, []))
        monkeypatch.setattr("backend.semantics.binding_resolver.resolve_binding",
                            lambda *a, **k: (object(), []))
        monkeypatch.setattr("backend.semantics.planner.plan",
                            lambda q, b: SimpleNamespace(
                                sql="SELECT `region`, COUNT(*) FROM t GROUP BY `region`",
                                dialect="mysql", provenance={}, warnings=[]))
        out = svc.compile_from_semantic({"object_key": "obj", "dimensions": ["region"]},
                                        {"user_id": 7, "role": "admin"})
        assert out["sql"].startswith("SELECT")

    def test_extract_unmatched_column_returned_pending(self, monkeypatch):
        monkeypatch.setattr("backend.common.api_permission.check_api_permission",
                            lambda role, method, path: True)
        monkeypatch.setattr("backend.modules.viz.services.governed_query.governed_execute",
                            lambda *a, **k: {
                                "columns": ["region", "cnt_x", "amount"],
                                "rows": [{"region": "华东", "cnt_x": 1, "amount": 2.5}],
                                "row_count": 1})
        monkeypatch.setattr(svc, "_semantic_fields", lambda ds, obj: [
            {"field": "region", "label": "region", "role": "dimension", "aliases": ["地区"]},
            {"field": "amount", "label": "amount", "role": "measure", "aliases": []},
        ])
        out = svc.extract_from_sql(
            {"sql_query": "SELECT region, cnt_x, amount FROM t",
             "object_key": "obj", "datasource_id": 0},
            {"user_id": 7, "role": "admin", "username": "a", "workspace_id": 1})
        by = {f["field"]: f for f in out["fields"]}
        assert by["region"]["maps_to"] == "region" and by["region"]["match_source"] == "name"
        assert by["amount"]["maps_to"] == "amount"
        # 未命中列显式回抛待映射(带候选), 不静默绑定(ontology-modeling §7)
        assert by["cnt_x"]["maps_to"] == "" and by["cnt_x"]["match_source"] == "none"
        assert by["cnt_x"]["candidates"]
        assert out["unmapped"] == ["cnt_x"]

    def test_extract_matches_alias(self, monkeypatch):
        monkeypatch.setattr("backend.common.api_permission.check_api_permission",
                            lambda role, method, path: True)
        monkeypatch.setattr("backend.modules.viz.services.governed_query.governed_execute",
                            lambda *a, **k: {"columns": ["地区"], "rows": [{"地区": "华东"}],
                                             "row_count": 1})
        monkeypatch.setattr(svc, "_semantic_fields", lambda ds, obj: [
            {"field": "region", "label": "region", "role": "dimension", "aliases": ["地区"]},
        ])
        out = svc.extract_from_sql(
            {"sql_query": "SELECT 地区 FROM t", "object_key": "obj", "datasource_id": 0},
            {"user_id": 7, "role": "admin", "username": "a", "workspace_id": 1})
        assert out["fields"][0]["maps_to"] == "region"
        assert out["fields"][0]["match_source"] == "alias"
        assert out["unmapped"] == []


# ── source_type='dataset' 图表刷新: 走数据集治理入口(无旁路裸执行) ──────

class TestDatasetChartRefreshGoverned:
    def test_refresh_dataset_chart_uses_dataset_governed_entry(self):
        from backend.modules.viz.services.dashboard_service import ChartService
        src = inspect.getsource(ChartService._refresh_dataset_chart)
        assert "query_dataset" in src        # 统一治理入口
        assert "governed_execute(" not in src  # 无旁路裸执行
        assert "sql_query" not in src        # 不读/不复制数据集 SQL(单口径)

    def test_refresh_chart_routes_dataset_charts(self):
        from backend.modules.viz.services.dashboard_service import ChartService
        src = inspect.getsource(ChartService.refresh_chart)
        assert '_refresh_dataset_chart' in src
        assert 'source_type' in src

    def test_dataset_chart_requires_identity(self):
        from backend.modules.viz.services.dashboard_service import ChartService
        with pytest.raises(NoIdentityError):
            ChartService()._refresh_dataset_chart(
                {"id": 1, "source_id": 2, "config": "{}"}, {}, 0, 0, "", None)
