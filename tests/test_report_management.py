"""报表中心「管理报表」回归：内容型报表的可见性、增删改、会话存报表、AI 存报表。

口径（report_access）：
* 数据派生（scheduled/analysis）—— 正文由受治理取数产出，必须过权限快照，不可在线编辑；
* 内容型（manual/conversation/legacy）—— 人写或会话里已展示给属主本人的内容，
  属主应用内实时查看，一律不参与外链/公开分享。
"""
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.modules.viz.services import report_access as access
from backend.modules.viz.services import report_service as reports


def _content_report(**over):
    row = {"id": 1, "owner_id": 7, "workspace_id": 3, "source_type": "manual",
           "generation_status": "ready", "publication_status": "draft",
           "content": "# 报表\n正文", "format": "markdown", "access_mode": "private",
           "security_context": None, "share_revoked": 1}
    row.update(over)
    return row


OWNER = {"user_id": 7, "role": "analyst"}
OTHER = {"user_id": 8, "role": "analyst"}


@pytest.fixture(autouse=True)
def no_workspace_http(monkeypatch):
    """属主自检路径里 authorize_workspace 只做工作空间归属，测试里放行即可。"""
    monkeypatch.setattr(access, "authorize_workspace", lambda user, ws: ws)


# ── 1. 可见性 ────────────────────────────────────────────────────────────────

def test_content_report_owner_views_in_app_without_snapshot():
    row = _content_report()
    assert access.can_access_report(row, OWNER)
    assert not access.can_access_report(row, OTHER)
    assert not access.can_access_report(row)          # 匿名不放


@pytest.mark.parametrize("source", ["manual", "conversation", "legacy"])
def test_content_report_never_shareable(source):
    row = _content_report(source_type=source, publication_status="published",
                          access_mode="public", security_context={"version": 1, "share_eligible": True})
    assert not access.can_access_report(row, token="whatever")
    assert not access.can_access_report(row)


def test_legacy_history_report_is_owner_viewable():
    """M2 之前的存量报表没有权限快照，过去属主自己也打不开（报表中心成空壳）。"""
    row = _content_report(source_type="legacy", generation_status="legacy")
    assert access.can_access_report(row, OWNER)
    assert not access.can_access_report(row, OTHER)


def test_empty_body_content_report_not_served():
    assert not access.can_access_report(_content_report(content="   "), OWNER)


def test_missing_source_type_keeps_snapshot_requirement(monkeypatch):
    """来源未知按数据派生处理：不放宽快照要求（防把受治理报告当人写内容放行）。"""
    monkeypatch.setattr(access, "snapshot_is_current", lambda row: False)
    row = _content_report()
    row.pop("source_type")
    assert not access.can_access_report(row, OWNER)


def test_data_derived_report_still_requires_snapshot(monkeypatch):
    monkeypatch.setattr(access, "snapshot_is_current", lambda row: False)
    assert not access.can_access_report(_content_report(source_type="scheduled"), OWNER)
    monkeypatch.setattr(access, "snapshot_is_current", lambda row: True)
    assert access.can_access_report(_content_report(source_type="scheduled"), OWNER)


def test_projection_exposes_source_and_never_leaks_internals():
    row = _content_report(source_type="conversation", updated_at=None,
                          access_token="legacy-secret",
                          security_context={"policy_digest": "x"},
                          analysis_source={"origin": {"channel": "chat", "conversation_id": 9},
                                           "intent": {"object": "订单"}})
    out = access.public_report(row)
    assert out["source_type"] == "conversation"
    assert out["origin"] == {"channel": "chat", "conversation_id": 9}
    for forbidden in ("access_token", "security_context", "share_token_hash", "intent", "datasource_id"):
        assert forbidden not in json.dumps(out, ensure_ascii=False, default=str)


# ── 2. 内容型报表的增删改 ─────────────────────────────────────────────────────

@pytest.fixture
def insert_capture(monkeypatch):
    """捕获 INSERT/UPDATE/DELETE，不连真库。"""
    statements = []

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): statements.append((sql, params))
        def fetchone(self): return None
        def fetchall(self): return []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cursor()

    monkeypatch.setattr(reports, "DBConnection", lambda: _Conn())
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): statements.append((sql, params)) or 1)
    return statements


@pytest.mark.parametrize("kwargs", [
    {"title": "  ", "content": "正文"},
    {"title": "报表", "content": "   "},
    {"title": "报表", "content": "正文", "format": "pdf"},
    {"title": "报表", "content": "正文", "source_type": "scheduled"},
    {"title": "报表", "content": "正文", "source_type": "legacy"},
])
def test_create_content_report_rejects_invalid(insert_capture, kwargs):
    with pytest.raises(ValueError):
        reports.create_content_report(workspace_id=3, owner_id=7, **kwargs)
    assert not [s for s in insert_capture if "INSERT" in s[0]]


def test_create_content_report_requires_owner(insert_capture):
    with pytest.raises(PermissionError):
        reports.create_content_report(title="报表", content="正文", workspace_id=3, owner_id=0)


def test_create_content_report_persists_ready_private_content_row(insert_capture):
    out = reports.create_content_report(title="新加坡近30天案例", content="# 结论",
                                       workspace_id=3, owner_id=7, source_type="manual",
                                       origin={"channel": "report_center"})
    sql, params = insert_capture[0]
    assert "INSERT INTO adh_reports" in sql
    assert out["generation_status"] == "ready" and out["source_type"] == "manual"
    assert "'private'" in sql and "'ready','draft'" in sql      # 私有 + 未发布
    assert "manual" in params and json.dumps({"origin": {"channel": "report_center"}},
                                            ensure_ascii=False) in params


def test_update_content_report_rejects_data_derived(monkeypatch):
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="scheduled"))
    with pytest.raises(ValueError) as exc:
        reports.update_content_report(1, OWNER, content="改一下")
    assert "不可在线编辑" in str(exc.value)


def test_legacy_report_is_viewable_but_read_only(monkeypatch):
    """历史报表（任务取数产出）可看可删不可改 —— 改它等于篡改取数结果。"""
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="legacy", generation_status="legacy"))
    assert access.can_access_report(_content_report(source_type="legacy", generation_status="legacy"), OWNER)
    with pytest.raises(ValueError) as exc:
        reports.update_content_report(1, OWNER, content="改写历史")
    assert "只读可查不可改" in str(exc.value)
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    reports.delete_report(1, OWNER)          # 清理存量历史报表是允许的
    assert written and written[0][0].startswith("DELETE")


def test_update_content_report_requires_owner(monkeypatch):
    monkeypatch.setattr(reports, "load_report", lambda rid: _content_report())
    with pytest.raises(PermissionError):
        reports.update_content_report(1, OTHER, title="改名")
    with pytest.raises(PermissionError):
        reports.update_content_report(1, {}, title="改名")


def test_update_content_report_writes_title_and_timestamp(monkeypatch):
    monkeypatch.setattr(reports, "load_report", lambda rid: _content_report())
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    reports.update_content_report(1, OWNER, title="新标题")
    sql, params = written[0]
    assert "SET updated_at=%s,title=%s" in sql and "owner_id=%s" in sql
    assert params[-2:] == (1, 7)                                # 属主条件写死在 WHERE


def test_delete_report_owner_only_and_data_derived_guard(monkeypatch):
    monkeypatch.setattr(reports, "load_report", lambda rid: _content_report())
    with pytest.raises(PermissionError):
        reports.delete_report(1, OTHER)
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="analysis"))
    with pytest.raises(ValueError):
        reports.delete_report(1, OWNER)
    monkeypatch.setattr(reports, "load_report", lambda rid: _content_report())
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    reports.delete_report(1, OWNER)
    assert written[0][0].startswith("DELETE FROM adh_reports WHERE id=%s AND owner_id=%s")


# ── 3. 会话存报表（正文只从服务端已落库会话读）─────────────────────────────────

def _conv(messages):
    return {"id": 9, "workspace_id": 3, "title": "查看最近7天的案例", "messages": messages}


@pytest.fixture
def conversation(monkeypatch):
    holder = {"row": None}
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), fetchone=False: holder["row"] if "adh_conversations" in sql else None)
    created = []
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.append(kw) or {"report_id": 55, "title": kw["title"]})
    return holder, created


def test_save_conversation_report_reads_server_side_message(conversation):
    holder, created = conversation
    holder["row"] = _conv([
        {"role": "user", "content": "近7天案例数"},
        {"role": "assistant", "content": "共 **12** 单", "question": "近7天案例数",
         "result": {"columns": [{"name": "案例数"}], "rows": [{"案例数": 12}]}},
    ])
    out = reports.save_conversation_report(user=OWNER, conversation_id=9, message_index=1)
    assert out["report_id"] == 55
    saved = created[0]
    assert saved["source_type"] == "conversation" and saved["workspace_id"] == 3
    assert saved["owner_id"] == 7
    assert "共 **12** 单" in saved["content"] and "| 案例数 |" in saved["content"]
    assert saved["origin"]["conversation_id"] == 9


def test_save_conversation_report_refuses_non_analysis_turn(conversation):
    """“已记住…”这类系统操作确认不是报表：服务端同样拒，不给绕过前端的入口。"""
    holder, created = conversation
    holder["row"] = _conv([{
        "role": "assistant", "question": "默认用近3个月",
        "content": "已记住：您一般查询数据时默认使用近 3 个月的时间范围。",
    }])
    with pytest.raises(ValueError) as exc:
        reports.save_conversation_report(user=OWNER, conversation_id=9, message_index=1)
    assert "不是数据分析结果" in str(exc.value)
    assert created == []


def test_save_conversation_report_rejects_other_users_conversation(conversation):
    holder, created = conversation
    holder["row"] = None            # 按 user_id 过滤后取不到
    with pytest.raises(PermissionError):
        reports.save_conversation_report(user=OWNER, conversation_id=9)
    assert created == []


def test_save_conversation_report_rejects_infrastructure_leak(conversation):
    holder, created = conversation
    holder["row"] = _conv([{"role": "assistant", "sql": "select 1",
                            "content": "select order_id from crm.t_order limit 10"}])
    with pytest.raises(ValueError) as exc:
        reports.save_conversation_report(user=OWNER, conversation_id=9)
    assert "不能存为报表" in str(exc.value)
    assert created == []


def test_save_conversation_report_without_answer_is_explicit(conversation):
    holder, _ = conversation
    holder["row"] = _conv([{"role": "user", "content": "hi"}])
    with pytest.raises(ValueError):
        reports.save_conversation_report(user=OWNER, conversation_id=9)
    with pytest.raises(ValueError):
        reports.save_conversation_report(user=OWNER, conversation_id=0)


def test_render_conversation_report_caps_rows_and_labels_source():
    md = reports.render_conversation_report(
        question="近30天新增", answer="结论：稳步上升",
        result={"columns": ["日期", "数量"], "rows": [{"日期": f"d{i}", "数量": i} for i in range(260)]})
    assert "> 提问：近30天新增" in md and "结论：稳步上升" in md
    assert md.count("\n| d") == 200 and "仅展示前 200 行" in md


# ── 3b. 哪些回合算报表（单一口径，前端只消费）────────────────────────────────

@pytest.mark.parametrize("turn,expected", [
    ({"result": {"columns": ["案例数"], "rows": [{"案例数": 1}]}}, True),      # 内置结果卡
    ({"sql": "SELECT 1"}, True),                                              # 跑过查询
    ({"chart_type": "timeseries_line"}, True),                                # 图表
    ({"reply": "结论如下\n```chart\n{}\n```"}, True),                          # 内嵌图表块
    ({"tool_calls": [{"tool": "mcp__datahub_query__execute_sql"}]}, True),     # 取数工具
    ({"tool_calls": [{"tool": "mcp__datahub_semantic__run_semantic_query"}]}, True),
    ({"content": "已记住：您默认使用近 3 个月。"}, False),                        # 系统操作确认
    ({"content": "我能用哪些 skills：检索、取数、建模…"}, False),                  # 纯问答
    ({"tool_calls": [{"tool": "mcp__datahub_memory__save_memory"}]}, False),      # 非取数工具
    ({"content": "执行层执行失败", "error": "boom"}, False),                       # 失败回合
], ids=["result", "sql", "chart_type", "chart_block", "execute_sql", "semantic",
        "memory_confirm", "chitchat", "non_data_tool", "error"])
def test_turn_reportability_single_source(turn, expected):
    assert reports.turn_reportability(turn)[0] is expected


def test_non_reportable_reason_tells_user_the_way_out():
    ok, reason = reports.turn_reportability({"content": "已记住偏好"})
    assert not ok and "不是数据分析结果" in reason and "新建报表" in reason


def test_done_event_and_history_share_the_same_verdict():
    """done 事件与会话回放必须给同一个 reportable（否则刷新前后按钮会飘）。"""
    from backend.modules.mind.services.chat_service import _sse_event
    turn = {"intent": "agent", "reply": "共 12 单",
            "result": {"columns": ["案例数"], "rows": [{"案例数": 12}]}}
    payload = json.loads(_sse_event("done", dict(turn)).decode().split("data: ", 1)[1])
    assert payload["reportable"] is True
    plain = json.loads(_sse_event("thinking", {"text": "x"}).decode().split("data: ", 1)[1])
    assert "reportable" not in plain                       # 不污染其他事件
    assert reports.with_reportability(turn)["reportable"] == payload["reportable"]
    chat_turn = {"role": "assistant", "content": "已记住：默认近 3 个月"}
    assert reports.with_reportability(chat_turn)["reportable"] is False


# ── 4. 清单：报表跟随属主，跨工作站；任务名 JOIN 显示 ──────────────────────────

@pytest.fixture
def list_query(monkeypatch):
    seen = []

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            seen.append((sql, params))
            return None
        def fetchone(self): return {"total": 1}
        def fetchall(self): return [{"id": 1, "title": "每日案例 - 2026-10-01", "content": "x",
                                     "task_id": 88, "task_name": "每日案例统计",
                                     "source_type": "scheduled", "generation_status": "ready",
                                     "owner_id": 7, "workspace_id": 0}]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cursor()

    monkeypatch.setattr(reports, "DBConnection", lambda: _Conn())
    return seen


def test_list_reports_is_owner_scoped_across_workspaces(list_query):
    out = reports.list_reports(7, 0, user={"user_id": 7})
    sql, params = list_query[1]
    assert "r.owner_id = %s" in sql and "workspace_id = %s" not in sql.split("WHERE")[1]
    assert params == [7, 20, 0]
    assert out["items"][0]["task_name"] == "每日案例统计"      # 显示用 name，不露裸 id
    assert out["items"][0]["source_type"] == "scheduled"


def test_list_reports_narrows_when_workspace_given(list_query):
    reports.list_reports(7, 301, user={"user_id": 7})
    sql, params = list_query[1]
    assert "r.workspace_id = %s" in sql and params[:2] == [7, 301]


def test_list_reports_without_trusted_identity_refused():
    with pytest.raises(PermissionError):
        reports.list_reports(7, 3, user={"user_id": 8})
    with pytest.raises(PermissionError):
        reports.list_reports(7, 3, user=None)


# ── 5. REST 面（新建/编辑/删除/详情）───────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    from fastapi import FastAPI
    from backend.modules.viz.api import report as report_api
    from backend.common.auth import get_current_user
    app = FastAPI()
    app.include_router(report_api.router, prefix="/api/reports")
    app.dependency_overrides[get_current_user] = lambda: dict(OWNER)
    monkeypatch.setattr(report_api, "authorize_workspace", lambda user, ws: ws)
    return TestClient(app)


def test_api_create_manual_report(api, monkeypatch):
    client = api
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return {"report_id": 77}
    monkeypatch.setattr(reports, "create_content_report", fake)
    res = client.post("/api/reports", json={"workspace_id": 3, "title": "手写报表",
                                           "content": "正文", "source_type": "manual"})
    assert res.status_code == 201 and res.json()["report_id"] == 77
    assert captured["owner_id"] == 7 and captured["source_type"] == "manual"


def test_api_create_rejects_unknown_source_and_extra_fields(api):
    client = api
    res = client.post("/api/reports", json={"workspace_id": 3, "title": "t", "content": "c",
                                           "source_type": "scheduled"})
    assert res.status_code == 422
    res = client.post("/api/reports", json={"workspace_id": 3, "title": "t", "owner_id": 99})
    assert res.status_code == 422                      # extra=forbid：不接受 body 传身份


def test_api_conversation_save_maps_errors(api, monkeypatch):
    client = api
    monkeypatch.setattr(reports, "save_conversation_report",
                        Mock(side_effect=PermissionError("会话不存在或不属于当前用户")))
    res = client.post("/api/reports", json={"conversation_id": 9, "source_type": "conversation"})
    assert res.status_code == 403 and "不属于当前用户" in res.json()["detail"]


def test_api_update_and_delete_are_owner_gated(api, monkeypatch):
    client = api
    monkeypatch.setattr(reports, "update_content_report", Mock(return_value={"report_id": 1}))
    assert client.put("/api/reports/1", json={"title": "新名"}).status_code == 200
    monkeypatch.setattr(reports, "update_content_report",
                        Mock(side_effect=ValueError("不可在线编辑")))
    assert client.put("/api/reports/1", json={"content": "x"}).status_code == 422
    monkeypatch.setattr(reports, "delete_report",
                        Mock(side_effect=PermissionError("只能管理自己创建的报表")))
    assert client.delete("/api/reports/1").status_code == 403


def test_api_detail_returns_body_for_owner(api, monkeypatch):
    client = api
    monkeypatch.setattr(reports, "get_report",
                        lambda rid, access_token=None, user=None: {"id": rid, "content": "正文",
                                                                   "source_type": "manual"})
    res = client.get("/api/reports/1/detail")
    assert res.status_code == 200 and res.json()["content"] == "正文"
    monkeypatch.setattr(reports, "get_report", lambda *a, **kw: None)
    assert client.get("/api/reports/1/detail").status_code == 404


# ── 6. AI 通道：save_report 工具 ───────────────────────────────────────────────

def _tool_payload(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def as_bot(monkeypatch):
    from backend.modules.mind.execution import function_tools as ft
    from backend.modules.mind.execution.models import ExecutionContext
    from backend.modules.mind.execution.sdk_tools.context import ExecutionContextVar
    from backend.common import auth
    token = ExecutionContextVar.set(ExecutionContext(user_id=7, workspace_id=3))
    monkeypatch.setattr(auth, "resolve_execution_owner",
                        lambda uid, ws: {"user_id": uid, "workspace_id": ws, "role": "analyst"})
    created = []
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.append(kw) or {"report_id": 66, "title": kw["title"],
                                                            "generation_status": "ready"})
    try:
        yield ft, created
    finally:
        ExecutionContextVar.reset(token)


def test_ai_save_report_writes_owner_scoped_content_report(as_bot):
    ft, created = as_bot
    out = _tool_payload(ft._save_report({"title": "近7天案例结论", "content": "本周案例 12 单，环比 +3"}))
    assert out["report_id"] == 66
    assert created[0]["owner_id"] == 7 and created[0]["workspace_id"] == 3
    assert created[0]["source_type"] == "conversation"
    assert created[0]["origin"] == {"channel": "as_bot"}
    assert "正文" not in out and "content" not in out       # 不回正文，只回标识与状态


def test_ai_save_report_rejects_infrastructure_leak(as_bot):
    ft, created = as_bot
    out = _tool_payload(ft._save_report({"title": "t",
                                        "content": "select a from crm.t_order"}))
    assert out["error"] and created == []
    assert "业务名称" in out["error"]


@pytest.mark.parametrize("args", [{"content": "正文"}, {"title": "标题"}])
def test_ai_save_report_requires_title_and_body(as_bot, args):
    ft, created = as_bot
    assert _tool_payload(ft._save_report(args))["error"] and created == []


def test_ai_save_report_fails_closed_without_identity(monkeypatch):
    from backend.modules.mind.execution import function_tools as ft
    from backend.modules.mind.execution.sdk_tools.context import ExecutionContextVar
    token = ExecutionContextVar.set(None)
    try:
        assert _tool_payload(ft._save_report({"title": "t", "content": "c"}))["error"]
    finally:
        ExecutionContextVar.reset(token)


def test_save_report_action_is_a_write_action_on_report_manage():
    from backend.modules.mind.execution import function_tools as ft
    spec = ft.FUNCTION_ACTION_SPECS["report.save"]
    assert spec["perm_code"] == "report:manage" and spec["read_only"] is False
    assert "datasource_id" not in (spec["schema"]["properties"])
    assert set(spec["schema"]["required"]) == {"title", "content"}


def test_ai_report_list_is_owner_scoped_view(as_bot, monkeypatch):
    ft, _ = as_bot
    seen = []
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), fetchone=False: seen.append((sql, params)) or [])
    ft._list_reports({})
    sql, params = seen[0]
    assert "owner_id = %s" in sql and params[0] == 7          # 不跨用户递别人报表标题
    assert "SELECT *" not in sql.upper()
