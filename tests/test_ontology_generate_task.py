"""『生成本体模型』任务链路回归 — 端点派发 AS-BOT 会话任务 + 工具提交。

背景：生成本体草案不再走 llm_client 单发（anthropic 直连退场），改为服务端
自动建 AS-BOT 会话 + 派发任务，归纳由会话内 qoder agent 完成（工具不调 LLM）。

锁住的行为：
1. 端点鉴权 fail-closed：datasource_id 必填；无 ontology:generate 权限码、
   数据源未授权、授权集为空一律 403；执行层不可用 503 显式报错（不静默回退）；
2. 任务消息只含数据源**业务名**，内部 id 对 LLM 黑盒；身份/目标源服务端注入；
3. SSE 事件契约仅 progress/done/error（前端零破坏）；done 带 model_id/
   conversation_id/completed，未落库不得假报成功；
4. 工具域约束：业务源草案仅限『生成本体模型』任务绑定会话（fail-closed）；
   无 ontology:generate 权限拒、目标源未授权拒、拒收 datasource_id 参数；
5. save_generated_draft 确定性合并：同身份同主表合并并回带 warnings，
   同名不同主表中止（宁缺勿错）。

纯函数 + fake DB/monkeypatch，不触碰数据库、不派发真实任务。
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

from fastapi import HTTPException


# 目标源内部 id：只允许出现在服务端落库参数里，不得进任务消息（LLM 黑盒）。
DS_ID = 987654321
DS_NAME = "doris_prod"


def _user(uid=7):
    return {"user_id": uid, "username": "u7", "role": "admin"}


def _payload(result):
    """工具返回是 MCP 格式：content[0].text 里是 JSON 字符串。"""
    return json.loads(result["content"][0]["text"])


# ── fake DB：记录 SQL/参数，按语句形态回放结果（不触碰真实库）────────

class _Cursor:
    def __init__(self, ops):
        self.ops = ops
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.ops.append((sql, params))
        s = sql.lower()
        if "from adh_datasources" in s:
            self._row = {"name": DS_NAME}
        elif "from adh_table_info" in s:
            self._row = {"cnt": 42}
        elif "from adh_conversations" in s:
            self._row = {"messages": "[]"}
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    @property
    def lastrowid(self):
        return 501


class _Conn:
    def __init__(self, ops):
        self.ops = ops

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cursor(self.ops)

    def commit(self):
        return None


def _patch_meta_db(monkeypatch):
    """端点侧 fake 元数据库（数据源简报/建会话/回写会话共用）。"""
    ops = []
    import backend.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _Conn(ops))
    return ops


def _patch_draft_db(monkeypatch, existing_objects=None):
    """save_generated_draft 落库段 fake（ontology_service 内部引用）。"""
    import backend.modules.catalog.services.ontology_service as osvc
    ops = []
    monkeypatch.setattr(osvc, "get_metadata_conn", lambda: _Conn(ops))
    monkeypatch.setattr(osvc, "_load_draft_objects",
                        lambda ds: (list(existing_objects or []), "旧域", "旧述"))

    def _fake_get_model(mid):
        for sql, params in reversed(ops):
            if "INSERT INTO adh_ontology_models" in sql:
                return {"id": mid, "object_count": params[8]}
        return {"id": mid, "object_count": 0}

    monkeypatch.setattr(osvc, "get_model", _fake_get_model)
    return ops


def _inserted_doc(ops):
    """取落库 INSERT 的 (doc, params)；未落库显式断言失败。"""
    for sql, params in ops:
        if "INSERT INTO adh_ontology_models" in sql:
            return json.loads(params[5]), params
    raise AssertionError("未执行草案落库 INSERT")


# ═══════════════════════════════════════════════════════════════════
# 端点鉴权 fail-closed
# ═══════════════════════════════════════════════════════════════════

class TestGenerateEndpointFailClosed:
    def _call(self, req, user=None):
        from backend.modules.catalog.api import ontology as ontology_api
        return asyncio.run(ontology_api.generate_ontology(None, req, user or _user()))

    def test_missing_datasource_400(self):
        with pytest.raises(HTTPException) as exc:
            self._call({})
        assert exc.value.status_code == 400

    def test_no_default_workspace_422(self, monkeypatch):
        import backend.common.auth as auth_mod
        monkeypatch.setattr(auth_mod, "resolve_user_default_workspace_id", lambda uid: 0)
        with pytest.raises(HTTPException) as exc:
            self._call({"datasource_id": DS_ID})
        assert exc.value.status_code == 422

    def test_missing_generate_perm_403(self, monkeypatch):
        """无 ontology:generate 权限码必须 403（fail-closed，拒绝可解释）。"""
        import backend.common.auth as auth_mod
        import backend.modules.auth.services.role_service as rs
        monkeypatch.setattr(auth_mod, "resolve_user_default_workspace_id", lambda uid: 5)
        monkeypatch.setattr(auth_mod, "authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", lambda *a: {})
        with pytest.raises(HTTPException) as exc:
            self._call({"datasource_id": DS_ID})
        assert exc.value.status_code == 403
        assert "ontology:generate" in str(exc.value.detail)

    def test_unauthorized_datasource_403(self, monkeypatch):
        """授权集为空不得当全量放行（admin 同样纯角色裁决）。"""
        import backend.common.auth as auth_mod
        import backend.modules.auth.services.role_service as rs
        from backend.modules.mind.execution import perm_link
        monkeypatch.setattr(auth_mod, "resolve_user_default_workspace_id", lambda uid: 5)
        monkeypatch.setattr(auth_mod, "authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [])
        with pytest.raises(HTTPException) as exc:
            self._call({"datasource_id": DS_ID})
        assert exc.value.status_code == 403
        assert "数据源" in str(exc.value.detail)

    def test_datasource_outside_grant_403(self, monkeypatch):
        import backend.common.auth as auth_mod
        import backend.modules.auth.services.role_service as rs
        from backend.modules.mind.execution import perm_link
        monkeypatch.setattr(auth_mod, "resolve_user_default_workspace_id", lambda uid: 5)
        monkeypatch.setattr(auth_mod, "authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [1, 2])
        with pytest.raises(HTTPException) as exc:
            self._call({"datasource_id": DS_ID})
        assert exc.value.status_code == 403

    def test_execution_layer_unavailable_503(self, monkeypatch):
        """执行层不可用必须 fail-loud 503，不做任何静默回退。"""
        import backend.common.auth as auth_mod
        import backend.modules.auth.services.role_service as rs
        from backend.modules.mind.execution import perm_link
        from backend.modules.mind.execution import service as exec_service
        monkeypatch.setattr(auth_mod, "resolve_user_default_workspace_id", lambda uid: 5)
        monkeypatch.setattr(auth_mod, "authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [DS_ID])
        _patch_meta_db(monkeypatch)
        monkeypatch.setattr(exec_service, "get_workspace_layers", lambda ws: [])
        monkeypatch.setattr(exec_service, "get_default_external_layer", lambda: None)
        with pytest.raises(HTTPException) as exc:
            self._call({"datasource_id": DS_ID})
        assert exc.value.status_code == 503
        assert "执行层" in str(exc.value.detail)

    def test_identity_is_server_injected(self, monkeypatch):
        """请求体伪造 created_by/身份无效：会话归属写的是服务端登录用户。"""
        cap = self._run_full(monkeypatch, frames=_DONE_FRAMES, req={
            "datasource_id": DS_ID, "created_by": "hacker", "user_id": 999})
        insert = [p for s, p in cap["ops"] if "insert into adh_conversations" in s.lower()]
        assert insert and insert[0][0] == 7   # user_id=登录用户 7，不是 999

    # ── 全流程公共脚手架（鉴权全放行，聚焦派发/转译）──────────────

    def _run_full(self, monkeypatch, frames, req=None, stream_error=None):
        import backend.common.auth as auth_mod
        import backend.modules.auth.services.role_service as rs
        from backend.modules.catalog.api import ontology as ontology_api
        from backend.modules.mind.execution import perm_link
        from backend.modules.mind.execution import service as exec_service
        from backend.modules.mind.execution import tool_policy

        monkeypatch.setattr(auth_mod, "resolve_user_default_workspace_id", lambda uid: 5)
        monkeypatch.setattr(auth_mod, "authorize_workspace", lambda *a: 0)
        monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [DS_ID])
        monkeypatch.setattr(exec_service, "get_workspace_layers",
                            lambda ws: [{"status": "active", "is_default": True}])
        monkeypatch.setattr(exec_service, "is_healthy_layer", lambda layer: True)
        monkeypatch.setattr(tool_policy, "resolve_policy",
                            lambda ctx: SimpleNamespace(as_bot={"as_bot_key": "admin"}))
        ops = _patch_meta_db(monkeypatch)
        captured = {}

        def fake_stream_query(self, question=None, **kwargs):
            captured["question"] = question
            captured.update(kwargs)

            async def _gen():
                if stream_error:
                    raise RuntimeError(stream_error)
                for f in frames:
                    yield f
            return _gen()

        from backend.modules.mind.services import chat_service
        monkeypatch.setattr(chat_service.ChatService, "stream_query", fake_stream_query)

        async def _call():
            resp = await ontology_api.generate_ontology(
                None, req or {"datasource_id": DS_ID}, _user())
            out = []
            async for chunk in resp.body_iterator:
                out.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
            return "".join(out)

        body = asyncio.run(_call())
        captured["ops"] = ops
        captured["events"] = _parse_events(body)
        return captured


def _frame(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_events(body):
    events = []
    for frame in body.strip().split("\n\n"):
        ev, data = "", {}
        for line in frame.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[6:].strip())
        events.append((ev, data))
    return events


_SAVE_OUTPUT = json.dumps({
    "success": True, "model_id": 501, "object_count": 3, "merged": 1,
    "warnings": ["对象 'case_file' 重复出现, 已合并为一份"]}, ensure_ascii=False)

_DONE_FRAMES = [
    _frame("token", {"text": "开始归纳\n"}),
    _frame("tool_start", {"tool": "mcp__datahub_ontology__save_ontology_draft"}),
    _frame("tool_result", {"tool": "mcp__datahub_ontology__save_ontology_draft",
                           "output": _SAVE_OUTPUT}),
    _frame("done", {"reply": "归纳完成", "tool_calls": [{"tool": "save_ontology_draft"}]}),
]


# ═══════════════════════════════════════════════════════════════════
# 任务消息：业务名进 prompt，内部 id 对 LLM 黑盒
# ═══════════════════════════════════════════════════════════════════

class TestTaskMessage:
    def test_message_carries_business_name_not_internal_id(self):
        from backend.modules.catalog.api import ontology as ontology_api
        msg = ontology_api._build_task_message(DS_NAME, 42)
        assert DS_NAME in msg and "42" in msg
        assert "save_ontology_draft" in msg and "append=false" in msg
        # 内部 id 与物理列名都不得进 prompt（黑盒）
        assert str(DS_ID) not in msg
        assert "datasource_id" not in msg

    def test_dispatch_flow_keeps_internal_id_out_of_message(self, monkeypatch):
        """端到端：建会话落的任务消息同样只有业务名，id 只进服务端落库参数。"""
        cap = TestGenerateEndpointFailClosed()._run_full(monkeypatch, frames=_DONE_FRAMES)
        insert = [p for s, p in cap["ops"] if "insert into adh_conversations" in s.lower()]
        assert insert, "未创建任务会话"
        user_id, title, ds_id, ws, as_bot_key, messages = insert[0][:6]
        assert ds_id == DS_ID and ws == 5 and as_bot_key == "admin"   # 服务端注入
        assert DS_NAME in title
        task_message = json.loads(messages)[0]["content"]
        assert DS_NAME in task_message
        assert str(DS_ID) not in task_message
        # 任务派发带着任务绑定标记（工具侧业务源写入仅限该会话）
        assert cap["task_binding"] == {"kind": "ontology_generate", "datasource_id": DS_ID}
        assert cap["pipeline_mode"] == "agent" and cap["as_bot_key"] == "admin"
        assert cap["question"] == task_message


# ═══════════════════════════════════════════════════════════════════
# SSE 事件契约（前端只识别 progress/done/error，零破坏）
# ═══════════════════════════════════════════════════════════════════

class TestSseEventContract:
    def test_only_contract_events_emitted(self, monkeypatch):
        cap = TestGenerateEndpointFailClosed()._run_full(monkeypatch, frames=_DONE_FRAMES)
        names = {ev for ev, _ in cap["events"]}
        assert names <= {"progress", "done", "error"}
        assert "done" in names

    def test_done_carries_model_and_conversation(self, monkeypatch):
        cap = TestGenerateEndpointFailClosed()._run_full(monkeypatch, frames=_DONE_FRAMES)
        done = [d for ev, d in cap["events"] if ev == "done"]
        assert len(done) == 1
        assert done[0]["model_id"] == 501
        assert done[0]["object_count"] == 3
        assert done[0]["merged"] == 1
        assert done[0]["warnings"] and "case_file" in done[0]["warnings"][0]
        assert done[0]["conversation_id"] == 501
        assert done[0]["workspace_id"] == 5
        assert done[0]["completed"] is True

    def test_progress_translates_agent_and_tools(self, monkeypatch):
        cap = TestGenerateEndpointFailClosed()._run_full(monkeypatch, frames=_DONE_FRAMES)
        details = [d.get("detail", "") for ev, d in cap["events"] if ev == "progress"]
        assert any("开始归纳" in d for d in details)                 # token → progress 行
        assert any("save_ontology_draft" in d for d in details)      # 工具调用可见

    def test_no_save_result_is_not_reported_completed(self, monkeypatch):
        """agent 跑完但没提交草案：不得假报成功（completed=false 由前端显式提示）。"""
        frames = [_frame("token", {"text": "想了想\n"}),
                  _frame("done", {"reply": "没提交", "tool_calls": []})]
        cap = TestGenerateEndpointFailClosed()._run_full(monkeypatch, frames=frames)
        done = [d for ev, d in cap["events"] if ev == "done"]
        assert done and done[0]["model_id"] is None
        assert done[0]["completed"] is False

    def test_dispatch_failure_emits_error_with_conversation(self, monkeypatch):
        cap = TestGenerateEndpointFailClosed()._run_full(
            monkeypatch, frames=[], stream_error="boom")
        errors = [d for ev, d in cap["events"] if ev == "error"]
        assert not [d for ev, d in cap["events"] if ev == "done"]   # 失败不得再发 done
        assert errors and "生成任务派发失败" in errors[0]["message"]
        assert errors[0]["conversation_id"] == 501
        assert errors[0]["workspace_id"] == 5

    def test_reply_is_persisted_to_conversation(self, monkeypatch):
        """执行回复回写会话历史（聊天侧可回看/追问）。"""
        cap = TestGenerateEndpointFailClosed()._run_full(monkeypatch, frames=_DONE_FRAMES)
        updates = [p for s, p in cap["ops"] if s.lower().startswith("update adh_conversations")]
        assert updates, "执行回复未回写会话"
        reply = json.loads(updates[0][0])
        assert reply[-1]["role"] == "assistant" and reply[-1]["reply"] == "归纳完成"


# ═══════════════════════════════════════════════════════════════════
# 工具域约束：assert_ontology_draft_scope（业务源仅限任务绑定会话）
# ═══════════════════════════════════════════════════════════════════

def _ctx(datasource_id=0, binding=None, username="u7"):
    from backend.modules.mind.execution.models import ExecutionContext
    extra = {}
    if binding is not None:
        extra["task_binding"] = binding
    return ExecutionContext(user_id=7, user_role="admin", workspace_id=3,
                            datasource_id=datasource_id, username=username, extra=extra)


class TestDraftScope:
    def test_system_domain_always_allowed(self):
        from backend.modules.mind.execution.resource_guard import assert_ontology_draft_scope
        assert assert_ontology_draft_scope(_ctx(0)) == 0

    def test_business_source_without_task_binding_rejected(self):
        from backend.modules.mind.execution.resource_guard import (
            ResourceScopeError, assert_ontology_draft_scope)
        with pytest.raises(ResourceScopeError, match="任务会话"):
            assert_ontology_draft_scope(_ctx(5))

    def test_task_binding_mismatch_rejected(self):
        from backend.modules.mind.execution.resource_guard import (
            ResourceScopeError, assert_ontology_draft_scope)
        binding = {"kind": "ontology_generate", "datasource_id": 3}
        with pytest.raises(ResourceScopeError):
            assert_ontology_draft_scope(_ctx(5, binding=binding))

    def test_task_binding_but_unauthorized_source_rejected(self, monkeypatch):
        """授权集为空 fail-closed：有任务绑定也不得越权写源草案。"""
        import backend.modules.auth.services.role_service as rs
        from backend.modules.mind.execution.resource_guard import (
            ResourceScopeError, assert_ontology_draft_scope)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [])
        binding = {"kind": "ontology_generate", "datasource_id": 5}
        with pytest.raises(ResourceScopeError, match="无权使用该数据源"):
            assert_ontology_draft_scope(_ctx(5, binding=binding))

    def test_task_binding_authorized_passes(self, monkeypatch):
        import backend.modules.auth.services.role_service as rs
        from backend.modules.mind.execution.resource_guard import assert_ontology_draft_scope
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [5])
        binding = {"kind": "ontology_generate", "datasource_id": 5}
        assert assert_ontology_draft_scope(_ctx(5, binding=binding)) == 5


# ═══════════════════════════════════════════════════════════════════
# save_ontology_draft 工具：权限拒 / 参数拒 / 确定性合并落库
# ═══════════════════════════════════════════════════════════════════

class TestSaveDraftTool:
    def _run(self, monkeypatch, args, ctx):
        from backend.modules.mind.execution.sdk_tools import ontology_tools as ot
        from backend.modules.mind.execution.sdk_tools.context import (
            ExecutionContextVar, set_execution_context)
        token = set_execution_context(ctx)
        try:
            return asyncio.run(ot.save_ontology_draft(args))
        finally:
            ExecutionContextVar.reset(token)

    def _grant_generate(self, monkeypatch):
        import backend.modules.auth.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:generate": {"ai_access": "write", "ai_note": ""}})

    def test_missing_generate_perm_rejected(self, monkeypatch):
        import backend.modules.auth.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", lambda *a: {})
        p = _payload(self._run(monkeypatch, {"objects": [{"key": "k", "primary_table": "t"}]},
                               _ctx(0)))
        assert "error" in p and "ontology:generate" in p["error"]

    def test_datasource_arg_rejected(self, monkeypatch):
        """LLM 传数据源标识必须显式报错（目标源服务端注入，不静默丢弃）。"""
        self._grant_generate(monkeypatch)
        p = _payload(self._run(monkeypatch, {
            "objects": [{"key": "k", "primary_table": "t"}], "datasource_id": 7}, _ctx(0)))
        assert "error" in p and "不接受数据源标识" in p["error"]

    def test_business_source_unauthorized_rejected(self, monkeypatch):
        """目标源未授权拒（fail-closed：空授权集不放行）。"""
        import backend.modules.auth.services.role_service as rs
        self._grant_generate(monkeypatch)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [])
        binding = {"kind": "ontology_generate", "datasource_id": 5}
        p = _payload(self._run(monkeypatch, {"objects": [{"key": "k", "primary_table": "t"}]},
                               _ctx(5, binding=binding)))
        assert "error" in p and "无权使用该数据源" in p["error"]

    def test_append_merges_duplicates_with_warnings(self, monkeypatch):
        """同身份同主表 → 确定性合并，合并事实随 warnings 显式带回。"""
        import backend.modules.auth.services.role_service as rs
        import backend.modules.catalog.services.ontology_service as osvc
        self._grant_generate(monkeypatch)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [5])
        ops = _patch_draft_db(monkeypatch)
        binding = {"kind": "ontology_generate", "datasource_id": 5}
        p = _payload(self._run(monkeypatch, {
            "objects": [
                {"key": "case_file", "primary_table": "t_case_files",
                 "properties": [{"column": "a", "name": "甲"}], "metrics": []},
                {"key": "casefile", "primary_table": "t_case_files",
                 "properties": [], "metrics": [{"name": "m1", "formula": "x", "description": ""}]},
            ],
            "domain": "案例域", "append": False,
        }, _ctx(5, binding=binding, username="u7")))
        assert p.get("success") is True and p["model_id"]
        assert p["merged"] == 1 and p["warnings"]
        doc, params = _inserted_doc(ops)
        assert len(doc["objects"]) == 1                       # 判重合并成一份
        assert doc["objects"][0]["metrics"] and doc["objects"][0]["properties"]
        assert params[2] == "source" and params[9] == "u7"    # kind/created_by 服务端注入

    def test_same_key_different_table_aborts(self, monkeypatch):
        """同名不同主表是真冲突：中止落库（宁缺勿错），不得静默二选一。"""
        import backend.modules.auth.services.role_service as rs
        self._grant_generate(monkeypatch)
        monkeypatch.setattr(rs.role_service, "get_user_allowed_datasources", lambda *a: [5])
        ops = _patch_draft_db(monkeypatch)
        binding = {"kind": "ontology_generate", "datasource_id": 5}
        p = _payload(self._run(monkeypatch, {
            "objects": [
                {"key": "case_file", "primary_table": "t_case_files", "properties": []},
                {"key": "casefile", "primary_table": "t_other_files", "properties": []},
            ], "append": False}, _ctx(5, binding=binding)))
        assert "error" in p and "同名不同主表" in p["error"]
        assert not [s for s, _ in ops if "INSERT INTO adh_ontology_models" in s]


# ═══════════════════════════════════════════════════════════════════
# generate_ontology_draft 工具：素材提供（不调 LLM）
# ═══════════════════════════════════════════════════════════════════

class TestGenerateDraftTool:
    def _run(self, monkeypatch, args, ctx, materials=("M0", "M1")):
        import backend.modules.auth.services.role_service as rs
        import backend.modules.catalog.services.ontology_service as osvc
        from backend.modules.mind.execution.sdk_tools import ontology_tools as ot
        from backend.modules.mind.execution.sdk_tools.context import (
            ExecutionContextVar, set_execution_context)
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"ontology:generate": {"ai_access": "write", "ai_note": ""}})
        monkeypatch.setattr(osvc, "build_generation_batches", lambda ds: list(materials))
        token = set_execution_context(ctx)
        try:
            return asyncio.run(ot.generate_ontology_draft(args))
        finally:
            ExecutionContextVar.reset(token)

    def test_returns_material_not_llm_result(self, monkeypatch):
        """语义变化锁死：返回的是分批素材+归纳指引，不是 LLM 生成结果。"""
        p = _payload(self._run(monkeypatch, {"batch": 1}, _ctx(0)))
        assert p["material"] == "M1" and p["total_batches"] == 2
        assert "spec" in p and "save_ontology_draft" in p["note"]
        assert "model_id" not in p and "objects" not in p   # 无落库/归纳产物

    def test_batch_out_of_range_rejected(self, monkeypatch):
        p = _payload(self._run(monkeypatch, {"batch": 5}, _ctx(0)))
        assert "error" in p and "batch 越界" in p["error"]

    def test_business_source_requires_task_session(self, monkeypatch):
        """普通会话给业务源取素材必须拒（fail-closed，不回退系统域）。"""
        p = _payload(self._run(monkeypatch, {}, _ctx(5)))
        assert "error" in p and "任务会话" in p["error"]

    def test_datasource_arg_rejected(self, monkeypatch):
        p = _payload(self._run(monkeypatch, {"datasource_id": 7}, _ctx(0)))
        assert "error" in p and "不接受数据源标识" in p["error"]
