"""写动作直执行回归：权限码 fail-closed 把关 + decided_by 服务端注入与审计落库。

覆盖: 审批通道退役后的唯一回路——菜单与功能权限码（perm_link）直执行把关；
无权限/级别不足/涉密收窄一律拒绝且原因可解释；别名回写决策人由服务端注入，
客户端伪造无效，decided_by 落入审计列。
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.datamind.api import as_bot
from services.datamind.execution import perm_link
from services.datamind.rag import alias_suggestion


# ── 写动作权限码 fail-closed（check_write_perm） ─────────────────

class TestWritePermFailClosed:
    def test_missing_perm_rejected(self):
        ok, reason = perm_link.check_write_perm({}, "ontology:save", "本体保存")
        assert not ok
        assert "ontology:save" in reason and "本体保存" in reason

    def test_read_level_cannot_write(self):
        role_perms = {"ontology:save": {"ai_access": "read", "ai_note": ""}}
        ok, reason = perm_link.check_write_perm(role_perms, "ontology:save", "本体保存")
        assert not ok
        assert "写操作" in reason

    def test_none_level_rejected(self):
        role_perms = {"ontology:save": {"ai_access": "none", "ai_note": ""}}
        ok, _ = perm_link.check_write_perm(role_perms, "ontology:save", "本体保存")
        assert not ok

    def test_write_level_allowed(self):
        role_perms = {"ontology:save": {"ai_access": "write", "ai_note": ""}}
        ok, reason = perm_link.check_write_perm(role_perms, "ontology:save", "本体保存")
        assert ok and reason == ""

    def test_secret_bound_caps_write_to_none(self):
        # asbot:manage 是涉密硬 deny-list：即使配置 write 也不得执行写动作
        role_perms = {"asbot:manage": {"ai_access": "write", "ai_note": ""}}
        ok, reason = perm_link.check_write_perm(role_perms, "asbot:manage", "AS-BOT 配置")
        assert not ok
        assert "涉密" in reason or "收窄" in reason

    def test_secret_bound_read_only_caps_write(self):
        role_perms = {"datasource:read": {"ai_access": "write", "ai_note": "涉连接信息"}}
        ok, reason = perm_link.check_write_perm(role_perms, "datasource:read", "数据源")
        assert not ok
        assert "收窄" in reason

    def test_empty_role_perms_fail_closed(self):
        assert not perm_link.check_write_perm(None, "ontology:save")[0]
        assert not perm_link.check_write_perm({}, "ontology:save")[0]


class TestRequireWritePerm:
    def _patch_role_perms(self, monkeypatch, role_perms):
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda uid, ws: role_perms)

    def test_raises_permission_error_with_reason(self, monkeypatch):
        self._patch_role_perms(monkeypatch, {})
        with pytest.raises(PermissionError, match="ontology:save"):
            perm_link.require_write_perm(7, 0, "ontology:save", "别名回写")

    def test_passes_with_write_grant(self, monkeypatch):
        self._patch_role_perms(
            monkeypatch, {"ontology:save": {"ai_access": "write", "ai_note": ""}})
        perm_link.require_write_perm(7, 0, "ontology:save", "别名回写")  # 不抛即通过

    def test_read_grant_rejected(self, monkeypatch):
        self._patch_role_perms(
            monkeypatch, {"ontology:save": {"ai_access": "read", "ai_note": ""}})
        with pytest.raises(PermissionError, match="写操作"):
            perm_link.require_write_perm(7, 0, "ontology:save", "别名回写")


# ── 别名直执行端点：403 拒绝 / decided_by 服务端注入 ─────────────────

def _user(uid=7):
    return {"user_id": uid, "role": "admin", "username": "u"}


class TestAliasDirectExecution:
    def test_approve_without_perm_403(self, monkeypatch):
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", lambda *a: {})
        approve = lambda *a, **k: (_ for _ in ()).throw(AssertionError("无权限不得执行"))
        monkeypatch.setattr(alias_suggestion, "approve_suggestion", approve)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(as_bot.approve_alias_suggestion(1, None, _user()))
        assert exc.value.status_code == 403
        assert "ontology:save" in str(exc.value.detail)

    def test_read_level_rejected_403(self, monkeypatch):
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:save": {"ai_access": "read", "ai_note": ""}})
        with pytest.raises(HTTPException) as exc:
            asyncio.run(as_bot.approve_alias_suggestion(1, None, _user()))
        assert exc.value.status_code == 403

    def test_approve_direct_executes_with_server_decider(self, monkeypatch):
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:save": {"ai_access": "write", "ai_note": ""}})
        captured = {}

        def fake_approve(payload):
            captured.update(payload)
            return {"success": True, "term": payload.get("term", "")}

        monkeypatch.setattr(alias_suggestion, "approve_suggestion", fake_approve)
        req = as_bot.AliasDecisionRequest(term="就诊量", target_type="dimension", target_ref="量")
        out = asyncio.run(as_bot.approve_alias_suggestion(7, req, _user(uid=42)))
        assert out["success"] is True
        # decided_by 由服务端注入，与登录用户一致（客户端无法伪造决策人）
        assert captured["decided_by"] == 42
        assert captured["suggestion_id"] == 7
        assert captured["term"] == "就诊量"

    def test_reject_direct_executes_with_server_decider(self, monkeypatch):
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:save": {"ai_access": "write", "ai_note": ""}})
        captured = {}

        def fake_reject(payload):
            captured.update(payload)
            return {"success": True}

        monkeypatch.setattr(alias_suggestion, "reject_suggestion", fake_reject)
        out = asyncio.run(as_bot.reject_alias_suggestion(9, _user(uid=42)))
        assert out["success"] is True
        assert captured["decided_by"] == 42
        assert captured["suggestion_id"] == 9

    def test_writeback_value_error_maps_422(self, monkeypatch):
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:save": {"ai_access": "write", "ai_note": ""}})
        monkeypatch.setattr(alias_suggestion, "approve_suggestion",
                            lambda p: (_ for _ in ()).throw(ValueError("target_ref 必填")))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(as_bot.approve_alias_suggestion(1, None, _user()))
        assert exc.value.status_code == 422


# ── 审计落库：decided_by 写入审计列（不静默丢决策人） ─────────────────

class TestSendPreflightStatusGate:
    """发送前置状态闸：interrupted 不卡死（新轮可发），running/deleting/closed 明确拒绝。"""

    def _req(self):
        return SimpleNamespace(workspace_id=0, pipeline_mode="agent", attachments=None,
                               as_bot_key="", conversation_id=5)

    def _run(self, monkeypatch, status):
        from services.datamind.execution import session_workspace as sw
        from services.datamind.execution import tool_policy
        monkeypatch.setattr(sw, "execute_query", lambda *a, **k: {"status": status})
        monkeypatch.setattr("services.shared.common.auth.authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(sw, "validate_conversation", lambda *a: None)
        monkeypatch.setattr(tool_policy, "resolve_policy",
                            lambda ctx, *a, **k: SimpleNamespace(as_bot={"as_bot_key": "admin"}))
        sw.preflight_request(self._req(), {"user_id": 7})

    def test_interrupted_allows_next_turn(self, monkeypatch):
        """上轮中断不该卡死会话：新消息是新一轮请求（不重放中断轮副作用）。"""
        self._run(monkeypatch, "interrupted")  # 不抛即通过

    def test_running_rejected_with_actionable_reason(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._run(monkeypatch, "running")
        assert exc.value.status_code == 409
        assert "仍在执行" in str(exc.value.detail)  # 原因可直观、带下一步指引

    def test_deleting_and_closed_rejected(self, monkeypatch):
        for status, kw in (("deleting", "删除"), ("closed", "新建会话")):
            with pytest.raises(HTTPException) as exc:
                self._run(monkeypatch, status)
            assert exc.value.status_code == 409 and kw in str(exc.value.detail)


class TestConversationOwnership:
    """会话归属是固有属性：看得到就用得了（互见语义贯穿执行链，不再出现 403 卡死）。"""

    def _run(self, monkeypatch, conv_ws, ctx_ws):
        from services.datamind.execution import session_workspace as sw
        from services.datamind.execution.models import ExecutionContext
        monkeypatch.setattr(sw, "execute_query",
                            lambda *a, **k: {"id": 5, "user_id": 7, "workspace_id": conv_ws,
                                             "as_bot_key": ""})
        monkeypatch.setattr("services.shared.common.auth.authorize_workspace", lambda *a: 0)
        ctx = ExecutionContext(user_id=7, user_role="admin", workspace_id=ctx_ws,
                               extra={"as_bot_key": ""})
        sw.validate_conversation(ctx, 5)
        return ctx

    def test_global_conversation_usable_from_any_entry(self, monkeypatch):
        """全局会话(ws=0)从工作空间入口打开：不再 403，执行归属归一到会话空间。"""
        ctx = self._run(monkeypatch, 0, 301)
        assert ctx.workspace_id == 0

    def test_workspace_conversation_follows_owning_space(self, monkeypatch):
        """工作空间会话跨入口打开：按会话归属空间校验授权并归一执行归属。"""
        ctx = self._run(monkeypatch, 300, 0)
        assert ctx.workspace_id == 300

    def test_foreign_conversation_still_404(self, monkeypatch):
        from services.datamind.execution import session_workspace as sw
        from services.datamind.execution.models import ExecutionContext
        monkeypatch.setattr(sw, "execute_query", lambda *a, **k: None)
        with pytest.raises(HTTPException) as exc:
            sw.validate_conversation(ExecutionContext(user_id=7, user_role="admin",
                                                      workspace_id=0, extra={"as_bot_key": ""}), 5)
        assert exc.value.status_code == 404


class TestToolHandlerCompat:
    """compat.guarded 兼容同步/异步双形态 handler（同步 handler 曾被 `await dict` 炸掉）。"""

    def _call(self, monkeypatch, handler, name="list_datasets",
              qualified="mcp__datahub_functions__list_datasets"):
        from types import SimpleNamespace
        from services.datamind.execution import tool_policy, resource_guard
        from services.datamind.execution.models import ExecutionContext
        from services.datamind.execution.sdk_tools import compat
        from services.datamind.execution.sdk_tools.context import (
            ExecutionContextVar, set_execution_context)
        monkeypatch.setattr(tool_policy, "check_tool", lambda *a, **k: None)
        monkeypatch.setattr(resource_guard, "validate_tool_resources", lambda ctx, n, args: args)
        policy = SimpleNamespace(selection={}, as_bot={}, allowed=frozenset(), unavailable={})
        ctx = ExecutionContext(user_id=7, user_role="admin", workspace_id=3, datasource_id=1,
                               extra={"as_bot_key": "t",
                                      "secure_runtime": SimpleNamespace(policy=policy, tool_tasks=set())})
        token = set_execution_context(ctx)
        try:
            tool = compat.make_tool("qoder", name, "d", {"type": "object"}, handler,
                                    qualified_name=qualified)
            fn = getattr(tool, "handler", tool)  # qoder SdkMcpTool 持回调于 handler
            return asyncio.run(fn({}))
        finally:
            ExecutionContextVar.reset(token)

    def test_sync_handler_executes(self, monkeypatch):
        out = self._call(monkeypatch, lambda args: {"content": [{"type": "text", "text": "ok"}]})
        assert out["content"][0]["text"] == "ok" and not out.get("isError")

    def test_async_handler_executes(self, monkeypatch):
        async def h(args):
            return {"content": [{"type": "text", "text": "ok-async"}]}
        out = self._call(monkeypatch, h)
        assert out["content"][0]["text"] == "ok-async" and not out.get("isError")

class TestDecisionAudit:
    def test_set_status_persists_decided_by(self, monkeypatch):
        from services.shared.common import db
        rows = []
        monkeypatch.setattr(db, "execute_query",
                            lambda sql, params=None: rows.append((sql, params)))
        alias_suggestion._set_status(7, "approved", 42)
        assert len(rows) == 1
        sql, params = rows[0]
        assert "decided_by" in sql
        assert 42 in tuple(params)

    def test_reject_records_decider(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(alias_suggestion, "get_suggestion",
                            lambda sid: {"id": sid, "term": "x", "status": "pending"})
        monkeypatch.setattr(alias_suggestion, "_set_status",
                            lambda sid, status, decider: recorded.update(
                                {"sid": sid, "status": status, "decider": decider}))
        alias_suggestion.reject_suggestion({"suggestion_id": 5, "decided_by": 42})
        assert recorded == {"sid": 5, "status": "rejected", "decider": 42}

    def test_approve_records_decider(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(alias_suggestion, "get_suggestion",
                            lambda sid: {"id": sid, "term": "就诊量", "target_type": "dimension",
                                         "target_ref": "量", "datasource_id": 1})
        monkeypatch.setattr(alias_suggestion, "_approve_dict_alias", lambda *a, **k: None)
        monkeypatch.setattr(alias_suggestion, "_set_status",
                            lambda sid, status, decider: recorded.update(
                                {"sid": sid, "status": status, "decider": decider}))
        alias_suggestion.approve_suggestion({"suggestion_id": 5, "decided_by": 42})
        assert recorded == {"sid": 5, "status": "approved", "decider": 42}


# ── 本体生成工具语义变化：素材化取数（不再 LLM 单发）+ 域约束 fail-closed ─

class TestOntologyGenerateToolSemantics:
    """generate_ontology_draft 已改为素材提供工具（不调 LLM）：返回分批素材与
    归纳指引，归纳由 AS-BOT 会话内 agent 完成后经 save_ontology_draft 提交。
    业务源仅限『生成本体模型』任务绑定会话（fail-closed，不静默回退系统域）。"""

    def _payload(self, result):
        import json
        return json.loads(result["content"][0]["text"])

    def _ctx(self, datasource_id=0, binding=None):
        from services.datamind.execution.models import ExecutionContext
        extra = {"task_binding": binding} if binding else {}
        return ExecutionContext(user_id=7, user_role="admin", workspace_id=3,
                                datasource_id=datasource_id, extra=extra)

    def _run(self, monkeypatch, args, ctx, materials=("M0", "M1")):
        from services.datamind.execution.sdk_tools import ontology_tools as ot
        from services.datamind.execution.sdk_tools.context import (
            ExecutionContextVar, set_execution_context)
        import services.authservice.services.role_service as rs
        import services.datacatalog.services.ontology_service as osvc
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:generate": {"ai_access": "write", "ai_note": ""}})
        monkeypatch.setattr(osvc, "build_generation_batches", lambda ds: list(materials))
        token = set_execution_context(ctx)
        try:
            return asyncio.run(ot.generate_ontology_draft(args))
        finally:
            ExecutionContextVar.reset(token)

    def test_material_provider_not_llm_single_shot(self, monkeypatch):
        """语义变化锁死：返回素材+指引（batch/total_batches），无任何 LLM 生成产物。"""
        p = self._payload(self._run(monkeypatch, {"batch": 0}, self._ctx(0)))
        assert p["material"] == "M0" and p["total_batches"] == 2
        assert "spec" in p and "save_ontology_draft" in p["note"]
        assert "objects" not in p and "model_id" not in p

    def test_business_source_requires_task_binding_session(self, monkeypatch):
        """普通会话给业务源取素材必须拒（任务绑定是服务端注入，LLM 不可伪造）。"""
        p = self._payload(self._run(monkeypatch, {}, self._ctx(5)))
        assert "error" in p and "任务会话" in p["error"]

    def test_task_bound_session_for_other_source_rejected(self, monkeypatch):
        binding = {"kind": "ontology_generate", "datasource_id": 3}
        p = self._payload(self._run(monkeypatch, {}, self._ctx(5, binding=binding)))
        assert "error" in p and "任务会话" in p["error"]

    def test_datasource_arg_rejected(self, monkeypatch):
        """目标源由服务端注入，LLM 传数据源标识显式报错（不静默丢弃）。"""
        p = self._payload(self._run(monkeypatch, {"datasource_id": 7}, self._ctx(0)))
        assert "error" in p and "不接受数据源标识" in p["error"]
