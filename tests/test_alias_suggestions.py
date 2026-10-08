"""T6 别名级联校验与回流队列单测(离线, 无 DB)。

覆盖: 别名回写直执行的参数校验（不再走审批回路）、unresolved_terms 落入 provenance、
孤儿字典引用的检测与 activate 硬阻断语义。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.mind.rag import alias_suggestion
from backend.semantics.planner import _ColumnResolver, plan
from backend.semantics.models import ResolvedBinding, SemanticQuery
from backend.modules.catalog.services import ontology_service


# ── 别名回写直执行：参数校验（fail-loud，不静默丢参数） ─────────

class TestAliasDecisionValidation:
    def test_missing_term_rejected(self, monkeypatch):
        monkeypatch.setattr(alias_suggestion, "get_suggestion",
                            lambda sid: {"id": sid, "term": "", "target_type": "metric", "target_ref": "m"})
        with pytest.raises(ValueError, match="term"):
            alias_suggestion.approve_suggestion({"suggestion_id": 7})

    def test_bad_target_type_rejected(self, monkeypatch):
        monkeypatch.setattr(alias_suggestion, "get_suggestion",
                            lambda sid: {"id": sid, "term": "x", "target_type": "table", "target_ref": "m"})
        with pytest.raises(ValueError, match="target_type"):
            alias_suggestion.approve_suggestion({"suggestion_id": 7})

    def test_dict_alias_requires_target_ref(self, monkeypatch):
        monkeypatch.setattr(alias_suggestion, "get_suggestion",
                            lambda sid: {"id": sid, "term": "x", "target_type": "metric", "target_ref": ""})
        with pytest.raises(ValueError, match="target_ref"):
            alias_suggestion.approve_suggestion({"suggestion_id": 7})

    def test_reject_requires_suggestion_id(self):
        with pytest.raises(ValueError, match="suggestion_id"):
            alias_suggestion.reject_suggestion({})

    def test_valid_payload_passes(self, monkeypatch):
        monkeypatch.setattr(alias_suggestion, "get_suggestion",
                            lambda sid: {"id": sid, "term": "就诊量", "target_type": "dimension",
                                         "target_ref": "量", "datasource_id": 1})
        monkeypatch.setattr(alias_suggestion, "_approve_dict_alias", lambda *a, **k: None)
        monkeypatch.setattr(alias_suggestion, "_set_status", lambda *a, **k: None)
        out = alias_suggestion.approve_suggestion({"suggestion_id": 7, "term": "就诊量",
                                                  "target_type": "dimension", "target_ref": "量"})
        assert out["success"] is True and out["term"] == "就诊量"


# ── planner unresolved_terms 结构化产出 ──────────────────

PHYS = {"create_time": {"data_type": "datetime", "business_desc": "创建时间"},
        "case_status": {"data_type": "int", "business_desc": "案例状态"}}
DIMS = {"案例状态": {"name": "案例状态", "name_en": "case_status",
                   "target_column": "case_status", "category": "状态",
                   "aliases": [], "value_labels": {}}}
METRICS = {"案例数量": {"name": "案例数量", "name_en": "case_count", "formula": "COUNT(*)",
                     "formula_dsl": None, "agg_type": "COUNT", "default_agg": "COUNT",
                     "unit": "", "description": "", "aliases": [], "formula_dialects": None}}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(_ColumnResolver, "_load_phys", lambda self: dict(PHYS))
    monkeypatch.setattr(_ColumnResolver, "_load_dims", lambda self: dict(DIMS))
    monkeypatch.setattr(_ColumnResolver, "_load_metrics", lambda self: dict(METRICS))


@pytest.fixture
def binding():
    return ResolvedBinding(object_key="case", datasource_id=1,
                           physical_table="t_case_records", db_name="stardb")


class TestUnresolvedTerms:
    def test_fuzzy_rejected_collected(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["案例数量"], dimensions=["案例状"], limit=10)
        p = plan(q, binding)
        terms = p.provenance["unresolved_terms"]
        assert any(t["term"] == "案例状" and t["reason"] == "fuzzy_rejected"
                   and "案例状态" in (t["candidates"] or []) for t in terms)

    def test_unresolved_metric_collected(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["利润率"], limit=10)
        p = plan(q, binding)
        terms = p.provenance["unresolved_terms"]
        assert any(t["term"] == "利润率" and t["type"] == "metric" for t in terms)

    def test_clean_query_has_no_terms(self, patched, binding):
        q = SemanticQuery(object="case", metrics=["案例数量"], dimensions=["案例状态"], limit=10)
        p = plan(q, binding)
        assert p.provenance["unresolved_terms"] == []


# ── 孤儿字典引用检测 ─────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows_by_call):
        self.rows_by_call = rows_by_call
        self.i = -1
        self.rows = []

    def execute(self, sql, params=None):
        self.i += 1
        self.rows = self.rows_by_call[self.i] if self.i < len(self.rows_by_call) else []

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows_by_call):
        self.rows_by_call = rows_by_call

    def cursor(self):
        return _FakeCursor(self.rows_by_call)

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestOrphanBindings:
    def test_orphan_detected(self, monkeypatch):
        doc = {"objects": [{"key": "case", "aliases": [], "primary_table": "t_case"}]}
        # metrics 查询返回一个指向不存在对象 ticket 的行; dimensions 返回干净行
        rows = [
            [{"name": "工单量", "bound_object_key": "ticket"}],  # adh_metrics
            [{"name": "创建日期", "bound_object_key": "case"}],  # adh_dimensions
        ]
        monkeypatch.setattr(ontology_service, "get_metadata_conn",
                            lambda: _FakeConn(rows))
        orphans = ontology_service.find_orphan_dict_bindings(doc)
        assert any("工单量" in o and "ticket" in o for o in orphans)
        assert not any("创建日期" in o for o in orphans)

    def test_case_insensitive_match(self, monkeypatch):
        doc = {"objects": [{"key": "Case", "aliases": ["CASES"], "primary_table": "t"}]}
        rows = [
            [{"name": "m1", "bound_object_key": "case"}],   # 小写指向大写 key → 命中
            [{"name": "d1", "bound_object_key": "cases"}],  # 指向别名(大小写不同) → 命中
        ]
        monkeypatch.setattr(ontology_service, "get_metadata_conn",
                            lambda: _FakeConn(rows))
        assert ontology_service.find_orphan_dict_bindings(doc) == []

    def test_no_tables_uses_global_scope(self, monkeypatch):
        """业务本体对象无 primary_table → 孤儿检查走全局口径，不得跳过。

        历史语义是"无表跳过查询"；业务本体路由化剥离 primary_table 后，若仍跳过，
        字典孤儿检查对业务本体整体失效（缺口）。现按 bound_object_key 全局对比
        （含其它 active 模型对象 key），悬空必须暴露（§4 宁阻断勿悬空）。
        """
        import json as _json
        doc = {"objects": [{"key": "case", "aliases": []}]}  # 无 primary_table
        rows_by_call = [
            [{"json_content": _json.dumps({"objects": [{"key": "other"}]})}],  # 其它 active 模型
            [{"name": "幽灵指标", "bound_object_key": "ghost"}],               # metrics 悬空
            [{"name": "创建日期", "bound_object_key": "case"}],               # dimensions 命中
        ]
        monkeypatch.setattr(ontology_service, "get_metadata_conn",
                            lambda: _FakeConn(rows_by_call))
        orphans = ontology_service.find_orphan_dict_bindings(doc)
        assert any("幽灵指标" in o and "ghost" in o for o in orphans)
        assert not any("创建日期" in o for o in orphans)
