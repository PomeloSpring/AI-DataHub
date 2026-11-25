"""指标/维度条目级检索（dict_index）+ get_metrics 两档语义 的单测。

锁三件事（对应两次真实事故）：
1. 发现层只回命中条目，且每对象带全量计数 —— 「子集不得冒充全量」（1783069459337：
   只回名称含“订单”的 3 个维度，模型断言对象没有「接单技工所」）；
2. 条目级 topK 取代「命中即整对象补全」—— 泛词不再一次下发十几对象/70K 字符被运行时截断
   （1783069459352）；
3. 越权过滤、BM25 不可用的显式降级都必须可诊断（no-silent-degradation）。
"""

import json

import pytest

from backend.modules.mind.rag import dict_index


@pytest.fixture(autouse=True)
def _fresh_index():
    dict_index.invalidate()
    yield
    dict_index.invalidate()


def _rows():
    return [
        {"object": "case", "kind": "metric", "name": "案例数", "name_en": "case_count",
         "aliases": ["工单数"]},
        {"object": "case", "kind": "metric", "name": "案例金额", "name_en": "case_amount",
         "aliases": []},
        {"object": "case", "kind": "dimension", "name": "案例下发渠道", "name_en": "",
         "aliases": ["来源"]},
        {"object": "patient", "kind": "dimension", "name": "患者来源渠道", "name_en": "",
         "aliases": [], "value_labels": {"1": "官网", "2": "表单提交"}},
        {"object": "order", "kind": "dimension", "name": "下单渠道", "name_en": "order_channel",
         "aliases": []},
        {"object": "order", "kind": "metric", "name": "订单数", "name_en": "order_cnt",
         "aliases": []},
    ]


def test_hits_only_and_object_totals():
    """只回命中条目；计数必须来自该对象全量（不是本页条数）。"""
    out = dict_index.rank(_rows(), ["渠道"], top_k=10)
    names = {h["name"] for h in out["hits"]}
    assert names == {"案例下发渠道", "患者来源渠道", "下单渠道"}
    # case 一共 2 指标 1 维度，命中只有 1 条 → 计数与命中条数必须可区分
    assert out["objects"]["case"] == {"metric_total": 2, "dimension_total": 1}
    assert out["objects"]["order"] == {"metric_total": 1, "dimension_total": 1}
    # 每个 hit 能回溯到原始行（调用方据此取明细，而不是把整行复制进检索结果）
    assert all(_rows()[h["idx"]]["name"] == h["name"] for h in out["hits"])


def test_alias_and_enum_labels_are_searchable():
    """别名与枚举业务名在匹配面内（与 scoped_metadata._kw_hay 同口径）。"""
    assert {h["name"] for h in dict_index.rank(_rows(), ["工单"], top_k=9)["hits"]} == {"案例数"}
    out = dict_index.rank(_rows(), ["表单提交"], top_k=9)
    assert [h["name"] for h in out["hits"]] == ["患者来源渠道"]


def test_description_never_matched():
    """口径文字（description）不在匹配面：否则两个不同概念会被误当同一个。"""
    rows = _rows()
    rows[0]["description"] = "这里写了渠道两个字，但不该被命中"
    assert all(h["name"] != "案例数" for h in dict_index.rank(rows, ["渠道"], top_k=9)["hits"])


def test_topk_truncates_but_reports_matched_total():
    out = dict_index.rank(_rows(), ["渠道"], top_k=2)
    assert len(out["hits"]) == 2 and out["truncated"] is True
    assert out["matched_total"] == 3


def test_empty_keywords_return_nothing():
    out = dict_index.rank(_rows(), [], top_k=10)
    assert out["hits"] == [] and out["matched_total"] == 0


def test_stable_order_across_calls():
    a = [h["name"] for h in dict_index.rank(_rows(), ["渠道"], top_k=10)["hits"]]
    b = [h["name"] for h in dict_index.rank(_rows(), ["渠道"], top_k=10)["hits"]]
    assert a == b


def test_allowed_keys_filter_runs_before_ranking(monkeypatch):
    """越权对象不得靠“先全量排序、后过滤”混进来。"""
    def query(sql, params=(), **kw):
        if "adh_metrics" in sql:
            return [
                {"bound_object_key": "case", "name": "案例数", "name_en": "", "aliases": "[]"},
                {"bound_object_key": "secret", "name": "秘密案例数", "name_en": "", "aliases": "[]"},
            ]
        return []
    monkeypatch.setattr(dict_index, "execute_query", query)
    out = dict_index.search(["案例"], datasource_id=7, allowed_keys={"case"}, top_k=10)
    assert set(out["objects"]) == {"case"}
    assert all(h["object"] == "case" for h in out["hits"])


def test_bm25_failure_degrades_explicitly(monkeypatch):
    """索引建不起来必须标 engine=substring_fallback，不能静默当“查不到”。"""
    def boom(self, docs):
        raise RuntimeError("index broken")
    monkeypatch.setattr(dict_index.BM25, "index", boom)
    out = dict_index.rank(_rows(), ["渠道"], top_k=10)
    assert out["engine"] == "substring_fallback"
    assert len(out["hits"]) == 3  # 子串匹配仍可用


# ── 生产路径 scoped_metadata（L1 检索 / L2 下钻） ────────────────────

def _patch_scoped(monkeypatch):
    from backend.modules.mind.execution.sdk_tools import scoped_metadata
    def query(sql, params=(), **kw):
        if "adh_ontology_models" in sql:
            return [{"id": 3, "name": "案例模型", "status": "active", "kind": "business",
                     "datasource_id": 7, "deploy_sites": None, "json_content": {"objects": [
                         {"key": "case", "display_name": "案例"},
                         {"key": "order", "display_name": "订单"}]}}]
        if "adh_metrics" in sql:
            return [
                {"bound_object_key": "case", "name": "案例数", "name_en": "case_count",
                 "aliases": [], "description": "", "unit": "个"},
                {"bound_object_key": "case", "name": "案例金额", "name_en": "case_amount",
                 "aliases": [], "description": "", "unit": "元"},
                {"bound_object_key": "order", "name": "订单数", "name_en": "order_cnt",
                 "aliases": [], "description": "", "unit": "个"},
            ]
        if "adh_dimensions" in sql:
            return [
                {"bound_object_key": "case", "name": "案例下发渠道", "name_en": "",
                 "aliases": [], "description": "", "value_labels": json.dumps({"1": "官网"}),
                 "category": "文本"},
            ]
        return []
    monkeypatch.setattr(scoped_metadata, "execute_query", query)
    return scoped_metadata


def test_scoped_keyword_mode_returns_hits_not_whole_object(monkeypatch):
    from backend.modules.mind.execution.sdk_tools.context import ExecutionContext

    sm = _patch_scoped(monkeypatch)
    out = sm.execute("get_metrics", {"keyword": "渠道"}, ExecutionContext(datasource_id=7))
    case = next(o for o in out["objects"] if o["object"] == "case")
    # 命中 1 条维度，不是整对象 2 指标 + 1 维度
    assert len(case["dimensions"]) == 1 and case["metrics"] == []
    assert case["metric_total"] == 2 and case["dimension_total"] == 1
    assert out["search"]["engine"] == "bm25" and "truncated" in out["search"]


def test_scoped_object_mode_returns_full_and_rejects_unknown(monkeypatch):
    from backend.modules.mind.execution.sdk_tools.context import ExecutionContext

    sm = _patch_scoped(monkeypatch)
    out = sm.execute("get_metrics", {"object": "case"}, ExecutionContext(datasource_id=7))
    assert out["mode"] == "object_full"
    case = out["objects"][0]
    assert len(case["metrics"]) == 2 and len(case["dimensions"]) == 1

    with pytest.raises(PermissionError):
        sm.execute("get_metrics", {"object": "not_exists"}, ExecutionContext(datasource_id=7))


# ── knowledge_search 的字典候选校验（KB 出候选，字典出裁决） ──────────

def test_attach_dict_candidates_returns_verified_names(monkeypatch):
    from backend.modules.mind.execution.sdk_tools import semantic_tools as st

    monkeypatch.setattr(dict_index, "search", lambda *a, **kw: {"hits": [{
        "object": "case", "kind": "dimension", "name": "案例下发渠道", "name_en": "",
        "aliases": [], "enum_labels": ["官网"], "matched_by": ["渠道"]}]})
    res = st._attach_dict_candidates(
        {"hit_object_keys": ["case"], "count": 2}, "按渠道看案例", 7)
    assert res["dict_verify"] == "ok"
    assert res["dict_candidates"][0]["name"] == "案例下发渠道"


def test_attach_dict_candidates_empty_guides_to_get_metrics(monkeypatch):
    """KB 无法把问题词接到已登记名称：必须显式引导，不能默认“平台上没有”。"""
    from backend.modules.mind.execution.sdk_tools import semantic_tools as st

    monkeypatch.setattr(dict_index, "search", lambda *a, **kw: {"hits": []})
    res = st._attach_dict_candidates({"hit_object_keys": ["case"]}, "时代天使的订单", 7)
    assert res["dict_verify"] == "empty"
    assert "get_metrics" in res["dict_note"]


def test_attach_dict_candidates_marks_stale_docs(monkeypatch):
    from backend.modules.mind.execution.sdk_tools import semantic_tools as st

    monkeypatch.setattr(dict_index, "search", lambda *a, **kw: {"hits": []})
    res = st._attach_dict_candidates(
        {"hit_object_keys": ["case"], "doc_stale": True}, "订单口径", 7)
    assert "doc_stale" in res["dict_warning"]


def test_attach_dict_candidates_failure_is_visible(monkeypatch):
    """校验失败不得扮成“已验证”或“无候选”（no-silent-degradation）。"""
    from backend.modules.mind.execution.sdk_tools import semantic_tools as st

    def boom(*a, **kw):
        raise RuntimeError("dict down")
    monkeypatch.setattr(dict_index, "search", boom)
    res = st._attach_dict_candidates({"hit_object_keys": ["case"]}, "订单", 7)
    assert res["dict_verify"].startswith("failed:")