"""报告「按最新数据重新生成」与「快捷创建定时任务」回归。

口径：
* 标题按报告实际内容归纳（正文小标题/图表标题优先），不把用户口语提问当报告名；
* 可复跑只需登记原始问题（+ 当时的站点），**不要求报告持有 AS-BOT 与数据源**——重跑身份
  与可查范围按报告属主的角色实时继承，跟任务调度同一口径；
* 任务产出的报告不在报告侧重开一条链路——重跑入口是所属定时任务；
* 「用最新数据重新生成」在报告页同一条 SSE 连接里跑完：prepare 只建档认领、不派发队列，
  产出收口只有一处（_finalize_agent_report），观看方离开就中止（新版本记 cancelled）；
* 从报告建任务是写动作，必须过 scheduled:manage 权限码（与任务调度页同一口径）。
"""
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.modules.viz.services import report_access as access
from backend.modules.viz.services import report_service as reports

OWNER = {"user_id": 7, "role": "analyst"}


def _chat_report(**over):
    row = {"id": 1, "owner_id": 7, "workspace_id": 3, "task_id": None, "log_id": None,
           "title": "全国企业分布地图", "source_type": "conversation",
           "generation_status": "ready", "content": "x", "format": "markdown"}
    row.update(over)
    return row


# ── 1. 标题归纳：报告自己的名字优先于用户口语提问 ─────────────────────────────

def test_title_prefers_body_heading_over_question():
    title = reports.derive_report_title(
        "能帮忙展示中国地图吗", "好的，为您展示。\n\n### 全国企业分布地图\n\n正文…")
    assert title == "全国企业分布地图"


def test_generic_heading_is_not_used_as_title():
    """「查询结果」这类结构性小标题不是报告名，跳过它取下一个有意义标题。"""
    title = reports.derive_report_title(
        "统计各区域案例数", "## 查询结果\n\n| 区域 |\n\n## 各区域案例分布\n\n…")
    assert title == "各区域案例分布"


def test_chart_block_title_is_used():
    body = "说明文字\n```chart\n" + json.dumps({"type": "bar", "title": "月度新增用户趋势"}) + "\n```\n"
    assert reports.derive_report_title("本月新增用户趋势怎么样", body) == "月度新增用户趋势"


def test_question_is_cleaned_when_nothing_else_available():
    """兜底才用提问，且要去掉口语前缀与后半句，不能整句照抄。"""
    assert reports.derive_report_title(
        "帮忙查一下近半年企业新增数量，要按月份拆分", "没有标题的纯文字回答") == "近半年企业新增数量"
    # 空正文 + 空提问也不能给出空标题（报告中心条目名不能是空白）
    assert reports.derive_report_title("", "") == "会话分析报告"


def test_save_conversation_report_uses_derived_title_and_keeps_rerun_config(monkeypatch):
    """存报告时标题走归纳；origin 登记问题与站点，AS-BOT/源只作当时的事实快照。"""
    answer = {"role": "assistant", "message_uuid": "m1", "question": "能帮忙展示中国地图吗",
              "content": "### 全国企业分布地图\n\n正文", "sql": "SELECT 1",
              "result": {"columns": [{"name": "省份"}], "rows": [{"省份": "广东"}]}}
    # 会话行与拆行消息是两个面（读路径已收口到 backend/core/conversation_store），
    # 只桩 execute_query 会把消息查询也当成会话行返回。
    from backend.core import conversation_store
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), **kw: {
                            "id": 42, "workspace_id": 3, "title": "会话", "as_bot_key": "nl2sql_expert",
                            "datasource_id": 5, "site_code": "cn", "messages": [answer]}
                        if "adh_conversations" in str(sql) else None,
                        raising=True)
    monkeypatch.setattr(conversation_store, "load_messages", lambda cid, **k: [answer])
    created = {}
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.update(kw) or {"report_id": 9})
    reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="m1")
    assert created["title"] == "全国企业分布地图"
    origin = created["origin"]
    assert origin["question"] == "能帮忙展示中国地图吗"
    assert origin["as_bot_key"] == "nl2sql_expert" and origin["datasource_id"] == 5


# ── 2. 对外响应：可复跑配置不外露，重跑口径由服务端给出 ──────────────────────

def test_public_report_strips_infrastructure_ids_from_origin():
    row = _chat_report(analysis_source={"origin": {
        "channel": "chat", "conversation_id": 42, "question": "查个数",
        "as_bot_key": "nl2sql_expert", "datasource_id": 5, "site_code": "cn"}})
    out = access.public_report(row, content=False)
    assert out["origin"]["question"] == "查个数"
    assert "datasource_id" not in out["origin"] and "as_bot_key" not in out["origin"]
    assert out["refresh"]["available"] is True


def test_refreshability_only_needs_the_registered_question():
    no_question = _chat_report(analysis_source={"origin": {"channel": "report_center"}})
    info = access.refreshability(no_question)
    assert info["available"] is False and "原始问题" in info["hint"] and info["action_label"] == ""

    # 任务产出的报告：从这里点下去能触发所属任务 = 有能生效的动作，available 必须 True
    # （旧写法回 False，前端先 toast.error 就 return，触发任务的分支成了死代码——用户只得到一句报错）
    task_born = _chat_report(task_id=88, analysis_source={"origin": {
        "question": "q", "as_bot_key": "b", "datasource_id": 5}})
    info = access.refreshability(task_born)
    assert info["available"] is True and info["channel"] == "task"
    assert info["action_label"] == "触发所属任务重新生成"

    # 没登记 AS-BOT 与数据源也能重跑：身份与可查范围按属主角色实时继承（旧报告不是孤儿）
    no_source = _chat_report(analysis_source={"origin": {"question": "q"}})
    assert access.refreshability(no_source)["available"] is True


# ── 3. 重新生成建档：只消费服务端登记的事实（不派发队列）────────────────────

def test_refresh_rejects_task_born_report(monkeypatch):
    monkeypatch.setattr(reports, "_load_owned_report",
                        lambda rid, user: _chat_report(task_id=88))
    with pytest.raises(ValueError, match="定时任务"):
        reports.prepare_report_refresh(1, OWNER)


def test_refresh_rejects_report_without_registered_question(monkeypatch):
    monkeypatch.setattr(reports, "_load_owned_report",
                        lambda rid, user: _chat_report(analysis_source={}))
    with pytest.raises(ValueError, match="原始分析问题"):
        reports.prepare_report_refresh(1, OWNER)

    # 只有问题、没有 AS-BOT/源：照样可重跑（不再把旧报告当成不可复跑）
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "查个数"}}))
    monkeypatch.setattr(reports, "create_report", lambda **kw: {"id": 99})
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    assert reports.prepare_report_refresh(1, OWNER)["report_id"] == 99


def test_prepare_creates_and_claims_the_new_version_without_dispatch(monkeypatch):
    """建档 + 认领 + 把版式契约与执行身份算齐，**不派发队列**（重跑改成在线直播）。

    旧写法是 create + dispatch_report 交给 Celery 后台跑：页面只剩"生成中"三个字，
    过程看不见、离开页面它照跑（用户实拍）。派发这条腿一并退役，所以这里断言
    `dispatch_report` 一次都没被调用 —— 留一条没人走的异步腿，就等于留两套结局口径。
    """
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        content_object_key="ai-datahub/7/reports/1",
        analysis_source={"origin": {"question": "查个数", "as_bot_key": "nl2sql_expert",
                                    "datasource_id": 5, "site_code": "cn",
                                    "conversation_id": 42}}))
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return {"id": 99}

    monkeypatch.setattr(reports, "create_report", fake_create)
    claimed, dispatched = [], Mock()
    monkeypatch.setattr(reports, "claim_report", lambda rid: claimed.append(rid) or True)
    monkeypatch.setattr(reports, "dispatch_report", dispatched)
    monkeypatch.setattr(reports, "resolve_execution_owner",
                        lambda uid, ws: {"user_id": uid, "workspace_id": ws})
    monkeypatch.setattr(reports, "read_report_content_bytes",
                        lambda r: _ORIGINAL_BODY.encode("utf-8"))
    out = reports.prepare_report_refresh(1, OWNER)
    assert out["report_id"] == 99 and claimed == [99]
    assert dispatched.call_args_list == []                 # 队列侧不再代跑报告重跑
    assert captured["generation_status"] == "queued"
    assert captured["source_type"] == "analysis"          # 数据派生，正文只读
    source = captured["analysis_source"]
    assert source["kind"] == "agent_question" and source["question"] == "查个数"
    # 只带走问题与站点：AS-BOT/数据源不参与重跑配置（每次按属主角色重解）
    assert source["site_code"] == "cn"
    assert "as_bot_key" not in source and "datasource_id" not in source
    assert source["origin"]["question"] == "查个数"       # 新报告自己也要能再次重跑
    # 运行所需的三件（问题/配置/身份）在建档时就齐 —— 流里不再回头查库、不再拼契约
    assert out["question"] == "查个数"
    assert out["config"]["site_code"] == "cn"
    assert "china_map" in out["config"]["layout_contract"]        # 原版式契约随本轮带走
    assert out["identity"] == {"user_id": 7, "workspace_id": 3}


def test_prepare_refuses_when_previous_rerun_has_not_finished(monkeypatch):
    """抢不到认领就是上一轮还在跑：显式停住，不并发跑两次把正文写成半截。"""
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "查个数"}}))
    monkeypatch.setattr(reports, "create_report", lambda **kw: {"id": 99})
    monkeypatch.setattr(reports, "claim_report", lambda rid: False)
    with pytest.raises(ValueError, match="还没结束"):
        reports.prepare_report_refresh(1, OWNER)


def test_agent_question_branch_finishes_as_fact_report(monkeypatch):
    """无版式可沿的重跑（旧报告没正文/没结构）仍产事实表，并把原因带在版本上。"""
    finished = {}
    monkeypatch.setattr(reports, "finish_report",
                        lambda rid, **kw: finished.update(kw) or True)
    reports._finalize_agent_report(
        99, {"title": "T"}, {"kind": "agent_question", "question": "查个数",
                             "site_code": "cn", "layout_error": "上一版正文读不到"},
        {"status": "success", "answer": "随便一段正文", "_analysis_results": [
            {"status": "success", "columns": [{"name": "省份"}], "rows": [{"省份": "广东"}],
             "_security_context": {"version": 1}}]})
    # 事实表也是已鉴权取数的产物 → ready；"没沿用版式"记在 layout_result，不当假降级用
    assert finished["generation_status"] == "ready"
    assert finished["evidence_summary"]["validation_status"] == "governed"
    assert finished["security_context"] == {"version": 1}
    assert "广东" in finished["content"] and "不可对外发布" not in finished["content"]
    # 拿不到原版式 → 不声称沿用了版式，原因随 layout_result 下发给用户
    layout = finished["analysis_source"]["layout_result"]
    assert layout["status"] == "fact_only"
    assert "上一版正文读不到" in layout["reasons"][0]
    assert finished["stage_error_code"] == "FACT_ONLY"


def test_refresh_product_inherits_the_source_series_key(monkeypatch):
    """重跑产物必须归集回**原报告**（同一 series_key）。

    历史缺陷：会话保存的报告点「用最新数据重新生成」后，产物按标题落到另一张卡片，
    报告中心出现两张同名卡片；新卡片里只有失败的空正文，用户以为历史版本丢了。
    """
    row = _chat_report(analysis_source={"origin": {"question": "查个数"}})
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)
    captured = {}
    monkeypatch.setattr(reports, "create_report",
                        lambda **kw: captured.update(kw) or {"id": 99})
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    reports.prepare_report_refresh(1, OWNER)
    assert captured["analysis_source"]["refresh_series"] == access.series_key(row) == "report:1"

    # 链式重跑（在新产物上再点一次）仍归到最初那张卡片，不逐次改键
    child = {"id": 99, "owner_id": 7, "workspace_id": 3, "task_id": None, "title": row["title"],
             "source_type": "analysis", "generation_status": "degraded",
             "analysis_source": {"origin": {"question": "查个数"},
                                 "refresh_series": captured["analysis_source"]["refresh_series"]}}
    assert access.series_key(child) == "report:1"


@pytest.mark.parametrize("row,expected", [
    ({"id": 1, "task_id": 88, "source_type": "scheduled", "title": "T"}, "task:88"),
    ({"id": 2, "task_id": None, "source_type": "conversation", "title": "T"}, "report:2"),
    ({"id": 3, "task_id": None, "source_type": "analysis", "title": "T",
      "analysis_source": {"refresh_series": "report:2"}}, "report:2"),
    ({"id": 4, "task_id": None, "source_type": "analysis", "title": "月度案例"}, "title:月度案例"),
    ({"id": 5, "task_id": None, "title": "旧报告"}, "title:旧报告"),      # 无 source_type → 数据派生口径
])
def test_series_key_is_a_single_server_side_ruling(row, expected):
    """归集键只在一处算（前端不得再拼一套），并随对外响应下发。"""
    assert access.series_key(row) == expected
    assert access.public_report(row, content=False)["series_key"] == expected


# ── 3b. 读不到正文时必须照实说清是哪一类（no-silent-degradation §1）─────────────

def _data_row(**over):
    row = {"id": 9, "owner_id": 7, "workspace_id": 3, "task_id": None, "log_id": None,
           "title": "全国页面访问统计", "source_type": "analysis", "format": "markdown",
           "generation_status": "failed", "stage_error_code": "LLM_UNAVAILABLE",
           "content": "", "content_object_key": ""}
    row.update(over)
    return row


def test_failed_rerun_is_not_reported_as_a_permission_problem(monkeypatch):
    """重跑失败不能报成“权限已变化”：用户会去查权限，而真因是这次运行根本没跑成。"""
    monkeypatch.setattr(reports, "load_report", lambda rid: _data_row())
    out = reports.get_report(9, user=OWNER)
    warning = out["access_warning"]
    assert "本次生成未完成" in warning and "分析模型" in warning
    assert "权限已变化" not in warning
    assert out["content_url"] == ""


def test_pending_report_says_generating_not_unready(monkeypatch):
    monkeypatch.setattr(reports, "load_report",
                        lambda rid: _data_row(generation_status="queued", stage_error_code=None))
    assert "生成中" in reports.get_report(9, user=OWNER)["access_warning"]


def test_stale_permission_snapshot_says_snapshot_not_generation(monkeypatch):
    """正文在、快照失效 → 说“权限快照已变化”，与“生成失败”分开（两类动作不同）。"""
    monkeypatch.setattr(reports, "load_report", lambda rid: _data_row(
        generation_status="degraded", stage_error_code="FACT_ONLY",
        content_object_key="ai-datahub/7/reports/9", security_context={"version": 1,
                                                                       "policy_digest": "d"}))
    monkeypatch.setattr(access, "snapshot_is_current", lambda row: False)
    warning = reports.get_report(9, user=OWNER)["access_warning"]
    assert "权限快照已变化" in warning and "本次生成未完成" not in warning


def test_timeout_and_cancelled_get_their_own_reason():
    assert "生成超时" in reports.generation_failure_reason(
        {"generation_status": "timeout", "stage_error_code": "TIMEOUT"})
    assert "人工停止" in reports.generation_failure_reason(
        {"generation_status": "cancelled", "stage_error_code": "CANCELLED"})
    # 未知错码也要可诊断：带上出路，不给“未知错误”了事
    unknown = reports.generation_failure_reason({"stage_error_code": "WHATEVER"})
    assert "重新生成" in unknown and "WHATEVER" not in unknown


# ── 3c. 版式沿用：重跑是“同一份报告换新数据”，不是“另出一份新报告” ──────────────
# 历史缺陷：重跑分支不分情况一律只存 render_fact_report（通用「分析项 N」表格），
# 模型写的叙述与 ```chart 图表整段丢弃 —— 用户读到的是“数据新了但不是原本报告的样式”。

_ORIGINAL_BODY = """好的，为您展示企业分布。

```chart
{"title": "全国企业分布地图", "chart_type": "china_map", "columns": ["省份", "企业数"],
 "rows": [["广东", 1304]]}
```

## 核心发现

**广东**：1,304 家
"""


def _layout_body(rows='[["广东", 1500]]'):
    return ('## 核心发现\n\n```chart\n{"title": "全国企业分布地图", "chart_type": "china_map", '
            '"columns": ["省份", "企业数"], "rows": ' + rows + '}\n```\n\n**广东**：1,500 家\n')


def test_layout_extraction_keeps_structure_and_drops_data():
    layout = reports.extract_report_layout(_ORIGINAL_BODY)
    assert layout["format"] == "markdown"
    assert layout["headings"] == ["核心发现"]
    assert layout["charts"] == [{"title": "全国企业分布地图", "chart_type": "china_map",
                                 "columns": ["省份", "企业数"]}]
    # 数据不进版式：rows 正是每次重跑要换的东西
    assert "1304" not in str(layout)


def test_layout_extraction_needs_complete_chart_shape():
    """缺 chart_type/columns 的块不计入版式：形状不完整就没法校验“同一张图”，宁可退回事实表。"""
    layout = reports.extract_report_layout("## 标题\n\n```chart\n{\"title\": \"残缺\"}\n```\n")
    assert layout["charts"] == [] and layout["headings"] == ["标题"]
    assert reports.has_report_layout(layout) is True
    assert reports.has_report_layout(reports.extract_report_layout("只有一句正文，没有结构")) is False


def test_layout_contract_orders_only_data_may_change():
    contract = reports.report_layout_contract(reports.extract_report_layout(_ORIGINAL_BODY))
    assert "只换数据，不换版式" in contract
    assert "china_map" in contract and '["省份", "企业数"]' in contract
    assert "严禁沿用上一版的数字" in contract          # 照拄旧数字比换版式更坏
    assert "核心发现" in contract
    # 沉淀来的图表标题常自带时间窗（"近 7 天 · Top 15 省份"）：必须允许按本轮窗口改写，
    # 否则月度任务会挂着一个不相干的窗口骗读者（校验只看 chart_type+columns，改窗口零成本）。
    assert "时间范围按本轮实际窗口改写" in contract
    assert reports.report_layout_contract({"format": "markdown", "headings": [], "charts": []}) == ""


def test_layout_contract_skips_section_advice_when_only_charts():
    """纯图表版式（一张图、没有固定章节）不得教模型重写"章节下的结论"：那会诱导它凭空加章节。"""
    contract = reports.report_layout_contract({"format": "markdown", "headings": [], "charts": [
        {"title": "近7天案例数量趋势", "chart_type": "timeseries_line", "columns": ["日期", "案例数量"]}]})
    assert "章节下的结论文字" not in contract
    assert "章节标题逐字保留" not in contract
    assert "timeseries_line" in contract and '["日期", "案例数量"]' in contract


def test_layout_deviation_only_passes_when_structure_survives():
    layout = reports.extract_report_layout(_ORIGINAL_BODY)
    assert reports.layout_deviation(_layout_body(), layout) == []
    assert reports.layout_deviation("## 核心发现\n\n| 省份 |\n", layout)[0].startswith("缺少图表")
    assert any("缺少章节" in r for r in reports.layout_deviation(
        _layout_body().replace("## 核心发现", ""), layout))
    # 结构对上了但图里没数据 = 等于没图，不算沿用版式
    assert reports.layout_deviation(_layout_body("[]"), layout) == ["图表「全国企业分布地图」没有数据行"]


def test_html_layout_requires_a_full_document():
    layout = reports.extract_report_layout("<html><body><h2>核心结论</h2></body></html>", "html")
    assert layout == {"format": "html", "headings": ["核心结论"], "charts": []}
    assert reports.layout_deviation("## 核心结论", layout) == ["正文不是整份 HTML 文档（上一版是 HTML 报告）"]
    assert reports.layout_deviation("<!DOCTYPE html><html><h2>核心结论</h2></html>", layout) == []


def test_document_fence_and_lead_in_narration_are_stripped():
    """真机实测缺陷：模型把整份 HTML 报告包进 ```html 围栏，前面再带一句“我直接输出报告”。

    围栏与解说进隔离 iframe 会被当正文渲染（“存了但不长那样”同族），入库前必须剥掉；
    剥不出一份文档则明说原因，不拿带围栏的正文冒充沿用成功。
    """
    doc = "<!DOCTYPE html><html><head><style>b{}</style></head><body><h2>核心结论</h2></body></html>"
    answer = "基于已获取的数据，我直接输出 HTML 报告。\n\n```html\n" + doc + "\n```\n\n如需补充请提需求。"
    body, reason = reports.unwrap_document_fence(answer, "html")
    assert reason == "" and body == doc

    # 没围栏也一样：正文从文档声明起切，尾巴上的东西不留
    body, reason = reports.unwrap_document_fence("解说…\n" + doc + "\n收尾解说", "html")
    assert reason == "" and body == doc

    body, reason = reports.unwrap_document_fence("## 核心结论\n\n新数据", "html")
    assert body and reason.startswith("正文不是整份 HTML 文档")

    # Markdown：整段被 ```markdown 包起来时剥掉；正文内部的 ```chart 围栏不能动
    md = "## 核心发现\n\n```chart\n{}\n```\n"
    assert reports.unwrap_document_fence("```markdown\n" + md + "```", "markdown")[0] == md.strip()
    assert reports.unwrap_document_fence(md, "markdown")[0] == md.strip()


def test_refresh_registers_the_original_layout_and_format(monkeypatch):
    """点重跑时就把原版式抽出来随任务带走，新报告从一开始就按原格式建档。"""
    row = _chat_report(source_type="conversation", format="markdown",
                       content_object_key="ai-datahub/7/reports/1",
                       analysis_source={"origin": {"question": "查企业分布", "site_code": "cn"}})
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)
    monkeypatch.setattr(reports, "read_report_content_bytes",
                        lambda r: _ORIGINAL_BODY.encode("utf-8"))
    captured = {}
    monkeypatch.setattr(reports, "create_report",
                        lambda **kw: captured.update(kw) or {"id": 99})
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    reports.prepare_report_refresh(1, OWNER)
    source = captured["analysis_source"]
    assert source["layout"]["charts"][0]["chart_type"] == "china_map"
    assert source["layout_error"] == ""
    assert captured["format"] == "markdown"
    # 上一版交付声明过的主题随任务带走（没有则空，由提示词按平台默认主题包兜）
    assert source["report_theme"] == ""

    # 旧报告没有可沿用的结构：不猜版式，但把原因登下来（产出时要显式回显）
    monkeypatch.setattr(reports, "read_report_content_bytes",
                        lambda r: "只有一句正文，没有结构".encode("utf-8"))
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    reports.prepare_report_refresh(1, OWNER)
    assert captured["analysis_source"]["layout_error"]


def test_refresh_records_why_the_layout_could_not_be_read(monkeypatch):
    """上一版正文读不到不能悄声重跑：用户必须知道这一版为什么长得不一样。"""
    row = _chat_report(content_object_key="ai-datahub/7/reports/1",
                       analysis_source={"origin": {"question": "查企业分布"}})
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)

    def missing(r):
        raise ValueError("报告正文在对象存储里取不到了")

    monkeypatch.setattr(reports, "read_report_content_bytes", missing)
    captured = {}
    monkeypatch.setattr(reports, "create_report",
                        lambda **kw: captured.update(kw) or {"id": 99})
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    reports.prepare_report_refresh(1, OWNER)
    assert "取不到" in captured["analysis_source"]["layout_error"]


def _finish_through_fakes(monkeypatch, answer, layout, theme=""):
    """只校产出收口一处：给定模型终稿与版式，落库字段该是什么。

    提示词契约的组装不在此验证（那是 prepare_report_refresh 建档时的事，见上面那节），
    收口也不自己再判一次"要不要沿用版式"——两条路（在线流式 / 任何后续产出方）共用本函数。
    """
    finished = {}
    monkeypatch.setattr(reports, "finish_report", lambda rid, **kw: finished.update(kw) or True)
    reports._finalize_agent_report(
        99, {"title": "全国企业分布地图"},
        {"kind": "agent_question", "question": "查企业分布", "site_code": "cn",
         "layout": layout, "report_theme": theme},
        {"status": "success", "answer": answer, "_analysis_results": [
            {"status": "success", "columns": [{"name": "省份"}], "rows": [{"省份": "广东"}],
             "_security_context": {"version": 1}}]})
    return finished


def test_rerun_body_follows_the_original_layout(monkeypatch):
    """模型交回沿用原版式的正文 → 报告正文就是它（地图还在），不再被事实表盖掉。"""
    layout = reports.extract_report_layout(_ORIGINAL_BODY)
    finished = _finish_through_fakes(monkeypatch, _layout_body(), layout)
    assert finished["content"] == _layout_body().strip()
    assert "1500" in finished["content"] and "分析项 1" not in finished["content"]
    assert finished["format"] == "markdown" and finished["stage_error_code"] == ""
    assert finished["analysis_source"]["layout_result"] == {
        "status": "preserved", "format": "markdown", "reasons": []}
    # 沿用版式 + 有受治理取数结果 = 证据成立，按事实记 ready（旧口径一律 degraded 是假降级）
    assert finished["generation_status"] == "ready"
    assert finished["evidence_summary"]["completeness"] == "as_queried"


def test_rerun_that_breaks_the_layout_falls_back_with_the_reason(monkeypatch):
    layout = reports.extract_report_layout(_ORIGINAL_BODY)
    finished = _finish_through_fakes(monkeypatch, "## 核心发现\n\n新数据见下表\n", layout)
    assert "分析项 1" in finished["content"]              # 退回事实表格
    assert finished["format"] == "markdown" and finished["stage_error_code"] == "FACT_ONLY"
    result = finished["analysis_source"]["layout_result"]
    assert result["status"] == "fact_only" and "缺少图表" in result["reasons"][0]


def test_rerun_without_final_text_or_past_outbound_guard_falls_back(monkeypatch):
    layout = reports.extract_report_layout(_ORIGINAL_BODY)
    finished = _finish_through_fakes(monkeypatch, "", layout)
    assert "未交回报告正文" in finished["analysis_source"]["layout_result"]["reasons"][0]

    monkeypatch.setattr(reports, "outbound_guard_hits", lambda text: ["datasource_id"])
    finished = _finish_through_fakes(monkeypatch, _layout_body(), layout)
    assert "脱敏" in finished["analysis_source"]["layout_result"]["reasons"][0]
    assert "分析项 1" in finished["content"]


def test_html_report_refresh_keeps_html_when_the_doc_survives(monkeypatch):
    layout = {"format": "html", "headings": ["核心结论"], "charts": []}
    doc = "<!DOCTYPE html><html><body><h2>核心结论</h2><p>新数据</p></body></html>"
    finished = _finish_through_fakes(monkeypatch, doc, layout)
    assert finished["content"] == doc and finished["format"] == "html"
    assert finished["analysis_source"]["layout_result"]["status"] == "preserved"

    # 围栏 + 开场白也要能存成干净文档（否则 iframe 里会渲染出 ```html 与解说）
    finished = _finish_through_fakes(
        monkeypatch, "我直接输出报告。\n\n```html\n" + doc + "\n```", layout)
    assert finished["content"] == doc and finished["format"] == "html"
    assert finished["analysis_source"]["layout_result"]["status"] == "preserved"

    # 模型没交回整份文档→ 格式要跟着正文换回 markdown（库与渲染面不能各说一套）
    finished = _finish_through_fakes(monkeypatch, "## 核心结论\n\n新数据", layout)
    assert finished["format"] == "markdown" and "分析项 1" in finished["content"]
    assert "不是整份 HTML 文档" in finished["analysis_source"]["layout_result"]["reasons"][0]


def test_public_report_exposes_layout_status():
    row = {"id": 3, "task_id": None, "source_type": "analysis", "title": "T",
           "analysis_source": {"layout": {"format": "markdown", "headings": ["核心发现"], "charts": []},
                               "layout_result": {"status": "fact_only", "format": "markdown",
                                                 "reasons": ["缺少图表：china_map（省份/企业数）"]}}}
    out = access.public_report(row, content=False)
    assert out["layout"]["status"] == "fact_only"
    assert "缺少图表" in out["layout"]["reasons"][0]
    # 非重跑产物不下发空对象（前端据字段存在与否区分“这一版不是重跑出来的”）
    assert "layout" not in access.public_report({"id": 4, "source_type": "conversation",
                                                 "title": "T"}, content=False)


def test_finish_report_can_switch_format_with_the_body(monkeypatch):
    """正文与格式同批落下：html 报告退回事实表时要能改回 markdown，不留假格式。"""
    from backend.common import db
    writes, uploaded = [], {}
    monkeypatch.setattr(db, "execute_query",
                        lambda sql, params=(), **kw: {"owner_id": 7, "format": "html",
                                                      "content_object_key": "k"})
    monkeypatch.setattr(db, "execute_write",
                        lambda sql, params=(), **kw: writes.append(sql) or 1)
    monkeypatch.setattr(reports, "put_report_content",
                        lambda rid, owner, fmt, content: uploaded.update(fmt=fmt) or ("k", 3))
    monkeypatch.setattr(reports, "content_storage", lambda: Mock())
    assert reports.finish_report(9, generation_status="degraded", content="### x",
                                 format="markdown", analysis_source={"a": 1}) is True
    assert uploaded["fmt"] == "markdown"
    assert "format" in writes[0] and "analysis_source" in writes[0]
    with pytest.raises(ValueError, match="格式无效"):
        reports.finish_report(9, generation_status="ready", content="x", format="pdf")


# ── 4. 从报告建任务：权限码 + 配置来源 ───────────────────────────────────────
def test_create_task_from_report_requires_scheduled_manage(monkeypatch):
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report())
    from backend.core import perm_link

    def deny(user_id, workspace_id, perm_code, label=""):
        raise PermissionError(f"角色未授予「{perm_code}」权限")

    monkeypatch.setattr(perm_link, "require_write_perm", deny)
    with pytest.raises(PermissionError, match="scheduled:manage"):
        reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *")


def test_create_task_from_report_reuses_registered_question(monkeypatch):
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "查个数", "as_bot_key": "nl2sql_expert",
                                    "datasource_id": 5, "site_code": "cn"}}))
    from backend.core import perm_link
    from backend.modules.flow.services import scheduled_task_service as sts_mod
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(sts_mod.scheduled_task_service, "create_task",
                        lambda data, owner_id, workspace_id=0: captured.update(
                            {"data": data, "owner_id": owner_id, "ws": workspace_id}) or 777)
    out = reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *")
    assert out == {"task_id": 777, "name": captured["data"]["name"]}
    data = captured["data"]
    assert data["task_type"] == "agent" and captured["owner_id"] == 7
    cfg = data["task_config"]
    assert cfg["questions"][0]["question"] == "查个数"
    # 不带 AS-BOT 与数据源：能力与可查范围每次执行按属主角色重解；站点（业务名）带走
    assert "as_bot_key" not in cfg and "datasource_id" not in cfg
    assert cfg["site_code"] == "cn"


def test_create_task_from_report_validates_cron_and_question(monkeypatch):
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "查个数"}}))
    from backend.core import perm_link
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
    with pytest.raises(ValueError, match="执行周期"):
        reports.create_task_from_report(1, OWNER, cron_expression="")
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"channel": "report_center"}}))
    with pytest.raises(ValueError, match="原始分析问题"):
        reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *")


# ── 4b. 可复跑问题归位：续问（只换交付形态）不得当成分析主体 ─────────────────
# 真机缺陷：报告「direct-ip 页面访问 IP 归属地分布报告」登记的可复跑问题只剩
# “帮我输出成html形式的报告”——本轮只有 write、没取数，定时任务据此跑出来的与报告无关。

_DATA_TURN = {"role": "assistant", "message_uuid": "a1",
              "question": "查 direct-ip 页面的访问 ip 归属地分布",
              "tool_calls": [{"tool": "mcp__datahub_semantic__run_semantic_query"}],
              "content": "## 核心结论\n\n共 50 个省市有访问记录"}
_FORMAT_TURN = {"role": "assistant", "message_uuid": "a2", "question": "帮我输出成html形式的报告",
                "tool_calls": [{"tool": "mcp__datahub_workspace__write"}],
                "content": "```artifact\n{\"type\": \"html\", \"path\": \"/workspace/r.html\"}\n```"}


def _messages():
    return [{"role": "user", "content": "查 direct-ip 页面的访问 ip 归属地分布"}, _DATA_TURN,
            {"role": "user", "content": "帮我输出成html形式的报告"}, _FORMAT_TURN]


def test_reproducible_question_carries_back_to_the_turn_that_queried():
    """续问回合没取数 → 归位到实际取数那一轮，本轮诉求作为「后续要求」带走（交付形态不能丢）。"""
    question, note = reports.reproducible_question(_messages(), _messages()[3])
    assert question.startswith("查 direct-ip 页面的访问 ip 归属地分布")
    assert "帮我输出成html形式的报告" in question
    assert note == reports.QUESTION_CARRIED_NOTE


def test_reproducible_question_keeps_own_when_the_turn_did_query():
    """本轮真的取过数：用自己的问题，不往前翻（归位只治“续问”这一类）。"""
    question, note = reports.reproducible_question(_messages(), _messages()[1])
    assert question == "查 direct-ip 页面的访问 ip 归属地分布" and note == ""


def test_reproducible_question_without_any_data_turn_does_not_invent_one():
    """整段会话都没取数（纯问答后要求出报告）：不编造分析主体，照实回本轮那句。"""
    msgs = [{"role": "user", "content": "你好"},
            {"role": "assistant", "message_uuid": "x", "question": "帮我输出成html形式的报告",
             "tool_calls": [{"tool": "mcp__datahub_workspace__write"}]}]
    question, note = reports.reproducible_question(msgs, msgs[1])
    assert question == "帮我输出成html形式的报告" and note == ""


def test_reproducible_question_does_not_guess_when_the_turn_is_missing():
    """定位键对不上时不猜它是哪一轮（历史报告的消息可能已清理）。"""
    question, note = reports.reproducible_question(
        _messages(), {"message_uuid": "已清理的那条", "question": "旧的那句"})
    assert question == "旧的那句" and note == ""


def test_save_conversation_report_registers_the_carried_question(monkeypatch):
    """归档时 origin 登记归位后的问题 + 归位说明（弹窗要能如实展示“这句不是本轮原话”）。"""
    from backend.core import conversation_store
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), **kw: {
                            "id": 42, "workspace_id": 3, "title": "会话", "as_bot_key": "admin",
                            "datasource_id": 0, "site_code": "global",
                        } if "adh_conversations" in str(sql) else None, raising=True)
    monkeypatch.setattr(conversation_store, "load_messages", lambda cid, **k: _messages())
    monkeypatch.setattr(reports, "read_document_artifact",
                        lambda **kw: ("html", "<h1>direct-ip 页面访问 IP 归属地分布报告</h1>正文"))
    created = {}
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.update(kw) or {"report_id": 9})
    reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="a2")
    assert created["origin"]["question"].startswith("查 direct-ip 页面的访问 ip 归属地分布")
    assert "帮我输出成html形式的报告" in created["origin"]["question"]
    assert created["origin"]["question_note"] == reports.QUESTION_CARRIED_NOTE


# ── 4c. 从报告建任务：问题正文可就地校正，改了写回（一份报告一个口径）──────────

def _stub_task_creation(monkeypatch, captured):
    from backend.core import perm_link
    from backend.modules.flow.services import scheduled_task_service as sts_mod
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr(sts_mod.scheduled_task_service, "create_task",
                        lambda data, owner_id, workspace_id=0: captured.update(data=data) or 777)


def test_create_task_from_report_accepts_corrected_question(monkeypatch):
    """弹窗里改过的那句才是任务要跑的（登记的句子口语化/归位不准时，属主是最终裁判）。"""
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "帮我输出成html形式的报告", "site_code": "cn"}}))
    monkeypatch.setattr(reports, "_persist_report_question", lambda *a: None)
    captured = {}
    _stub_task_creation(monkeypatch, captured)
    reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *",
                                    question="查 direct-ip 的访问 ip 归属地分布，输出 HTML 报告")
    assert captured["data"]["task_config"]["questions"][0]["question"] == \
        "查 direct-ip 的访问 ip 归属地分布，输出 HTML 报告"


def test_corrected_question_is_written_back_to_the_report(monkeypatch):
    """改了就必须写回 origin：否则「用最新数据重新生成」还按旧那句跑，同一份报告两个口径。"""
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "旧的那句", "site_code": "cn"}}))
    persisted = []
    monkeypatch.setattr(reports, "_persist_report_question",
                        lambda row, origin, q: persisted.append((row, origin, q)))
    _stub_task_creation(monkeypatch, {})
    reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *", question="改后的那句")
    assert [p[2] for p in persisted] == ["改后的那句"]


def test_unchanged_question_does_not_touch_the_report(monkeypatch):
    """预填后没改动：不得白写一次库（幂等，也不该刷新 updated_at 误导排查）。"""
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "登记的那句"}}))
    spy = Mock()
    monkeypatch.setattr(reports, "_persist_report_question", spy)
    _stub_task_creation(monkeypatch, {})
    reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *", question="登记的那句")
    spy.assert_not_called()


def test_invalid_cron_does_not_rewrite_the_report_question(monkeypatch):
    """周期非法时任务没建成，就不该留下“报告问题已被改”的半截状态。"""
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"question": "登记的那句"}}))
    from backend.core import perm_link
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
    spy = Mock()
    monkeypatch.setattr(reports, "_persist_report_question", spy)
    with pytest.raises(ValueError, match="执行周期"):
        reports.create_task_from_report(1, OWNER, cron_expression="", question="改后的那句")
    spy.assert_not_called()


def test_create_task_from_report_needs_a_question_one_way_or_another(monkeypatch):
    """没登记问题的报告（手写/历史报告）不再是死路：弹窗里写一句就能建，空着则如实拒。"""
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: _chat_report(
        analysis_source={"origin": {"channel": "report_center"}}))
    from backend.core import perm_link
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)
    with pytest.raises(ValueError, match="请填写要复用的分析问题"):
        reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *")
    monkeypatch.setattr(reports, "_persist_report_question", lambda *a: None)
    captured = {}
    _stub_task_creation(monkeypatch, captured)
    out = reports.create_task_from_report(1, OWNER, cron_expression="0 9 * * *", question="新写的一句")
    assert out["task_id"] == 777 and captured["data"]["task_config"]["questions"][0]["question"] == "新写的一句"


def test_persist_report_question_marks_it_owner_edited(monkeypatch):
    """写回时打 question_edited：这句是属主亲手改的，回填/归位不得再按会话事实盖掉。"""
    row = {"id": 5, "analysis_source": json.dumps(
        {"origin": {"question": "旧的那句", "question_note": "归位说明"}})}
    written = {}
    monkeypatch.setattr("backend.common.db.execute_write",
                        lambda sql, params=(), **kw: written.update(params=params))
    reports._persist_report_question(row, json.loads(row["analysis_source"])["origin"], "改后的那句")
    saved = json.loads(written["params"][0])
    assert saved["origin"]["question"] == "改后的那句"
    assert saved["origin"]["question_edited"] is True and saved["origin"]["question_note"] == ""


def test_public_report_exposes_question_note():
    """归位说明要下发：弹窗得如实展示“这句不是本轮原话”，不能让用户以为是报告原话。"""
    row = _chat_report(analysis_source={"origin": {
        "channel": "chat", "conversation_id": 42, "question": "查个数\n后续要求：出 HTML",
        "question_note": reports.QUESTION_CARRIED_NOTE}})
    out = access.public_report(row, content=False)
    assert out["origin"]["question_note"] == reports.QUESTION_CARRIED_NOTE


# ── 5. SQL 定时不在工作空间（API 层也堵住，不只靠前端隐藏） ────────────────

def test_sql_task_cannot_be_created_via_api():
    from backend.modules.flow.api import scheduled as scheduled_api
    # 用端点真正绑定的请求模型（不是 models/schemas 里的同名旧模型）
    req = scheduled_api.ScheduledTaskCreate(name="t", task_type="query",
                              task_config={"questions": [{"title": "a", "sql": "SELECT 1"}]},
                              cron_expression="0 9 * * *")
    with pytest.raises(HTTPException) as exc:
        scheduled_api.create_scheduled_task(req, {"user_id": 7, "role": "analyst"})
    assert exc.value.status_code == 422 and "数据中台的数据同步" in exc.value.detail


def test_agent_task_is_the_default_shape():
    """不声明任务形态也给 agent：旧调用方不会因为漏字段而建出不可识别的任务。"""
    from backend.modules.flow.api.scheduled import ScheduledTaskCreate
    req = ScheduledTaskCreate(name="t", task_config={"questions": [{"question": "统计"}]},
                              cron_expression="0 9 * * *")
    assert req.task_type == "agent"


def test_rerun_keeps_the_previous_report_theme(monkeypatch):
    """配色也要"只换数据不换样子"：上一版主题随契约带走，模型不再每轮自造一套颜色。

    真机实测缺陷：无人值守把 report_theme_id 写死空串，提示词里完全没有主题令牌，
    产出的 HTML 用了一堆与字模库无关的硬编码 hex。
    """
    row = _chat_report(format="markdown", content_object_key="ai-datahub/7/reports/1",
                       analysis_source={"origin": {"question": "查企业分布", "theme": "datafoundry"}})
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)
    monkeypatch.setattr(reports, "read_report_content_bytes", lambda r: _ORIGINAL_BODY.encode("utf-8"))
    captured = {}
    monkeypatch.setattr(reports, "create_report",
                        lambda **kw: captured.update(kw) or {"id": 99})
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    reports.prepare_report_refresh(1, OWNER)
    assert captured["analysis_source"]["report_theme"] == "datafoundry"

    layout = captured["analysis_source"]["layout"]
    contract = reports.report_layout_contract(layout, "datafoundry")
    # 这段是给模型的提示词（AI 侧），不随 UI 术语改名：模型不会把指令原样写进正文，
    # 改了只会让提示词基线飘移，所以这里仍断言「报表」。
    assert "配色沿用上一版报表的主题 datafoundry" in contract
    assert reports.report_layout_contract(layout, "") .find("配色沿用") == -1

    finished = _finish_through_fakes(monkeypatch, _layout_body(), layout,
                                    theme="datafoundry")
    assert finished["format"] == "markdown"
    assert finished["analysis_source"]["layout_result"]["status"] == "preserved"


def test_theme_contract_is_registered_on_save(monkeypatch):
    """存报告时把 ```artifact 声明的 theme 写进 origin —— 重跑沿用它，而不是重新猜。"""
    answer = {"role": "assistant", "message_uuid": "m1", "question": "出个 HTML 报告",
              "content": ("```artifact\n" + json.dumps(
                  {"type": "html", "filename": "报告.html", "path": "报告.html",
                   "theme": "finance"}) + "\n```"),
              "result": {}}
    from backend.core import conversation_store
    monkeypatch.setattr("backend.common.db.execute_query",
                        lambda sql, params=(), **kw: {
                            "id": 42, "workspace_id": 3, "title": "会话", "as_bot_key": "admin",
                            "datasource_id": 0, "site_code": "cn"}
                        if "adh_conversations" in str(sql) else None, raising=True)
    monkeypatch.setattr(conversation_store, "load_messages", lambda cid, **k: [answer])
    monkeypatch.setattr(reports, "read_document_artifact",
                        lambda **kw: ("html", "<!DOCTYPE html><html><h1>访问分布报告</h1></html>"))
    created = {}
    monkeypatch.setattr(reports, "create_content_report",
                        lambda **kw: created.update(kw) or {"report_id": 9})
    reports.save_conversation_report(user=OWNER, conversation_id=42, message_uuid="m1")
    assert created["origin"]["theme"] == "finance"


# ── 5. 报告模板→统一版式契约（定时任务/问数/报告中心同一口径）────────────────

def test_resolve_report_template_reads_vis_library(monkeypatch):
    """模板单一真值源 = 字模库 report_template；新模板直读 layout/theme，旧模板从正文抽版式。"""
    import backend.modules.viz.services.vis_library_service as vls
    comp = {"id": 5, "name": "周报模板", "category": "report_template", "is_active": 1,
            "style_config": {"format": "markdown", "theme": "tech",
                             "layout": {"format": "markdown", "headings": ["核心结论"], "charts": []}}}
    monkeypatch.setattr(vls.vis_library_service, "get_component", lambda cid: dict(comp))
    tpl = reports.resolve_report_template("5")
    assert tpl["found"] and tpl["theme"] == "tech" and tpl["layout"]["headings"] == ["核心结论"]

    # 旧模板只有 Jinja2 正文：从正文抽骨架，新老收敛到同一形状
    legacy = {"id": 6, "name": "旧模板", "category": "report_template", "is_active": 1,
              "style_config": {"format": "markdown",
                               "content": "## 概览\n\n```chart\n{\"chart_type\":\"bar\",\"columns\":[\"省份\"]}\n```"}}
    monkeypatch.setattr(vls.vis_library_service, "get_component", lambda cid: dict(legacy))
    old = reports.resolve_report_template("6")
    assert old["found"] and old["layout"]["headings"] == ["概览"]
    assert old["layout"]["charts"][0]["chart_type"] == "bar"


def test_resolve_report_template_missing_is_loud(monkeypatch):
    """缺失/下架/空键均回 found=False + 原因（调用方据此 fail-loud，不静默退事实表）。"""
    import backend.modules.viz.services.vis_library_service as vls
    assert reports.resolve_report_template("")["found"] is False
    monkeypatch.setattr(vls.vis_library_service, "get_component",
                        lambda cid: {"id": 7, "category": "report_template", "is_active": 0})
    out = reports.resolve_report_template("7")
    assert out["found"] is False and "下架" in out["reason"]


def test_render_template_report_preserves_or_falls_back_explicitly():
    """单问且终稿达标 → 沿用版式；多问/不达标 → 退事实表格并把原因带在 layout_result。"""
    layout = {"format": "markdown", "headings": ["核心结论"],
              "charts": [{"title": "", "chart_type": "bar", "columns": ["省份"]}]}
    good = "## 核心结论\n\n```chart\n{\"chart_type\":\"bar\",\"columns\":[\"省份\"],\"rows\":[[\"广东\"]]}\n```"
    results = [{"status": "success", "columns": ["省份"], "rows": [{"省份": "广东"}]}]
    content, fmt, res = reports.render_template_report("报告", results, good, layout, single_question=True)
    assert res["status"] == "preserved" and "```chart" in content and fmt == "markdown"

    # 多问题：逐问终稿不等于整份报告 → 确定性事实表格，原因可见
    _, _, res2 = reports.render_template_report("报告", results, good, layout, single_question=False)
    assert res2["status"] == "fact_only" and res2["reasons"]

    # 单问但终稿不达版式（缺章节）→ 退事实表并记下缺什么
    _, _, res3 = reports.render_template_report("报告", results, "随便一段没有结构的正文", layout, single_question=True)
    assert res3["status"] == "fact_only" and any("核心结论" in r for r in res3["reasons"])


# ── 3b. 在线重跑：过程可见 · 离开即终止 ──────────────────────────────────────

def _stream_plan(**over):
    plan = {"report_id": 99, "run_id": "r1", "title": "全国企业分布地图",
            "question": "查个数", "config": {"site_code": "cn", "report_theme": "",
                                             "layout_contract": ""},
            "report": {"id": 99, "title": "全国企业分布地图"}, "source": {
                "kind": "agent_question", "question": "查个数", "layout": {},
                "layout_error": "上一版正文没有可复用的版式结构"},
            "identity": {"user_id": 7}}
    plan.update(over)
    return plan


def _gate_stub(monkeypatch):
    """并发闸门桩：报告重跑与 Chat 共用同一个闸门，测试里直接给一个空位。"""
    from backend.modules.mind.execution import capacity

    class _Gate:
        async def acquire(self, on_queued=None):
            return object()

        def release(self, handle):
            pass

    monkeypatch.setattr(capacity, "get_gate", lambda: _Gate())


def _collect(monkeypatch, plan, analyze, disconnected=None):
    """跑一遍流式重跑，回 ``(帧列表, 落库字段)``。

    播报协程结束后再给它一点时间：客户端离开时落库由 drive 自己收尾，
    不等它就断言等于没测到“拆两个协程”要解决的那个问题（行卡在 running）。
    """
    import asyncio

    from backend.modules.mind.execution import scheduled_analysis
    monkeypatch.setattr(scheduled_analysis, "analyze_question", analyze, raising=True)
    _gate_stub(monkeypatch)
    monkeypatch.setattr(reports, "REFRESH_TICK_S", 0.02)
    monkeypatch.setattr(reports, "REFRESH_ABORT_GRACE_S", 0.1)
    finished = {}
    monkeypatch.setattr(reports, "finish_report",
                        lambda rid, **kw: finished.update(kw) or True)

    async def main():
        frames = []

        async def pump():
            async for chunk in reports.stream_report_refresh(plan, disconnected):
                frames.append(chunk)

        task = asyncio.ensure_future(pump())
        await asyncio.wait([task], timeout=5)
        for _ in range(300):
            if finished.get("generation_status"):
                break
            await asyncio.sleep(0.01)
        if task.done() and not task.cancelled():
            task.result()          # 流里的异常必须把测试打，不留“没报错就是对的”
        return frames

    return asyncio.run(main()), finished


def _analyze_ok():
    async def fake(question, config, identity, check=None, on_event=None, cancel_event=None):
        if on_event:
            on_event({"type": "data", "query_count": 1, "row_count": 12,
                     "truncated": False, "source": "run_semantic_query"})
            on_event({"type": "tool_start", "tool_call_id": "t1",
                      "tool": "mcp__datahub_x__run_semantic_query", "call_index": 1})
            on_event({"type": "tool_result", "tool_call_id": "t1",
                      "tool": "mcp__datahub_x__run_semantic_query", "ok": True, "elapsed": 2.34})
        return {"status": "success", "answer": "## 核心发现\n\n12 行",
                "_analysis_results": [{"status": "success", "columns": [{"name": "省份"}],
                                       "rows": [{"省份": "广东"}], "row_count": 12,
                                       "truncated": False, "source": "run_semantic_query",
                                       "_security_context": {"version": 1}}]}
    return fake


def test_refresh_stream_publishes_progress_and_finishes_ready(monkeypatch):
    """过程逐条下发（取数/工具步/终态），终态与收口用的是同一处判定：ready 不再假降级。"""
    frames, finished = _collect(monkeypatch, _stream_plan(), _analyze_ok())
    text = "".join(f.decode() for f in frames)
    assert "event: stage" in text and "data: " in text
    assert "run_semantic_query" in text            # 取数步可见（通道业务名 + 行数）
    assert '"row_count": 12' in text               # 数字以结构化字段下发，文案由前端拼
    assert "语义取数" not in text                   # 人话在前端翻，服务端不下发界面文案
    assert "event: done" in text and '"generation_status": "ready"' in text
    assert finished["generation_status"] == "ready"
    assert finished["evidence_summary"]["query_count"] == 1
    # 事实表格/工具步都不带 SQL 与物理标识（护栏 §7）
    assert "SELECT" not in text.upper()


def test_refresh_stream_tools_survive_without_sql_leak(monkeypatch):
    """工具事件只带名字与耗时：原始返回体（可能含表列与 SQL）不进直播通道。"""
    frames, _ = _collect(monkeypatch, _stream_plan(), _analyze_ok())
    text = "".join(f.decode() for f in frames)
    assert "tool_call_id" in text and "2.34" in text
    assert "adh_" not in text and "password" not in text


def test_refresh_stream_aborts_when_viewer_leaves(monkeypatch):
    """断开 → 中止本轮 → 新版本记 cancelled（不是 timeout，也不是 failed）。"""
    import asyncio

    seen = {}

    async def fake(question, config, identity, check=None, on_event=None, cancel_event=None):
        from backend.core.task_runtime import RunInterrupted
        seen["cancel_event"] = cancel_event
        while not cancel_event.is_set():
            await asyncio.sleep(0.01)
        raise RunInterrupted("cancelled")

    calls = {"n": 0}

    async def left():
        calls["n"] += 1
        return calls["n"] > 1        # 第一拍还在，第二拍就发现客户端走了

    frames, finished = _collect(monkeypatch, _stream_plan(), fake, disconnected=left)
    assert frames and "event: done" not in "".join(f.decode() for f in frames)
    assert seen["cancel_event"].is_set(), "离开必须把中止标志交给分析侧"
    assert finished["generation_status"] == "cancelled"
    assert finished["stage_error_code"] == "CLIENT_ABORTED"


def test_refresh_stream_kills_a_stalled_run_after_grace(monkeypatch):
    """中止请求 ≠ 一定能停下来（SDK 流可能不再吐消息）：宽限期后硬取消，仍落 cancelled。"""
    import asyncio

    async def deaf(question, config, identity, check=None, on_event=None, cancel_event=None):
        await asyncio.sleep(30)         # 装作一直卡在模型侧，永不理会 cancel_event
        return {"status": "success", "answer": "x"}

    async def gone():
        return True

    frames, finished = _collect(monkeypatch, _stream_plan(), deaf, disconnected=gone)
    assert finished["generation_status"] == "cancelled"
    assert finished["stage_error_code"] == "CLIENT_ABORTED"


def test_refresh_stream_reports_analysis_failure(monkeypatch):
    """分析侧显式失败：终态与原因一并落到 done 事件（不留"生成中"的假状态）。"""
    async def failed(question, config, identity, check=None, on_event=None, cancel_event=None):
        return {"status": "failed", "error_code": "NO_QUERY_RESULT"}

    frames, finished = _collect(monkeypatch, _stream_plan(), failed)
    text = "".join(f.decode() for f in frames)
    assert '"generation_status": "failed"' in text and "NO_QUERY_RESULT" in text
    assert finished["stage_error_code"] == "NO_QUERY_RESULT"
    assert "本轮没有产出任何已授权的取数结果" in reports.generation_failure_reason(
        {"stage_error_code": "NO_QUERY_RESULT"})


def test_queue_side_no_longer_runs_agent_question_reports(monkeypatch):
    """报告重跑不再经队列执行：老行被重投递到这里要显式判失败并给出去路（§1 不静默）。"""
    row = {"id": 9, "owner_id": 7, "workspace_id": 3, "title": "T", "format": "markdown",
           "analysis_source": json.dumps({"kind": "agent_question", "question": "查个数"})}
    finished = []
    monkeypatch.setattr(reports, "load_report", lambda rid: dict(row))
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda uid, ws: {"user_id": uid})
    monkeypatch.setattr(reports, "finish_report",
                        lambda rid, **kw: finished.append(kw) or True)
    assert reports.run_report(9) == {"report_id": 9}
    assert finished[0]["generation_status"] == "failed"
    assert finished[0]["stage_error_code"] == "REFRESH_STREAM_REQUIRED"
    assert "在线执行" in reports.generation_failure_reason(
        {"stage_error_code": "REFRESH_STREAM_REQUIRED"})


def test_stale_running_version_says_the_producer_is_gone():
    """重跑被进程重启打断后，租约过期就不再说"正在生成中，稍后自动刷新"。

    那是把"没人在产这一版"讲成"还在跑"，用户只能干等；把它翻成 timeout 的清理任务是
    另一条腿（runs_reconcile），读侧必须能只按租约这个事实把实况说清楚（不改状态）。
    """
    stale = {"generation_status": "running", "lease_expires_at": "2026-11-11 09:00:00"}
    assert reports.lease_expired(stale) is True
    reason = reports.data_report_block_reason(stale)
    assert "已中断" in reason and "重新生成" in reason

    fresh = {"generation_status": "queued", "lease_expires_at": "2999-01-01 00:00:00"}
    assert reports.lease_expired(fresh) is False
    assert "正在生成中" in reports.data_report_block_reason(fresh)

    # 内容型报告没有租约值：不得凭空判成中断（NULL 不是"已过期"）
    assert reports.lease_expired({"generation_status": "ready", "lease_expires_at": None}) is False
