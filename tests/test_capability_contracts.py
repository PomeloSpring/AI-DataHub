"""系统能力本体动作契约的单一真源与漂移门禁。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import system_ontology_catalog as catalog
from backend.modules.mind.execution import capability_contracts as contracts
from backend.modules.mind.execution.function_tools import FUNCTION_ACTION_SPECS
from backend.modules.mind.execution.sdk_tools import ontology_tools
from backend.modules.mind.execution.sdk_tools.compat import to_json_schema


def _doc_actions(doc):
    return {
        action["action_key"]: action
        for obj in doc["objects"]
        for action in obj.get("actions", [])
    }


def test_action_keys_executors_and_tool_names_are_globally_unique():
    items = list(contracts.build_action_contracts().items())
    assert len(items) == len({key for key, _ in items})
    ai = [meta for _, meta in items if meta["channel"] == "ai"]
    assert len(ai) == len({meta["executor_ref"] for meta in ai})
    assert len(ai) == len({meta["tool_name"] for meta in ai})


def test_function_ontology_and_human_registries_have_bidirectional_coverage():
    actual = contracts.build_action_contracts()
    function_keys = set(FUNCTION_ACTION_SPECS)
    ontology_keys = {spec["action_key"] for spec in ontology_tools.TOOL_SPECS}
    human_keys = set(contracts.HUMAN_ACTION_SPECS)
    assert set(actual) == function_keys | ontology_keys | human_keys
    assert not (function_keys & ontology_keys)
    assert not (function_keys & human_keys)
    assert not (ontology_keys & human_keys)
    registry = contracts.get_executor_registry()
    assert set(registry) == function_keys | ontology_keys
    assert set(registry) == {key for key, meta in actual.items() if meta["channel"] == "ai"}


def test_schemas_are_derived_from_real_registries_without_second_copy():
    actual = contracts.build_action_contracts()
    for key, spec in FUNCTION_ACTION_SPECS.items():
        assert actual[key]["input_schema"] == to_json_schema(spec["schema"])
        assert actual[key]["perm_code"] == spec["perm_code"]
        assert actual[key]["read_only"] is bool(spec["read_only"])
        assert actual[key]["executor_ref"] == f"function:{spec['tool_name']}"
    for spec in ontology_tools.TOOL_SPECS:
        key = spec["action_key"]
        assert actual[key]["input_schema"] == to_json_schema(spec["schema"])
        assert actual[key]["perm_code"] == spec["perm_code"]
        assert actual[key]["read_only"] is bool(spec.get("readonly", spec.get("read_only", False)))
        assert actual[key]["executor_ref"] == f"ontology:{spec['name']}"
    for key, spec in contracts.HUMAN_ACTION_SPECS.items():
        assert actual[key]["input_schema"] == spec["input_schema"]
        assert actual[key]["executor_ref"] == ""
        assert actual[key]["auto_executable"] is False
        assert actual[key]["human_entry_ref"]


def test_every_contract_has_valid_permission_channel_idempotency_and_timeout():
    for key, meta in contracts.build_action_contracts().items():
        assert meta["perm_code"] and ":" in meta["perm_code"], key
        assert meta["channel"] in ("ai", "human"), key
        assert meta["read_only"] is (meta["read_only"] is True), key
        assert meta["timeout_ms"] > 0, key
        assert meta["idempotency"] in ("none", "required"), key
        assert meta["error_codes"], key
        assert meta["contract_digest"] == contracts.contract_digest(
            {name: value for name, value in meta.items() if name != "contract_digest"}
        )


def test_evolution_actions_keep_domain_permission_double_gates():
    actual = contracts.build_action_contracts()
    expected = {
        "evolution_candidate.adopt": "ontology:save",
        "evolution_candidate.reject": "ontology:save",
        "evolution_candidate.to_term": "ontology:save",
        "evolution_metric.create": "metrics:manage",
        "evolution_dimension.create": "metrics:manage",
        "evolution_proposal.applied": "knowledge:manage",
        "evolution_proposal.reject": "knowledge:manage",
    }
    for key, domain_code in expected.items():
        assert key in actual
        assert actual[key]["perm_code"] == "evolution:manage"
        assert actual[key]["required_perm_codes"] == ["evolution:manage", domain_code]


def test_data_engineering_and_human_actions_never_enter_ai_executable_set():
    actual = contracts.build_action_contracts()
    for key, meta in actual.items():
        blocked = key.startswith(("sync:", "dag:", "udf:")) or "materializ" in key.lower()
        if blocked or meta["channel"] == "human":
            assert meta["auto_executable"] is False, key
    executable = {key for key, meta in actual.items() if meta["auto_executable"]}
    assert not set(contracts.HUMAN_ACTION_SPECS) & executable
    assert not any(key.startswith(("sync:", "dag:", "udf:")) for key in executable)


def test_canonical_document_covers_every_contract_and_classifies_objects():
    doc = catalog.build_system_ontology_doc()
    actual = _doc_actions(doc)
    expected = contracts.build_action_contracts()
    assert set(actual) == set(expected)
    assert doc["contract_digest"] == contracts.contract_digest(expected)
    assert contracts.validate_document_contracts(doc) == []
    modes = {"actionable", "queryable", "human_only", "informational"}
    # 51 + capability_flow（P3 能力流程）：计数锁防对象清单意外缩永，新增对象同步 +1
    assert len(doc["objects"]) == 52
    for obj in doc["objects"]:
        assert obj["capability_mode"] in modes
        actions = obj.get("actions", [])
        if not actions:
            assert obj["capability_mode"] == "informational"
        elif all(action["channel"] == "human" for action in actions):
            assert obj["capability_mode"] == "human_only"
        elif all(action["read_only"] for action in actions):
            assert obj["capability_mode"] == "queryable"
        else:
            assert obj["capability_mode"] == "actionable"


def test_document_drift_is_loud_for_digest_schema_executor_and_missing_action():
    doc = catalog.build_system_ontology_doc()
    changed = copy.deepcopy(doc)
    changed["contract_digest"] = "0" * 64
    assert any("contract_digest" in error for error in contracts.validate_document_contracts(changed))

    changed = copy.deepcopy(doc)
    changed["objects"][0]["actions"][0]["executor_ref"] = "function:missing"
    assert any("executor_ref" in error for error in contracts.validate_document_contracts(changed))

    changed = copy.deepcopy(doc)
    changed["objects"][0]["actions"][0]["input_schema"]["required"] = ["missing"]
    assert any("input_schema" in error for error in contracts.validate_document_contracts(changed))

    changed = copy.deepcopy(doc)
    changed["objects"][0]["actions"].pop()
    assert any("缺动作" in error for error in contracts.validate_document_contracts(changed))


@pytest.mark.parametrize("action_key,required", [
    ("content_article.get", {"slug"}),
    ("content_draft.create", {"title", "body"}),
    ("content_draft.update", {"slug", "expected_version"}),
    ("content_review.submit", {"slug"}),
    ("evolution_proposal.applied", {"title", "applied_ref"}),
    ("evolution_proposal.reject", {"title", "note"}),
    ("knowledge_proposal.create", {"target_type", "title", "content"}),
    ("report.generate", {"title"}),
])
def test_known_parameter_drift_is_fixed(action_key, required):
    schema = contracts.build_action_contracts()[action_key]["input_schema"]
    assert set(schema["required"]) == required
    for name in required:
        assert name in schema["properties"]


def test_split_ontology_actions_replace_ambiguous_legacy_names():
    actual = contracts.build_action_contracts()
    assert "ontology.generate" not in actual
    assert "ontology.save" not in actual
    assert {"ontology.generate_material", "ontology.save_draft", "ontology.save_model",
            "ontology.publish", "ontology.create_object", "ontology.update_object"} <= set(actual)
    assert actual["ontology.create_object"]["executor_ref"] == "ontology:add_ontology_objects"
    assert actual["ontology.update_object"]["executor_ref"] == "ontology:update_ontology_object"


def test_flow_references_consumption_production_and_human_entries_are_valid():
    doc = catalog.build_system_ontology_doc()
    actions = _doc_actions(doc)
    rules = {rule["key"] for rule in doc["rules"]}
    principles = {p["key"] for p in doc.get("permission_principles") or []}
    for scenario in doc["scenarios"]:
        assert set(scenario["uses_actions"]) <= set(actions)
        assert set(scenario["uses_objects"]) <= {obj["key"] for obj in doc["objects"]}
        assert set(scenario.get("uses_rules") or []) <= rules
        assert set(scenario.get("uses_principles") or []) <= principles
        produced = set()
        for step in scenario.get("flow", []):
            assert step["action"] in actions
            assert step["action_key"] == step["action"]
            assert step["executor_ref"] == actions[step["action"]]["executor_ref"]
            assert step["input_schema"] == actions[step["action"]]["input_schema"]
            assert step["channel"] in ("ai", "human")
            if step.get("consumes"):
                assert step["consumes"] in produced
            if step.get("produces"):
                produced.add(step["produces"])
            if step["channel"] == "human":
                assert actions[step["action"]]["human_entry_ref"]


def test_contract_modules_import_in_both_orders_in_fresh_interpreters():
    root = os.path.join(os.path.dirname(__file__), "..")
    orders = [
        ["backend.modules.mind.execution.function_tools",
         "backend.modules.mind.execution.sdk_tools.ontology_tools",
         "backend.modules.mind.execution.capability_contracts",
         "backend.modules.catalog.services.system_ontology_catalog"],
        ["backend.modules.catalog.services.system_ontology_catalog",
         "backend.modules.mind.execution.capability_contracts",
         "backend.modules.mind.execution.sdk_tools.ontology_tools",
         "backend.modules.mind.execution.function_tools"],
    ]
    for modules in orders:
        code = "\n".join(f"import {module}" for module in modules)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr


def test_contract_document_contains_no_handler_or_connection_metadata():
    blob = json.dumps(catalog.build_system_ontology_doc(), ensure_ascii=False)
    assert "handler" not in blob
    assert "connection_string" not in blob
    assert "password" not in blob
