"""意图识别与技能分诊（提示词形态）的回归。

口径（2026-11-20 用户拍板）：**意图识别写在系统提示词里，由模型判，不由服务端词表打分**。
服务端只确定两件事实：这轮绑了哪些技能、每份技能自述负责哪类意图（SKILL.md `intent:`）。
因此本文件锁三件事：

1. 分诊表必须把「意图 → 技能名」逐条渲染出来（模型据此认意图、再取细则）；
2. 分诊表**不复制**技能描述与作业细则（同一文本每回合只付一遍），也不复制正文；
3. 取回通道的措辞必须与工具事实一致：有 `load_skill_guide` 才写命令式"先调"，
   没有就只写"随该域工具附带"——不存在时不广告调不到的入口（空承诺）。

另附一条存量缺口守卫：内置技能的 `requires_any` 必须都是**真实在册**的工具名。
历史缺陷：content-writer 声明了三个不存在的动作名，能力门控把它永久剔除，
`/` 点名也救不回；report-delivery 则完全没声明，导致"随结果附带"变成假承诺。
"""
from __future__ import annotations

import pytest

from backend.modules.mind.execution import prompt_composer as pc


def _skill(name, *, intent="", display=None, body=None, requires=None):
    return {
        "name": name,
        "display_name": display or name,
        "description": f"{display or name} 的定位说明",
        "intent": intent,
        "system_prompt": body if body is not None else f"{name} 的作业细则正文",
        "requires_any": requires or [],
        "triggers": [],
    }


def _caps(*names):
    return {"tools": [{"name": n, "description": ""} for n in names],
            "unavailable_tools": []}


SEM = "mcp__datahub_semantic__run_semantic_query"
GUIDE = "mcp__datahub_skills__load_skill_guide"
SCREEN = "mcp__datahub_screen__create_data_screen"


def _multi_skill():
    return [
        _skill("data-analysis", intent="查数分析（语义层）", display="数据分析（语义层）",
               requires=["run_semantic_query"]),
        _skill("screen-builder", intent="开发/修改数据大屏", display="看板设计",
               requires=["create_data_screen"]),
        _skill("legacy-skill", display="未声明意图的旧技能"),
    ]


# ── 1. 表格形态：意图 → 技能，不复制描述与正文 ────────────────────────
def test_routing_table_maps_intent_to_skill_name():
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=_multi_skill(), capabilities=_caps(SEM, SCREEN, GUIDE), question="看下趋势")
    assert "## 意图识别与技能分诊" in prompt
    assert "- 查数分析（语义层） → `data-analysis`" in prompt
    assert "- 开发/修改数据大屏 → `screen-builder`" in prompt
    # 认意图后必须先取回该技能的作业细则（命令式，不是"也可以"）
    assert "先调" in prompt and 'load_skill_guide("<该类的技能名>")' in prompt
    assert "只凭「已绑定技能」里那行概述动手算违规" in prompt
    # 跨类取主任务一份，且不要为分诊反问用户
    assert "只按**主任务**取一份" in prompt
    assert "不要为了分诊反问用户" in prompt


def test_routing_table_does_not_duplicate_description_or_body():
    skills = _multi_skill()
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=skills, capabilities=_caps(SEM, SCREEN, GUIDE), question="")
    section = prompt.split("## 意图识别与技能分诊", 1)[1].split("\n## ", 1)[0]
    for s in skills:
        if s.get("intent"):
            assert s["description"] not in section, "分诊表复制了「已绑定技能」里的描述（同一文本每回合付两遍）"
        assert s["system_prompt"] not in section, "分诊表把作业细则正文抄进来了"


def test_retrieval_command_is_written_once_per_prompt():
    """同一句“先调 load_skill_guide”不得按技能数重复：分诊表写一次，清单行只留病因。

    历史代价：9 份技能各带一段 200+ 字符的取回命令（≈2K 字符），每回合白付，
    还与尾部的强制复述三处撞车，模型读到的是重复噪声而非更强约束。
    """
    skills = [s for s in _multi_skill() if s.get("intent")]
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=skills, capabilities=_caps(SEM, SCREEN, GUIDE), question="")
    section = prompt.split("## 已绑定技能", 1)[1].split("\n## ", 1)[0]
    assert "load_skill_guide" not in section, "进分诊表的技能不该在清单行里再写一遍取回命令"

    # 未声明 intent 的存量技能不进分诊表，取回命令只能挂在它自己的清单行上
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=_multi_skill(), capabilities=_caps(SEM, SCREEN, GUIDE), question="")
    section = prompt.split("## 已绑定技能", 1)[1].split("\n## ", 1)[0]
    assert section.count("load_skill_guide") == 1


def test_skills_without_intent_are_not_listed():
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=_multi_skill(), capabilities=_caps(SEM, SCREEN, GUIDE), question="")
    section = prompt.split("## 意图识别与技能分诊", 1)[1].split("\n## ", 1)[0]
    assert "legacy-skill" not in section, "没声明 intent 的技能不该被编造一个意图"
    assert "legacy-skill" in prompt, "但它仍须出现在「已绑定技能」清单里（不静默消失）"


def test_unbound_intent_is_declared_not_pretended():
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=_multi_skill(), capabilities=_caps(SEM, SCREEN, GUIDE), question="")
    assert "上表没有的意图 = 本助手未绑定该能力" in prompt
    assert "AS-BOT 配置" in prompt


# ── 2. 与工具事实一致：有壳才写“先取回”，没壳不广告空承诺 ──────────────
def test_command_form_only_when_guide_tool_granted():
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=_multi_skill(), capabilities=_caps(SEM, SCREEN), question="看下趋势")
    section = prompt.split("## 意图识别与技能分诊", 1)[1].split("\n## ", 1)[0]
    assert "load_skill_guide" not in section, "未授权壳工具时不得出现调不到的入口"
    assert "首次调用其域工具时随结果附带" in section, "没壳时必须说清细则从哪来"


def test_single_skill_role_has_no_routing_table():
    """单技能角色正文按规则 2 常驻，再挂一张分诊表只是噪声。"""
    prompt = pc.compose_system_prompt(
        None, [], username="u", user_role="analyst",
        skills=[_skill("data-analysis", intent="查数分析（语义层）",
                       requires=["run_semantic_query"])],
        capabilities=_caps(SEM, GUIDE), question="看下趋势")
    assert "## 意图识别与技能分诊" not in prompt
    assert "data-analysis 的作业细则正文" in prompt, "单技能角色的正文必须仍常驻（规则 2 不变）"


# ── 3. 内置技能配置守卫（存量缺口的静态护栏）───────────────────────────
def test_shipped_skills_declare_intent():
    from backend.modules.mind.execution.as_bots import load_skills

    names = ["data-analysis", "pro-analysis", "arms-observability", "screen-builder",
             "report-delivery", "ontology-builder", "metadata-manager",
             "content-writer", "knowledge-qa"]
    loaded = {s["name"]: s for s in load_skills(names)}
    assert len(loaded) == len(names)
    for name, s in loaded.items():
        assert str(s.get("intent") or "").strip(), f"{name} 缺 intent：模型无法把它归到任何意图类"
        # 意图标签要能直接给人看（中文或至少非纯 ASCII 键）
        assert any("\u4e00" <= ch <= "\u9fff" for ch in s["intent"]), \
            f"{name} 的 intent 应为可读中文标签（当前 {s['intent']!r}）"


def test_requires_any_names_exist_in_registry():
    """`requires_any` 必须是真实在册的工具后缀，否则该技能永久不可用（能力门控先剔除）。"""
    from backend.modules.mind.execution import function_tools
    from backend.modules.mind.execution.as_bots import load_skills
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS

    available: set[str] = {str(s["name"]) for s in function_tools.SHELL_TOOL_SPECS}
    for _server, tools in TOOL_SERVER_TOOLS.values():
        available.update(str(t) for t in tools)

    names = ["data-analysis", "pro-analysis", "arms-observability", "screen-builder",
             "report-delivery", "ontology-builder", "metadata-manager",
             "content-writer", "knowledge-qa"]
    for s in load_skills(names):
        needs = set(s.get("requires_any") or [])
        assert needs, f"{s['name']} 未声明 requires_any：工具边界附带永远送不到它的细则"
        unknown = needs - available
        assert not unknown, f"{s['name']} 的 requires_any 含不在册工具名：{sorted(unknown)}"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
