"""报告主题令牌注册表 — Chat/定时任务报告交付(HTML/Excel)的配色与图表形状基线.

唯一真值源是 UI 字模库 theme_pack（``adh_vis_components``，code=``tp_<theme>``）：看板与
Chat 图表都按它渲染（``dashboardDesign.ts: themePackFor``），报告再拿一份代码常量就是
第二套真值源 —— 历史缺陷正是"改了字模库报告不变、报告与看板两套配色"。
``THEME_TOKENS`` 降级为**字模库读不到时的内置基线**，且回落必须在提示词里显式标注
（no-silent-degradation §1/§4），不得掩成"已按主题生成"。
默认主题 light 与 themeStore 初始值保持一致。
"""

import json
import logging

logger = logging.getLogger(__name__)

# theme_id -> 内置基线令牌(HSL 三元组字符串, 与 globals.css 同源; 仅作字模库缺失时兜底)
THEME_TOKENS: dict[str, dict] = {
    "dark": {
        "is_dark": True,
        "background": "222 47% 7%", "foreground": "210 40% 98%",
        "card": "222 47% 11%", "muted": "217 33% 17%", "muted_foreground": "215 20% 65%",
        "border": "217 33% 17%", "primary": "217 91% 60%", "primary_foreground": "222 47% 7%",
        "ring": "217 91% 60%",
        "chart": ["217 91% 60%", "160 84% 39%", "45 93% 47%", "0 84% 60%", "280 84% 60%",
                  "190 90% 50%", "340 82% 52%", "120 60% 40%", "260 70% 55%", "30 90% 55%"],
    },
    "light": {
        "is_dark": False,
        "background": "0 0% 100%", "foreground": "222 47% 7%",
        "card": "0 0% 100%", "muted": "210 40% 96%", "muted_foreground": "215 20% 35%",
        "border": "214 32% 91%", "primary": "217 91% 60%", "primary_foreground": "0 0% 100%",
        "ring": "217 91% 60%",
        "chart": ["217 91% 60%", "160 84% 39%", "45 93% 47%", "0 84% 60%", "280 84% 60%",
                  "190 90% 50%", "340 82% 52%", "120 60% 40%", "260 70% 55%", "30 90% 55%"],
    },
    "tech": {
        "is_dark": True,
        "background": "220 30% 5%", "foreground": "190 30% 92%",
        "card": "220 25% 9%", "muted": "220 20% 14%", "muted_foreground": "200 15% 55%",
        "border": "210 20% 15%", "primary": "190 100% 50%", "primary_foreground": "220 30% 5%",
        "ring": "190 100% 50%",
        "chart": ["190 100% 50%", "160 80% 45%", "280 80% 60%", "50 100% 55%", "340 80% 55%",
                  "120 60% 45%", "240 70% 60%", "30 90% 55%", "210 80% 55%", "170 90% 40%"],
    },
    "finance": {
        "is_dark": True,
        "background": "222 35% 6%", "foreground": "40 20% 90%",
        "card": "222 30% 10%", "muted": "222 20% 14%", "muted_foreground": "220 12% 55%",
        "border": "220 18% 14%", "primary": "38 92% 50%", "primary_foreground": "222 35% 6%",
        "ring": "38 92% 50%",
        "chart": ["38 92% 50%", "150 60% 42%", "210 80% 55%", "0 70% 50%", "280 60% 55%",
                  "190 70% 48%", "340 65% 52%", "120 50% 40%", "260 55% 55%", "25 85% 52%"],
    },
    "bento": {
        "is_dark": False,
        "background": "220 20% 97%", "foreground": "222 30% 12%",
        "card": "0 0% 100%", "muted": "220 16% 93%", "muted_foreground": "220 10% 45%",
        "border": "220 16% 90%", "primary": "250 80% 60%", "primary_foreground": "0 0% 100%",
        "ring": "250 80% 60%",
        "chart": ["250 80% 60%", "170 70% 45%", "340 70% 58%", "35 90% 55%", "200 80% 50%",
                  "280 60% 55%", "150 55% 42%", "10 80% 58%", "220 70% 50%", "310 55% 50%"],
    },
    "glass": {
        "is_dark": True,
        "background": "230 25% 6%", "foreground": "210 30% 92%",
        "card": "230 20% 12%", "muted": "230 15% 15%", "muted_foreground": "220 15% 55%",
        "border": "230 15% 20%", "primary": "200 90% 55%", "primary_foreground": "230 25% 6%",
        "ring": "200 90% 55%",
        "chart": ["200 90% 55%", "160 75% 48%", "280 70% 60%", "40 90% 55%", "340 75% 58%",
                  "120 55% 45%", "260 65% 58%", "20 85% 55%", "180 80% 48%", "300 55% 52%"],
    },
    "ainative": {
        "is_dark": True,
        "background": "230 30% 4%", "foreground": "180 20% 92%",
        "card": "230 25% 8%", "muted": "230 20% 12%", "muted_foreground": "200 15% 50%",
        "border": "230 20% 15%", "primary": "180 100% 50%", "primary_foreground": "230 30% 4%",
        "ring": "180 100% 50%",
        "chart": ["180 100% 50%", "270 80% 60%", "320 80% 55%", "50 100% 50%", "200 90% 55%",
                  "140 70% 45%", "290 70% 55%", "30 90% 55%", "220 80% 60%", "160 85% 40%"],
    },
    "medical": {
        "is_dark": False,
        "background": "185 30% 97%", "foreground": "185 40% 15%",
        "card": "0 0% 100%", "muted": "185 20% 93%", "muted_foreground": "185 10% 45%",
        "border": "185 20% 88%", "primary": "186 80% 40%", "primary_foreground": "0 0% 100%",
        "ring": "186 80% 40%",
        "chart": ["186 80% 40%", "160 60% 45%", "210 70% 50%", "340 65% 55%", "38 85% 55%",
                  "280 55% 55%", "140 50% 42%", "200 75% 50%", "320 60% 52%", "260 65% 58%"],
    },
    "datafoundry": {
        "is_dark": False,
        "background": "0 0% 98%", "foreground": "0 0% 5%",
        "card": "0 0% 100%", "muted": "0 0% 96%", "muted_foreground": "0 0% 40%",
        "border": "0 0% 93%", "primary": "0 0% 5%", "primary_foreground": "0 0% 100%",
        "ring": "0 0% 5%",
        "chart": ["0 0% 5%", "0 0% 40%", "210 18% 48%", "260 14% 52%", "178 20% 42%",
                  "35 24% 48%", "155 18% 46%", "205 14% 62%", "2 22% 54%", "248 10% 66%"],
    },
}
DEFAULT_THEME = "light"
VALID_THEMES = tuple(THEME_TOKENS.keys())

#: 字模库主题包编码前缀（与 frontend/src/lib/dashboardDesign.ts 的 themePackFor 同一约定）
_PACK_PREFIX = "tp_"
#: 带给模型的图表形状旋钮（与 dashboardDesign 的 VISUAL_KEYS 同口径的子集）
_SHAPE_KEYS = ("gradient", "borderRadius", "smooth", "lineWidth", "showLabel",
               "axisLabelSize", "barFade", "barCap", "gridColor", "areaOpacity")


def _css(value) -> str:
    """颜色值归一：字模库存 hex/rgb 原样可用，内置表存 HSL 三元组需补 hsl()。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith(("#", "rgb", "hsl", "var(")) else f"hsl({text})"


def _cycle10(colors: list) -> list:
    """色序循环补齐 10 位：报告 CSS 快照按 --chart-1..10 引用，位数不足会留空色位。"""
    if not colors:
        return []
    return [colors[i % len(colors)] for i in range(10)]


def _is_enabled(comp: dict) -> bool:
    """禁用态判定：列缺失（未跑迁移的存量库）按"启用"处理，不得把在用主题包误判成停用。"""
    val = comp.get("is_enabled")
    return True if val is None else int(val or 0) != 0


def _pack_state(theme_id: str) -> tuple:
    """按 ``tp_<theme>`` 读字模库主题包，返回 ``(style_config 或 None, 不可用原因)``。

    字模库是看板与 Chat 图表的事实源（ui-vis-library-style.md §1）：报告必须与它同源，
    否则管理员改字模库而报告不变、报告与看板两套配色（本模块此前就是第二套真值源）。
    原因必须分档（库不可用 / 没有该包 / 已下架 / **已禁用**）：禁用是管理员显式停用，
    写成一句"读不到"就查不出根因，也看不出"这套主题已被停用、报告还在给它出配色"。
    """
    code = f"{_PACK_PREFIX}{(theme_id or '').strip()}"
    try:
        from backend.modules.viz.services.vis_library_service import vis_library_service
        comp = vis_library_service.get_component_by_code(code)
    except Exception as exc:      # 库不可用/表缺失：走内置兜底，但降级事实要记日志
        logger.warning("字模库主题包读取失败 code=%s: %s", code, exc)
        return None, "字模库不可用"
    if not comp:
        return None, f"字模库没有主题包 {code}"
    if int(comp.get("is_active") or 0) == 0:
        return None, f"主题包 {code} 已下架"
    if not _is_enabled(comp):
        return None, (f"主题包「{comp.get('name') or code}」已在 UI 字模库禁用"
                      f"（{code}），该主题不可使用；如需恢复请在「系统配置 → UI 字模库」重新启用")
    sc = comp.get("style_config")
    if isinstance(sc, str):
        try:
            sc = json.loads(sc or "{}")
        except (ValueError, TypeError):
            return None
    if not isinstance(sc, dict) or not (sc.get("palette") or []):
        return None, f"主题包 {code} 的 style_config 缺少 palette"
    return sc, ""


def _tokens_from_pack(theme_id: str, sc: dict) -> dict:
    """字模库主题包 → 报告令牌（CSS 变量 + 色序 + 图表形状基线 + 卡片几何）。"""
    palette = _cycle10([_css(c) for c in (sc.get("palette") or []) if str(c).strip()])
    card = sc.get("card") or {} if isinstance(sc.get("card"), dict) else {}
    charts = sc.get("charts") or {} if isinstance(sc.get("charts"), dict) else {}
    default_shape = charts.get("default") or {} if isinstance(charts.get("default"), dict) else {}
    deco = sc.get("decoration") or {} if isinstance(sc.get("decoration"), dict) else {}
    bg = _css((sc.get("background") or {}).get("backgroundColor") if isinstance(sc.get("background"), dict)
              else None) or _css(sc.get("viewBg"))
    mode = str(sc.get("mode") or "").strip().lower()
    return {
        "theme": theme_id, "source": "vis_library", "source_name": f"{_PACK_PREFIX}{theme_id}",
        "is_dark": mode == "dark",
        "background": bg, "foreground": _css(sc.get("textColor")),
        "card": _css(card.get("cardBg") or sc.get("popoverBg")),
        "muted": _css(sc.get("muted") or sc.get("gridColor") or bg),
        "muted_foreground": _css(sc.get("subTextColor") or card.get("labelColor")),
        "border": _css(sc.get("gridColor")),
        "primary": palette[0] if palette else "",
        # 主题包不单独登记"主色上的文字色"：深色底主色通常偏亮 → 用背景色作前景；浅色底用白字
        "primary_foreground": bg if mode == "dark" else _css("#ffffff"),
        "ring": palette[0] if palette else "",
        "chart": palette,
        "shape": {k: default_shape[k] for k in _SHAPE_KEYS if k in default_shape},
        "card_radius": card.get("cardRadius"), "card_shadow": card.get("glow"),
        "title_color": _css(deco.get("titleColor")), "value_color": _css(card.get("valueColor")),
    }


def resolve_theme(theme_id, fallback=None) -> dict:
    """主题令牌解析：**字模库 ``tp_<theme>`` 优先**，读不到才回落内置基线并标注来源。

    回落不是静默兜底：结果带 ``source="builtin_fallback"`` 与 ``reason``，提示词会把降级事实
    写给模型与用户（§1/§4）——"报告配色与看板不一致"必须可见，否则下次改字模库照样没人发现。
    未知/空主题 → ``fallback`` → ``DEFAULT_THEME``（与 themeStore 初始值一致）。
    """
    tid = (theme_id or "").strip() if isinstance(theme_id, str) else ""
    note = ""
    # 先认字模库：主题包可以只存在于库里（如 tp_as），拿内置表当合法集会把它误判成未知主题
    sc, _why = _pack_state(tid) if tid else (None, "未指定主题")
    if sc:
        return _tokens_from_pack(tid, sc)
    if tid and tid not in THEME_TOKENS:
        fb = (fallback or "").strip() if isinstance(fallback, str) else ""
        if fb in THEME_TOKENS or _pack_state(fb)[0]:
            note, tid = f"主题「{tid}」无定义，已改用 {fb}", fb
        else:
            note, tid = f"主题「{tid}」无定义，已改用默认 {DEFAULT_THEME}", DEFAULT_THEME
    if not tid:
        tid = DEFAULT_THEME
    sc, why = _pack_state(tid)
    if sc:
        tokens = _tokens_from_pack(tid, sc)
        if note:
            tokens["note"] = note
        return tokens
    builtin = THEME_TOKENS[tid]
    out = {"theme": tid, "source": "builtin_fallback", "source_name": "", "is_dark": bool(builtin["is_dark"]),
           "reason": f"字模库主题包 {_PACK_PREFIX}{tid} 未生效（{why or '字模库不可用'}），"
                     "本次取内置基线令牌，报告配色可能与看板/图表实际渲染不一致",
           "shape": {}, "card_radius": None, "card_shadow": "", "title_color": "", "value_color": ""}
    for key in ("background", "foreground", "card", "muted", "muted_foreground", "border",
                "primary", "primary_foreground", "ring"):
        out[key] = _css(builtin.get(key))
    out["chart"] = _cycle10([_css(c) for c in builtin["chart"]])
    if note:
        out["note"] = note
    return out


def available_themes() -> list:
    """可切换主题清单：字模库**在用**主题包（去 tp_ 前缀）∪ 内置基线键，字模库优先排序。

    被禁用的主题包连同其主题 id 一起摘掉：模型不能把"管理员已停用"的主题当成可切换选项
    推给用户，否则等于从另一条通道绕过了禁用（内置基线表里有同名主题也不能补回来）。
    """
    codes = list(THEME_TOKENS.keys())
    try:
        from backend.modules.viz.services.vis_library_service import vis_library_service
        # 取全量再自判：禁用行要单独识别出来把主题摘掉，下架行维持既有口径（仍可当内置基线主题）
        rows = vis_library_service.list_components(
            "theme_pack", include_inactive=True, include_disabled=True) or []
        usable, disabled = [], set()
        for c in rows:
            code = str(c.get("code") or "")
            if not code.startswith(_PACK_PREFIX) or int(c.get("is_active") or 0) == 0:
                continue
            theme = code[len(_PACK_PREFIX):]
            if _is_enabled(c):
                usable.append(theme)
            else:
                disabled.add(theme)
        codes = usable + [t for t in codes if t not in usable and t not in disabled]
    except Exception as exc:
        logger.warning("字模库主题清单读取失败，回落内置清单: %s", exc)
    seen, out = set(), []
    for t in codes:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def chart_palette(theme_id, fallback=None) -> list:
    """图表色序（可直接落 CSS 的颜色值；来源=字模库 palette，循环补齐 10 位）。"""
    return list(resolve_theme(theme_id, fallback)["chart"])


def theme_css_block(theme_id, fallback=None) -> str:
    """生成可内联进自包含 HTML 报告的 ``:root`` 令牌块（值快照，离线一致）。"""
    t = resolve_theme(theme_id, fallback)
    lines = [":root {",
             f"    --bg: {t['background']};",
             f"    --fg: {t['foreground']};",
             f"    --card: {t['card']};",
             f"    --muted: {t['muted']};",
             f"    --muted-fg: {t['muted_foreground']};",
             f"    --border: {t['border']};",
             f"    --primary: {t['primary']};",
             f"    --primary-fg: {t['primary_foreground']};"]
    lines += [f"    --chart-{i + 1}: {c};" for i, c in enumerate(t["chart"])]
    if t.get("card_radius") is not None:
        lines.append(f"    --card-radius: {t['card_radius']}px;")
    if t.get("card_shadow"):
        lines.append(f"    --card-shadow: {t['card_shadow']};")
    lines.append("}")
    return "\n".join(lines)


def theme_tokens_for_prompt(theme_id, fallback=None) -> str:
    """给系统提示词的主题说明：令牌来源、CSS 变量、色序、图表形状基线、可选主题清单。

    来源必须写在第一行：报告与看板是否同源，看这一句就能判定（历史缺陷：报告另用一套
    代码常量，改字模库没反应，排查时没人怀疑到这里）。
    """
    t = resolve_theme(theme_id, fallback)
    from_lib = t.get("source") == "vis_library"
    source = (f"UI 字模库 {t.get('source_name')}（与看板/图表同一套配色）" if from_lib
              else f"内置基线（{t.get('reason')}）")
    lines = [f"生效主题: {t['theme']} ({'深色' if t['is_dark'] else '浅色'})｜令牌来源: {source}"]
    if t.get("note"):
        lines.append(f"  主题解析: {t['note']}")
    lines += [
        f"  背景 --bg: {t['background']}  前景 --fg: {t['foreground']}",
        f"  卡片 --card: {t['card']}  弱化 --muted: {t['muted']}  次要文字 --muted-fg: {t['muted_foreground']}",
        f"  边框 --border: {t['border']}  主色 --primary: {t['primary']}  主色前景 --primary-fg: {t['primary_foreground']}",
        f"  图表色序(按序取用): {', '.join(t['chart'])}",
    ]
    if t.get("card_radius") is not None or t.get("card_shadow"):
        lines.append(f"  卡片几何: 圆角 {t.get('card_radius')}px  阴影 {t.get('card_shadow') or '无'}")
    if t.get("shape"):
        lines.append("  图表形状基线(内联 SVG 与图表卡必须照此，不得自造): "
                     + json.dumps(t["shape"], ensure_ascii=False))
    lines.append(f"  可选主题(仅当用户明确点名时切换): {', '.join(available_themes())}")
    return "\n".join(lines)
