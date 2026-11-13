"""M5 场景绑模板：分析场景 → 报告模板 → 版式契约（选择/写入/注入/复盘四段各锁一条线）。

方案 §8-M5 的验收是"模板以业务名选择、不可手填 id；模板读不到显式失败"。这三条各自对应
一个真实缺陷形态，所以都按文本锁：

1. **措辞档位**（`report_layout_contract(framing=...)`）：模板出稿是**第一次**按该版式，
   没有"上一版"。沿用重跑措辞会让模型以为模板正文里的示例数字是可以沿用的旧数据 ——
   一份"版式对、数字是模板示例"的报告比版式错了更坏。
2. **口径只认业务名**：场景 metadata 存名字（`quick_paths` 一路带到提示词），写入点拒 id、
   拒读不到的名字；候选接口不回字模 id（递过去就等于让人手填）。
3. **不静默**（no-silent-degradation）：模板下架/无版式/多场景冲突都要在提示词里说出来，
   不得默默按通用骨架出一份"看起来按模板"的报告。
"""
import datetime as dt
import json

import pytest
from fastapi import HTTPException

from backend.core import task_runtime
from backend.modules.mind.execution import prompt_composer as pc
from backend.modules.mind.execution import quick_paths as qp
from backend.modules.viz.services import report_service as reports

_LAYOUT = {"format": "markdown", "headings": ["核心结论", "关键指标"],
           "charts": [{"title": "各省 PV", "chart_type": "bar", "columns": ["省份", "PV"]}]}


# ── 1. 版式契约的两种语境 ──────────────────────────────────────────

def test_template_faming_does_not_claim_a_previous_version():
    """模板档位不得说"沿用上一版"：本轮是第一次按该模板出稿，没有上一版可抄。"""
    contract = reports.report_layout_contract(_LAYOUT, "finance", framing="template",
                                              origin="分析场景「渠道诊断」绑定的报告模板「周报模板」")
    assert "按分析场景「渠道诊断」绑定的报告模板「周报模板」出稿" in contract
    assert "结构照模板" in contract
    assert "上一版" not in contract
    # 模板正文里的数字是示例，不是"可以沿用的旧数据"
    assert "模板正文里的示例数字一个都不许写进本次报告" in contract
    assert "配色按模板指定的主题 finance" in contract
    # 版式硬要求与重跑档同一份（章节逐字、图位形状不变）
    assert "章节标题逐字保留" in contract and '"chart_type": "bar"' in contract


def test_rerun_faming_wording_is_unchanged():
    """默认档位（报表重跑）措辞不回潮成模板口径 —— 那段文字是事后达标校验的同源依据。"""
    contract = reports.report_layout_contract(_LAYOUT, "finance")
    assert "只换数据，不换版式" in contract
    assert "严禁沿用上一版的数字" in contract
    assert "配色沿用上一版报表的主题 finance" in contract
    assert "示例数字" not in contract


def test_reference_block_relabels_for_template_faming():
    """带参照正文时标题也要跟着语境走：模板语境下没有"上一版报表正文"这种东西。"""
    body = "## 核心结论\n\n示例数字 1234\n\n```chart\n{\"title\": \"x\", \"chart_type\": \"bar\", \"columns\": [\"a\", \"b\"]}\n```\n"
    rerun = reports.report_layout_contract(_LAYOUT, "", body)
    assert "### 上一版报表正文（仅作版式参照）" in rerun
    tpl = reports.report_layout_contract(_LAYOUT, "", body, framing="template")
    assert "### 报告模板正文（仅作版式参照）" in tpl
    assert "上一版" not in tpl and "数值一律不得沿用" in tpl
    # 截断提示跟着语境走（旧文字会说“上一版正文过长”，模板语境里那是假的）
    long_ref = body + "## 附录\n\n" + "很长的正文" * 9000
    assert "模板正文过长" in reports.report_layout_contract(
        _LAYOUT, "", long_ref, framing="template")
    assert "上一版正文过长" in reports.report_layout_contract(_LAYOUT, "", long_ref)


def test_scheduled_task_uses_the_template_faming():
    """定时任务选定模板 → 走模板档位（executor 只传档位，措辞仍在报表域一处生成）。

    源码级断言：这段解析内联在 `_execute_core` 里，端到端跑它要桩掉整个调度壳；
    而这里要锁的是“档位传了没有”，不是调度壳的行为（先例见 test_decision_report_block）。
    """
    import inspect

    from backend.modules.flow.tasks import executor

    src = inspect.getsource(executor._execute_core)
    idx = src.find('config["layout_contract"]')
    assert idx >= 0, "executor 不再拼版式契约了（改动本用例前先同步措辞档位口径）"
    block = src[idx:idx + 400]
    assert 'framing="template"' in block
    assert "origin=" in block


def test_layout_contract_callback_names_are_registered():
    """跨模块解析模板必须走回调表：未登记名会在运行期 KeyError（而不是"没模板"）。"""
    for name in ("report.resolve_template_by_name", "report.layout_contract",
                 "report.list_templates"):
        assert name in task_runtime._CALLBACKS, name


# ── 2. 场景携带业务名 ─────────────────────────────────────────────

def _row(metadata: dict) -> dict:
    return {"id": 7, "title": "渠道诊断", "content": "先查 PV 再查转化", "metadata": metadata,
            "related_tables": "", "priority": 0, "workspace_id": 0, "datasource_id": 0}


def test_quick_path_normalize_carries_template_name():
    assert qp._normalize(_row({"triggers": ["渠道"], "report_template": " 周报模板 "}))["report_template"] == "周报模板"
    assert qp._normalize(_row({"triggers": ["渠道"]}))["report_template"] == ""
    # metadata 是 JSON 字符串（DB 列实际形态）时同样取到
    assert qp._normalize(_row(json.dumps({"report_template": "日报模板"}, ensure_ascii=False)))["report_template"] == "日报模板"


def test_template_name_reaches_the_prompt_payload():
    """命中载荷必须带模板名：不带就等于配了模板但没人消费（注入端拿不到事实）。"""
    payload = qp._hit_payload(qp._normalize(_row({"report_template": "周报模板"})), ["渠道"])
    assert payload["report_template"] == "周报模板"


# ── 3. 注入端：命中即契约，读不到即显式失败 ───────────────────────

def _stub_callbacks(monkeypatch, *, resolve=None, contract=None):
    def _resolve(name):
        return resolve(name) if resolve else {"found": True, "name": name, "theme": "",
                                              "layout": _LAYOUT}

    def _contract(layout, theme="", reference="", framing="rerun", origin=""):
        # 默认档（重跑）会说“沿用上一版”，模板语境没有上一版可沿 —— 档位必须由注入端传
        assert framing == "template", "场景模板契约必须传 framing=template"
        return contract if contract is not None else "## 报表版式要求（契约正文）"

    monkeypatch.setitem(task_runtime._CALLBACKS, "report.resolve_template_by_name", _resolve)
    monkeypatch.setitem(task_runtime._CALLBACKS, "report.layout_contract", _contract)


def test_no_scene_bound_template_costs_nothing():
    """没绑模板的回合一个字都不加（常驻段为它付费是方案的预算纪律红线）。"""
    assert pc._scene_template_contract_lines([{"title": "a"}], []) == []
    assert pc._scene_template_contract_lines([], []) == []
    assert pc._scene_template_contract_lines(None, None) == []


def test_hit_scene_injects_the_layout_contract(monkeypatch):
    _stub_callbacks(monkeypatch)
    lines = pc._scene_template_contract_lines(
        [{"title": "渠道诊断", "report_template": "周报模板"}], ["render_html"])
    text = "\n".join(lines)
    assert "## 分析场景绑定的报告模板（版式契约）" in text
    assert "「渠道诊断」绑定了报告模板「周报模板」" in text
    assert "## 报表版式要求（契约正文）" in text
    # 渲染机已授权 → 必须补那句桥接，否则模型会把整份文档贴进对话正文（引用块白做）
    assert "与已授权的交付通道同时生效" in text
    assert "`render_html`" in text and "`render_chart`" not in text


def test_bridge_names_only_granted_channels(monkeypatch):
    """桥接句不能把未授权的渲染机说成出路（与契约按授权粒度注入同一条纪律）。"""
    _stub_callbacks(monkeypatch)
    only_spec = "\n".join(pc._scene_template_contract_lines(
        [{"title": "渠道诊断", "report_template": "周报模板"}], ["render_report"]))
    assert "`render_report`" in only_spec
    assert "`render_html`" not in only_spec and "`render_chart`" not in only_spec


def test_bridge_line_is_not_claimed_without_render_tools(monkeypatch):
    _stub_callbacks(monkeypatch)
    text = "\n".join(pc._scene_template_contract_lines(
        [{"title": "渠道诊断", "report_template": "周报模板"}], []))
    assert "与已授权的交付通道同时生效" not in text


def test_unavailable_template_fails_loudly(monkeypatch):
    """模板下架/删除：注入一段显式失败并给可用清单，不得静默按通用骨架冒充套过模板。"""
    _stub_callbacks(monkeypatch, resolve=lambda name: {
        "found": False, "reason": f"报告模板「{name}」不存在或已下架", "available": ["日报模板", "周报模板"]})
    text = "\n".join(pc._scene_template_contract_lines(
        [{"title": "渠道诊断", "report_template": "幽灵模板"}], []))
    assert "## 分析场景绑定的报告模板未生效" in text
    assert "不存在或已下架" in text and "日报模板" in text
    assert "不得假装已套用" in text
    assert "## 报表版式要求" not in text


def test_template_without_layout_is_named_not_silent(monkeypatch):
    """模板只有示例正文、没登记可用版式：契约会是空串，此时必须明说而不是灌空段。"""
    _stub_callbacks(monkeypatch, contract="")
    text = "\n".join(pc._scene_template_contract_lines(
        [{"title": "渠道诊断", "report_template": "空白模板"}], []))
    assert "没有登记可用版式" in text and "未生效" in text


def test_resolve_crash_is_reported_as_failure(monkeypatch):
    """解析口抛异常（库不可用等）：记成显式失败，不把异常塞进提示词组装主链路。"""
    def boom(name):
        raise RuntimeError("db down")

    _stub_callbacks(monkeypatch, resolve=boom)
    text = "\n".join(pc._scene_template_contract_lines(
        [{"title": "渠道诊断", "report_template": "周报模板"}], []))
    assert "模板读取失败" in text and "未生效" in text


def test_only_one_layout_applies_per_turn(monkeypatch):
    """多场景各绑模板时一轮只套一份，另一份必须写明未套用（不是悄悄挑一个）。"""
    _stub_callbacks(monkeypatch)
    text = "\n".join(pc._scene_template_contract_lines(
        [{"title": "场景甲", "report_template": "周报模板"},
         {"title": "场景乙", "report_template": "日报模板"}], []))
    assert "「场景甲」绑定了报告模板「周报模板」" in text
    assert "「场景乙」也绑定了模板「日报模板」" in text
    assert "一轮只套一份版式" in text


def test_compose_prompt_wires_the_section_only_when_bound(monkeypatch):
    """端到端：compose_system_prompt 只在场景真绑模板时多这一段（预算纪律）。"""
    _stub_callbacks(monkeypatch)
    bound = pc.compose_system_prompt(None, [], username="u", user_role="viewer",
                                     quick_paths=[{"title": "渠道诊断", "content": "步骤",
                                                   "report_template": "周报模板", "pinned": True}])
    assert "## 分析场景绑定的报告模板（版式契约）" in bound
    plain = pc.compose_system_prompt(None, [], username="u", user_role="viewer",
                                     quick_paths=[{"title": "渠道诊断", "content": "步骤"}])
    assert "报告模板（版式契约）" not in plain


# ── 4. 配置面写入校验：只认业务名 ─────────────────────────────────

def _stub_template_list(monkeypatch, found: dict):
    monkeypatch.setitem(task_runtime._CALLBACKS, "report.resolve_template_by_name",
                        lambda name: found)


@pytest.mark.parametrize("metadata,expect", [
    ({"report_template": "周报模板"}, "pass"),
    ({"report_template": "周报模板 "}, "pass"),
    ({}, "pass"),
    ({"report_template": ""}, "pass"),
    ({"report_template": "不存在的"}, 422),
    ({"report_template": "1783069459276"}, 422),      # 手填 id：直接拒
])
def test_write_time_template_validation(monkeypatch, metadata, expect):
    from backend.modules.auth.api import knowledge

    found = {"found": "周报模板" in str(metadata.get("report_template") or ""),
             "reason": "报告模板不存在或已下架", "available": ["日报模板"]}
    _stub_template_list(monkeypatch, found)
    if expect == "pass":
        knowledge._assert_scenario_template("quick_path", metadata)
    else:
        with pytest.raises(HTTPException) as exc:
            knowledge._assert_scenario_template("quick_path", metadata)
        assert exc.value.status_code == expect
        if expect == 422:
            assert exc.value.detail
    # 非 quick_path 类型不接管（推荐问题/追问案例没有模板概念）
    knowledge._assert_scenario_template("recommend_question", {"report_template": "幽灵模板"})


def test_write_time_list_failure_is_not_success(monkeypatch):
    """清单读不到时不能"当没绑"存下来 —— 那会把配置缺口推到运行时才炸。"""
    from backend.modules.auth.api import knowledge

    def boom(name):
        raise RuntimeError("db down")

    monkeypatch.setitem(task_runtime._CALLBACKS, "report.resolve_template_by_name", boom)
    with pytest.raises(HTTPException) as exc:
        knowledge._assert_scenario_template("quick_path", {"report_template": "周报模板"})
    assert exc.value.status_code == 502


def test_candidates_endpoint_returns_names_not_ids(monkeypatch):
    """候选清单只回业务名与形状：把字模 id 递到前端就等于允许人手填它。"""
    from backend.modules.auth.api import knowledge

    rows = [{"name": "周报模板", "format": "markdown", "has_layout": True, "is_active": 1,
             "headings": ["本周概览", "趋势变化"], "id": "42", "code": "tpl-weekly"},
            {"name": "已下架模板", "format": "markdown", "has_layout": True, "is_active": 0,
             "headings": [], "id": "43"},
            {"name": "空白模板", "format": "markdown", "has_layout": False, "is_active": 1,
             "headings": [], "id": "44"}]
    monkeypatch.setitem(task_runtime._CALLBACKS, "report.list_templates", lambda: rows)
    monkeypatch.setattr(knowledge, "_assert_type_perm", lambda user, ktype: None)
    out = knowledge.list_scenario_report_templates({"user_id": 1})["items"]
    assert [i["name"] for i in out] == ["周报模板", "空白模板"]
    assert not any("id" in i or "code" in i for i in out)
    assert out[0]["sections"] == 2 and out[1]["usable"] is False


# ── 5. 选道分布复盘（M5 的"先看效果"落地件）──────────────────────

def _span(channel, *, status="success", doc_id="", blockers=None, notes=None, attempt=1,
          trace="t1", entrypoint="chat", name=None, raw=None):
    payload = {"channel": channel, "doc_id": doc_id, "blockers": blockers or [],
               "notes": notes or [], "attempt": attempt}
    return {"trace_id": trace, "name": name or f"render_{channel}", "status": status,
            "output_text": raw if raw is not None else json.dumps(payload, ensure_ascii=False),
            "started_at": dt.datetime(2026, 11, 13, 10, 0, 0), "entrypoint": entrypoint}


def test_audit_counts_channels_and_primary_share():
    from backend.scripts import render_channel_audit as audit

    rows = [_span("chart"), _span("chart"), _span("html", doc_id="d1"),
            _span("spec", doc_id="d2"), _span("html", status="error", blockers=["外部资源"])]
    s = audit.summarize(rows)
    assert s["spans"] == 5
    assert s["channels"]["chart"]["calls"] == 2 and s["channels"]["chart"]["docs"] == 0
    assert s["channels"]["html"]["error"] == 1 and s["channels"]["html"]["ok"] == 1
    # 占比只按**落成的交付件**算：那条被打回的 html 不该再占一次份额
    assert s["primary_share"]["html"] == 0.5 and s["primary_share"]["spec"] == 0.5
    assert s["blockers"]["html"]["外部资源"] == 1
    assert "交付形态选道分布" in audit.render_text(s, 14)


def test_audit_reads_legacy_and_truncated_payloads():
    """旧写法（channel=doc）归入 html；截断过的回执靠 span 名归位；两条路都不通才算未归因。"""
    from backend.scripts import render_channel_audit as audit

    rows = [_span("doc", doc_id="d9"),
            _span("chart", raw='{"channel": "chart", "atte'),      # 截断：JSON 解析失败但 name 可用
            {"trace_id": "t3", "name": "", "status": "success", "output_text": "{",
             "started_at": dt.datetime(2026, 11, 13), "entrypoint": "chat"}]
    s = audit.summarize(rows)
    assert s["channels"]["html"]["calls"] == 1 and s["channels"]["chart"]["calls"] == 1
    assert s["unattributed"] == 1
    assert "解析不出通道" in audit.render_text(s, 14)


def test_audit_refuses_to_invent_a_distribution():
    """零样本必须说"没有样本"，不给百分比（否则会把"没上线"读成"效果差"）。"""
    from backend.scripts import render_channel_audit as audit

    s = audit.summarize([])
    lines = audit.verdict(s)
    assert any("没有选道记录" in ln for ln in lines)
    assert not any("%" in ln for ln in lines)


def test_audit_flags_html_dominance_with_the_soft_constraint_question():
    """§10 待确认 1 的判据入口：html 明显压过 spec 时提示评估软约束（并提醒先抽查）。"""
    from backend.scripts import render_channel_audit as audit

    rows = [_span("html", doc_id=f"d{i}") for i in range(9)] + [_span("spec", doc_id="s1")]
    text = audit.render_text(audit.summarize(rows), 14)
    assert "整页文档 html 90%" in text
    assert "§10" in text and "别按占比直接收权" in text


def test_audit_reports_retry_peaks():
    """重试次数（attempt>1）就是"第一份被打回过"：没有它会把反复试出来的一份交付读成一次成功。"""
    from backend.scripts import render_channel_audit as audit

    rows = [_span("html", attempt=1, status="error", blockers=["缺 :root 令牌块"]),
            _span("html", attempt=2, status="error", blockers=["缺 :root 令牌块"]),
            _span("html", attempt=3, doc_id="d1")]
    s = audit.summarize(rows)
    assert s["channels"]["html"]["max_attempt"] == 3
    assert s["channels"]["html"]["retried"] == 2      # attempt>1 的两条
    assert s["entrypoints"] == {"chat": 3}
    assert "重试" in audit.render_text(s, 14)
