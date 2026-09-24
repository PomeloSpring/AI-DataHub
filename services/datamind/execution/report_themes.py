"""报告主题令牌注册表 — Chat 报告交付(HTML/Excel)的配色单一真值源.

值与 frontend/src/styles/globals.css 各主题 class 的核心 CSS 变量同源(HSL 三元组)。
改主题配色时必须同步本表, 否则报告与 App 主题漂移(违反"同一生效配置多处消费必须共用")。
默认主题 datafoundry 与 themeStore 初始值保持一致。
"""

# theme_id -> 核心令牌(HSL 三元组字符串, 与 globals.css 一致)
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

DEFAULT_THEME = "datafoundry"
VALID_THEMES = tuple(THEME_TOKENS.keys())


def resolve_theme(theme_id, fallback=None) -> dict:
    """返回规范化主题令牌 dict(含 theme 字段); 未知/空 → fallback → DEFAULT_THEME。"""
    tid = (theme_id or "").strip() if isinstance(theme_id, str) else ""
    if tid not in THEME_TOKENS:
        fb = (fallback or "").strip() if isinstance(fallback, str) else ""
        tid = fb if fb in THEME_TOKENS else DEFAULT_THEME
    return {"theme": tid, **THEME_TOKENS[tid]}


def chart_palette(theme_id, fallback=None) -> list:
    """图表色序(hsl() 字符串列表)。"""
    t = resolve_theme(theme_id, fallback)
    return [f"hsl({c})" for c in t["chart"]]


def theme_css_block(theme_id, fallback=None) -> str:
    """生成可内联进自包含 HTML 报告的 :root 令牌块(hsl 值快照)。"""
    t = resolve_theme(theme_id, fallback)
    charts = "\n".join(f"    --chart-{i + 1}: hsl({c});" for i, c in enumerate(t["chart"]))
    return (
        ":root {\n"
        f"    --bg: hsl({t['background']});\n"
        f"    --fg: hsl({t['foreground']});\n"
        f"    --card: hsl({t['card']});\n"
        f"    --muted: hsl({t['muted']});\n"
        f"    --muted-fg: hsl({t['muted_foreground']});\n"
        f"    --border: hsl({t['border']});\n"
        f"    --primary: hsl({t['primary']});\n"
        f"    --primary-fg: hsl({t['primary_foreground']});\n"
        f"{charts}\n"
        "}"
    )


def theme_tokens_for_prompt(theme_id, fallback=None) -> str:
    """给系统提示词的紧凑主题令牌说明(要求 HTML/Excel 只用这些值)。"""
    t = resolve_theme(theme_id, fallback)
    charts = ", ".join(f"hsl({c})" for c in t["chart"])
    return (
        f"生效主题: {t['theme']} ({'深色' if t['is_dark'] else '浅色'})\n"
        f"  背景 --bg: hsl({t['background']})  前景 --fg: hsl({t['foreground']})\n"
        f"  卡片 --card: hsl({t['card']})  弱化 --muted: hsl({t['muted']})  次要文字 --muted-fg: hsl({t['muted_foreground']})\n"
        f"  边框 --border: hsl({t['border']})  主色 --primary: hsl({t['primary']})  主色前景 --primary-fg: hsl({t['primary_foreground']})\n"
        f"  图表色序(按序取用): {charts}\n"
        f"  可选主题(仅当用户明确指定时切换): {', '.join(VALID_THEMES)}"
    )
