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

import contextvars
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


# ── 2. 元数据本地化（双语 JSON 列的唯一读取口径）─────────────────────────
#: i18n 列的形态：`{"name": {"en-US": "ISV Sign Report"}, "description": {...}}`。
#: 只登记非默认语言的译文；默认语言（中文）永远以原列为事实源，避免同一句话两个中文事实源。
I18N_COLUMN = "i18n"


#: 请求级界面语言（中间件写入，服务层读取）。
#: 为什么走 contextvar 而不是逐个接口传参：消费面本地化点散在六套 router × service
#: （看板/图表/字模/AS-BOT/栏目/场景/内容），把 lang 一参参往下穿会漏且把业务函数签名改脏。
#: 与 trace_id 同一机制（backend/app/main.py 的中间件在 call_next 前 set），因此同样享有
#: "子 task 拷贝上下文"的传播；线程池回跳（run_in_executor）需自行 copy_context。
#: 无人值守路径（定时分析/评测/内部调用）不经过中间件 → 拿到默认语言（中文事实源）。
_locale_var: contextvars.ContextVar[str] = contextvars.ContextVar("adh_ui_locale", default="")


def set_ui_locale(value: object):
    """设置当前上下文的界面语言（中间件专用），返回 token 供 reset。"""
    return _locale_var.set(normalize_locale(value) or DEFAULT_LOCALE)


def reset_ui_locale(token) -> None:
    try:
        _locale_var.reset(token)
    except (ValueError, LookupError):  # noqa: BLE001 — token 跨上下文失效不应把请求带崩
        pass


def current_locale() -> str:
    """当前请求的界面语言（服务层默认取这一个）。无上下文时回默认语言。"""
    return _locale_var.get() or DEFAULT_LOCALE


def resolve_request_locale(request, *, default: str = DEFAULT_LOCALE) -> str:
    """从请求头取界面语言（消费面本地化的唯一入口）。

    没有 request / 没有该头 / 解析不到支持项时返回 `default`：展示层总得选一种语言，
    但调用方不得据此认为"已经双语到位"——回落事实由 localize_display 的 missing 清单负责暴露。
    """
    try:
        header = request.headers.get("accept-language") if request is not None else None
    except Exception:  # noqa: BLE001 — 语言只是展示口径，取不到就走默认，不得因此打断请求
        return default
    return parse_accept_language(header) or default


def localized_text(value: object, blob: object, field: str, lang: str) -> tuple:
    """取一个字段的展示文本。返回 (text, fell_back)。

    - lang == 默认语言：直接用原列（中文事实源），fell_back=False；
    - 其他语言：命中 blob[field][lang] 且非空 → 用它；否则回原列并 fell_back=True。
      fell_back=True 必须被调用方记账到 `i18n_fallback_fields`（no-silent-degradation：
      没译文是事实，不能让用户以为"这就是英文版"）。
    """
    original = str(value or "")
    locale = normalize_locale(lang) or DEFAULT_LOCALE
    if locale == DEFAULT_LOCALE:
        return original, False
    table = blob if isinstance(blob, dict) else {}
    per_field = table.get(field) if isinstance(table.get(field), dict) else {}
    hit = str(per_field.get(locale) or "").strip()
    if hit:
        return hit, False
    return original, True


def localize_row(row: dict, lang: str, fields, *, blob_key: str = I18N_COLUMN) -> tuple:
    """就地把一行元数据按语言本地化（消费面用）。

    返回 (新行, 回落字段清单)。新行里 `fields` 的值变成该语言的文本；原值不丢。
    `blob_key` 指向的行内双语列会被从输出剔掉，不把内部结构塞给消费面响应。
    `lang` 留空表示"用当前请求语言"。
    """
    lang = normalize_locale(lang) or current_locale()
    out = dict(row or {})
    blob = out.pop(blob_key, None)
    missing = []
    for field in fields:
        text, fell_back = localized_text(out.get(field), blob, field, lang)
        out[field] = text
        if fell_back:
            missing.append(field)
    return out, missing


def merge_overlay(base, overlay):
    """config 类双语覆盖层的深合并（i18n["config"][lang] 用）。

    口径（必须写清，因为它是图文不一样的两类字段的交汇点）：
    - 字典递归合并（只覆盖出现的键，其余保持 base 值）；
    - 列表整体替换（不按下标对齐——参数顺序一变就静默贴错标签，比不贴更危）；
    - overlay 为空或非字典时原样返回 base（= 没英文就是中文，调用方负责记回落）。
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for key, value in overlay.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = merge_overlay(out[key], value)
            else:
                out[key] = value
        return out
    if isinstance(overlay, dict) and not isinstance(base, dict):
        return merge_overlay({}, overlay)
    return overlay if overlay is not None else base


def localize_display(row: dict, *, fields=(), overlays=()) -> list:
    """消费面本地化的一把用助手：短文本字段 + JSON 结构覆盖层，返回回落字段清单。

    为什么提到语言层：看板/字模/栏目/图表都有"列上文案 + JSON 里文案"两类，两处各写一套
    必然口径不一致（一边记了回落一边没记）。行为固定三条：
      * 默认语言请求 → 什么都不改（不加 `*_zh`、不加标记），存量响应形状零变化；
      * 命中译文 → 改写 `row[field]`，原值留 `row["<field>_zh"]`（编辑页回填中文事实源用，
        堵住"英文界面保存把英文写进中文列"这条误写路径）；
      * 未命中 → 不改写并把字段名记入返回清单，由调用方落 `i18n_fallback_fields`。
    """
    lang = current_locale()
    if lang == DEFAULT_LOCALE:
        return []
    blob = row.get(I18N_COLUMN)
    blob = blob if isinstance(blob, dict) else {}
    missing = []
    for field in fields:
        text, fell_back = localized_text(row.get(field), blob, field, lang)
        if fell_back:
            missing.append(field)
            continue
        row[f"{field}_zh"] = row.get(field)
        row[field] = text
    for field in overlays:
        entry = blob.get(field)
        overlay = entry.get(lang) if isinstance(entry, dict) else None
        base = row.get(field)
        if not isinstance(overlay, dict) or not isinstance(base, (dict, list)):
            missing.append(field)
            continue
        row[f"{field}_zh"] = base
        row[field] = merge_overlay(base, overlay)
    return missing


# ── 3. 产出语言（交付物：文章 / 报表 / 大屏）────────────────────────────
OUTPUT_BOTH = "both"
#: 用户口径 → 受控令牌（技能拿到的是自然语言选项，这里归一，不留第二套映射）
_OUTPUT_ALIASES = {
    "zh": "zh-CN", "zh-cn": "zh-CN",
    "中文": "zh-CN", "简体中文": "zh-CN", "仅中文": "zh-CN",
    "en": "en-US", "en-us": "en-US",
    "英文": "en-US", "英语": "en-US", "english": "en-US",
    "both": OUTPUT_BOTH, "双语": OUTPUT_BOTH,
    "两者都要": OUTPUT_BOTH, "两个语言都要": OUTPUT_BOTH,
    "中英": OUTPUT_BOTH, "中英都要": OUTPUT_BOTH, "都要": OUTPUT_BOTH,
}


def normalize_output_language(value: object) -> str:
    """产出语言归一：返回 "zh-CN" | "en-US" | "both" | ""（空=未指定，由调用方兜底）。"""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in _OUTPUT_ALIASES:
        return _OUTPUT_ALIASES[raw]
    for token, locale in _OUTPUT_ALIASES.items():
        if len(token) > 2 and token in raw:
            return locale
    return normalize_locale(value)


def resolve_output_language(asked: str = "", *, ui_locale: str = "",
                            question: str = "") -> tuple:
    """交付物产出语言：用户在技能里选过的优先，否则按界面语言，再否则按提问语言。

    与回复语言的优先级相反（回复语言以提问语言优先）：交付物是要长期留在门户上的
    资产，用户显式选了就要那一份，不能被"这轮用中文问了句顺手的事"改掉。
    """
    picked = normalize_output_language(asked)
    if picked:
        return picked, "用户本轮确认的产出语言"
    ui = normalize_locale(ui_locale)
    if ui:
        return ui, "界面语言（用户未指定产出语言）"
    detected = detect_question_language(question)
    if detected:
        return detected, "提问语言（无界面语言上下文）"
    return DEFAULT_LOCALE, "平台默认语言（无人值守产出，如定时报表）"


def t_pair(zh: str, en: str) -> str:
    """用户可见文案的双语分岔：跟随 `current_locale()`（contextvar，由 Accept-Language 中间件写入）。

    与前端 i18next 语言包不同层的**极简双语**：只用于后端直接吐给浏览器的一行式回执
    （如"草稿已保存到内容中心…"这类），不引入新语言包文件、不做插值。
    默认语言（zh-CN）走 `zh`，其它一律走 `en`；带插值请由调用方在传入前 f-string 组好。
    """
    return zh if current_locale() == DEFAULT_LOCALE else en


def output_language_directive(locale: str, basis: str) -> str:
    """交付物产出语言的提示词段（只在产出交付物的技能里注入，不进常驻段：常驻预算已超门禁）。"""
    if locale == OUTPUT_BOTH:
        return (
            "## 产出语言\n"
            "本次交付物要中英各出一份（两份独立资产，不是同一份里做语言切换）："
            "中文一份、英文一份，英文份的术语按平台统一译法，"
            "数字/出处/日期与中文份逐值一致。依据：用户确认（双语）。"
        )
    locale = locale if is_supported_locale(locale) else DEFAULT_LOCALE
    instruction = REPLY_LANGUAGE_INSTRUCTION.get(locale) or REPLY_LANGUAGE_INSTRUCTION[DEFAULT_LOCALE]
    return (
        "## 产出语言\n"
        f"本次交付物（文章/报表/大屏）一律用{instruction.replace('回答', '撰写')}写；"
        f"HTML 文档的 `<html lang>` 用 {locale}。"
        "业务名、数据行内容、指标口径仍保持源语言不译（英文份里首次出现时括注中文原名一次）。"
        f"依据：{basis}。"
    )


# ── 4. 统一译法表（存量元数据批量起草英文时复用，避免同词多译）──────────────
#: 中文术语 → 平台统一英文。批量翻译工具按此表硬替换，用例锁定关键条目。
#: 收这些词的理由：它们在看板名/图表标题/说明卡里高频出现，且一旦各页译法不同，
#: 英文界面对照同一指标就会出现三个名字（用户无法确认是不是同一件事）。
METADATA_GLOSSARY = {
    "站点": "Site",
    "推送": "Push",
    "推送成功率": "Push Success Rate",
    "获取": "Fetch",
    "获取次数": "Fetch Count",
    "绑定": "Bound",
    "未绑定": "Unbound",
    "绑定率": "Binding Rate",
    "覆盖率": "Coverage",
    "合作方": "Partner",
    "外部合作方": "External Partner",
    "应用": "App",
    "接入": "Integration",
    "榜": "Ranking",
    "明细": "Details",
    "趋势": "Trend",
    "概览": "Overview",
    "成功率": "Success Rate",
    "活跃": "Active",
    "设备": "Device",
    "扫描仪": "Scanner",
    "工作台": "Workbench",
    "看板": "Dashboard",
    "大屏": "Screen",
    "报表": "Report",
    "内容中心": "Content Center",
    "字模库": "Style Library",
}
