"""数据集标签（tags）与「查看语义取数 SQL」的离线回归（不触真库/真执行）。

锁定口径（2026-11-24）：
  - 标签是**自由文本分类**（业务域/物化目的等），只进检索筛选，**不参与**取数/权限裁决；
    写路径越界（超长/超量/非字符串）显式拒绝，不静默截断后入库；
  - 筛选**下推 SQL**（`JSON_CONTAINS`，多标签 AND），不在前端拉全量再 filter；
  - 标签选项接口与清单**同一套可见性谓词**（`_list_sql`），看不到的数据集不贡献选项；
  - 「查看语义 SQL」只编译不执行：回 base_sql/secured_sql（按当前身份 RLS 改写）与
    闸门拒绝原因，且仅限 `dataset:manage` 的数据集编辑者（护栏 §7 例外），
    不回 provenance/物理表列/数据源内部 id。
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ds_svc = importlib.import_module("backend.modules.viz.services.dataset_service")
ds_api = importlib.import_module("backend.modules.viz.api.datasets")

ADMIN = {"user_id": 1, "username": "admin", "role": "admin", "workspace_id": 1}
OWNER = {"user_id": 7, "username": "analyst01", "role": "analyst", "workspace_id": 1}


# ── 标签归一（容错读路径） ───────────────────────────────────────────────────

class TestNormalizeTags:
    def test_json_string_from_db_is_parsed(self):
        """pymysql 回读 JSON 列给的是字符串：前端不得自己 JSON.parse。"""
        assert ds_svc.normalize_tags('["售后域", "大屏物化"]') == ["售后域", "大屏物化"]

    def test_list_dedupes_and_strips(self):
        assert ds_svc.normalize_tags([" 域名 ", "域名", "", "物化"]) == ["域名", "物化"]

    def test_empty_values(self):
        for v in (None, "", [], "  ", "[]"):
            assert ds_svc.normalize_tags(v) == []

    def test_single_string_is_one_tag(self):
        assert ds_svc.normalize_tags("售后域") == ["售后域"]

    def test_non_string_items_dropped_on_read_path(self):
        """读路径容错：历史脏数据不能让整页清单炸掉（写路径另有硬校验）。"""
        assert ds_svc.normalize_tags(["a", 1, None, {"x": 1}, "b"]) == ["a", "b"]

    def test_long_tag_clamped_and_count_capped(self):
        out = ds_svc.normalize_tags(["x" * (ds_svc.MAX_TAG_LENGTH + 10)])
        assert len(out[0]) == ds_svc.MAX_TAG_LENGTH
        many = [f"t{i}" for i in range(ds_svc.MAX_DATASET_TAGS + 5)]
        assert len(ds_svc.normalize_tags(many)) == ds_svc.MAX_DATASET_TAGS


# ── 写路径校验（fail-loud） ─────────────────────────────────────────────────

class TestValidateTags:
    def test_accepts_list_and_comma_string(self):
        assert ds_svc.validate_tags(["售后域", " 大屏物化 "]) == ["售后域", "大屏物化"]
        assert ds_svc.validate_tags("售后域,大屏物化") == ["售后域", "大屏物化"]

    def test_accepts_json_array_string(self):
        """前端可能原样回传 JSON 文本：解析成数组，而不是当成一个超长标签。"""
        assert ds_svc.validate_tags('["a","b"]') == ["a", "b"]

    def test_none_means_empty(self):
        assert ds_svc.validate_tags(None) == []

    def test_too_many_tags_rejected(self):
        many = [f"t{i}" for i in range(ds_svc.MAX_DATASET_TAGS + 1)]
        with pytest.raises(ValueError) as exc:
            ds_svc.validate_tags(many)
        assert str(ds_svc.MAX_DATASET_TAGS) in str(exc.value)

    def test_too_long_tag_rejected_not_truncated(self):
        with pytest.raises(ValueError):
            ds_svc.validate_tags(["x" * (ds_svc.MAX_TAG_LENGTH + 1)])

    def test_non_string_rejected(self):
        for bad in (123, ["ok", 1], {"a": 1}):
            with pytest.raises(ValueError):
                ds_svc.validate_tags(bad)


class TestParseTagFilter:
    def test_comma_string(self):
        assert ds_svc.parse_tag_filter("售后域, 大屏物化") == ["售后域", "大屏物化"]

    def test_list_and_empty(self):
        assert ds_svc.parse_tag_filter(["a", "a", ""]) == ["a"]
        assert ds_svc.parse_tag_filter(None) == []
        assert ds_svc.parse_tag_filter("") == []


# ── 筛选下推 SQL（list-pagination §2） ──────────────────────────────────────

class TestTagFilterPushedDown:
    def test_each_tag_becomes_json_contains(self):
        select_sql, where, params, *_ = ds_svc._list_sql(
            ADMIN, tags=["售后域", "大屏物化"])
        assert where.count("JSON_CONTAINS(d.tags, %s)") == 2
        assert params == ['"售后域"', '"大屏物化"']      # 参数是 JSON 字面量，不是拼接
        assert select_sql.startswith("SELECT ") and "d.tags" in select_sql

    def test_no_tag_means_no_predicate(self):
        _select, where, params, *_ = ds_svc._list_sql(ADMIN, tags="")
        assert "JSON_CONTAINS" not in where
        assert params == []

    def test_filter_combines_with_visibility_and_keyword(self):
        _select, where, params, *_ = ds_svc._list_sql(
            ADMIN, keyword="订单", visibility="workspace", tags="域")
        assert "d.name LIKE %s" in where and "d.visibility = %s" in where
        assert "JSON_CONTAINS(d.tags, %s)" in where
        assert params[-1] == '"域"'
        assert where.index("d.visibility = %s") < where.index("JSON_CONTAINS")


# ── 标签选项：与清单同一可见性谓词，Python 侧聚合计数 ──────────────────────

class TestListDatasetTags:
    def test_counts_and_reuses_list_where(self, monkeypatch):
        captured = {}

        def fake_query(sql, params=None, **kw):
            captured["sql"] = sql
            captured["params"] = params
            return [{"tags": '["售后域","大屏物化"]'}, {"tags": '["售后域"]'}, {"tags": None}]

        monkeypatch.setattr(ds_svc, "execute_query", fake_query)
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        out = ds_svc.list_dataset_tags(OWNER)
        assert out == [{"name": "售后域", "count": 2}, {"name": "大屏物化", "count": 1}]
        assert "d.status='active'" in captured["sql"]        # 谓词来自 _list_sql
        assert "JSON_CONTAINS" not in captured["sql"]        # 选项清单本身不带标签筛选

    def test_hidden_rows_do_not_leak_options(self, monkeypatch):
        """非属主且未授权：WHERE 里必须带可见性条件（否则标签会泄露别人的分类）。"""
        captured = {}

        def fake_query(sql, params=None, **kw):
            captured["sql"] = sql
            captured["params"] = params
            return []

        monkeypatch.setattr(ds_svc, "execute_query", fake_query)
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        monkeypatch.setattr(
            "backend.core.role_service.role_service.get_user_allowed_datasets",
            lambda uid: [])
        ds_svc.list_dataset_tags(OWNER)
        assert "d.owner_id = %s" in captured["sql"]
        assert "AND 1=0" in captured["sql"]                  # 无角色授权 = 只看到自己的


# ── 来源筛（枚举白名单，与清单/计数同源） ───────────────────────────────────

class TestOriginFilter:
    def test_origin_pushed_down(self):
        _select, where, params, *_ = ds_svc._list_sql(ADMIN, origin="ai_session")
        assert "d.origin = %s" in where
        assert params == ["ai_session"]              # 枚举白名单值直接当参数，不拼 SQL

    def test_blank_or_all_means_no_predicate(self):
        for v in ("", "all", None):
            _select, where, params, *_ = ds_svc._list_sql(ADMIN, origin=v)
            assert "d.origin = %s" not in where
            assert params == []

    def test_unknown_origin_rejected_with_candidates(self):
        """未知来源显式拒（不静默当“不限”），文案带可选枚举与业务名。"""
        with pytest.raises(ValueError) as exc:
            ds_svc._list_sql(ADMIN, origin="robot")
        assert "robot" in str(exc.value)
        assert "ai_session" in str(exc.value) and "AI 会话产物" in str(exc.value)

    def test_page_total_and_rows_share_origin_predicate(self, monkeypatch):
        """`total` 的 COUNT 与当前页 SELECT 必须同一份 WHERE（含来源谓词）。"""
        seen = []

        def fake_query(sql, params=None, fetchone=False, **kw):
            seen.append(sql)
            return {"n": 0} if fetchone else []

        monkeypatch.setattr(ds_svc, "execute_query", fake_query)
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        out = ds_svc.list_datasets_page(ADMIN, page=1, page_size=10, origin="chart_backfill")
        assert out["total"] == 0
        assert len(seen) == 2
        assert "COUNT(*)" in seen[0] and "d.origin = %s" in seen[0]
        assert "d.origin = %s" in seen[1] and "LIMIT %s OFFSET %s" in seen[1]

    def test_origin_options_from_server_enum(self, monkeypatch):
        """选项由服务端枚举下发（固定顺序 + 业务名），前端不硬编码。"""
        def fake_query(sql, params=None, **kw):
            return [{"origin": "manual"}, {"origin": "ai_session"},
                    {"origin": "manual"}, {"origin": None}]

        monkeypatch.setattr(ds_svc, "execute_query", fake_query)
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        out = ds_svc.list_dataset_origins(ADMIN)
        assert [o["value"] for o in out] == ["manual", "chart_backfill", "ai_session"]
        assert out[0] == {"value": "manual", "label": "人工定义", "count": 3}
        assert out[1]["count"] == 0                     # 枚举保留，0 命中也是真实结果
        assert out[2] == {"value": "ai_session", "label": "AI 会话产物", "count": 1}


# ── 取数方式筛（SQL / 语义对象 / 两种并存，枚举白名单与清单同源） ────────

class TestSourceTypeFilter:
    def test_source_type_pushed_down(self):
        _select, where, params, *_ = ds_svc._list_sql(ADMIN, source_type="semantic")
        assert "d.source_type = %s" in where
        assert params == ["semantic"]                 # 白名单值直接当参数，不拼 SQL

    def test_blank_or_all_means_no_predicate(self):
        for v in ("", "all", None):
            _select, where, params, *_ = ds_svc._list_sql(ADMIN, source_type=v)
            assert "d.source_type = %s" not in where
            assert params == []

    def test_unknown_source_type_rejected_with_candidates(self):
        with pytest.raises(ValueError) as exc:
            ds_svc._list_sql(ADMIN, source_type="es")
        assert "es" in str(exc.value)
        assert "semantic" in str(exc.value) and "语义对象" in str(exc.value)

    def test_page_total_and_rows_share_source_type_predicate(self, monkeypatch):
        seen = []

        def fake_query(sql, params=None, fetchone=False, **kw):
            seen.append(sql)
            return {"n": 0} if fetchone else []

        monkeypatch.setattr(ds_svc, "execute_query", fake_query)
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        out = ds_svc.list_datasets_page(ADMIN, page=1, page_size=10, source_type="both")
        assert out["total"] == 0
        assert "d.source_type = %s" in seen[0] and "COUNT(*)" in seen[0]
        assert "d.source_type = %s" in seen[1] and "LIMIT %s OFFSET %s" in seen[1]

    def test_source_type_options_from_server_enum(self, monkeypatch):
        """选项由服务端枚举下发（三档全保留，历史 es 值归一为 sql）。"""
        def fake_query(sql, params=None, **kw):
            return [{"source_type": "sql"}, {"source_type": "semantic"},
                    {"source_type": "es"}, {"source_type": None}]

        monkeypatch.setattr(ds_svc, "execute_query", fake_query)
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        out = ds_svc.list_dataset_source_types(ADMIN)
        assert [o["value"] for o in out] == ["sql", "semantic", "both"]
        assert out[0] == {"value": "sql", "label": "SQL", "count": 3}
        assert out[1]["count"] == 1
        assert out[2]["count"] == 0                    # 枚举保留，0 命中也是真实结果


# ── 新建/更新落库 tags ─────────────────────────────────────────────────────

class TestPersistTags:
    def test_update_writes_json_array(self, monkeypatch):
        writes = []
        reads = {"n": 0}

        def fake_get(dataset_id):
            reads["n"] += 1
            # 第 1 次 = 存量行；第 2 次 = 保存后回读（tags 已更新）
            tags = ["旧标签"] if reads["n"] == 1 else ["售后域", "大屏物化"]
            return {"id": dataset_id, "name": "订单宽表", "object_key": "",
                    "sql_query": "SELECT 1 AS a LIMIT 10", "datasource_id": 5,
                    "description": "", "tags": tags, "owner_id": 7,
                    "status": "active", "visibility": "private"}

        monkeypatch.setattr(ds_svc, "get_dataset", fake_get)
        monkeypatch.setattr(ds_svc, "execute_write",
                            lambda sql, params=None: writes.append((sql, params)))
        monkeypatch.setattr(ds_svc, "_dialect_coverage_warnings", lambda *a, **k: [])
        monkeypatch.setattr(ds_svc, "_load_datasource_catalog", lambda: {})
        saved = ds_svc.update_dataset(
            33, {"tags": ["售后域", " 大屏物化 "]}, OWNER)
        sql, params = writes[0]
        assert "tags = %s" in sql
        assert '["售后域", "大屏物化"]' in params
        assert saved["tags"] == ["售后域", "大屏物化"]

    def test_update_rejects_bad_tags_before_writing(self, monkeypatch):
        writes = []
        monkeypatch.setattr(ds_svc, "get_dataset", lambda dataset_id: {
            "id": dataset_id, "name": "x", "object_key": "", "sql_query": "SELECT 1 LIMIT 1",
            "datasource_id": 5, "owner_id": 7, "status": "active", "visibility": "private"})
        monkeypatch.setattr(ds_svc, "execute_write",
                            lambda sql, params=None: writes.append(sql))
        with pytest.raises(ValueError):
            ds_svc.update_dataset(33, {"tags": ["x" * 999]}, OWNER)
        assert writes == []                                  # 校验不过不留半截写入


# ── 查看语义取数对应的 SQL（只编译不执行） ──────────────────────────────────

SEMANTIC_DS = {
    "id": 44, "name": "订单语义集", "object_key": "order", "sql_query": "",
    "datasource_id": 5, "status": "active", "visibility": "private",
    "owner_id": 7, "source_type": "semantic",
}


SEMANTIC_BASE_SQL = "SELECT store, SUM(amount) AS amt FROM order_t GROUP BY store LIMIT 200"


def _stub_semantic_pipeline(monkeypatch, *, allowed=True, blocked_at="", reason=""):
    """把语义编译链换成固定产物：断言点在“回什么”，不在 planner 内部。"""
    from backend.semantics.gates import SemanticExecution

    class Planned:
        sql = SEMANTIC_BASE_SQL
        secured_sql = SEMANTIC_BASE_SQL
        dialect = "mysql"
        route = "single"
        warnings = ["样例告警"]
        provenance = {"physical_table": "order_t", "unresolved_terms": []}
        resolved_tables = ["order_t"]
        output_header_map = {}

    class Q:
        object = "order"
        dimensions = ["门店"]
        metrics = ["销售额"]
        filters = []
        order = []
        limit = 200
        datasource_id = 5
        user_id = 7
        workspace_id = 1
        dry_run = False

    monkeypatch.setattr("backend.semantics.intent.parse_intent",
                        lambda payload: (Q(), "", []))
    monkeypatch.setattr("backend.semantics.binding_resolver.resolve_binding",
                        lambda obj, datasource_id=0: (object(), []))
    monkeypatch.setattr("backend.semantics.planner.plan",
                        lambda q, b: Planned())

    def fake_gates(q, b, p, ctx):
        return SemanticExecution(
            allowed=allowed, blocked_at=blocked_at, reason=reason,
            base_sql=p.sql,
            secured_sql=(SEMANTIC_BASE_SQL.replace("GROUP BY", "WHERE store <> 'x' GROUP BY")
                         if allowed else ""),
            applied_rls=["rl_store"] if allowed else [],
            masked_columns=[],
            warnings=list(p.warnings),
        )

    monkeypatch.setattr("backend.semantics.gates.run_gates", fake_gates)


def _stub_manage(monkeypatch, *, allowed=True):
    monkeypatch.setattr(
        "backend.common.api_permission.check_api_permission",
        lambda role, method, path: allowed)


class TestExplainSemanticSql:
    def _setup(self, monkeypatch, **kw):
        monkeypatch.setattr(ds_svc, "get_dataset", lambda dataset_id: dict(SEMANTIC_DS))
        monkeypatch.setattr(ds_svc, "resolve_effective_source",
                            lambda ds, ident, **k: 5)
        monkeypatch.setattr(ds_svc, "_scope_filters", lambda dataset_id, ident: [])
        monkeypatch.setattr(ds_svc, "effective_source_name", lambda sid: "prd-cn")
        _stub_manage(monkeypatch, allowed=kw.get("manage", True))
        _stub_semantic_pipeline(
            monkeypatch, allowed=kw.get("allowed", True),
            blocked_at=kw.get("blocked_at", ""), reason=kw.get("reason", ""))

    def test_returns_base_and_secured_sql(self, monkeypatch):
        self._setup(monkeypatch)
        out = ds_svc.explain_dataset_sql(44, {"dimensions": ["门店"], "measures": ["销售额"]}, OWNER)
        assert out["execution_path"] == "semantic"
        assert out["sql"].startswith("SELECT store")
        assert "store <> 'x'" in out["secured_sql"]          # 按当前身份的 RLS 改写
        assert out["allowed"] is True and out["blocked_at"] == ""
        assert out["applied_rls"] == ["rl_store"]
        assert out["dataset"] == {"id": 44, "name": "订单语义集", "source_type": "semantic"}
        # 护栏 §7：只回生效源业务名，不回内部 id / 物理表 / provenance
        assert out["datasource"] == "prd-cn"
        assert "datasource_id" not in out
        # provenance 携带物理表/列与内部路由，不进响应（SQL 本身是 §7 例外内的显式回显）
        assert "provenance" not in out

    def test_blocked_gate_still_reports_where_and_why(self, monkeypatch):
        """闸门拒绝也要能看“拒在哪一步”：不得静默回空 SQL 让人以为没配置。"""
        self._setup(monkeypatch, allowed=False, blocked_at="permission",
                    reason="字段 '手机号' 为敏感合规屏蔽列, 不允许查询")
        out = ds_svc.explain_dataset_sql(44, {}, OWNER)
        assert out["allowed"] is False
        assert out["blocked_at"] == "permission"
        assert "敏感合规屏蔽列" in out["reason"]
        assert out["sql"]                                   # base SQL 仍可见（诊断用）
        assert out["secured_sql"] == ""

    def test_requires_dataset_manage(self, monkeypatch):
        self._setup(monkeypatch, manage=False)
        with pytest.raises(PermissionError):
            ds_svc.explain_dataset_sql(44, {}, OWNER)

    def test_rejects_datasource_id_param(self, monkeypatch):
        self._setup(monkeypatch)
        with pytest.raises(ValueError) as exc:
            ds_svc.explain_dataset_sql(44, {"datasource_id": 5}, OWNER)
        assert "内部 id" in str(exc.value)

    def test_requires_editor(self, monkeypatch):
        """非属主/非管理员即便有 dataset:manage 也不得看他人数据集的口径。"""
        self._setup(monkeypatch)
        monkeypatch.setattr(ds_svc, "get_dataset", lambda dataset_id: {
            **SEMANTIC_DS, "owner_id": 999})
        with pytest.raises(PermissionError):
            ds_svc.explain_dataset_sql(44, {}, OWNER)

    def test_route_registered_before_dataset_id(self):
        """`/tags`・`/origins`・`/source-types` 必须先于 `/{dataset_id}` 注册，
        否则会被当成 dataset_id 解析（422）。"""
        paths = [r.path for r in ds_api.router.routes]
        assert paths.index("/tags") < paths.index("/{dataset_id}")
        assert paths.index("/origins") < paths.index("/{dataset_id}")
        assert paths.index("/source-types") < paths.index("/{dataset_id}")
        assert "/{dataset_id}/explain-sql" in paths