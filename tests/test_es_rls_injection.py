"""ES 型数据源的治理改写用例（行级注入 / 列级策略 / 引擎形态门 / 键空间归一）。

覆盖 2026-11-13 阶段一落地的能力，全部离线（不打真实集群）；实机取证结论写在
`backend/semantics/es_dialect.py` 模块头与 `docker/mysql/es_datasource_rls_migration.sql`。

锁定要点（任一条回退即视为缺陷）：
- es 必须有自己的方言登记，**不得**落到 mysql 生成器（那会把 `"x.keyword"` 变成字符串字面量，
  语法能过、结果全错 = 静默错数）；
- 行策略在 ES 上的形态是「谓词 AND 进顶层 WHERE」，通用实现的包裹子查询在 ES 必失败；
- 策略命不中表引用必须**显式拒**（静默原句放行 = 假治理）；
- 列名归一：正文写 `"appCode.keyword"`、策略登记 `appCode` 必须都能命中（否则配了不生效）；
- `hash` 型脱敏在 ES 不可表达 → 登记与取数两侧都拒；
- ES 改写产物必须过形态白名单复核（不用严格渲染保真门，那会误拒合法正文）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.core import es_client  # noqa: E402
from backend.core.enforcer import PermissionEnforcer, PermissionResult, _es_table_columns  # noqa: E402
from backend.semantics import sql_guard  # noqa: E402
from backend.semantics.dialect import (  # noqa: E402
    ENGINE_OF_DBTYPE, FAMILY_OF_DBTYPE, is_es_db_type, engine_of, is_known_db_type,
)
from backend.semantics.es_dialect import ES_DIALECT, assert_es_renderable, es_rejection  # noqa: E402
from backend.semantics.security.masking import mask_expr  # noqa: E402

TABLE = "t_software_app_operation_log"
ALIAS_SQL = f'SELECT "appmanager.keyword" AS m, COUNT(*) AS c FROM "{TABLE}" GROUP BY 1 ORDER BY 2 DESC LIMIT 10'
PATTERN = "t-t_software_app_operation_log*"
FILTERS = {TABLE: "appmanager = 'ALLIEDSTAR'"}


# ── 方言登记：es 必须在任何治理改写之前登记 ──────────────────────────────

def _es_ctx(datasource_id: int = 5, **over) -> "es_client.EsCtx":
    """一条 ES 型数据源的连接上下文。

    旧写法拿 `es_client.legacy_ctx()`（内置通道、`datasource_id=0`、由 `.env` 托管）当测试夹具；
    那个入口已随阶段三第 2 步删除（现在 `ctx_for(0)` 直接拒），所以这里直接组 EsCtx：
    与运行期同一类型、同一个开放范围口径，不留第二份假想配置。
    """
    return es_client.EsCtx(**{
        "datasource_id": datasource_id, "label": "cloud-platform-es",
        "base_url": "http://es-inner:9200", "username": "", "password": "", "api_key": "",
        "verify_certs": True, "timeout_sec": 20, "max_rows": 1000, "allowed": ("t_x",),
        "pool_size": es_client.DEFAULT_POOL_SIZE, **over})


class TestDialectRegistration:
    def test_db_type_maps_to_es_dialect(self):
        assert sql_guard.dialect_of_db_type("es") == ES_DIALECT
        assert sql_guard.dialect_of_db_type("elasticsearch") == ES_DIALECT

    def test_engine_registered_but_family_deliberately_absent(self):
        # engine 必须有（治理层按引擎分支）；family 故意不登记：
        # planner 的 `is_known_db_type` 硬门据此拒绝语义编译，避免拿 mysql 骨架编出错 SQL（阶段四再放开）
        assert engine_of("es") == "es"
        assert ENGINE_OF_DBTYPE["elasticsearch"] == "es"
        assert not is_known_db_type("es")
        assert "es" not in FAMILY_OF_DBTYPE

    def test_is_es_db_type_covers_both_spellings(self):
        assert is_es_db_type("es") and is_es_db_type("Elasticsearch")
        assert not is_es_db_type("mysql") and not is_es_db_type(None)


class TestEsFunctionRendering:
    """Presto 生成器会把取子串降级成 ES 不认的 `SUBSTR`，es 方言必须改回规范形。"""

    def _render(self, sql):
        import sqlglot

        return sqlglot.parse_one(sql, read=ES_DIALECT).sql(dialect=ES_DIALECT)

    def test_left_right_substring_render_as_three_arg_substring(self):
        assert self._render('SELECT LEFT("a", 2) FROM "x" LIMIT 1') == \
            'SELECT SUBSTRING("a", 1, 2) FROM "x" LIMIT 1'
        out = self._render('SELECT RIGHT("a", 2) FROM "x" LIMIT 1')
        assert "SUBSTR(" not in out.replace("SUBSTRING(", "")
        assert "SUBSTRING(" in out and "GREATEST(" in out

    def test_substring_without_length_gets_closed_form(self):
        out = self._render('SELECT SUBSTRING("a", 3) FROM "x" LIMIT 1')
        assert "SUBSTRING(" in out and "LENGTH(" in out

    def test_shape_gate_rejects_engine_unsupported_forms(self):
        for sql, frag in (
            (f'SELECT SUBSTR("a", 1, 2) FROM "{TABLE}" LIMIT 1', "SUBSTR"),
            (f'SELECT "a" || "b" FROM "{TABLE}" LIMIT 1', "||"),
            (f'SELECT MD5("a") FROM "{TABLE}" LIMIT 1', "MD5"),
            (f'SELECT `a` FROM "{TABLE}" LIMIT 1', "反引号"),
            (f'SELECT "a" FROM "{TABLE}" LIMIT 1 OFFSET 2', "OFFSET"),
            (f'SELECT "a" FROM "{TABLE}" LIMIT 1', None),      # 合规
        ):
            reason = es_rejection(sql)
            if frag is None:
                assert reason is None, sql
            else:
                assert reason and frag in reason, (sql, reason)

    def test_limit_is_mandatory_and_quotes_are_not_scanned(self):
        assert "LIMIT" in (es_rejection(f'SELECT "a" FROM "{TABLE}"') or "")
        # 字段名/字面量里出现关键字不该被当成语法形态（判定前先剥引号内容）
        assert es_rejection(f'SELECT "join.keyword" AS "with" FROM "{TABLE}" '
                            "WHERE \"appmanager\" = 'offset' LIMIT 1") is None

    def test_assert_renderable_raises_permission_error(self):
        with pytest.raises(PermissionError):
            assert_es_renderable(f'SELECT SUBSTR("a", 1, 2) FROM "{TABLE}" LIMIT 1')


# ── 行级注入：顶层 WHERE AND，而不是包裹子查询 ───────────────────────────

class TestInjectFiltersEs:
    def test_no_where_clause_gets_predicate_inserted(self):
        out, applied = sql_guard.inject_filters(ALIAS_SQL, FILTERS, dialect=ES_DIALECT, engine="es")
        assert applied == [TABLE]
        assert f'FROM "{TABLE}" WHERE appmanager = \'ALLIEDSTAR\'' in out
        assert "(SELECT * FROM" not in out          # 包裹形态在 ES 上必失败
        assert es_rejection(out) is None

    def test_existing_where_is_anded_not_replaced(self):
        sql = (f'SELECT "appmanager.keyword" AS m FROM "{TABLE}" '
               "WHERE \"evenTime.keyword\" >= '2026-11-01' LIMIT 5")
        out, _applied = sql_guard.inject_filters(sql, FILTERS, dialect=ES_DIALECT, engine="es")
        assert "evenTime.keyword" in out and "appmanager = 'ALLIEDSTAR'" in out
        assert out.upper().count(" WHERE ") == 1          # 同一 WHERE 里 AND，不得多出一个子句

    def test_pattern_form_body_still_hits_alias_keyed_policy(self):
        sql = ALIAS_SQL.replace(TABLE, PATTERN)
        out, applied = sql_guard.inject_filters(sql, FILTERS, dialect=ES_DIALECT, engine="es")
        assert applied == [PATTERN] and f'FROM "{PATTERN}"' in out
        assert "appmanager = 'ALLIEDSTAR'" in out

    def test_alias_form_body_still_hits_pattern_keyed_policy(self):
        filters = {PATTERN: "appmanager = 'ALLIEDSTAR'"}
        _out, applied = sql_guard.inject_filters(ALIAS_SQL, filters, dialect=ES_DIALECT, engine="es")
        assert applied == [TABLE]

    def test_unmatched_policy_key_must_raise_not_pass_through(self):
        # 静默返回原句 = “过了治理口却一条策略也没套”的假治理，代价最高的一种失效
        with pytest.raises(PermissionError) as exc:
            sql_guard.inject_filters(ALIAS_SQL, {"other_table": "x = 1"}, dialect=ES_DIALECT, engine="es")
        text = str(exc.value)
        assert "other_table" in text and TABLE in text      # 两侧名字都要能诊断

    def test_multi_table_body_is_rejected(self):
        sql = (f'SELECT "a" FROM "{TABLE}" JOIN "t_equipment_warning_log" ON 1=1 LIMIT 10')
        with pytest.raises(PermissionError):
            sql_guard.inject_filters(sql, FILTERS, dialect=ES_DIALECT, engine="es")

    def test_predicate_with_subquery_is_rejected(self):
        filters = {TABLE: "appCode IN (SELECT app FROM t)"}
        with pytest.raises(Exception):
            sql_guard.inject_filters(ALIAS_SQL, filters, dialect=ES_DIALECT, engine="es")

    def test_non_es_engine_keeps_wrapping_shape(self):
        # 通用支路行为零变化（本阶段只加 es 专支）
        out, applied = sql_guard.inject_filters(
            "SELECT c FROM t WHERE c > 1 LIMIT 5", {"t": "region = 'cn'"}, dialect="mysql")
        assert applied == ["t"] and "(SELECT * FROM t WHERE region = 'cn')" in out


# ── 列名归一：`.keyword` 子字段与物理列名是同一逻辑字段 ─────────────────

class TestEsNameNormalization:
    def test_alias_of_normalizes_both_spellings(self):
        assert es_client.es_alias_of(PATTERN) == TABLE
        assert es_client.es_alias_of(TABLE) == TABLE
        assert es_client.es_alias_of('"t-foo*"') == "foo"

    def test_field_of_strips_only_trailing_keyword(self):
        assert es_client.es_field_of("appCode.keyword") == "appcode"
        assert es_client.es_field_of("appCode") == "appcode"
        assert es_client.es_field_of("a.b") == "a.b"          # 其他带点名字不动

    def test_variants_cover_both_writings(self):
        assert set(es_client.es_field_variants("appCode.keyword")) == {"appcode", "appcode.keyword"}
        assert es_client.es_field_variants("") == []


# ── 列级策略改写：产物过形态门，改写不成即拒（不回退结果侧） ─────────────

class TestColumnRestrictionInEsSql:
    def _result(self):
        res = PermissionResult()
        res.masked_columns = {"appCode": "partial"}
        return res

    def test_masked_column_is_compiled_into_sql(self):
        res = self._result()
        sql = f'SELECT "appCode.keyword" AS app, COUNT(*) AS c FROM "{TABLE}" GROUP BY 1 LIMIT 5'
        out = PermissionEnforcer()._restrict_columns_in_es_sql(
            sql, {TABLE: {"hidden": [], "masked": {"appCode": "partial"}}}, 0, ES_DIALECT, "es", res)
        assert "SUBSTRING(\"appCode.keyword\", 1, 2)" in out
        assert "SUBSTR(" not in out.replace("SUBSTRING(", "")
        assert any(p.startswith("column_restriction:in_sql") for p in res.policies_applied)
        assert es_rejection(out) is None

    def test_policy_registered_on_keyword_spelling_also_hits(self):
        res = self._result()
        sql = f'SELECT "appCode" AS app FROM "{TABLE}" LIMIT 5'
        out = PermissionEnforcer()._restrict_columns_in_es_sql(
            sql, {TABLE: {"hidden": [], "masked": {"appCode.keyword": "partial"}}},
            0, ES_DIALECT, "es", res)
        assert "SUBSTRING(" in out and out != sql

    def test_unwritable_restriction_raises_instead_of_result_side_fallback(self):
        # ES 输出列是业务别名/子字段路径，结果侧按物理列名对不上 → 回退等于静默不脱敏
        res = self._result()
        with pytest.raises(PermissionError):
            PermissionEnforcer()._restrict_columns_in_es_sql(
                f'SELECT * FROM "{TABLE}" LIMIT 5',
                {TABLE: {"hidden": ["evenTime"], "masked": {}}}, 0, ES_DIALECT, "es", res)

    def test_hash_mask_is_rejected_on_es_engine(self):
        res = self._result()
        with pytest.raises(ValueError):
            PermissionEnforcer()._restrict_columns_in_es_sql(
                f'SELECT "appCode" AS app FROM "{TABLE}" LIMIT 5',
                {TABLE: {"hidden": [], "masked": {"appCode": "hash"}}}, 0, ES_DIALECT, "es", res)

    def test_unrelated_policy_leaves_sql_untouched(self):
        res = self._result()
        sql = f'SELECT "appCode.keyword" AS app FROM "{TABLE}" LIMIT 5'
        out = PermissionEnforcer()._restrict_columns_in_es_sql(
            sql, {"other_table": {"hidden": [], "masked": {"appCode": "partial"}}},
            0, ES_DIALECT, "es", res)
        assert out == sql and res.policies_applied == []

    def test_mask_expr_es_shapes(self):
        assert mask_expr('"a"', "partial", ES_DIALECT, "es").count("CONCAT(") == 2   # CONCAT 只两参 → 嵌套
        assert mask_expr('"a"', "null", ES_DIALECT, "es") == "NULL"
        with pytest.raises(ValueError):
            mask_expr('"a"', "hash", ES_DIALECT, "es")

    def test_column_list_drops_keyword_subfields_for_star_expansion(self, monkeypatch):
        # `SELECT *` 在 ES 只返回根字段；把子字段当列展开会凭空多出输出列
        import backend.core.enforcer as enf

        monkeypatch.setattr(enf, "_table_columns_snapshot", lambda ds, table: (
            None if table != TABLE else
            ["appCode", "appCode.keyword", "userCode", "userCode.keyword", "@timestamp"]))
        cols = _es_table_columns(1, PATTERN)
        assert cols == ["appCode", "userCode", "@timestamp"]


# ── 配置面：连接工厂拒绝 ES、策略表名登记时把关 ─────────────────────────

class TestConfigurationGuards:
    def test_dbapi_factory_refuses_es_datasource(self):
        from backend.common.db.datasource_db import get_datasource_conn

        with pytest.raises(ValueError, match="DB-API"):
            get_datasource_conn("es", "localhost", 9200, "u", "p")

    def test_index_allowed_uses_union_of_aliases_and_patterns(self):
        ctx = es_client.EsCtx(
            datasource_id=1, label="probe", base_url="https://x", username="", password="",
            api_key="", verify_certs=True, timeout_sec=20, max_rows=100,
            allowed=(TABLE, PATTERN))
        assert es_client.index_allowed(TABLE, ctx)
        assert es_client.index_allowed(PATTERN, ctx)
        assert es_client.index_allowed("t-t_software_app_operation_log-cloud-platform-v2-cn-2026.11", ctx)
        # 丢掉 `t-` 前缀的模式匹配的是另一族索引 → 算扩权，必须拒
        assert not es_client.index_allowed("t_software_app_operation_log*", ctx)
        assert not es_client.index_allowed(TABLE, None.__class__ and es_client.EsCtx(
            datasource_id=1, label="probe", base_url="https://x", username="", password="",
            api_key="", verify_certs=True, timeout_sec=20, max_rows=100, allowed=()))

    def test_ctx_for_unknown_datasource_fails_loud(self):
        from unittest.mock import patch

        with patch("backend.common.db.get_datasource_by_id", return_value=None):
            with pytest.raises(es_client.EsError):
                es_client.ctx_for(999999)
        with patch("backend.common.db.get_datasource_by_id",
                   return_value={"id": 5, "name": "prd-cn", "db_type": "mysql", "host": "h"}):
            with pytest.raises(es_client.EsError):
                es_client.ctx_for(5)

    def test_policy_table_normalizes_and_rejects_unknown_alias(self):
        from unittest.mock import patch

        from backend.core import rls_service as rls

        es_row = {"id": 5, "name": "cloud-platform-es", "db_type": "es"}
        ctx = es_client.EsCtx(
            datasource_id=5, label="es", base_url="https://x", username="", password="",
            api_key="", verify_certs=True, timeout_sec=20, max_rows=100, allowed=(PATTERN,))
        with patch("backend.common.db.get_datasource_by_id", return_value=es_row), \
             patch.object(es_client, "ctx_for", return_value=ctx):
            assert rls._es_policy_table(5, PATTERN) == TABLE
            with pytest.raises(ValueError) as exc:
                rls._es_policy_table(5, "t_software_app_operation_logg")
            assert TABLE in str(exc.value)          # 候选别名要写出来，便于改正

    def test_policy_table_passthrough_for_non_es(self):
        from unittest.mock import patch

        from backend.core import rls_service as rls

        with patch("backend.common.db.get_datasource_by_id",
                   return_value={"id": 1, "name": "prd-cn", "db_type": "mysql"}):
            assert rls._es_policy_table(1, "orders") == "orders"

    def test_hash_mask_rejected_at_save_time_for_es(self):
        from unittest.mock import patch

        from backend.core import rls_service as rls

        cols = [{"column_name": "appCode", "access_type": "masked", "mask_pattern": "hash"}]
        with patch("backend.common.db.get_datasource_by_id",
                   return_value={"id": 5, "name": "cloud-platform-es", "db_type": "es"}):
            with pytest.raises(ValueError, match="hash"):
                rls._es_reject_unsupported_mask(5, cols)
        with patch("backend.common.db.get_datasource_by_id",
                   return_value={"id": 1, "name": "prd-cn", "db_type": "mysql"}):
            rls._es_reject_unsupported_mask(1, cols)      # 其他引擎不受本约束


# ── 执行分派：ES 源走 es_client，不参与跨源联邦 ────────────────────────

class TestExecutionDispatch:
    def test_es_datasource_executes_via_es_client(self, monkeypatch):
        import pandas as pd

        import backend.core.query_executor as qe

        calls = {}

        def fake_ctx(ds_id):
            calls["ctx"] = ds_id
            return _es_ctx(int(ds_id or 0))

        def fake_run_sql(sql, *, fetch_size=200, ctx=None):
            calls["sql"] = sql
            return {"columns": [{"name": "c"}, {"name": "c"}], "rows": [["1", "2"], ["3", "4"]]}

        monkeypatch.setattr(qe, "_is_es_datasource", lambda ds_id: True)
        monkeypatch.setattr(es_client, "ctx_for", fake_ctx)
        monkeypatch.setattr(es_client, "run_sql", fake_run_sql)
        df, _ms, n = qe._execute_via_es_client(ALIAS_SQL, 5)
        assert calls["ctx"] == 5 and calls["sql"] == ALIAS_SQL
        assert n == 2 and list(df.columns) == ["c", "c__1"]      # 重名列必须消歧（护栏 §8）
        assert isinstance(df, pd.DataFrame)

    def test_federated_query_on_es_is_refused(self, monkeypatch):
        """跨源联邦不得静默回退成直连：ES SQL 无 JOIN，拿别源充数是错数。"""
        import backend.core.query_executor as qe

        monkeypatch.setattr(qe, "_is_es_datasource", lambda ds_id: True)
        with pytest.raises(PermissionError):
            qe.execute_query(
                f'SELECT "a" FROM "{TABLE}" LIMIT 10', datasource_id=5,
                federated=[{"name": "prd-cn", "config": {}}])

    def test_run_sql_fetch_size_follows_declared_limit(self, monkeypatch):
        """回归（阶段二 p2a 实测）：治理口不传 fetch_size 时旧默认 200 会**静默少给行**。

        ES SQL 一次只回 `fetch_size` 行，剩下的要拿响应 cursor 续取；`run_sql` 不续 cursor，
        所以拿固定小批次取一条 `LIMIT 1000` 的正文只会回 200 行——少行数的错数比报错难查。

        旧写法还要先 `monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", ...)`（内置通道的全局
        配置），现在开放范围与行上限就在 ctx 上，不多一个口径入口。
        """
        seen = {}

        def fake_request(method, path, *, params=None, json_body=None, ctx=None):
            seen["payload"] = json_body
            return {"columns": [{"name": "a", "type": "keyword"}], "rows": [["x"]]}

        monkeypatch.setattr(es_client, "_request", fake_request)
        # 开放范围与行上限均来自这条 ES 数据源（全局 config.ES_* 已随内置通道退役）
        ctx = _es_ctx(900, allowed=("logs-*",), max_rows=1000)
        body = f'SELECT "a" AS x FROM "logs-2026.11" LIMIT 1000'
        es_client.run_sql(body, ctx=ctx)
        assert seen["payload"]["fetch_size"] == 1000        # 缺省 = 按正文 LIMIT 取满
        es_client.run_sql(body, fetch_size=20, ctx=ctx)
        assert seen["payload"]["fetch_size"] == 20          # 显式给值仍按调用方要的行数收


class TestGovernedEsPaging:
    """ES 分页取数口：治理改写/审计与主口同一实现，只是执行载体换成 search_after。"""

    PAGE = {"columns": ["m"], "rows": [["appmanager"]], "row_count": 1,
            "total": 4468044, "page_size": 20, "page_num": 2, "paging_mode": "cursor",
            "sql": ALIAS_SQL}

    def _stub(self, monkeypatch, *, es=True, secured=ALIAS_SQL, deny=None, page=None):
        import backend.core.query_executor as qe
        from backend.core import es_pagination
        from backend.core.enforcer import PermissionResult

        seen = {}

        def fake_secure(sql, ds_id, uid, ws):
            seen["secure"] = (sql, ds_id, uid, ws)
            if deny:
                raise deny
            return secured, PermissionResult(policies_applied=["rls:es_where_and"])

        def fake_query_page(pattern, sql, **kw):
            seen["page_call"] = (pattern, sql, kw)
            return page if page is not None else dict(self.PAGE)

        monkeypatch.setattr(qe, "_is_es_datasource", lambda _ds_id: es)
        monkeypatch.setattr(qe, "secure_read_sql", fake_secure)
        monkeypatch.setattr(es_pagination, "query_page", fake_query_page)
        monkeypatch.setattr(es_client, "ctx_for", lambda _i: _es_ctx(9))
        audits = []
        monkeypatch.setattr(qe, "log_read_success_audit",
                            lambda *a, **k: audits.append(("ok", a, k)))
        monkeypatch.setattr(qe, "log_read_denial_audit",
                            lambda *a, **k: audits.append(("deny", a, k)))
        return qe, seen, audits

    def test_secured_sql_is_what_gets_paginated(self, monkeypatch):
        """游标检索只能拿改写后的正文（拿原句 = RLS 旁路）。"""
        qe, seen, audits = self._stub(monkeypatch, secured='SELECT "a" AS m FROM "t_x" LIMIT 20')
        out = qe.execute_es_page_with_permission(
            'SELECT "a" AS m FROM "t_x" LIMIT 20', 5, {"user_id": 7, "username": "u"}, 1,
            page_size=20, page_num=2, context_key="k1")
        assert seen["page_call"][1] == 'SELECT "a" AS m FROM "t_x" LIMIT 20'
        assert seen["page_call"][0] == "t_x"                 # 索引目标从改写后正文取
        assert out["total"] == 4468044 and out["paging_mode"] == "cursor"
        assert out["rows"] == [{"m": "appmanager"}]
        assert audits and audits[0][0] == "ok"               # 成功侧审计照落

    def test_no_identity_is_refused_before_touching_the_cluster(self, monkeypatch):
        from backend.core.governed_query import NoIdentityError

        qe, seen, _ = self._stub(monkeypatch)
        with pytest.raises(NoIdentityError):
            qe.execute_es_page_with_permission(ALIAS_SQL, 5, {"user_id": 0}, 1,
                                               page_size=20, page_num=1, context_key="k")
        assert "secure" not in seen and "page_call" not in seen

    def test_non_es_datasource_is_refused_not_degraded(self, monkeypatch):
        """不得降级成 LIMIT/OFFSET 分页继续“能用”：翻页口径静默换形状 = 错页。"""
        qe, seen, _ = self._stub(monkeypatch, es=False)
        with pytest.raises(ValueError, match="Elasticsearch"):
            qe.execute_es_page_with_permission(ALIAS_SQL, 5, {"user_id": 7}, 1,
                                               page_size=20, page_num=1, context_key="k")
        assert "secure" not in seen

    def test_permission_denial_is_audited(self, monkeypatch):
        qe, _seen, audits = self._stub(monkeypatch, deny=PermissionError("行级策略未命中正文"))
        with pytest.raises(PermissionError):
            qe.execute_es_page_with_permission(ALIAS_SQL, 5, {"user_id": 7}, 1,
                                               page_size=20, page_num=1, context_key="k")
        assert audits and audits[0][0] == "deny"

    def test_body_without_index_reference_is_refused(self, monkeypatch):
        """拿不到唯一 FROM 目标就不能下发：猜索引名 = 可能查到别的集合的行冒充答案。

        （多表正文由 `parse_select` 的 `_MULTI_INDEX_FROM` 硬门在同一实现里拒，不在这里重复判。）
        """
        qe, seen, _ = self._stub(monkeypatch, secured="SELECT 1 AS x LIMIT 10")
        with pytest.raises(ValueError, match="唯一的索引别名"):
            qe.execute_es_page_with_permission(ALIAS_SQL, 5, {"user_id": 7}, 1,
                                               page_size=20, page_num=1, context_key="k")
        assert "page_call" not in seen

    def test_main_entry_shares_the_same_secure_and_audit_helpers(self, monkeypatch):
        """护栏 §1：分页口不是第二套治理实现 —— 主口也必须走同一对 helper。"""
        import inspect

        import backend.core.query_executor as qe

        src = inspect.getsource(qe.execute_query_with_permission)
        assert "secure_read_sql(" in src and "log_read_success_audit(" in src \
            and "log_read_denial_audit(" in src
        page_src = inspect.getsource(qe.execute_es_page_with_permission)
        assert "secure_read_sql(" in page_src and "log_read_success_audit(" in page_src
        # 两个入口都不得自己拿未审核的 SQL 去下发
        assert "enforce_sql(" not in src and "enforce_sql(" not in page_src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
