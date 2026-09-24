"""T6 别名级联校验与回流队列单测(离线, 无 DB)。

覆盖: AS-BOT 参数 schema 校验(提议即校验)、unresolved_terms 落入 provenance、
孤儿字典引用的检测与 activate 硬阻断语义。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.datamind.execution.wakers import validate_action_payload, AS_BOT_WRITE_ACTIONS
from services.shared.semantics.planner import _ColumnResolver, plan
from services.shared.semantics.models import ResolvedBinding, SemanticQuery
from services.datacatalog.services import ontology_service


# ── AS-BOT 参数 schema ───────────────────────────────────

class TestActionPayloadSchema:
    def test_new_actions_registered(self):
        assert "alias.approve" in AS_BOT_WRITE_ACTIONS
        assert "alias.reject" in AS_BOT_WRITE_ACTIONS

    def test_missing_required_rejected(self):
        ok, err = validate_action_payload("alias.approve", {"target_type": "metric"})
        assert not ok and "term" in err

    def test_bad_enum_rejected(self):
        ok, err = validate_action_payload(
            "alias.approve", {"target_type": "table", "term": "x"})
        assert not ok and "target_type" in err

    def test_bad_int_rejected(self):
        ok, err = validate_action_payload(
            "alias.reject", {"suggestion_id": "abc"})
        assert not ok

    def test_valid_payload_passes(self):
        ok, err = validate_action_payload(
            "alias.approve", {"target_type": "dimension", "term": "就诊量",
                              "suggestion_id": 7})
        assert ok and err == ""

    def test_existing_actions_validated(self):
        ok, _ = validate_action_payload("ontology.save", {"model_id": 1})
        assert not ok  # 缺 json_content
        ok, _ = validate_action_payload("ontology.activate", {"model_id": 1})
        assert ok


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

    def test_no_tables_skips_query(self, monkeypatch):
        doc = {"objects": [{"key": "case", "aliases": []}]}  # 无 primary_table
        called = []
        monkeypatch.setattr(ontology_service, "get_metadata_conn",
                            lambda: called.append(1) or _FakeConn([]))
        assert ontology_service.find_orphan_dict_bindings(doc) == []
        assert not called  # 表集合为空时不应连库
