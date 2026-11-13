"""交付文档对象（```report 块）→ 报告中心：判定面 / 存储面 / 渲染面三方对齐。

M4 的口径（历史同类缺口：```artifact 上线时判定面不看它，用户看得见报告却存不进中心）：
* ` ```report ` 引用块 = 本轮主交付物，就是报告，给「存为报告」入口；
* 正文取**交付对象本身**（与对话里渲染的同一份），不是回答里那段结论摘要；
* 交付对象取不到 / 声明形态与实际不符 / 形态尚未接线 → 显式报错给出路，**不得**回退存摘要；
* 归档时登记 doc_id 与文档实际主题，重跑靠它沿用同一套配色。
"""
import json

import pytest

from backend.modules.viz.services import report_document_service as docs
from backend.modules.viz.services import report_service as reports

OWNER = {"user_id": 7, "role": "analyst"}

_HTML_BODY = ('<!DOCTYPE html><html><head><title>渠道成交结构分析报告</title>'
              '<style>:root{--primary:#4f46e5}</style></head>'
              '<body><h1>渠道成交结构分析报告</h1><table><tr><td>线上</td><td>62%</td></tr></table>'
              '</body></html>')

_DOC = {"id": "d" * 32, "kind": "doc", "body_kind": "html",
        "title": "渠道成交结构分析报告", "theme": "tech", "template_key": None,
        "digest": "abc", "size_chars": len(_HTML_BODY), "warnings": [],
        "row_counts": None, "updated_at": "2026-11-13T00:00:00", "body": _HTML_BODY}


def _turn(text="分析结论如下：线上占比最高。", doc_id=None, body_kind="html", title=""):
    block = "```report\n" + json.dumps(
        {"doc_id": doc_id or _DOC["id"], "body_kind": body_kind,
         "title": title or ""}, ensure_ascii=False) + "\n```"
    return {"role": "assistant", "message_uuid": "m1",
            "question": "分析各渠道成交结构", "content": text + "\n\n" + block,
            "tool_calls": [{"tool": "mcp__datahub_semantic__run_semantic_query"}]}


@pytest.fixture
def stub(monkeypatch):
    """桩掉会话定位与文档读取两条外部事实，返回可编排的假存储。"""
    from backend.core import conversation_store

    holder = {"answer": None, "load_error": None, "loads": []}

    def _install(answer):
        monkeypatch.setattr("backend.common.db.execute_query",
                            lambda sql, params=(), **kw: {
                                "id": 42, "workspace_id": 3, "title": "会话",
                                "as_bot_key": "admin", "datasource_id": 0, "site_code": "cn",
                            } if "adh_conversations" in str(sql) else None, raising=True)
        monkeypatch.setattr(conversation_store, "load_messages", lambda cid, **k: [holder["answer"]])
        holder["answer"] = answer

        def fake_load(doc_id, *, user_id, include_body=True):
            holder["loads"].append({"doc_id": doc_id, "user_id": user_id, "include_body": include_body})
            if holder["load_error"]:
                raise holder["load_error"]
            return dict(_DOC)

        monkeypatch.setattr(docs, "load_document", fake_load)
        return holder

    return _install


# ── 判定面：report 块 = 报告 ────────────────────────────────────────────

def test_report_block_turn_is_reportable(stub):
    holder = stub(_turn())
    ok, reason = reports.turn_reportability(holder["answer"])
    assert ok is True and reason == "含交付文档对象"


def test_report_block_without_doc_id_is_not_guessed(stub):
    turn = {"role": "assistant", "content": "```report\n{\"title\":\"x\"}\n```"}
    assert reports.report_deliverables(turn) == []
    assert reports.turn_reportability(turn)[0] is False


def test_report_block_parsed_from_either_content_or_reply_field():
    """done 事件用 reply、落库消息用 content：两侧都要认（否则刷新前后判定不一致）。"""
    body = _turn()
    assert reports.latest_report_deliverable(body)["doc_id"] == _DOC["id"]
    as_reply = {"role": "assistant", "reply": body["content"]}
    assert reports.latest_report_deliverable(as_reply)["doc_id"] == _DOC["id"]


def test_latest_deliverable_wins_when_turn_revises_report(stub):
    """同一轮修订两次：以最新一份为正文（取第一份会把旧版当成品归档）。"""
    first = "```report\n{\"doc_id\":\"aaa\",\"body_kind\":\"html\"}\n```"
    second = "```report\n{\"doc_id\":\"bbb\",\"body_kind\":\"html\"}\n```"
    turn = {"role": "assistant", "content": f"{first}\n改一版\n{second}"}
    assert reports.latest_report_deliverable(turn)["doc_id"] == "bbb"


# ── 存储面：正文取交付对象本身 ──────────────────────────────────────────

def test_draft_uses_render_document_as_body(stub):
    holder = stub(_turn())
    draft = reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
    assert draft["format"] == "html"
    assert draft["markdown"] == _HTML_BODY, "正文必须是交付对象原文，不是回答里那段结论"
    assert draft["title"] == "渠道成交结构分析报告"
    assert draft["document"] == {"doc_id": _DOC["id"], "body_kind": "html"}
    assert holder["loads"][0]["doc_id"] == _DOC["id"] and holder["loads"][0]["user_id"] == 7


def test_missing_document_fails_loud_instead_of_storing_summary(stub):
    """取不到就报错给出路：拿对话摘要冒充报告，等于伪装成一份能用但内容不同的东西。"""
    holder = stub(_turn())
    holder["load_error"] = docs.RenderDocumentError("文档不存在或已被清理", "not_found")
    with pytest.raises(ValueError) as exc:
        reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
    assert "已被清理" in str(exc.value) and "不能拿对话摘要冒充" in str(exc.value)


def test_unwired_chart_data_kind_is_rejected_not_converted(stub):
    """尚未接归档面的形态（图表数据对象）：显式拒，不静默转成 markdown 凑一份渲染面不相等的报表。"""
    holder = stub(_turn(body_kind="chart-data"))
    with pytest.raises(ValueError) as exc:
        reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
    assert "尚未接线" in str(exc.value)
    assert holder["loads"] == [], "未接线形态不该去读正文"


def test_spec_kind_archives_as_self_contained_snapshot(stub, monkeypatch):
    """spec 归档存**服务端渲染的自包含快照**，不存引用。

    为什么不存引用：交付对象随会话删除会被清理，而报表要脱离会话独立存活；
    快照里图表退成表格并写明原因（而不是拿 HTML 伪图冒充平台渲染）。
    """
    spec_body = json.dumps({"title": "渠道分析报告", "theme": "tech", "template_name": "周报模板",
                            "sections": [{"title": "核心指标", "kind": "kpi",
                                          "data": {"columns": ["渠道", "成交额"],
                                                    "rows": [{"渠道": "线上", "成交额": 62}],
                                                    "row_count": 1}},
                                         {"title": "地域分布", "kind": "chart", "chart_type": "china_map",
                                          "data": {"columns": ["省份", "浏览量"],
                                                    "rows": [{"省份": "江苏省", "浏览量": 251}],
                                                    "row_count": 1}},
                                         {"title": "归因", "kind": "text", "narrative": "线上占比最高。"},
                                         {"title": "异常分析", "kind": "chart", "chart_type": "line",
                                          "error": "会话未确定数据源"}]}, ensure_ascii=False)

    stub(_turn(body_kind="spec", title="渠道分析报告"))
    # 先装 stub 再改 load_document：stub 自己也会桩一层（顺序反了会被它盖掉）
    monkeypatch.setattr(docs, "load_document",
                        lambda doc_id, **kw: dict(_DOC, body_kind="spec", body=spec_body,
                                                  title="渠道分析报告", row_counts=[]))
    draft = reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
    assert draft["format"] == "html" and draft["document"]["body_kind"] == "spec"
    body = draft["markdown"]
    assert "<h1>渠道分析报告</h1>" in body and "--primary" in body, "快照必须自带主题令牌（iframe 不吃主文档变量）"
    assert "<title>渠道分析报告</title>" in body, "归档快照的文档标题不能还是骨架占位名"
    assert "线上占比最高" in body and "江苏省" in body
    assert "图表退为表格" in body, "图型退化要写在文档里，不得默默拿表格冒充地图"
    assert "暂无数据" in body and "会话未确定数据源" in body, "失败章节要如实保留，不能静默删章"
    assert "</script" not in body and "http://" not in body, "快照必须自包含（不得引入外部资源）"


def test_spec_snapshot_renders_offline_without_theme_lookup(stub, monkeypatch):
    """主题不可用时快照照旧产出（带样式规则），不因为拿不到令牌就拒归档。"""
    spec_body = json.dumps({"sections": [{"title": "说明", "kind": "text", "narrative": "无数据章节。"}]})
    stub(_turn(body_kind="spec"))
    monkeypatch.setattr(docs, "load_document",
                        lambda doc_id, **kw: dict(_DOC, body_kind="spec", body=spec_body, theme=""))
    draft = reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
    assert "无数据章节。" in draft["markdown"]


def test_declared_kind_must_match_document():
    """块声明 html 而对象实际是 spec（两边漂移）→ 拒，不能“就当 html 存”。"""
    def fake_load(doc_id, *, user_id, include_body=True):
        return dict(_DOC, body_kind="spec")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(docs, "load_document", fake_load)
        with pytest.raises(ValueError) as exc:
            reports.read_render_document_body(
                user=OWNER, deliverable={"doc_id": "x", "body_kind": "html"})
    assert "不一致" in str(exc.value)


# ── 归档面：登记 doc_id 与实际主题（重跑靠它）──────────────────────────

def test_archive_records_doc_origin_and_theme(stub, monkeypatch):
    holder = stub(_turn())
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {"id": 991, "title": kwargs["title"], "format": kwargs["format"]}

    monkeypatch.setattr(reports, "create_content_report", fake_create)
    out = reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="m1")
    assert out["id"] == 991
    origin = captured["origin"]
    assert origin["render_doc_id"] == _DOC["id"]
    # 主题取文档实际采用的值（tech），不是 artifact 的 theme（本轮没有 artifact）
    assert origin["theme"] == "tech"
    assert origin["question"] == "分析各渠道成交结构"
    assert captured["content"] == _HTML_BODY and captured["format"] == "html"


def test_draft_body_wins_over_document_but_keeps_origin(stub, monkeypatch):
    """用户改过稿：正文以草稿为准（不拿交付对象覆盖人的修改），但 doc_id 仍登记供溯源。"""
    holder = stub(_turn())
    holder["answer"] = dict(holder["answer"], reportDraft={
        "title": "我改过的标题", "markdown": "# 人工修订版", "format": "markdown", "revisions": 2})
    captured = {}
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: (captured.update(kw), {"id": 992, "title": kw["title"]})[1])
    reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="m1")
    assert captured["content"] == "# 人工修订版" and captured["format"] == "markdown"
    assert captured["origin"]["render_doc_id"] == _DOC["id"]
    assert captured["title"] == "我改过的标题"
    # 草稿分支只要主题不要整份正文（避免多拉一次大字段）
    assert holder["loads"] and holder["loads"][0]["include_body"] is False
