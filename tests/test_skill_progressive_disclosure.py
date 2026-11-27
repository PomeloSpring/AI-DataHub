"""技能作业细则的加载通道回归（工具边界披露）。

锁定口径：
- **正文不常驻提示词**：多技能角色的常规回合，系统提示词只有 metadata（name+description）+
  一句话契约，**一份正文都不注入**（7 份 = 25,531 字符压在每回合是白付费，还打爆提示词缓存）；
- **口径在"用到"时到达**：本回合首次调用某技能 `requires_any` 声明的工具，细则随该次工具结果
  附带（`guide_block_for_tool` + `compat.guarded` 接线），一个回合同一份只发一次；
  这条通道与有没有 `load_skill_guide` 无关 —— 实测（会话 335/336/337 与 340 的纠正轮）
  只给一句话契约、指望模型自己取回，它一次都没调，所以确定性通道不能是"模型自觉"；
- **主动取回仍在**（`load_skill_guide`）：动手前就想读口径时的补充通道；
- **授权按事实自动接入**：助手绑了技能就接入壳工具（与平台功能惰性壳同一口径），
  管理员显式配置过 `mcp.skills` 时尊重配置，执行层 ceiling 同样管得住它；
- **三条强制展开不变**：pinned / 单职责角色 / 无人值守 force_all 仍常驻正文；
- **原因可诊断**：零命中与"命中了但按设计不常驻"必须写成两句话（历史误诊点）；
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


def _attach_ctx(skill_names, allowed_suffixes=()):
    """构造一个带 secure_runtime 的回合上下文（工具边界附带与 handler 用例共用）。"""
    from backend.modules.mind.execution.models import ExecutionContext as EC
    from backend.modules.mind.execution.sdk_tools.context import set_execution_context
    allowed = {f"mcp__datahub_x__{s}" for s in allowed_suffixes}
    runtime = type("R", (), {"policy": type("P", (), {
        "as_bot": {"skills": list(skill_names)}, "allowed": allowed})()})()
    return EC(user_id=7, workspace_id=3, user_role="analyst",
              extra={"conversation_id": 42, "secure_runtime": runtime})


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


def test_multi_skill_turn_residents_no_body_at_all():
    """常规回合（多技能、用户未点名）：一份正文都不常驻，命中与否只影响原因措辞。

    刻意把得分高的技能放在清单末尾：无论得分高低，常驻份数都是 0 —— 口径改由工具边界送达。
    """
    skills = [_skill("arms-observability", triggers=["渠道"], body="剧本BBB"),
              _skill("data-analysis", triggers=["转化", "趋势"], body="剧本AAA")]
    loaded, reasons, _missing = pc._select_skill_bodies(
        skills, _caps([SEM, GUIDE]), "转化趋势怎么样", progressive=True)
    assert loaded == [], "正文常驻提示词 = 每回合为可能不发生的任务付费"
    # 主通道是"动手前先取回"，措辞必须是命令式：旧文案写"也可以先调"，实测三轮重问
    # + 纠正轮模型一次都没调用（会话 1783069459335/336/337/340），等于没有口径。
    assert "必须先调" in reasons["data-analysis"], "动手前取回细则要写成命令式，不是可选项"
    assert 'load_skill_guide("data-analysis")' in reasons["data-analysis"], "必须点名该取哪一份"
    assert "也可以" not in reasons["data-analysis"], "不得留可选式措辞（会被模型读成不必做）"
    assert "随结果补发" in reasons["data-analysis"], "事后核对那份的来处也要说清，不能只字不提"
    assert "命中 2 个触发词" in reasons["data-analysis"], "命中信息要留着当相关性提示"
    assert "未命中" in reasons["arms-observability"], "零命中与命中了必须写成两句话（历史误诊点）"
    assert 'load_skill_guide("data-analysis")' in reasons["data-analysis"], "主动取回入口要写明"


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


def test_boundary_delivery_does_not_depend_on_the_guide_shell():
    """没接壳（老角色/被 ceiling 筛掉）同样不常驻：附带通道在 compat 里，与壳无关；
    但提示词**不得**点名一个模型调不到的工具。"""
    skills = [_skill("data-analysis", triggers=["转化"], body="剧本AAA"),
              _skill("arms-observability", triggers=["渠道"], body="剧本BBB")]
    loaded, reasons, _m = pc._select_skill_bodies(skills, _caps([SEM]), "各渠道转化怎么样")
    assert loaded == [], "附带通道不靠 load_skill_guide，缺壳也不能回到整份常驻"
    assert "首次调用该域工具时随结果自动附带" in reasons["data-analysis"]
    assert "load_skill_guide" not in reasons["data-analysis"], "不承诺调不到的入口"


def test_correction_turn_without_trigger_hit_still_gets_the_body(monkeypatch):
    """事故口径（会话 1783069459340 seq4）：纠正短句零命中，旧策略整轮丢口径。

    新策略下命中与否都不常驻，因此"零命中"不再等于"没细则"——模型一动该域工具就拿到。
    """
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "看板设计",
                                        "description": "契约", "system_prompt": "版面细则",
                                        "requires_any": ["create_data_screen", "update_data_screen"]}])
    skills = [_skill("screen-builder", triggers=["大屏"], body="版面细则"),
              _skill("data-analysis", triggers=["转化"], body="分析细则")]
    loaded, _reasons, _m = pc._select_skill_bodies(
        skills, _caps(["mcp__datahub_screen__update_data_screen"]),
        "我要的是图表和图表间的间隔，不是改图表的大小")
    assert loaded == []
    ctx = _attach_ctx(["screen-builder"], {"update_data_screen"})
    block = skt.guide_block_for_tool(ctx, "update_data_screen")
    assert "版面细则" in block, "纠正轮一动该域工具就该拿到口径（与触发词无关）"
    assert skt.guide_block_for_tool(ctx, "update_data_screen") == "", "同一回合不得重发"
    assert skt.guide_block_for_tool(ctx, "run_semantic_query") == "", "别的域不受牵连"
    assert ctx.extra[skt.ATTACHED_KEY] == ["screen-builder"], "附带事实要落进回合 meta（侧栏数据源）"


def test_attach_lands_in_the_tool_result_and_survives_failure(monkeypatch):
    """接线：结果尾部多一块细则；读取异常不得改写业务结果，但必须**显式**说明细则没送达。"""
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "看板设计",
                                        "description": "", "system_prompt": "版面细则",
                                        "requires_any": ["update_data_screen"]}])
    ctx = _attach_ctx(["screen-builder"], {"update_data_screen"})
    result = skt.attach_to_result(ctx, "update_data_screen",
                                  {"content": [{"type": "text", "text": '{"ok":true}'}]})
    assert len(result["content"]) == 2 and "版面细则" in result["content"][1]["text"]
    twice = skt.attach_to_result(ctx, "update_data_screen",
                                 {"content": [{"type": "text", "text": "第二次"}]})
    assert twice["content"] == [{"type": "text", "text": "第二次"}], "同一回合同一份细则只附一次"

    def boom(names):
        raise RuntimeError("读不到技能文件")
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills", boom)
    ctx2 = _attach_ctx(["screen-builder"], {"update_data_screen"})
    out = skt.attach_to_result(ctx2, "update_data_screen",
                               {"content": [{"type": "text", "text": '{"ok":true}'}]})
    assert out["content"][0]["text"] == '{"ok":true}', "附件失败不得损坏业务结果"
    assert "未能随本次结果下发" in out["content"][1]["text"], "失败要看得见，不得静默当没事"


def test_attach_respects_total_tool_result_budget(monkeypatch):
    """业务结果 + 自动附带细则共用 16K；细则超限必须显式截断。"""
    monkeypatch.setattr("backend.modules.mind.execution.as_bots.load_skills",
                        lambda names: [{"name": names[0], "display_name": "分析",
                                        "description": "", "system_prompt": "细则正文" * 5000,
                                        "requires_any": ["get_metrics"]}])
    ctx = _attach_ctx(["data-analysis"], {"get_metrics"})
    out = skt.attach_to_result(ctx, "get_metrics",
                               {"content": [{"type": "text", "text": "x" * 900}]},
                               max_chars=1000)
    total = sum(len(block["text"]) for block in out["content"])
    assert total <= 1000
    assert out["guide_truncated"] is True
    assert "预算已截断" in out["content"][1]["text"]


# ── 4. 提示词：省了字数，且不承诺调不到的入口 ──────────────────────────────────
def _compose(caps, skills, question):
    state: dict = {}
    text = pc.compose_system_prompt(None, [], username="u", user_role="analyst",
                                    skills=skills, capabilities=caps, question=question,
                                    skill_state=state)
    return text, state


def test_prompt_carries_no_body_and_points_at_the_boundary():
    skills = [_skill("data-analysis", display="数据分析", triggers=["转化"], body="剧本AAA" * 1200),
              _skill("arms-observability", display="可观测分析", triggers=["渠道"], body="剧本BBB" * 1200)]
    text, state = _compose(_caps([SEM, GUIDE]), skills, "各渠道转化怎么样")
    assert "剧本AAA" not in text and "剧本BBB" not in text, "两份正文一份都不许进提示词"
    assert "技能详细指引" not in text, "没有常驻正文就没有该小节"
    assert "必须先调" in text, "模型要能在动手前知道要先取回细则（不是等附带）"
    assert "技能作业细则（收尾复述，动手前生效）" in text, \
        "命令式要求必须落在尾部：中部会被后面的通用规则与工具权限段压过（本平台实测失效模式）"
    assert "不代替动手前取口径" in text, "附带那份的定位要写清，否则模型会以为不必取"
    assert "数据分析" in text and "可观测分析" in text, "名称+契约仍常驻可见"
    assert state["loaded"] == [] and state["progressive"] is True


def test_prompt_never_names_the_guide_tool_when_it_is_not_granted():
    """与既有门禁同口径：提示词里点名未授权工具 = 向模型广告一个调不到的入口。"""
    skills = [_skill("data-analysis", triggers=["转化"], body="剧本AAA")]
    text, _state = _compose(_caps([SEM]), skills, "转化怎么样")
    assert "load_skill_guide" not in text


def test_production_shape_residents_zero_bodies():
    """生产同形（绑定技能 → 壳工具自动接入）：常驻正文从"命中即展（上限 2 份）"降到 0 份。

    省下的字数必须**可核查**：与"命中即整份常驻"的旧形态相比，两个 6,600 字符级正文都不在提示词里。
    """
    skills = [_skill("data-analysis", display="数据分析", triggers=["访问", "页面"],
                     body="剧本AAA" * 2200, requires=["run_semantic_query"]),
              _skill("arms-observability", display="可观测分析", triggers=["访问量", "页面"],
                     body="剧本BBB" * 2200, requires=["run_semantic_query"]),
              _skill("report-delivery", display="报告交付", triggers=["报告"],
                     body="剧本CCC" * 1800)]
    names = [SEM, "mcp__datahub_semantic__get_metrics", "mcp__datahub_semantic__knowledge_search",
             "mcp__datahub_skills__load_skill_guide"]
    question = "这个页面的访问量和上周比怎么样"
    text, state = _compose(_caps(names), skills, question)
    assert state["loaded"] == [] and state["progressive"] is True
    assert "剧本AAA" not in text and "剧本BBB" not in text
    assert len(text) < len("剧本AAA" * 2200) + len("剧本BBB" * 2200), \
        "两份正文的量还留在提示词里 = 没真省下来"


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


# ── 出厂正文（screen-builder）：尺寸线 + 版面语义送达 ─────────────────
def _shipped_screen_skill():
    from backend.modules.mind.config import skill_loader
    skill_loader.clear_skill_cache()
    return skill_loader.load_skill("screen-builder")


def test_shipped_screen_body_fits_the_guide_truncation_line():
    """正文超过 `SKILL_GUIDE_MAX_CHARS` 会被附带/取回通道**截掉尾部** —— 截掉的正是版面细则与禁止项，
    等于口径没送达却看不出来。出厂尺寸必须由测试守住，而不是靠人肉数。"""
    body = _shipped_screen_skill()["system_prompt"]
    assert len(body) <= skt.SKILL_GUIDE_MAX_CHARS, f"正文 {len(body)} 字符超截断线"


def test_shipped_screen_skill_renamed_but_keeps_technical_name():
    """显示名改「看板设计」，技术名仍是 `screen-builder`：`adh_as_bots.skills` 存的是技术名，
    改名会把已绑定关系写成悬空名（迁移风险与本次目标无关）。"""
    skill = _shipped_screen_skill()
    assert skill["display_name"] == "看板设计"
    assert skill["name"] == "screen-builder"
    assert skill["skill_config"]["requires_any"] == ["create_data_screen", "update_data_screen"]


def test_shipped_screen_body_teaches_the_three_layout_dimensions():
    """正文必须能回答「我要的是间隔不是大小」：间距/大小/位置三维度各有字段，且等距有可核验判据。
    只有触发词命中才送达的细则，写少了维度就等于没有（历史 seq4 复现的根因）。

    字段名是**契约**（不是措辞）：正文写一个工具没有的参数，模型照抄就被拒，白烧一轮。
    """
    from backend.modules.mind.execution.sdk_tools.screen_tools import TOOL_SPECS

    specs = {s["name"]: set(((s.get("schema") or {}).get("properties") or {}).keys()) for s in TOOL_SPECS}
    body = _shipped_screen_skill()["system_prompt"]
    for token in ("gap", "layout_rows", "even_spacing", "base_from", "pageLayout"):
        assert token in body, f"正文缺版面语义通道 {token}"
    assert "间距 ⟂ 大小" in body, "未写明间距与大小互不代偿"
    assert {"gap", "layout_rows"} <= specs["update_data_screen"], specs["update_data_screen"]
    assert {"size", "after"} <= specs["update_data_screen_chart"]
    assert {"gap", "base_from"} <= specs["create_data_screen"]
    widget_props = next(s for s in TOOL_SPECS if s["name"] == "create_data_screen")
    widget_props = set(widget_props["schema"]["properties"]["widgets"]["items"]["properties"])
    assert {"layout", "position"} <= widget_props, widget_props
    triggers = _shipped_screen_skill()["skill_config"]["triggers"]
    assert {"间距", "间隔", "图表大小"} <= set(triggers), "纠正轮措辞要能命中清单"