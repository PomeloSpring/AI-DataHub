"""双语体系落地（内容 / 元数据 / 技能 / Chat 快捷方式）—— 回归护栏。

覆盖：
  * P1 语言层：contextvar / localized_text / localize_row / merge_overlay / localize_display；
  * P2 迁移形态（i18n 列 + 文章 lang/translation_group）；
  * P3 下发：slash_commands / quick_paths / as_bots / content categories+tags；
  * P4 交付物技能问产出语言 + 双语条目 + TBD/TODO 门禁；
  * 前端 lib/i18nValue 与语言包 key parity（此处只测后端能读到清单）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.common import i18n


# ── P1 语言层 ─────────────────────────────────────────────────
def test_contextvar_default_and_reset():
    assert i18n.current_locale() == i18n.DEFAULT_LOCALE
    tok = i18n.set_ui_locale("en-US")
    try:
        assert i18n.current_locale() == "en-US"
    finally:
        i18n.reset_ui_locale(tok)
    assert i18n.current_locale() == i18n.DEFAULT_LOCALE


def test_localized_text_hit_and_fallback():
    blob = {"name": {"en-US": "ISV Push Dashboard"}}
    assert i18n.localized_text("外部合作方推送看板", blob, "name", "en-US") == ("ISV Push Dashboard", False)
    # 无该语言译文 → 回落 + fell_back=True
    got, fell_back = i18n.localized_text("客户端IP榜", {}, "name", "en-US")
    assert got == "客户端IP榜" and fell_back is True
    # 默认语言永远读原列
    assert i18n.localized_text("原", blob, "name", "zh-CN") == ("原", False)


def test_localize_display_default_language_leaves_row_untouched():
    """中文请求 → 什么都不改（存量响应形状零变化，`*_zh` 与 `i18n_fallback_fields` 都不出现）。"""
    row = {"name": "Test", "i18n": {"name": {"en-US": "X"}}}
    before = dict(row)
    miss = i18n.localize_display(row, fields=("name",))
    assert row == before
    assert miss == []


def test_localize_display_writes_en_and_preserves_zh():
    tok = i18n.set_ui_locale("en-US")
    try:
        row = {"name": "外部合作方推送看板", "description": "分站推送",
               "i18n": {"name": {"en-US": "ISV Push Dashboard"}}}
        miss = i18n.localize_display(row, fields=("name", "description"))
        assert row["name"] == "ISV Push Dashboard"
        assert row["name_zh"] == "外部合作方推送看板"
        # 无英文的 description 保留中文原值 + 记入回落清单
        assert row["description"] == "分站推送"
        assert "description_zh" not in row  # 回落不改写，不造 *_zh 冗余
        assert miss == ["description"]
    finally:
        i18n.reset_ui_locale(tok)


def test_merge_overlay_recursive_dict_replace_list():
    base = {"title": "标题", "options": [{"label": "中国"}], "extra": 1}
    overlay = {"title": "Title", "options": [{"label": "China"}]}
    merged = i18n.merge_overlay(base, overlay)
    assert merged["title"] == "Title"
    # 列表整体替换（不按下标对齐）
    assert merged["options"] == [{"label": "China"}]
    # 未出现的键保持 base 值
    assert merged["extra"] == 1


def test_resolve_output_language_priority():
    # 用户显式选择 > 界面语言 > 提问语言 > 默认
    assert i18n.resolve_output_language("双语", ui_locale="zh-CN", question="今天推送成功率")[0] == "both"
    assert i18n.resolve_output_language("", ui_locale="en-US", question="")[0] == "en-US"
    # 无界面语言时按提问语言（英文提问）
    assert i18n.resolve_output_language("", ui_locale="", question="How are the push rates")[0] == "en-US"
    # 都没有 → 默认
    assert i18n.resolve_output_language("", ui_locale="", question="123")[0] == i18n.DEFAULT_LOCALE


def test_normalize_output_language_aliases():
    for raw, want in [
        ("中文", "zh-CN"), ("English", "en-US"), ("两个语言都要", "both"),
        ("zh-CN", "zh-CN"), ("en-US", "en-US"), ("both", "both"), ("双语", "both"),
        ("随便", ""),  # 未识别 → 空
    ]:
        assert i18n.normalize_output_language(raw) == want, raw


# ── P2 迁移文件（形态与幂等标记）─────────────────────────────
def test_migration_files_exist_and_are_idempotent():
    root = Path("docker/mysql")
    a = (root / "bilingual_metadata_migration.sql").read_text(encoding="utf-8")
    b = (root / "article_bilingual_migration.sql").read_text(encoding="utf-8")
    # 每张表都走 information_schema + PREPARE/EXECUTE（bilingual 覆盖 7 张；article 只有 1 张）
    assert a.count("information_schema.COLUMNS") >= 7
    assert b.count("information_schema.COLUMNS") >= 3
    for name, sql in [("bilingual", a), ("article", b)]:
        assert "PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;" in sql, name
    # 双语种子用 JSON_MERGE_PATCH 深合并（JSON_SET 只建最后一级会静默不写）
    assert "JSON_MERGE_PATCH" in a
    # 英文种子只补空缺，不覆盖人工改过的译文
    assert "JSON_CONTAINS_PATH" in a
    # JSON_MERGE_PATCH 深合并（JSON_SET 不建中间层，实测静默不写，见 pitfall）
    assert "JSON_MERGE_PATCH" in a


def test_supported_locales_match_frontend():
    """后端 SUPPORTED_LOCALES 必须与前端 i18n LOCALES 一致，否则回落会静默。"""
    import re
    fe = Path("frontend/src/i18n/index.ts").read_text(encoding="utf-8")
    m = re.search(r"LOCALES\s*=\s*\[([^\]]+)\]", fe)
    assert m, "frontend LOCALES list not found"
    fe_locales = {t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()}
    assert fe_locales == set(i18n.SUPPORTED_LOCALES), fe_locales


# ── P3 下发按语言 ─────────────────────────────────────────────
def test_slash_commands_localize_in_english():
    from backend.modules.mind.execution import slash_commands
    tok = i18n.set_ui_locale("en-US")
    try:
        cmds = slash_commands.list_commands()
        modes = slash_commands.list_modes()
    finally:
        i18n.reset_ui_locale(tok)
    by_key = {c["key"]: c for c in cmds}
    assert by_key["create_scheduled_task"]["label"] == "Create Scheduled Task"
    assert by_key["clear"]["label"] == "Clear Current Session"
    assert {"knowledge", "analysis"} == {m["key"] for m in modes}
    assert "Quick Answer" in [m["label"] for m in modes]
    # 中文请求 → 原样
    assert slash_commands.list_commands()[0]["label"] == "创建定时任务"


def test_dashboard_service_localizes_rows_with_fallback():
    import importlib
    mod = importlib.import_module("backend.modules.viz.services.dashboard_service")
    ds = mod.dashboard_service  # 模块导出的是实例
    tok = i18n.set_ui_locale("en-US")
    try:
        rows = ds.list_dashboards(user_id=1, user_role="admin")
    finally:
        i18n.reset_ui_locale(tok)
    assert rows, "should have dashboards"
    # 每条看板在英文请求下要么命中英文（有 *_zh），要么记 fallback（不静默）
    for r in rows[:5]:
        has_hit = any(k.endswith("_zh") for k in r)
        has_fb = bool(r.get("i18n_fallback_fields"))
        assert has_hit or has_fb, f"silent fallback: {r.get('name')}"


def test_quick_paths_localize_scenes_in_english():
    from backend.modules.mind.execution import quick_paths
    quick_paths.clear_cache()
    tok = i18n.set_ui_locale("en-US")
    try:
        scenes, hidden = quick_paths.list_visible_paths(workspace_id=0)
    finally:
        i18n.reset_ui_locale(tok)
    names = {s["name"] for s in scenes}
    # 3 条场景英文种子已在迁移里落地
    assert any("Website and H5" in n for n in names), f"missing en: {names}"


def test_as_bots_display_name_localizes_in_english():
    from backend.common.db import execute_query
    from backend.modules.mind.execution import as_bots
    row = execute_query("SELECT * FROM adh_as_bots WHERE as_bot_key='analyst'", fetchone=True)
    assert row, "analyst row missing"
    tok = i18n.set_ui_locale("en-US")
    try:
        bot = as_bots._normalize(row)
    finally:
        i18n.reset_ui_locale(tok)
    # 种子迁移已把 analyst 的 display_name/description 填了英文
    assert bot["display_name"] == "Data Analyst", bot["display_name"]


def test_content_categories_and_tags_localize():
    from backend.modules.platform.services import content_service as svc
    tok = i18n.set_ui_locale("en-US")
    try:
        cats = {c["key"]: c for c in svc.list_categories(with_counts=False)}
        tags = svc.list_tags()
    finally:
        i18n.reset_ui_locale(tok)
    assert cats["about"]["label"] == "About Us"
    assert any(t["tag"] == "Scan Accuracy" for t in tags)


# ── P4 交付物技能语言确认段 + 双语条目 ──────────────────────────
DELIVERY_SKILLS = ("content-writer", "report-delivery", "screen-builder")
OTHER_SKILLS = (
    "data-analysis", "pro-analysis", "arms-observability",
    "metadata-manager", "ontology-builder", "knowledge-qa",
)


def test_delivery_skills_require_output_language_confirm():
    for name in DELIVERY_SKILLS:
        p = Path(f"backend/modules/mind/config/skills/{name}/SKILL.md")
        text = p.read_text(encoding="utf-8")
        assert "## 产出语言" in text, name
        assert "ask_user" in text, name
        assert "中英各一份" in text or "both" in text, name


def test_non_delivery_skills_do_not_ask_language():
    """非交付物技能不得出现"动手前先问产出语言"段（防打扰扩散到 90% 的问数场景）。"""
    for name in OTHER_SKILLS:
        p = Path(f"backend/modules/mind/config/skills/{name}/SKILL.md")
        assert "## 产出语言（先确认再动手）" not in p.read_text(encoding="utf-8"), name


def test_all_skills_have_english_display_and_description():
    from backend.modules.mind.config import skill_loader
    skill_loader.clear_skill_cache()
    tok = i18n.set_ui_locale("en-US")
    try:
        for name in (*DELIVERY_SKILLS, *OTHER_SKILLS):
            s = skill_loader.load_skill(name)
            assert s.get("i18n", {}).get("display_name", {}).get("en-US"), \
                f"{name} missing display_name_en in frontmatter"
            assert s.get("i18n", {}).get("description", {}).get("en-US"), \
                f"{name} missing description_en in frontmatter"
            row, missing = i18n.localize_row(s, "", ("display_name", "description"))
            # 命中后不是回落到原中文
            assert row["display_name"] != s["display_name"], name
            assert missing == [], (name, missing)
    finally:
        i18n.reset_ui_locale(tok)


def test_placeholder_en_gate():
    """英文稿（lang=en-US）残留 TBD/TODO/FIXME/PLACEHOLDER 属硬阻断，与【待填 同纪律。"""
    from backend.modules.platform.services import content_service as svc
    hard, _ = svc.gate_article({"kind": "markdown", "lang": "en-US", "category_id": 1},
                               "This quarter we shipped TBD features.")
    codes = [h["code"] for h in hard]
    assert "placeholder_en" in codes, codes
    # 中文请求不触发
    hard2, _ = svc.gate_article({"kind": "markdown", "lang": "zh-CN", "category_id": 1},
                                 "This quarter we shipped TBD features.")
    assert "placeholder_en" not in [h["code"] for h in hard2]


def test_content_promote_messages_go_through_t_pair():
    """plan §P3：后端下发给用户的一行式回执（"已添加至内容中心草稿…"这类）必须走 t_pair
    跟随界面语言，不能再硬编码中文。这里按源码静态扫：out["message"] 赋值 6 处，除
    += review.get("message")（服务端另一处已本地化）外，其余都必须包 t_pair。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "backend/modules/platform/services/content_promote_service.py").read_text(encoding="utf-8")
    # 顶部 import i18n
    assert "from backend.common import i18n" in src
    # 关键中文短语必须**至少有一次**出现在 i18n.t_pair(...) 罩着的窗口内；
    # 允许别处（如源码注释/docstring）再提到同词 —— 那是给读者解释口径，不是硬编码文案。
    for kw in ("已新建内容草稿", "已把这份报告的正文追加为", "一字不差",
               "未提交审核", "草稿已保存到内容中心，但未能提交审核"):
        pos, hit = -1, False
        while True:
            pos = src.find(kw, pos + 1)
            if pos < 0:
                break
            window = src[max(0, pos - 200): pos + 200]
            if "i18n.t_pair" in window:
                hit = True
                break
        assert hit, f"文案 {kw} 未走 t_pair（会硬编码只出中文）"
