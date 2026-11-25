"""技能作用域（frontmatter `attach_scope`）回归。

锁定口径：
- 两个域**共用同一工具**时（`arms-observability` 与 `data-analysis` 都靠 `run_semantic_query`），
  正文只在问题命中该技能作用域时才随工具结果附带：业务取数问题（「案例有多少」）不得拿到观测剧本，
  反过来站点观测问题仍要拿到（不拿作用域换字数）；
- 作用域是“问错域”的隔离，**不是失败降级**：本轮问题为空时不判（宁附带也不静默丢口径），
  模型显式 `load_skill_guide` 仍可取回，但结果里必须写明“本轮问题不在该作用域”；
- 落盘形态锁死：ARMS 技能必须声明 `attach_scope`，触发词里不得再放「页面/流量/访问/设备/渠道」
  这类与业务问题撞车的裸词（它们会把业务问题引到观测域），正文必须有“业务对象取数不属于本技能”的边界段。

离线运行：技能正文走文件加载器，不连库；观测 span 未开启即 no-op。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.mind.execution import as_bots
from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools import skill_tools as skt
from backend.modules.mind.execution.sdk_tools.context import (
    ExecutionContextVar, set_execution_context)

SEM = "mcp__datahub_semantic__run_semantic_query"

#: 两个同为语义层的剧本：一个限定站点观测作用域，一个不设（业务取数）。
_SKILLS = {
    "arms-observability": {
        "name": "arms-observability", "display_name": "ARMS 可观测数据分析",
        "description": "站点观测数据", "system_prompt": "观测细则",
        "requires_any": ["run_semantic_query"],
        "attach_scope": ["arms", "rum", "官网", "首屏", "页面性能"],
    },
    "data-analysis": {
        "name": "data-analysis", "display_name": "数据分析（语义层）",
        "description": "业务对象取数", "system_prompt": "业务分析细则",
        "requires_any": ["run_semantic_query"],
    },
}


def _fake_load(names):
    return [dict(_SKILLS[names[0]])]


def _runtime(names, allowed=(SEM,)):
    return type("R", (), {"policy": type("P", (), {
        "as_bot": {"skills": list(names)}, "allowed": set(allowed)})()})()


def _ctx(question, names=("arms-observability", "data-analysis")):
    return ExecutionContext(user_id=7, workspace_id=3, user_role="analyst",
                            extra={"conversation_id": 42, "secure_runtime": _runtime(names),
                                   "question": question})


# ── 1. 工具边界附带：按作用域隔离 ────────────────────────────────────────────
def test_business_question_does_not_get_the_observability_playbook(monkeypatch):
    """「案例有多少」是业务对象取数：不得附带观测剧本（历史缺陷：业务问题被 RUM 口径带偏）。"""
    monkeypatch.setattr(as_bots, "load_skills", _fake_load)
    ctx = _ctx("案例有多少")
    block = skt.guide_block_for_tool(ctx, "run_semantic_query")
    assert "业务分析细则" in block, "业务（不设作用域）的剧本照常附带"
    assert "观测细则" not in block, "作用域外的观测剧本不得随工具结果下发"
    assert ctx.extra[skt.ATTACHED_KEY] == ["data-analysis"], "附带事实只记真正送达的那份"


def test_observability_question_still_gets_the_observability_playbook(monkeypatch):
    """站点观测问题（官网/首屏）命中作用域：该附带的一份不能少。"""
    monkeypatch.setattr(as_bots, "load_skills", _fake_load)
    ctx = _ctx("官网首屏加载慢不慢")
    block = skt.guide_block_for_tool(ctx, "run_semantic_query")
    assert "观测细则" in block
    assert ctx.extra[skt.ATTACHED_KEY] == ["arms-observability"]


def test_unknown_question_attaches_instead_of_dropping_the_playbook(monkeypatch):
    """问题为空时无从判域：宁附带也不静默丢口径（作用域不是降级开关）。"""
    monkeypatch.setattr(as_bots, "load_skills", _fake_load)
    ctx = _ctx("")
    block = skt.guide_block_for_tool(ctx, "run_semantic_query")
    assert "观测细则" in block


# ── 2. 显式取回：给正文，但必须点明作用域不符 ────────────────────────────────
def _fetch(name, question):
    token = set_execution_context(_ctx(question))
    try:
        raw = asyncio.run(skt.load_skill_guide({"name": name}))
    finally:
        ExecutionContextVar.reset(token)
    return json.loads(raw["content"][0]["text"])


@pytest.fixture(autouse=True)
def _patch_loader(monkeypatch):
    monkeypatch.setattr(as_bots, "load_skills", _fake_load)


def test_explicit_fetch_out_of_scope_returns_body_but_warns():
    out = _fetch("arms-observability", "案例有多少")
    assert out["guide"] == "观测细则", "显式取回不拒绝（可能确实要读全文）"
    note = out.get("scope_note") or ""
    assert "未命中该技能的作用域" in note and "业务数据分析" in note, \
        "必须写明这轮问题不在该域，别让观测口径被当成业务问题的作业口径"


def test_explicit_fetch_in_scope_has_no_scope_warning():
    out = _fetch("arms-observability", "官网页面性能这周怎么样")
    assert "scope_note" not in out


# ── 3. 落盘形态：ARMS 技能的作用域与边界段 ───────────────────────────────────
def _shipped():
    from backend.modules.mind.config.skill_loader import load_skill
    skill = load_skill("arms-observability")
    assert skill, "内置技能 arms-observability 必须存在"
    return skill


def test_shipped_arms_skill_declares_scope_and_keeps_business_words_out():
    cfg = _shipped()["skill_config"]
    scope = [str(x).strip() for x in (cfg.get("attach_scope") or [])]
    assert scope, "与业务数据分析共用 run_semantic_query 的技能必须声明作用域"
    assert not ({"案例", "订单", "客户"} & set(scope)), "业务对象词不得进观测作用域"
    triggers = [str(x).strip() for x in (cfg.get("triggers") or [])]
    for word in ("页面", "流量", "访问", "访问量", "设备", "渠道", "监控"):
        assert word not in triggers, f"裸词「{word}」会把业务问题引到观测域（分诊表也会误导）"


def test_shipped_scope_words_hit_domain_questions_and_miss_business_ones():
    skill = _shipped()
    as_dict = {"attach_scope": skill["skill_config"].get("attach_scope") or []}
    assert skt._scope_hits(as_dict, "案例有多少") == [], "业务问题不进观测域"
    assert skt._scope_hits(as_dict, "官网首页的首屏时间是多少"), "站点观测问题必须命中"
    assert skt._scope_hits(as_dict, "这条链接 https://x.com/a 的访问量"), "贴 URL 的问题也属观测域"


def test_shipped_body_states_the_boundary():
    body = _shipped()["system_prompt"]
    assert "业务对象取数不属于本技能" in body, "正文必须自己划边界（常驻/取回/附带三条通道都靠它）"
    assert "案例有多少" in body, "用用户实际问过的问题当反例，口径才落地"


# ── 4. 平台请求日志技能：同样与业务/RUM 共用工具，同样要划作用域 ─────────────────
def _shipped_log_skill():
    from backend.modules.mind.config.skill_loader import load_skill
    skill = load_skill("platform-request-log")
    assert skill, "内置技能 platform-request-log 必须存在"
    return skill


def test_shipped_log_skill_declares_scope_without_bare_words():
    """截获「页面/访问」这类裸词会把 RUM 与业务问题全拉进本域（本域只管平台请求日志）。"""
    cfg = _shipped_log_skill()["skill_config"]
    scope = [str(x).strip() for x in (cfg.get("attach_scope") or [])]
    assert scope, "与业务/RUM 共用 run_semantic_query 的技能必须声明作用域"
    assert not ({"案例", "订单", "客户"} & set(scope)), "业务对象词不得进日志作用域"
    for words in (scope, [str(x).strip() for x in (cfg.get("triggers") or [])]):
        for word in ("页面", "流量", "访问", "访问量", "设备", "渠道", "监控"):
            assert word not in words, f"裸词「{word}」会让 RUM/业务问题误命中本域"


def test_shipped_log_scope_hits_only_platform_request_questions():
    skill = _shipped_log_skill()
    as_dict = {"attach_scope": skill["skill_config"].get("attach_scope") or []}
    assert skt._scope_hits(as_dict, "这周接口成功率怎么样"), "平台接口问题必须命中"
    assert skt._scope_hits(as_dict, "哪些平台接口的 P95 最慢"), "慢接口问题必须命中"
    assert skt._scope_hits(as_dict, "案例有多少") == [], "业务对象取数不进本域"
    assert skt._scope_hits(as_dict, "官网首屏加载慢不慢") == [], "页面性能属 RUM，不得命中本域"


def test_shipped_log_body_states_the_boundary():
    body = _shipped_log_skill()["system_prompt"]
    assert "不是业务对象取数" in body and "案例" in body, "正文必须自己划业务边界"
    assert "不是 RUM" in body, "页面性能/RUM 必须显式让路，否则两边口径互相冒充"
    assert "不得相加" in body, "两站两个本体，数据不得静默相加"

