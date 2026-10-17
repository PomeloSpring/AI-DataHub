"""本体「写入哪张命名图」与「状态对账查哪张图」必须同一口径（离线，无 DB/图服务）。

缺陷原型（2026-10）：关系图谱页明明渲染出 200 节点，头部徽章却显示「图谱 未生成」。
根因是选图口径写了两遍：写入侧按 **kind** 分图（business→ds:0 跨源聚合图），
而对账侧直接拿 ``ds:{model.datasource_id}`` 去查 —— 业务本体在多站点下 datasource_id
是某个真实源，查到的是一张空图 → 误报 absent；系统本体(ds_id=0)则查到业务聚合图 →
误报「滞后 17/12」。两个活着的 kind 全部误报，且只有重启进程才会暴露（旧代码常驻）。

本用例把口径钉死：两处都只能经 ``graph_target_for_model``，任何一侧自己再写一套 if kind
就红。
"""
import inspect

import pytest

from backend.semantics.graph_rag import graph_builder as gb_mod
from backend.semantics.graph_rag.oxigraph_store import (
    SYSTEM_DATASOURCE_ID, graph_target_for_model,
)


# ── 1. 映射表本身 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,datasource_id,expected", [
    ("business", 1780478236183, 0),        # 业务本体：跨源聚合图（不是它的归属源图！）
    ("business", 0, 0),
    ("system", 0, SYSTEM_DATASOURCE_ID),   # 系统本体：ds:-1，不与业务图混
    ("source", 1791117518309, 1791117518309),
    ("source", 0, 0),
    ("", 0, 0),                             # 未知 kind + ds=0 → 聚合图（不猜成系统图）
    ("", SYSTEM_DATASOURCE_ID, SYSTEM_DATASOURCE_ID),   # 只有哨兵 -1 才代表系统域
    ("", 7, 7),
])
def test_graph_target_resolves_by_kind_not_datasource_id(kind, datasource_id, expected):
    assert graph_target_for_model(kind, datasource_id) == expected


def test_business_model_on_a_real_source_still_maps_to_aggregate_graph():
    """回归本条缺陷的最小场景：business + datasource_id>0 必须对到 ds:0。"""
    assert graph_target_for_model("business", 1780478236183) == 0


# ── 2. 写入侧与对账侧共用，不许各写一套 ──────────────────────────────────────

def test_builder_no_longer_reimplements_the_mapping():
    src = inspect.getsource(gb_mod.GraphBuilder._merge_ontology_models)
    assert "graph_target_for_model" in src
    # 分支自己算 target_ds 就是口径分叉的开始
    assert 'target_ds = 0' not in src and "target_ds = SYSTEM_DATASOURCE_ID" not in src


def test_status_badge_queries_the_kind_graph_not_the_datasource_graph(monkeypatch):
    from backend.common.rdf import sparql_client
    from backend.modules.catalog.services import ontology_kb_sync as sync
    from backend.modules.catalog.services import ontology_service

    model = {"id": 1, "kind": "business", "datasource_id": 1780478236183,
             "status": "active", "object_count": 17}
    monkeypatch.setattr(ontology_service, "get_model", lambda mid: dict(model))
    seen = {}

    class _FakeClient:
        def query(self, q, *a, **kw):
            seen["sparql"] = q
            return [{"s": f"obj{i}"} for i in range(17)]     # ds:0 里对象齐全

    monkeypatch.setattr(sparql_client, "get_sparql_client", lambda *a, **kw: _FakeClient())
    # kb 水位线走 DB：本用例只断言 graph 面，令其失败进容错分支（state=unknown）
    from backend.common import db
    def _boom(*a, **kw):
        raise AssertionError("离线用例不应查库")
    monkeypatch.setattr(db, "execute_query", _boom)

    out = sync.model_sync_status(1)
    assert "ds:0" in seen["sparql"], "徽章仍在查源图 → 业务本体必误报未生成"
    assert out["graph"]["state"] == "synced"
    assert out["graph"]["graph_datasource_id"] == 0          # 可诊断：到底在对哪张图


def test_system_model_is_not_compared_against_the_business_graph(monkeypatch):
    """系统本体(ds_id=0) 若按 datasource_id 查，会拿业务聚合图的类数来比 → 误报滞后。"""
    from backend.common.rdf import sparql_client
    from backend.modules.catalog.services import ontology_kb_sync as sync
    from backend.modules.catalog.services import ontology_service

    monkeypatch.setattr(ontology_service, "get_model", lambda mid: {
        "id": 2, "kind": "system", "datasource_id": 0, "status": "active", "object_count": 12})
    seen = {}

    class _FakeClient:
        def query(self, q, *a, **kw):
            seen["sparql"] = q
            return [{"s": f"o{i}"} for i in range(12)]

    monkeypatch.setattr(sparql_client, "get_sparql_client", lambda *a, **kw: _FakeClient())
    from backend.common import db
    monkeypatch.setattr(db, "execute_query",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("offline")))

    out = sync.model_sync_status(2)
    assert f"ds:{SYSTEM_DATASOURCE_ID}" in seen["sparql"]
    assert out["graph"]["state"] == "synced"
