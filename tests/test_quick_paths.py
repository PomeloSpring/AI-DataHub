"""快速路径（Quick Path）匹配与提示词注入用例。

覆盖：触发词命中打分、路径名称加权、优先级排序、数据源/工作空间作用域过滤、
无命中返回空，以及 compose_system_prompt 命中方案小节渲染。
"""
from __future__ import annotations

from backend.modules.mind.execution import quick_paths
from backend.modules.mind.execution.prompt_composer import compose_system_prompt


def _row(id_, title, content, triggers, *, priority=0, datasource_id=0, workspace_id=0, scenario=""):
    return {
        "id": id_, "title": title, "content": content, "scenario": scenario,
        "triggers": triggers, "related_tables": "", "priority": priority,
        "workspace_id": workspace_id, "datasource_id": datasource_id,
    }


def _patch_rows(monkeypatch, rows):
    monkeypatch.setattr(quick_paths, "_load_rows", lambda: rows)
    quick_paths.clear_cache()


def test_trigger_hit_returns_path(monkeypatch):
    rows = [_row(1, "按月统计销售额", "按月聚合 orders.amount", ["销售额", "GMV"])]
    _patch_rows(monkeypatch, rows)
    hits = quick_paths.match_quick_paths("今年总GMV是多少")
    assert len(hits) == 1
    assert hits[0]["title"] == "按月统计销售额"
    assert "GMV" in hits[0]["matched_triggers"]
    assert hits[0]["content"] == "按月聚合 orders.amount"


def test_no_trigger_no_hit(monkeypatch):
    rows = [_row(1, "按月统计销售额", "方案", ["销售额"])]
    _patch_rows(monkeypatch, rows)
    assert quick_paths.match_quick_paths("列出所有用户") == []


def test_empty_question_no_hit(monkeypatch):
    _patch_rows(monkeypatch, [_row(1, "x", "方案", ["x"])])
    assert quick_paths.match_quick_paths("") == []


def test_more_triggers_outrank_priority(monkeypatch):
    # 命中 2 个触发词的路径应排在只命中 1 个的高优先级路径之前
    rows = [
        _row(1, "A", "方案A", ["销售额"], priority=100),
        _row(2, "B", "方案B", ["销售额", "GMV", "成交"], priority=1),
    ]
    _patch_rows(monkeypatch, rows)
    hits = quick_paths.match_quick_paths("查询销售额和GMV")
    assert hits[0]["title"] == "B"


def test_title_full_match_weighted(monkeypatch):
    rows = [
        _row(1, "复购率分析", "方案A", ["其它"]),
        _row(2, "别的", "方案B", ["分析"]),
    ]
    _patch_rows(monkeypatch, rows)
    # 问题整词包含路径名称"复购率分析" → 加权 +2，胜过仅命中"分析"
    hits = quick_paths.match_quick_paths("帮我做复购率分析")
    assert hits[0]["title"] == "复购率分析"


def test_datasource_scope_filter(monkeypatch):
    rows = [
        _row(1, "全局", "方案", ["销售额"], datasource_id=0),
        _row(2, "源5", "方案", ["销售额"], datasource_id=5),
    ]
    _patch_rows(monkeypatch, rows)
    # 会话源=5：全局 + 源5 都可命中
    assert len(quick_paths.match_quick_paths("销售额", datasource_id=5)) == 2
    # 会话源=9：仅全局命中，源5 被过滤
    hits = quick_paths.match_quick_paths("销售额", datasource_id=9)
    assert [h["title"] for h in hits] == ["全局"]


def test_workspace_scope_filter(monkeypatch):
    rows = [
        _row(1, "全局", "方案", ["销售额"], workspace_id=0),
        _row(2, "空间301", "方案", ["销售额"], workspace_id=301),
    ]
    _patch_rows(monkeypatch, rows)
    assert len(quick_paths.match_quick_paths("销售额", workspace_id=301)) == 2
    hits = quick_paths.match_quick_paths("销售额", workspace_id=999)
    assert [h["title"] for h in hits] == ["全局"]


def test_content_empty_not_injected(monkeypatch):
    rows = [_row(1, "空方案", "", ["销售额"])]
    _patch_rows(monkeypatch, rows)
    assert quick_paths.match_quick_paths("销售额") == []


# ── 提示词注入 ──────────────────────────────────────────────────────

def test_prompt_renders_quick_path_section():
    paths = [{"title": "按月统计销售额", "content": "按月聚合 orders.amount",
              "scenario": "销售趋势", "matched_triggers": ["销售额"]}]
    prompt = compose_system_prompt(None, [], username="u", user_role="admin", quick_paths=paths)
    assert "命中的快速路径查询方案" in prompt
    assert "按月统计销售额" in prompt
    assert "按月聚合 orders.amount" in prompt
    assert "销售额" in prompt  # 命中触发词回显


def test_prompt_no_section_without_paths():
    prompt = compose_system_prompt(None, [], username="u", user_role="admin")
    assert "命中的快速路径查询方案" not in prompt
