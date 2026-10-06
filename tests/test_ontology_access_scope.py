"""本体访问与用途归属裁决回归（用户裁决落地）。

裁决：业务本体=全员共享、与 AS-BOT 系统本体**共用知识库**（RAG 检索）；
      源本体=按用户数据源权限分配、只服务**语义检索/取数**（不做 RAG）。

锁住的行为：
1. 语义取数真源显式化：resolve 只认 source/system 模型绑定（按查询域），
   业务本体绑定永不参与取数（历史缺陷：双绑定靠 id ASC 意外命中）；
2. 可见性：源本体按数据源授权过滤（空集=无可见源本体，fail-closed），
   业务/系统本体全员可见；
3. 知识库路由：business/system → 全局 sync_ontology 库；source → 不做 RAG
   （显式标注 source_model_semantic_only，不伪装成配置遗漏）；
4. enforcer 数据源步 fail-closed：空授权=拒绝（历史缺陷：`if allowed_ds and` 放行）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datacatalog.services import ontology_kb_sync, ontology_service  # noqa: E402


# ── 语义取数真源（按 kind 裁决）─────────────────────────────

class TestSemanticBindingSourceOfTruth:
    def test_bindings_query_filters_by_kind(self):
        """Level 1/2 档位 SQL 必须 JOIN models 按 kind 裁决，business 不参与取数。"""
        import inspect
        from services.shared.semantics import binding_resolver as br
        src = inspect.getsource(br._resolve_from_bindings)
        assert "JOIN adh_ontology_models" in src
        assert "m.kind = 'source'" in src and "m.kind = 'system'" in src
        # business 不得进入取数解析（其绑定只承载治理血缘）
        assert "m.kind = 'business'" not in src.replace("kind = 'business' 的绑定", "")

    def test_objects_inline_same_scope(self):
        import inspect
        from services.shared.semantics import binding_resolver as br
        src = inspect.getsource(br._resolve_from_objects_inline)
        assert "JOIN adh_ontology_models" in src and "m.kind = 'source'" in src


# ── 可见性：源本体按数据源授权，业务/系统全员 ────────────────

class TestModelVisibility:
    def _model(self, kind, ds_id):
        return {"id": 1, "kind": kind, "datasource_id": ds_id, "name": "m"}

    def test_allowed_source_ids_accepts_get_current_user_dict(self, monkeypatch):
        """API 层回归：get_current_user 返回 dict（非对象），不得按属性取值。

        历史缺陷：`user.id` 对 dict 取属性 → AttributeError → GET /models 500，
        前端模型列表整页空白（"本体怎么都没了"）。
        """
        from services.datacatalog.api import ontology as ontology_api
        calls = []

        class _FakeRoleService:
            @staticmethod
            def get_user_allowed_datasources(uid, ws):
                calls.append((uid, ws))
                return [7, 8]

        monkeypatch.setattr("services.authservice.services.role_service.role_service",
                            _FakeRoleService)
        allowed = ontology_api._allowed_source_ids(
            {"user_id": 42, "username": "u", "role": "admin"})
        assert allowed == {7, 8}
        assert calls == [(42, 0)]

    def test_source_model_requires_datasource_grant(self):
        assert ontology_service.can_view_model(self._model("source", 7), {7}) is True
        assert ontology_service.can_view_model(self._model("source", 7), {8}) is False

    def test_empty_grant_is_fail_closed(self):
        """空授权集=源本体全不可见（不得当全量）。"""
        assert ontology_service.can_view_model(self._model("source", 7), set()) is False

    def test_business_and_system_visible_to_all(self):
        assert ontology_service.can_view_model(self._model("business", 0), set()) is True
        assert ontology_service.can_view_model(self._model("system", 0), set()) is True

    def test_none_means_internal_unrestricted(self):
        assert ontology_service.can_view_model(self._model("source", 7), None) is True


# ── 知识库路由：business/system 共用，source 不做 RAG ────────

class TestKbRouting:
    def test_business_and_system_share_global_kb(self, monkeypatch):
        monkeypatch.setattr(ontology_kb_sync, "sync_targets",
                            lambda: [{"id": 5, "name": "共享知识库", "notebook_id": "nb"}])
        for kind in ("business", "system"):
            t = ontology_kb_sync.sync_targets_for_model({"kind": kind, "datasource_id": 0})
            assert t and t[0]["id"] == 5, f"{kind} 应路由到全局共用库"

    def test_source_model_does_no_rag(self):
        """源本体不做 RAG 是裁决（只服务语义检索），返回空目标。"""
        t = ontology_kb_sync.sync_targets_for_model({"kind": "source", "datasource_id": 9})
        assert t == []

    def test_source_skip_reason_is_decision_not_misconfig(self, monkeypatch):
        """源本体不同步要标注裁决原因，不伪装成 no_kb_bound 配置遗漏。"""
        from services.datacatalog.services.ontology_service import get_model
        monkeypatch.setattr(ontology_service, "get_model",
                            lambda mid: {"id": mid, "kind": "source", "datasource_id": 9,
                                         "name": "s", "status": "active"})
        r = ontology_kb_sync.sync_model_to_qmind(1)
        assert r["skipped"] == "source_model_semantic_only"

    def test_model_kb_selection_retired(self):
        """per-model 选库入口退役：显式拒绝，不静默忽略。"""
        with pytest.raises(ValueError, match="知识库归属已统一"):
            ontology_service.set_model_kb(1, 4)


# ── enforcer 数据源步 fail-closed（源码级锁）────────────────

class TestEnforcerFailClosed:
    def test_datasource_step_never_skips_on_empty_grant(self):
        """历史缺陷回归：`if allowed_ds and ...` 空授权跳过检查（fail-open）。"""
        import inspect
        from services.datamind.permission import enforcer
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
        from services.authservice.services.role_service import role_service
        doc = inspect.getdoc(role_service.get_user_allowed_datasources)
        assert "fail-closed" in doc and "no restriction" not in doc.lower()
