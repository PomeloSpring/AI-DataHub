"""本体对象 key 重复检测与合并（历史缺陷根因回归）。

背景：canonical JSON 里出现过 7 对重复对象（`case_file` vs `casefile` 这类
snake_case / 全小写变体），空壳那侧 0 属性却携带独有 metrics/links。
根因是 generate_draft 与 palantir_to_canonical 的去重都只按精确字符串判 key。

锁住的行为：
1. 判重走 object_identity_key（忽略大小写与分隔符），命名走 normalize_object_key；
2. 合并必须**无损**：独有指标、关系描述不得丢（no-silent-degradation）；
3. 同名不同主表是真冲突，必须拒绝而不是静默二选一；
4. activate 遇到重复 key 硬阻断。

均为纯函数 / monkeypatch 测试，不触碰数据库。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datacatalog.services import ontology_service, ontology_yaml_import


# ── 判重与命名 ────────────────────────────────────────────────────

class TestObjectIdentity:
    def test_identity_ignores_case_and_separators(self):
        """`case_file` / `casefile` / `CaseFile` 必须归到同一身份。"""
        variants = ["case_file", "casefile", "CaseFile", "CASE_FILE", "case-file", "case file"]
        ids = {ontology_service.object_identity_key(v) for v in variants}
        assert ids == {"casefile"}

    def test_identity_distinguishes_different_objects(self):
        assert (ontology_service.object_identity_key("case_file")
                != ontology_service.object_identity_key("case_treatment"))

    def test_identity_handles_empty(self):
        assert ontology_service.object_identity_key("") == ""
        assert ontology_service.object_identity_key(None) == ""

    def test_normalize_to_snake_case(self):
        assert ontology_service.normalize_object_key("CaseFile") == "case_file"
        assert ontology_service.normalize_object_key("WorkOrder") == "work_order"
        assert ontology_service.normalize_object_key("case-file") == "case_file"
        assert ontology_service.normalize_object_key("  case  file ") == "case_file"

    def test_normalize_keeps_acronym_prefix(self):
        """AIConversation → ai_conversation，不能被切成 a_iconversation。"""
        assert ontology_service.normalize_object_key("AIConversation") == "ai_conversation"
        assert ontology_service.normalize_object_key("DCCaseRecord") == "dc_case_record"

    def test_normalize_strips_fullwidth(self):
        assert ontology_service.normalize_object_key("ｃａｓｅ＿ｆｉｌｅ") == "case_file"


# ── 重复检测 ──────────────────────────────────────────────────────

class TestDuplicateDetection:
    def _doc(self, *keys):
        return {"objects": [{"key": k, "primary_table": f"t_{k}"} for k in keys]}

    def test_clean_doc_has_no_duplicates(self):
        assert ontology_service.find_duplicate_object_keys(
            self._doc("case_file", "case_treatment", "user")) == []

    def test_detects_separator_variants(self):
        dups = ontology_service.find_duplicate_object_keys(self._doc("case_file", "casefile"))
        assert len(dups) == 1
        assert sorted(dups[0]["keys"]) == ["case_file", "casefile"]
        assert dups[0]["identity"] == "casefile"

    def test_reports_primary_tables_for_conflict_judgement(self):
        doc = {"objects": [
            {"key": "case_file", "primary_table": "t_case_files"},
            {"key": "casefile", "primary_table": "t_case_files"},
        ]}
        dups = ontology_service.find_duplicate_object_keys(doc)
        assert dups[0]["primary_tables"] == ["t_case_files"]


# ── 合并无损（关键回归：独有内容不能丢）──────────────────────────

class TestMergeObjectPair:
    def test_keeps_richer_as_base(self):
        rich = {"key": "case_file", "primary_table": "t_case_files",
                "properties": [{"column": "a", "name": "甲"}],
                "links": [], "metrics": [], "aliases": ["CaseFile"], "description": "案例文件"}
        shell = {"key": "casefile", "primary_table": "t_case_files",
                 "properties": [], "links": [], "metrics": [],
                 "aliases": ["CaseFile", "案例文件"], "description": ""}
        merged, detail = ontology_service.merge_object_pair(rich, shell)
        assert merged["key"] == "case_file"
        assert len(merged["properties"]) == 1
        assert detail["dropped"] == "casefile"

    def test_gains_unique_metrics_from_shell(self):
        """回归：空壳侧的独有指标必须并入，不得随对象一起丢。"""
        rich = {"key": "work_order", "primary_table": "t_product_work_order",
                "properties": [{"column": "a", "name": "甲"}], "links": [],
                "metrics": [{"name": "resolution_hours", "formula": "x", "description": ""}],
                "aliases": []}
        shell = {"key": "workorder", "primary_table": "t_product_work_order",
                 "properties": [], "links": [],
                 "metrics": [{"name": "pending_work_orders", "formula": "y",
                              "description": "待处理工单数"}],
                 "aliases": []}
        merged, detail = ontology_service.merge_object_pair(rich, shell)
        names = [m["name"] for m in merged["metrics"]]
        assert "pending_work_orders" in names
        assert "resolution_hours" in names
        assert detail["gained_metrics"] == ["pending_work_orders"]

    def test_preserves_link_description_on_same_signature(self):
        """回归：同 (target, join) 的关系去重时，非空描述必须胜出。"""
        rich = {"key": "dc_case_record", "primary_table": "t_order_record",
                "properties": [{"column": "a", "name": "甲"}],
                "links": [{"target": "case", "type": "source_case",
                           "join": "DCCaseRecord.case_code = Case.case_code",
                           "cardinality": "N:1", "description": ""}],
                "metrics": [], "aliases": []}
        shell = {"key": "dccaserecord", "primary_table": "t_order_record",
                 "properties": [],
                 "links": [{"target": "case", "type": "dc_record_to_case",
                            "join": "DCCaseRecord.case_code = Case.case_code",
                            "cardinality": "N:1",
                            "description": "跨库关联，需通过 site 字段路由到对应地域 RDS 实例"}],
                 "metrics": [], "aliases": []}
        merged, _ = ontology_service.merge_object_pair(rich, shell)
        assert len(merged["links"]) == 1
        assert "跨库关联" in merged["links"][0]["description"]

    def test_unions_aliases_without_duplicates(self):
        a = {"key": "k", "aliases": ["CaseFile", "案例文件"], "properties": []}
        b = {"key": "k", "aliases": ["casefile", "病例附件"], "properties": []}
        merged, _ = ontology_service.merge_object_pair(a, b)
        # "casefile" 与 "CaseFile" 判为同一别名只留一个，但两个中文别名都保留
        assert merged["aliases"] == ["CaseFile", "案例文件", "病例附件"]

    def test_fills_empty_description_from_other(self):
        a = {"key": "k", "description": "", "properties": []}
        b = {"key": "k", "description": "跨站点案例汇总记录", "properties": []}
        merged, _ = ontology_service.merge_object_pair(a, b)
        assert merged["description"] == "跨站点案例汇总记录"


# ── activate 硬阻断重复 key ──────────────────────────────────────

class TestActivateBlocksDuplicates:
    def test_activate_rejects_duplicate_keys(self, monkeypatch):
        doc = {"objects": [
            {"key": "case_file", "primary_table": "t_case_files", "properties": []},
            {"key": "casefile", "primary_table": "t_case_files", "properties": []},
        ]}
        import json
        monkeypatch.setattr(
            ontology_service, "get_model",
            lambda mid: {"id": mid, "status": "draft", "datasource_id": 1,
                         "json_content": json.dumps(doc, ensure_ascii=False)})
        with pytest.raises(ValueError) as e:
            ontology_service.activate(123)
        assert "对象 key 重复" in str(e.value)

    def test_activate_rejects_duplicate_keys_case_insensitive(self, monkeypatch):
        doc = {"objects": [
            {"key": "WorkOrder", "primary_table": "t_a", "properties": []},
            {"key": "work_order", "primary_table": "t_a", "properties": []},
        ]}
        import json
        monkeypatch.setattr(
            ontology_service, "get_model",
            lambda mid: {"id": mid, "status": "draft", "datasource_id": 1,
                         "json_content": json.dumps(doc, ensure_ascii=False)})
        with pytest.raises(ValueError):
            ontology_service.activate(123)


# ── YAML 导入入口去重 ─────────────────────────────────────────────

class TestYamlImportDedup:
    def _run(self, object_types, monkeypatch):
        monkeypatch.setattr(ontology_yaml_import, "_datasource_name_by_id",
                            lambda ds_id: "test-alb")
        return ontology_yaml_import.palantir_to_canonical(
            [{"domain": "测试域", "object_types": object_types}],
            datasource_id=1, table_info={})

    def test_same_identity_same_table_is_merged(self, monkeypatch):
        """`CaseFile` 与 `casefile` 同主表 → 合并成一份，且保留独有指标。"""
        doc = self._run([
            {"name": "CaseFile", "api_name": "case_file", "source": "t_case_files",
             "properties": [{"name": "case_code", "type": "string", "is_primary_key": True}],
             "computed_properties": [{"name": "a_metric", "expression": "COUNT(*)"}]},
            {"name": "casefile", "source": "t_case_files",
             "computed_properties": [{"name": "b_metric", "expression": "SUM(x)",
                                      "description": "独有指标"}]},
        ], monkeypatch)
        keys = [o["key"] for o in doc["objects"]]
        assert keys.count("case_file") == 1
        assert len(doc["objects"]) == 1
        names = [m["name"] for m in doc["objects"][0]["metrics"]]
        assert "b_metric" in names and "a_metric" in names

    def test_same_identity_different_table_is_rejected(self, monkeypatch):
        """同名不同主表是真冲突，必须中止而不是静默二选一。"""
        with pytest.raises(ValueError) as e:
            self._run([
                {"name": "CaseFile", "api_name": "case_file", "source": "t_case_files"},
                {"name": "casefile", "source": "t_other_files"},
            ], monkeypatch)
        assert "同名不同主表" in str(e.value)

    def test_distinct_objects_are_not_merged(self, monkeypatch):
        doc = self._run([
            {"name": "CaseFile", "api_name": "case_file", "source": "t_case_files"},
            {"name": "CaseTreatment", "api_name": "case_treatment", "source": "t_case_treatments"},
        ], monkeypatch)
        assert len(doc["objects"]) == 2

    def test_keys_are_normalized_to_snake_case(self, monkeypatch):
        doc = self._run([
            {"name": "AIConversation", "source": "t_ai_conversation"},
        ], monkeypatch)
        assert doc["objects"][0]["key"] == "ai_conversation"


# ── 合并后的别名归一（否则旧写法会静默解析失败）────────────────

class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return self.rows


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self.rows)


class _ExplodingConn:
    """模拟元数据库不可用：归一必须降级为原样返回，不得阻断解析主链路。"""

    def cursor(self):
        raise RuntimeError("metadata db down")


class TestAliasNormalizationAfterMerge:
    """合并重复对象后，历史写法（casefile/CaseFile）不再是 object_key，
    但仍是别名 —— 解析层必须能归一，否则清理动作反而损失能力。"""

    ROWS = [
        {"object_key": "case_file", "aliases": "CaseFile, case_file, 案例文件, t_case_files"},
        {"object_key": "work_order", "aliases": "WorkOrder, work_order, 工单"},
    ]

    def _resolve(self, key, rows=None, warnings=None):
        from services.shared.semantics.binding_resolver import _resolve_key_by_alias
        return _resolve_key_by_alias(
            _FakeConn(self.ROWS if rows is None else rows), key, 1,
            warnings if warnings is not None else [])

    def test_unique_alias_is_normalized(self):
        assert self._resolve("casefile") == "case_file"
        assert self._resolve("CaseFile") == "case_file"
        assert self._resolve("CASE_FILE") == "case_file"
        assert self._resolve("工单") == "work_order"

    def test_canonical_key_is_noop(self):
        assert self._resolve("case_file") == "case_file"

    def test_unknown_ref_is_left_untouched(self):
        warnings = []
        assert self._resolve("不存在的对象", warnings=warnings) == "不存在的对象"
        assert warnings == []

    def test_ambiguous_alias_is_not_guessed(self):
        """同一别名命中多个对象时不得猜，必须回抛候选（宁缺勿错）。"""
        rows = [
            {"object_key": "order", "aliases": "订单"},
            {"object_key": "dc_case_record", "aliases": "订单"},
        ]
        warnings = []
        out = self._resolve("订单", rows=rows, warnings=warnings)
        assert out == "订单"          # 不改写
        assert len(warnings) == 1
        assert "命中多个对象的别名" in warnings[0]
        assert "order" in warnings[0] and "dc_case_record" in warnings[0]

    def test_db_failure_does_not_block_resolution(self):
        from services.shared.semantics.binding_resolver import _resolve_key_by_alias
        assert _resolve_key_by_alias(_ExplodingConn(), "casefile", 1, []) == "casefile"
