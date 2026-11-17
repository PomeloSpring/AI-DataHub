"""双语文章条目（lang / translation_group / i18n）落地护栏。

覆盖 plan §P7 里"文章按语言各一条 + 译文关联"这条链路的**把关点**：
  * 迁移文件形态：adh_content_articles 新增 lang / translation_group / i18n + 索引；
  * `list_articles(lang=...)` 受控清单校验，未知值直接拒（不就近、不猜）；
  * `create_article` 的 lang 默认跟随当前请求语言，translation_group 空则自关联回落到 slug；
  * `_attach_translations` 的输出契约：无英文条目时 en.slug=""、available=False（供前端
    显示"该篇暂无英文版"，**不得静默换语言**）；
  * 英文稿门禁：`lang=en-US` 才扫 TBD/TODO/FIXME/PLACEHOLDER，中文稿不受影响；
  * 批量工具幂等：`_merge_patch` 已有英文不覆盖。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.common import i18n
from backend.modules.platform.services import content_service as svc


REPO = Path(__file__).resolve().parents[1]


# ── 迁移形态 ─────────────────────────────────────────────────
def test_article_bilingual_migration_declares_columns_and_index():
    sql = (REPO / "docker/mysql/article_bilingual_migration.sql").read_text(encoding="utf-8")
    # 三列必须都在，且都是幂等 ADD（存储过程或 IF NOT EXISTS 判断）
    for col in ("lang", "translation_group", "i18n"):
        assert re.search(rf"ADD COLUMN\s+`?{col}`?", sql) or re.search(rf"ADD\s+`?{col}`?\s", sql), col
    assert "idx_article_lang_group" in sql
    # lang 默认值必须是中文（存量行为零变化）；SQL 里字面量转义为 ''zh-CN''
    assert re.search(r"lang[^,\n]*DEFAULT\s+''zh-CN''", sql), "lang DEFAULT ''zh-CN'' not found"
    # translation_group 空回填 slug：保证存量条目自动成组，前端译文跳转不 404
    assert "translation_group=slug" in sql.replace(" ", "")


# ── list_articles 的 lang 参数：受控清单外一律拒 ─────────
class _RecordingDB:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []

    def query(self, sql, params=(), fetchone=False):
        self.calls.append((sql, tuple(params)))
        if sql.startswith("SELECT COUNT(*)"):
            return {"n": len(self.rows)} if fetchone else [{"n": len(self.rows)}]
        return self.rows

    def write(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return 1

    def insert(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return 9001


def test_list_articles_rejects_unknown_lang(monkeypatch):
    db = _RecordingDB()
    monkeypatch.setattr(svc, "execute_query", db.query)
    with pytest.raises(svc.ContentError) as ei:
        svc.list_articles(lang="fr-FR")
    assert "语言取值不合法" in str(ei.value)
    # 拒得早：SQL 一条都没发出去
    assert db.calls == []


def test_list_articles_normalizes_and_filters_by_lang(monkeypatch):
    db = _RecordingDB()
    monkeypatch.setattr(svc, "execute_query", db.query)
    svc.list_articles(lang="en")  # normalize 应展开成 en-US
    # WHERE 里必须出现 a.lang=%s，且参数是 en-US（不是 en）
    where_sqls = [c for c in db.calls if "a.lang=%s" in c[0]]
    assert where_sqls, db.calls
    assert "en-US" in where_sqls[0][1]


# ── create_article：lang 默认跟随界面；translation_group 空回退 slug ──
def test_create_article_sql_includes_lang_group_and_i18n_columns():
    """INSERT 语句必须把 lang / translation_group / i18n 三列一次落库（不额外 UPDATE），
    且占位符数量与列数量对齐（MySQL 1136 是最容易踩的历史坑）。

    读 co_consts 而不是 regex 源码 —— 源码里 INSERT SQL 是相邻字符串常量拼接，
    Python 编译期已经合成一条完整字面量，直接拿比正则稳。"""
    sql = next((c for c in svc.create_article.__code__.co_consts
                if isinstance(c, str) and c.startswith("INSERT INTO adh_content_articles")), None)
    assert sql, "未在 create_article 常量池找到 INSERT SQL"
    m = re.search(r"\(([^)]+?)\)\s*VALUES\s*\(([^)]+?)\)", sql)
    assert m, sql
    cols = [c.strip() for c in m.group(1).split(",")]
    marks = [c.strip() for c in m.group(2).split(",")]
    for col in ("lang", "translation_group", "i18n"):
        assert col in cols, col
    # 列数 == 值数（`version` 用常量 1 占位，不参与 %s）
    assert len(cols) == len(marks), (cols, marks)


def test_create_article_defaults_follow_locale_and_slug():
    """服务源码里 lang / tg 的默认逻辑：payload.lang 走 normalize_locale；
    空则跟随 current_locale；tg 空回退本篇 slug。"""
    src = (REPO / "backend/modules/platform/services/content_service.py").read_text(encoding="utf-8")
    assert re.search(r'article_lang\s*=\s*i18n\.normalize_locale\(payload\.get\("lang"\)\)\s*or\s*i18n\.current_locale\(\)', src), \
        "create_article 未采用『payload.lang 优先 / 界面语言兜底』的默认"
    # tg or slug：中文条目自关联即成组（不回填 = 前端译文跳转 404）
    assert "tg or slug" in src


# ── _attach_translations 输出契约：无英文版必须显式 available=False ──
def test_attach_translations_no_group_marks_both_unavailable(monkeypatch):
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: [])
    items = [{"slug": "x", "translation_group": ""}]
    svc._attach_translations(items)
    assert items[0]["translation"] == {
        "en": {"slug": "", "available": False},
        "zh": {"slug": "", "available": False},
    }


def test_attach_translations_paired_group_reports_availability(monkeypatch):
    rows = [
        {"translation_group": "g1", "slug": "g1", "lang": "zh-CN", "live_revision_id": 12},
        {"translation_group": "g1", "slug": "g1-en", "lang": "en-US", "live_revision_id": 0},
    ]
    monkeypatch.setattr(svc, "execute_query", lambda *a, **k: rows)
    items = [{"slug": "g1", "translation_group": "g1"}]
    svc._attach_translations(items)
    tr = items[0]["translation"]
    assert tr["zh"] == {"slug": "g1", "available": True}
    # 英文条目存在但未发布 —— available=False，前端不得把它当成"可切换"
    assert tr["en"] == {"slug": "g1-en", "available": False}


def test_attach_translations_batch_single_query_no_n_plus_1(monkeypatch):
    calls = []

    def q(sql, params=(), fetchone=False):
        calls.append((sql, tuple(params)))
        return []

    monkeypatch.setattr(svc, "execute_query", q)
    items = [{"slug": f"a{i}", "translation_group": f"g{i}"} for i in range(5)]
    svc._attach_translations(items)
    assert len(calls) == 1, calls
    # 一次 SQL 拉齐 5 个 group：IN (...) 里应有 5 个 %s
    assert calls[0][0].count("%s") == 5


# ── 英文稿 TBD/TODO 门禁 ─────────────────────────────────────
@pytest.fixture
def _stub_gate_db(monkeypatch):
    """gate_article 会查 categories 表是否 active；这里 stub 掉，只保留纯文本判定。"""
    monkeypatch.setattr(svc, "execute_query", lambda sql, params=(), fetchone=False: ({"is_active": 1} if fetchone else [{"is_active": 1}]))


def _gate(article, body):
    hard, _warns = svc.gate_article({"category_id": 1, **article}, body)
    return [h["code"] for h in hard]


def test_english_gate_blocks_TBD_placeholder(_stub_gate_db):
    codes = _gate({"kind": "markdown", "lang": "en-US"}, "# Report\n\nCoverage: TBD\n")
    assert "placeholder_en" in codes


def test_english_gate_does_not_flag_word_inside_identifier(_stub_gate_db):
    # "PTBDS" / "ITBDX" 里含 TBD 子串但整词不匹配 → 不应误报（word boundary 保证）
    codes = _gate({"kind": "markdown", "lang": "en-US"}, "See table PTBDS and XITBDY row.\n")
    assert "placeholder_en" not in codes


def test_chinese_gate_not_affected_by_english_placeholder(_stub_gate_db):
    # 中文稿里出现 TBD 不触发 placeholder_en（避免"英文词出现在中英混排中文稿里"的误伤）
    codes = _gate({"kind": "markdown", "lang": "zh-CN"}, "状态: TBD\n")
    assert "placeholder_en" not in codes


def test_chinese_gate_still_blocks_chinese_placeholder(_stub_gate_db):
    codes = _gate({"kind": "markdown", "lang": "zh-CN"}, "覆盖率: 【待填】\n")
    assert "placeholder" in codes


# ── 批量工具幂等：已有英文不覆盖 ───────────────────────────
def test_translate_metadata_merge_patch_preserves_existing_english():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tmb", REPO / "logs/translate_metadata_batch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    current = {"name": {"en-US": "Human-Curated Name"}, "description": {"en-US": ""}}
    patch = {"name": {"en-US": "Machine Guess"},       # 已有英文 → 不覆盖
             "description": {"en-US": "Machine Filled"}}  # 空 → 填上
    merged = mod._merge_patch(current, patch)
    assert merged["name"]["en-US"] == "Human-Curated Name"
    assert merged["description"]["en-US"] == "Machine Filled"


def test_translate_metadata_uses_glossary_in_prompt():
    src = (REPO / "logs/translate_metadata_batch.py").read_text(encoding="utf-8")
    # 术语表必须硬注入 system prompt（否则同词多译，前端切语言看到两套说法）
    assert "METADATA_GLOSSARY" in src
    assert "glossary" in src.lower()


# ── API 层：/articles 的 lang QueryParam 转发 ───────────────
def test_api_articles_accepts_lang_query_param():
    src = (REPO / "backend/modules/platform/api/content.py").read_text(encoding="utf-8")
    # 路由必须显式声明 lang: str = Query(...)，且转发到 list_articles(lang=...)
    assert re.search(r"lang\s*:\s*str\s*=\s*Query", src)
    assert re.search(r"lang=lang", src)
