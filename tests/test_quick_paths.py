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
    # 触发词命中不是用户点名：不得带上 pinned 标记（否则提示词会把它写成强制剧本）
    assert hits[0]["pinned"] is False


def test_paths_by_ids_marks_user_pinned(monkeypatch):
    """`/` 显式选中的场景必须携带 pinned 事实。

    历史缺陷：合并进同一列表后“用户点名”与“触发词猜测”无从区分，提示词统一写成
    “由服务端按触发词命中”，模型把它当启发式建议 —— 用户选了场景，答案却按通用
    指标清单反问（选了等于没选）。
    """
    rows = [_row(7, "按页面 URL 或 IP 关联用户操作路径", "先认锚点再下钻", ["操作路径"])]
    _patch_rows(monkeypatch, rows)
    accepted, rejected = quick_paths.paths_by_ids(["7"])
    assert rejected == []
    assert len(accepted) == 1
    assert accepted[0]["pinned"] is True
    # 显式指定不看触发词：matched_triggers 为空不影响注入
    assert accepted[0]["matched_triggers"] == []


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
    assert "用户本轮指定的分析场景" not in prompt


def test_prompt_renders_pinned_scene_as_mandate():
    """用户 `/` 指定的场景 = 命令式独立成节，不得再披“触发词命中”的口径。"""
    paths = [{"title": "按页面 URL 或 IP 关联用户操作路径",
              "content": "第 1 步 认锚点", "scenario": "还原访问链路",
              "matched_triggers": [], "pinned": True}]
    prompt = compose_system_prompt(None, [], username="u", user_role="admin", quick_paths=paths)
    assert "用户本轮指定的分析场景" in prompt
    assert "显式指定" in prompt
    assert "按页面 URL 或 IP 关联用户操作路径" in prompt
    assert "第 1 步 认锚点" in prompt
    # 关键行为约束：缺输入时的反问必须落在场景语境内，不许回落到通用指标清单
    assert "禁止回落到与该场景无关的通用指标清单反问" in prompt
    # 唯一一项是用户指定时，不得出现“按触发词命中”的错标
    assert "由服务端按用户问题触发词命中" not in prompt


def test_pinned_scene_is_echoed_at_prompt_tail():
    """指定场景必须在提示词**末尾**再复述一次（中部注入会被后面的常驻闸门压过）。

    实测（会话 1783069459276）：只在中部写“必须按此方案执行”时，qwen3.7-plus 仍按
    「歧义闸门」的通用模板反问“查哪类数据”，而不是问该场景缺的锚点。
    """
    from backend.modules.mind.execution.prompt_composer import TOOL_ORCHESTRATION_RULES
    paths = [{"title": "按页面 URL 或 IP 关联用户操作路径", "content": "第 1 步 认锚点",
              "pinned": True}]
    prompt = compose_system_prompt(None, [], username="u", user_role="admin", quick_paths=paths)
    assert "收尾复述" in prompt
    # 尾部复述必须排在常驻闸门之后（recency 位置才有意义）
    assert prompt.index("## 歧义闸门") < prompt.index("收尾复述")
    # 尾部复述里要带场景业务名（不带名字就等于只提了句空口号）
    tail = prompt[prompt.index("收尾复述"):]
    assert "按页面 URL 或 IP 关联用户操作路径" in tail
    assert "不得换个口径把用户的指定掩掉" in tail      # 无法执行时必须显式说明
    # 指定场景的让路条款只能写在条件注入段里（常驻闸门段受字数护栏封顶，不得加条件条款）
    assert "优先级高于上面的通用反问模板" in tail
    assert "用户显式指定的分析场景" not in TOOL_ORCHESTRATION_RULES


def test_no_tail_echo_without_pinned_scene():
    """没指定场景时不得凭空多出这段（否则模型会去找一个不存在的场景）。"""
    prompt = compose_system_prompt(None, [], username="u", user_role="admin",
                                   quick_paths=[{"title": "自动命中", "content": "方案",
                                                 "matched_triggers": ["销售额"]}])
    assert "收尾复述" not in prompt


# ── 回合提示词里的作业行（系统提示词不够，实测会被通用模板压过）────────────────

def test_scene_turn_directive_empty_without_scene():
    from backend.modules.mind.execution.prompt_composer import scene_turn_directive
    assert scene_turn_directive("") == ""
    assert scene_turn_directive("   ") == ""


def test_scene_turn_directive_names_scene_and_blocks_cross_scene_ask():
    from backend.modules.mind.execution.prompt_composer import scene_turn_directive
    text = scene_turn_directive("按页面 URL 或 IP 关联用户操作路径")
    assert "按页面 URL 或 IP 关联用户操作路径" in text
    assert "在该场景内理解" in text
    assert "不得改问「想查哪类数据」" in text
    assert "先说明原因" in text          # 无法执行时必须显式告知（no-silent-degradation）


def test_build_turn_prompt_appends_directive_at_tail():
    """作业行必须拼在**回合提示词末尾**（问题之后），不是只留在系统提示词里。

    实测（会话 1783069459277，site=global）：系统提示词中部命令式 + 尾部复述全到位，
    qwen3.7-plus 仍答“你想查近三个月的什么内容呢？比如某个业务指标的趋势…”；
    同一载荷只加这行作业行后，三次探针均改为要场景锚点（页面URL/客户端IP/会话ID/用户ID）。
    """
    from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask
    from backend.modules.mind.execution.secure_sdk import build_turn_prompt

    class _Adapter:
        def _prompt_with_attachments(self, task, include_history=True):
            return task.question

    def _task(directive):
        extra = {"_scene_turn_directive": directive} if directive else {}
        return ExecutionTask(task_id="t", question="查近三个月的", context=ExecutionContext(extra=extra))

    plain = build_turn_prompt(_Adapter(), _task(""), include_history=False)
    assert plain == "查近三个月的"                      # 没指定场景：不往提示词里塞空指令

    with_scene = build_turn_prompt(_Adapter(), _task("〔本轮指定分析场景：X〕\n要求……"),
                                   include_history=False)
    assert with_scene.startswith("查近三个月的")          # 问题在前，作业行在尾
    assert with_scene.endswith("要求……")
    assert "〔本轮指定分析场景：X〕" in with_scene


def test_prompt_splits_pinned_and_trigger_hit_sections():
    """两类同时存在时分两档渲染，各自口径不串（指定项不会被建议项措辞掩掉）。"""
    paths = [
        {"title": "用户选的", "content": "方案甲", "pinned": True},
        {"title": "触发词中的", "content": "方案乙", "matched_triggers": ["销售额"], "pinned": False},
    ]
    prompt = compose_system_prompt(None, [], username="u", user_role="admin", quick_paths=paths)
    assert "用户本轮指定的分析场景" in prompt and "方案甲" in prompt
    assert "命中的快速路径查询方案" in prompt and "方案乙" in prompt
    # 分档后“触发词命中”的头部不得盖在用户指定那节上
    assert prompt.index("用户本轮指定的分析场景") < prompt.index("命中的快速路径查询方案")
