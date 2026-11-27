"""P2 本体动作运行时契约（capability_runtime）。

锁 plan P2 验收六件事：

1. 能从 active 本体契约执行一个真实只读动作（契约解析 → Schema → 权限 → executor → 输出校验）；
2. 无权限写动作在 Handler 调用前拒绝，调用次数为 0；
3. 人工动作拒绝 AI 执行（HUMAN_ACTION_REQUIRED + human_entry_ref）；
4. active doc 缺失 / Schema 错误 / executor 缺失 / 契约漂移均显式失败（fail-loud）；
5. system_capability_plan（经 build_capability_plan）返回精确工具名、executor、Schema、
   幂等与超时信息；
6. 幂等语义：required 缺键拒、同键同参重放、同键异参 IDEMPOTENCY_CONFLICT；
   数据工程动作永不可执行；超时显式 TIMEOUT。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.modules.mind.execution import capability_contracts
from backend.modules.mind.execution import capability_runtime as rt


# ── 契约夹具：一份最小 active doc（动作契约字段与 _base_contract 同形）────────

def _action(key: str, *, channel: str = "ai", read_only: bool = True,
            perm_code: str = "report:read", required=None, tool_name: str = "",
            source: str = "function_tools", input_schema=None,
            idempotency: str = "none", timeout_ms: int = 1000,
            human_entry_ref: str = "", executor_ref: str = "") -> dict:
    tool_name = tool_name or key.replace(".", "_")
    if not executor_ref:
        executor_ref = (f"function:{tool_name}" if source == "function_tools"
                        else f"ontology:{tool_name}")
    return {
        "action_key": key, "key": key, "label": key,
        "source_registry": source, "tool_name": tool_name,
        "executor_ref": executor_ref if channel == "ai" else "",
        "qualified_name": f"mcp__{'datahub_functions' if source == 'function_tools' else 'datahub_ontology'}__{tool_name}",
        "channel": channel, "read_only": read_only, "perm_code": perm_code,
        "required_perm_codes": list(required or [perm_code]),
        "input_schema": input_schema or {"type": "object", "properties": {}, "required": []},
        "output_schema": {"type": "object",
                          "properties": {"content": {"type": "array"}, "isError": {"type": "boolean"}},
                          "required": ["content"]},
        "error_codes": ["SCHEMA_INVALID", "PERMISSION_DENIED", "TIMEOUT", "EXECUTION_FAILED"],
        "idempotency": idempotency, "timeout_ms": timeout_ms,
        "auto_executable": channel == "ai" and not perm_code.startswith(("sync:", "dag:", "udf:")),
        "human_entry_ref": human_entry_ref,
    }


def _doc(*actions: dict) -> dict:
    return {
        "kind": "system",
        "contract_digest": "digest-1",
        "objects": [{"key": "report", "display_name": "报表", "actions": list(actions)}],
        "rules": [{"key": "r1", "statement": "rule-one"}],
        "scenarios": [],
    }


def _install(monkeypatch, doc=None, *, drift=(), executor=None, handler=None):
    """返回 calls（handler 调用计数）；把 DB/注册表/裁决换成测试替身。"""
    doc = doc if doc is not None else _doc(
        _action("report.list", tool_name="list_reports"),
        _action("report.export", tool_name="export_report", read_only=False,
                perm_code="report:manage",
                input_schema={"type": "object",
                              "properties": {"report_id": {"type": "integer"}},
                              "required": ["report_id"]}),
        _action("content_review.publish", channel="human", read_only=False,
                perm_code="article:publish",
                human_entry_ref="http:POST /api/content/publish/{ident}"),
        _action("metric.idem_write", tool_name="idem_write", read_only=False,
                perm_code="metrics:manage", idempotency="required"),
        _action("sync.run", tool_name="sync_run", read_only=False, perm_code="sync:execute"),
    )
    calls = {"handler": 0, "args": None}

    def _handler(args):
        calls["handler"] += 1
        calls["args"] = args
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(rt, "_conn_query", lambda sql: {
        "id": 7, "name": "AS-BOT 系统能力本体",
        "json_content": json.dumps(doc, ensure_ascii=False),
        "updated_at": "2026-11-27 00:00:00",
    })
    monkeypatch.setattr(capability_contracts, "validate_document_contracts",
                        lambda d: list(drift))
    monkeypatch.setattr(capability_contracts, "resolve_executor",
                        lambda key: (executor or {
                            "handler": handler or _handler,
                            "qualified_name": f"mcp__datahub_functions__{key.replace('.', '_')}",
                            "async_handler": False,
                        }) if key != "UNREGISTERED" else (_ for _ in ()).throw(KeyError(key)))
    monkeypatch.setattr("backend.modules.mind.execution.tool_policy.check_tool",
                        lambda ctx, qualified: None)
    monkeypatch.setattr("backend.modules.mind.execution.resource_guard.validate_tool_resources",
                        lambda ctx, qualified, args: dict(args))
    monkeypatch.setattr("backend.core.perm_link.require_write_perm",
                        lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.context.get_execution_context",
                        lambda: SimpleNamespace(user_id=1, workspace_id=1))
    monkeypatch.setattr("backend.common.auth.log_audit", lambda *a, **k: None)
    return calls


_CTX = SimpleNamespace(user_id=1, workspace_id=1)


@pytest.fixture(autouse=True)
def _runtime_enabled(monkeypatch):
    """灰度开关（P5）：契约执行链默认关闭，测试显式开启。"""
    monkeypatch.setenv("CAPABILITY_RUNTIME_ENABLED", "1")


def test_feature_disabled_by_default_is_loud(monkeypatch):
    """开关未开时执行链显式拒（FEATURE_DISABLED），不静默回退。"""
    monkeypatch.delenv("CAPABILITY_RUNTIME_ENABLED", raising=False)
    calls = _install(monkeypatch)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("report.list", {}, ctx=_CTX)
    assert exc.value.code == rt.FEATURE_DISABLED
    assert calls["handler"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 1. 从 active 契约执行真实只读动作
# ═══════════════════════════════════════════════════════════════════════════

def test_executes_real_read_only_action_from_active_contract(monkeypatch):
    calls = _install(monkeypatch)
    result = rt.execute_action("report.list", {}, ctx=_CTX)
    assert result["content"][0]["text"] == "ok"
    assert calls["handler"] == 1


def test_load_active_contract_fixes_fingerprint_and_indexes_actions(monkeypatch):
    _install(monkeypatch)
    contract = rt.load_active_contract()
    assert contract.model_id == 7 and contract.fingerprint == "digest-1"
    assert set(contract.actions) >= {"report.list", "report.export"}
    assert rt.resolve_action("report.list", contract)["tool_name"] == "list_reports"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 无权限写动作：Handler 调用前拒绝，调用次数为 0
# ═══════════════════════════════════════════════════════════════════════════

def test_denied_write_action_never_touches_handler(monkeypatch):
    calls = _install(monkeypatch)

    def _deny(user_id, workspace_id, code, label=""):
        raise PermissionError(f"角色未授予「{code}」权限")

    monkeypatch.setattr("backend.core.perm_link.require_write_perm", _deny)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("report.export", {"report_id": 1}, ctx=_CTX)
    assert exc.value.code == rt.PERMISSION_DENIED
    assert calls["handler"] == 0, "权限拒绝必须发生在 Handler 调用之前"


def test_no_trusted_identity_is_denied(monkeypatch):
    calls = _install(monkeypatch)
    monkeypatch.setattr(
        "backend.modules.mind.execution.sdk_tools.context.get_execution_context", lambda: None)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("report.list", {}, ctx=None)
    assert exc.value.code == rt.PERMISSION_DENIED
    assert calls["handler"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. 人工动作拒绝 AI 执行；数据工程动作永不可执行
# ═══════════════════════════════════════════════════════════════════════════

def test_human_action_rejected_with_entry_hint(monkeypatch):
    calls = _install(monkeypatch)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("content_review.publish", {}, ctx=_CTX)
    assert exc.value.code == rt.HUMAN_ACTION_REQUIRED
    assert "http:POST /api/content/publish" in exc.value.human_entry_ref
    assert calls["handler"] == 0


def test_data_engineering_action_is_never_executable(monkeypatch):
    calls = _install(monkeypatch)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("sync.run", {}, ctx=_CTX)
    assert exc.value.code == rt.PERMISSION_DENIED
    assert "数据工程域" in str(exc.value)
    assert calls["handler"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. fail-loud：active doc 缺失 / Schema / executor / 漂移 / 未知动作
# ═══════════════════════════════════════════════════════════════════════════

def test_missing_active_doc_is_loud(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(rt, "_conn_query", lambda sql: None)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.load_active_contract()
    assert exc.value.code == rt.ONTOLOGY_UNAVAILABLE


def test_contract_drift_is_loud(monkeypatch):
    _install(monkeypatch, drift=["动作 report.list 的 executor_ref 与代码注册表漂移"])
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.load_active_contract()
    assert exc.value.code == rt.CONTRACT_DRIFT
    assert "漂移" in str(exc.value)


def test_schema_violation_rejects_before_handler(monkeypatch):
    calls = _install(monkeypatch)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("report.export", {"report_id": "not-a-number"}, ctx=_CTX)
    assert exc.value.code == rt.SCHEMA_INVALID
    assert calls["handler"] == 0


def test_missing_executor_is_loud(monkeypatch):
    calls = _install(monkeypatch)
    monkeypatch.setattr(rt, "resolve_action",
                        lambda key, contract=None: _action("ghost.action", tool_name="ghost"))
    monkeypatch.setattr(capability_contracts, "resolve_executor",
                        lambda key: (_ for _ in ()).throw(KeyError(key)))
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("ghost.action", {}, ctx=_CTX)
    assert exc.value.code == rt.EXECUTOR_NOT_REGISTERED
    assert calls["handler"] == 0


def test_unknown_action_is_not_fuzzy_matched(monkeypatch):
    _install(monkeypatch)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.resolve_action("report.lis")   # 差一个字符也不许模糊命中
    assert exc.value.code == rt.ACTION_NOT_FOUND
    with pytest.raises(rt.CapabilityRuntimeError) as exc2:
        rt.resolve_action("report.")
    assert exc2.value.code == rt.ACTION_NOT_FOUND
    assert "相近 key" in str(exc2.value), "可以给诊断提示，但不得自动匹配"


def test_duplicate_action_in_doc_is_ambiguous(monkeypatch):
    _install(monkeypatch, doc=_doc(_action("report.list"), _action("report.list")))
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.load_active_contract()
    assert exc.value.code == rt.ACTION_AMBIGUOUS


def test_output_not_matching_schema_is_execution_failed(monkeypatch):
    _install(monkeypatch, handler=lambda args: {"unexpected": "shape"})
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("report.list", {}, ctx=_CTX)
    assert exc.value.code == rt.EXECUTION_FAILED
    assert "output_schema" in str(exc.value)


# ═══════════════════════════════════════════════════════════════════════════
# 5. build_capability_plan：精确工具名 / executor / Schema / 幂等 / 超时
# ═══════════════════════════════════════════════════════════════════════════

def test_build_capability_plan_carries_execution_contract(monkeypatch):
    _install(monkeypatch)
    contract = rt.load_active_contract()
    scenario = {"key": "s1", "title": "导出报表", "goal": "出一份报表",
                "uses_rules": ["r1"],
                "flow": [{"order": 1, "action": "report.export",
                          "purpose": "导出", "gated_by": ["r1"]},
                         {"order": 2, "action": "content_review.publish",
                          "purpose": "发布"}]}
    plan = rt.build_capability_plan(scenario, contract=contract)
    assert plan["fingerprint"] == "digest-1" and plan["model_id"] == 7
    steps = plan["steps"]
    assert [s["action"] for s in steps] == ["report.export", "content_review.publish"]
    first = steps[0]
    assert first["tool_name"] == "export_report"
    assert first["executor_ref"] == "function:export_report"
    assert first["input_schema"]["required"] == ["report_id"]
    assert first["idempotency"] == "none" and first["timeout_ms"] == 1000
    assert first["error_codes"] and "TIMEOUT" in first["error_codes"]
    assert first["rule_gates"][0]["statement"] == "rule-one"
    second = steps[1]
    assert second["channel"] == "human"
    assert second["human_entry_ref"].startswith("http:POST")


def test_build_capability_plan_marks_unresolvable_step(monkeypatch):
    _install(monkeypatch)
    plan = rt.build_capability_plan(
        {"key": "s", "flow": [{"order": 1, "action": "ghost.action"}]})
    step = plan["steps"][0]
    assert step["resolvable"] is False and "不在系统本体契约中" in step["why_not"]


def test_system_capability_plan_includes_tool_executor_schema_timeout(monkeypatch):
    """system_capability_plan 的步骤必须带精确工具名、executor、Schema、幂等与超时。"""
    import asyncio

    from backend.modules.mind.execution.sdk_tools import system_tools

    _install(monkeypatch)
    monkeypatch.setattr(system_tools, "_admin_identity",
                        lambda: {"user_id": 1, "username": "admin"})
    monkeypatch.setattr(system_tools, "_active_system_doc", lambda: {
        "rules": [{"key": "r1", "statement": "rule-one"}],
        "objects": [{"key": "report", "actions": [
            _action("report.list", tool_name="list_reports")]}],
        "scenarios": [{"key": "s1", "title": "出报表", "flow": [
            {"order": 1, "action": "report.list", "perm_code": "report:read",
             "channel": "ai", "purpose": "取数"}]}],
    })
    monkeypatch.setattr("backend.core.role_service.role_service.get_user_role_ai_perms",
                        lambda *a, **k: {"report:read": {"ai_access": "read", "label": "", "ai_note": ""}})

    out = asyncio.run(system_tools.system_capability_plan({"scenario": "s1"}))
    payload = json.loads(out["content"][0]["text"])
    step = payload["steps"][0]
    assert step["tool_name"] == "list_reports"
    assert step["executor_ref"] == "function:list_reports"
    assert step["input_schema"] is not None and "output_schema" in step
    assert step["idempotency"] == "none" and step["timeout_ms"] == 1000
    assert step["you_can_run"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. 幂等与超时
# ═══════════════════════════════════════════════════════════════════════════

def test_idempotency_required_without_key_is_rejected(monkeypatch):
    calls = _install(monkeypatch)
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("metric.idem_write", {}, ctx=_CTX)
    assert exc.value.code == rt.IDEMPOTENCY_CONFLICT
    assert calls["handler"] == 0


def test_idempotent_replay_returns_first_result_and_conflicts_on_mismatch(monkeypatch):
    calls = _install(monkeypatch)
    first = rt.execute_action("metric.idem_write", {"x": 1}, ctx=_CTX, idempotency_key="k1")
    assert calls["handler"] == 1 and "replayed" not in first

    replay = rt.execute_action("metric.idem_write", {"x": 1}, ctx=_CTX, idempotency_key="k1")
    assert replay["replayed"] is True
    assert calls["handler"] == 1, "同键同参重放不得再次执行"

    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("metric.idem_write", {"x": 2}, ctx=_CTX, idempotency_key="k1")
    assert exc.value.code == rt.IDEMPOTENCY_CONFLICT
    assert calls["handler"] == 1


def test_timeout_is_explicit(monkeypatch):
    import time

    def _slow(args):
        time.sleep(1.5)
        return {"content": [{"type": "text", "text": "late"}]}

    _install(monkeypatch, handler=_slow)
    contract = rt.load_active_contract()
    with pytest.raises(rt.CapabilityRuntimeError) as exc:
        rt.execute_action("report.list", {}, ctx=_CTX, contract=contract)
    # 契约 timeout_ms=1000：超过即 TIMEOUT（不静默等到跑完）
    assert exc.value.code == rt.TIMEOUT
