"""站点参数按对象消歧（观测对象不被「本站默认业务库」抢走）+ 绑定告警脱敏。

背景（实测 EDMCA 落地页访问量时暴露，会话 1783069459140 的同类问题）：
中国站下 admin 授权了 3 个源（`prd-cn` mysql 业务库 + `arms-prd-cn`/`prd-sls-cn` 观测源）。
`run_semantic_query` 的多站点编排要求逐站传 `site`，而 `resolve_site_to_source` 在站点内
多源时只会用 `business_route_target` 定「默认业务库」（该函数**故意**排除观测源），于是
问 RUM 对象却路由到 mysql 库 → `object 'rum_event' 在本次生效数据源上未绑定到任何物理表`。
对象在哪个站走哪个源，部署绑定行与模型 `deploy_sites` 早就写明了（waker-datasource-domain
§2「站点内多源时用模型 deploy_sites 路由目标消歧」），拿它裁决是读已配事实，不是猜。

同时锁定第二条：绑定解析的对外告警（未登记为数据产品 / 表未在元数据目录登记 /
同名兜底）不得插值物理表名——历史形态直接外显 `arms-prd-en.logstore-rum`，
而其中一条旧英文文案因含 LEAK_KEYWORDS 被 `safe_warnings` 整条静默丢掉，
两种形态都是缺陷（一个泄露、一个丢诊断），物理细节只进服务端日志（护栏 §7）。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace  # noqa: I001

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.core import site_service  # noqa: E402

PRD_CN = 1780478236183        # 中国站 mysql 业务库
ARMS_CN = 1792408797510       # 中国站 ARMS RUM 观测源
PRD_SLS_CN = 1791617929234    # 中国站另一 SLS 源
ARMS_EN = 1793178243857       # 国际站 ARMS RUM 观测源


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):  # noqa: ARG002 — 单表单查询，无需按 SQL 分支
        return None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cursor(self._rows)

    def close(self):
        return None


def _patch_bindings(monkeypatch, rows):
    monkeypatch.setattr(site_service, "get_metadata_conn", lambda: _Conn(rows))


def _rum_rows():
    """rum_event 的 active 绑定：契约行登记在主源 arms-prd-cn（跨站由 deploy_sites 展开）。"""
    return [{"model_id": 7001, "object_key": "rum_event", "datasource_id": ARMS_CN}]


class TestObjectSiteTargets:
    def test_unique_hit_on_candidate_source(self, monkeypatch):
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "datasource_name_id_map", lambda: {})
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "")
        got = site_service.object_site_target_ids("rum_event", "cn", [PRD_CN, ARMS_CN, PRD_SLS_CN])
        assert got == {ARMS_CN}

    def test_object_key_case_and_separator_variant_still_hits(self, monkeypatch):
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "datasource_name_id_map", lambda: {})
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "")
        assert site_service.object_site_target_ids("RUM-Event", "cn", [ARMS_CN]) == {ARMS_CN}

    def test_deploy_sites_target_for_requested_site_is_included(self, monkeypatch):
        """跨站：请求国际站时，契约行虽登记在主源，模型 deploy_sites 声明本站走 arms-prd-en。"""
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "datasource_name_id_map",
                            lambda: {"arms-prd-cn": ARMS_CN, "arms-prd-en": ARMS_EN})
        monkeypatch.setattr(site_service, "site_of_model_target",
                            lambda mid, code: "arms-prd-en" if code == "int" else "arms-prd-cn")
        assert site_service.object_site_target_ids("rum_event", "int", [ARMS_EN, PRD_CN]) == {ARMS_EN}

    def test_ambiguous_or_unknown_returns_empty(self, monkeypatch):
        """零命中/对象未知/候选为空一律空集（交回调用方 fail-loud），不得在这里猜源。"""
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "datasource_name_id_map", lambda: {})
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "")
        assert site_service.object_site_target_ids("no_such_object", "cn", [ARMS_CN]) == set()
        assert site_service.object_site_target_ids("rum_event", "", [ARMS_CN]) == set()
        assert site_service.object_site_target_ids("rum_event", "cn", []) == set()

    def test_target_outside_candidates_is_not_returned(self, monkeypatch):
        """候选=授权集∩本站：站外/未授权源不得被塞进结果（越权风险）。"""
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "datasource_name_id_map",
                            lambda: {"arms-prd-en": ARMS_EN})
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "arms-prd-en")
        assert site_service.object_site_target_ids("rum_event", "int", [PRD_CN]) == set()


class TestResolveSiteWithObject:
    """站点多源时的消歧顺序：模型声明 > 对象部署事实 > 本站默认业务库 > fail-loud。"""

    def _cn_multi(self, monkeypatch):
        monkeypatch.setattr(site_service, "normalize_site_ref", lambda s: s)
        monkeypatch.setattr(site_service, "get_site",
                            lambda code: {"site_code": code, "display_name": "中国站点",
                                          "is_aggregate": 0})
        monkeypatch.setattr(site_service, "list_sites", lambda include_aggregate=True: [])
        monkeypatch.setattr(site_service, "datasources_of_site", lambda code, allowed: [
            {"id": PRD_CN, "name": "prd-cn", "db_type": "mysql", "is_default": 1},
            {"id": ARMS_CN, "name": "arms-prd-cn", "db_type": "sls", "is_default": 0},
            {"id": PRD_SLS_CN, "name": "prd-sls-cn", "db_type": "sls", "is_default": 0},
        ])
        monkeypatch.setattr(site_service, "site_of_model_target", lambda mid, code: "")

    def test_observation_object_routes_to_its_own_source(self, monkeypatch):
        self._cn_multi(monkeypatch)
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "datasource_name_id_map", lambda: {})
        got = site_service.resolve_site_to_source(
            "cn", [PRD_CN, ARMS_CN, PRD_SLS_CN], object_key="rum_event")
        assert got == ARMS_CN

    def test_without_object_behaviour_unchanged(self, monkeypatch):
        """未点名对象时保持旧口径（本站默认业务库），存量调用零影响。"""
        self._cn_multi(monkeypatch)
        _patch_bindings(monkeypatch, _rum_rows())
        monkeypatch.setattr(site_service, "business_route_target",
                            lambda code, ids: "prd-cn")
        got = site_service.resolve_site_to_source("cn", [PRD_CN, ARMS_CN, PRD_SLS_CN])
        assert got == PRD_CN

    def test_business_object_still_goes_to_business_library(self, monkeypatch):
        """业务对象的绑定行就在业务库上 → 按对象消歧与旧口径同解（不改变既有路由）。"""
        self._cn_multi(monkeypatch)
        _patch_bindings(monkeypatch, [{"model_id": 7002, "object_key": "case",
                                      "datasource_id": PRD_CN}])
        monkeypatch.setattr(site_service, "datasource_name_id_map", lambda: {})
        got = site_service.resolve_site_to_source(
            "cn", [PRD_CN, ARMS_CN, PRD_SLS_CN], object_key="case")
        assert got == PRD_CN

    def test_ambiguous_object_still_fails_loud_with_candidates(self, monkeypatch):
        """对象在同站多源上都有 active 绑定 → 不猜，报错列出业务名候选。"""
        self._cn_multi(monkeypatch)
        _patch_bindings(monkeypatch, [
            {"model_id": 7001, "object_key": "rum_event", "datasource_id": ARMS_CN},
            {"model_id": 7001, "object_key": "rum_event", "datasource_id": PRD_SLS_CN},
        ])
        monkeypatch.setattr(site_service, "datasource_name_id_map", lambda: {})
        monkeypatch.setattr(site_service, "business_route_target", lambda code, ids: "")
        with pytest.raises(site_service.SiteError) as e:
            site_service.resolve_site_to_source(
                "cn", [PRD_CN, ARMS_CN, PRD_SLS_CN], object_key="rum_event")
        msg = str(e.value)
        assert "prd-cn" in msg and "arms-prd-cn" in msg
        # 护栏 §7：错误文案只含业务名，不得出现内部 id
        assert str(ARMS_CN) not in msg


class TestToolWiring:
    """`run_semantic_query`/`execute_sql` 的 site 解析必须把 intent 的 object 传下去。"""

    def test_resolve_datasource_arg_passes_object(self, monkeypatch):
        from backend.modules.mind.execution.sdk_tools import query_tools
        seen = {}

        def _fake(site, allowed, model_id=0, object_key=""):
            seen["site"], seen["object_key"] = site, object_key
            return ARMS_CN
        monkeypatch.setattr(site_service, "resolve_site_to_source", _fake)
        ctx = SimpleNamespace(model_id=0, datasource_id=0,
                              extra={"available_datasource_ids": [PRD_CN, ARMS_CN]})
        got = query_tools._resolve_datasource_arg(
            {"site": "cn", "object": "rum_event"}, ctx)
        assert got == ARMS_CN and seen == {"site": "cn", "object_key": "rum_event"}

    def test_sql_tools_without_object_unchanged(self, monkeypatch):
        """execute_sql/check_sql 没有 object 字段 → 传空串，路由行为与改造前一致。"""
        from backend.modules.mind.execution.sdk_tools import query_tools
        seen = {}

        def _fake(site, allowed, model_id=0, object_key=""):
            seen["object_key"] = object_key
            return PRD_CN
        monkeypatch.setattr(site_service, "resolve_site_to_source", _fake)
        ctx = SimpleNamespace(model_id=0, datasource_id=0,
                              extra={"available_datasource_ids": [PRD_CN]})
        assert query_tools._resolve_datasource_arg({"site": "cn"}, ctx) == PRD_CN
        assert seen["object_key"] == ""


class TestBindingWarningsNoPhysicalLeak:
    """绑定解析的对外告警不得含物理表名（护栏 §7），但仍要能定位问题。"""

    def _binding(self):
        from backend.semantics.models import ResolvedBinding
        return ResolvedBinding(object_key="rum_event", datasource_id=ARMS_CN,
                               datasource_name="arms-prd-cn", physical_table="logstore-rum")

    def test_unregistered_product_warning_has_business_names_only(self):
        from backend.semantics import binding_resolver

        class _Cur:
            def execute(self, sql, params=None):
                return None

            def fetchone(self):
                return None

        class _C:
            def cursor(self):
                from contextlib import contextmanager

                @contextmanager
                def _cm():
                    yield _Cur()
                return _cm()

        b, warnings = self._binding(), []
        binding_resolver._enrich_from_data_product(_C(), b, warnings)
        assert len(warnings) == 1
        assert "未登记为数据产品" in warnings[0]
        assert "arms-prd-cn" in warnings[0] and "rum_event" in warnings[0]
        assert "logstore-rum" not in warnings[0]

    def test_table_fallback_warning_source_has_no_physical_table(self):
        """源码级护栏：绑定解析的对外告警不得把物理表名/三段式引用**插值**进去。

        这类告警会随 `run_semantic_query` 返回体进 LLM/客户端（护栏 §7）；历史形态是
        `表 'arms-prd-en.logstore-rum' 未登记为数据产品`。只禁止取值插值，文案里提
        “physical_table”这类字段名不算泄露（旧写法会被 safe_warnings 整条静默丢掉，
        所以本批改成业务名文案，而不是依赖过滤丢消息）。
        """
        import inspect
        import re

        from backend.semantics import binding_resolver
        src = inspect.getsource(binding_resolver)
        offenders = [m.group(0) for m in re.finditer(r"\{[^{}]*physical_table[^{}]*\}", src)]
        assert offenders == [], f"对外文案插了物理表名: {offenders}"
