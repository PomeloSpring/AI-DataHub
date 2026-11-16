"""平台语言口径（与前端 i18n 同源，后端唯一判定处）。

为什么需要这一层：前端 2026-11 起了多语言框架（zh-CN / en-US，存 localStorage），
但语言从没传到后端 —— 系统提示词与技能正文里到处硬编码「中文回答」，用户切到
English 后界面是英文、LLM 仍回中文，属于"配置面有、消费端没有"的空承诺。

口径（用户 2026-11-16 拍板）：**提问语言优先，界面语言兜底** ——
本轮回复语言 = 用户本轮提问使用的语言；提问语言判不出来（空、纯数字、纯符号、
单个专有名词）时退回界面语言（Accept-Language）；两者都没有（无人值守：定时分析、
评测、内部调用）退回 DEFAULT_LOCALE。用户在对话里显式要求换语言时以用户指令为准
（由提示词声明，不在此处判定）。

边界：
- 语言只决定**叙述文字**的语言。业务名、指标/维度字典名、合作方名、数据行内容一律
  保持源语言，不得自行翻译（译了就和字典/看板对不上）。
- 不猜浏览器语言：Accept-Language 解析不到支持项就是"没有界面语言"，走兜底，
  不把 "fr-FR" 之类当成英文（宁可少判，不要错判）。
- 支持清单必须与 `frontend/src/i18n/index.ts` 的 LOCALES 一致（前端是唯一录入处，
  后端只消费）。新增语言改两处，本模块的单测会挡住不一致。
"""
from __future__ import annotations

import re

SUPPORTED_LOCALES: tuple[str, ...] = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"

#: 展示名（供错误文案与报表 lang 属性等场景复用，不再各处自写）
LOCALE_LABELS: dict[str, str] = {"zh-CN": "简体中文", "en-US": "English"}

#: 回复语言的权威指令文案（提示词里只在这里写一遍，技能正文不得另立口径）
REPLY_LANGUAGE_INSTRUCTION: dict[str, str] = {
    "zh-CN": "请用**简体中文**回答",
    "en-US": "Respond in **English**（完整句子，不是逐条翻译的中文短语）",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def is_supported_locale(value: object) -> bool:
    return isinstance(value, str) and value in SUPPORTED_LOCALES


def normalize_locale(value: object) -> str:
    """把任意语言标识归一到受支持清单；不认的一律返回空串（不猜、不就近映射）。

    允许 "zh" / "zh_CN" / "en" / "EN-us" 这类简写落到对应语言，
    除此之外（如 fr-FR、klingon）视为"没有界面语言"。
    """
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in ("zh", "zh-cn", "zh-hans", "zh-sg"):
        return "zh-CN"
    if lowered in ("zh-tw", "zh-hk", "zh-hant"):
        # 繁体仍走中文叙述口径（平台没有单独繁体包），不把它当"不支持"丢掉
        return "zh-CN"
    if lowered in ("en", "en-us", "en-gb", "en-au", "en-ca"):
        return "en-US"
    return ""


def parse_accept_language(header: str | None) -> str:
    """解析 Accept-Language，取第一个受支持项（尊重 q 值优先级）。

    返回空串 = 没有可用的界面语言（缺失、全不支持、格式异常），由调用方走兜底。
    """
    if not header:
        return ""
    items: list[tuple[float, int, str]] = []
    for order, part in enumerate(str(header).split(",")):
        token, _, q_raw = part.strip().partition(";")
        q = 1.0
        if q_raw:
            match = re.search(r"q\s*=\s*([0-9.]+)", q_raw)
            try:
                q = float(match.group(1)) if match else 1.0
            except ValueError:
                q = 0.0
        locale = normalize_locale(token)
        if locale:
            # 同 q 值按出现顺序取胜（稳定排序，不受 dict 顺序影响）
            items.append((q, -order, locale))
    if not items:
        return ""
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return items[0][2]


def detect_question_language(text: str) -> str:
    """判断"这段提问是什么语言"；判不出来返回空串（交给界面语言兜底）。

    判据保守：
    - 出现汉字即中文（中英夹杂的技术提问绝大多数仍期望中文回答，且汉字是最强信号）；
    - 拉丁词数 ≥ 2 才算英文（单个词如 "CPU"、"ES"、表名不构成语言信号）；
    - 其余（空、纯数字、纯符号、单个标识符）返回 ""。
    """
    body = str(text or "").strip()
    if not body:
        return ""
    if _CJK_RE.search(body):
        return "zh-CN"
    if len(_LATIN_WORD_RE.findall(body)) >= 2:
        return "en-US"
    return ""


def resolve_reply_language(question: str = "", ui_locale: str = "") -> tuple[str, str]:
    """本轮回复语言与其依据（唯一判定入口，返回给提示词与审计复用）。

    Returns:
        (locale, basis)：basis 取值为可读来源说明，写进提示词让模型知道"为什么是这个语言"，
        也让评测/日志能分辨三条路径（与透明回退口径一致）。
    """
    detected = detect_question_language(question)
    if detected:
        return detected, "用户本轮提问的语言"
    ui = normalize_locale(ui_locale)
    if ui:
        return ui, "界面语言（本轮提问不足以判断语言）"
    return DEFAULT_LOCALE, "平台默认语言（无提问语言与界面语言上下文，如定时分析/评测）"


def reply_language_directive(locale: str, basis: str) -> str:
    """提示词「回复语言」段：短、常驻、可被用户显式指令覆盖。

    字数受 tests/test_prompt_compose_budget.py 门禁约束，别在这里加长句。
    """
    instruction = REPLY_LANGUAGE_INSTRUCTION.get(locale) or REPLY_LANGUAGE_INSTRUCTION[DEFAULT_LOCALE]
    return (
        "## 回复语言\n"
        f"本轮{instruction}（依据：{basis}）。"
        "用户在本轮或历史中明确要求换语言时，以用户的要求为准。"
        "业务名、指标/维度字典名、合作方名与数据行内容**保持源语言不翻译**；"
        "SQL、代码、字段名、专有名词与技术缩写照原样输出。"
        f"交付 HTML 文档/报表时，文档语言与 `<html lang>` 都用 {locale}。"
    )


#: 回合提示词末尾的那一句（与上面同源判定，只是更短）。
#: 为什么要两处：实测（会话 1783069459277）证明"专项指令只写进系统提示词"会被后面的
#: 常驻段压过去；把作业要求复述到**本轮问题之后**才稳定生效。两处都调本模块同一判定，不会漂移。
_TURN_LINE = {
    "zh-CN": "（本轮用简体中文回复；业务名与数据内容保持原文，不翻译。）",
    "en-US": "(Reply this turn in English; keep business names and data values as-is, untranslated.)",
}


def turn_language_line(locale: str, basis: str) -> str:
    """拼在回合提示词末尾的一句语言作业要求（问题之后，比系统提示词更靠近输出）。"""
    text = _TURN_LINE.get(locale) or _TURN_LINE[DEFAULT_LOCALE]
    return f"{text} 依据：{basis}；用户明确要求换语言时以用户要求为准。"
