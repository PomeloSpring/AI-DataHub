"""技能加载数据源回归：config/skills/ 文件是唯一事实源，DB 覆盖层不得回潮。

背景（日志现象）：`Failed to load skill from DB report-delivery: (1146, "Table
'adh2.adh_skills' doesn't exist")` —— 每轮带技能的对话都刷一条 WARNING。
根因不是"表该建"：adh_skills 全仓无建表迁移、无生产写入（保存走 save_skill_folder
落盘），它只是退役没清干净的"DB 覆盖文件"第二事实源。留着有两个问题：
  1. 加载路径的 try/except 把"读不到第二源"降级成 warning → 日志噪音掩盖结构问题；
  2. 若某环境手建过该表并残留旧行，会**静默遮蔽** SKILL.md 修改（改了不生效且无提示）。
本文件锁定：加载路径不碰 DB、残留数据改为启动显式告警。
"""
import inspect
import logging

import pytest

from backend.modules.mind.config import skill_loader
from backend.modules.mind.config.skill_loader import (
    legacy_db_skill_names, warn_legacy_db_skills,
)

SKILL_MD = """---
name: demo-skill
display_name: 演示技能
description: 用于回归的示例技能
category: analysis
---

正文：按此提示词执行分析。
"""


@pytest.fixture(autouse=True)
def _clean_cache():
    skill_loader.clear_skill_cache()
    yield
    skill_loader.clear_skill_cache()


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """把 SKILLS_DIR 指向临时目录，并**封死 DB**：加载路径若碰 DB 直接炸。"""
    root = tmp_path / "skills"
    (root / "demo-skill").mkdir(parents=True)
    (root / "demo-skill" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    monkeypatch.setattr(skill_loader, "SKILLS_DIR", root)

    def _no_db():
        raise AssertionError("技能加载路径不得再访问数据库（adh_skills 已退役）")
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn", _no_db)
    return root


# ── 加载路径：只读文件 ───────────────────────────────────────────

def test_load_skill_from_file_only(skills_dir):
    skill = skill_loader.load_skill("demo-skill")
    assert skill is not None
    assert skill["display_name"] == "演示技能"
    assert skill["system_prompt"].startswith("正文")
    assert skill["format"] == "skill_md"


def test_list_skills_from_file_only(skills_dir):
    names = [s["name"] for s in skill_loader.list_skills()]
    assert names == ["demo-skill"]
    assert [s["name"] for s in skill_loader.list_analysis_skills()] == ["demo-skill"]


def test_missing_skill_returns_none(skills_dir):
    assert skill_loader.load_skill("nope") is None


def test_save_skill_folder_preserves_unmanaged_frontmatter_keys(skills_dir):
    """管理页保存只改展示字段，不得剥掉 `triggers` / `requires_any`。

    被剥掉的后果是技能正文静默停止展开（改了不生效且无线索），与
    no-silent-degradation 相违；这里把“未纳管键回带”锁住。
    """
    (skills_dir / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndisplay_name: 演示技能\n"
        "description: 用于回归的示例技能\ncategory: analysis\n"
        "requires_any: [run_semantic_query]\ntriggers: [\u73af\u6bd4, \u8d8b\u52bf]\n---\n\n旧正文\n",
        encoding="utf-8")
    skill_loader.clear_skill_cache()
    skill_loader.save_skill_folder(
        "demo-skill", display_name="演示技能V2", description="改过了", category="analysis",
        body="新正文", custom=False)
    skill_loader.clear_skill_cache()
    skill = skill_loader.load_skill("demo-skill")
    assert skill["display_name"] == "演示技能V2"
    assert skill["system_prompt"] == "新正文"
    assert skill["skill_config"]["requires_any"] == ["run_semantic_query"]
    assert skill["skill_config"]["triggers"] == ["环比", "趋势"]


# ── 防回潮：加载路径不得再出现访问 adh_skills 的 SQL ────────────────

_DB_TOUCH = ("adh_skills", "get_metadata_conn")


def test_loader_functions_have_no_db_source():
    for fn in (skill_loader._load_skill_uncached, skill_loader._list_skills_uncached):
        src = inspect.getsource(fn)
        for token in _DB_TOUCH:
            assert token not in src, f"{fn.__name__} 又接回了 DB 覆盖层（{token}）"


def test_skills_admin_api_has_no_db_source():
    from backend.modules.mind.api import skills_admin
    src = inspect.getsource(skills_admin)
    # 文档里允许提及退役事实，但不得再出现访问该表的 SQL
    for verb in ("FROM adh_skills", "UPDATE adh_skills", "INTO adh_skills"):
        assert verb not in src, f"Skills 管理 API 不得再读写已退役的 adh_skills（{verb}）"


def test_db_overlay_functions_are_gone():
    assert not hasattr(skill_loader, "_load_skill_from_db")


# ── 残留数据：启动显式告警（不静默丢）──────────────────────────────

class _FakeCursor:
    def __init__(self, rows=None, error=None):
        self._rows, self._error = rows or [], error

    def execute(self, sql, params=None):
        if self._error:
            raise self._error

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows=None, error=None):
        self._cursor = _FakeCursor(rows, error)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _patch_query(monkeypatch, rows=None, error=None):
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn(rows, error))


def test_missing_table_is_not_a_failure(monkeypatch, caplog):
    """表不存在 = 标准退役态：静默通过，不再每轮对话刷 WARNING。"""
    _patch_query(monkeypatch, error=RuntimeError("(1146, \"Table 'adh2.adh_skills' doesn't exist\")"))
    with caplog.at_level(logging.WARNING):
        assert warn_legacy_db_skills() is None
    assert not caplog.records


def test_legacy_rows_are_reported_loudly(monkeypatch, caplog):
    """表里还有生效行 → 必须点名告警，提醒迁移或清理（不静默丢弃）。"""
    _patch_query(monkeypatch, rows=[{"name": "report-delivery"}, {"name": "old-flow"}])
    with caplog.at_level(logging.WARNING, logger="backend.modules.mind.config.skill_loader"):
        names = warn_legacy_db_skills()
    assert names == ["report-delivery", "old-flow"]
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "report-delivery" in msg and "不再生效" in msg


def test_empty_legacy_table_is_quiet(monkeypatch, caplog):
    _patch_query(monkeypatch, rows=[])
    with caplog.at_level(logging.WARNING):
        assert warn_legacy_db_skills() == []
    assert not caplog.records


def test_unexpected_db_error_does_not_break_startup(monkeypatch, caplog):
    """非"表缺失"的异常仍要留痕（可诊断），但不得阻断启动对账。"""
    _patch_query(monkeypatch, error=RuntimeError("(2003, 'Can't connect to MySQL')"))
    with caplog.at_level(logging.WARNING, logger="backend.modules.mind.config.skill_loader"):
        assert legacy_db_skill_names() is None
    assert "2003" in " ".join(r.getMessage() for r in caplog.records)


def test_missing_table_detection():
    assert skill_loader._is_missing_table_error(RuntimeError("(1146, \"Table 'x' doesn't exist\")"))
    assert not skill_loader._is_missing_table_error(RuntimeError("(1054, 'Unknown column')"))
