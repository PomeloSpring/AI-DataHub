"""报告中心「管理报告」回归：内容型报告的可见性、增删改、会话存报告、AI 存报告。

口径（report_access）：
* 数据派生（scheduled/analysis）—— 正文由受治理取数产出，必须过权限快照，不可在线编辑；
* 内容型（manual/conversation/legacy）—— 人写或会话里已展示给属主本人的内容，
  属主应用内实时查看，一律不参与外链/公开分享；
* 正文本身在对象存储里（adh_reports 只存 content_object_key），所以本文件用内存桶
  替身（fake_storage）跑，绝不碰真对象存储，也不碰真库。
"""
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.modules.viz.services import report_access as access
from backend.modules.viz.services import report_service as reports


def _content_report(**over):
    """报告行形状：正文在对象存储（key 非空），库里 content 恒空串。"""
    row = {"id": 1, "owner_id": 7, "workspace_id": 3, "source_type": "manual",
           "generation_status": "ready", "publication_status": "draft",
           "content": "", "content_object_key": "ai-datahub/7/reports/1", "content_size": 20,
           "format": "markdown", "access_mode": "private",
           "security_context": None, "share_revoked": 1}
    row.update(over)
    return row


class _MemoryStorage:
    """内存桶：只实现报告正文用到的那几个动作，行为对齐 ObjectStorage 的公开面。"""

    def __init__(self, is_object_storage: bool = True):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_upload = False
        self.is_object_storage = is_object_storage

    def upload_bytes(self, key, data, content_type="application/octet-stream"):
        if self.fail_upload:
            raise RuntimeError("simulated object storage outage")
        self.objects[key] = data

    def download_bytes(self, key):
        return self.objects.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)
        return True

    def exists(self, key):
        return key in self.objects

    def get_presigned_url(self, key, expires=3600, download_filename=""):
        return None if key not in self.objects else f"https://oss.test/{key}?sig=x&expires={expires}"


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    """报告正文一律走内存桶；断网断凭据，测试只验我们自己的逻辑。"""
    store = _MemoryStorage()
    monkeypatch.setattr(reports, "content_storage", lambda: store)
    return store


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
    """M2 之前的存量报告没有权限快照，过去属主自己也打不开（报告中心成空壳）。"""
    row = _content_report(source_type="legacy", generation_status="legacy")
    assert access.can_access_report(row, OWNER)
    assert not access.can_access_report(row, OTHER)


def test_empty_body_content_report_not_served():
    """正文判据是对象 key：key 为空就是没正文，库里的 content 列不再参与判断。"""
    assert not access.can_access_report(_content_report(content_object_key=""), OWNER)
    # 存量还没搬（key 空、content 非空）同样按"不可读"处理，由详情接口显式提示跑迁移
    assert not access.can_access_report(
        _content_report(content_object_key="", content="# 旧正文"), OWNER)


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
    for forbidden in ("access_token", "security_context", "share_token_hash", "intent",
                      "datasource_id", "content_object_key"):
        assert forbidden not in json.dumps(out, ensure_ascii=False, default=str)
    # 正文不在投影里：对象 key 属内部存储标识，客户端只拿读取时现拼的签名直链
    assert "content" not in out and out["content_size"] == 20


# ── 2. 内容型报告的增删改 ─────────────────────────────────────────────────────

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
    {"title": "报告", "content": "   "},
    {"title": "报告", "content": "正文", "format": "pdf"},
    {"title": "报告", "content": "正文", "source_type": "scheduled"},
    {"title": "报告", "content": "正文", "source_type": "legacy"},
])
def test_create_content_report_rejects_invalid(insert_capture, kwargs):
    with pytest.raises(ValueError):
        reports.create_content_report(workspace_id=3, owner_id=7, **kwargs)
    assert not [s for s in insert_capture if "INSERT" in s[0]]


def test_create_content_report_requires_owner(insert_capture):
    with pytest.raises(PermissionError):
        reports.create_content_report(title="报告", content="正文", workspace_id=3, owner_id=0)


def test_create_content_report_persists_ready_private_content_row(insert_capture, fake_storage):
    out = reports.create_content_report(title="新加坡近30天案例", content="# 结论",
                                       workspace_id=3, owner_id=7, source_type="manual",
                                       origin={"channel": "report_center"})
    sql, params = insert_capture[0]
    assert "INSERT INTO adh_reports" in sql
    assert out["generation_status"] == "ready" and out["source_type"] == "manual"
    assert "'private'" in sql and "'ready','draft'" in sql      # 私有 + 未发布
    assert "manual" in params and json.dumps({"origin": {"channel": "report_center"}},
                                            ensure_ascii=False) in params
    # 正文只进对象存储：列名带 key，且正文不再作为参数写库
    assert "content_object_key" in sql and "content_size" in sql
    assert "# 结论".encode() in fake_storage.objects.values()
    assert not any(isinstance(p, str) and "# 结论" in p for p in params), \
        "正文不得再作为参数写进库"


def test_content_upload_failure_does_not_persist_row(insert_capture, fake_storage):
    """对象存储写不进去 → 显式报错且不落行：宁可不给报告，也不给一份没正文的报告。"""
    fake_storage.fail_upload = True
    with pytest.raises(ValueError) as exc:
        reports.create_content_report(title="报告", content="# 结论", workspace_id=3,
                                     owner_id=7, source_type="manual")
    assert "对象存储" in str(exc.value)
    assert not [s for s in insert_capture if "INSERT" in s[0]]


def test_update_content_report_rejects_data_derived(monkeypatch):
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="scheduled"))
    with pytest.raises(ValueError) as exc:
        reports.update_content_report(1, OWNER, content="改一下")
    assert "不可在线编辑" in str(exc.value)


def test_legacy_report_is_viewable_but_read_only(monkeypatch):
    """历史报告（任务取数产出）可看可删不可改 —— 改它等于篡改取数结果。"""
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="legacy", generation_status="legacy"))
    assert access.can_access_report(_content_report(source_type="legacy", generation_status="legacy"), OWNER)
    with pytest.raises(ValueError) as exc:
        reports.update_content_report(1, OWNER, content="改写历史")
    assert "只读可查不可改" in str(exc.value)
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    monkeypatch.setattr("backend.common.db.execute_query", lambda sql, params=(): [])
    reports.delete_report(1, OWNER)          # 清理存量历史报告是允许的
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
    # 定时任务产出与执行记录一一对应，报告中心不给删（去任务调度管理）
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="scheduled", task_id=88))
    with pytest.raises(ValueError):
        reports.delete_report(1, OWNER)
    # 分析生成（含「用最新数据重新生成」追加的版本）不绑执行记录 → 属主可删
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(source_type="analysis"))
    monkeypatch.setattr("backend.common.db.execute_query", lambda sql, params=(): [])
    written = []
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append((sql, params)) or 1)
    reports.delete_report(1, OWNER)
    assert written[0][0].startswith("DELETE FROM adh_reports WHERE id=%s AND owner_id=%s")


def test_delete_report_removes_whole_series(monkeypatch):
    """删一份报告 = 删它的全部历史版本（含重跑追加的分析版），不留孤儿卡片。

    历史缺陷：卡片身份取最新一版时，只删 latest 会把原始会话报告留下、卡片仍在。
    归集口径只认 series_key：同键的属主行一起删。
    """
    base = _content_report(id=1, title="全国页面访问统计", source_type="conversation", content_object_key="k/1")
    # 重跑产物继承 base 的归集键 report:1
    child = {"id": 2, "owner_id": 7, "task_id": None, "log_id": None, "title": base["title"],
             "source_type": "analysis", "content_object_key": "k/2",
             "analysis_source": {"refresh_series": "report:1"}}
    unrelated = {"id": 3, "owner_id": 7, "task_id": None, "log_id": None, "title": "别的报告",
                 "source_type": "conversation", "content_object_key": "k/3"}
    monkeypatch.setattr(reports, "load_report", lambda rid: dict(base))
    monkeypatch.setattr("backend.common.db.execute_query", lambda sql, params=(): [base, child, unrelated])
    dropped, written = [], []
    monkeypatch.setattr(reports, "drop_report_content", lambda row: dropped.append(row["content_object_key"]))
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(): written.append(params[0]) or 1)
    out = reports.delete_report(1, OWNER)
    assert out["deleted"] == 2                       # base + 重跑版，unrelated 不牵连
    assert set(written) == {1, 2}
    assert set(dropped) == {"k/1", "k/2"}


# ── 3. 会话存报告（正文只从服务端已落库会话读）─────────────────────────────────

def _conv(messages):
    return {"id": 9, "workspace_id": 3, "title": "查看最近7天的案例", "messages": messages}


@pytest.fixture
def conversation(monkeypatch):
    """会话读取替身：消息已拆行存放（backend/core/conversation_store），
    报告侧只读 `load_messages`，所以两个面都要桩：会话行 + 拆行后的消息清单。"""
    holder = {"row": None}
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), fetchone=False: holder["row"] if "adh_conversations" in sql else None)
    from backend.core import conversation_store
    monkeypatch.setattr(conversation_store, "load_messages",
                        lambda cid, **kw: (holder["row"] or {}).get("messages") or [])
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
    # 问题不进正文（报告标题已承载同一件事），但仍登记在 origin 供重跑/建任务使用
    assert "近7天案例数" not in saved["content"]
    assert saved["origin"]["question"] == "近7天案例数"


def test_save_conversation_report_refuses_non_analysis_turn(conversation):
    """“已记住…”这类系统操作确认不是报告：服务端同样拒，不给绕过前端的入口。"""
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
    # 归档通道（无草稿的旧客户端直接存）面向的是“进报告中心”，文案跟着语义走
    assert "不能进报告中心" in str(exc.value)
    assert created == []


def test_save_conversation_report_without_answer_is_explicit(conversation):
    holder, _ = conversation
    holder["row"] = _conv([{"role": "user", "content": "hi"}])
    with pytest.raises(ValueError):
        reports.save_conversation_report(user=OWNER, conversation_id=9)
    with pytest.raises(ValueError):
        reports.save_conversation_report(user=OWNER, conversation_id=0)


# ── 3b. 报告草稿（两段式：草稿留会话，归档才进报告中心）──────────────────────

def _answer_turn(**over):
    turn = {"role": "assistant", "content": "共 **12** 单", "question": "近7天案例数",
            "message_uuid": "m-1",
            "result": {"columns": [{"name": "案例数"}], "rows": [{"案例数": 12}]}}
    turn.update(over)
    return turn


def test_report_draft_does_not_touch_report_center(conversation):
    """生成草稿只读会话、不建报告行（报告中心不过滤 publication_status，建行就混进正式产物）。"""
    holder, created = conversation
    holder["row"] = _conv([{"role": "user", "content": "近7天案例数"}, _answer_turn()])
    draft = reports.build_report_draft(user=OWNER, conversation_id=9, message_index=1)
    assert created == []
    assert "共 **12** 单" in draft["markdown"] and "| 案例数 |" in draft["markdown"]
    assert draft["title"] and draft["revisions"] == 0
    assert draft["message_uuid"] == "m-1" and draft["conversation_id"] == 9


def test_report_draft_refuses_non_analysis_turn(conversation):
    """纯问答/系统操作确认连草稿都不给生成，原因与归档通道同一句。"""
    holder, created = conversation
    holder["row"] = _conv([{"role": "assistant", "question": "默认用近3个月",
                             "content": "已记住：您一般查询数据时默认使用近 3 个月的时间范围。"}])
    with pytest.raises(ValueError) as exc:
        reports.build_report_draft(user=OWNER, conversation_id=9, message_index=0)
    assert "不是数据分析结果" in str(exc.value)
    assert created == []


def test_report_draft_blocks_infrastructure_leak_early(conversation):
    """含物理表/SQL 的回答在生成草稿时就拦，不等用户改完稿点归档才发现存不了。"""
    holder, created = conversation
    holder["row"] = _conv([_answer_turn(sql="select 1",
                                        content="select order_id from crm.t_order limit 10")])
    with pytest.raises(ValueError) as exc:
        reports.build_report_draft(user=OWNER, conversation_id=9, message_index=0)
    assert "不能存为报告" in str(exc.value)
    assert created == []


def test_report_draft_of_other_users_conversation_is_refused(conversation):
    holder, created = conversation
    holder["row"] = None            # 按 user_id 过滤后取不到
    with pytest.raises(PermissionError):
        reports.build_report_draft(user=OWNER, conversation_id=9)
    assert created == []


def test_archive_uses_draft_body_and_title(conversation):
    """归档以草稿为准：正文用用户改完的那份，不再追加结果表（删掉就是不要）。"""
    holder, created = conversation
    holder["row"] = _conv([_answer_turn(reportDraft={
        "title": "近7天案例交付口径", "markdown": "改过的正文段落\n\n- 只算已结案", "revisions": 2})])
    out = reports.save_conversation_report(user=OWNER, conversation_id=9, message_index=0)
    assert out["report_id"] == 55
    saved = created[0]
    assert saved["title"] == "近7天案例交付口径"
    assert "改过的正文段落" in saved["content"] and "只算已结案" in saved["content"]
    assert "共 **12** 单" not in saved["content"] and "| 案例数 |" not in saved["content"]
    assert out["archived_at"]                     # 供前端回填会话里的归档回执


def test_archive_blocks_leak_edited_into_draft(conversation):
    holder, created = conversation
    holder["row"] = _conv([{"role": "assistant", "content": "共 12 单", "sql": "select 1",
                            "reportDraft": {"title": "t", "markdown":
                                "明细见 select order_id from crm.t_order limit 10"}}])
    with pytest.raises(ValueError) as exc:
        reports.save_conversation_report(user=OWNER, conversation_id=9, message_index=0)
    assert "不能进报告中心" in str(exc.value)
    assert created == []


def test_archive_is_idempotent_for_archived_draft(conversation, monkeypatch):
    """已归档的草稿再点不重复落报告（前端归档标记没写回也不会多出一份）。"""
    holder, created = conversation
    holder["row"] = _conv([_answer_turn(reportDraft={
        "title": "已归档稿", "markdown": "正文",
        "archived": {"report_id": 55, "title": "已归档稿", "archived_at": "2026-11-06 15:20:00"}})])
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _content_report(id=55, owner_id=7,
                                                    source_type="conversation", title="已归档稿"))
    out = reports.save_conversation_report(user=OWNER, conversation_id=9, message_index=0)
    assert out["already_archived"] is True and out["report_id"] == 55
    assert created == []


def test_archive_rearchives_when_prior_report_gone(conversation, monkeypatch):
    """归档回执指向的报告已被删除 → 不能拿旧回执当“已归档”把用户锁死，重新归档。"""
    holder, created = conversation
    holder["row"] = _conv([_answer_turn(reportDraft={
        "title": "旧稿", "markdown": "正文",
        "archived": {"report_id": 55, "title": "旧稿", "archived_at": "2026-11-06 15:20:00"}})])
    monkeypatch.setattr(reports, "load_report", lambda rid: None)
    out = reports.save_conversation_report(user=OWNER, conversation_id=9, message_index=0)
    assert out["report_id"] == 55 and created and not out.get("already_archived")


def test_render_conversation_report_caps_rows_and_keeps_question_out_of_body():
    # 正文不抄用户问题（与报告标题同一件事说两遍）；行上限与截断标注不变。
    md = reports.render_conversation_report(
        answer="结论：稳步上升",
        result={"columns": ["日期", "数量"], "rows": [{"日期": f"d{i}", "数量": i} for i in range(260)]})
    assert "提问" not in md and "稳步上升" in md
    assert md.count("\n| d") == 200 and "仅展示前 200 行" in md


# ── 3b. 哪些回合算报告（单一口径，前端只消费）────────────────────────────────

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
    """不可存的原因要给出路，但出路不得指向已下线的入口（报告中心不再手建空白报告）。"""
    ok, reason = reports.turn_reportability({"content": "已记住偏好"})
    assert not ok and "不是数据分析结果" in reason
    assert "新建报告" not in reason


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


# ── 4. 清单：报告跟随属主，跨工作空间；任务名 JOIN 显示 ──────────────────────────

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


def test_api_manual_report_entry_is_retired(api, monkeypatch):
    """手建空白报告的入口已下线：422 + 出路（报告=分析产物），且不得偷偷落一行。

    存量手写报告不在此列（仍可编辑/导出/删除），退役的只是创建口。
    """
    client = api
    spy = Mock()
    monkeypatch.setattr(reports, "create_content_report", spy)
    res = client.post("/api/reports", json={"workspace_id": 3, "title": "手写报告",
                                           "content": "正文", "source_type": "manual"})
    assert res.status_code == 422
    assert "存为报告" in res.json()["detail"]
    spy.assert_not_called()


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
                        Mock(side_effect=PermissionError("只能管理自己创建的报告")))
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
    assert "owner_id = %s" in sql and params[0] == 7          # 不跨用户递别人报告标题
    assert "SELECT *" not in sql.upper()
