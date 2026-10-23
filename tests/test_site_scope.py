"""站点作用域（多站点部署拓扑）契约测试。

锁住的行为（迁移 docker/mysql/site_scope_migration.sql + backend/core/site_service.py）：
1. 站点清单事实源 = `adh_sites`（6 行种子，global 是聚合态）；数据源以 site_code 归属站点；
2. 可查站点 = 角色数据源授权的**派生视图**（空授权 fail-closed；单站不出「全球」）；
3. 站点 → 生效源解析只在授权集内做，多义/无权/聚合态一律 fail-loud，且文案只含业务名
   （不得出现 datasource_id/host，护栏 §7）；
4. 业务本体模型级 `deploy_sites` 取代逐对象逐站绑定行：契约行在主源、生效源改写到请求站，
   产品身份随站点刷新；未声明站点/路由目标不符一律拒绝（宁缺勿错）；
5. 模型未配置 deploy_sites 时解析行为与改造前一致（源本体/系统本体/存量零影响）。
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402
from backend.core import site_service  # noqa: E402
from backend.semantics.binding_resolver import resolve_binding  # noqa: E402

BUSINESS_MODEL_ID = 1789467214865307   # 联耀业务本体（一份契约 × 5 站）
TMP_MODEL_ID = 9999999990077
TMP_MODEL_NAME = "__test_site_scope__"
TMP_OBJECT_KEY = "__t_site_route_obj__"


def _ds_by_name(name: str) -> dict:
    rows = _query("SELECT id, name, site_code FROM adh_datasources WHERE name = %s", (name,))
    return rows[0] if rows else {}


def _query(sql: str, params=None) -> list:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall() or [])


# 真实站点拓扑（迁移已回填）：按名取 id，避免硬编码雪花 id
IDS = {
    r["name"]: int(r["id"])
    for r in _query("SELECT id, name FROM adh_datasources "
                    "WHERE name IN ('prd-cn','prd-en','prd-eu','prd-uk','prd-jp','prd-sls-cn')")
}


def _exec(sql: str, params=None) -> None:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()


def _cleanup_tmp_model() -> None:
    _exec("DELETE FROM adh_ontology_bindings WHERE model_id = %s", (TMP_MODEL_ID,))
    _exec("DELETE FROM adh_ontology_models WHERE id = %s", (TMP_MODEL_ID,))


def _make_tmp_model(deploy_sites: list | None, status: str = "active") -> None:
    """建一个业务模型 + 只在 prd-cn（契约站）登记的一条绑定行。"""
    _cleanup_tmp_model()
    contract = _ds_by_name("prd-cn")
    _exec(
        "INSERT INTO adh_ontology_models (id, name, datasource_id, kind, status, json_content, "
        "deploy_sites, created_at, updated_at) "
        "VALUES (%s, %s, 0, 'business', %s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
        (TMP_MODEL_ID, TMP_MODEL_NAME, status, json.dumps({"name": TMP_MODEL_NAME, "objects": []}),
         None if deploy_sites is None else json.dumps(deploy_sites, ensure_ascii=False)))
    _exec(
        "INSERT INTO adh_ontology_bindings (model_id, object_key, datasource_id, datasource_name, "
        "product_ref, physical_table, column_map, bind_kind, sync_state, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, 'prd-cn', 'prd-cn.t_case_records', 't_case_records', %s, 'primary', "
        "'bound', 'active', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
        (TMP_MODEL_ID, TMP_OBJECT_KEY, int(contract["id"]), json.dumps({"案例ID": "id"})))


FAKE_SITES = [
    {"site_code": "global", "display_name": "全球站点", "is_aggregate": 1, "sort_order": 0, "is_active": 1},
    {"site_code": "cn", "display_name": "中国站点", "is_aggregate": 0, "sort_order": 10, "is_active": 1},
    {"site_code": "int", "display_name": "国际站点(新加坡)", "is_aggregate": 0, "sort_order": 20, "is_active": 1},
    {"site_code": "eu", "display_name": "欧洲站点", "is_aggregate": 0, "sort_order": 30, "is_active": 1},
]


@pytest.fixture
def fake_sites(monkeypatch):
    """站点清单打桩：别名归一/解析逻辑的纯单测（不依赖库里有没有 eu）。"""
    monkeypatch.setattr(site_service, "list_sites",
                        lambda only_active=True, include_aggregate=True: [
                            dict(s) for s in FAKE_SITES
                            if (not only_active or s["is_active"])
                            and (include_aggregate or not s["is_aggregate"])])


# ── 1. 站点清单与数据源归属（迁移已应用）────────────────────────────

class TestSiteRegistrySeed:
    def test_seed_has_six_sites_with_global_aggregate(self):
        codes = {s["site_code"]: s for s in site_service.list_sites()}
        assert {"global", "cn", "int", "eu", "uk", "jp"} <= set(codes)
        assert int(codes["global"]["is_aggregate"]) == 1
        assert int(codes["cn"]["is_aggregate"]) == 0
        # 展示顺序由 sort_order 决定（全球在最前）
        order = [s["site_code"] for s in site_service.list_sites()]
        assert order[0] == "global"

    def test_prd_datasources_are_site_bound(self):
        """prd-* 已回填归属站点；未站点化的平台库保持空（不编造站点）。"""
        rows = _query("SELECT name, site_code FROM adh_datasources "
                      "WHERE name IN ('prd-cn','prd-en','prd-eu','prd-uk','prd-jp')")
        got = {r["name"]: r["site_code"] for r in rows}
        assert got.get("prd-cn") == "cn"
        assert got.get("prd-en") == "int"
        assert {got.get(n) for n in ("prd-eu", "prd-uk", "prd-jp")} == {"eu", "uk", "jp"}

    def test_aggregate_site_is_not_assignable_to_datasource(self, fake_sites):
        with pytest.raises(site_service.SiteError):
            site_service.validate_site_code("global", allow_aggregate=False)

    def test_unknown_site_is_rejected(self, fake_sites):
        with pytest.raises(site_service.SiteError) as exc:
            site_service.validate_site_code("mars", allow_aggregate=False)
        assert "中国站点" in str(exc.value)  # 回抛可选站点名，不静默放过


# ── 2. 站点写法归一 ────────────────────────────────────────────────

class TestSiteAliasNormalization:
    @pytest.mark.parametrize("ref", ["cn", "CN", "中国", "中国站", "中国站点"])
    def test_chinese_variants_all_resolve(self, fake_sites, ref):
        assert site_service.normalize_site_ref(ref) == "cn"

    def test_parenthesised_alias_resolves(self, fake_sites):
        """「国际站点(新加坡)」的括号内别名也是合法写法（用户口语说新加坡）。"""
        assert site_service.normalize_site_ref("新加坡") == "int"
        assert site_service.normalize_site_ref("国际") == "int"

    def test_unknown_is_empty_not_guessed(self, fake_sites):
        assert site_service.normalize_site_ref("美国") == ""
        assert site_service.normalize_site_ref("") == ""


# ── 3. 站点 → 生效数据源（只在授权集内解析）────────────────────────

class TestResolveSiteToSource:
    def test_unique_source_in_site(self, fake_sites, monkeypatch):
        monkeypatch.setattr(site_service, "datasources_of_site",
                            lambda code, allowed: ([{"id": 200, "name": "prd-en", "db_type": "mysql"}]
                                                   if code == "int" else []))
        assert site_service.resolve_site_to_source("国际", [200, 100]) == 200

    def test_ambiguous_site_raises_with_business_names_only(self, fake_sites, monkeypatch):
        """站点内多源且无模型路由 → 回抛候选源名（不猜），且文案不含 id/host。"""
        monkeypatch.setattr(site_service, "datasources_of_site", lambda code, allowed: [
            {"id": 101, "name": "prd-cn", "db_type": "mysql"},
            {"id": 102, "name": "prd-sls-cn", "db_type": "sls"},
        ])
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "")
        with pytest.raises(site_service.SiteError) as exc:
            site_service.resolve_site_to_source("中国", [101, 102])
        msg = str(exc.value)
        assert "prd-cn" in msg and "prd-sls-cn" in msg
        assert "101" not in msg and "102" not in msg

    def test_model_deploy_sites_disambiguates(self, fake_sites, monkeypatch):
        monkeypatch.setattr(site_service, "datasources_of_site", lambda code, allowed: [
            {"id": 101, "name": "prd-cn", "db_type": "mysql"},
            {"id": 102, "name": "prd-sls-cn", "db_type": "sls"},
        ])
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "prd-cn")
        assert site_service.resolve_site_to_source("中国", [101, 102], model_id=7) == 101

    def test_aggregate_site_is_refused_with_orchestration_hint(self, fake_sites, monkeypatch):
        monkeypatch.setattr(site_service, "datasources_of_site", lambda code, allowed: [])
        with pytest.raises(site_service.SiteError) as exc:
            site_service.resolve_site_to_source("全球", [101])
        assert "逐" in str(exc.value) or "各调一次" in str(exc.value)

    def test_unauthorized_site_fails_closed(self, fake_sites, monkeypatch):
        """站点存在但授权集内没有该站的源 → 拒绝并列出当前可查站点（不换站重试）。"""
        monkeypatch.setattr(site_service, "datasources_of_site", lambda code, allowed: [])
        monkeypatch.setattr(site_service, "authorized_sites_from_ids",
                            lambda ids: [{"site_code": "int", "display_name": "国际站点(新加坡)",
                                          "is_aggregate": 0, "sort_order": 20,
                                          "datasources": ["prd-en"]}])
        with pytest.raises(site_service.SiteError) as exc:
            site_service.resolve_site_to_source("欧洲", [200])
        assert "国际站点" in str(exc.value)

    def test_session_source_returns_zero_when_ambiguous(self, fake_sites, monkeypatch):
        """会话站点派生不猜源：多义返回 0，交由工具侧 site 参数逐查询解析。"""
        monkeypatch.setattr(site_service, "datasources_of_site", lambda code, allowed: [
            {"id": 101, "name": "prd-cn", "db_type": "mysql"},
            {"id": 102, "name": "prd-sls-cn", "db_type": "sls"},
        ])
        assert site_service.session_source_for_site("中国", [101, 102]) == 0
        assert site_service.session_source_for_site("全球", [101]) == 0
        assert site_service.session_source_for_site("", [101]) == 0


# ── 4. 可查站点 = 角色授权派生（不是第二套裁决）─────────────────────

class TestAuthorizedSitesDerivation:
    def _patch_allowed(self, monkeypatch, ids):
        from backend.core.role_service import role_service
        monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                            lambda user_id, workspace_id=0: list(ids))

    def test_empty_authorization_is_fail_closed(self, monkeypatch):
        self._patch_allowed(monkeypatch, [])
        assert site_service.authorized_sites(1, 0) == []

    def test_single_site_does_not_offer_global(self, monkeypatch):
        """只有一个可查站点时给「全球」是空承诺 → 不出聚合态。"""
        one = int(_ds_by_name("prd-cn")["id"])
        self._patch_allowed(monkeypatch, [one])
        codes = [s["site_code"] for s in site_service.authorized_sites(1, 0)]
        assert codes == ["cn"]

    def test_multi_site_offers_global_first(self, monkeypatch):
        ids = [int(_ds_by_name(n)["id"]) for n in ("prd-cn", "prd-jp")]
        self._patch_allowed(monkeypatch, ids)
        out = site_service.authorized_sites(1, 0)
        codes = [s["site_code"] for s in out]
        assert "global" in codes and codes[0] == "global"
        assert {"cn", "jp"} <= set(codes)
        cn = next(s for s in out if s["site_code"] == "cn")
        assert "prd-cn" in [d["name"] for d in cn["datasources"]]

    def test_non_site_datasource_contributes_nothing(self, monkeypatch):
        """未站点化的平台库不产生假站点。"""
        self._patch_allowed(monkeypatch, [int(_ds_by_name("Doris")["id"])])
        assert site_service.authorized_sites(1, 0) == []


# ── 5. 模型级 deploy_sites 的绑定路由 ──────────────────────────────

class TestModelSiteRouting:
    def setup_method(self):
        _cleanup_tmp_model()

    def teardown_method(self):
        _cleanup_tmp_model()

    def test_contract_row_routes_to_requested_site(self):
        """请求日本站：命中 prd-cn 契约行，但生效源/产品身份改写为日本站。"""
        jp = int(_ds_by_name("prd-jp")["id"])
        _make_tmp_model([{"site_code": "cn", "datasource": "prd-cn"},
                         {"site_code": "jp", "datasource": "prd-jp"}])
        binding, warnings = resolve_binding(TMP_OBJECT_KEY, datasource_id=jp)
        assert binding is not None, warnings
        assert binding.datasource_id == jp
        assert binding.datasource_name == "prd-jp"
        assert binding.site_routed is True
        assert binding.physical_table == "t_case_records"      # 同构：形状沿用契约行
        assert binding.product_ref == "prd-jp.t_case_records"  # 产品身份随站刷新

    def test_contract_site_keeps_exact_row(self):
        cn = int(_ds_by_name("prd-cn")["id"])
        _make_tmp_model([{"site_code": "cn", "datasource": "prd-cn"}])
        binding, warnings = resolve_binding(TMP_OBJECT_KEY, datasource_id=cn)
        assert binding is not None, warnings
        assert binding.site_routed is False  # 精确命中不算路由

    def test_site_not_declared_is_refused(self):
        """模型声明了站点但不含请求站点 → 拒绝并回已适用站点（不拿别站数据充数）。"""
        eu = int(_ds_by_name("prd-eu")["id"])
        _make_tmp_model([{"site_code": "cn", "datasource": "prd-cn"}])
        binding, warnings = resolve_binding(TMP_OBJECT_KEY, datasource_id=eu)
        assert binding is None
        assert any("不适用站点" in w and "欧洲站点" in w for w in warnings), warnings
        assert any("中国站点" in w for w in warnings), warnings

    def test_route_target_mismatch_is_refused(self):
        """同站点内多源：请求源不是模型声明的业务库 → 拒绝并说明路由目标。"""
        sls = int(_ds_by_name("prd-sls-cn")["id"])
        _make_tmp_model([{"site_code": "cn", "datasource": "prd-cn"}])
        binding, warnings = resolve_binding(TMP_OBJECT_KEY, datasource_id=sls)
        assert binding is None
        assert any("prd-cn" in w and "prd-sls-cn" in w for w in warnings), warnings

    def test_model_without_deploy_sites_is_unchanged(self):
        """deploy_sites=NULL：源本体/存量口径零变化（仍按绑定行精确匹配）。"""
        cn = int(_ds_by_name("prd-cn")["id"])
        _make_tmp_model(None)
        binding, warnings = resolve_binding(TMP_OBJECT_KEY, datasource_id=cn)
        assert binding is not None, warnings
        assert binding.site_routed is False
        other = int(_ds_by_name("prd-jp")["id"])
        miss, _ = resolve_binding(TMP_OBJECT_KEY, datasource_id=other)
        assert miss is None  # 未声明站点时不再展开到别站


# ── 6. 存量收敛结果（迁移已 apply）────────────────────────────────

class TestMigrationConvergence:
    def test_business_model_declares_five_sites(self):
        rows = _query("SELECT deploy_sites FROM adh_ontology_models WHERE id = %s",
                      (BUSINESS_MODEL_ID,))
        assert rows, "业务本体行缺失"
        raw = rows[0]["deploy_sites"]
        declared = json.loads(raw) if isinstance(raw, str) else (raw or [])
        assert {d["site_code"] for d in declared} == {"cn", "int", "eu", "uk", "jp"}
        assert {d["datasource"] for d in declared} == {"prd-cn", "prd-en", "prd-eu", "prd-uk", "prd-jp"}

    def test_binding_rows_collapsed_to_one_contract_row_per_object(self):
        """一份契约 × 模型级站点：active 行不再逐站重复。"""
        rows = _query(
            "SELECT object_key, COUNT(*) AS n FROM adh_ontology_bindings "
            "WHERE model_id = %s AND status = 'active' GROUP BY object_key "
            "HAVING COUNT(*) > 1", (BUSINESS_MODEL_ID,))
        assert not rows, f"仍有逐站重复的 active 绑定行: {rows}"

    def test_cross_site_query_still_resolves_after_collapse(self):
        """收敛后逐站取数照常命中（经站点路由，产品身份是本站的）。"""
        jp = int(_ds_by_name("prd-jp")["id"])
        binding, warnings = resolve_binding("case", datasource_id=jp)
        assert binding is not None, warnings
        assert binding.datasource_id == jp and binding.site_routed
        assert binding.product_ref == "prd-jp.t_case_records"


class TestDeploySiteActivationGate:
    """activate 前的引用一致性门禁（§4 宁阻断勿悬空）：站点/数据源事后被
    停用、删除、改归属时必须拒绝激活，而不是拿一堆取不到数的配置上线。"""

    def test_real_business_model_passes(self):
        from backend.modules.catalog.services.ontology_service import find_deploy_site_issues
        assert find_deploy_site_issues(BUSINESS_MODEL_ID) == []

    def test_model_without_deploy_sites_is_not_a_defect(self):
        """未配置 deploy_sites（源本体/存量）不适用本门禁。"""
        from backend.modules.catalog.services.ontology_service import find_deploy_site_issues
        _make_tmp_model(None)
        try:
            assert find_deploy_site_issues(TMP_MODEL_ID) == []
        finally:
            _cleanup_tmp_model()

    def test_dangling_references_are_all_reported(self):
        from backend.modules.catalog.services.ontology_service import find_deploy_site_issues
        _make_tmp_model([
            {"site_code": "cn", "datasource": "prd-gone"},      # 源不存在
            {"site_code": "global", "datasource": "prd-cn"},    # 聚合态挂源
            {"site_code": "jp", "datasource": "prd-cn"},        # 归属不一致
            {"site_code": "cn", "datasource": "prd-cn"},        # 站点重复
        ])
        try:
            issues = "；".join(find_deploy_site_issues(TMP_MODEL_ID))
            assert "prd-gone" in issues and "不存在" in issues
            assert "聚合态" in issues
            assert "不一致" in issues
            assert "重复" in issues
        finally:
            _cleanup_tmp_model()

    def test_activate_refuses_dangling_sites(self):
        """门禁真的接在 activate 上（不是只写了个函数没人调）。

        草案态才走激活校验链（已是 active 的模型 activate 会短路返回），
        且站点门禁先于 doc 级校验（它是模型级配置，与 doc 内容无关）。
        """
        from backend.modules.catalog.services import ontology_service as osvc
        _make_tmp_model([{"site_code": "jp", "datasource": "prd-cn"}], status="draft")
        try:
            with pytest.raises(ValueError) as exc:
                osvc.activate(TMP_MODEL_ID)
            assert "适用站点" in str(exc.value)
        finally:
            _cleanup_tmp_model()


class TestAuthorizedSitesAPIContract:
    """`/datasources/authorized-sites` 的响应结构契约。

    真实事故：只缓存内层 list 会让“未命中返 {items:[..]}、命中返裸 list”，
    前端 `data.items` 变 undefined → 聊天站点选择器静默消失（看起来像“没有站点选项”）。
    """

    def test_shape_is_identical_on_cache_hit_and_miss(self, monkeypatch):
        import asyncio
        from backend.common import ttl_cache
        from backend.modules.catalog.api import datasources as ds_api

        store: dict = {}
        monkeypatch.setattr(ttl_cache.datasource_cache, "get", lambda k: store.get(k))
        monkeypatch.setattr(ttl_cache.datasource_cache, "set",
                            lambda k, v: store.__setitem__(k, v))
        monkeypatch.setattr(site_service, "authorized_sites", lambda uid, ws=0: [
            {"site_code": "cn", "display_name": "中国站点", "is_aggregate": 0,
             "sort_order": 10, "datasources": []}])

        first = asyncio.run(ds_api.list_authorized_sites(301, {"user_id": 1}))
        second = asyncio.run(ds_api.list_authorized_sites(301, {"user_id": 1}))
        assert store, "未进缓存则本用例没有意义"
        assert isinstance(first, dict) and isinstance(second, dict)
        assert first == second
        assert [x["site_code"] for x in first["items"]] == ["cn"]

    def test_legacy_bare_list_cache_entry_is_not_served(self, monkeypatch):
        """旧版本写进缓存的裸 list 必须当作未命中重算（自愈，不继续发错结构）。"""
        import asyncio
        from backend.common import ttl_cache
        from backend.modules.catalog.api import datasources as ds_api

        store = {"sites:auth:1:301": [{"site_code": "cn", "display_name": "中国站点"}]}
        monkeypatch.setattr(ttl_cache.datasource_cache, "get", lambda k: store.get(k))
        monkeypatch.setattr(ttl_cache.datasource_cache, "set",
                            lambda k, v: store.__setitem__(k, v))
        monkeypatch.setattr(site_service, "authorized_sites", lambda uid, ws=0: [])
        out = asyncio.run(ds_api.list_authorized_sites(301, {"user_id": 1}))
        assert out == {"items": []}


class TestSessionSiteTakesPrecedence:
    """会话站点是作用域权威，必须压过陈旧/默认的 datasource_id。

    真实事故（比错数更严重的是“错数还被说对”）：会话行同时持久化了 site_code='uk'
    与自动选中的 datasource_id=prd-cn，而旧派生条件是 `if not ctx.datasource_id`，
    根本不进入 → 选英国站点查了中国库，而提示词已告诉模型“会话站点=英国”，
    回答就把中国数据讲成了英国。本类用例永久锁定：站点赢、拿不定不猜、无权则报错。
    """

    def _apply(self, site, stale_ds, monkeypatch, allowed=None):
        from backend.core.role_service import role_service
        from backend.modules.mind.services.chat_service import _apply_session_site
        ids = allowed if allowed is not None else set(IDS.values())
        monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                            lambda uid, ws=0: sorted(ids))
        return _apply_session_site(site, stale_ds, 1, 301)

    def test_site_overrides_stale_datasource(self, monkeypatch):
        """选英国站 + 陈旧 prd-cn → 生效源必须是 prd-uk。"""
        got = self._apply("uk", IDS["prd-cn"], monkeypatch)
        assert got == IDS["prd-uk"], f"站点未压过陈旧源：{got}"

    def test_each_site_lands_on_its_own_source(self, monkeypatch):
        for code, name in (("cn", "prd-cn"), ("int", "prd-en"), ("eu", "prd-eu"),
                           ("uk", "prd-uk"), ("jp", "prd-jp")):
            got = self._apply(code, IDS["prd-cn"], monkeypatch)
            assert got == IDS[name], f"{code} 应落在 {name}，实际 {got}"

    def test_multi_source_site_uses_business_route_declaration(self, monkeypatch):
        """中国站点挂了 3 个源：靠活跃业务本体在本站声明的业务库定到 prd-cn（读已配事实，不是猜）。"""
        assert site_service.business_route_target("cn") == "prd-cn"
        assert self._apply("cn", 0, monkeypatch) == IDS["prd-cn"]

    def test_aggregate_site_clears_the_source(self, monkeypatch):
        """全球 = 不锁定单源（逐站编排），陈旧源必须被清掉而不是拿去当默认。"""
        assert self._apply("global", IDS["prd-cn"], monkeypatch) == 0

    def test_unauthorized_site_raises_instead_of_falling_back(self, monkeypatch):
        """站点被收回授权 → 报错停止，绝不静默回退到旧数据源。"""
        allowed = {IDS["prd-cn"], IDS["prd-jp"]}   # 没有 prd-eu/uk/en
        with pytest.raises(ValueError) as exc:
            self._apply("uk", IDS["prd-cn"], monkeypatch, allowed=allowed)
        assert "英国" in str(exc.value)

    def test_stale_site_code_raises_not_silent(self, monkeypatch):
        with pytest.raises(ValueError):
            self._apply("__已不存在的站点__", IDS["prd-cn"], monkeypatch)

    def test_bind_resources_also_honours_site(self, monkeypatch):
        """工具层纵深防御：bind_resources 也不能让陈旧 ds 压过站点。"""
        from backend.core.role_service import role_service
        from backend.modules.mind.execution.models import ExecutionContext
        from backend.modules.mind.execution.resource_guard import bind_resources
        monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                            lambda uid, ws=0: sorted(IDS.values()))
        ctx = ExecutionContext(user_id=1, workspace_id=301, datasource_id=IDS["prd-cn"],
                               extra={"site_code": "jp"})
        bind_resources(ctx, SimpleNamespace(as_bot={}))
        assert ctx.datasource_id == IDS["prd-jp"]

    def test_no_site_keeps_legacy_behaviour(self, monkeypatch):
        """未选站点（定时分析/未站点化部署）行为不变：ds 原样保留。"""
        from backend.core.role_service import role_service
        from backend.modules.mind.execution.models import ExecutionContext
        from backend.modules.mind.execution.resource_guard import bind_resources
        monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                            lambda uid, ws=0: sorted(IDS.values()))
        ctx = ExecutionContext(user_id=1, workspace_id=301, datasource_id=IDS["prd-cn"], extra={})
        bind_resources(ctx, SimpleNamespace(as_bot={}))
        assert ctx.datasource_id == IDS["prd-cn"]


# ── 8. 意图契约：site 是寻址参数，不是 SQL 维度 ────────────────────
class TestIntentSiteContract:
    def test_site_field_is_accepted_as_addressing(self):
        from backend.semantics.intent import parse_intent
        q, err, _ = parse_intent({"object": "case", "metrics": ["案例数"], "site": "中国"})
        assert err is None and q is not None
        assert q.site == "中国"

    def test_site_as_dimension_is_still_rejected(self):
        from backend.semantics.intent import parse_intent
        q, err, _ = parse_intent({"object": "case", "metrics": ["案例数"], "dimensions": ["站点"]})
        assert q is None and err
        assert "site" in err and "维度" in err

    def test_tool_schema_exposes_site(self):
        """run_semantic_query / execute_sql 的入参 schema 必须有 site（业务名，不含 id）。"""
        from backend.modules.mind.execution.sdk_tools.semantic_query import TOOL_SPECS as SEM
        from backend.modules.mind.execution.sdk_tools.query_tools import TOOL_SPECS as QRY
        desc = SEM[0]["description"]
        assert '"site"' in desc and "站点范围" in desc
        for spec in QRY:
            assert "site" in spec["schema"], spec["name"]
            assert "datasource_id" not in spec["schema"], spec["name"]


# ── 9. 提示词注入：站点范围段来自服务端派生 ────────────────────────

class TestPromptSiteScope:
    SITES = [
        {"site_code": "global", "display_name": "全球站点", "is_aggregate": 1,
         "sort_order": 0, "datasources": []},
        {"site_code": "cn", "display_name": "中国站点", "is_aggregate": 0,
         "sort_order": 10, "datasources": [{"id": 1, "name": "prd-cn", "db_type": "mysql"}]},
        {"site_code": "jp", "display_name": "日本站点", "is_aggregate": 0,
         "sort_order": 50, "datasources": [{"id": 2, "name": "prd-jp", "db_type": "mysql"}]},
    ]

    def test_global_session_gets_orchestration_rule(self):
        from backend.modules.mind.execution.prompt_composer import _site_scope_lines
        text = "\n".join(_site_scope_lines("global", self.SITES))
        assert "会话查询站点: 全球站点" in text
        assert "各调一次" in text and "各站合计≠去重全球数" in text
        assert "中国站点(prd-cn)" in text

    def test_single_site_session_does_not_tell_model_to_iterate(self):
        """会话选了中国站 → 规则③不得再说"会话站点=全球"（历史缺陷：文案与实际范围矛盾）。"""
        from backend.modules.mind.execution.prompt_composer import _site_scope_lines
        text = "\n".join(_site_scope_lines("cn", self.SITES))
        assert "会话查询站点: 中国站点" in text
        assert "会话站点=全球站点" not in text

    def test_no_site_choice_injects_nothing(self):
        """定时分析/评测等绑定单一源的链路不带 site_code → 不注入站点范围（不鼓励逐站展开）。"""
        from backend.modules.mind.execution.prompt_composer import _site_scope_lines
        assert _site_scope_lines("", self.SITES) == []

    def test_revoked_site_choice_injects_nothing(self):
        """会话带的站点已不在可查清单（授权被收回）→ 不注入假范围。"""
        from backend.modules.mind.execution.prompt_composer import _site_scope_lines
        assert _site_scope_lines("eu", self.SITES) == []

    def test_prompt_never_leaks_datasource_id(self):
        from backend.modules.mind.execution.prompt_composer import _site_scope_lines
        text = "\n".join(_site_scope_lines("global", self.SITES))
        assert " 1" not in text.replace("①", "").replace("②", "")  # 不出现裸 id 清单
        assert "datasource_id" not in text


# ── 10. 分层契约：core.site_service 不反向依赖 modules ───────────────

class TestLayering:
    def test_site_service_has_no_module_imports(self):
        import ast
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "backend/core/site_service.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            for t in targets:
                assert not t.startswith("backend.modules"), f"core → modules 违规: {t}"
