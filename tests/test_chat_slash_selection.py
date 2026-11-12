"""智能问数输入框「/」快捷检索的契约：候选口径、指定技能生效、未登记指令显式拒。

背景：用户想「用某个技能回答」或「把这轮问题变成定时任务」时，过去只能把名字写进
问句里碰触发词。`/` 让选择变成显式动作，因此必须锁住三件事：
* 指定技能**优先**展开正文（不看触发词），但仍受能力门控与 MAX_SKILL_BODIES 约束；
* 指定了却没绑/没授权的技能不静默丢弃 —— 提示词里必须出现「未生效」事实（no-silent-degradation）；
* 前端能选的候选与后端能执行的指令同源：未登记的 slash_command 直接 400，不静默当"没选"。
"""
import pytest
from fastapi import HTTPException

from backend.modules.mind.execution import slash_commands
from backend.modules.mind.execution.prompt_composer import (
    MAX_SKILL_BODIES,
    _select_skill_bodies,
    compose_system_prompt,
)


def _caps(*tool_names):
    return {"tools": [{"name": n, "description": ""} for n in tool_names],
            "unavailable_tools": []}


def _skill(name, *, display=None, body=None, triggers=None, requires=None, group=""):
    return {"name": name, "display_name": display or name, "description": "演示用",
            "system_prompt": body if body is not None else f"{name} 的作业细则正文",
            "requires_any": requires or [], "triggers": triggers or [],
            "exclusive_group": group}


SEMANTIC = "mcp__datahub_semantic__run_semantic_query"


# ── 1. 指定技能：优先展开正文，不受触发词限制 ────────────────────────────────

def test_pinned_skill_body_loads_without_trigger_hit():
    skills = [_skill("data-analysis", triggers=["分析"]), _skill("report-delivery", triggers=["报表"])]
    # 问句里没有任何触发词
    loaded, reasons, missing = _select_skill_bodies(skills, _caps(SEMANTIC), "今天天气如何",
                                                    pinned=["report-delivery"])
    assert [s["name"] for s in loaded] == ["report-delivery"]
    assert missing == []
    assert reasons["data-analysis"]            # 未选中的仍带原因，不静默消失


def test_pinned_skill_matches_by_display_name():
    skills = [_skill("data-analysis", display="数据分析师")]
    loaded, _, missing = _select_skill_bodies(skills, _caps(SEMANTIC), "随便问问",
                                              pinned=["数据分析师"])
    assert [s["name"] for s in loaded] == ["data-analysis"] and missing == []


def test_pinned_respects_capability_gate():
    """指定了但本会话没授权它依赖的工具 → 不展开正文，并回传为「未生效」而不是假装成功。"""
    skills = [_skill("pro-analysis", triggers=["x"], requires=["execute_sql"]),
              _skill("data-analysis", triggers=["y"])]
    loaded, _, missing = _select_skill_bodies(skills, _caps(SEMANTIC), "问句",
                                              pinned=["pro-analysis"])
    assert "pro-analysis" not in [s["name"] for s in loaded]
    assert missing == ["pro-analysis"]


def test_pinned_count_still_capped():
    skills = [_skill(f"s{i}") for i in range(MAX_SKILL_BODIES + 2)]
    loaded, _, _ = _select_skill_bodies(skills, _caps(SEMANTIC), "问句",
                                        pinned=[s["name"] for s in skills])
    assert len(loaded) == MAX_SKILL_BODIES


def test_unbound_pinned_skill_is_visible_in_prompt():
    """用户点名了没绑定的技能：提示词必须写清「本轮没按它执行」，让模型告知用户。"""
    prompt = compose_system_prompt(
        None, [], username="u", user_role="viewer",
        skills=[_skill("data-analysis")], capabilities=_caps(SEMANTIC),
        question="随便问问", pinned_skills=["nonexistent-skill"])
    assert "## 用户指定技能未生效" in prompt
    assert "nonexistent-skill" in prompt


def test_no_pinned_skills_keeps_prompt_unchanged():
    prompt = compose_system_prompt(None, [], username="u", user_role="viewer",
                                   skills=[_skill("data-analysis")],
                                   capabilities=_caps(SEMANTIC), question="随便问问")
    assert "用户指定技能未生效" not in prompt
    assert "## 本轮用户指定操作" not in prompt


# ── 2. 指令目录与作业要求同源 ────────────────────────────────────────────────

def test_every_advertised_command_has_its_own_kind_backend():
    """菜单里能选到的指令，必须有对应类型的落点（否则就是点了没反应的空承诺）。

    两类分开校（用户 2026-11-06 要求把「清空当前会话」放进指令面板）：
      * directive —— 必须有作业要求文案，本轮仍走模型；
      * action —— 必须有客户端动作名，且**不得有**作业要求（下发给 LLM 就是
        向它广告一个它做不到的事）。
    """
    for item in slash_commands.list_commands():
        assert item["key"] and item["label"]
        assert slash_commands.is_known_command(item["key"])
        if item["kind"] == "action":
            assert item["action"], f"action 类指令 {item['key']} 没带动作名"
            assert slash_commands.directive_for(item["key"]) == "", item["key"]
        else:
            assert slash_commands.directive_for(item["key"]), f"指令 {item['key']} 缺作业要求"


def test_clear_command_is_a_client_action_not_an_llm_directive():
    """「/ 清空当前会话」登记为客户端动作：面板选中即执行，不进请求体。"""
    clear = next(c for c in slash_commands.list_commands() if c["key"] == "clear")
    assert clear["kind"] == "action" and clear["action"] == "clear_conversation"
    # 描述里要如实写明不可撤销与运行中限制（不能只说“清空”就完事）
    assert "不可撤销" in clear["description"]


def test_action_command_sent_as_slash_command_is_rejected():
    """action 类指令被当成 slash_command 上送 = 契约错配，必须 400 拒而不是静默忽略。"""
    from backend.modules.mind.api.chat import _assert_slash_command
    with pytest.raises(HTTPException) as exc:
        _assert_slash_command("clear")
    assert exc.value.status_code == 400 and "会话操作" in exc.value.detail


def test_create_scheduled_task_directive_keeps_permission_authority_with_tools():
    text = slash_commands.directive_for("create_scheduled_task")
    assert "create_scheduled_task" in text
    # 周期不明确要先问，不拿猜出来的 cron 落库；权限拒绝要转述而不是冒充成功
    assert "ask_user" in text and "拒绝" in text


def test_unknown_command_directive_is_empty():
    assert slash_commands.directive_for("delete_everything") == ""


@pytest.mark.parametrize("key", ["create_scheduled_task", ""])
def test_known_command_passes_assertion(key):
    from backend.modules.mind.api.chat import _assert_slash_command
    _assert_slash_command(key)          # 空 = 未选指令，合法


def test_unknown_command_is_rejected_at_api_entry():
    from backend.modules.mind.api.chat import _assert_slash_command
    with pytest.raises(HTTPException) as exc:
        _assert_slash_command("not-a-command")
    assert exc.value.status_code == 400 and "快捷指令" in exc.value.detail


def test_service_side_unknown_command_does_not_inject_anything():
    """内部调用方漏校验时也不得注入未知指令（宁可不执行，不可冒充已执行）。"""
    from backend.modules.mind.services.chat_service import _slash_directive
    assert _slash_directive("") == ""
    assert _slash_directive("nope") == ""
    assert _slash_directive("create_scheduled_task")


# ── 3. 互斥组：两份打架的剧本不得同时展开 ────────────────────────────────────

KNOWLEDGE = "mcp__datahub_semantic__knowledge_search"
PLAYBOOK = (
    _skill("knowledge-qa", display="知识问答", triggers=["是什么", "口径"], group="playbook"),
    _skill("data-analysis", display="数据分析", triggers=["趋势", "新增", "活跃"], group="playbook"),
)


def test_exclusive_group_loads_only_one_body_on_trigger_path():
    """定义题命中知识问答：同组的数据分析即使也命中触发词，不再展开第二份正文。"""
    skills = list(PLAYBOOK) + [_skill("report-delivery", triggers=["报表"])]
    loaded, reasons, _ = _select_skill_bodies(skills, _caps(KNOWLEDGE, SEMANTIC),
                                              "活跃客户是什么口径")
    assert [s["name"] for s in loaded] == ["knowledge-qa"]
    assert "互斥组" in reasons["data-analysis"]      # 落选原因说得出真相


def test_exclusive_group_keeps_analysis_for_data_question():
    skills = list(PLAYBOOK)
    loaded, reasons, _ = _select_skill_bodies(skills, _caps(KNOWLEDGE, SEMANTIC),
                                              "近7天新增用户趋势")
    assert [s["name"] for s in loaded] == ["data-analysis"]
    assert "互斥组" not in reasons.get("knowledge-qa", "")   # 这条是没命中，不是被互斥挤掉


def test_pinned_two_conflicting_skills_still_expand_one():
    """用户 `/` 同时点了一对互斥剧本：只展开先点的那个，另一个给显式原因（不静默丢）。"""
    loaded, reasons, missing = _select_skill_bodies(
        list(PLAYBOOK), _caps(KNOWLEDGE, SEMANTIC), "问句",
        pinned=["knowledge-qa", "data-analysis"])
    assert [s["name"] for s in loaded] == ["knowledge-qa"]
    assert missing == [] and "互斥组" in reasons["data-analysis"]


# ── 4. 问答模式（深度分析 / 快速问答）：与剧本同源，未绑定不静默 ──────────────

def test_every_advertised_mode_has_skill_and_directive():
    """前端能切的模式，服务端必须既有对应剧本名又有作业要求（选了就得真生效）。"""
    for m in slash_commands.list_modes():
        assert slash_commands.skill_for_mode(m["key"])
        assert slash_commands.mode_directive(m["key"]), m["key"]
        assert slash_commands.is_known_mode(m["key"])
        # 选择框直接渲染 label/description：两者缺一就是个看不出在选什么的空控件
        assert m["label"] and m["description"], m["key"]


def test_mode_labels_are_the_agreed_business_names():
    """模式业务名口径（用户拍板）：深度分析 / 快速问答，不回到旧的 数据分析/知识问答。

    旧名与剧本 display_name 同形，用户分不清“选模式”和“选技能”；改名只动 label，
    key（analysis/knowledge）不变，否则存量会话的 chat_mode 会集体变成未知入口。
    """
    labels = {m["key"]: m["label"] for m in slash_commands.list_modes()}
    assert labels == {"analysis": "深度分析", "knowledge": "快速问答"}
    assert slash_commands.label_for_mode("knowledge") == "快速问答"


def test_mode_order_is_light_first():
    """服务端模式清单顺序：轻任务在前（快速问答 → 深度分析），默认项 = 第一项。

    前端入口已撤（用户拍板不区分快速/深度，不再上送 chat_mode），本清单只供服务端
    内部一致性与外部调用方参考；顺序与 MODE_DEFAULT 必须同项，否则“默认选中第二项”。
    """
    modes = slash_commands.list_modes()
    assert [m["key"] for m in modes] == ["knowledge", "analysis"]
    assert slash_commands.MODE_DEFAULT == modes[0]["key"]


def test_frontend_no_longer_sends_chat_mode():
    """入口已撤的前端口径：不得再上送 chat_mode，也不得留 chatMode 死配置。

    只隐去控件但仍上送默认值 = 行为上仍在“分快速/深度”，只是用户看不见在哪切。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "frontend" / "src"
    chat = (root / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    store = (root / "stores" / "chatStore.ts").read_text(encoding="utf-8")
    # 请求体不再带 chat_mode；store 不再有这个状态/类型/ setter（注释里提“已撤”不算）
    assert "chat_mode:" not in store
    assert "chatMode:" not in store
    assert "export type ChatMode" not in store
    assert "setChatMode" not in store
    # 输入区不再渲染模式选择框与兜底清单
    assert "MODE_FALLBACK" not in chat
    assert "<Select value={chatMode}" not in chat


def test_knowledge_mode_expands_playbook_and_blocks_rival():
    prompt = compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=list(PLAYBOOK), capabilities=_caps(KNOWLEDGE, SEMANTIC),
        question="随便问问", chat_mode="knowledge")
    assert "### 技能:知识问答" in prompt
    assert "### 技能:数据分析" not in prompt
    assert "## 本轮用户指定操作" in prompt and "快速问答" in prompt


def test_unbound_mode_skill_still_states_the_fact():
    """用户**显式切到**某模式但剧本没绑：仍按作业要求执行，并把“未绑定”写进提示词告知用户。"""
    prompt = compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=[_skill("knowledge-qa")], capabilities=_caps(KNOWLEDGE, SEMANTIC),
        question="活跃客户近 7 天趋势", chat_mode="analysis")
    assert "## 问答模式未绑定剧本（深度分析）" in prompt
    assert "## 本轮用户指定操作" in prompt


def test_default_mode_is_first_option_and_still_carries_its_own_requirements():
    """默认项 = 快速问答 = 清单第一项，且默认也注入自己的作业要求。

    旧写法把默认模式短路成“不注入作业要求”：那时默认项还是数据分析（=存量现状）。
    默认项换成快速问答后继续短路，界面就写着“快速问答”而实际按老路跑（空承诺）。
    """
    assert slash_commands.MODE_DEFAULT == slash_commands.list_modes()[0]["key"]
    assert slash_commands.MODE_DEFAULT == "knowledge"

    for skills in ([_skill("metadata-manager")], [_skill("data-analysis")]):
        prompt = compose_system_prompt(
            None, [], username="u", user_role="viewer",
            skills=skills, capabilities=_caps(SEMANTIC),
            question="随便问问", chat_mode="knowledge")
        # 作业要求照注入（名称必须兜得住能力）
        assert "## 本轮用户指定操作" in prompt and "快速问答" in prompt
        # 但默认模式不因“剧本没绑”逐轮骚扰用户：没人选它，存量 AS-BOT 普遍未绑
        assert "问答模式未绑定剧本" not in prompt
        assert "用户指定技能未生效" not in prompt


def test_no_mode_at_all_keeps_legacy_prompt_shape():
    """未传模式（无人值守链路：定时任务/评测）= 存量行为：不注入任何模式的作业要求。"""
    prompt = compose_system_prompt(
        None, [], username="u", user_role="viewer",
        skills=[_skill("data-analysis")], capabilities=_caps(SEMANTIC),
        question="随便问问", chat_mode="")
    assert "## 本轮用户指定操作" not in prompt
    assert "问答模式未绑定剧本" not in prompt


def test_explicit_deep_analysis_mode_injects_its_requirements():
    """显式选深度分析：注入全量能力的作业要求（它不再是默认项，不再被短路成“无要求”）。

    深度分析是用户主动选的，所以剧本没绑时仍会如实提醒（见
    test_unbound_mode_skill_still_states_the_fact）；绑了就强制展开正文。
    """
    prompt = compose_system_prompt(
        None, [], username="u", user_role="viewer",
        skills=[_skill("metadata-manager")], capabilities=_caps(SEMANTIC),
        question="随便问问", chat_mode="analysis")
    assert "## 本轮用户指定操作" in prompt and "深度分析" in prompt

    bound = compose_system_prompt(
        None, [], username="u", user_role="admin",
        skills=list(PLAYBOOK), capabilities=_caps(KNOWLEDGE, SEMANTIC),
        question="活跃客户近 7 天趋势", chat_mode="analysis")
    assert "### 技能:数据分析" in bound
    assert "### 技能:知识问答" not in bound      # 互斥组只展开一份


@pytest.mark.parametrize("mode", ["analysis", "knowledge", ""])
def test_known_mode_passes_assertion(mode):
    from backend.modules.mind.api.chat import _assert_chat_mode
    _assert_chat_mode(mode)                # 空 = 未选入口，合法


def test_unknown_mode_is_rejected_at_api_entry():
    from backend.modules.mind.api.chat import _assert_chat_mode
    with pytest.raises(HTTPException) as exc:
        _assert_chat_mode("do-anything")
    assert exc.value.status_code == 400 and "问答入口" in exc.value.detail
    # 拒回时把可选模式用业务名说清楚（候选来自同一份 MODES，不写死旧名）
    assert "深度分析" in exc.value.detail and "快速问答" in exc.value.detail


def test_shipped_playbooks_are_mutually_exclusive_by_config():
    """真实 SKILL.md 必须带上互斥组：知识问答与数据分析同时展开就是给模型矛盾指令。"""
    from backend.modules.mind.execution.as_bots import load_skills
    loaded = {s["name"]: s for s in load_skills(
        ["knowledge-qa", "data-analysis", "pro-analysis"])}
    assert set(loaded) == {"knowledge-qa", "data-analysis", "pro-analysis"}
    assert all(s["exclusive_group"] == "playbook" for s in loaded.values())
    assert loaded["knowledge-qa"]["requires_any"] == ["knowledge_search"]
