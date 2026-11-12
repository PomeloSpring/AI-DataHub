"""报告主题令牌回归：唯一真值源是 UI 字模库主题包，回落必须显式。

口径：
* 报告配色/形状与看板同源（同一份 tp_<theme> 定义），否则"改了字模库报告不变"；
* 字模库读不到才用内置基线，且降级事实要写进提示词（no-silent-degradation §1/§4）；
* 只存在于字模库的主题（如 tp_as）不能被内置表判成"未知主题"；
* 无人值守没传主题时也要注入令牌（旧写法整段不注 → 模型自造颜色）。
"""
import pytest

from backend.modules.mind.execution import report_themes as rt
from backend.modules.mind.execution.prompt_composer import compose_system_prompt
from backend.modules.viz.services.vis_library_service import vis_library_service
from backend.modules.viz.services import report_service as reports

ALL = {"dark", "light", "tech", "finance", "bento", "glass", "ainative", "medical", "datafoundry"}

_LIGHT_PACK = {
    "mode": "light", "viewBg": "#ffffff", "popoverBg": "#ffffff",
    "textColor": "#262626", "subTextColor": "#3A4149", "gridColor": "#E9ECF0",
    "palette": ["#0037CB", "#38AD2A", "#E56F22", "#08997E", "#7C4DFF", "#D81B60",
                "#00A3C4", "#8A939F"],
    "background": {"backgroundColor": "#F7F8FA"},
    "card": {"cardBg": "#ffffff", "cardRadius": 12, "glow": "0 4px 8px rgba(11,106,229,.08)",
             "labelColor": "#3A4149", "valueColor": "#262626"},
    "decoration": {"titleColor": "#262626"},
    "charts": {"default": {"gradient": False, "borderRadius": 0, "smooth": False,
                           "lineWidth": 2, "showLabel": False, "axisLabelSize": 12,
                           "barFade": True, "barCap": "#0037CB", "gridColor": "#E9ECF0"}},
}


@pytest.fixture
def packs(monkeypatch):
    """字模库主题包桩：库里有 tp_light/tp_as；其它 code 一律"未读到"。"""
    state = {"items": {"tp_light": _LIGHT_PACK, "tp_as": {**_LIGHT_PACK, "mode": "light"}},
             "error": None}

    def get_component_by_code(code):
        if state["error"]:
            raise state["error"]
        sc = state["items"].get(code)
        return None if sc is None else {"code": code, "is_active": 1, "style_config": sc}

    def list_components(category="", include_inactive=False, include_disabled=False):
        if state["error"]:
            raise state["error"]
        return [{"code": c, "is_active": 1, "is_enabled": 1} for c in state["items"]]

    monkeypatch.setattr(vis_library_service, "get_component_by_code", get_component_by_code)
    monkeypatch.setattr(vis_library_service, "list_components", list_components)
    return state


def test_builtin_registry_still_covers_nine_themes():
    """内置基线表仍是九套（字模库缺失时的兜底面），默认主题与 themeStore 初值一致。"""
    assert set(rt.THEME_TOKENS.keys()) == ALL
    assert rt.DEFAULT_THEME == "light"


def test_tokens_come_from_vis_library(packs):
    t = rt.resolve_theme("light")
    assert t["source"] == "vis_library" and t["source_name"] == "tp_light"
    assert t["primary"] == "#0037CB"                  # 字模库 AS 主色，不是内置 hsl
    assert t["background"] == "#F7F8FA"
    assert t["muted"] == "#E9ECF0"                    # 弱化底取 gridColor，不与卡片同色
    assert t["chart"][0] == "#0037CB" and len(t["chart"]) == 10   # 8 位色序循环补齐 10
    assert t["shape"]["borderRadius"] == 0 and t["shape"]["barCap"] == "#0037CB"
    assert t["card_radius"] == 12 and t["card_shadow"].startswith("0 4px")


def test_css_block_uses_library_values(packs):
    css = rt.theme_css_block("light")
    for token in ("--bg", "--fg", "--primary", "--card", "--border", "--chart-1", "--chart-10",
                  "--card-radius", "--card-shadow"):
        assert token + ":" in css
    assert "--primary: #0037CB;" in css


def test_library_only_theme_is_not_treated_as_unknown(packs):
    """tp_as 只存在于字模库：拿内置表当合法集会把它误判成未知主题并换成 light。"""
    assert "as" not in rt.THEME_TOKENS
    t = rt.resolve_theme("as")
    assert t["theme"] == "as" and t["source"] == "vis_library"


def test_missing_pack_falls_back_loudly(packs):
    del packs["items"]["tp_light"]
    t = rt.resolve_theme("light")
    assert t["source"] == "builtin_fallback"
    assert "字模库" in t["reason"] and "不一致" in t["reason"]
    assert t["primary"].startswith("hsl(")            # 内置表值归一成可用 CSS 颜色
    prompt = rt.theme_tokens_for_prompt("light")
    assert "内置基线" in prompt and "字模库" in prompt  # 降级事实进提示词，不掩成"已按主题生成"


def test_library_read_error_does_not_break_generation(packs):
    """字模库读取异常（库挂了）不能让报告生成主链路炸掉，但必须落到内置基线并说明。"""
    packs["error"] = RuntimeError("db down")
    t = rt.resolve_theme("light")
    assert t["source"] == "builtin_fallback" and t["reason"]


def test_unknown_theme_notes_the_switch(packs):
    t = rt.resolve_theme("bogus")
    assert t["theme"] == "light" and "无定义" in t["note"]
    assert rt.resolve_theme("bogus", "tech")["theme"] == "tech"
    assert rt.resolve_theme(None)["theme"] == "light"


def test_available_themes_lists_library_first(packs):
    """清单：字模库在用主题包先给（它们才是能真同源的），内置独有项只作补齐。"""
    themes = rt.available_themes()
    assert themes[:2] == ["light", "as"]        # 桩里在用的两个包，按库序
    assert "tech" in themes[2:] and "dark" in themes[2:]


def test_prompt_reports_the_token_source(packs):
    prompt = rt.theme_tokens_for_prompt("light")
    assert prompt.startswith("生效主题: light")
    assert "UI 字模库 tp_light" in prompt
    assert "图表形状基线" in prompt and "borderRadius" in prompt
    assert "可选主题" in prompt


def test_compose_injects_tokens_even_without_a_theme(packs):
    """无人值守没有前端可传主题：旧写法在 report_theme_id 为空时整段不注，模型只能自造颜色。"""
    empty = compose_system_prompt(None, [], username="u", user_role="admin", report_theme_id="")
    assert "## 报告主题令牌" in empty and "生效主题:" in empty
    assert "未传主题时按平台默认主题包" in empty
    named = compose_system_prompt(None, [], username="u", user_role="admin", report_theme_id="light")
    assert "UI 字模库 tp_light" in named


def test_report_delivery_skill_locks_the_map_and_theme_rules():
    """技能正文里三条止血口径要按文本锁住（真机缺陷的修复点，改回去没人发现）。

    ① HTML 禁自绘伪地图/伪图表（气泡散布冒充中国地图）；② 主题令牌来源=UI 字模库；
    ③ artifact 必须回写 theme（报表重跑靠它沿用同一主题）。
    """
    from backend.modules.mind.config import skill_loader
    skill = skill_loader.load_skill("report-delivery")
    assert skill and skill.get("system_prompt")
    body = skill["system_prompt"] + "\n" + str(skill.get("description") or "")
    assert "禁止自绘伪地图" in body
    assert "china_map" in body and "map_tile" in body
    assert "UI 字模库主题包" in body
    assert "必须**带 `theme` 字段" in body or "必须带 `theme` 字段" in body
    assert "不得硬编码具体色值" in body


def test_report_delivery_skill_matches_the_layout_contract_wording():
    """技能第四节的版式口径必须与 `report_layout_contract` 实际下发的文字一致。

    历史缺口：契约教模型"章节逐字保留 / chart_type 与 columns 不得改 / 数值只取本轮"，
    而技能正文只讲通用骨架 —— 模型按技能写、按契约被校验，两边词表对不上就永远判不达标。
    """
    import re
    from backend.modules.mind.config import skill_loader
    body = skill_loader.load_skill("report-delivery")["system_prompt"]
    contract = reports.report_layout_contract(
        {"format": "markdown", "headings": ["核心结论"],
         "charts": [{"title": "各省 PV", "chart_type": "bar", "columns": ["省份", "PV"]}]}, "finance")
    assert contract, "契约文本不应为空"
    # 契约里的四个硬要求，技能正文都要有对应说法
    for phrase in ("章节标题逐字", "chart_type", "严禁沿用上一版", "theme"):
        assert phrase in contract or phrase in body
    assert "版式契约优先" in body and "严禁沿用上一版" in body and "一字不改" in body
    # 交叉引用不得漂到旧编号（改章节序号忘改"第 N 节"是这类长提示词的典型腐化）
    headings = re.findall(r"^## ([一二三四五六七八])、", body, re.M)
    for ref in set(re.findall(r"第([一二三四五六七八])节", body)):
        assert ref in headings, f"正文引用了不存在的章节：第{ref}节"
