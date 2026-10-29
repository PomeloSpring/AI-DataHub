"""「业务黑话先澄清」提示词段落的落库脚本回归（离线，不连 DB）。

锁定两件事：
1. **幂等不叠加**：重复执行只替换本脚本的段落，其他文字原样保留；同一 AS-BOT
   在只读版/取数版之间切换时不留两段互斥说明。
2. **行为口径不漂移**：用户拍板的核心指令（不猜、不到处翻物理元数据、先推测
   候选语义词再 ask_user、候选必须来自实时目录原文、选定前不下发取数）必须
   原样出现在段落里——文案被删弱等于这条约束消失。
"""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "apply_jargon_clarification_prompt",
    Path(__file__).resolve().parents[1] / "backend" / "scripts" / "apply_jargon_clarification_prompt.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_splice_is_idempotent_and_keeps_other_text():
    original = "你是数据助手。\n\n## 工作规则\n- 不臆造数据"
    once = mod._splice(original, mod._BODY)
    twice = mod._splice(once, mod._BODY)
    assert once.count(mod._MARKER) == 1
    assert twice == once, "重复执行不得叠加段落"
    assert "不臆造数据" in twice and twice.startswith("你是数据助手。"), "原有文字必须保留"
    assert "不臆造数据" in twice.split(mod._MARKER)[0], "新段落应追加在原有内容之后"


def test_splice_replaces_other_variant_not_append():
    """只读版 → 取数版切换：旧变体整段替换，不留两段互斥说明。"""
    viewer = mod._splice("你是只读助手。", mod._segment_for(False))
    upgraded = mod._splice(viewer, mod._segment_for(True))
    assert mod._VIEWER_MARKER not in upgraded
    assert upgraded.count(mod._MARKER) == 1
    assert "你是只读助手。" in upgraded


def test_splice_handles_empty_prompt():
    body = mod._segment_for(True)
    assert mod._splice("", body) == body
    assert mod._splice("   \n  ", body) == body


def test_body_carries_the_agreed_directives():
    for body in (mod._segment_for(True), mod._segment_for(False)):
        assert "ask_user" in body, "必须显式指引 ask_user"
        assert "不许猜" in body
        assert "get_metrics" in body, "候选名称的唯一权威口径必须点名"
        # 候选只能从实时目录复制，不得发明（含只读版）
        assert "目录原文复制" in body
    assert "search_metadata" in mod._segment_for(True), "取数版须堵住『翻物理元数据找相似列』"
    assert "不下发取数" in mod._segment_for(True), "用户选定前不得取数"
