"""本体访问与用途归属裁决回归（合并后用户裁决落地）。

裁决：源本体已退役（kind='source' 合并为 'business'），只保留 system/business。
      业务本体（含原 source 迁移而来）按模型触达数据源（主源∪deploy_sites 路由源）
      与授权集求交收口（按 id 粒度，杜绝同站跨源泄漏）；系统本体全员可见、与业务本体共用知识库。

锁住的行为：
1. 语义取数真源显式化：resolve JOIN models 按域裁决（source 已退役），
   业务域取数认非 system 模型、系统域认 system 模型（不靠 id ASC 意外命中）；
2. 可见性：business 按触达数据源授权过滤（空集=不可见，fail-closed），抽象业务本体（无主源
   无 deploy_sites）与 system 本体全员可见；
3. 知识库路由：business/system → 全局 sync_ontology 库（源本体退役，不再有“不做 RAG”分支）；
4. enforcer 数据源步 fail-closed：空授权=拒绝（历史缺陷：`if allowed_ds and` 放行）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import ontology_kb_sync, ontology_service  # noqa: E402


# ── 语义取数真源（按 kind 裁决）─────────────────────────────

class TestSemanticBindingSourceOfTruth:
    def test_bindings_query_filters_by_kind(self):
        """取数解析必须 JOIN models 按**域**裁决（v0.4：source kind 已退役）。"""
        import inspect
        from backend.semantics import binding_resolver as br
        src = inspect.getsource(br._resolve_from_bindings)
        assert "JOIN adh_ontology_models" in src
        # 域边界：system 与非 system 分判；不再按已退役的 'source' kind 取数
        assert "m.kind = 'system'" in src and "COALESCE(m.kind,'') <> 'system'" in src
        assert "m.kind = 'source'" not in src

    def test_objects_inline_same_scope(self):
        import inspect
        from backend.semantics import binding_resolver as br
        src = inspect.getsource(br._resolve_from_objects_inline)
        assert "JOIN adh_ontology_models" in src
        assert "COALESCE(m.kind,'') <> 'system'" in src


# ── 可见性：源本体按数据源授权，业务/系统全员 ────────────────

class TestModelVisibility:
    def _model(self, kind, ds_id):
        return {"id": 1, "kind": kind, "datasource_id": ds_id, "name": "m"}

    def _scope(self, monkeypatch, ids):
        """mock 模型触达数据源解析（避免真 DB），使 can_view_model 纯逻辑可测。"""
        monkeypatch.setattr("backend.core.site_service.model_source_scope_ids",
                            lambda mid, ds_id=0, name_id_map=None: set(ids))

    def test_allowed_source_ids_accepts_get_current_user_dict(self, monkeypatch):
        """API 层回归：get_current_user 返回 dict（非对象），不得按属性取值。

        历史缺陷：`user.id` 对 dict 取属性 → AttributeError → GET /models 500，
        前端模型列表整页空白（"本体怎么都没了"）。
        """
        from backend.modules.catalog.api import ontology as ontology_api
        calls = []

        class _FakeRoleService:
            @staticmethod
            def get_user_allowed_datasources(uid, ws):
                calls.append((uid, ws))
                return [7, 8]

        monkeypatch.setattr("backend.core.role_service.role_service",
                            _FakeRoleService)
        allowed = ontology_api._allowed_source_ids(
            {"user_id": 42, "username": "u", "role": "admin"})
        assert allowed == {7, 8}
        assert calls == [(42, 0)]

    def test_business_model_requires_datasource_grant(self, monkeypatch):
        """合并后 business 本体（含原 source）按触达源与授权集求交：命中可见，不命中拒绝。"""
        self._scope(monkeypatch, [7])
        assert ontology_service.can_view_model(self._model("business", 7), {7}) is True
        assert ontology_service.can_view_model(self._model("business", 7), {8}) is False

    def test_empty_grant_is_fail_closed(self, monkeypatch):
        """空授权集=具体业务本体全不可见（不得当全量）。"""
        self._scope(monkeypatch, [7])
        assert ontology_service.can_view_model(self._model("business", 7), set()) is False

    def test_abstract_business_and_system_visible_to_all(self, monkeypatch):
        """无具体数据源归属的抽象业务本体（scope 空）与 system 本体全员可见。"""
        self._scope(monkeypatch, [])
        assert ontology_service.can_view_model(self._model("business", 0), set()) is True
        assert ontology_service.can_view_model(self._model("system", 0), set()) is True

    def test_none_means_internal_unrestricted(self):
        assert ontology_service.can_view_model(self._model("business", 7), None) is True


# ── 知识库路由：business/system 共用全局库（源本体退役）───────

class TestKbRouting:
    def test_business_and_system_share_global_kb(self, monkeypatch):
        monkeypatch.setattr(ontology_kb_sync, "sync_targets",
                            lambda: [{"id": 5, "name": "共享知识库", "notebook_id": "nb"}])
        for kind in ("business", "system"):
            t = ontology_kb_sync.sync_targets_for_model({"kind": kind, "datasource_id": 0})
            assert t and t[0]["id"] == 5, f"{kind} 应路由到全局共用库"

    def test_business_model_with_ds_syncs_to_global_kb(self, monkeypatch):
        """源本体退役：带 ds 的模型（原 source，迁移后 kind=business）路由到全局库进 RAG。"""
        monkeypatch.setattr(ontology_kb_sync, "sync_targets",
                            lambda: [{"id": 5, "name": "共享知识库", "notebook_id": "nb"}])
        t = ontology_kb_sync.sync_targets_for_model({"kind": "business", "datasource_id": 9})
        assert t and t[0]["id"] == 5

    def test_legacy_empty_kind_with_ds_treated_as_business(self, monkeypatch):
        """兼容存量空 kind 行：ds>0 视为业务本体（源本体退役），路由到全局库。"""
        monkeypatch.setattr(ontology_kb_sync, "sync_targets",
                            lambda: [{"id": 5, "name": "共享知识库", "notebook_id": "nb"}])
        t = ontology_kb_sync.sync_targets_for_model({"kind": "", "datasource_id": 9})
        assert t and t[0]["id"] == 5

    def test_model_kb_selection_retired(self):
        """per-model 选库入口退役：显式拒绝，不静默忽略。"""
        with pytest.raises(ValueError, match="知识库归属已统一"):
            ontology_service.set_model_kb(1, 4)


# ── enforcer 数据源步 fail-closed（源码级锁）────────────────

class TestEnforcerFailClosed:
    def test_datasource_step_never_skips_on_empty_grant(self):
        """历史缺陷回归：`if allowed_ds and ...` 空授权跳过检查（fail-open）。"""
        import inspect
        from backend.core import enforcer
        src = inspect.getsource(enforcer.PermissionEnforcer)
        # 只看代码行（历史缺陷描述在注释里，不算）
        code_lines = [ln for ln in src.splitlines()
                      if not ln.lstrip().startswith("#") and "曾写" not in ln]
        code = "\n".join(code_lines)
        assert "if allowed_ds and" not in code, \
            "空授权不得跳过数据源检查（fail-closed：空=无权）"
        assert code.count("not in allowed_ds") >= 2  # check_access + enforce_sql 两处

    def test_role_service_doc_is_fail_closed(self):
        import inspect
        from backend.core.role_service import role_service
        doc = inspect.getdoc(role_service.get_user_allowed_datasources)
        assert "fail-closed" in doc and "no restriction" not in doc.lower()
