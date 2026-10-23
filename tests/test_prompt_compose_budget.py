"""执行层 system_prompt 体积契约：常驻指令不得重复下发、技能正文按需展开且失效可见。

背景（实测）：一个 agent 回合平均 ~6 次 LLM 调用，system_prompt 每次都重发；
改造前典型业务用户 24,735 字符、admin 45,548 字符，其中工具清单把 schema 里
已有的描述重抄一遍（9,064/11,511 字符）、多技能角色把 7 个剧本正文全量展开
（25,531 字符，且 data-analysis「不写 SQL」与 pro-analysis「用 execute_sql」并存
= 给模型的矛盾指令）。

本文件锁定两刀的契约与"不得回退"的尺寸护栏：
- 工具清单只裁决可用性，不重复工具描述；
- 技能正文按「能力门控 + 触发词命中」展开，未展开的必须带原因可见（禁止静默消失）；
- 安全强约束段与语义层权威划分不得被瘦身砍掉。
"""
from __future__ import annotations

import pytest

from backend.modules.mind.execution.prompt_composer import (
    MAX_SKILL_BODIES,
    _select_skill_bodies,
    compose_system_prompt,
)


def _caps(*tool_names, descriptions=None):
    """capabilities 载荷（与 ToolPolicy.manifest() 同形：name 为 mcp__<server>__<tool>）。"""
    descriptions = descriptions or {}
    return {"tools": [{"name": n, "description": descriptions.get(n, "")} for n in tool_names],
            "unavailable_tools": []}


def _skill(name, *, display=None, desc="只做演示用", body=None, triggers=None, requires=None):
    return {
        "name": name,
        "display_name": display or name,
        "description": desc,
        "system_prompt": body if body is not None else f"{name} 的作业细则正文",
        "requires_any": requires or [],
        "triggers": triggers or [],
    }


# ── 工具清单段：只裁决可用性 ────────────────────────────────────────

def test_tool_manifest_keeps_names_and_drops_descriptions():
    """工具名必须逐个可见（可用性唯一权威），但描述不再重抄一遍。"""
    desc = "列出当前工作空间的分析报表摘要（标题/格式/状态/浏览次数）。"
    caps = _caps("mcp__datahub_functions__list_reports",
                 descriptions={"mcp__datahub_functions__list_reports": desc})
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", capabilities=caps)
    assert "mcp__datahub_functions__list_reports" in prompt
    assert desc not in prompt
    assert "只裁决可用性" in prompt


def test_tool_manifest_groups_by_server_but_lists_every_tool():
    caps = _caps("mcp__datahub_semantic__get_metrics", "mcp__datahub_semantic__run_semantic_query",
                 "mcp__datahub_query__execute_sql")
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", capabilities=caps)
    for name in ("mcp__datahub_semantic__get_metrics", "mcp__datahub_semantic__run_semantic_query",
                 "mcp__datahub_query__execute_sql"):
        assert name in prompt
    assert "datahub_semantic" in prompt and "datahub_query" in prompt


def test_tool_manifest_empty_still_declares_no_tools():
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", capabilities=_caps())
    assert "当前未授权任何工具" in prompt


def test_unavailable_tool_reason_is_visible():
    caps = {"tools": [], "unavailable_tools": [
        {"name": "mcp__datahub_workspace__task", "reason": "子代理隔离契约尚未验证"}]}
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", capabilities=caps)
    assert "mcp__datahub_workspace__task" in prompt
    assert "子代理隔离契约尚未验证" in prompt


# ── 技能正文装载：能力门控 + 触发词命中 ────────────────────────────

def test_single_skill_role_always_expands_body():
    """只绑一个技能（analyst/viewer 形态）与改造前一致，不看问题命中。"""
    skills = [_skill("data-analysis", display="数据分析（语义层）",
                     body="分析剧本正文XYZ", triggers=["趋势"])]
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=skills, capabilities=_caps(), question="今天天气")
    assert "分析剧本正文XYZ" in prompt
    assert "本轮未展开作业细则" not in prompt


def test_multi_skill_expands_only_matched_bodies():
    skills = [
        _skill("data-analysis", display="数据分析（语义层）", body="语义层剧本AAA", triggers=["趋势", "环比"]),
        _skill("screen-builder", display="可视化大屏生成", body="大屏剧本BBB", triggers=["大屏"]),
    ]
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=skills, capabilities=_caps(), question="看下环比趋势")
    assert "语义层剧本AAA" in prompt
    assert "大屏剧本BBB" not in prompt
    # 未展开的技能不静默消失：仍在清单里（名称+描述），并带可读原因
    assert "可视化大屏生成" in prompt and "本轮未展开作业细则" in prompt
    assert "未命中该技能的触发词" in prompt


def test_no_trigger_hit_expands_no_body_but_lists_skills():
    skills = [_skill("data-analysis", body="语义层剧本AAA", triggers=["趋势"]),
              _skill("screen-builder", body="大屏剧本BBB", triggers=["大屏"])]
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=skills, capabilities=_caps(), question="")
    assert "语义层剧本AAA" not in prompt and "大屏剧本BBB" not in prompt
    assert "## 已绑定技能" in prompt          # 清单 + description 常驻
    assert "只做演示用" in prompt


def test_body_count_is_capped():
    skills = [_skill(f"s{i}", body=f"剧本正文{i}", triggers=["命中"]) for i in range(4)]
    loaded, _reasons = _select_skill_bodies(skills, _caps(), "命中一下")
    assert len(loaded) == MAX_SKILL_BODIES


def test_capability_gate_blocks_body_for_unauthorized_tools():
    """requires_any 全未注册 → 不展开正文（不承诺没有的能力），且原因可见。"""
    skills = [
        _skill("data-analysis", body="语义层剧本AAA", triggers=["分析"],
               requires=["run_semantic_query"]),
        _skill("pro-analysis", body="SQL剧本CCC", triggers=["分析"], requires=["execute_sql"]),
    ]
    caps = _caps("mcp__datahub_semantic__run_semantic_query")
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=skills, capabilities=caps, question="SQL剧本 分析")
    assert "语义层剧本AAA" in prompt
    assert "SQL剧本CCC" not in prompt
    assert "本会话未授权 execute_sql 工具" in prompt
    # 同一对互斥剧本不会同时展开（否则是矛盾指令）
    assert not ("语义层剧本AAA" in prompt and "SQL剧本CCC" in prompt)


def test_capability_gate_passes_when_tool_authorized():
    skills = [_skill("pro-analysis", body="SQL剧本CCC", requires=["execute_sql"])]
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", skills=skills,
                                  capabilities=_caps("mcp__datahub_query__execute_sql"),
                                  question="任意问题")
    assert "SQL剧本CCC" in prompt


def test_unattended_run_expands_all_capable_bodies():
    """无人值守链路（定时分析/评测）跳过触发词收窄，不因省字数拿产出口径冒险。"""
    skills = [_skill("data-analysis", body="剧本AAA", triggers=["趋势"]),
              _skill("screen-builder", body="剧本BBB", triggers=["大屏"])]
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", skills=skills,
                                   capabilities=_caps(), question="今天天气",
                                   force_all_skill_bodies=True)
    assert "剧本AAA" in prompt and "剧本BBB" in prompt
    # 但能力门控仍生效
    blocked = [_skill("pro-analysis", body="剧本CCC", requires=["execute_sql"])]
    prompt2 = compose_system_prompt(None, [], username="u", user_role="viewer", skills=blocked,
                                     capabilities=_caps(), question="", force_all_skill_bodies=True)
    assert "剧本CCC" not in prompt2


def test_malformed_frontmatter_list_is_reported_not_silently_dropped(caplog):
    from backend.modules.mind.execution.as_bots import _name_list

    assert _name_list(["趋势", "环比"], "x", "triggers") == ["趋势", "环比"]
    assert _name_list("趋势,环比", "x", "triggers") == ["趋势", "环比"]
    assert _name_list(None, "x", "triggers") == []
    assert _name_list({"a": 1}, "x", "triggers") == []
    assert "不是名单" in caplog.text


# ── 尺寸护栏：不得再悄悄长回去 ─────────────────────────────────────

def _fat_case():
    """7 技能（含 2 个互斥分析剧本）+ 58 工具，问题只命中一个剧本。"""
    skills = [
        _skill("data-analysis", display="数据分析", body="剧本" * 1200,
               triggers=["趋势"], requires=["run_semantic_query"]),
        _skill("pro-analysis", display="专业分析师", body="SQL剧本" * 1300,
               triggers=["复杂查询"], requires=["execute_sql"]),
        _skill("screen-builder", display="大屏", body="大屏剧本" * 800,
               triggers=["大屏"], requires=["create_data_screen"]),
        _skill("report-delivery", display="报告交付", body="交付剧本" * 700, triggers=["报告"]),
        _skill("arms-observability", display="ARMS", body="观测剧本" * 1200,
               triggers=["首屏"], requires=["run_semantic_query"]),
        _skill("ontology-builder", display="本体构建", body="建模剧本" * 600,
               triggers=["本体"], requires=["generate_ontology_draft"]),
        _skill("metadata-manager", display="元数据管理", body="元数据剧本" * 150,
               triggers=["表结构"], requires=["search_metadata"]),
    ]
    caps = _caps(*[f"mcp__datahub_semantic__t{i}" for i in range(30)]
                 + [f"mcp__datahub_functions__f{i}" for i in range(28)]
                 + ["mcp__datahub_semantic__run_semantic_query"])
    return skills, caps


def test_prompt_size_guardrail_multi_skill_role():
    skills, caps = _fat_case()

    empty = compose_system_prompt(None, [], username="u", user_role="admin",
                                 skills=skills, capabilities=caps, question="")
    # 改造前同等配置实测 45,548 字符；护栏值取 2 倍余量，防止重新堆料
    assert len(empty) < 20_000, f"空输入 system_prompt 回涨到 {len(empty)} 字符"

    analysis = compose_system_prompt(None, [], username="u", user_role="admin",
                                     skills=skills, capabilities=caps, question="看下趋势")
    assert "剧本剧本" in analysis and "SQL剧本" not in analysis
    assert len(analysis) < 26_000, f"分析回合 system_prompt 回涨到 {len(analysis)} 字符"


def test_safety_sections_never_dropped_by_slimming():
    """瘦身不得动安全强约束与语义层权威划分（no-silent-degradation / 护栏 §7）。"""
    skills, caps = _fat_case()
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=skills, capabilities=caps, question="趋势")
    for must in ("## 数据源黑盒（强约束）", "## 敏感数据合规（强约束，不可协商）",
                 "## 数据查询规范", "get_metrics` 实时目录为唯一权威",
                 "# 数据可视化输出约定"):
        assert must in prompt, f"强约束段缺失: {must}"


def test_shipped_skills_declare_loading_contract():
    """内置技能的 frontmatter 必须带真实的装载元数据（锁住配置，不让“改了丢键”逸出）。

    两个分析剧本的取数口径互斥（data-analysis 不写 SQL / pro-analysis 走受治理 SQL），
    `requires_any` 就是让它们不在同一会话里并存的手段，丢了键会退回矛盾指令同现。
    """
    from backend.modules.mind.execution.as_bots import load_skills

    loaded = {s["name"]: s for s in load_skills([
        "data-analysis", "pro-analysis", "screen-builder", "ontology-builder", "metadata-manager"])}
    assert loaded["data-analysis"]["requires_any"] == ["run_semantic_query"]
    assert loaded["pro-analysis"]["requires_any"] == ["execute_sql"]
    for name, skill in loaded.items():
        assert skill["description"], f"{name} 缺 description（未展开正文时的浓缩契约）"
        assert skill["triggers"], f"{name} 缺 triggers（多技能时无法判定是否展开）"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
