"""v0.5/v0.6 部署清单与意图契约（GLOBAL_SEMANTIC_LAYER_DESIGN.md §6/§7）。

- 部署清单从**模型级 deploy_sites** 派生（v0.6，事实源唯一，不落第二登记处）；
  未声明站点的模型（源本体/存量）回落绑定行自身的数据源；
- 站点不是 SQL 维度：「站点」维度拒收 + 编排引导；
- 数据源寻址：site(站点业务名) / datasource(名) 在授权集内解析，
  未命中回抛候选名（不猜）；
- 服务端跨站展开机制已退役（maybe_execute_cross_site/resolve_deployments），
  此处锁定退役状态防旧机制回潮——跨站=LLM 逐站编排（llm suite trace 盯轨迹）。
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.semantics import cross_site  # noqa: E402
from backend.semantics.intent import parse_intent  # noqa: E402

FAKE_BIND = [
    {"object_key": "case", "datasource_name": "prd-cn"},
    {"object_key": "case", "datasource_name": "prd-en"},
    {"object_key": "case", "datasource_name": "prd-jp"},
    {"object_key": "help_center", "datasource_name": "prd-cn"},
]
FAKE_SITES = {"prd-cn": "中国站", "prd-en": "国际站", "prd-jp": "日本站"}
FAKE_META = {
    "case": {"name": "案例", "aliases": ["case", "工单"]},
    "help_center": {"name": "官网询盘", "aliases": []},
}


class TestDeploymentsListing:
    def test_derives_from_bindings(self, monkeypatch):
        monkeypatch.setattr(cross_site, "_load_facts",
                            lambda: (FAKE_BIND, FAKE_SITES, FAKE_META))
        out = cross_site.list_deployments()
        by_key = {e["object"]: e for e in out}
        case = by_key["case"]
        assert case["cross_site"] is True
        assert case["name"] == "案例" and "工单" in case["aliases"]
        assert [d["site"] for d in case["deployments"]] == ["中国站", "国际站", "日本站"]
        assert [d["datasource"] for d in case["deployments"]] == ["prd-cn", "prd-en", "prd-jp"]
        hc = by_key["help_center"]
        assert hc["cross_site"] is False
        assert hc["deployments"] == [{"site": "中国站", "datasource": "prd-cn"}]

    def test_site_missing_falls_back_to_datasource_name(self, monkeypatch):
        """产品行未回填 site 时用数据源名占位（不编造站点名）。"""
        monkeypatch.setattr(cross_site, "_load_facts",
                            lambda: ([{"object_key": "x", "datasource_name": "prd-en"}],
                                     {}, {"x": {"name": "X", "aliases": []}}))
        out = cross_site.list_deployments()
        assert out[0]["deployments"] == [{"site": "prd-en", "datasource": "prd-en"}]

    def test_execution_machinery_retired(self):
        """服务端跨站展开已退役（v0.5）：站点编排归 LLM，禁止旧机制回潮。"""
        for retired in ("maybe_execute_cross_site", "resolve_deployments",
                        "_reverse_deployments", "list_product_classes"):
            assert not hasattr(cross_site, retired), f"{retired} 应已退役"


class TestDeploymentFactSource:
    """v0.6：部署清单的事实源 = 模型级 deploy_sites，不再逐对象逐站登记。"""

    def test_derives_from_model_deploy_sites(self):
        """契约行只在主源，但模型声明了 3 站 → 对象清单必须出 3 站。"""
        facts = _load_facts_with(
            models=[{"id": 1, "deploy_sites": json.dumps([
                {"site_code": "cn", "datasource": "prd-cn"},
                {"site_code": "int", "datasource": "prd-en"},
                {"site_code": "jp", "datasource": "prd-jp"}])}],
            site_names={"cn": "中国站点", "int": "国际站点(新加坡)", "jp": "日本站点"},
            bindings=[{"model_id": 1, "object_key": "case", "datasource_name": "prd-cn"}],
            objects=[{"object_key": "case", "display_name": "案例", "aliases": '["工单"]'}])
        bind_rows, site_map, meta = facts
        assert site_map["prd-jp"] == "日本站点"
        assert sorted(r["datasource_name"] for r in bind_rows
                      if r["object_key"] == "case") == ["prd-cn", "prd-en", "prd-jp"]
        assert meta["case"]["name"] == "案例"

    def test_model_without_deploy_sites_falls_back_to_binding_rows(self):
        """未声明站点的模型（源本体/存量）仍按绑定行自身数据源上报。"""
        bind_rows, site_map, _meta = _load_facts_with(
            models=[{"id": 2, "deploy_sites": None}],
            site_names={},
            bindings=[{"model_id": 2, "object_key": "rum_event", "datasource_name": "arms-prd-cn"}],
            objects=[])
        assert bind_rows == [{"object_key": "rum_event", "datasource_name": "arms-prd-cn"}]
        assert site_map == {}  # 不编造站点名，下游回落到数据源名占位

    def test_object_site_rules_restrict_object_to_declared_sites(self):
        """对象级白名单：模型声明 3 站，但对象只在部分站点有表 → 清单不得广告别站。

        与 binding_resolver 的取数硬门同源：LLM 看到可用却查不到 = 误导编排。
        """
        facts = _load_facts_with(
            models=[{"id": 1,
                     "deploy_sites": json.dumps([
                         {"site_code": "cn", "datasource": "prd-cn"},
                         {"site_code": "int", "datasource": "prd-en"},
                         {"site_code": "jp", "datasource": "prd-jp"}]),
                     "object_site_rules": json.dumps({"rum_event": ["cn"]})}],
            site_names={"cn": "中国站点", "int": "国际站点", "jp": "日本站点"},
            bindings=[{"model_id": 1, "object_key": "rum_event", "datasource_name": "prd-cn"},
                      {"model_id": 1, "object_key": "case", "datasource_name": "prd-cn"}],
            objects=[])
        bind_rows, _site_map, _meta = facts
        assert {"object_key": "rum_event", "datasource_name": "prd-cn"} in bind_rows
        # rum_event 被收紧到 cn，不得出现在 int/jp（未登记的对象照旧全站点展开）
        assert [r["datasource_name"] for r in bind_rows if r["object_key"] == "rum_event"] == ["prd-cn"]
        assert sorted(r["datasource_name"] for r in bind_rows if r["object_key"] == "case") \
            == ["prd-cn", "prd-en", "prd-jp"]

    def test_unparsable_object_site_rules_does_not_shrink(self):
        """规则非法/空 → 不收紧（模型级声明照旧展开，不影响存量）。"""
        for raw in (None, "", "[]", "not-json", json.dumps({"case": []})):
            bind_rows, _m, _meta = _load_facts_with(
                models=[{"id": 1,
                         "deploy_sites": json.dumps([{"site_code": "cn", "datasource": "prd-cn"},
                                                     {"site_code": "jp", "datasource": "prd-jp"}]),
                         "object_site_rules": raw}],
                site_names={"cn": "中国站点", "jp": "日本站点"},
                bindings=[{"model_id": 1, "object_key": "case", "datasource_name": "prd-cn"}],
                objects=[])
            assert sorted(r["datasource_name"] for r in bind_rows) == ["prd-cn", "prd-jp"], raw


class _FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self._last = []

    def execute(self, sql, params=None):
        self._last = self._results.pop(0) if self._results else []

    def fetchall(self):
        return list(self._last)


class _FakeConn:
    def __init__(self, results):
        self._cursor = _FakeCursor(results)

    def cursor(self):
        outer = self

        class _Ctx:
            def __enter__(_):
                return outer._cursor
            def __exit__(_, *a):
                return False
        return _Ctx()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _load_facts_with(models, site_names, bindings, objects):
    """按 _load_facts 的查询顺序打桩元数据库（站点名 → 模型 → 绑定行 → 对象元信息）。"""
    results = [
        [{"site_code": k, "display_name": v} for k, v in site_names.items()],
        models,
        bindings,
        objects,
    ]
    conn = _FakeConn(results)
    import backend.common.db.metadata_db as mdb
    original = mdb.get_metadata_conn
    mdb.get_metadata_conn = lambda: conn
    try:
        return cross_site._load_facts()
    finally:
        mdb.get_metadata_conn = original


class TestIntentContract:
    def test_site_dim_rejected_with_orchestration_hint(self):
        q, err, _ = parse_intent(
            {"object": "case", "metrics": ["案例数"], "dimensions": ["站点"]})
        assert q is None
        # v0.6：拒收文案引导到 site 参数（站点业务名），不再要求 LLM 自己拿源名
        assert "编排" in err and "site" in err

    def test_site_dim_alias_rejected(self):
        for alias in ("站点名", "site", "所属站点"):
            q, err, _ = parse_intent(
                {"object": "case", "metrics": ["案例数"], "dimensions": [alias]})
            assert q is None and err, alias

    def test_business_dims_pass(self):
        q, err, _ = parse_intent(
            {"object": "case", "metrics": ["案例数"], "dimensions": ["创建日期"]})
        assert err is None and q is not None

    def test_datasource_name_field_accepted(self):
        q, err, _ = parse_intent(
            {"object": "case", "metrics": ["案例数"], "datasource": "prd-en"})
        assert err is None and q.datasource == "prd-en"

    def test_scope_field_retired(self):
        """scope:"all_sites" 已退役：被当作未知键丢弃（不报错也不生效）。"""
        q, err, notes = parse_intent(
            {"object": "case", "metrics": ["案例数"], "scope": "all_sites"})
        assert err is None and q is not None
        assert any("dropped_unknown_keys:scope" in n for n in notes)


class TestDatasourceAddressing:
    def _ctx(self, available):
        return SimpleNamespace(datasource_id=100, workspace_id=301, user_id=1,
                               extra={"available_datasource_ids": available})

    def test_resolves_within_authorized(self, monkeypatch):
        from backend.modules.mind.execution.sdk_tools import query_tools
        monkeypatch.setattr(
            "backend.modules.mind.execution.resource_guard._resolve_authorized_source",
            lambda name, avail: 200 if name == "prd-en" and 200 in avail else
            (_ for _ in ()).throw(ValueError(f"unknown datasource '{name}'")))
        assert query_tools._resolve_datasource_arg(
            {"datasource": "prd-en"}, self._ctx([100, 200])) == 200

    def test_omitted_falls_back_to_session_source(self):
        from backend.modules.mind.execution.sdk_tools import query_tools
        assert query_tools._resolve_datasource_arg({}, self._ctx([100])) == 100

    def test_unknown_name_raises_with_candidates(self, monkeypatch):
        from backend.modules.mind.execution.sdk_tools import query_tools
        monkeypatch.setattr(
            "backend.modules.mind.execution.resource_guard._resolve_authorized_source",
            lambda name, avail: (_ for _ in ()).throw(ValueError(
                f"unknown datasource '{name}', candidates: prd-cn, prd-en")))
        try:
            query_tools._resolve_datasource_arg({"datasource": "nope"}, self._ctx([100]))
            raise AssertionError("应当抛出")
        except ValueError as e:
            assert "candidates" in str(e)  # 可操作错误：带候选名，不猜
