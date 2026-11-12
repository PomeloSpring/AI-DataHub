"""分析场景「按主体授权」的契约（用户 2026-11-06 拍板：有主体权限的角色才能看到该场景）。

锁四件事：
  1. **fail-closed**：未登记主体、也没勾「不限主体」的场景 → 对所有身份都不可见、不命中，
     并给出可诊断原因；空授权集不等于全量（与 waker-datasource-domain §1 同口径）。
  2. **三处消费同源**：`/` 候选、提示词注入（含显式选中）、管理面可见性标注都走
     scenario_access 的同一判定；只过滤展示层是假的 —— 靠触发词仍能把别人主体的
     场景正文带进提示词，那是越权信息面。本文件用源码级护栏钉住"生产调用点必须
     显式传 allowed_subjects"（与 test_function_ai_access 锁 resolve_policy 同一手法）。
  3. **写入点拦住空承诺**：配场景时必须声明主体范围（服务端 422），否则管理员配完
     发现"哪都没生效"只能被当成运行时故障。
  4. SQL 保留字：`key` 不加反引号会 1064，而异常被吞成空清单 = 所有场景对所有角色
     不可见（难查且危险），所以把这条也钉住。
  5. **显式指定不降格**：用 `/` 点名的场景要带着 pinned 事实一路到提示词并按命令式成节，
     且本轮选了哪个场景要可回放（否则“注入了方案但模型没按它做”从外观上无从分辨）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.core import scenario_access as sa
from backend.modules.mind.execution import quick_paths as qp

ROOT = Path(__file__).resolve().parents[1]


def _row(id_, title, content, triggers, *, subjects=None, subject_all=False, priority=0):
    return {
        "id": id_, "title": title, "content": content, "scenario": "",
        "triggers": triggers, "related_tables": "", "priority": priority,
        "workspace_id": 0, "datasource_id": 0,
        "subjects": subjects or [], "subject_all": subject_all,
    }


def _patch_rows(monkeypatch, rows):
    monkeypatch.setattr(qp, "_load_rows", lambda: rows)
    qp.clear_cache()


# ── 1. fail-closed ────────────────────────────────────────────────────────

def test_subject_unbound_scenario_is_invisible_with_reason(monkeypatch):
    """"没绑主体又没勾不限主体"的场景不命中，且原因说得出来（不静默当成"没配场景"）。"""
    ok, reason = sa.is_scenario_visible({"triggers": ["x"]}, {"rum_event"})
    assert ok is False and "未绑定主体" in reason

    ok2, _ = sa.is_scenario_visible({"subjects": ["rum_event"]}, {"rum_event"})
    assert ok2 is True
    ok3, reason3 = sa.is_scenario_visible({"subjects": ["rum_event"]}, {"case"})
    assert ok3 is False and "无权限" in reason3
    # 显式勾了「不限主体」= 对所有身份可见（这是主动扩权，不是漏配的副作用）
    assert sa.is_scenario_visible({"subject_all": True}, set())[0] is True


def test_empty_allowed_set_is_not_treated_as_unrestricted(monkeypatch):
    """空授权集 = 只有「不限主体」的场景可用，绝不能解释成"全量放行"。"""
    rows = [
        _row(1, "绑主体的场景", "方案", ["官网"], subjects=["rum_event"]),
        _row(2, "不限主体的场景", "方案", ["官网"], subject_all=True),
    ]
    _patch_rows(monkeypatch, rows)
    hits = qp.match_quick_paths("官网最近7天UV", allowed_subjects=set())
    assert [h["title"] for h in hits] == ["不限主体的场景"]


def test_none_means_caller_declined_subject_check(monkeypatch):
    """None = 调用方显式声明不做主体裁决（仅限测试/离线自证，生产点必须传集合）。"""
    _patch_rows(monkeypatch, [_row(1, "绑主体的场景", "方案", ["官网"], subjects=["rum_event"])])
    assert len(qp.match_quick_paths("官网 UV", allowed_subjects=None)) == 1
    assert qp.match_quick_paths("官网 UV", allowed_subjects={"other"}) == []


# ── 2. 显式选中（/ 面板）与候选下发 ────────────────────────────────────────

def test_pinned_scene_rejections_carry_reasons(monkeypatch):
    """选了没生效的场景必须带原因回传，不能静默丢弃（no-silent-degradation）。

    连“排第 4 位、因名额不够而没注入”也要有拒因 —— 旧写法先截断再判定，
    超出的选中项会连原因一起消失。
    """
    _patch_rows(monkeypatch, [
        _row(1, "可用场景", "方案A", ["x"], subjects=["rum_event"]),
        _row(2, "无正文场景", "", ["x"], subjects=["rum_event"]),
        _row(3, "别人主体场景", "方案C", ["x"], subjects=["equipment"]),
        _row(4, "名额外的可用场景", "方案D", ["x"], subjects=["rum_event"]),
    ])
    accepted, rejected = qp.paths_by_ids(["1", "2", "3", "4"], allowed_subjects={"rum_event"}, limit=1)
    assert [p["title"] for p in accepted] == ["可用场景"]
    reasons = {r["id"]: r["reason"] for r in rejected}
    assert "正文为空" in reasons["2"]
    assert "无权限" in reasons["3"]
    assert "最多注入" in reasons["4"]
    # id 不存在（含已停用）同样要给出原因，不静默少返
    _acc, rej2 = qp.paths_by_ids(["999"], allowed_subjects={"rum_event"})
    assert rej2 and "不存在" in rej2[0]["reason"]


def test_candidate_list_reports_hidden_count(monkeypatch):
    """因主体无权限被隐去的条数要如实带出，否则用户以为"没配场景"。"""
    _patch_rows(monkeypatch, [
        _row(1, "可见", "方案", ["x"], subjects=["rum_event"]),
        _row(2, "不可见", "方案", ["x"], subjects=["equipment"]),
        _row(3, "未绑主体", "方案", ["x"]),
    ])
    items, hidden = qp.list_visible_paths(allowed_subjects={"rum_event"})
    assert [i["name"] for i in items] == ["可见"]
    assert hidden == 2


def test_candidate_payload_field_names_match_frontend_contract(monkeypatch):
    """候选项的字段名必须跟前端契约一致（标识叫 key）。

    历史缺陷：这里下发成 `id`，前端读 `s.key` 得到 undefined → 同组所有项的
    React key 撞成一个，来回切类别就渲染出重复行、Esc 也关不掉那些残影；
    同时选中项拿不到标识，selected_scenes 变成空数组（选了等于没选）。
    """
    _patch_rows(monkeypatch, [_row(1, "可见", "方案", ["x"], subjects=["rum_event"])])
    items, _hidden = qp.list_visible_paths(allowed_subjects={"rum_event"})
    assert set(items[0]) == {"key", "name", "description"}
    assert items[0]["key"] == "1"
    # 内部 object_key 不随候选下发（对选择没意义，属实现细节）
    assert "subjects" not in items[0]


# ── 3. 三处消费同源（源码级护栏）──────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "backend/modules/mind/execution/secure_sdk.py",              # 交互会话注入
    "backend/modules/mind/execution/scheduled_analysis.py",      # 无人值守注入（按属主身份）
])
def test_production_call_sites_pass_allowed_subjects(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "match_quick_paths(" in src, rel
    assert "allowed_subjects=" in src, f"{rel} 调 match_quick_paths 没传 allowed_subjects = 不做主体裁决"
    assert "scenario_access.visible_subject_keys(" in src, rel


def test_slash_options_scenes_are_subject_filtered():
    """`/` 面板候选与注入同源：不能只在前端藏，服务端下发前就得过滤。"""
    src = (ROOT / "backend/modules/mind/api/chat.py").read_text(encoding="utf-8")
    block = src.split("def list_chat_slash_options")[1].split("# ── 会话上下文用量")[0]
    assert "list_visible_paths(allowed_subjects=allowed_subjects" in block
    assert "visible_subject_keys" in block          # 候选用与注入同一派生口径
    assert "scenes_hidden" in block                  # 被隐去的条数要下发，不静默少返


def test_selected_scenes_flows_through_both_chat_entries():
    """两条问答入口（/api/chat 与 /api/pipeline）都得带 selected_scenes，不得一条通一条断。"""
    for rel in ("backend/modules/mind/api/chat.py", "backend/modules/mind/api/pipeline.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "selected_scenes" in src, rel
    svc = (ROOT / "backend/modules/mind/services/chat_service.py").read_text(encoding="utf-8")
    assert '("selected_scenes"' in svc


# ── 4. 写入点拦住"配了没生效" ─────────────────────────────────────────────

def test_write_point_requires_subject_declaration():
    from fastapi import HTTPException

    from backend.modules.auth.api.knowledge import _assert_scenario_subjects

    with pytest.raises(HTTPException) as exc:
        _assert_scenario_subjects("quick_path", {"triggers": ["x"]})
    assert exc.value.status_code == 422 and "主体" in exc.value.detail

    # 勾了「不限主体」就是合法声明（显式扩权，由管理员负责）
    _assert_scenario_subjects("quick_path", {"subject_all": True})
    # 非场景类型不受这条约束（SQL 示例对等另有归属）
    _assert_scenario_subjects("sql_pair", {"triggers": []})


def test_unknown_subject_is_rejected(monkeypatch):
    from fastapi import HTTPException

    from backend.modules.auth.api import knowledge as kn

    monkeypatch.setattr(sa, "subject_catalog", lambda: [{"key": "rum_event"}])
    with pytest.raises(HTTPException) as exc:
        kn._assert_scenario_subjects("quick_path", {"subjects": ["ghost_object"]})
    assert exc.value.status_code == 422 and "ghost_object" in exc.value.detail


# ── 5. SQL 保留字（历史上把整张清单吞成空的坑）────────────────────────────

def test_subject_catalog_quotes_reserved_key():
    """`key` 是 MySQL 保留字：不加反引号会 1064，而异常被吞成空清单 = 所有场景不可见。"""
    src = (ROOT / "backend/core/scenario_access.py").read_text(encoding="utf-8")
    assert "o.object_key AS `key`" in src


def test_chat_frontend_picks_scenes_instead_of_refusing_them():
    """前端已接入场景选择：不得再出现“尚未接入执行链”的拒接，也不得漏上送 id。

    旧写法是候选存在但点一下就被拒（面板能选、后端不认），属于空承诺；现在两侧同源：
    选中项随 selected_scenes 上送，服务端无权时给原因而不是拒点。
    """
    fe = ROOT / "frontend" / "src"
    chat = (fe / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    store = (fe / "stores" / "chatStore.ts").read_text(encoding="utf-8")
    assert "尚未接入执行链" not in chat
    assert "scenes: pickedScenes" in chat
    assert "selected_scenes: opts?.scenes || []," in store
    # 候选与隐去数都要从 slash-options 映射进面板状态，漏一个就是“没列出来 = 没配”的误读
    assert "scenes: data?.scenes || []," in chat
    assert "scenes_hidden: data?.scenes_hidden || 0," in chat


# ── 6. 显式指定必须一路带到提示词（不能降格成“建议”）────────────────────────

def test_pinned_scene_survives_to_prompt_as_mandate():
    """用户 `/` 选中的场景，必须带着“用户指定”事实进提示词并按命令式成节。

    历史缺陷：`paths_by_ids` 的结果与触发词命中项合并成一份无差别的列表，提示词统一
    写成“由服务端按用户问题触发词命中”——模型把它当启发式建议，问题一含糊就回落到
    通用指标清单反问（方案确实注入了，但没人按它做）。三处同源不得丢标记。
    """
    qp_src = (ROOT / "backend/modules/mind/execution/quick_paths.py").read_text(encoding="utf-8")
    sdk_src = (ROOT / "backend/modules/mind/execution/secure_sdk.py").read_text(encoding="utf-8")
    pc_src = (ROOT / "backend/modules/mind/execution/prompt_composer.py").read_text(encoding="utf-8")
    # 1) 显式选中打标记，触发词命中不打
    assert "_hit_payload(path, [], pinned=True)" in qp_src
    assert "_hit_payload(path, hits)" in qp_src
    # 2) 合并不得抹掉标记（按 pinned 分档而不是只拼接）
    assert 'pinned = [p for p in items if p.get("pinned")]' in pc_src
    # 3) 命令式口径 + 缺输入时的反问必须落在场景语境内
    assert "用户本轮指定的分析场景" in pc_src
    assert "禁止回落到与该场景无关的通用指标清单反问" in pc_src
    # 3b) 中部注入会被后面的常驻闸门压过（实测两轮都失败），所以尾部复述与闸门让路都得在
    assert "收尾复述" in pc_src
    assert 'pinned_scenes = [p for p in quick_paths if isinstance(p, dict) and p.get("pinned")]' in pc_src
    # 让路条款只写在条件注入段（常驻闸门段受 prompt 字数护栏封顶，不得加条件条款）
    assert "用户显式指定的分析场景" not in pc_src
    # 4) 执行层侧：显式选中项置顶并与自动命中项合并（不各自丢到另一个参数里）
    assert "_pinned, _scene_rejected = paths_by_ids(" in sdk_src
    assert "quick_paths = _pinned + [p for p in _auto_hits" in sdk_src
    # 5) 作业行必须进**回合提示词**：只靠系统提示词实测不够（两轮都仍按通用清单反问）
    assert 'ctx.extra["_scene_turn_directive"] = scene_turn_directive(' in sdk_src
    assert "def build_turn_prompt(" in sdk_src
    assert "prompt = build_turn_prompt(" in sdk_src
    # 拼提示词的唯一入口：别处再直接调 _prompt_with_attachments 就会漏掉作业行
    assert sdk_src.count("adapter._prompt_with_attachments(") == 1


def test_chat_turn_records_selected_scene_for_replay():
    """本轮用了哪个场景必须可回放：消息里记 scenes，重试也不能把它丢掉。

    场景方案只进提示词不进答案，不记在轮次上就无从核对“选了到底生效没”；
    重试不带上 scenes 则变成“同一句话换了一套作业细则”的隐形改变。
    """
    fe = ROOT / "frontend" / "src"
    store = (fe / "stores" / "chatStore.ts").read_text(encoding="utf-8")
    chat = (fe / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    assert "scenes?: string[];" in store
    assert "...(opts?.scenes?.length ? { scenes: opts.scenes } : {})," in store
    assert "{ skills: last.skills, scenes: last.scenes, command: last.command }" in store
    # 回放 chip 显示场景业务名（不裸显 id，也不回退成“本轮未选”）
    assert "msg.scenes || []" in chat
    assert "slashOptions?.scenes?.find(s => s.key === id)?.name" in chat
