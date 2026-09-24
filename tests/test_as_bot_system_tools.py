"""AS-BOT 系统能力 + 检索系统级硬限定回归（离线，无 DB/CLI/SDK）。"""
import asyncio
import json

import pytest

from services.datamind.execution.models import ExecutionContext
from services.datamind.execution.sdk_tools import system_tools as st
from services.datamind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from services.datamind.rag import qmind_retriever as qr


def _payload(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def ctx():
    token = set_execution_context(ExecutionContext(user_id=7, workspace_id=3, user_role="admin"))
    yield
    ExecutionContextVar.reset(token)


def _patch_role(monkeypatch, role):
    from services.shared.common import auth
    monkeypatch.setattr(auth, "resolve_execution_owner",
                        lambda uid, ws: {"user_id": uid, "workspace_id": ws, "role": role})


# ── 管理员门禁（fail-closed）──────────────────────────────────────

def test_admin_identity_fail_closed_without_user(monkeypatch):
    from services.shared.common import auth
    called = []
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda uid, ws: called.append(uid))
    token = ExecutionContextVar.set(ExecutionContext(user_id=0))
    try:
        assert st._admin_identity() is None
    finally:
        ExecutionContextVar.reset(token)
    assert called == []  # 无身份不查库


@pytest.mark.parametrize("role", ["viewer", "analyst", ""])
def test_system_usage_denies_non_admin(ctx, monkeypatch, role):
    _patch_role(monkeypatch, role)
    out = asyncio.run(st.system_usage({"days": 3}))
    assert out.get("isError") and "管理员" in _payload(out)["error"]


def test_system_overview_denies_non_admin(ctx, monkeypatch):
    _patch_role(monkeypatch, "viewer")
    out = asyncio.run(st.system_overview({}))
    assert out.get("isError")


# ── system_usage 复用 observability_service ────────────────────────

def test_system_usage_answers_chat_activity(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from services.authservice.services import observability_service as obs
    captured = {}
    def fake_summary(params):
        captured.update(params)
        return {"summary": {"users": 4, "turns": 12, "sessions": 6, "total_tokens": 999,
                            "credits": 1.5, "errors": 0}, "daily": [{"dt": "2026-09-21", "turns": 12}]}
    monkeypatch.setattr(obs, "usage_summary", fake_summary)
    out = _payload(asyncio.run(st.system_usage({"days": 3, "entrypoint": "chat"})))
    assert out["has_usage"] is True and out["active_users"] == 4
    assert captured["entrypoint"] == "chat" and captured["start"] and captured["end"]


def test_system_usage_rejects_bad_entrypoint(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from services.authservice.services import observability_service as obs
    captured = {}
    monkeypatch.setattr(obs, "usage_summary", lambda p: captured.update(p) or {"summary": {}, "daily": []})
    asyncio.run(st.system_usage({"days": 3, "entrypoint": "'; DROP TABLE x;--"}))
    assert captured["entrypoint"] == ""  # 非白名单入口被清空，杜绝注入/串味


def test_system_usage_by_user_grouping(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from services.authservice.services import observability_service as obs
    monkeypatch.setattr(obs, "usage_by_user", lambda p: [{"user_id": 7, "turns": 5}])
    out = _payload(asyncio.run(st.system_usage({"days": 3, "group_by": "user"})))
    assert out["group_by"] == "user" and out["users"] == [{"user_id": 7, "turns": 5}]


# ── system_overview 只读快照，best-effort 降级 ─────────────────────

def test_system_overview_counts_and_degrades(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from services.shared.common import db
    def fake_query(sql, params=None, fetchone=False):
        if "adh_datasources" in sql and "is_active" in sql:
            raise RuntimeError("列缺失")  # 该项降级为 None，不阻断整体
        return {"c": 3}
    monkeypatch.setattr(db, "execute_query", fake_query)
    out = _payload(asyncio.run(st.system_overview({})))
    ov = out["overview"]
    assert ov["datasources"] == 3 and ov["datasources_active"] is None
    assert ov["pending_approvals"] == 3


# ── 检索系统级硬限定：绝不回退业务本体 ────────────────────────────

def test_system_scope_never_falls_back_to_graphrag(monkeypatch):
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [])
    hit = []
    monkeypatch.setattr(qr, "_fallback_graphrag", lambda *a, **k: hit.append(1) or {"chunks": [{"content": "test-alb"}]})
    out = qr.qmind_retrieve("近3天有谁用chat", 0, [], system_scope=True)
    assert hit == []  # 未回退业务检索
    assert out["rag_source"] == "system_kb_only" and out["count"] == 0
    assert not any("test-alb" in str(c) for c in out["chunks"])


def test_non_system_scope_still_falls_back(monkeypatch):
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [])
    monkeypatch.setattr(qr, "_fallback_graphrag", lambda *a, **k: {"chunks": [{"content": "biz"}], "count": 1, "rag_source": "graphrag"})
    out = qr.qmind_retrieve("销售额", 5, [])
    assert out["rag_source"] == "graphrag" and out["count"] == 1


def test_system_scope_circuit_open_returns_empty_not_business(monkeypatch):
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [{"id": 1, "name": "sys", "notebook_id": "nb", "cfg": {}}])
    monkeypatch.setattr(qr, "_breaker_allowed", lambda now=None: False)
    called = []
    monkeypatch.setattr(qr, "_fallback_graphrag", lambda *a, **k: called.append(1))
    out = qr.qmind_retrieve("用量", 0, [1], system_scope=True)
    assert called == [] and out["rag_source"] == "system_kb_only" and out.get("degraded")


def test_knowledge_search_system_bot_passes_system_scope(monkeypatch):
    from services.datamind.execution.sdk_tools import semantic_tools as stl
    from services.datamind.execution import wakers
    token = set_execution_context(ExecutionContext(
        user_id=7, workspace_id=3, extra={"waker_key": wakers.SYSTEM_BOT_WAKER_KEY}))
    seen = {}
    def fake_retrieve(q, ds, kb_ids, system_scope=False):
        seen["system_scope"] = system_scope
        seen["kb_ids"] = kb_ids
        return {"chunks": [], "count": 0, "rag_source": "system_kb_only"}
    monkeypatch.setattr("services.datamind.rag.qmind_retriever.qmind_retrieve", fake_retrieve)
    monkeypatch.setattr(wakers, "resolve_system_bot_waker", lambda: {"id": 99, "knowledge_base_ids": [11]})
    monkeypatch.setattr(wakers, "collect_knowledge_base_ids", lambda ws: [11])
    monkeypatch.setattr("services.datamind.execution.resource_guard.execute_query", lambda *a: [{"id": 11}])
    try:
        asyncio.run(stl.knowledge_search({"question": "近3天有谁用chat"}))
    finally:
        ExecutionContextVar.reset(token)
    assert seen["system_scope"] is True and seen["kb_ids"] == [11]


# ── 工具组注册 ────────────────────────────────────────────────────

def test_system_group_registered():
    from services.datamind.execution.sdk_tools import TOOL_SERVER_BUILDERS, TOOL_SERVER_TOOLS
    assert "system" in TOOL_SERVER_BUILDERS
    srv, tools = TOOL_SERVER_TOOLS["system"]
    assert srv == "datahub_system" and set(tools) == {"system_usage", "system_overview"}


def test_system_bot_waker_includes_system_group(monkeypatch):
    from services.datamind.execution import wakers
    monkeypatch.setattr(wakers, "_query", lambda sql, params=(): [
        {"id": 1, "waker_key": wakers.SYSTEM_BOT_WAKER_KEY, "tools": "{}", "is_active": 1}])
    w = wakers.resolve_system_bot_waker()
    assert "system" in w["tools"]["groups"] and "query" not in w["tools"]["groups"]
