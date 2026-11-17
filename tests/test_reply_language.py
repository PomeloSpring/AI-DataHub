"""回复语言口径契约（提问语言优先 → 界面语言兜底 → 默认中文）。

背景（2026-11-16 用户拍板）：前端已有多语言切换（zh-CN / en-US，localStorage），
但语言从没传到后端，且 5 份技能正文与角色 persona 里都硬编码「中文回答」——
切到 English 后界面是英文、LLM 仍回中文，属于"配置面有、消费端没有"的空承诺。

本文件锁定四件事：
1. 判定唯一在 `backend/common/i18n.resolve_reply_language`，三条路径可分辨（basis 不同）；
2. 提示词常驻注入「回复语言」段，且**只有这一处**指定语言（技能/迁移/persona 不得再写死）；
3. 界面语言从请求头进入 ctx.extra（服务端内部键，不进 LLM 视野、不参与权限裁决）；
4. 交付 HTML 文档的 `<html lang>` 与文档语言一致（显式传入优先，其次按内容自判）。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from backend.common import i18n

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── 1) Accept-Language 解析与归一 ──────────────────────────────────

@pytest.mark.parametrize("header, expected", [
    ("zh-CN", "zh-CN"),
    ("en-US", "en-US"),
    ("en-US,en;q=0.9,zh-CN;q=0.8", "en-US"),          # 尊重 q 值优先级
    ("zh-CN;q=0.5,en;q=0.9", "en-US"),                 # 首选被降权时取次选
    ("fr-FR,de-DE;q=0.9", ""),                          # 全不支持 = 没有界面语言（不猜）
    ("klingon", ""),
    ("", ""),
    (None, ""),
    ("zh_TW", "zh-CN"),                                 # 简写/下划线变体归一
    ("en-GB", "en-US"),
])
def test_parse_accept_language_normalizes_and_never_guesses(header, expected):
    assert i18n.parse_accept_language(header) == expected


def test_supported_locales_match_frontend_declaration():
    """前后端语言清单不得漂移（前端是唯一录入处，后端只消费）。"""
    src = (ROOT / "frontend/src/i18n/index.ts").read_text(encoding="utf-8")
    declared = re.search(r"export const LOCALES = \[([^\]]*)\]", src)
    assert declared, "前端 LOCALES 常量没找到（清单结构变了必须同步本用例）"
    listed = tuple(re.findall(r"'([^']+)'", declared.group(1)))
    assert listed == i18n.SUPPORTED_LOCALES


# ── 2) 提问语言检测（保守，判不出就交兜底）────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("上个月各站点推送了多少次", "zh-CN"),
    ("近 7 天 TOP10 客户是谁", "zh-CN"),                 # 中英夹杂：汉字是最强信号
    ("top 10 customers by push volume last month", "en-US"),
    ("What is the success rate this week?", "en-US"),
    ("2026-11", ""),                                     # 纯数字/日期不构成语言信号
    ("CPU", ""),                                         # 单个标识符不是句子
    ("", ""),
    ("   ", ""),
])
def test_detect_question_language_is_conservative(text, expected):
    assert i18n.detect_question_language(text) == expected


# ── 3) 判定优先级与三条路径可分辨 ─────────────────────────────────

def test_question_language_wins_over_ui_locale():
    """英文界面 + 中文提问 → 中文（提问语言优先，用户拍板的口径）。"""
    locale, basis = i18n.resolve_reply_language("上个月销售额趋势如何", ui_locale="en-US")
    assert (locale, basis) == ("zh-CN", "用户本轮提问的语言")


def test_ui_locale_is_fallback_when_question_undeterminable():
    locale, basis = i18n.resolve_reply_language("2026-11", ui_locale="en-US")
    assert locale == "en-US" and basis.startswith("界面语言")


def test_default_when_no_context_at_all():
    """无人值守（定时分析/评测/内部调用）：既无提问语言也无界面语言 → 平台默认。"""
    locale, basis = i18n.resolve_reply_language("", "")
    assert locale == i18n.DEFAULT_LOCALE and "默认" in basis


def test_three_paths_are_distinguishable():
    """三条路径的 basis 必须两两不同（评测/日志要能分辨正常路径与兜底路径）。"""
    bases = {i18n.resolve_reply_language(q, u)[1] for q, u in [
        ("查一下销量", "en-US"),      # 提问语言赢
        ("2026-11", "en-US"),        # 提问判不出 → 界面语言
        ("", ""),                    # 两者都无 → 平台默认
    ]}
    assert len(bases) == 3, bases


# ── 4) 提示词注入：唯一权威语言段 ─────────────────────────────────

def _compose(question: str = "", ui_locale: str = "") -> str:
    from backend.modules.mind.execution.prompt_composer import compose_system_prompt
    return compose_system_prompt(None, [], username="u", user_role="analyst",
                                 question=question, ui_locale=ui_locale)


def test_reply_language_section_always_present():
    assert "## 回复语言" in _compose()


def test_english_question_gets_english_instruction():
    prompt = _compose("top 10 customers by volume last month", ui_locale="zh-CN")
    assert "Respond in **English**" in prompt
    # 中文指令句只存在于「回复语言」段里：它没出现 = 本轮确实按英文下发（而不是两段并存矛盾）
    assert "请用**简体中文**回答" not in prompt
    assert "依据：用户本轮提问的语言" in prompt


def test_ui_locale_fallback_appears_in_prompt_with_basis():
    prompt = _compose("2026-11", ui_locale="en-US")
    assert "Respond in **English**" in prompt
    assert "依据：界面语言" in prompt


class _FakeAdapter:
    def _prompt_with_attachments(self, task, include_history=True):
        return task.question


class _FakeCtx:
    def __init__(self, extra):
        self.extra = extra


class _FakeTask:
    def __init__(self, question, extra):
        self.question = question
        self.context = _FakeCtx(extra)


def test_turn_prompt_repeats_language_after_the_question():
    """语言指令不能只进系统提示词（实测会被后面的常驻段压过去）：本轮问题之后也要有一句。"""
    from backend.modules.mind.execution.secure_sdk import build_turn_prompt
    out = build_turn_prompt(_FakeAdapter(), _FakeTask("top 10 customers last month",
                                                     {"ui_locale": "zh-CN"}),
                            include_history=False)
    assert out.index("Reply this turn in English") > out.index("top 10 customers")  # 落在问题之后
    assert "依据：用户本轮提问的语言" in out


def test_turn_prompt_keeps_scene_directive_order():
    """接语言行时不得挤掉场景作业行：顺序固定为 问题 → 场景 → 语言。"""
    from backend.modules.mind.execution.secure_sdk import build_turn_prompt
    extra = {"_scene_turn_directive": "按已选场景取数", "ui_locale": "en-US"}
    out = build_turn_prompt(_FakeAdapter(), _FakeTask("查销量", extra), include_history=False)
    assert "按已选场景取数" in out and "本轮用简体中文回复" in out
    assert out.index("查销量") < out.index("按已选场景取数") < out.index("本轮用简体中文回复")


def test_language_never_leaks_into_tool_args_or_identity_block():
    """界面语言只影响叙述：不得出现在权限段（它不是权限开关）。"""
    prompt = _compose("sales", ui_locale="en-US")
    assert "Accept-Language" not in prompt           # 只注入判定结果，不注入原始头
    tail = prompt.split("## 当前用户与权限", 1)[-1]
    assert "en-US" not in tail.split("##")[0]


# ── 5) 单一事实源：硬编码语言必须清零 ─────────────────────────────

HARD_CODED = ("中文回答", "使用中文与用户沟通", "请用中文回答", "回答使用中文")


@pytest.mark.parametrize("path", sorted(
    (ROOT / "backend/modules/mind/config/skills").glob("**/SKILL.md")))
def test_skill_bodies_do_not_hardcode_language(path):
    text = path.read_text(encoding="utf-8")
    hits = [w for w in HARD_CODED if w in text]
    assert not hits, f"{path.name} 又写死了语言 {hits}：语言口径只在提示词「回复语言」段"


def test_seed_migrations_do_not_reintroduce_language():
    """种子迁移里写死语言 = 新环境初始化把 bug 装回去（persona 会压过通用输出契约）。

    例外：本次的收敛迁移 `reply_language_persona_migration.sql` 本身必须含那些字串
    （它是 REPLACE 的匹配模式）——不把它放进扫描，否则下一个写收敛迁移的人会把护栏改掉。
    """
    SELF = "reply_language_persona_migration.sql"
    bad = []
    for p in sorted((ROOT / "docker/mysql").glob("*.sql")):
        if p.name == SELF:
            continue
        text = p.read_text(encoding="utf-8")
        hits = [w for w in HARD_CODED if w in text]
        if hits:
            bad.append(f"{p.name}: {hits}")
    assert not bad, "迁移种子不得再写死回复语言：\n  " + "\n  ".join(bad)


def test_converge_migration_pattern_covers_hardcoded_variants():
    """收敛迁移自己得能命中存量那几种写法（否则“跑过了但残留”）。"""
    text = (ROOT / "docker/mysql" / "reply_language_persona_migration.sql").read_text(encoding="utf-8")
    for variant in ("，中文回答；", ", 中文回答", "；中文回答、", "。中文回答，"):
        assert variant in text, f"存量写法 {variant} 没被收敛规则覆盖"


def test_role_default_persona_has_no_language():
    src = (ROOT / "backend/core/role_service.py").read_text(encoding="utf-8")
    block = src.split("default_persona = {", 1)[1].split("}", 1)[0]
    assert not [w for w in HARD_CODED if w in block]


# ── 6) 请求头 → ctx.extra 的接线 ─────────────────────────────────

class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def test_ui_locale_read_from_request_headers():
    from backend.modules.mind.services.chat_service import _ui_locale_from_request
    assert _ui_locale_from_request(_FakeRequest({"accept-language": "en-US,en;q=0.9"})) == "en-US"
    assert _ui_locale_from_request(_FakeRequest({})) == ""
    assert _ui_locale_from_request(None) == ""


def test_ui_locale_is_internal_key_not_sent_to_llm():
    """ctx.extra 里的 ui_locale 属服务端内部键：不得进工具入参 schema（护栏 §7 同口径）。"""
    src = (ROOT / "backend/modules/mind/execution/secure_sdk.py").read_text(encoding="utf-8")
    assert 'ctx.extra.get("ui_locale")' in src                       # 只喂提示词组装
    for p in sorted((ROOT / "backend/modules/mind/execution/sdk_tools").glob("*.py")):
        assert "ui_locale" not in p.read_text(encoding="utf-8"), f"{p.name} 把界面语言暴露成了工具入参"


# ── 7) 交付 HTML 文档的 lang ─────────────────────────────────────

def test_html_skeleton_lang_follows_explicit_locale():
    from backend.modules.viz.services.report_service import template_skeleton_from_layout
    layout = {"format": "html", "headings": ["Overview"], "charts": []}
    assert '<html lang="en-US">' in template_skeleton_from_layout(layout, lang="en-US")
    assert '<html lang="zh-CN">' in template_skeleton_from_layout(layout)          # 留空 = 平台默认
    assert '<html lang="zh-CN">' in template_skeleton_from_layout(layout, lang="zz-ZZ")  # 非法值不猜


def test_spec_snapshot_lang_follows_document_content():
    """归档快照没有请求上下文：按文档自身文字判语言，比一律贴 zh-CN 准确。"""
    from backend.modules.viz.services.report_document_service import render_spec_to_html
    zh = render_spec_to_html({"title": "季度销售分析",
                              "sections": [{"kind": "text", "title": "总体表现", "content": "本季增长"}]},
                             title="季度销售分析")
    en = render_spec_to_html({"title": "Quarterly Sales Review",
                              "sections": [{"kind": "text", "title": "Overview", "content": "Growth"}]},
                             title="Quarterly Sales Review")
    assert '<html lang="zh-CN">' in zh
    assert '<html lang="en-US">' in en
    assert '<html lang="en-US">' in render_spec_to_html({"title": "x"}, title="x", lang="en-US")


# ── 8) 尺寸护栏：语言段不得把提示词撑爆（与预算门禁同口径）────────

def test_reply_language_directive_is_compact():
    """本段每回合都发：超过这个尺寸就是在往常驻段堆长句（预算护栏既有红，只准更紧）。"""
    text = i18n.reply_language_directive(*i18n.resolve_reply_language("sales", "en-US"))
    assert len(text) < 260, f"「回复语言」段 {len(text)} 字符，超出锁定尺寸 {text!r}"


# ═════════════════════════════════════════════════════════════
# 双语内容元数据体系 · 回复语言 vs 产出语言解耦（plan §Test Plan 明列）
#
# 上一轮锁的是**回复语言**：用户提问语言优先、界面语言兜底。
# 本轮锁的是**产出语言**：界面语言优先、提问语言兜底 —— **两条链优先级相反**，
# 若被合并成一处，英文界面里问中文会连交付物一起翻成英文（用户看不到本要发布的
# 中文版）。plan §P1/§P4 把两者解耦，这里就是那条护栏。
# ═════════════════════════════════════════════════════════════
def test_reply_and_output_language_are_decoupled_in_priority():
    """用户**提问是中文、界面是英文**：回复用中文（提问优先），产出用英文（界面优先）。"""
    from backend.common import i18n
    # 回复：提问语言赢
    reply_locale, reply_basis = i18n.resolve_reply_language(
        question="今年推送成功率是多少", ui_locale="en-US")
    assert reply_locale == "zh-CN"
    # basis 是人话标签（会原样注入 prompt 让用户看得见），不断言英文常量
    assert "提问" in reply_basis or "question" in reply_basis.lower()
    # 产出：界面语言赢
    out_locale, out_basis = i18n.resolve_output_language(
        "", ui_locale="en-US", question="今年推送成功率是多少")
    assert out_locale == "en-US"
    # basis 是人话标签；关键是它属于"界面"这一档、不是"用户确认"或"提问语言"
    assert "界面" in out_basis or "ui" in out_basis.lower() or "interface" in out_basis.lower()
    # 两条链结论**必须相反** —— 一旦被合并成同一函数就会一致
    assert reply_locale != out_locale, "回复语言与产出语言被合并（回归：plan §P1 解耦失效）"


def test_explicit_output_choice_wins_over_ui_locale():
    """用户显式选「中英都要」→ 界面/提问语言都要让位（plan §P4：默认档只在用户没说时才生效）。"""
    from backend.common import i18n
    loc, basis = i18n.resolve_output_language("双语", ui_locale="zh-CN", question="Coverage report please")
    assert loc == i18n.OUTPUT_BOTH
    # 用户显式选择必须赢过界面（zh-CN）与提问（英文）；basis 应显式说是"用户确认"
    assert "用户" in basis or "user" in basis.lower(), basis


def test_output_language_directive_is_distinct_from_reply_directive():
    """两条指令不能一字冲抵：产出语言指令必须显式讲"交付物"，回复语言指令讲"对话"。"""
    from backend.common import i18n
    reply = i18n.reply_language_directive("en-US", "question")
    output = i18n.output_language_directive("en-US", "ui_locale")
    # 语义必须不同 —— 直接对字符串比会因措辞调整而脆，这里判**关键词有无**
    assert "回复" in reply or "reply" in reply.lower(), reply[:60]
    assert "产出" in output or "交付" in output or "artifact" in output.lower() or "deliverable" in output.lower(), output[:60]


# ── 交付物技能语言确认段（plan §P4：只有 content-writer/report-delivery/screen-builder 三家要问）──
_DELIVERY_SKILLS = ("content-writer", "report-delivery", "screen-builder")
_NON_DELIVERY_SKILLS = ("data-analysis", "knowledge-qa", "metadata-manager",
                        "ontology-builder", "pro-analysis", "arms-observability")


def test_delivery_skills_have_output_language_confirmation_section():
    """3 个交付物技能的 SKILL.md 必须含"产出语言"段：未确认不得动手做交付物。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "backend/modules/mind/config/skills"
    for name in _DELIVERY_SKILLS:
        text = (root / name / "SKILL.md").read_text(encoding="utf-8")
        assert "产出语言" in text, f"{name}: 交付物技能缺『产出语言』确认段"
        # 段里必须显式提到 ask_user 三档（不能只写"考虑语言"含糊过去）
        assert "ask_user" in text, f"{name}: 缺 ask_user 询问动作"
        # 中英各一份 / 双语 之类的第三条路径要可见
        has_both = ("中英" in text) or ("both" in text.lower())
        assert has_both, f"{name}: 缺『中英各一份』这一档"


def test_non_delivery_skills_do_not_have_output_language_section():
    """其余 6 个技能**不得**出现"产出语言"段：只答疑、不交付文件，问了就是打扰（plan §P4 明列"防打扰扩散"）。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "backend/modules/mind/config/skills"
    for name in _NON_DELIVERY_SKILLS:
        text = (root / name / "SKILL.md").read_text(encoding="utf-8")
        assert "产出语言" not in text, f"{name}: 非交付物技能不该有产出语言确认段（防打扰扩散）"


def test_content_writer_dual_output_creates_two_articles():
    """content-writer「中英都要」→ 落地上必须是**两次** create_article（各带不同 lang），
    不能"一份带双语切换"（plan §关键假设 1 + §P4 明列）。"""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1]
            / "backend/modules/mind/config/skills/content-writer/SKILL.md").read_text(encoding="utf-8")
    # 静态护栏：正文里必须明说两条独立条目 + lang 不同 + translation_group 同
    assert ("translation_group" in text) or ("译文关联" in text), \
        "content-writer 未讲清「两条独立条目通过 translation_group 关联」"
    assert ("zh-CN" in text and "en-US" in text), \
        "content-writer 未讲清「中文条目 lang=zh-CN，英文条目 lang=en-US」"
