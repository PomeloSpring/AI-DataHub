"""report_themes 注册表与主题注入回归。"""
from services.datamind.execution import report_themes as rt
from services.datamind.execution.prompt_composer import compose_system_prompt

ALL = {"dark", "light", "tech", "finance", "bento", "glass", "ainative", "medical", "datafoundry"}


def test_registry_has_all_nine_themes():
    assert set(rt.THEME_TOKENS.keys()) == ALL
    assert rt.DEFAULT_THEME == "light"


def test_resolve_theme_unknown_falls_back():
    assert rt.resolve_theme("bogus")["theme"] == rt.DEFAULT_THEME
    assert rt.resolve_theme("bogus", "light")["theme"] == "light"
    assert rt.resolve_theme(None)["theme"] == rt.DEFAULT_THEME
    assert rt.resolve_theme("tech")["theme"] == "tech"


def test_tokens_shape_and_palette_length():
    for tid in ALL:
        t = rt.resolve_theme(tid)
        assert t["chart"] and len(t["chart"]) == 10
        assert "is_dark" in t and isinstance(t["is_dark"], bool)
    assert rt.chart_palette("datafoundry")[0].startswith("hsl(")


def test_css_block_snapshot():
    css = rt.theme_css_block("finance")
    for token in ("--bg", "--fg", "--primary", "--card", "--border", "--chart-1", "--chart-10"):
        assert token + ":" in css


def test_compose_injects_theme_only_when_provided():
    with_theme = compose_system_prompt(None, [], username="u", user_role="admin", report_theme_id="tech")
    assert "生效主题: tech" in with_theme
    without = compose_system_prompt(None, [], username="u", user_role="admin", report_theme_id="")
    assert "生效主题:" not in without
