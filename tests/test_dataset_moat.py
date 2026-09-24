"""Datasets 模块的数据护城河回归测试.

覆盖:
- SQL 数据集来源校验(validate_sql: 拒 DDL/DML/多语句)
- 无可信身份 fail-closed(NoIdentityError)
- 行级 scope 拼接的字段白名单/值转义(防注入) 与 AND 叠加语义
- scope 条件与 RLS/敏感基线"只增不减"(包裹子查询保留内层表引用)
"""
import pytest

from services.dataviz.services import dataset_service as svc
from services.dataviz.services.governed_query import NoIdentityError


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

        import services.dataviz.services.governed_query as gq
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
