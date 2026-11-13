"""技能作业细则的按需加载（渐进披露 A2）回归。

锁定口径：
- **三层披露**：metadata（name+description）常驻；正文由**模型自己**调 `load_skill_guide` 取回；
- **授权按事实自动接入**：助手绑了技能就接入壳工具（与平台功能惰性壳同一口径），
  管理员显式配置过 `mcp.skills` 时尊重配置，执行层 ceiling 同样管得住它；
- **未接入 = 回到老行为，不是能力倒退**：清单里没有该工具时，装载策略整体退回
  "按触发词自动展开正文"，且提示词里**不得**出现该工具名（不承诺调不到的入口）；
- **三条强制展开不变**：pinned / 单职责角色 / 无人值守 force_all 仍常驻正文；
- **原因可诊断**：零命中与"命中了但被名额挤掉"必须写成两句话（历史误诊点）；
- **只回已绑定技能**：未绑定的剧本属别的助手，拒收并回可加载清单（不猜名字）。

离线运行：不连库（技能正文走文件加载器），观测 span 用替身捕获。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.mind.execution import prompt_composer as pc
from backend.modules.mind.execution import tool_policy
from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
from backend.modules.mind.execution.sdk_tools import skill_tools as skt
from backend.modules.mind.execution.sdk_tools.context import (
    ExecutionContextVar, set_execution_context)


def _skill(name, *, display=None, desc="只做演示用", body=None, triggers=None, requires=None):
    return {"name": name, "display_name": display or name, "description": desc,
            "system_prompt": body or f"{name} 的作业细则正文" * 40,
            "triggers": triggers or [], "requires_any": requires or [],
            "exclusive_group": ""}


def _caps(names):
    return {"tools": [{"name": n, "description": ""} for n in names], "unavailable_tools": []}


SEM = "mcp__datahub_semantic__run_semantic_query"
GUIDE = "mcp__datahub_skills__load_skill_guide"


# ── 1. 注册与可见性：机制入口已接线，但**不进授权面**─────────────────────
def test_mechanism_group_is_registered_but_not_rendered_in_the_panel(monkeypatch):
    """渐进披露是机制不是能力开关。

    AS-BOT 面板的工具目录是 `TOOL_SERVER_TOOLS` 全量枚举，所以一旦把机制组当成普通组登记，
    面板就长出一个“默认生效但显示未勾”的复选框 —— 管理员会以为“没启用”而随手勾一下，
    从此该助手被显式配置钉住，未来改默认策略时顶不动它。同一先例：平台功能惰性壳 list/run
    也不在授权面，只随角色权限码自动接入。
    """
    import inspect

    from backend.modules.mind.execution.sdk_tools import MECHANISM_GROUPS
    from backend.modules.platform.api import as_bots as as_bots_api
    from backend.core.role_service import role_service

    assert "skills" in TOOL_SERVER_TOOLS, "未注册就是机制没接线（工具到不了模型手上）"
    assert "skills" in MECHANISM_GROUPS

    src = inspect.getsource(as_bots_api.tool_catalog)
    assert "MECHANISM_GROUPS" in src, "工具目录又变成全量枚举，机制开关会重新长出复选框"

    monkeypatch.setattr(role_service, "get_user_role_ai_perms", lambda *a, **kw: {})
    payload = as_bots_api.tool_catalog(user={"user_id": 1, "workspace_id": 1})
    rendered = {g["group"] for g in payload["groups"]}
    assert "skills" not in rendered, "机制组不得出现在 AS-BOT 授权面"
    assert "semantic" in rendered, "正常能力组仍要渲染（不能一刀切把目录清空）"


def test_mechanism_group_has_no_orphan_panel_metadata():
    """不在目录渲染就不登记标签：登记了却没消费方 = 配了看不见的空承诺。"""
    from backend.modules.platform.api.as_bots import _TOOL_GROUP_META, _TOOL_LABELS
    assert "skills" not in _TOOL_GROUP_META
    assert "load_skill_guide" not in _TOOL_LABELS


def test_build_tool_servers_registers_the_guide_tool():
    from backend.modules.mind.execution.sdk_tools import build_tool_servers
    got = build_tool_servers("qoder", [], selection={"semantic": ["run_semantic_query"],
                                                     "skills": ["load_skill_guide"]})
    assert "datahub_skills" in got["servers"]
    assert GUIDE in got["allowed_tools"]


# ── 2. 授权：按事实自动接入，且 ceiling 管得住 ────────────────────────────────
def _bot(mcp=None, skills=("data-analysis", "arms-observability")):
    return {"as_bot_key": "analyst", "name": "analyst", "display_name": "分析师",
            "tools": {"groups": ["semantic"], "mcp": mcp if mcp is not None
                      else {"semantic": ["run_semantic_query"]}},
            "skills": list(skills), "mcp_server_ids": []}


def test_guide_shell_is_auto_granted_when_skills_are_bound():
    policy = tool_policy.compile_policy(_bot(), ceiling=None, role_perms={})
    assert GUIDE in policy.allowed
    assert policy.selection.get("skills") == ["load_skill_guide"], "未进 selection 就不会注册 server"


def test_no_guide_shell_without_bound_skills():
    policy = tool_policy.compile_policy(_bot(skills=()), ceiling=None, role_perms={})
    assert GUIDE not in policy.allowed
    assert "skills" not in policy.selection


def test_single_skill_role_does_not_get_a_useless_shell():
    """只绑 1 个技能时规则 2 永远展开正文，壳工具只会回“已在上下文”：不注册调了也没用的入口。"""
    policy = tool_policy.compile_policy(_bot(skills=("data-analysis",)), ceiling=None, role_perms={})
    assert GUIDE not in policy.allowed


def test_explicit_panel_config_is_respected_in_both_directions():
    off = tool_policy.compile_policy(_bot(mcp={"semantic": ["run_semantic_query"], "skills": []}),
                                     ceiling=None, role_perms={})
    assert GUIDE not in off.allowed, "管理员显式清空 skills 组时必须尊重配置（回到自动展开）"
    on = tool_policy.compile_policy(_bot(mcp={"semantic": ["run_semantic_query"],
                                              "skills": ["load_skill_guide"]}),
                                   ceiling=None, role_perms={})
    assert GUIDE in on.allowed


def test_execution_layer_ceiling_still_caps_the_guide_shell():
    """自动接入必须走 ceiling 交集：否则等于给新工具开了绕过执行层上限的后门。"""
    policy = tool_policy.compile_policy(_bot(), ceiling=["run_semantic_query"], role_perms={})
    assert GUIDE not in policy.allowed


# ── 3. 装载策略：正文不常驻，但三条强制展开不变 ────────────────────────────────
def test_progressive_flag_follows_tool_availability():
    assert pc._progressive_disclosure(_caps([SEM, GUIDE])) is True
    assert pc._progressive_disclosure(_caps([SEM])) is False
    assert pc._progressive_disclosure(None) is False


def test_progressive_mode_does_not_expand_trigger_matched_bodies():
    skills = [_skill("data-analysis", triggers=["转化"], body="剧本AAA"),
              _skill("arms-observability", triggers=["渠道"], body="剧本BBB")]
    loaded, reasons, _missing = pc._select_skill_bodies(
        skills, _caps([SEM, GUIDE]), "各渠道转化怎么样", progressive=True)
    assert loaded == []
    assert "load_skill_guide(\"data-analysis\")" in reasons["data-analysis"]


def test_pinned_single_skill_and_force_all_still_expand():
    skills = [_skill("data-analysis", triggers=["转化"], body="剧本AAA"),
              _skill("arms-observability", triggers=["渠道"], body="剧本BBB")]
    pinned, _r, _m = pc._select_skill_bodies(skills, _caps([SEM, GUIDE]), "随便问点什么",
                                             pinned=["data-analysis"], progressive=True)
    assert [_skill_key(x) for x in pinned] == ["data-analysis"], "用户点名的技能必须常驻"
    single, _r, _m = pc._select_skill_bodies([skills[0]], _caps([SEM, GUIDE]), "随便问点什么",
                                             progressive=True)
    assert len(single) == 1, "单职责角色不拿产出口径换字数（规则 2）"
    unattended, _r, _m = pc._select_skill_bodies(skills, _caps([SEM, GUIDE]), "各渠道转化怎么样",
                                                 force_all=True, progressive=True)
    assert len(unattended) == 2, "无人值守没有人在现场纠偏，正文照常常驻"


def test_quota_loser_reason_is_distinguishable_from_zero_hit():
    """历史误诊点：被名额挤掉与零命中写成同一句，排查时会误去改触发词。"""
    skills = [_skill("data-analysis", triggers=["转化", "趋势"], body="剧本AAA"),
              _skill("arms-observability", triggers=["渠道"], body="剧本BBB"),
              _skill("report-delivery", triggers=["报告"], body="剧本CCC"),
              _skill("screen-builder", triggers=["大屏"], body="剧本DDD")]
    loaded, reasons, _m = pc._select_skill_bodies(
        skills, _caps([SEM]), "转化趋势渠道报告", progressive=False)
    assert [_skill_key(x) for x in loaded] == ["data-analysis", "arms-observability"]
    assert "名额已满" in reasons["report-delivery"], "命中了却被挤掉 ≠ 未命中"
    assert "data-analysis" in reasons["report-delivery"], "要写明输给了谁"
    assert reasons["screen-builder"] == "本轮问题未命中该技能的触发词"


def test_progressive_off_keeps_the_old_behaviour_byte_for_byte():
    """没接入壳（老角色/被 ceiling 筛掉）时行为零变化：只回落旧开销，不丢细则。"""
    skills = [_skill("data-analysis", triggers=["转化"], body="剧本AAA"),
              _skill("arms-observability", triggers=["渠道"], body="剧本BBB")]
    loaded, _reasons, _m = pc._select_skill_bodies(skills, _caps([SEM]), "各渠道转化怎么样")
    assert [_skill_key(x) for x in loaded] == ["data-analysis", "arms-observability"]


# ── 4. 提示词：省了字数，且不承诺调不到的入口 ──────────────────────────────────
def _compose(caps, skills, question):
    state: dict = {}
    text = pc.compose_system_prompt(None, [], username="u", user_role="analyst",
                                    skills=skills, capabilities=caps, question=question,
                                    skill_state=state)
    return text, state


def test_progressive_prompt_drops_bodies_and_tells_the_model_how_to_fetch():
    skills = [_skill("data-analysis", display="数据分析", triggers=["转化"], body="剧本AAA" * 1200),
              _skill("arms-observability", display="可观测分析", triggers=["渠道"], body="剧本BBB" * 1200)]
    old, old_state = _compose(_caps([SEM]), skills, "各渠道转化怎么样")
    new, new_state = _compose(_caps([SEM, GUIDE]), skills, "各渠道转化怎么样")
    assert "剧本AAA" in old and "技能详细指引" in old
    assert "剧本AAA" not in new and "技能详细指引" not in new
    assert "load_skill_guide(\"data-analysis\")" in new, "清单必须给出按需取回的入口，否则细则无从获得"
    assert len(old) - len(new) > 5000, "改了却没省下正文 = 渐进披露没生效"
    assert old_state == {"loaded": ["data-analysis", "arms-observability"], "progressive": False}
    assert new_state == {"loaded": [], "progressive": True}


def test_prompt_never_names_the_guide_tool_when_it_is_not_granted():
    """与既有门禁同口径：提示词里点名未授权工具 = 向模型广告一个调不到的入口。"""
    skills = [_skill("data-analysis", triggers=["转化"], body="剧本AAA")]
    text, _state = _compose(_caps([SEM]), skills, "转化怎么样")
    assert "load_skill_guide" not in text


def test_production_shape_with_auto_granted_shell_clears_the_guardrail():
    """生产同形（绑定技能 → 壳工具自动接入）必须落在预算护栏内。

    这条只新增断言，**不改** tests/test_prompt_compose_budget.py 里那两条既有红载荷：
    它们量的是“没接入壳”的回退路径（被 ceiling 筛掉/管理员关掉），那条路径仍然超阈、
    仍然应该响。拿它变绿来交差就是把护栏自己掩了。
    """
    skills = [_skill("data-analysis", display="数据分析", triggers=["访问", "页面"],
                     body="剧本AAA" * 2200, requires=["run_semantic_query"]),
              _skill("arms-observability", display="可观测分析", triggers=["访问量", "页面"],
                     body="剧本BBB" * 2200, requires=["run_semantic_query"]),
              _skill("report-delivery", display="报告交付", triggers=["报告"],
                     body="剧本CCC" * 1800)]
    names = [SEM, "mcp__datahub_semantic__get_metrics", "mcp__datahub_semantic__knowledge_search",
             "mcp__datahub_skills__load_skill_guide"]
    text, state = _compose(_caps(names), skills, "这个页面的访问量和上周比怎么样")
    assert state["loaded"] == [] and state["progressive"] is True
    assert len(text) < 26_000, f"接入壳后生产同形仍超护栏（{len(text)}）= 渐进披露没真生效"


# ── 5. handler：只回已绑定技能，四种档位都可诊断 ───────────────────────────────
@pytest.fixture
def guide_ctx():
    runtime = type("R", (), {"policy": type("P", (), {
        "as_bot": {"skills": ["data-analysis", "arms-observability"]},
        "allowed": {SEM}})()})()
    token = set_execution_context(ExecutionContext(
        user_id=7, workspace_id=3, user_role="analyst",
        extra={"conversation_id": 42, "secure_runtime": runtime}))
    yield ExecutionContextVar.get()
    ExecutionContextVar.reset(token)


def _call(args):
    return json.loads(asyncio.run(skt.load_skill_guide(args))["content"][0]["text"])


def test_handler_returns_body_for_bound_skill(guide_ctx, monkeypatch):
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "数据分析",
                                        "description": "契约", "system_prompt": "细则正文",
                                        "requires_any": ["run_semantic_query"]}])
    out = _call({"name": "data-analysis"})
    assert out["guide"] == "细则正文" and out["already_in_prompt"] is False
    assert "不是查询结果" in out["note"], "必须声明这是方法文字，不是数据"
    assert "requires_missing" not in out, "run_semantic_query 已授权，不该误报缺工具"


def test_handler_refuses_unbound_skill_and_lists_what_is_loadable(guide_ctx):
    out = _call({"name": "screen-builder"})
    assert "未绑定" in out["error"] and "data-analysis" in out["error"], "给可加载清单，不猜名字"


def test_handler_requires_a_name(guide_ctx):
    assert "可加载" in _call({"name": ""})["error"]


def test_handler_says_so_when_nothing_is_bound(guide_ctx):
    guide_ctx.extra["secure_runtime"].policy.as_bot["skills"] = []
    assert "没有绑定任何技能" in _call({"name": "data-analysis"})["error"]


def test_handler_skips_body_already_in_prompt(guide_ctx):
    guide_ctx.extra[skt.RESIDENT_KEY] = {"loaded": ["data-analysis"], "progressive": True}
    out = _call({"name": "data-analysis"})
    assert out["already_in_prompt"] is True and "guide" not in out, "重复下发只是占位"


def test_handler_flags_missing_required_tools_instead_of_inducing_them(guide_ctx, monkeypatch):
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "SQL 分析师",
                                        "description": "", "system_prompt": "细则正文",
                                        "requires_any": ["execute_sql"]}])
    out = _call({"name": "data-analysis"})
    assert out["requires_missing"] == ["execute_sql"], "细则点名的工具本会话调不到必须写明"


def test_handler_marks_truncation_explicitly(guide_ctx, monkeypatch):
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "长剧本",
                                        "description": "", "system_prompt": "字" * 30000,
                                        "requires_any": []}])
    monkeypatch.setattr(skt, "SKILL_GUIDE_MAX_CHARS", 1000)
    out = _call({"name": "data-analysis"})
    assert len(out["guide"]) == 1000 and "截断" in out["truncated"]
    assert out["guide_chars"] == 30000, "对外要写明真实全文尺寸，不能把截断当全文"


def test_each_load_is_recorded_for_review(guide_ctx, monkeypatch):
    """复盘"模型到底有没有读细则"的唯一依据：没有它，A2 是不可观测的赌博。"""
    captured = []
    monkeypatch.setattr("backend.observability.context.record_span",
                        lambda **kw: captured.append(kw))
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "", "description": "",
                                        "system_prompt": "细则正文", "requires_any": []}])
    _call({"name": "data-analysis"})
    _call({"name": "screen-builder"})
    kinds = [(c["kind"], c["status"]) for c in captured]
    assert ("skill_guide", "success") in kinds and ("skill_guide", "rejected") in kinds


def _skill_key(skill):
    return pc._skill_key(skill)