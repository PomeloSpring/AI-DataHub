"""ES 型数据集（数据集第三种定义类型）的契约回归测试.

覆盖（全部不依赖真实 ES 集群：只 monkeypatch `es_client.run_sql`，
`parse_select`/白名单是纯配置判定，可原样跑）：

- 定义校验分支：白名单外范围拒、缺 LIMIT 拒、JOIN 拒、正文超出所选范围拒、
  DATE_FORMAT 拒（实测"语法过但错数"，属必须硬拦的静默缺陷）
- ES 型不共存：sql_bodies/dialect/object_key/datasource_id 全部显式拒
- 字段声明：度量 expr 在 ES 不可用（字段层派生靠 sqlglot，ES 上永不生效）
- 取数分派：ES 型不走 resolve_effective_source（不挂在数据源上），不经 governed_execute
- 结果可诊断：execution_mode='es' + governance 标注 + 审计落条（成功与拒绝均落）
- 不支持项 fail-loud：运行时 filters/order、行级 scope 一律显式拒；分页走 search_after
  游标通道（ES SQL 语法里没有 OFFSET），旧分页参数一律失效
- 身份 fail-closed；dataset:es 只把关配置面（消费面不受它限制）
- 迁移落地：adh_datasets.es_pattern 列存在、dataset:es 已登记且 api_pattern 留空
"""
import json

import pytest

from backend.common import config
from backend.core import es_client
from backend.modules.viz.services import dataset_registry as reg
from backend.modules.viz.services import dataset_service as svc
from backend.modules.viz.services import es_pagination
from backend.core.governed_query import NoIdentityError

PATTERN = "logs-*"
NARROW = "logs-*2026.11"
BODY = f'SELECT DATE_TRUNC(\'DAY\', "@timestamp") AS d, COUNT(*) AS c FROM "{NARROW}" ' \
       f'GROUP BY DATE_TRUNC(\'DAY\', "@timestamp") ORDER BY d ASC LIMIT 30'


@pytest.fixture(autouse=True)
def _whitelist(monkeypatch):
    """白名单固定为 `logs-*`（判定用 fnmatch，与在线部署同口径）。"""
    monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", (PATTERN,), raising=False)
    monkeypatch.setattr(config, "ES_MAX_ROWS", 1000, raising=False)
    monkeypatch.setattr(config, "ES_MCP_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "ES_URL", "https://es.invalid:9200", raising=False)


def _es_row(**over) -> dict:
    row = {"id": 900, "name": "设备告警按天", "status": "active", "source_type": "es",
           "es_pattern": NARROW, "object_key": "", "datasource_id": 0,
           "available_datasources": "[]", "sql_query": BODY, "variables": "{}",
           "sql_bodies": "{}", "field_config": "[]", "owner_id": 1}
    row.update(over)
    return row


class _Boom:
    """被调用即抛：用来锁定"这条路径不得经过某个实现"。"""

    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **kw):
        raise AssertionError(f"{self.name} 不应被 ES 分支调用")


# ── 来源类型派生与判别 ────────────────────────────────────────────────

class TestSourceType:
    def test_es_wins_over_sql(self):
        assert svc._derive_source_type("", BODY, NARROW) == "es"

    def test_both_still_works_for_legacy(self):
        """存量双定义口径零变化（es_pattern 为空时行为与改动前一致）。"""
        assert svc._derive_source_type("order", "SELECT 1 FROM t LIMIT 1") == "both"
        assert svc._derive_source_type("", "SELECT 1 FROM t LIMIT 1") == "sql"
        assert svc._derive_source_type("order", "") == "semantic"

    def test_is_es_dataset_reads_both_fields(self):
        assert svc.is_es_dataset({"source_type": "es"})
        assert svc.is_es_dataset({"source_type": "sql", "es_pattern": PATTERN})
        assert not svc.is_es_dataset({"source_type": "sql", "es_pattern": ""})


# ── 定义校验（保存这一关 fail-loud）────────────────────────────────────

class TestEsDefinitionValidation:
    def test_accepts_valid_body(self):
        errors, _stored = reg.validate_es_definition(
            {"es_pattern": NARROW, "sql_query": BODY})
        assert errors == [], errors

    def test_rejects_pattern_outside_allowlist(self):
        errors, _ = reg.validate_es_definition(
            {"es_pattern": "secret-*", "sql_query": 'SELECT COUNT(*) AS c FROM "secret-*" LIMIT 10'})
        assert any("不在当前部署开放的范围内" in e for e in errors)
        # 错误文案必须给出可查清单（引导改写法，不是只说"不行"）
        assert any(PATTERN in e for e in errors)

    def test_rejects_missing_limit(self):
        errors, _ = reg.validate_es_definition(
            {"es_pattern": NARROW, "sql_query": f'SELECT COUNT(*) AS c FROM "{NARROW}"'})
        assert any("LIMIT" in e for e in errors)

    def test_rejects_join(self):
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW,
            "sql_query": f'SELECT a.x FROM "{NARROW}" a JOIN "{NARROW}" b ON a.x = b.x LIMIT 10'})
        assert any("JOIN" in e for e in errors)

    def test_rejects_body_wider_than_declared_scope(self):
        """数据集级收窄：正文 FROM 必须落在 es_pattern 范围内，否则图表复用即越界。"""
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW,
            "sql_query": 'SELECT COUNT(*) AS c FROM "logs-2027.01" LIMIT 10'})
        assert any("超出本数据集已选范围" in e for e in errors)

    def test_rejects_date_format_silently_wrong(self):
        """实测该函数语法通过但把格式串当常量返回 → 属于"不报错的错数"，必须硬拦。"""
        bad = (f'SELECT DATE_FORMAT("@timestamp", \'yyyy-MM\') AS m, COUNT(*) AS c '
               f'FROM "{NARROW}" GROUP BY DATE_FORMAT("@timestamp", \'yyyy-MM\') LIMIT 10')
        errors, _ = reg.validate_es_definition({"es_pattern": NARROW, "sql_query": bad})
        assert any("DATE_FORMAT" in e for e in errors)
        assert any("DATE_TRUNC" in e for e in errors)   # 必须给出可用替代

    def test_rejects_multi_index_from(self):
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW,
            "sql_query": f'SELECT COUNT(*) AS c FROM "{NARROW}", "logs-*" LIMIT 10'})
        assert any("多表" in e for e in errors)

    def test_rejects_empty_body(self):
        errors, _ = reg.validate_es_definition({"es_pattern": NARROW, "sql_query": ""})
        assert any("正文" in e for e in errors)

    def test_rejects_coexisting_definitions(self):
        """ES × 语义对象 / 多方言正文 / dialect 标注都不成立（存了永不命中=死键）。"""
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW, "sql_query": BODY, "object_key": "order"})
        assert any("本体对象" in e for e in errors)
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW, "sql_query": BODY, "sql_bodies": {"mysql": "SELECT 1"}})
        assert any("sql_bodies" in e for e in errors)
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW, "sql_query": BODY, "dialect": "mysql"})
        assert any("dialect" in e for e in errors)

    def test_db_row_shape_bodies_string_is_not_rejected(self):
        """更新路径会把存量行的 JSON 列以**字符串**合进来（sql_bodies="{}"）。

        形状归一在判空之前做，否则 ES 数据集改正文保存必被误拒（历史缺陷）。
        """
        errors, _ = reg.validate_es_definition(_es_row())
        assert errors == [], errors

    def test_measure_expr_rejected_but_agg_ok(self):
        """字段层 expr 靠 sqlglot 解析拼回 SQL，ES 上不可用；聚合必须写在正文里。"""
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW, "sql_query": BODY,
            "field_config": [{"field": "c", "role": "measure", "expr": "SUM(c)"}]})
        assert any("expr" in e for e in errors)
        errors, _ = reg.validate_es_definition({
            "es_pattern": NARROW, "sql_query": BODY,
            "field_config": [{"field": "c", "role": "measure", "agg": "SUM"}]})
        assert errors == [], errors

    def test_touched_tables_server_derived(self):
        _errors, stored = reg.validate_es_definition(
            {"es_pattern": NARROW, "sql_query": BODY})
        assert json.loads(stored["touched_tables"]) == [NARROW]

    def test_validate_definition_dispatches_es(self):
        """主入口必须把 ES 型分流出去：否则正文会去走 sqlglot（无 ES 方言，必误判）。"""
        errors, _ = reg.validate_definition(
            {"source_type": "es", "es_pattern": NARROW, "sql_query": BODY})
        assert errors == [], errors

    def test_probe_es_sql_does_not_use_sqlglot(self, monkeypatch):
        """行为断言（不看源码文本）：把 sqlglot 解析入口拆成"被调用即报错"，
        合法 ES 正文仍须校验通过 —— 证明它没走过治理域解析器（无 ES 方言，走过必误杀）。"""
        from backend.semantics import sql_guard

        monkeypatch.setattr(sql_guard, "parse_readonly", _Boom("sqlglot.parse_readonly"))
        monkeypatch.setattr(sql_guard, "extract_tables", _Boom("sqlglot.extract_tables"))
        errors, indexes = reg.probe_es_sql(BODY, {}, NARROW)
        assert errors == [], errors
        assert indexes == [NARROW]


# ── 取数分支 ──────────────────────────────────────────────────────────

class _FakeEs:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result or {"columns": [{"name": "d", "type": "datetime"},
                                               {"name": "c", "type": "long"}],
                                  "rows": [["2026-11-01T00:00:00.000Z", 10],
                                           ["2026-11-02T00:00:00.000Z", 20]],
                                  "row_count": 2, "indexes": [NARROW]}
        self._error = error

    def run_sql(self, sql, *, fetch_size=200):
        self.calls.append((sql, fetch_size))
        if self._error:
            raise self._error
        return self._result


class TestQueryEsDispatch:
    def test_query_dataset_routes_to_es_without_effective_source(self, monkeypatch):
        """ES 不挂在数据源上：走定源会被"可用范围 ∩ 授权集 = 空"误拒成无权，必须绕开。"""
        fake = _FakeEs()
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        monkeypatch.setattr(svc, "resolve_effective_source", _Boom("resolve_effective_source"))
        monkeypatch.setattr(svc, "effective_source_name", _Boom("effective_source_name"))
        monkeypatch.setattr(svc, "_scope_filters", _Boom("_scope_filters"))
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        audits = []
        monkeypatch.setattr(svc, "_es_audit",
                            lambda *a, **kw: audits.append((a, kw)))

        out = svc.query_dataset(900, {"limit": 30},
                                {"user_id": 7, "username": "u", "role": "analyst",
                                 "workspace_id": 1})
        assert out["execution_mode"] == "es"
        assert out["columns"] == ["d", "c"]
        assert out["rows"][0] == {"d": "2026-11-01T00:00:00.000Z", "c": 10}
        assert "datasource" not in out      # ES 不属于任何数据源，不得假标"默认引擎"
        assert out["es_scope"] == NARROW
        assert out["governance"]["channel"] == "builtin_elasticsearch"
        assert out["governance"]["column_policy"] == "not_applied"
        assert audits and audits[0][1].get("ok") is True

    def test_never_touches_governed_execute(self, monkeypatch):
        """护栏 §1 的显式例外：本分支不得经治理入口（也绝不能裸连数据源）。
        不是"绕过了治理"，而是"治理手法对 ES 不成立"，见 dataset_service「ES 分支」段注释。"""
        fake = _FakeEs()
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: None)
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        import backend.core.governed_query as gq
        monkeypatch.setattr(gq, "governed_execute", _Boom("governed_execute"))

        out = svc.query_dataset(900, {"limit": 10},
                                {"user_id": 7, "username": "u", "role": "analyst",
                                 "workspace_id": 1})
        assert out["execution_mode"] == "es"

    def test_duplicate_columns_disambiguated(self, monkeypatch):
        """ES 允许 SELECT 同一列两次；不得因重名把列吞掉（护栏 §8 同 df_serialize 口径）。"""
        fake = _FakeEs(result={"columns": [{"name": "a", "type": "keyword"},
                                           {"name": "a", "type": "keyword"}],
                               "rows": [["x", "y"]], "row_count": 1, "indexes": [NARROW]})
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: None)
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        monkeypatch.setattr(es_client, "parse_select", lambda sql: (sql, 30))

        out = svc.query_dataset(900, {"limit": 10},
                                {"user_id": 7, "username": "u", "role": "analyst",
                                 "workspace_id": 1})
        assert out["columns"] == ["a", "a__1"]
        assert out["rows"] == [{"a": "x", "a__1": "y"}]

    def test_no_identity_fail_closed(self, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        with pytest.raises(NoIdentityError):
            svc.query_dataset(900, {"limit": 10},
                              {"user_id": 0, "username": "", "role": "analyst",
                               "workspace_id": 1})

    def test_channel_not_ready_raises_not_empty(self, monkeypatch):
        """通道不可用必须是错误，不能回空结果 —— 空结果会被读成"这段时间没有告警"。"""
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        monkeypatch.setattr(svc, "require_es_channel",
                            lambda: (_ for _ in ()).throw(ValueError("通道当前不可用")))
        with pytest.raises(ValueError, match="不可用"):
            svc.query_dataset(900, {"limit": 10},
                              {"user_id": 7, "username": "u", "role": "analyst",
                               "workspace_id": 1})

    def test_allowlist_revoked_is_rejected(self, monkeypatch):
        """白名单被收回后取数必须拒，不静默换模式继续出数、也不回空结果。

        执行期唯一硬门是正文 FROM 目标过 `parse_select`（定义保存时另校
        正文 ⊆ es_pattern），两层都在同一个实现上，不会飘。
        """
        monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", (), raising=False)
        fake = _FakeEs()
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        with pytest.raises(ValueError, match="未开放"):
            svc._query_es(_es_row(), {"limit": 10},
                          {"user_id": 7, "username": "u", "role": "analyst"})
        assert fake.calls == []      # 拒在下发之前

    def test_retired_pagination_params_are_inert(self, monkeypatch):
        """旧分页口径（page_offset / page_count_sql / page_limit）不再开启任何分页。

        ES 分页现在只认 page_size/page_num + 数据集 paginable；拿旧字段“半开”分页
        就是“同一页当第二页”的伪分页形态，必须锁成完全无效。
        """
        fake = _FakeEs()
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        for stale in ({"page_offset": 10}, {"page_count_sql": "SELECT COUNT(*) FROM t"},
                      {"page_limit": 20}):
            out = svc._query_es(_es_row(), stale,
                                {"user_id": 7, "username": "u", "role": "analyst"})
            assert "paging" not in out and "total" not in out
        assert len(fake.calls) == 3          # 三条都走原通道，没有半截分页查询打到集群

    def test_non_paginable_es_dataset_ignores_page_params(self, monkeypatch):
        """未标支持分页：page_size/page_num 不生效，整页返回（统计型正文的本意）。"""
        fake = _FakeEs()
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        boom = _Boom("query_page")
        monkeypatch.setattr(es_pagination, "query_page", boom)
        out = svc._query_es(_es_row(paginable=0), {"page_size": 20, "page_num": 2},
                            {"user_id": 7, "username": "u", "role": "analyst"})
        assert "paging" not in out and fake.calls

    def test_paginable_es_dataset_pages_via_cursor_channel(self, monkeypatch):
        """标了支持分页 → 走 search_after 游标通道（不是在 SQL 里拼 OFFSET）。"""
        hit = {}

        def fake_query_page(pattern, sql, *, page_size, page_num, context_key):
            hit["args"] = (pattern, page_size, page_num)
            hit["context_key"] = context_key
            return {"columns": ["d", "c"], "rows": [["2026-11-01", 10]], "row_count": 1,
                    "total": 12345, "page_size": page_size, "page_num": page_num,
                    "paging_mode": "cursor", "sql": sql}

        monkeypatch.setattr(es_pagination, "query_page", fake_query_page)
        out = svc._query_es(_es_row(paginable=1), {"page_size": 20, "page_num": 2},
                            {"user_id": 7, "username": "u", "role": "analyst"})
        assert hit["args"] == (NARROW, 20, 2)
        # 上下文签名不含渲染结果（服务端补的时间窗会逐秒漂，含它就永远翻不到第 2 页）
        assert hit["context_key"] == es_pagination.paging_key(
            NARROW, _es_row(paginable=1)["sql_query"], {}, 20)
        # 集群无 PIT ⇒ 游标式不能随机跳页：前端据此只给上/下页按钮
        assert out["paging"]["mode"] == "cursor" and out["paging"]["can_jump"] is False
        assert out["total"] == 12345

    def test_runtime_filters_rejected(self, monkeypatch):
        """看板筛选器要往正文拼 WHERE：ES 无 AST 改写可用，拼接即注入口，必须拒。"""
        fake = _FakeEs()
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        with pytest.raises(ValueError, match="筛选器"):
            svc._query_es(_es_row(), {"filters": [{"field": "c", "op": "gt", "value": 1}]},
                          {"user_id": 7, "username": "u", "role": "analyst"})
        assert fake.calls == []

    def test_es_error_is_audited_and_surfaced(self, monkeypatch):
        fake = _FakeEs(error=es_client.EsError('索引「logs-*」未开放查询'))
        audits = []
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: audits.append(kw))
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        with pytest.raises(ValueError, match="未开放"):
            svc._query_es(_es_row(), {"limit": 10},
                          {"user_id": 7, "username": "u", "role": "analyst"})
        assert audits and audits[0]["ok"] is False

    def test_unexpected_error_not_leaked(self, monkeypatch):
        """非预期异常（原始体可能含索引表达式/集群信息）对外只回通用文案。"""
        fake = _FakeEs(error=RuntimeError("connect to es-cn-xxxx:9200 failed"))
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: None)
        with pytest.raises(ValueError) as exc:
            svc._query_es(_es_row(), {"limit": 10},
                          {"user_id": 7, "username": "u", "role": "analyst"})
        assert "9200" not in str(exc.value) and "es-cn" not in str(exc.value)

    def test_render_body_undeclared_var_rejected(self, monkeypatch):
        """参数必须按声明渲染：未声明/缺参不得静默丢条件（丢了就多数）。"""
        row = _es_row(sql_query='SELECT COUNT(*) AS c FROM "logs-*" '
                                'WHERE "region" = ${region} LIMIT 10',
                      variables=json.dumps({"region": {"type": "string"}}))
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        out = svc._render_es_body(row, {"region": "cn"})
        assert "'cn'" in out
        with pytest.raises(ValueError):
            svc._render_es_body(row, {})          # 缺参且无默认值 → 拒


# ── 定义面不支持项与权限把关 ───────────────────────────────────────────

class TestEsConfigGates:
    def test_row_scopes_rejected(self, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        monkeypatch.setattr(svc, "_require_editor", lambda *a: None)
        with pytest.raises(ValueError, match="行级范围"):
            svc.set_scopes(900, [{"subject_type": "role", "subject_id": 2,
                                  "filters": [{"field": "c", "op": "gt", "value": 0}]}],
                                 {"user_id": 1, "role": "admin", "username": "a"})

    def test_legacy_scopes_still_allowed(self, monkeypatch):
        """非 ES 数据集的 scope 行为零变化。"""
        calls = []
        monkeypatch.setattr(svc, "get_dataset",
                            lambda _id: _es_row(source_type="sql", es_pattern=""))
        monkeypatch.setattr(svc, "_require_editor", lambda *a: None)
        monkeypatch.setattr(svc, "execute_write",
                            lambda sql, params=None: calls.append(sql))
        svc.set_scopes(900, [{"subject_type": "role", "subject_id": 2,
                              "filters": [{"field": "c", "op": "gt", "value": 0}]}],
                             {"user_id": 1, "role": "admin", "username": "a"})
        assert any("adh_dataset_scopes" in c for c in calls)

    def test_config_perm_fail_closed(self, monkeypatch):
        from backend.core.role_service import role_service as rs

        monkeypatch.setattr(rs, "get_user_role_perm_codes",
                            lambda _uid: ["dataset:read", "dataset:manage"])
        with pytest.raises(PermissionError, match="dataset:es"):
            svc._require_es_config_perm({"user_id": 1, "role": "analyst"}, "create")

    def test_config_perm_granted_passes(self, monkeypatch):
        from backend.core.role_service import role_service as rs

        monkeypatch.setattr(rs, "get_user_role_perm_codes",
                            lambda _uid: ["dataset:es"])
        svc._require_es_config_perm({"user_id": 1, "role": "analyst"}, "create")

    def test_admin_bypasses_code_check(self):
        svc._require_es_config_perm({"user_id": 1, "role": "admin"}, "create")

    def test_options_endpoints_need_code(self, monkeypatch):
        from backend.core.role_service import role_service as rs

        monkeypatch.setattr(rs, "get_user_role_perm_codes", lambda _uid: [])
        with pytest.raises(PermissionError):
            svc.es_pattern_options({"user_id": 1, "role": "analyst"})
        with pytest.raises(PermissionError):
            svc.es_field_options(PATTERN, {"user_id": 1, "role": "analyst"})


# ── 显示与可见性（ui-resource-display：不得把内部枚举/裸 id 当名称）────────

class TestDisplayAndVisibility:
    def test_list_hides_es_when_channel_down(self, monkeypatch):
        monkeypatch.setattr(svc, "es_channel_state", lambda: {"ready": False, "state": "disabled"})
        monkeypatch.setattr(svc, "execute_query", lambda *a, **kw: [_es_row()])
        monkeypatch.setattr(svc, "_load_datasource_catalog", lambda: {})
        monkeypatch.setattr(svc, "_allowed_source_ids", lambda _i: set())
        monkeypatch.setattr(svc, "resolve_fields", lambda *a, **kw: [])
        rows = svc.list_datasets({"user_id": 1, "role": "admin", "workspace_id": 1})
        assert rows == []      # 无可用通道 → 隐藏，不给"点得开、一取数就报错"的入口

    def test_list_shows_es_with_readable_labels(self, monkeypatch):
        monkeypatch.setattr(svc, "es_channel_state", lambda: {"ready": True})
        monkeypatch.setattr(svc, "execute_query", lambda *a, **kw: [_es_row()])
        monkeypatch.setattr(svc, "_load_datasource_catalog", lambda: {})
        monkeypatch.setattr(svc, "_allowed_source_ids", lambda _i: set())
        monkeypatch.setattr(svc, "resolve_fields", lambda *a, **kw: [])
        rows = svc.list_datasets({"user_id": 1, "role": "admin", "workspace_id": 1})
        assert len(rows) == 1
        row = rows[0]
        assert row["datasource_name"] == svc._ES_CHANNEL_LABEL
        assert "es_pattern" in row and row["es_pattern"] == NARROW
        assert "900" not in str(row["datasource_name"])   # 不是裸 id

    def test_narrowed_pattern_still_visible(self, monkeypatch):
        """收窄模式（logs-*2026.11）被白名单 logs-* 覆盖但仍可查：
        可见性判定必须按 fnmatch，不能拿字面值比白名单元组（历史缺陷）。"""
        monkeypatch.setattr(svc, "es_channel_state", lambda: {"ready": True})
        monkeypatch.setattr(svc, "execute_query", lambda *a, **kw: [_es_row()])
        monkeypatch.setattr(svc, "_load_datasource_catalog", lambda: {})
        monkeypatch.setattr(svc, "_allowed_source_ids", lambda _i: set())
        monkeypatch.setattr(svc, "resolve_fields", lambda *a, **kw: [])
        assert svc.list_datasets({"user_id": 1, "role": "admin", "workspace_id": 1})

    def test_es_row_excluded_when_filtering_by_datasource(self, monkeypatch):
        """按具体数据源筛选时 ES 不属于任何源（不静默当命中）。"""
        monkeypatch.setattr(svc, "es_channel_state", lambda: {"ready": True})
        monkeypatch.setattr(svc, "execute_query", lambda *a, **kw: [_es_row()])
        cat = {"prod-mysql": {"id": 5, "name": "prod-mysql", "db_type": "mysql",
                              "site_code": ""}}
        monkeypatch.setattr(svc, "_load_datasource_catalog", lambda: cat)
        monkeypatch.setattr(svc, "_allowed_source_ids", lambda _i: {5})
        rows = svc.list_datasets({"user_id": 1, "role": "admin", "workspace_id": 1},
                                 datasource_id=5)
        assert rows == []


# ── 横向护栏：ES 型不得被治理域消费路径拿去做语义编译 ────────────────────

class TestNotConsumedByGovernedPaths:
    """ES 正文不在数据权限治理域：语义层与业务本体路由图都必须拒它，不静默编译。"""

    def test_planner_refuses_es_dataset_binding(self, monkeypatch):
        from backend.semantics import planner
        from backend.semantics.models import ResolvedBinding, SemanticQuery

        monkeypatch.setattr(planner, "_load_dataset_row", lambda ref: _es_row())
        binding = ResolvedBinding(
            object_key="device_alert", datasource_id=0, physical_table="",
            bind_kind="dataset", dataset_ref="设备告警按天")
        query = SemanticQuery(object="device_alert", metrics=["c"], dimensions=["d"])
        warnings: list = []
        plan = planner._plan_dataset(query, binding, "mysql", warnings)

        assert not plan.sql, "ES 数据集不得被编译成可执行 SQL"
        assert any("Elasticsearch" in w for w in warnings), warnings

    def test_planner_still_compiles_sql_dataset(self, monkeypatch):
        """护栏只拦 ES；sql 型数据集行为零变化（不因新分支误拒）。"""
        from backend.semantics import planner
        from backend.semantics.models import ResolvedBinding, SemanticQuery

        row = _es_row(source_type="sql", es_pattern="", datasource_id=5,
                      sql_bodies=json.dumps({"mysql": "SELECT 1 AS d LIMIT 10"}))
        monkeypatch.setattr(planner, "_load_dataset_row", lambda ref: row)
        binding = ResolvedBinding(
            object_key="device_alert", datasource_id=5, physical_table="t_x",
            bind_kind="dataset", dataset_ref="设备告警按天")
        warnings: list = []
        plan = planner._plan_dataset(SemanticQuery(object="device_alert"), binding,
                                     "mysql", warnings)

        assert plan.sql, "sql 型数据集不得被 ES 护栏误拒"
        assert not any("Elasticsearch" in w for w in warnings), warnings

    def test_business_asset_ontology_skips_es_dataset_with_note(self):
        from backend.modules.catalog.services import business_asset_ontology as bao

        data = {
            "datasources": [{"id": 5, "name": "主库", "db_type": "mysql",
                             "database_name": "d", "is_default": 1}],
            "datasets": [
                {"id": 900, "name": "设备告警按天", "description": "", "source_type": "es",
                 "es_pattern": NARROW, "object_key": "", "datasource_id": 0, "chart_type": ""},
                {"id": 901, "name": "订购明细", "description": "", "source_type": "sql",
                 "es_pattern": "", "object_key": "", "datasource_id": 5, "chart_type": ""},
            ],
            "sync_tasks": [], "etl_tasks": [], "quality_rules": [], "quality_results": [],
        }
        objects, notes = bao._build_asset_objects(data, {})

        names = [o["display_name"] for o in objects]
        assert not any("设备告警按天" in n for n in names), \
            "ES 数据集不得生成业务本体节点（会把路由引到未治理通道）"
        assert any("订购明细" in n for n in names), names
        assert any("Elasticsearch" in n and "设备告警按天" in n for n in notes), \
            "排除必须可诊断（显式 note），不得静默过滤"


# ── 迁移落地核验（库里有生效行才算完成，不是只提交文件）──────────────────

def _db_conn_or_skip():
    try:
        from backend.common.db import get_metadata_conn

        return get_metadata_conn()
    except Exception:  # noqa: BLE001 — 本地无库时跳过，不伪装成通过
        pytest.skip("元数据库不可用，跳过迁移落地核验")


class TestMigrationApplied:
    def test_column_and_perm_exist(self):
        with _db_conn_or_skip() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets' "
                    "AND COLUMN_NAME = 'es_pattern'")
                assert cur.fetchone(), "adh_datasets.es_pattern 未应用（迁移只提交了文件没跑库）"

                cur.execute("SELECT label, menu_key, api_pattern, ai_access "
                            "FROM adh_perm_registry WHERE perm_code = 'dataset:es'")
                row = cur.fetchone()
                assert row, "dataset:es 未登记"
                vals = list(row.values()) if isinstance(row, dict) else list(row)
                assert vals[1] == "data:datasets", "权限点必须挂到菜单，否则角色页渲不出（配了也白配）"
                # api_pattern 留空是显式设计：路径匹配分不出 ES 与数据集端点，裁决在代码按码做
                assert not (vals[2] or ""), "dataset:es 不得配 api_pattern（会是假门）"
                # ai_access=none 才对：AI 侧访问 ES 走内置 MCP 工具授权，不由本码裁决
                assert vals[3] == "none", "本码没有 AI 消费方，配非 none 就是空承诺"


# ── 清单筛选：数据源下拉要能选到 ES 通道 ─────────────────────────────────
# ES 型数据集不挂在 adh_datasources 上，拿 datasource_id 永远筛不到它（历史缺口：
# 卡片上显示的是「内置 Elasticsearch 通道」，下拉里却没有这一项）。

class TestChannelFilter:
    IDENT = {"user_id": 1, "role": "admin", "workspace_id": 1}

    def _stub(self, monkeypatch, rows, *, ready=True):
        monkeypatch.setattr(svc, "es_channel_state", lambda: {"ready": ready})
        monkeypatch.setattr(svc, "execute_query", lambda *a, **kw: [dict(r) for r in rows])
        # 源目录给一份真的：按数据源筛时后端要先拿 id → 业务名，查不到会当作"源已不存在"直接空命中
        monkeypatch.setattr(svc, "_load_datasource_catalog",
                            lambda: {"prd-cn": {"id": 11, "name": "prd-cn",
                                                "db_type": "mysql", "site_code": "cn"}})
        monkeypatch.setattr(svc, "_allowed_source_ids", lambda _i: {11})
        monkeypatch.setattr(svc, "resolve_fields", lambda *a, **kw: [])
        # SQL 行的可用范围固定给一个源，避免依赖真实源目录
        monkeypatch.setattr(svc, "usable_scope", lambda *a, **kw: ([{"id": 11, "name": "prd-cn"}], []))

    def _mixed(self):
        return [_es_row(), _es_row(id=901, name="按天案例数", source_type="sql",
                                   es_pattern="", datasource_id=11)]

    def test_es_channel_is_selectable_and_isolates_es_rows(self, monkeypatch):
        self._stub(monkeypatch, self._mixed())
        rows = svc.list_datasets(self.IDENT, channel="es")
        assert [r["id"] for r in rows] == [900]
        assert rows[0]["datasource_name"] == svc._ES_CHANNEL_LABEL

    def test_mcp_channel_value_is_same_filter_as_es(self, monkeypatch):
        """`mcp:<服务>`（筛选器选项的 value）与旧写法 `es` 同义，不得两种写法两套结果。"""
        self._stub(monkeypatch, self._mixed())
        assert (svc.list_datasets(self.IDENT, channel="mcp:elasticsearch")
                == svc.list_datasets(self.IDENT, channel="es"))

    def test_unknown_mcp_channel_rejected_with_candidates(self, monkeypatch):
        """未登记的 MCP 服务不得静默当“不限”：那会把空清单误读成“没数据集”。"""
        self._stub(monkeypatch, self._mixed())
        with pytest.raises(ValueError, match="不支持的取数通道筛选"):
            svc.list_datasets(self.IDENT, channel="mcp:clickhouse")

    def test_channel_options_come_from_builtin_mcp_registry(self, monkeypatch):
        """筛选选项回源 builtin_mcp（与 MCP 服务页同一口径），且只列能承载数据集的服务。

        未就绪的通道照旧返回并带原因 —— 遮掉一项会被读成“这个部署没这通道”。
        """
        from backend.modules.mind.execution import builtin_mcp

        monkeypatch.setattr(builtin_mcp, "catalog", lambda: [
            {"name": "elasticsearch", "label": "Elasticsearch 检索服务", "state": "ready",
             "status_message": "", "open_view_count": 4},
            {"name": "some_other_mcp", "label": "别的内置服务", "state": "ready",
             "status_message": "", "open_view_count": 0},
        ])
        monkeypatch.setattr(builtin_mcp, "state_of", lambda name: {
            "ready": False, "state": "disabled", "status_message": "未启用（部署侧开关关闭）",
            "open_view_count": 0})

        items = svc.mcp_channel_options()
        assert [i["server"] for i in items] == ["elasticsearch"], "只能承载数据集的 MCP 服务才进筛选清单"
        es = items[0]
        assert es["value"] == "mcp:elasticsearch" and es["channel"] == "es"
        # 显示用业务名（前端不得自己拼文案）；分组名非空才能渲成按 MCP 分组的选择框
        assert es["label"] == svc._ES_CHANNEL_LABEL and es["group"]
        assert es["ready"] is False and "未启用" in es["status_message"], "未就绪要带原因，不是遮掉"

    def test_no_channel_returns_both_channels(self, monkeypatch):
        self._stub(monkeypatch, self._mixed())
        assert sorted(r["id"] for r in svc.list_datasets(self.IDENT)) == [900, 901]

    def test_datasource_filter_still_excludes_es_rows(self, monkeypatch):
        """选了具体数据源时 ES 行不命中（它不在任何源上），与旧行为零变化。"""
        self._stub(monkeypatch, self._mixed())
        assert [r["id"] for r in svc.list_datasets(self.IDENT, datasource_id=11)] == [901]

    def test_unknown_channel_rejected_not_ignored(self, monkeypatch):
        """传错值不得静默当"不限"：那会让用户以为筛过了。"""
        self._stub(monkeypatch, self._mixed())
        with pytest.raises(ValueError, match="不支持的取数通道筛选"):
            svc.list_datasets(self.IDENT, channel="doris")

    def test_channel_and_datasource_are_mutually_exclusive(self, monkeypatch):
        self._stub(monkeypatch, self._mixed())
        with pytest.raises(ValueError, match="不能同时生效"):
            svc.list_datasets(self.IDENT, datasource_id=11, channel="es")

    def test_down_channel_filters_to_empty(self, monkeypatch):
        """通道未就绪：ES 行本就不可见（可见性优先于筛选，不给"选得出、永远 0 结果"的假筛选项）。"""
        self._stub(monkeypatch, self._mixed(), ready=False)
        assert svc.list_datasets(self.IDENT, channel="es") == []

    def test_api_exposes_channel_param(self):
        """端点必须真的收下这个参数（登记不消费 = 空头承诺）。"""
        import inspect

        from backend.modules.viz.api import datasets as api

        src = inspect.getsource(api.list_datasets)
        assert "channel" in src and "channel=channel" in src

    def test_channels_route_registered_before_dataset_id(self):
        """`/channels` 是静态路由，必须注册在 `/{dataset_id}` 之前，否则会被当成 id=channels 404。"""
        import inspect

        from backend.modules.viz.api import datasets as api

        paths = [r.path for r in api.router.routes]
        assert "/channels" in paths
        assert paths.index("/channels") < paths.index("/{dataset_id}")
        assert "mcp_channel_options" in inspect.getsource(api.dataset_channels)


# ── ES 时间参数留空时的服务端滚动窗 ────────────────────────────────────
# ES SQL 拿不到可用的"本地当前时刻"：NOW() 不支持、CURRENT_TIMESTAMP 是 UTC ISO 串
# （与库里 yyyy-MM-dd HH:mm:ss 的本地串不能按字典序比）、DATE_FORMAT 把格式串当字面量
# （上面已硬拦）。因此留空窗口只能由服务端补，且必须回显在 default_window 里。

TIME_BODY = ('SELECT COUNT(*) AS c FROM "logs-*2026.11" WHERE isRelease = 1 '
             "AND (${start_time} = '' OR \"warningTime.keyword\" >= ${start_time}) "
             "AND (${end_time} = '' OR \"warningTime.keyword\" <= ${end_time}) "
             "AND (${site} = '' OR \"site.keyword\" = ${site}) LIMIT 10")
TIME_VARS = json.dumps({"start_time": {"type": "string", "default": ""},
                        "end_time": {"type": "string", "default": ""},
                        "site": {"type": "string", "default": ""}})


class TestEsDefaultTimeWindow:
    IDENT = {"user_id": 7, "username": "u", "role": "analyst", "workspace_id": 1}

    def _run(self, monkeypatch, args, *, variables=TIME_VARS):
        from datetime import datetime

        fake = _FakeEs()
        row = _es_row(sql_query=TIME_BODY, variables=variables)
        monkeypatch.setattr(svc, "get_dataset", lambda _id: row)
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: None)
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        params = {"limit": 10}
        if args is not None:
            params["args"] = args
        out = svc.query_dataset(900, params, self.IDENT)
        return fake.calls[0][0], out, datetime

    def test_empty_args_are_replaced_by_rolling_window(self, monkeypatch):
        """留空不得等于"全量扫所有月度索引"（实测会 20s 读超时并把几年历史灌进缓存）。"""
        sql, out, datetime = self._run(monkeypatch, {"start_time": "", "end_time": "", "site": ""})
        win = out["default_window"]
        assert win["days"] == 30 and win["reason"]
        # 时间变量的“空串短路”必须消失（换成具体时间后该分句恒假，比较走真实下界）；
        # site 不是时间参数，不得被顺手注入（那会把“不限站点”隐性改成某个站点）。
        assert "'' = '' OR \"warningTime.keyword\"" not in sql
        assert "'' = '' OR \"site.keyword\"" in sql
        assert win["start_time"] in sql and win["end_time"] in sql
        start = datetime.strptime(win["start_time"], "%Y-%m-%d %H:%M:%S")
        assert 29 <= (datetime.now() - start).days <= 30, "默认窗必须是本地时间的近 30 天"

    def test_explicit_values_are_never_overridden(self, monkeypatch):
        """调用方给了值就用它的，不擅自改窗口（口径由人定，不由服务端猜）。"""
        sql, out, _ = self._run(monkeypatch, {"start_time": "2026-01-01 00:00:00",
                                              "end_time": "2026-01-31 23:59:59", "site": ""})
        assert "default_window" not in out
        assert "2026-01-01 00:00:00" in sql and "2026-01-31 23:59:59" in sql

    def test_body_without_time_params_stays_untouched(self, monkeypatch):
        """正文没引用时间参数时不得注入隐形窗口（否则等于给无关数据集改了口径）。"""
        fake = _FakeEs()
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _es_row())
        monkeypatch.setattr(svc, "require_es_channel", lambda: {"ready": True})
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: None)
        monkeypatch.setattr(es_client, "run_sql", fake.run_sql)
        out = svc.query_dataset(900, {"limit": 10}, self.IDENT)
        assert "default_window" not in out
        assert fake.calls[0][0] == BODY.strip().rstrip(";")

    def test_days_variable_drives_window(self, monkeypatch):
        """数据集可用 days 变量声明自己的默认窗口（与 MySQL 侧 COALESCE(..., NOW()) 对齐）。"""
        from datetime import datetime

        vars_ = json.loads(TIME_VARS)
        vars_["days"] = {"type": "number", "default": 7}
        _, out, _ = self._run(monkeypatch, {}, variables=json.dumps(vars_))
        assert out["default_window"]["days"] == 7
        start = datetime.strptime(
            out["default_window"]["start_time"], "%Y-%m-%d %H:%M:%S")
        assert 6 <= (datetime.now() - start).days <= 7
