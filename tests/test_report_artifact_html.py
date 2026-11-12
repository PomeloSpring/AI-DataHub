"""会话里的 HTML 报告产物 → 报告中心（```artifact 交付的整份报告）。

口径：
* 交付了 HTML/Markdown 报告文件的回合**就是报告**（旧写法不看 artifact，连「存为报告」
  入口都不给，用户只能看着那份 HTML 报告存不进报告中心）；
* 报告正文取**产物文件本身**（与下载卡片同一份），不是"报告已生成，包含…"那段目录摘要；
* 产物文件取不到 → 显式报错给出路，**不得**回退去存摘要（no-silent-degradation §1）；
* Excel/CSV/PDF/图片是数据文件或二进制产物 → 出路是资产清单，不给报告入口（报告中心
  只有 markdown/html 渲染面，不假装能展示）。
"""
import json

import pytest

from backend.modules.viz.services import report_service as reports

OWNER = {"user_id": 7, "role": "analyst"}

_HTML_DOC = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
             '<title>direct-ip 页面访问 IP 归属地分布报告</title>'
             '<style>body{font-family:sans-serif}</style></head>'
             '<body><h1>direct-ip 页面访问 IP 归属地分布报告</h1>'
             '<table><tr><th>省份</th><th>浏览量</th></tr>'
             '<tr><td>江苏省</td><td>251</td></tr></table></body></html>')


def _artifact_turn(art_type="html", path="/workspace/report.html"):
    block = "```artifact\n" + json.dumps(
        {"type": art_type, "filename": path.rsplit("/", 1)[-1], "path": path,
         "description": "近 7 天访问分布"}, ensure_ascii=False) + "\n```"
    return {"role": "assistant", "message_uuid": "m1",
            "question": "查 direct-ip 的访问 ip 归属地分布",
            "content": "报告已生成,包含:\n1. 核心指标概览\n\n" + block,
            "tool_calls": [{"tool": "mcp__datahub_semantic__run_semantic_query"}]}


@pytest.fixture
def stub_conversation(monkeypatch):
    """把「定位会话那一条回答」与「读会话工作区产物」两条外部事实桩掉。"""
    from backend.core import conversation_store
    from backend.modules.mind.execution import session_workspace as sw

    def _install(answer):
        monkeypatch.setattr("backend.common.db.execute_query",
                            lambda sql, params=(), **kw: {
                                "id": 42, "workspace_id": 3, "title": "会话",
                                "as_bot_key": "admin", "datasource_id": 0, "site_code": "cn",
                            } if "adh_conversations" in str(sql) else None, raising=True)
        monkeypatch.setattr(conversation_store, "load_messages", lambda cid, **k: [answer])
        return answer

    return _install, sw, monkeypatch


def test_html_artifact_turn_is_reportable(stub_conversation):
    install, _, _ = stub_conversation
    ok, reason = reports.turn_reportability(install(_artifact_turn()))
    assert ok is True and reason == "含报告文件产物"


def test_data_file_artifact_turn_points_to_asset_list():
    """Excel/CSV/PDF/图片不给报告入口，但原因要说清去哪儿归档。"""
    for art_type in ("excel", "csv", "pdf", "png"):
        turn = {"role": "assistant", "content": f"```artifact\n{json.dumps({'type': art_type, 'path': 'a'})}\n```"}
        ok, reason = reports.turn_reportability(turn)
        assert ok is False and "资产清单" in reason, art_type


def test_malformed_artifact_block_is_not_guessed():
    turn = {"role": "assistant", "content": "```artifact\n{不是 JSON\n```"}
    assert reports.conversation_artifacts(turn) == []
    assert reports.turn_reportability(turn)[0] is False


def test_draft_uses_the_artifact_file_as_body(stub_conversation):
    """草稿正文 = 产物文件原文，格式=html，标题取文档自己的 h1。"""
    install, sw, monkeypatch = stub_conversation
    install(_artifact_turn())
    seen = {}

    def fake_read(conversation_id, user_id, path, max_chars=200_000):
        seen.update({"conversation_id": conversation_id, "user_id": user_id, "path": path})
        return _HTML_DOC

    monkeypatch.setattr(sw, "read_conversation_workspace_text", fake_read)
    draft = reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
    assert draft["format"] == "html"
    assert draft["markdown"] == _HTML_DOC
    assert draft["title"] == "direct-ip 页面访问 IP 归属地分布报告"
    assert draft["artifact"] == {"filename": "report.html", "type": "html"}
    # 身份只信服务端：读文件用属主 id + 会话 id，不接受请求体传路径外的东西
    assert seen == {"conversation_id": 42, "user_id": 7, "path": "/workspace/report.html"}


def test_archive_without_draft_still_uses_the_artifact(stub_conversation):
    """没有草稿正文（旧客户端直接归档）也取产物，不退回存目录摘要。"""
    install, sw, monkeypatch = stub_conversation
    install(_artifact_turn())
    monkeypatch.setattr(sw, "read_conversation_workspace_text", lambda *a, **kw: _HTML_DOC)
    created = {}
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.update(kw) or {"report_id": 9})
    reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="m1")
    assert created["format"] == "html"
    assert created["content"] == _HTML_DOC
    assert created["origin"]["artifact_filename"] == "report.html"
    # 可复跑事实仍在：重跑/建任务读的是问题正文，不是产物文件
    assert created["origin"]["question"] == "查 direct-ip 的访问 ip 归属地分布"


def test_missing_artifact_file_fails_loudly(stub_conversation):
    """产物取不到必须报错给出路，不得静默把摘要当报告正文存下来。"""
    install, sw, monkeypatch = stub_conversation
    install(_artifact_turn())

    def gone(conversation_id, user_id, path, max_chars=200_000):
        raise FileNotFoundError("会话工作区里找不到产物文件（report.html），可能已被清理")

    monkeypatch.setattr(sw, "read_conversation_workspace_text", gone)
    with pytest.raises(ValueError, match="已取不到"):
        reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")


def test_hand_edited_draft_wins_and_keeps_its_format(stub_conversation):
    """用户改过草稿 → 以草稿为准（人的意图优先），格式沿用草稿声明的 html。"""
    install, _, monkeypatch = stub_conversation
    answer = install(_artifact_turn())
    answer["reportDraft"] = {"title": "我自己起的名字", "format": "html",
                             "markdown": "<h1>改过的正文</h1>"}
    created = {}
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.update(kw) or {"report_id": 9})
    reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="m1")
    assert created["content"] == "<h1>改过的正文</h1>"
    assert created["format"] == "html"
    assert created["title"] == "我自己起的名字"


def test_html_body_still_passes_outbound_guard(stub_conversation):
    """HTML 正文同样过出站收口：含基础设施信息就拒归档（不是"存下来再说"）。"""
    install, sw, monkeypatch = stub_conversation
    install(_artifact_turn())
    monkeypatch.setattr(sw, "read_conversation_workspace_text",
                        lambda *a, **kw: '<h1>报告</h1><p>datasource_id: 5, host 10.0.0.1</p>')
    with pytest.raises(ValueError, match="基础设施信息"):
        reports.build_report_draft(user=OWNER, conversation_id=42, message_uuid="m1")
