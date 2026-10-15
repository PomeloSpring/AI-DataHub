"""T6 进化候选与级联校验单测（离线，无 DB）。

覆盖：候选分类的可解释性与歧义不自动合并、别名回写参数校验（不再走审批回路）、
unresolved_terms 落入 provenance、孤儿字典引用的检测与 activate 硬阻断语义。

历史说明：本文件原先指向 `mind.rag.alias_suggestion`。该模块已被
`catalog.services.evolution_service` 取代（旧表退役为冻结、单一事实源收口），
测试跟着迁移而不是留着指向死代码——否则"测试全绿"会掩住"被测物已不存在"。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.catalog.services import evolution_service as evo
from backend.semantics.planner import _ColumnResolver, plan
from backend.semantics.models import ResolvedBinding, SemanticQuery
from backend.modules.catalog.services import ontology_service


# ── 候选分类：可解释、歧义不自动合并、不可误诊 ───────────

class TestClassifyTerm:
    def test_authoritative_name_hit_is_binding_gap_not_alias(self, monkeypatch):
        """命中权威名却解析不通 = 绑定不可达，不是缺别名。

        实测依据：旧队列 9 条里 7 条属此类。归成 alias 会引人去补一个
        没用的别名，归成 new_* 会造出重复字典行——两个都是错行动。
        """
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [
            {"kind": "metric", "ref": "activated_devices", "object_key": "equipment",
             "target_table": "t_product_equipment", "via": "name"}])
        out = evo.classify_term("activated_devices", hint_type="metric")
        assert out["kind"] == "binding_gap"
        assert out["target_ref"] == "activated_devices"
        assert "绑定" in out["classify_reason"] or "可达" in out["classify_reason"]

    def test_alias_registration_hit_is_adoptable(self, monkeypatch):
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [
            {"kind": "metric", "ref": "案例数量", "object_key": "case",
             "target_table": "t_case_records", "via": "aliases"}])
        out = evo.classify_term("病例数")
        assert out["kind"] == "alias" and out["target_ref"] == "案例数量"

    def test_multiple_hits_are_not_auto_merged(self, monkeypatch):
        """歧义词只能给人选，自动合并会污染别名（absorb_business_terms 同纪律）。"""
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [
            {"kind": "metric", "ref": "营收", "object_key": "order", "target_table": "a", "via": "name"},
            {"kind": "dimension", "ref": "营收口径", "object_key": "case", "target_table": "b", "via": "name"}])
        out = evo.classify_term("营收")
        assert out["kind"] == "alias" and out["target_ref"] == "" and out["ambiguous"] is True
        assert "不自动合并" in out["classify_reason"]

    def test_fuzzy_candidates_route_to_adopt_not_new_row(self, monkeypatch):
        """planner 回了近似候选 = 概念很可能已存在；先走收编，不新建重复行。"""
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [])
        out = evo.classify_term("状态分布", hint_type="dimension",
                               candidates=["案例状态", "拆牙状态"])
        assert out["kind"] == "alias" and out["target_ref"] == "案例状态"
        assert out["ambiguous"] is True

    def test_structured_type_beats_shape_guess(self, monkeypatch):
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [])
        assert evo.classify_term("online_devices", hint_type="metric")["kind"] == "new_metric"
        assert evo.classify_term("企业省份类型", hint_type="dimension")["kind"] == "new_dimension"

    def test_no_signal_goes_to_term_buffer_not_guessed(self, monkeypatch):
        """什么信号都看不出来就落缓冲区，不猜建什么——猜错会往字典里插垃圾行。"""
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [])
        out = evo.classify_term("xk9q")
        assert out["kind"] == "term" and "不猜" in out["classify_reason"]

    def test_feedback_reason_dispatches_kind(self, monkeypatch):
        monkeypatch.setattr(evo, "find_authoritative", lambda t, ds="": [])
        got = {r: evo.classify_term("某词" + str(i), origin="feedback",
                                   feedback_reason=r)["kind"]
               for i, r in enumerate(evo.FEEDBACK_REASONS)}
        assert got["missing_metric"] == "new_metric"
        assert got["missing_dimension"] == "new_dimension"


# ── 候选入库与出口参数校验（fail-loud，不静默丢参数）──────

class TestCandidateValidation:
    def test_missing_classify_reason_rejected(self, monkeypatch):
        """没分类依据的候选 = 不可审计的黑盒分类，宁可报错也不入库。"""
        monkeypatch.setattr(evo, "_q", lambda *a, **k: [])
        monkeypatch.setattr(evo, "_w", lambda *a, **k: 1)
        with pytest.raises(ValueError, match="classify_reason"):
            evo.upsert_candidate(term="DAU", kind="new_metric", source="parse_fail",
                                classify_reason="")

    def test_unknown_kind_and_source_rejected(self):
        with pytest.raises(ValueError, match="kind"):
            evo.upsert_candidate(term="x", kind="new_index", source="parse_fail",
                                classify_reason="r")
        with pytest.raises(ValueError, match="source"):
            evo.upsert_candidate(term="x", kind="alias", source="guess",
                                classify_reason="r")

    def test_blank_term_rejected(self):
        with pytest.raises(ValueError, match="业务词"):
            evo.upsert_candidate(term="   ", kind="alias", source="parse_fail",
                                classify_reason="r")

    def test_bad_target_type_rejected(self):
        with pytest.raises(ValueError, match="target_type"):
            evo.adopt_alias(term="x", target_type="table", target_ref="m")

    def test_missing_dict_row_returns_next_action_instead_of_raise(self, monkeypatch):
        """旧实现在这里 raise，把 8/9 条 pending 卡死。

        目标字典行不存在不是错误，是"该走另一个出口"：必须回可操作的
        next_action，而不是抛异常让审核按钮永久点不动。
        """
        monkeypatch.setattr(evo, "_q", lambda sql, params=(): [] if "aliases" in sql else [])
        out = evo.adopt_alias(term="DAU", target_type="metric", target_ref="不存在的指标")
        assert out["success"] is False
        assert out["next_action"] == "new_metric"
        assert "不存在" in out["message"]


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
