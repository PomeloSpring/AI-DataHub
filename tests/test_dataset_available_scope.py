"""数据集「可用数据源范围」回归测试（一份定义 × N 个源，同构本体 deploy_sites）。

覆盖:
1. 范围解析: 执行源隐式属于范围、悬空源名显式暴露、默认引擎直通;
2. 生效源解析六步顺序（显式 datasource → 显式 site → 会话/看板偏好 → 执行源 →
   交集唯一 → 交集多义回抛候选 → 交集为空拒），错误文案只出业务名（护栏 §7）;
3. 只有 B 源权限的用户可以看见并在 B 上跑「执行源为 A」的数据集（用户核心诉求）;
4. 清单可见性 = 角色数据集授权 ∧ (范围 ∩ 授权源 ≠ ∅)，筛选按范围命中;
5. 保存侧: 未知名拒绝入库、跨引擎正文缺口显式告警、字段目录按生效源取;
6. 看板挂图/报表快照/语义层 planner/本体 activate 的一致性门禁;
7. Chat 工具面: 只接业务名、剥离 datasource_id、定源规范入提示词。

全部内存 monkeypatch，不依赖元库数据。
"""
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.core.governed_query import NoIdentityError          # noqa: E402
from backend.core.role_service import role_service               # noqa: E402
from backend.modules.viz.services.dashboard_service import (      # noqa: E402
    assert_dataset_chart_usable,)
from backend.modules.viz.services import dataset_service as svc  # noqa: E402
from backend.modules.viz.services import report_access           # noqa: E402

# ── 夹具 ────────────────────────────────────────────────────────────────

CATALOG = {
    "prd-cn":   {"id": 11, "name": "prd-cn",   "db_type": "mysql", "site_code": "cn"},
    "prd-jp":   {"id": 12, "name": "prd-jp",   "db_type": "mysql", "site_code": "jp"},
    "prd-sls":  {"id": 13, "name": "prd-sls",  "db_type": "sls",   "site_code": "cn"},
}
ALL_IDS = {11, 12, 13}


def _ds(**over):
    """默认引擎上的纯 SQL 数据集（最小形状，按需覆写）。"""
    row = {
        "id": 501, "name": "订单概览", "status": "active",
        "source_type": "sql", "object_key": "",
        "datasource_id": 11, "available_datasources": [],
        "sql_query": "SELECT region, COUNT(*) AS cnt FROM t_order GROUP BY region",
        "sql_bodies": None, "variables": "{}", "field_config": "[]",
        "chart_type": "", "chart_preset": "{}", "owner_id": 1,
    }
    row.update(over)
    return row


def _identity(role="analyst", user_id=1001):
    return {"user_id": user_id, "username": "u", "role": role, "workspace_id": 301}


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(svc, "_load_datasource_catalog", lambda: dict(CATALOG))
    return CATALOG


@pytest.fixture
def granted(monkeypatch):
    """角色数据源授权集（唯一裁决点）；测试里改写 holder 即可换权限。"""
    holder = {"ids": [11, 12, 13]}
    monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                        lambda uid, ws=0: list(holder["ids"]))
    return holder


class _Cursor:
    def __init__(self, router):
        self._router = router
        self._hit = []

    def execute(self, sql, params=None):
        self._hit = list(self._router(sql, params) or [])

    def fetchall(self):
        return list(self._hit)

    def fetchone(self):
        return self._hit[0] if self._hit else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    """内存元库连接：SQL → 行集 由 router 决定（cursor/with 两种用法都支持）。"""

    def __init__(self, router):
        self._router = router

    def cursor(self):
        return _Cursor(self._router)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


# ── 1. 范围解析 ─────────────────────────────────────────────────────────

class TestUsableSources:
    def test_exec_source_implicitly_in_scope(self, catalog):
        usable, missing = svc.usable_sources(_ds(datasource_id=11, available_datasources=[]))
        assert [u["id"] for u in usable] == [11]
        assert missing == []

    def test_declared_scope_appended_deduped(self, catalog):
        usable, _ = svc.usable_sources(
            _ds(datasource_id=11, available_datasources=["prd-jp", "prd-cn", " prd-jp "]))
        assert [u["id"] for u in usable] == [11, 12]

    def test_dangling_name_reported_not_dropped(self, catalog):
        """已删/改名的源必须显式暴露（no-silent-degradation），不得当作没配过。"""
        usable, missing = svc.usable_sources(
            _ds(datasource_id=11, available_datasources=["ghost-source"]))
        assert [u["id"] for u in usable] == [11]
        assert missing == ["ghost-source"]

    def test_deleted_exec_source_is_dangling_too(self, catalog):
        usable, missing = svc.usable_sources(_ds(datasource_id=99, available_datasources=[]))
        assert usable == []
        assert any("99" in m for m in missing), missing

    def test_default_engine_always_in_scope(self, catalog):
        """datasource_id=0（元库）不参与源级 RBAC：存量行为零变化。"""
        usable, _ = svc.usable_sources(_ds(datasource_id=0, available_datasources=[]))
        assert [u["id"] for u in usable] == [0]

    def test_scope_label_uses_business_names_only(self, catalog):
        ds = _ds(datasource_id=11, available_datasources=["prd-jp"])
        scope, _ = svc.usable_scope(ds, _identity(), allowed_ds=ALL_IDS)
        label = svc._scope_label(scope)
        assert label == "prd-cn、prd-jp"
        assert "11" not in label and "12" not in label


# ── 2. 生效源解析六步（顺序不可调） ─────────────────────────────────────

class TestResolveEffectiveSource:
    DS = None

    def setup_method(self):
        self.DS = _ds(datasource_id=11, available_datasources=["prd-jp", "prd-sls"])

    # 1. 显式 datasource
    def test_explicit_datasource_wins(self, catalog, granted):
        assert svc.resolve_effective_source(self.DS, _identity(), datasource="prd-jp") == 12

    def test_explicit_datasource_outside_authorized_is_permission_error(self, catalog, granted):
        """在可用范围内但角色无授权 = 越权（不换源重试、不静默降级）。"""
        granted["ids"] = [11]
        with pytest.raises(PermissionError) as e:
            svc.resolve_effective_source(self.DS, _identity(), datasource="prd-jp")
        assert "prd-cn" in str(e.value)          # 候选只给业务名
        assert "12" not in str(e.value)

    def test_explicit_datasource_outside_scope_lists_candidates(self, catalog, granted):
        """源根本不在范围内 → 可操作错误 + 候选清单，引导 ask_user 而不是猜。"""
        with pytest.raises(ValueError) as e:
            svc.resolve_effective_source(_ds(datasource_id=11), _identity(),
                                         datasource="prd-sls")
        msg = str(e.value)
        assert "list_datasources" in msg and "prd-cn" in msg
        assert "11" not in msg

    def test_dangling_scope_name_shown_in_error(self, catalog, granted):
        ds = _ds(datasource_id=11, available_datasources=["ghost-source"])
        with pytest.raises(ValueError) as e:
            svc.resolve_effective_source(ds, _identity(), datasource="prd-jp")
        assert "ghost-source" in str(e.value)

    # 1b. 显式 site
    def test_site_resolves_inside_authorized_scope(self, catalog, granted, monkeypatch):
        seen = {}

        def fake_resolve(site, allowed_ids, model_id=0):
            seen["site"] = site
            seen["allowed_ids"] = list(allowed_ids)
            seen["model_id"] = model_id
            return 12

        monkeypatch.setattr("backend.core.site_service.resolve_site_to_source", fake_resolve)
        out = svc.resolve_effective_source(self.DS, _identity(), site="日本", model_id=77)
        assert out == 12
        assert seen["site"] == "日本" and seen["model_id"] == 77
        # 只在"具体可用且已授权"的源集内解析（不得传空清单绕过授权过滤）
        assert set(seen["allowed_ids"]) == {11, 12, 13}

    def test_site_on_default_engine_only_is_refused(self, catalog, granted):
        ds = _ds(datasource_id=0, available_datasources=[])
        with pytest.raises(ValueError) as e:
            svc.resolve_effective_source(ds, _identity(), site="中国")
        assert "默认引擎" in str(e.value)

    def test_site_error_propagates_verbatim(self, catalog, granted, monkeypatch):
        from backend.core.site_service import SiteError

        def boom(site, allowed_ids, model_id=0):
            raise SiteError("站点「美国」下无你有权访问的数据源")

        monkeypatch.setattr("backend.core.site_service.resolve_site_to_source", boom)
        with pytest.raises(ValueError) as e:
            svc.resolve_effective_source(self.DS, _identity(), site="美国")
        assert "站点「美国」" in str(e.value)

    # 2. 会话/看板偏好源
    def test_preferred_source_beats_exec_source(self, catalog, granted):
        out = svc.resolve_effective_source(self.DS, _identity(), preferred_id=12)
        assert out == 12

    def test_preferred_outside_scope_falls_through_to_exec(self, catalog, granted):
        """偏好源不在范围内（如会话切到别的源）→ 用执行源，不报错也不换别的源。"""
        ds = _ds(datasource_id=11, available_datasources=["prd-jp"])
        assert svc.resolve_effective_source(ds, _identity(), preferred_id=999) == 11

    # 3/4. 执行源优先 / 交集唯一自动选定
    def test_exec_source_used_when_authorized(self, catalog, granted):
        ds = _ds(datasource_id=11, available_datasources=["prd-jp"])
        assert svc.resolve_effective_source(ds, _identity()) == 11

    def test_user_with_only_b_runs_on_b(self, catalog, granted):
        """核心诉求：执行源 A、范围含 B，用户只有 B 权限 → 自动定源 B（不是报错）。"""
        granted["ids"] = [12]
        ds = _ds(datasource_id=11, available_datasources=["prd-jp"])
        assert svc.resolve_effective_source(ds, _identity()) == 12

    # 5. 交集多义
    def test_ambiguous_intersection_fails_loud_with_candidates(self, catalog, granted):
        """执行源无权限 + 交集多个 → 必须回抛候选，禁止猜源。"""
        granted["ids"] = [12, 13]
        with pytest.raises(ValueError) as e:
            svc.resolve_effective_source(self.DS, _identity())
        msg = str(e.value)
        assert "prd-jp" in msg and "prd-sls" in msg and "site" in msg
        assert "12" not in msg and "13" not in msg

    # 6. 交集为空
    def test_empty_intersection_is_permission_error(self, catalog, granted):
        granted["ids"] = [999]
        with pytest.raises(PermissionError) as e:
            svc.resolve_effective_source(_ds(datasource_id=11), _identity())
        assert "任何可用数据源" in str(e.value)

    def test_default_engine_passthrough(self, catalog, granted):
        granted["ids"] = []                  # 空授权不得解释为全量，但默认引擎直通
        assert svc.resolve_effective_source(_ds(datasource_id=0), _identity()) == 0

    def test_effective_source_name_never_returns_bare_id(self, catalog):
        assert svc.effective_source_name(0) == "默认引擎"
        assert svc.effective_source_name(12) == "prd-jp"
        assert svc.effective_source_name(99999) == "默认引擎"   # 查不到也不裸显 id


# ── 3. 取数入口接线 ─────────────────────────────────────────────────────

class TestQueryDatasetWiring:
    def _stub(self, monkeypatch, row, sink):
        monkeypatch.setattr(svc, "get_dataset", lambda _id: dict(row))
        monkeypatch.setattr(svc, "_scope_filters", lambda *a: [])

        def fake_query_sql(ds, params, scopes, identity):
            sink.append(ds)
            return {"columns": ["region"], "rows": [{"region": "cn"}],
                    "datasource_id": ds.get("datasource_id")}

        monkeypatch.setattr(svc, "_query_sql", fake_query_sql)

    def test_internal_datasource_id_rejected(self, catalog, granted, monkeypatch):
        """§7 黑盒：请求体不接受内部 id（前端/工具都只能给业务名）。"""
        sink = []
        self._stub(monkeypatch, _ds(), sink)
        with pytest.raises(ValueError) as e:
            svc.query_dataset(501, {"datasource_id": 12}, _identity())
        assert "不接受数据源内部 id" in str(e.value)
        assert sink == []                    # 解析前即拒，未下发执行

    def test_effective_source_injected_downstream(self, catalog, granted, monkeypatch):
        sink = []
        self._stub(monkeypatch, _ds(datasource_id=11, available_datasources=["prd-jp"]), sink)
        out = svc.query_dataset(501, {"datasource": "prd-jp"}, _identity())
        assert sink[0]["datasource_id"] == 12                  # 方言/编译/执行按生效源
        assert out["datasource"] == "prd-jp"
        assert "datasource_id" not in out                      # 对外不回 id

    def test_site_and_preferred_passed_through(self, catalog, granted, monkeypatch):
        sink = []
        self._stub(monkeypatch, _ds(datasource_id=11, available_datasources=["prd-jp"]), sink)
        out = svc.query_dataset(501, {"site": "", "preferred_datasource_id": 12}, _identity())
        assert sink[0]["datasource_id"] == 12 and out["datasource"] == "prd-jp"


# ── 4. 清单可见性（交集） ───────────────────────────────────────────────

class TestListVisibility:
    def _stub(self, monkeypatch, rows, *, hits=None):
        def fake(sql, params=None, fetchone=False, **kw):
            if hits is not None:
                hits.append(sql)
            if "adh_datasets" in sql:               # 主查询（被引计数已折进 LEFT JOIN）
                hit = [dict(r, references=0) for r in rows]
                return (hit[0] if hit else None) if fetchone else hit
            if "adh_charts" in sql:                 # 其他单图引用查询
                return {"n": 0} if fetchone else [{"n": 0}]
            return None if fetchone else []

        monkeypatch.setattr(svc, "execute_query", fake)
        return hits

    def test_only_b_user_sees_and_can_run_ab_dataset(self, catalog, granted, monkeypatch):
        granted["ids"] = [12]
        self._stub(monkeypatch, [_ds(datasource_id=11, available_datasources=["prd-jp"])])
        items = svc.list_datasets(_identity(role="admin"))
        assert len(items) == 1
        it = items[0]
        assert it["datasource_name"] == "prd-cn"               # 执行源业务名
        assert it["available_source_names"] == ["prd-cn", "prd-jp"]
        assert it["usable_now"] == ["prd-jp"]                  # 真正能跑的才叫可用

    def test_no_intersection_dataset_hidden(self, catalog, granted, monkeypatch):
        """完全无交集 → 隐藏（看得见却一取数就报错 = 空承诺）。"""
        granted["ids"] = [13]
        self._stub(monkeypatch, [_ds(datasource_id=11, available_datasources=["prd-jp"])])
        assert svc.list_datasets(_identity(role="admin")) == []

    def test_filter_by_datasource_hits_scope(self, catalog, granted, monkeypatch):
        granted["ids"] = [11, 12]
        self._stub(monkeypatch, [
            _ds(id=1, datasource_id=11, available_datasources=["prd-jp"]),
            _ds(id=2, datasource_id=13, available_datasources=[]),
        ])
        hit = svc.list_datasets(_identity(role="admin"), datasource_id=12)
        assert [d["id"] for d in hit] == [1]

    def test_filter_by_missing_datasource_returns_empty(self, catalog, granted, monkeypatch):
        """筛的源已不存在 → 无命中，不得静默当"全部"。"""
        self._stub(monkeypatch, [_ds()])
        assert svc.list_datasets(_identity(role="admin"), datasource_id=999) == []

    def test_list_never_falls_back_to_bare_id(self, catalog, granted, monkeypatch):
        granted["ids"] = [11]
        self._stub(monkeypatch, [_ds(datasource_id=99)])       # 执行源已被删除
        assert svc.list_datasets(_identity(role="admin")) == []

    def test_list_roundtrips_do_not_grow_with_rows(self, catalog, granted, monkeypatch):
        """清单页往返次数不得随行数增长（元数据库在远端，N+1 会把页面拖到秒级以上）。"""
        one = [_ds(id=1, datasource_id=11)]
        many = [_ds(id=i, datasource_id=11) for i in range(1, 13)]

        hits_a, hits_b = [], []
        self._stub(monkeypatch, one, hits=hits_a)
        assert len(svc.list_datasets(_identity(role="admin"))) == 1
        self._stub(monkeypatch, many, hits=hits_b)
        assert len(svc.list_datasets(_identity(role="admin"))) == 12
        assert len(hits_a) == len(hits_b) == 1, f"1 行 {hits_a} vs 12 行 {hits_b}"


# ── 5. 保存侧校验与告警 ─────────────────────────────────────────────────

class TestSaveScopeValidation:
    def test_unknown_name_refused(self, catalog):
        with pytest.raises(ValueError) as e:
            svc._normalize_available_datasources(["prd-jp", "ghost"], CATALOG)
        assert "可用数据源不存在" in str(e.value) and "prd-cn" in str(e.value)

    def test_ids_refused(self, catalog):
        """范围存业务名，不接受 id（删建 id 会变，且 id 属黑盒）。"""
        with pytest.raises(ValueError):
            svc._normalize_available_datasources([12], CATALOG)

    def test_dedupe_strip_and_json(self, catalog):
        assert json.loads(svc._normalize_available_datasources(
            [" prd-jp ", "prd-jp", "prd-cn"], CATALOG)) == ["prd-jp", "prd-cn"]

    def test_emptyMeansOnlyExecSource(self, catalog):
        assert svc._normalize_available_datasources(None, CATALOG) == "[]"
        assert svc._normalize_available_datasources("", CATALOG) == "[]"
        assert svc._normalize_available_datasources('["prd-jp"]', CATALOG) == '["prd-jp"]'

    def test_cross_engine_gap_warns(self, catalog, monkeypatch):
        """跨引擎而无对应正文：保存即告警（运行期由编译器拒），不静默存下。"""
        monkeypatch.setattr(svc, "_dataset_db_type", lambda ds: "mysql")
        row = _ds(datasource_id=11, available_datasources=["prd-sls"], sql_bodies=None)
        warns = svc._dialect_coverage_warnings(row, CATALOG)
        assert any("prd-sls" in w and "postgres" in w for w in warns), warns

    def test_missing_body_for_engine_warns(self, catalog, monkeypatch):
        monkeypatch.setattr(svc, "_dataset_db_type", lambda ds: "mysql")
        row = _ds(datasource_id=11, available_datasources=["prd-jp"],
                  sql_query="", sql_bodies=json.dumps({"postgres": "SELECT 1"}))
        warns = svc._dialect_coverage_warnings(row, CATALOG)
        assert any("prd-jp" in w and "正文" in w for w in warns), warns

    def test_same_engine_no_warning(self, catalog, monkeypatch):
        monkeypatch.setattr(svc, "_dataset_db_type", lambda ds: "mysql")
        row = _ds(datasource_id=11, available_datasources=["prd-jp"])
        assert svc._dialect_coverage_warnings(row, CATALOG) == []

    def test_semantic_only_dataset_skips_dialect_check(self, catalog):
        row = _ds(sql_query="", sql_bodies=None, object_key="order", available_datasources=["prd-jp"])
        assert svc._dialect_coverage_warnings(row, CATALOG) == []


class TestResolveFieldsByEffectiveSource:
    def test_fields_follow_effective_source(self, catalog, monkeypatch):
        """跨源数据集切到生效源后，字典行必须按生效源取（否则能取数但字段目录空）。"""
        seen = {}

        def fake_semantic(ds_id, object_key, **kw):
            seen["ds_id"] = ds_id
            return [{"field": "region", "label": "区域", "role": "dimension"}]

        monkeypatch.setattr(svc, "_semantic_fields", fake_semantic)
        ds = _ds(datasource_id=11, object_key="order", sql_query="")
        svc.resolve_fields(ds, datasource_id=12)
        assert seen["ds_id"] == 12
        svc.resolve_fields(ds)
        assert seen["ds_id"] == 11

    def test_memo_fetches_dict_rows_once_per_source(self, catalog, monkeypatch):
        """传入请求级 memo 时，同一源的字典行只回源一次（清单页 N 个语义数据集不得 N×2 次）。"""
        calls = []

        def fake_metrics(ds_id):
            calls.append(("m", ds_id))
            return [{"name": "订单数", "bound_object_key": "order", "name_en": "", "unit": "",
                     "formula": "", "aliases": [], "description": ""}]

        def fake_dims(ds_id):
            calls.append(("d", ds_id))
            return [{"name": "区域", "bound_object_key": "order", "name_en": "", "category": "",
                     "aliases": [], "value_labels": {}, "description": ""}]

        import backend.semantics.mdl_compiler as mdl
        monkeypatch.setattr(mdl, "_get_metrics", fake_metrics)
        monkeypatch.setattr(mdl, "_get_dimensions", fake_dims)

        memo: dict = {}
        for i in range(4):
            ds = _ds(id=i, datasource_id=11, object_key="order", sql_query="", field_config="[]")
            assert len(svc.resolve_fields(ds, memo=memo)) == 2
        assert calls == [("m", 11), ("d", 11)], calls


# ── 6. 看板挂图入口把关 ─────────────────────────────────────────────────

class TestDashboardChartGate:
    def test_usable_scope_allows_binding(self, catalog, granted, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset",
                            lambda _id: _ds(datasource_id=11, available_datasources=["prd-jp"]))
        assert_dataset_chart_usable(
            {"source_type": "dataset", "source_id": 501}, _identity())

    def test_no_intersection_refuses_with_business_name(self, catalog, granted, monkeypatch):
        granted["ids"] = [13]
        monkeypatch.setattr(svc, "get_dataset",
                            lambda _id: _ds(datasource_id=11, available_datasources=["prd-jp"]))
        with pytest.raises(ValueError) as e:
            assert_dataset_chart_usable(
                {"source_type": "dataset", "source_id": 501}, _identity())
        assert "订单概览" in str(e.value) and "13" not in str(e.value)

    def test_patch_only_payload_reads_back_chart_row(self, catalog, granted, monkeypatch):
        """改图常只带局部字段 → 回读 adh_charts 拼有效口径，不得因缺字段而跳过校验。"""
        granted["ids"] = [13]
        monkeypatch.setattr(svc, "get_dataset",
                            lambda _id: _ds(datasource_id=11))
        monkeypatch.setattr("backend.common.db.execute_query",
                            lambda sql, params=None, **kw: {"source_type": "dataset",
                                                             "source_id": 501})
        with pytest.raises(ValueError):
            assert_dataset_chart_usable({"title": "改名"}, _identity(),
                                                           chart_id=88)

    def test_non_dataset_source_is_noop(self, catalog, granted, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda _id: pytest.fail("不应加载数据集"))
        assert_dataset_chart_usable(
            {"source_type": "raw_sql", "source_id": 501}, _identity())

    def test_missing_identity_fails_closed(self, catalog, granted, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _ds())
        with pytest.raises(NoIdentityError):
            assert_dataset_chart_usable(
                {"source_type": "dataset", "source_id": 501}, {"user_id": 0})


# ── 7. 报表快照：改范围必须失效 ─────────────────────────────────────────

class TestReportSnapshotDigest:
    def test_scope_change_invalidates_digest(self, monkeypatch):
        """可用范围是数据集配置的一部分：改了范围，报表授权快照不得继续复用。"""
        monkeypatch.setattr(svc, "_scope_filters", lambda *a: [])
        before = report_access.dataset_snapshot(
            501, _identity(), _ds(datasource_id=11, available_datasources=["prd-jp"]))
        after = report_access.dataset_snapshot(
            501, _identity(), _ds(datasource_id=11, available_datasources=[]))
        assert before != after


# ── 8. 语义层 planner 门禁（站点路由后的目标源必须被数据集声明） ────────

class TestPlannerScopeGate:
    def _binding(self, datasource_id, db_type="mysql"):
        from backend.semantics.models import Guardrail, ResolvedBinding

        return ResolvedBinding(object_key="order_wide", datasource_id=datasource_id,
                               physical_table="", db_name="stardb", db_type=db_type,
                               bind_kind="dataset", dataset_ref="订单概览",
                               guardrail=Guardrail(query_mode="raw_source", max_rows=5000))

    def _query(self):
        from backend.semantics.models import SemanticQuery

        return SemanticQuery(object="order_wide", metrics=["cnt"], dimensions=["region"])

    def _stub(self, monkeypatch, ds_row, rows):
        from backend.semantics import planner

        monkeypatch.setattr(planner, "_load_dataset_row", lambda ref: dict(ds_row))
        monkeypatch.setattr(planner, "_dataset_has_scopes", lambda _id: False)
        monkeypatch.setattr(planner, "get_metadata_conn", lambda: _Conn(
            lambda sql, params=None: [r for r in rows
                                      if ("name in" in " ".join(sql.split()).lower()
                                          and r["name"] in set(params or ()))
                                      or ("id = %s" in " ".join(sql.split()).lower()
                                          and r["id"] == int((params or (0,))[0]))]))
        return planner

    def test_source_scope_covers_exec_and_declared_names(self, monkeypatch):
        rows = [{"id": 11, "name": "prd-cn"}, {"id": 12, "name": "prd-jp"}]
        planner = self._stub(monkeypatch, _ds(datasource_id=11,
                                              available_datasources=["prd-jp"]), rows)
        ids, labels = planner._dataset_source_scope(_ds(datasource_id=11,
                                                        available_datasources=["prd-jp"]))
        assert ids == {11, 12} and sorted(labels) == ["prd-cn", "prd-jp"]

    def test_dangling_name_labelled_and_excluded_from_ids(self, monkeypatch):
        planner = self._stub(monkeypatch, _ds(), [{"id": 11, "name": "prd-cn"}])
        ids, labels = planner._dataset_source_scope(
            _ds(datasource_id=11, available_datasources=["ghost"]))
        assert ids == {11}
        assert any("ghost" in x and "已失效" in x for x in labels), labels

    def test_default_engine_scope_is_zero(self, monkeypatch):
        planner = self._stub(monkeypatch, _ds(), [{"id": 11, "name": "prd-cn"}])
        ids, labels = planner._dataset_source_scope(_ds(datasource_id=0))
        assert ids == {0} and labels == ["默认引擎"]

    def test_plan_refuses_source_outside_scope(self, monkeypatch):
        """路由目标源不在可用范围 → 空计划 + 指到该登记哪个源（不静默换源）。"""
        planner = self._stub(monkeypatch, _ds(datasource_id=11,
                                              available_datasources=["prd-jp"]),
                             [{"id": 11, "name": "prd-cn"}, {"id": 12, "name": "prd-jp"}])
        planned = planner.plan(self._query(), self._binding(13))
        assert planned.sql == ""
        msg = " ".join(planned.warnings)
        assert "不适用当前目标数据源" in msg and "prd-cn" in msg
        assert "13" not in msg                          # 文案只出业务名（§7）

    def test_plan_proceeds_when_source_in_scope(self, monkeypatch):
        planner = self._stub(monkeypatch, _ds(datasource_id=11,
                                              available_datasources=["prd-jp"]),
                             [{"id": 11, "name": "prd-cn"}, {"id": 12, "name": "prd-jp"}])
        planned = planner.plan(self._query(), self._binding(12))
        assert not any("不适用当前目标数据源" in w for w in planned.warnings), planned.warnings


# ── 9. 本体 activate 一致性门禁 ─────────────────────────────────────────

class TestOntologyActivateGate:
    def _stub(self, monkeypatch, rows, deploy):
        from backend.modules.catalog.services import ontology_service as ont

        monkeypatch.setattr("backend.core.site_service.model_deploy_sites",
                            lambda mid: deploy)
        monkeypatch.setattr(ont, "get_metadata_conn",
                            lambda: _Conn(lambda sql, params=None: rows))
        return ont

    def test_missing_route_blocks_activate(self, monkeypatch):
        ont = self._stub(
            monkeypatch,
            [{"object_key": "order", "dataset_ref": "订单概览", "dataset_name": "订单概览",
              "exec_id": 11, "exec_name": "prd-cn", "avail": json.dumps(["prd-jp"])}],
            [{"site_code": "cn", "datasource": "prd-cn"},
             {"site_code": "us", "datasource": "prd-us"}])
        issues = ont.find_dataset_scope_issues(7)
        assert issues and "prd-us" in issues[0] and "订单概览" in issues[0]

    def test_full_coverage_passes(self, monkeypatch):
        ont = self._stub(
            monkeypatch,
            [{"object_key": "order", "dataset_ref": "订单概览", "dataset_name": "订单概览",
              "exec_id": 11, "exec_name": "prd-cn",
              "avail": json.dumps(["prd-jp", "prd-us"])}],
            [{"site_code": "cn", "datasource": "prd-cn"},
             {"site_code": "jp", "datasource": "prd-jp"}])
        assert ont.find_dataset_scope_issues(7) == []

    def test_no_deploy_sites_skips_check(self, monkeypatch):
        """未配部署站点 = 沿用逐对象绑定行，不存在跨源路由，不得凭空报错。"""
        ont = self._stub(monkeypatch, [], [])
        assert ont.find_dataset_scope_issues(7) == []

    def test_activate_consumes_the_gate(self):
        """门禁必须真的挂在 activate 上（登记不消费 = 空头承诺）。"""
        from backend.modules.catalog.services import ontology_service as ont

        src = inspect.getsource(ont.activate)
        assert "find_dataset_scope_issues" in src


# ── 10. Chat 工具面（只接业务名 / 剥离 id / 定源规范） ──────────────────

class TestChatSurface:
    def test_query_dataset_tool_schema_has_no_datasource_id(self):
        from backend.modules.mind.execution.sdk_tools import semantic_tools as st

        spec = next(s for s in st.TOOL_SPECS if s["name"] == "query_dataset")
        assert "datasource_id" not in spec["schema"]
        assert {"site", "datasource"} <= set(spec["schema"])

    def test_query_dataset_handler_passes_business_names(self):
        from backend.modules.mind.execution.sdk_tools import semantic_tools as st

        src = inspect.getsource(st.query_dataset)
        assert '"site": args.get("site")' in src
        assert '"datasource": args.get("datasource")' in src
        assert "preferred_datasource_id" in src

    def test_resource_guard_strips_datasource_id_for_query_dataset(self):
        from backend.modules.mind.execution import resource_guard as rg

        assert "query_dataset" in inspect.getsource(rg)

    def test_function_tools_list_datasets_returns_business_names(self, monkeypatch):
        from backend.modules.mind.execution import function_tools as ft

        monkeypatch.setattr(ft, "_identity", lambda: {"user_id": 1, "role": "analyst",
                                                      "workspace_id": 301})
        monkeypatch.setattr(ft, "_limit", lambda args: 50)
        rows = [{"id": 501, "name": "订单概览", "description": "", "source_type": "sql",
                 "object_key": "", "chart_type": "", "status": "active",
                 "available_datasources": json.dumps(["prd-jp"]),
                 "datasource_name": "prd-cn", "created_at": None, "updated_at": None},
                {"id": 502, "name": "单源集", "description": "", "source_type": "sql",
                 "object_key": "", "chart_type": "", "status": "active",
                 "available_datasources": None,
                 "datasource_name": "prd-cn", "created_at": None, "updated_at": None}]
        monkeypatch.setattr(ft, "_select", lambda sql, params=None: [dict(r) for r in rows])
        out = json.loads(ft._list_datasets({})["content"][0]["text"])
        by_name = {d["name"]: d for d in out["datasets"]}
        assert by_name["订单概览"]["datasource"] == "prd-cn"
        assert by_name["订单概览"]["available_datasources"] == ["prd-cn", "prd-jp"]
        assert "available_datasources" not in by_name["单源集"]      # 单源不加噪声
        assert "datasource_id" not in json.dumps(out["datasets"])   # §7 黑盒：只回业务名

    def test_prompt_requires_explicit_source_for_multi_source(self):
        from backend.modules.mind.execution import prompt_composer as pc

        src = inspect.getsource(pc)
        assert "available_datasources" in src and "不得猜源" in src

    def test_graphrag_hydration_matches_scope(self):
        from backend.modules.mind.rag.strategies import graphrag as gr

        src = inspect.getsource(gr)
        assert "JSON_CONTAINS" in src and "available_datasources" in src

    def test_preview_api_refuses_internal_id(self):
        from backend.modules.viz.api import datasets as api

        src = inspect.getsource(api.preview_dataset)
        assert "不接受数据源内部 id" in src
        assert '"site": req.get("site")' in src
