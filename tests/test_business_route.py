"""业务本体路由化回归（路由索引层：概念 → 源本体）。

锁住的行为（业务本体路由化改造）：
1. find_business_route_issues 形态/引用校验：物理绑定拒绝、route 必填、源本体
   解析（name 稳定键）、datasource_name 错配防呆、object_key 悬空、场景 route 悬空；
2. resolve_business_routes：命中路由 + 关联跨源场景、未命中不猜、canonical 失败
   抛异常（不冒充无路由，与 routes=[] 可区分）；
3. knowledge_search._attach_routes：routes=[]（真无路由）与 route_resolution=failed
   （解析降级）行为可区分（no-silent-degradation）；
4. eval 契约 check_retrieval 的 routes/route_bucket 断言（filter_hint_dims 子集比对）。
"""
from __future__ import annotations

import pytest

from services.datacatalog.services import ontology_service
from services.shared.eval.contract import check_retrieval

SRC_INDEX = {
    "源本体A": {"id": 1, "datasource_name": "ds-a", "object_keys": {"order", "site"}},
    "源本体B": {"id": 2, "datasource_name": "ds-b", "object_keys": {"user"}},
}


@pytest.fixture(autouse=True)
def _fake_source_index(monkeypatch):
    monkeypatch.setattr(ontology_service, "_active_source_ontology_index",
                        lambda: {k: {**v, "object_keys": set(v["object_keys"])}
                                 for k, v in SRC_INDEX.items()})
    monkeypatch.setattr(ontology_service, "_datasource_name_set",
                        lambda: {"ds-a", "ds-b", "test-alb"})


def _errors(doc: dict) -> list:
    return [i for i in ontology_service.find_business_route_issues(doc)
            if i["severity"] == "error"]


# ── 1. find_business_route_issues：形态与引用校验 ─────────────────────

def test_rejects_physical_binding():
    """业务对象带 primary_table / properties[].column = 形态错误（物理事实唯一在源本体）"""
    errs = _errors({"kind": "business", "objects": [{
        "key": "order", "primary_table": "t_order",
        "properties": [{"column": "t_order.id", "name": "id"}],
        "route": {"source_ontology": "源本体A", "object_key": "order"},
    }]})
    assert len(errs) == 2
    assert any("primary_table" in e["message"] for e in errs)
    assert any("properties[].column" in e["message"] for e in errs)


def test_rejects_missing_route():
    errs = _errors({"kind": "business", "objects": [{"key": "order"}]})
    assert len(errs) == 1
    assert "缺少路由目标" in errs[0]["message"]


def test_rejects_unknown_datasource_name():
    """route.datasource_name 不是系统注册数据源 → 拒绝（带可用候选）"""
    errs = _errors({"kind": "business", "objects": [{
        "key": "order", "route": {"datasource_name": "不存在的源"}}]})
    assert len(errs) == 1
    assert "不是系统注册数据源" in errs[0]["message"]


def test_accepts_datasource_only_route():
    """数据源级路由（无 source_ontology）合法——业务本体核心用途是选源"""
    errs = _errors({"kind": "business", "objects": [{
        "key": "site_jp", "route": {"mode": "source", "datasource_name": "test-alb",
                                    "filter_hints": [{"dimension": "站点", "examples": ["日本"]}]}}]})
    assert errs == []


def test_rejects_unresolvable_source_with_candidates():
    """解析不到源本体的报错要带可用候选（可操作提示）"""
    errs = _errors({"kind": "business", "objects": [{
        "key": "order", "route": {"source_ontology": "不存在的本体"}}]})
    assert len(errs) == 1
    assert "解析不到 active 源本体" in errs[0]["message"]
    assert "源本体A" in errs[0]["message"]


def test_rejects_datasource_mismatch():
    """route.datasource_name 与目标源本体实际数据源不一致 → 防错配拒绝"""
    errs = _errors({"kind": "business", "objects": [{
        "key": "order", "route": {"source_ontology": "源本体A",
                                  "datasource_name": "ds-b", "object_key": "order"}}]})
    assert len(errs) == 1
    assert "不一致" in errs[0]["message"]


def test_rejects_dangling_object_key():
    errs = _errors({"kind": "business", "objects": [{
        "key": "order", "route": {"source_ontology": "源本体A", "object_key": "ghost"}}]})
    assert len(errs) == 1
    assert "不在目标源本体" in errs[0]["message"]


def test_warns_object_filter_without_hints():
    """object_filter 无 filter_hints 是 warn 不是 error（不阻断，但要可见）"""
    issues = ontology_service.find_business_route_issues({"kind": "business", "objects": [{
        "key": "order", "route": {"mode": "object_filter",
                                  "source_ontology": "源本体A", "object_key": "order"}}]})
    assert not [i for i in issues if i["severity"] == "error"]
    assert any(i["severity"] == "warn" and "filter_hints" in i["message"] for i in issues)


def test_rejects_invalid_mode():
    errs = _errors({"kind": "business", "objects": [{
        "key": "order", "route": {"mode": "guess", "source_ontology": "源本体A"}}]})
    assert any("route.mode" in e["message"] for e in errs)


def test_rejects_scenario_dangling():
    errs = _errors({"kind": "business", "objects": [
        {"key": "order", "route": {"source_ontology": "源本体A"}}],
        "scenarios": [{"key": "s1", "title": "跨源对比", "route": {"sources": [
            {"source_ontology": "源本体A", "object_keys": ["order", "ghost"]},
            {"source_ontology": "不存在的本体"},
        ]}}]})
    assert len(errs) == 2
    assert any("ghost" in e["message"] for e in errs)
    assert any("不存在的本体" in e["message"] for e in errs)


def test_accepts_valid_route():
    """合法路由（含跨源场景）零 error"""
    errs = _errors({"kind": "business", "objects": [
        {"key": "order", "route": {"mode": "object_filter", "source_ontology": "源本体A",
                                   "datasource_name": "ds-a", "object_key": "order",
                                   "filter_hints": [{"dimension": "站点", "examples": ["日本"]}]}},
        {"key": "user", "route": {"mode": "source", "source_ontology": "源本体B"}}],
        "scenarios": [{"key": "s1", "title": "跨源对比", "route": {
            "sources": [{"source_ontology": "源本体A", "object_keys": ["order"]},
                        {"source_ontology": "源本体B", "object_keys": ["user"]}],
            "join_hint": "按公司编码关联"}}]})
    assert errs == []


# ── 2. resolve_business_routes：命中/场景/不猜/失败抛 ─────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        pass


_BIZ_DOC = {
    "kind": "business",
    "objects": [
        {"key": "order", "route": {"mode": "object_filter", "source_ontology": "源本体A",
                                   "datasource_name": "ds-a", "object_key": "order",
                                   "filter_hints": [{"dimension": "站点", "examples": ["日本"]}]}},
        {"key": "bare", "display_name": "无路由对象"},   # 无 route → 不返回（不猜）
    ],
    "scenarios": [{"key": "s1", "title": "跨源对比", "route": {
        "sources": [{"source_ontology": "源本体A", "object_keys": ["order"]}],
        "join_hint": "按公司编码关联"}}],
}


def _patch_biz_rows(monkeypatch, rows):
    import services.shared.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _FakeConn(rows))


def test_resolve_routes_with_scenario(monkeypatch):
    from services.datamind.rag.business_route import resolve_business_routes
    import json as _json
    _patch_biz_rows(monkeypatch, [
        {"name": "业务本体", "json_content": _json.dumps(_BIZ_DOC, ensure_ascii=False)}])
    routes = resolve_business_routes(["order", "bare", "ghost"])
    assert len(routes) == 1          # bare 无 route、ghost 未命中 → 不猜
    r = routes[0]
    assert r["source_ontology"] == "源本体A"
    assert r["target_object_key"] == "order"
    assert r["filter_hints"][0]["dimension"] == "站点"
    assert r["scenarios"][0]["join_hint"] == "按公司编码关联"


def test_resolve_routes_empty_input(monkeypatch):
    from services.datamind.rag.business_route import resolve_business_routes
    _patch_biz_rows(monkeypatch, [])
    assert resolve_business_routes([]) == []
    assert resolve_business_routes(["ghost"]) == []


def test_resolve_routes_db_failure_raises(monkeypatch):
    """canonical 读取失败必须抛异常（调用方标注 failed），不得返回 [] 冒充无路由"""
    import services.shared.common.db.metadata_db as mdb

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mdb, "get_metadata_conn", _boom)
    from services.datamind.rag.business_route import resolve_business_routes
    with pytest.raises(RuntimeError):
        resolve_business_routes(["order"])


# ── 3. _attach_routes：失败可区分（no-silent-degradation）──────────────

def test_attach_routes_marks_failed(monkeypatch):
    import services.datamind.rag.business_route as br
    from services.datamind.execution.sdk_tools.semantic_tools import _attach_routes

    def _boom(keys):
        raise RuntimeError("canonical down")

    monkeypatch.setattr(br, "resolve_business_routes", _boom)
    out = _attach_routes({"hit_object_keys": ["order"]})
    assert out["routes"] == []
    assert out["route_resolution"].startswith("failed")


def test_attach_routes_ok_and_no_route(monkeypatch):
    import services.datamind.rag.business_route as br
    from services.datamind.execution.sdk_tools.semantic_tools import _attach_routes

    monkeypatch.setattr(br, "resolve_business_routes", lambda keys: [{"object_key": "order"}])
    out = _attach_routes({"hit_object_keys": ["order"]})
    assert out["route_resolution"] == "ok" and out["routes"][0]["object_key"] == "order"

    monkeypatch.setattr(br, "resolve_business_routes", lambda keys: [])
    out = _attach_routes({"hit_object_keys": ["ghost"]})
    assert out["routes"] == [] and out["route_resolution"] == "ok"   # 真无路由 ≠ failed


# ── 4. eval 契约：routes / route_bucket 断言 ─────────────────────────

def test_check_retrieval_routes_subset_and_filter_dims():
    observed = {"items": [{"kind": "table", "name": "x", "text": ""}], "sources": {"route_bucket": "route_resolved"},
                "routes": [{"object_key": "order", "source_ontology": "源本体A",
                            "datasource_name": "ds-a", "target_object_key": "order",
                            "filter_hints": [{"dimension": "站点", "examples": ["日本"]}]}]}
    ok, msg = check_retrieval(observed, {"routes": [
        {"object_key": "order", "source_ontology": "源本体A", "filter_hint_dims": ["站点"]}],
        "route_bucket": "route_resolved"})
    assert ok, msg
    ok, _ = check_retrieval(observed, {"routes": [{"object_key": "order", "source_ontology": "源本体B"}]})
    assert not ok                      # 目标源不符
    ok, _ = check_retrieval(observed, {"routes": [{"object_key": "order", "filter_hint_dims": ["国家"]}]})
    assert not ok                      # filter_hint_dims 缺失
    ok, _ = check_retrieval(observed, {"route_bucket": "route_none"})
    assert not ok                      # 分桶不符
