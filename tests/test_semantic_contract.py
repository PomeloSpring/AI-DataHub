"""Phase 6 契约先行 —— 语义层跨语言唯一契约与执行门面护栏。

- SemanticQuery/SemanticResult JSON Schema 导出锁定（extra="forbid" → additionalProperties=false）；
- 对外错误码稳定（只增不改）；
- 对外失败文案口径（护栏 §7）：引擎原始错误仅进日志，对外通用文案，绝不泄露 SQL/主机/账号/IP；
- execute / execute_sql 门面契约：拒绝/失败抛 SemanticError，成功返回 SemanticResult；
  身份/数据源只信 policy_ctx（护栏 §2），序列化经 df_to_columns_rows（护栏 §8）。
"""

import pandas as pd
import pytest

from backend.semantics import contract
from backend.semantics import execute as facade
from backend.semantics.contract import EXEC_FAIL_HINT, ErrorCode, SemanticError
from backend.semantics.gates import SemanticExecution
from backend.semantics.models import PlannedExecution, ResolvedBinding, SemanticQuery, SemanticResult

# ── 契约导出 ─────────────────────────────────────────────────────


def test_export_schemas_contract_locked():
    schemas = contract.export_schemas()
    assert schemas["contract_version"] == contract.CONTRACT_VERSION

    q_schema = schemas["SemanticQuery"]
    assert "object" in q_schema.get("required", [])
    assert q_schema.get("additionalProperties") is False  # extra="forbid"
    for prop in ("metrics", "dimensions", "filters", "order", "limit",
                 "time_grain", "time_window", "params", "dry_run"):
        assert prop in q_schema["properties"], f"SemanticQuery 缺契约字段 {prop}"

    r_schema = schemas["SemanticResult"]
    assert r_schema.get("additionalProperties") is False
    for prop in ("columns", "rows", "row_count", "applied_rls", "masked_columns",
                 "resolved_tables", "provenance", "warnings", "elapsed_ms"):
        assert prop in r_schema["properties"], f"SemanticResult 缺契约字段 {prop}"


def test_error_codes_frozen():
    """错误码跨版本稳定：只增不改（消费方/前端按值匹配）。"""
    assert {e.value for e in ErrorCode} == {
        "INVALID_INTENT", "UNBOUND", "NO_IDENTITY", "FORBIDDEN",
        "GUARDRAIL_BLOCKED", "RLS_BLOCKED", "APPROVAL_REQUIRED",
        "EXEC_FAILED", "INTERNAL",
    }


def test_block_code_mapping():
    assert contract.block_code("execute") is ErrorCode.EXEC_FAILED
    assert contract.block_code("rls") is ErrorCode.RLS_BLOCKED
    assert contract.block_code("identity") is ErrorCode.NO_IDENTITY
    assert contract.block_code("approval") is ErrorCode.APPROVAL_REQUIRED
    assert contract.block_code("未知环节") is ErrorCode.INTERNAL


# ── 对外失败文案口径（护栏 §7） ──────────────────────────────────


def test_sanitize_execute_reason_hides_engine_detail():
    raw = "执行失败: Access denied for user 'root'@'10.0.0.9' (using password: YES)"
    out = contract.sanitize_execute_reason(raw, "execute")
    assert out == EXEC_FAIL_HINT
    assert "10.0.0.9" not in out and "root" not in out

    # blocked_at 为空但 reason 形如执行失败 → 同样脱敏
    assert contract.sanitize_execute_reason(raw, "") == EXEC_FAIL_HINT

    # 非执行阶段的闸门声明式拒绝原样返回
    keep = "RLS 改写失败, 拒绝执行未过滤查询: 别名缺失"
    assert contract.sanitize_execute_reason(keep, "rls") == keep


def test_safe_warnings_filters_leak_keywords():
    out = contract.safe_warnings(
        ["查询被安全护栏拦截", "physical_table=t_user not in adh_table_info"],
        ["catalog_ref=adh.db.t", "已自动补 LIMIT 1000"],
    )
    assert out == ["查询被安全护栏拦截", "已自动补 LIMIT 1000"]


# ── execute 门面 ─────────────────────────────────────────────────


def _fake_binding() -> ResolvedBinding:
    return ResolvedBinding(object_key="Case", datasource_id=7, physical_table="t_case_records")


def _fake_plan() -> PlannedExecution:
    return PlannedExecution(
        sql="SELECT m1, d1 FROM t_case_records", secured_sql="SELECT m1, d1 FROM t_case_records",
        dialect="mysql", datasource_id=7, resolved_tables=["t_case_records"],
        provenance={"resolution_sources": ["bindings"]},
    )


def test_execute_facade_success(monkeypatch):
    monkeypatch.setattr(facade, "resolve_binding", lambda obj, datasource_id=0: (_fake_binding(), []))
    monkeypatch.setattr(facade, "plan", lambda q, b: _fake_plan())
    monkeypatch.setattr(
        facade, "execute_semantic",
        lambda q, b, p, uc, question: SemanticExecution(
            allowed=True, base_sql=p.sql, secured_sql=p.secured_sql,
            result={"columns": ["m1", "d1"], "rows": [{"m1": 5, "d1": "x"}],
                    "row_count": 1, "execution_ms": 12},
            provenance={"gate_trail": ["identity"]},
        ),
    )
    q = SemanticQuery(object="Case", metrics=["m1"], dimensions=["d1"], datasource_id=7)
    res = facade.execute(q, facade.PolicyContext(user_id=42, username="bob", datasource_id=7))

    assert isinstance(res, SemanticResult)
    assert [(c.name, c.role) for c in res.columns] == [("m1", "measure"), ("d1", "dimension")]
    assert res.rows == [[5, "x"]] and res.row_count == 1 and res.elapsed_ms == 12
    assert res.provenance.get("gate_trail") == ["identity"]


def test_execute_facade_identity_from_policy_ctx_only(monkeypatch):
    """身份/数据源只信 policy_ctx：query 内声明字段被权威覆盖（护栏 §2）。"""
    captured = {}

    def fake_resolve(obj, datasource_id=0):
        captured["datasource_id"] = datasource_id
        return _fake_binding(), []

    monkeypatch.setattr(facade, "resolve_binding", fake_resolve)
    monkeypatch.setattr(facade, "plan", lambda q, b: _fake_plan())
    monkeypatch.setattr(
        facade, "execute_semantic",
        lambda q, b, p, uc, question: captured.update(user_context=uc)
        or SemanticExecution(allowed=True, result={"columns": [], "rows": [], "row_count": 0}),
    )
    q = SemanticQuery(object="Case", metrics=["m1"], datasource_id=1, user_id=999)
    facade.execute(q, {"user_id": 42, "username": "bob", "workspace_id": 5, "datasource_id": 7})

    assert captured["datasource_id"] == 7  # policy_ctx 覆盖 query 声明的 1
    assert captured["user_context"]["user_id"] == 42 and captured["user_context"]["workspace_id"] == 5


def test_execute_facade_exec_error_hides_detail(monkeypatch):
    monkeypatch.setattr(facade, "resolve_binding", lambda obj, datasource_id=0: (_fake_binding(), []))
    monkeypatch.setattr(facade, "plan", lambda q, b: _fake_plan())
    monkeypatch.setattr(
        facade, "execute_semantic",
        lambda q, b, p, uc, question: SemanticExecution(
            allowed=False, blocked_at="execute",
            reason="执行失败: Access denied for user 'root'@'10.0.0.9'",
        ),
    )
    with pytest.raises(SemanticError) as ei:
        facade.execute(SemanticQuery(object="Case", metrics=["m1"], datasource_id=7))
    assert ei.value.code is ErrorCode.EXEC_FAILED
    assert "10.0.0.9" not in str(ei.value) and "root" not in str(ei.value)
    assert ei.value.to_dict()["code"] == "EXEC_FAILED"


def test_execute_facade_unbound_and_guardrail(monkeypatch):
    monkeypatch.setattr(facade, "resolve_binding", lambda obj, datasource_id=0: (None, []))
    with pytest.raises(SemanticError) as ei:
        facade.execute(SemanticQuery(object="Ghost", metrics=["m1"], datasource_id=7))
    assert ei.value.code is ErrorCode.UNBOUND and ei.value.blocked_at == "binding"

    monkeypatch.setattr(facade, "resolve_binding", lambda obj, datasource_id=0: (_fake_binding(), []))
    monkeypatch.setattr(
        facade, "plan",
        lambda q, b: PlannedExecution(sql="", warnings=["physical_table=t_case_records 全表扫描被拒"]),
    )
    with pytest.raises(SemanticError) as ei:
        facade.execute(SemanticQuery(object="Case", metrics=["m1"], datasource_id=7))
    assert ei.value.code is ErrorCode.GUARDRAIL_BLOCKED
    # detail 里的原因经 safe_warnings 过滤（不得带物理表）
    assert all("physical_table" not in str(w) for w in ei.value.detail.get("reason", []))


def test_execute_facade_rejects_sql_intent():
    with pytest.raises(SemanticError) as ei:
        facade.execute({"object": "Case", "sql": "SELECT * FROM t_user"})
    assert ei.value.code is ErrorCode.INVALID_INTENT


def test_execute_plan_error_detail_service_payload(monkeypatch):
    """execute_plan（run_semantic_query 等调用方的执行段）：错误 detail 带服务内消费产物，
    对外投影（to_dict）不泄 detail。"""
    monkeypatch.setattr(facade, "resolve_binding", lambda obj, datasource_id=0: (_fake_binding(), []))
    monkeypatch.setattr(facade, "plan", lambda q, b: _fake_plan())
    monkeypatch.setattr(
        facade, "execute_semantic",
        lambda q, b, p, uc, question: SemanticExecution(
            allowed=False, blocked_at="approval", needs_approval=True,
            proposed_edit={"op": "alias"}, applied_rls=["p1"], masked_columns=["phone"],
            reason="高风险需审批",
        ),
    )
    q = SemanticQuery(object="Case", metrics=["m1"], datasource_id=7)
    b, _ = facade.resolve_binding("Case")
    p = facade.plan(q, b)
    with pytest.raises(SemanticError) as ei:
        facade.execute_plan(q, b, p, {"user_id": 42})
    assert ei.value.code is ErrorCode.APPROVAL_REQUIRED
    assert ei.value.detail["needs_approval"] is True
    assert ei.value.detail["proposed_edit"] == {"op": "alias"}
    assert ei.value.detail["applied_rls"] == ["p1"]
    assert ei.value.detail["masked_columns"] == ["phone"]
    assert "proposed_edit" not in ei.value.to_dict()  # 对外投影不含 detail


# ── execute_sql 门面 ─────────────────────────────────────────────


def test_execute_sql_facade_governed_and_serialized(monkeypatch):
    captured = {}

    def fake_exec(sql, datasource_id=None, user_context=None, workspace_id=0):
        captured.update(sql=sql, datasource_id=datasource_id,
                        user_context=user_context, workspace_id=workspace_id)
        return pd.DataFrame([{"a": 1}]), 5, 1

    monkeypatch.setattr("backend.core.query_executor.execute_query_with_permission", fake_exec)
    res = facade.execute_sql("SELECT 1 AS a", {"user_id": 9, "username": "u", "workspace_id": 3, "datasource_id": 7})

    assert captured["sql"] == "SELECT 1 AS a" and captured["datasource_id"] == 7
    assert captured["user_context"]["user_id"] == 9 and captured["workspace_id"] == 3
    assert res.rows == [[1]] and res.row_count == 1


def test_execute_sql_facade_error_mapping(monkeypatch):
    def fail(sql, datasource_id=None, user_context=None, workspace_id=0):
        raise RuntimeError("Access denied for user 'root'@'10.0.0.9'")

    monkeypatch.setattr("backend.core.query_executor.execute_query_with_permission", fail)
    with pytest.raises(SemanticError) as ei:
        facade.execute_sql("SELECT 1", {"datasource_id": 7})
    assert ei.value.code is ErrorCode.EXEC_FAILED
    assert "10.0.0.9" not in str(ei.value)

    def denied(sql, datasource_id=None, user_context=None, workspace_id=0):
        raise PermissionError("角色无该数据源权限")

    monkeypatch.setattr("backend.core.query_executor.execute_query_with_permission", denied)
    with pytest.raises(SemanticError) as ei:
        facade.execute_sql("SELECT 1", {"datasource_id": 7})
    assert ei.value.code is ErrorCode.FORBIDDEN
