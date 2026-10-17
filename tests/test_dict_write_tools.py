"""指标/维度字典写工具 + 字段名归一 + 图谱分图口径 回归。

背景（用户报障）：让 AS-BOT「给 user 对象加创建时间维度/指标」时它回答
"当前会话环境下无法直接修改本体模型，建议去本体建模页面操作"。核实结果：
  1. `save_metric` / `save_dimension` 的设计拍板与服务层函数（create_metric /
     create_dimension，后者 docstring 明写"供 AS-BOT 工具复用"）都在，
     **但工具层从未落地**（全仓 grep 与 `git log --all -S save_metric` 零命中）；
  2. 本体写工具对业务本体 fail-closed 是**正确**的红线（as-bot-system-waker §2），
     但技能文本还留着已退役的"所有修改需用户审批后执行"，误导模型说"要走审批流程"；
  3. 图谱徽章按 datasource_id 查图，而写入按 kind 分图 → 系统/业务两个活 kind 全误报；
  4. 源本体 doc 用旧字段名 object_key/name，RDF 转换器只认 key → 4 个对象只剩
     1 个 `Unknown` 类。
"""
import asyncio
import json

import pytest

from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
from backend.modules.mind.execution.sdk_tools import ontology_tools as ot
from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools.context import ExecutionContextVar
from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle
from backend.semantics.graph_rag.oxigraph_store import graph_target_for_model


def _run(handler, args, *, user_id=7, workspace_id=3, username="alice"):
    """在真实执行上下文里调用工具 handler（身份由服务端注入，不接受入参）。"""
    ctx = ExecutionContext(user_id=user_id, workspace_id=workspace_id,
                           username=username, user_role="admin")
    token = ExecutionContextVar.set(ctx)
    try:
        return asyncio.run(handler(args))
    finally:
        ExecutionContextVar.reset(token)


def _payload(result):
    return json.loads(result["content"][0]["text"])


# ── 注册契约（防"实现存在但未登记"同类缺陷回潮）──────────────────

def test_dict_write_tools_are_registered():
    _, tools = TOOL_SERVER_TOOLS["ontology"]
    assert {"save_metric", "save_dimension"} <= set(tools), \
        "工具未登记进 TOOL_SERVER_TOOLS → 不会注册进 MCP，LLM 无从调用"


def test_dict_write_tools_have_editor_labels():
    from backend.modules.platform.api.as_bots import _TOOL_LABELS
    for name in ("save_metric", "save_dimension"):
        assert _TOOL_LABELS.get(name), f"{name} 未登记中文标签"


def test_build_ontology_server_registers_selected_tools():
    from backend.modules.mind.execution.sdk_tools import build_tool_servers
    out = build_tool_servers("qoder", None,
                             selection={"ontology": ["save_metric", "save_dimension"]})
    assert "datahub_ontology" in out["servers"]
    assert sorted(out["allowed_tools"]) == [
        "mcp__datahub_ontology__save_dimension", "mcp__datahub_ontology__save_metric"]


def test_skill_documents_dict_write_tools():
    """技能文本必须与实际工具清单一致：曾出现"技能承诺、清单没有"导致模型空转。"""
    from backend.modules.mind.config import skill_loader
    md = (skill_loader.SKILLS_DIR / "ontology-builder" / "SKILL.md").read_text(encoding="utf-8")
    assert "save_metric" in md and "save_dimension" in md
    # 退役口径不得回潮（提议→审批→执行 已被权限码直执行取代）
    assert "所有修改需用户审批后执行" not in md


# ── save_metric ───────────────────────────────────────────────────

def test_save_metric_requires_write_perm(monkeypatch):
    def _deny(user_id, workspace_id, perm_code, label=""):
        raise PermissionError(f"缺少权限码 {perm_code}，无法{label}")
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", _deny)
    r = _run(ot.save_metric, {"display_name": "案例数量", "bound_object_key": "case",
                              "formula": "COUNT(x.id)", "agg_type": "count"})
    assert r.get("isError") and "ontology:save" in _payload(r)["error"]


def test_save_metric_rejects_missing_required_without_touching_db(monkeypatch):
    called = {}
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.create_metric",
                        lambda data: called.setdefault("called", True))
    r = _run(ot.save_metric, {"display_name": "案例数量"})
    assert r.get("isError")
    err = _payload(r)["error"]
    assert "bound_object_key" in err and "formula" in err and "agg_type" in err
    assert not called, "校验未过就调服务层 → 会写出半成品口径"


def test_save_metric_rejects_datasource_arg(monkeypatch):
    """护栏 §7/waker-datasource-domain §2：LLM 拿不到真实 id，出现即拒，不静默丢弃。"""
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)
    r = _run(ot.save_metric, {"display_name": "案例数量", "bound_object_key": "case",
                              "formula": "COUNT(x.id)", "agg_type": "count", "datasource_id": 5})
    assert r.get("isError") and "数据源" in _payload(r)["error"]


def test_save_metric_injects_ownership_server_side(monkeypatch):
    """owner/workspace/datasource 由服务端注入，不接受 LLM 传值。"""
    captured = {}
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)

    def _create(data):
        captured.update(data)
        return {"id": 42, "success": True}
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.create_metric", _create)
    monkeypatch.setattr(ot, "_dict_row",
                        lambda table, row_id: {"name": "案例数量", "target_table": "pc_case"})

    r = _run(ot.save_metric, {"display_name": "案例数量", "bound_object_key": "case",
                              "formula": "COUNT(pc_case.id)", "agg_type": "count",
                              "owner": "hacker", "workspace_id": 999, "datasource_id": 0})
    out = _payload(r)
    assert out["success"] and out["id"] == 42 and not out.get("warning")
    assert captured["owner"] == "alice" and captured["workspace_id"] == 3
    assert captured["datasource_id"] == 0, "字典是跨源口径，不得被入参改写"


def test_save_metric_reports_unbound_target_table_loudly(monkeypatch):
    """自动绑不到单一主表时必须显式告警，不能静默落一条 planner 编译不了的指标。"""
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.create_metric",
                        lambda data: {"id": 43, "success": True})
    monkeypatch.setattr(ot, "_dict_row", lambda table, row_id: {"name": "X", "target_table": ""})
    r = _run(ot.save_metric, {"display_name": "X", "bound_object_key": "case",
                              "formula": "COUNT(a.b) + COUNT(c.d)", "agg_type": "custom"})
    out = _payload(r)
    assert out["success"] and out["target_table_bound"] is False and "warning" in out


# ── save_dimension ────────────────────────────────────────────────

def test_save_dimension_resolves_column_from_business_property_name(monkeypatch):
    captured = {}
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.resolve_object_property",
                        lambda obj, prop: {"matched": True, "table": "t_user_customer",
                                            "column": "create_time", "available": []})

    def _create(data):
        captured.update(data)
        return {"id": 7, "success": True}
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.create_dimension", _create)
    monkeypatch.setattr(ot, "_dict_row",
                        lambda table, row_id: {"name": "注册日期", "target_column": "create_time"})

    r = _run(ot.save_dimension, {"display_name": "注册日期", "bound_object_key": "user",
                                 "property_name": "创建时间", "category": "时间"})
    assert _payload(r)["success"]
    assert captured["target_table"] == "t_user_customer"
    assert captured["target_column"] == "create_time"


def test_save_dimension_rejects_unmatched_property_with_candidates(monkeypatch):
    """宁缺勿错：名字对不上就回抛该对象可用属性业务名，绝不模糊绑一个列。"""
    created = {}
    monkeypatch.setattr("backend.core.perm_link.require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.resolve_object_property",
                        lambda obj, prop: {"matched": False, "table": "", "column": "",
                                           "available": ["主键ID", "工号", "创建时间"]})
    monkeypatch.setattr("backend.modules.catalog.services.metrics_service.create_dimension",
                        lambda data: created.setdefault("called", True))
    r = _run(ot.save_dimension, {"display_name": "注册日期", "bound_object_key": "user",
                                 "property_name": "注册时间"})
    assert r.get("isError")
    err = _payload(r)["error"]
    assert "创建时间" in err and "找不到属性" in err
    assert not created, "未解析成功却写了字典行 → 会产生悬空维度"


# ── 服务层：属性解析 / 字段名归一 ─────────────────────────────────

DOCS = [{"objects": [{"key": "user", "display_name": "用户", "properties": [
    {"name": "创建时间", "column": "t_user.create_time", "type": "datetime"},
    {"name": "工号", "column": "t_user.user_code"},
    {"name": "无列属性"},
]}]}]


def test_resolve_object_property_hit_miss_and_no_column(monkeypatch):
    from backend.modules.catalog.services import metrics_service as ms
    monkeypatch.setattr(ms, "_iter_active_docs", lambda: DOCS)
    hit = ms.resolve_object_property("user", "创建时间")
    assert hit["matched"] and (hit["table"], hit["column"]) == ("t_user", "create_time")
    miss = ms.resolve_object_property("user", "注册时间")
    assert not miss["matched"] and set(miss["available"]) == {"创建时间", "工号", "无列属性"}
    nocol = ms.resolve_object_property("user", "无列属性")
    assert not nocol["matched"], "属性没有物理列时不得猜一个列"
    assert not ms.resolve_object_property("case", "创建时间")["matched"]
    assert not ms.resolve_object_property("user", "")["matched"]


def test_normalize_object_field_aliases_is_idempotent_and_non_overriding():
    from backend.modules.catalog.services import ontology_service as svc
    doc = {"objects": [{"object_key": "case_stat", "name": "案例统计", "primary_table": "t"},
                       {"key": "kept", "display_name": "已有名", "name": "不该覆盖"}]}
    touched = svc.normalize_object_field_aliases(doc)
    a, b = doc["objects"]
    assert a["key"] == "case_stat" and a["display_name"] == "案例统计"
    assert b["key"] == "kept" and b["display_name"] == "已有名", "归一不得覆盖已有 canonical 值"
    assert touched
    assert svc.normalize_object_field_aliases(doc) == [], "第二次必须完全 no-op（幂等）"


# ── 图谱分图口径（徽章误报根因）───────────────────────────────────

@pytest.mark.parametrize("kind,ds,want", [
    ("system", 0, -1),          # 系统本体 datasource_id=0，但图在 ds:-1
    ("business", 1780478236183, 0),   # 业务本体图在跨源聚合图 ds:0
    ("business", 0, 0),
    ("source", 1791117518309, 1791117518309),
    ("", -1, -1),               # 未知 kind 回落哨兵推断
])
def test_graph_target_for_model(kind, ds, want):
    assert graph_target_for_model(kind, ds) == want


def test_sync_status_queries_graph_by_kind(monkeypatch):
    """徽章必须按 kind 选图；旧实现查 ds:{datasource_id} 会让系统/业务两个 kind 全误报。"""
    from backend.modules.catalog.services import ontology_kb_sync as sync
    seen = {}

    monkeypatch.setattr("backend.modules.catalog.services.ontology_service.get_model",
                        lambda mid: {"status": "active", "kind": "system",
                                     "datasource_id": 0, "object_count": 12})

    class _Client:
        def query(self, q, *a, **k):
            seen["sparql"] = " ".join(q.split())
            return [{}] * 12
    monkeypatch.setattr("backend.common.rdf.sparql_client.get_sparql_client", lambda: _Client())
    out = sync.model_sync_status(1)["graph"]
    assert "ds:-1" in seen["sparql"], f"系统本体应查 ds:-1 系统图，实际: {seen['sparql']}"
    assert out["state"] == "synced" and out["classes"] == 12


def test_source_ontology_objects_still_emit_distinct_classes():
    """源本体 doc 用旧字段名 object_key/name 时，转换器必须仍为每个对象生成独立类。"""
    doc = {"objects": [
        {"object_key": "case_stat", "name": "案例统计", "primary_table": "daily_case_stats",
         "properties": [{"column": "daily_case_stats.stat_date", "name": "统计日期"}]},
        {"object_key": "user_reg_stat", "name": "用户注册统计",
         "primary_table": "hourly_user_registration_stats",
         "properties": [{"column": "hourly_user_registration_stats.stat_time", "name": "统计时点"}]},
    ]}
    out = ontology_json_to_turtle(doc, "adh:ds:probe")
    ttl = out[0] if isinstance(out, tuple) else out
    assert "adh:obj:case_stat a owl:Class" in ttl
    assert "adh:obj:user_reg_stat a owl:Class" in ttl
    assert "Unknown" not in ttl, "对象塌成同一个 Unknown 类会让源图只剩 1 个节点"
