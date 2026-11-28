import asyncio
from types import SimpleNamespace

from backend.modules.mind.execution.sdk_tools import semantic_query as sq


def test_semantic_query_records_plan_and_execute_phases_without_sql_or_row_values(monkeypatch):
    from backend import observability
    from backend.modules.mind.execution.sdk_tools import context as execution_context
    from backend.modules.mind.execution import resource_guard
    from backend.semantics import binding_resolver, execute, planner

    binding = SimpleNamespace(
        object_key="case",
        datasource_id=12,
        column_map={},
        physical_table="physical_case_table",
        dataset_ref="",
        guardrail=SimpleNamespace(query_mode="aggregate"),
    )
    plan = SimpleNamespace(
        sql="SELECT secret_column FROM physical_case_table",
        warnings=[],
        provenance={},
        dialect="mysql",
    )
    result = SimpleNamespace(
        columns=[SimpleNamespace(name="案例数量")],
        rows=[(42,)],
        row_count=1,
        elapsed_ms=4,
        applied_rls=[],
        masked_columns=[],
    )
    spans = []

    monkeypatch.setattr(observability, "record_span", lambda **span: spans.append(span))
    monkeypatch.setattr(binding_resolver, "resolve_binding", lambda *a, **k: (binding, []))
    monkeypatch.setattr(resource_guard, "validate_binding", lambda *a, **k: None)
    monkeypatch.setattr(planner, "plan", lambda *a, **k: plan)
    monkeypatch.setattr(execute, "execute_plan", lambda *a, **k: result)
    monkeypatch.setattr(execution_context, "record_unattended_query", lambda *a, **k: None)

    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["案例数量"], "datasource_id": 12}, None))

    assert is_error is False
    assert payload["rows"] == [{"案例数量": 42}]
    assert [span["name"] for span in spans] == ["semantic_plan", "semantic_execute"]
    assert all(span["kind"] == "phase" and span["duration_ms"] >= 0 for span in spans)
    assert "SELECT" not in str(spans)
    assert "physical_case_table" not in str(spans)
    assert "42" not in str(spans)
    assert "row_count=1" in spans[1]["output_text"]
