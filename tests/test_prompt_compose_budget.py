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


def test_real_skill_triggers_cover_traffic_and_url_questions():
    """内置技能 triggers 必须命中一线运营的流量/URL 类高频问法，且正文带空结果纪律。

    历史缺陷（会话 1783069459140，2026-10-29）：用户问“某条带 ?channel= 参数的 URL 的
    访问量”，三个绑定技能一个触发词都没命中 → 本轮零分析剧本正文，模型在没有 URL 拆解
    剧本、没有空结果裁决纪律的情况下取数，把一次 0 行快照断言成“渠道参数没进统计口径”（实测
    同一条 URL 后续查到 20 PV/2 UV）。用户第二轮补了个“rum”才命中技能，同一问题立刻答对
    ——差的就是触发词覆盖面。本用例把“流量类问句必须拿到剧本”锁住。
    """
    from backend.modules.mind.execution.as_bots import load_skills

    loaded = list(load_skills(["data-analysis", "arms-observability", "report-delivery"]))
    caps = _caps("mcp__datahub_semantic__run_semantic_query",
                 "mcp__datahub_semantic__knowledge_search", "mcp__datahub_semantic__get_metrics")
    for q in (
        "我想知道https://example.com/en/direct-ip?channel=EDMCA的访问量",
        "帮我看下官网昨天的 PV 和 UV",
        "这个页面的访问量和上周比怎么样",
        "direct-ip 落地页 EDM 投放带来多少访问",
    ):
        bodies, _reasons = _select_skill_bodies(loaded, caps, q)
        names = {b["name"] for b in bodies}
        assert names & {"data-analysis", "arms-observability"}, f"流量/URL 类问句未展开任何分析剧本: {q}"

    # 零结果裁决纪律与 URL 拆解剧本必须在正文里（本次答错就错在这两条缺席）
    bodies = {s["name"]: (s.get("system_prompt") or "") for s in loaded}
    for name in ("data-analysis", "arms-observability"):
        assert ("空结果" in bodies[name] or "零结果" in bodies[name]), f"{name} 缺空结果裁决纪律"
    url_body = bodies["arms-observability"]
    assert "两个数一起给" in url_body and "完整键值" in url_body, "观测域缺 URL 拆解剧本"


def test_shipped_skill_bodies_keep_traffic_prompt_within_guardrail():
    """真实内置技能正文尺寸不得失控：流量类问句会同时展开两个分析剧本。

    上一个用例锁“能命中”，本用例锁“命中后不超支”。三档实测基线（2026-10-29，本次
    补触发词后）必须分清，否则护栏会校准到一个比生产小的形状上、给出假安全感：
    - 窄载荷（2 个工具、无知识库/站点拓扑）：~23,300 字符；
    - 生产同形静态载荷（13 个工具 + 1 个绑定知识库 + 5 站含聚合态）：~24,845 字符；
    - 真实 Operations Manager（含 persona/system_prompt、用户记忆、真实工具描述）：
      ~26,600 字符。最后者与前者之间的 ~4,600 不在本用例可控范围（persona 尺寸由
      tests/test_as_bot_operations_prompt.py 的内容断言 + 迁移里的正文长度兼顾）。
    再涨就是往常驻指令里堆料，必须显式拍板而不是随手加两句。
    2026-10-29 第一次拍板：新增常驻「工具自主编排 + 歧义闸门」段与三个剧本的「入口分诊」段，
    22,000/20,400 -> 24,040/22,500，上限 25,000（理由是「让模型自主编排 + 歧义不猜」）。
    同日第二次拍板（实测缺陷：问「官网地址是什么」被答成站点对照表 + 三条推销 + 37.3s）：
    常驻段补「回答体量必须匹配问题」，三个剧本的「六、输出规范」补适用前提，
    24,040/22,500 -> 24,845/23,300，上限取 26,000。两次抬的都是行为收益换字数，
    不是瘦身失败——后续再涨必须先说明换来了什么行为。
    """
    from backend.modules.mind.execution.as_bots import load_skills

    skills = list(load_skills(["data-analysis", "arms-observability", "report-delivery"]))
    caps = _caps("mcp__datahub_semantic__run_semantic_query", "mcp__datahub_semantic__get_metrics")
    question = "这个页面的访问量和上周比怎么样"
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=skills, capabilities=caps, question=question)
    loaded, _ = _select_skill_bodies(skills, caps, question)
    assert {s["name"] for s in loaded} == {"data-analysis", "arms-observability"}
    assert len(prompt) < 27_000, f"两个分析剧本同现时 system_prompt 回涨到 {len(prompt)} 字符"

    # 生产同形：加上知识库段与站点拓扑段（聚合态的逐站规则 ③ 是最长的一条注入项），护栏必须跟着抬
    rich_caps = _caps(*[f"mcp__datahub_semantic__{n}" for n in (
        "run_semantic_query", "get_metrics", "knowledge_search", "get_glossary", "list_datasources",
        "search_business_knowledge", "get_business_semantics", "ask_user", "create_report",
        "search_metadata", "get_table_schema", "system_usage", "system_overview")])
    sites = [{"site_code": f"s{i}", "display_name": f"站点{i}", "datasources": [{"name": f"src{i}"}],
              "is_aggregate": 0} for i in range(1, 6)] + [
        {"site_code": "global", "display_name": "全球", "datasources": [], "is_aggregate": 1}]
    prod_shape = compose_system_prompt(None, [], username="u", user_role="operator",
                                       knowledge_bases=[{"name": "运营知识库", "kb_type": "qmind"}],
                                       skills=skills, capabilities=rich_caps, quick_paths=[],
                                       site_code="global", authorized_sites=sites, question=question)
    assert "## 数据源拓扑" in prod_shape and "运营知识库" in prod_shape, "生产同形载荷未注入拓扑/知识库段"
    assert len(prod_shape) < 26_000, f"生产同形载荷（含拓扑与知识库）回涨到 {len(prod_shape)} 字符"


def test_shipped_time_window_directive_matches_implementation():
    """技能里关于时间窗口的指令必须与实现一致（防止把模型往静默丢参数的方向带）。

    历史缺陷（arms-observability 旧正文）：“不要用 time_window（会被忽略并告警），
    改传 params{"days": …}”——实测正好相反：`time_window` 与 `time_start`/`time_end` 都会
    写进时间谓词，而未声明的 `params` 键会被**静默丢弃**，时间过滤整个消失（全量扫描）。
    错的常驻指令比没有指令更危害：模型会稳定地重复它，而失败形式是“数字看起来对但窗口没人知道”。
    """
    from backend.modules.mind.execution.as_bots import load_skills

    bodies = {s["name"]: (s.get("system_prompt") or "") for s in load_skills([
        "data-analysis", "arms-observability", "pro-analysis"])}
    for name, body in bodies.items():
        assert "不要用 `time_window`" not in body, f"{name} 仍在教模型避开真实生效的 time_window"
        assert "改传 params" not in body, f"{name} 仍在推荐用 params 控时间窗口（会被静默丢弃）"
    arms = bodies["arms-observability"]
    assert "time_window" in arms and "静默丢掉" in arms, "观测域缺窗口口径与静默丢参告警"

# ── 工具自主编排 + 歧义闸门（常驻段）与三个剧本的「入口分诊」（本次优化）────────
# 取向：工具选择权归模型，提示词只给判断依据与预算；歧义必须先问、不许拿默认值跑数。

def test_orchestration_and_ambiguity_rules_are_always_injected():
    """工具选择权归模型：给判断依据与预算，不给工具白名单；歧义必须先问不先猜。"""
    caps = _caps("mcp__datahub_semantic__get_metrics", "mcp__datahub_semantic__run_semantic_query")
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer", capabilities=caps)
    assert "## 工具自主编排" in prompt and "## 歧义闸门" in prompt
    assert "不规定你必须调用哪几个工具" in prompt          # 反白名单：由模型自己判断查哪件
    assert "先 `ask_user` 再取数" in prompt                 # 两种解读给出不同数字 → 不许猜
    assert "不要先按某个默认值跑出结果再解释" in prompt
    assert "别把能自己定的事推给用户" in prompt             # 反问不是免责，过度澄清同样扣分
    # 回答体量必须匹配问题（实测缺陷：问「官网地址是什么」被答成对照表 + 三条推销 + 37s）
    assert "## 回答体量必须匹配问题" in prompt
    assert "**事实型**" in prompt and "1~2 句把答案给完" in prompt
    assert "你可能还想看" in prompt and "判不准时按**更轻**的那档答" in prompt
    assert "回显所用口径" in prompt                         # 自己定的口径必须让用户能纠偏
    assert "不管该做的消歧" in prompt                       # 预算只管无目的重复
    # 历史缺陷回潮检测：旧措辞把纯口径问题挡在检索之外，模型不搜就回"不知道"
    assert "两种情况才" not in prompt


def test_semantic_rules_open_knowledge_questions_to_retrieval():
    """口径/定义类问题必须在检索场景清单里（(c) 档），否则等于默许凭记忆答口径。"""
    caps = _caps("mcp__datahub_semantic__get_metrics", "mcp__datahub_semantic__knowledge_search",
                 "mcp__datahub_semantic__run_semantic_query")
    prompt = compose_system_prompt(None, [], username="u", user_role="analyst", capabilities=caps)
    assert "三类问题" in prompt or "先查再答" in prompt
    assert "(c) 问题**只要解释、不要数字**" in prompt


def test_shipped_skills_open_with_triage_before_exploration():
    """三个分析剧本都以「入口分诊」开宗明义：先判要什么、有无第二种解读，再决定编排。"""
    from backend.modules.mind.execution.as_bots import load_skills

    bodies = {r["name"]: (r.get("system_prompt") or "")
              for r in load_skills(["data-analysis", "arms-observability", "pro-analysis"])}
    assert len(bodies) == 3
    for name, body in bodies.items():
        assert "## 〇、入口分诊" in body, f"{name} 缺入口分诊"
        assert body.index("## 〇、入口分诊") < body.index("## 一、"), f"{name} 分诊段位置不对"
        assert "第二种合理解读" in body and "ask_user" in body, f"{name} 缺歧义闸门"
        assert "回显所用口径" in body, f"{name} 缺口径回显"
        assert "无目的重复" in body, f"{name} 缺「消歧不算浪费」的预算口径"
        # 输出规范只服务分析型交付：事实型/解释型不得被套上「结论+图+下一步」模板
        assert "## 六、输出规范" in body and "适用前提" in body, f"{name} 缺输出规范的适用前提"
        assert body.index("适用前提") > body.index("## 六、输出规范"), f"{name} 适用前提位置不对"
        assert "至多 2 个值得继续深挖" in body or "主动提出下一步" not in body, \
            f"{name} 的下一步推销未限定在分析型交付"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])


# ── 纯知识/口径问答：最短路径规则（不建旁路通道，只让 agent 少走弯路）───────

if __name__ == "__main__":
    pytest.main([__file__, "-q"])
