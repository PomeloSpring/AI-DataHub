"""进化中心「Chat 内审批」的回归门禁（双闸口径）。

锁定五件事（对应 as-bot-system-waker §3 / perm_link / function_tools 的契约）：

1. **双闸不降级成单闸**：能审批 = ``evolution:manage``，但每个出口仍按它**本来所属域**的
   权限码再裁一次（ontology:save / metrics:manage / knowledge:manage）；少一道即拒。
2. **DB 镜像与注册表同源**：迁移文件里的 ``ai_action_key`` 必须与
   ``FUNCTION_ACTION_SPECS`` 的键集合完全一致（否则角色页显示“配了没消费方”）。
3. **黑盒**：入参不接受内部 id，返回不回内部 id 与物理列名（维度草稿的 target_column）。
4. **提议只是备忘录**：标记已处理必须带落点（applied_ref），驳回必须给理由。
5. **失败必须暴露**：归纳失败回显式错误，不吞成“没有新待办”（no-silent-degradation §1）。
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from backend.core import perm_link
from backend.modules.catalog.services import evolution_service as evo
from backend.modules.catalog.services import knowledge_update_service as kus
from backend.modules.mind.execution import function_tools as ft

#: 动作 → (能力面权限码, 是否只读)。与 docker/mysql/evolution_ai_review_migration.sql 对应。
EVO_ACTIONS = {
    "evolution_queue.list": ("evolution:read", True),
    "evolution_candidate.adopt": ("evolution:manage", False),
    "evolution_candidate.to_term": ("evolution:manage", False),
    "evolution_candidate.reject": ("evolution:manage", False),
    "evolution_metric.create": ("evolution:manage", False),
    "evolution_dimension.create": ("evolution:manage", False),
    "evolution_proposal.applied": ("evolution:manage", False),
    "evolution_proposal.reject": ("evolution:manage", False),
    "evolution_digest.run": ("evolution:manage", False),
}

MIGRATION = "docker/mysql/evolution_ai_review_migration.sql"


def _payload(result) -> dict:
    return json.loads(result["content"][0]["text"])


def _fake_ctx():
    return SimpleNamespace(user_id=1, workspace_id=0)


def _wire_handler(monkeypatch):
    """把身份/权限/审计换成测试替身，只留业务分支。"""
    monkeypatch.setattr(ft, "_evo_context", lambda: (_fake_ctx(), "tester"))
    monkeypatch.setattr(ft, "_evo_audit", lambda *a, **k: None)
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: None)


# ── 1. 注册表与通道 ──────────────────────────────────────────────────────────

def test_evolution_actions_registered_with_double_gate_codes():
    for action, (code, read_only) in EVO_ACTIONS.items():
        spec = ft.FUNCTION_ACTION_SPECS[action]
        assert spec["perm_code"] == code, action
        assert bool(spec["read_only"]) is read_only, action
        assert spec["tool_name"], action

    # 能力面码是 perm_gate 的第二道消费方（metrics:manage 同理，见建草稿出口）
    assert "evolution:manage" in perm_link.PERM_GATED_ACTION_PERMS
    assert "metrics:manage" in perm_link.PERM_GATED_ACTION_PERMS
    # 只看待办属只读通道（ai_action_key 已登记 → 通道判定以功能动作为准）
    assert perm_link.ai_channel("evolution:read", "evolution_queue.list") == \
        perm_link.AI_CHANNEL_FUNCTION_ACTION


def test_eval_manage_stays_closed_to_ai():
    """评价 AI 的标尺不能由 AI 自己写：任何进化动作都不得挂 eval:manage。"""
    assert "eval:manage" not in perm_link.PERM_GATED_ACTION_PERMS
    offenders = [a for a, s in ft.FUNCTION_ACTION_SPECS.items()
                 if s["perm_code"] == "eval:manage"]
    assert not offenders


def test_evolution_manage_action_keys_fit_db_column():
    """ai_action_key 是 varchar(255)：动作键合计（含逗号）必须装得下。"""
    keys = [a for a, s in ft.FUNCTION_ACTION_SPECS.items()
            if s["perm_code"] == "evolution:manage"]
    raw = ",".join(keys)
    assert len(raw) <= 255, f"ai_action_key 超长（{len(raw)} 字符），迁移会被截断"


# ── 2. 迁移镜像 == 注册表（不依赖 DB 也能拦漂移）─────────────────────────────

def _migration_action_keys(perm_code: str) -> tuple[str, set[str]]:
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, MIGRATION), encoding="utf-8") as fh:
        text = fh.read()
    pattern = (r"ai_action_key\s*=\s*'([^']*)'(?:(?!;)[\s\S])*?"
               r"WHERE perm_code = '" + re.escape(perm_code) + r"'")
    match = re.search(pattern, text)
    assert match, f"迁移里找不到 {perm_code} 的 ai_action_key 镜像"
    raw = match.group(1)
    return raw, {k.strip() for k in raw.split(",") if k.strip()}


def test_migration_mirrors_registry_exactly():
    for code in ("evolution:read", "evolution:manage"):
        raw, keys = _migration_action_keys(code)
        expected = {a for a, s in ft.FUNCTION_ACTION_SPECS.items() if s["perm_code"] == code}
        assert keys == expected, f"{code} 镜像漂移：DB={sorted(keys)} 注册表={sorted(expected)}"
        assert len(raw) <= 255


# ── 3. 黑盒：入参不收 id、返回不发 id / 物理列 ──────────────────────────────

def test_internal_id_args_are_rejected(monkeypatch):
    _wire_handler(monkeypatch)
    out = _payload(ft._adopt_evolution_alias({
        "term": "渠道", "target_type": "metric", "target": "案例数", "candidate_id": 3}))
    assert "不接受 AI 传值" in out["error"]


def test_queue_projection_has_no_internal_ids(monkeypatch):
    cand = {"id": 42, "term": "渠道", "kind": "new_dimension", "source": "feedback",
            "datasource_name": "", "hit_count": 3, "status": "pending",
            "created_at": None, "evidence": {}}
    monkeypatch.setattr(evo, "list_candidates", lambda **kw: [cand])
    monkeypatch.setattr(evo, "overview", lambda: {"pending_by_kind": {"new_dimension": 1}})
    monkeypatch.setattr(evo, "suggest_action", lambda c: {
        "suggested_kind": "new_dimension", "queue": "build", "tier": "act"})
    monkeypatch.setattr(kus, "list_proposals", lambda **kw: {
        "items": [{"id": 7, "title": "补一段口径", "target_type": "kb_doc",
                   "target_hint": "", "source": "chat", "datasource_name": "",
                   "created_at": "2026-11-20 10:00", "rationale": "r"}],
        "counts": {"pending": 1}})

    out = _payload(ft._list_evolution_review_queue({}))
    assert out["candidates"][0]["term"] == "渠道"
    assert "id" not in out["candidates"][0]
    assert "id" not in out["proposals"][0]
    assert "收编别名" in out["how_to_act"]


def test_dimension_draft_hides_physical_column_and_ids(monkeypatch):
    _wire_handler(monkeypatch)
    monkeypatch.setattr(ft, "_evo_find_candidate", lambda *a, **k: {
        "id": 9, "term": "渠道", "datasource_name": "es_prod"})
    monkeypatch.setattr(evo, "object_options", lambda ds: [
        {"key": "case", "display_name": "案例"}])
    monkeypatch.setattr(evo, "property_options", lambda **kw: [
        {"property": "客户等级", "column_name": "customer_level"}])
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return {"success": True, "id": 77, "name": "渠道",
                "target_column": "channel", "message": "已绑定"}

    monkeypatch.setattr(evo, "create_dimension_draft", _create)

    out = _payload(ft._create_dimension_from_evolution(
        {"term": "渠道", "object": "案例", "property": "客户等级"}))
    assert out["dimension"] == "渠道" and out["property"] == "客户等级"
    assert "target_column" not in out and "id" not in out
    assert "channel" not in json.dumps(out, ensure_ascii=False)
    # 物理列只由服务端映射：入参拿到的是业务属性名
    assert captured["property_name"] == "客户等级"


def test_unknown_object_lists_options_instead_of_guessing(monkeypatch):
    _wire_handler(monkeypatch)
    monkeypatch.setattr(ft, "_evo_find_candidate", lambda *a, **k: {
        "id": 9, "term": "渠道", "datasource_name": "es_prod"})
    monkeypatch.setattr(evo, "object_options", lambda ds: [
        {"key": "case", "display_name": "案例"}])
    out = _payload(ft._create_metric_from_evolution(
        {"term": "渠道", "object": "不存在的对象"}))
    assert "不在清单里" in out["error"] and "案例" in out["error"]


# ── 4. 候选 / 提议解析：业务名定位，歧义与缺失都给可操作原因 ─────────────────

def test_candidate_resolution_reports_ambiguity_and_missing(monkeypatch):
    rows = [
        {"id": 1, "term": "渠道", "kind": "new_dimension", "datasource_name": "a"},
        {"id": 2, "term": "渠道", "kind": "new_dimension", "datasource_name": "b"},
    ]

    def _rows(**kwargs):
        ds = str(kwargs.get("datasource_name") or "")
        return [r for r in rows if not ds or r["datasource_name"] == ds]

    monkeypatch.setattr(evo, "list_candidates", _rows)
    with pytest.raises(ValueError) as ei:
        ft._evo_find_candidate("渠道")
    assert "多个数据源" in str(ei.value)

    with pytest.raises(ValueError) as ei2:
        ft._evo_find_candidate("不存在的词")
    assert "没有业务词" in str(ei2.value) and "渠道" in str(ei2.value)

    # kind 只用于缩范围：传错 kind 不得把同词候选藏掉
    assert ft._evo_find_candidate("渠道", kind="alias", datasource="a")["id"] == 1


def test_proposal_actions_require_evidence(monkeypatch):
    _wire_handler(monkeypatch)
    out = _payload(ft._mark_evolution_proposal_done({"title": "补一段口径"}))
    assert "applied_ref 必填" in out["error"]
    out2 = _payload(ft._reject_evolution_proposal({"title": "补一段口径"}))
    assert "必须说明理由" in out2["error"]


def test_proposal_resolution_reports_ambiguous_title(monkeypatch):
    monkeypatch.setattr(kus, "list_proposals", lambda **kw: {"items": [
        {"id": 1, "title": "补口径", "target_type": "kb_doc"},
        {"id": 2, "title": "补口径", "target_type": "scenario"},
    ], "counts": {}})
    with pytest.raises(ValueError) as ei:
        ft._evo_find_proposal("补口径")
    assert "多条待审提议" in str(ei.value)


# ── 5. 双闸：少一道即拒，拒绝原因可解释 ──────────────────────────────────────

def test_capability_gate_blocks_before_touching_services(monkeypatch):
    monkeypatch.setattr(ft, "_evo_context", lambda: (_fake_ctx(), "tester"))

    def _deny(user_id, workspace_id, code, label):
        raise PermissionError(f"当前角色缺少「{code}」权限，功能「{label}」不可用")

    monkeypatch.setattr(perm_link, "require_write_perm", _deny)

    def _must_not_run(**kwargs):
        raise AssertionError("能力面被拒时不得触达业务服务")

    monkeypatch.setattr(evo, "adopt_alias", _must_not_run)
    out = _payload(ft._adopt_evolution_alias({
        "term": "渠道", "target_type": "metric", "target": "案例数"}))
    assert "evolution:manage" in out["error"]


def test_domain_gate_blocks_when_capability_is_granted(monkeypatch):
    """能力面通过但缺域码（如 metrics:manage）时也要拒 —— 双闸不是单闸。"""
    monkeypatch.setattr(ft, "_evo_context", lambda: (_fake_ctx(), "tester"))
    monkeypatch.setattr(ft, "_evo_audit", lambda *a, **k: None)

    def _deny(user_id, workspace_id, code, label):
        if code == "metrics:manage":
            raise PermissionError("当前角色缺少「metrics:manage」权限，功能「建指标」不可用")
        return None

    monkeypatch.setattr(perm_link, "require_write_perm", _deny)
    monkeypatch.setattr(evo, "create_metric_draft",
                        lambda **kw: (_ for _ in ()).throw(
                            AssertionError("域闸被拒时不得落库")))
    out = _payload(ft._create_metric_from_evolution(
        {"term": "渠道", "object": "案例"}))
    assert "metrics:manage" in out["error"]


# ── 6. 失败必须暴露 ──────────────────────────────────────────────────────────

def test_digest_failure_is_loud(monkeypatch):
    _wire_handler(monkeypatch)
    monkeypatch.setattr(evo, "run_digest",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("upstream down")))
    out = _payload(ft._run_evolution_digest({}))
    assert out["error"].startswith("归纳未完成")
    assert "RuntimeError" in out["error"] and "upstream down" in out["error"]


def test_tools_are_handlers_of_registered_actions():
    """每个动作都要能经 run_platform_function 派发到真实 handler（不是空壳）。"""
    for action in EVO_ACTIONS:
        assert callable(ft.FUNCTION_ACTION_SPECS[action]["handler"]), action