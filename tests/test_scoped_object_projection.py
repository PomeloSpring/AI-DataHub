"""业务投影回归：对象属性/别名/字典目录不得静默变空、不得冒充全量。

锁定会话 1783069459337 的两条缺陷：
1. 属性投影只认 `display_name`，而 canonical doc 的属性键是 `name`
   → 每个对象 properties 都被过滤成 []，模型据此断言“Order 属性登记为空、
   订单没有接单方字段”，本体建得再全也永远查不出来。
2. `get_metrics(keyword=...)` 只回命中子集却不声明
   → 模型把「订单」命中的 3 个维度当成对象全量，反复判定“无法按时代天使查订单”。
"""
import json
from types import SimpleNamespace

from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm

DOC = {
    "objects": [
        {
            "key": "order",
            "display_name": "订单",
            "aliases": ["Order", "订单", "t_order_records"],
            "primary_table": "t_order_records",
            "description": "产品订购订单",
            "properties": [
                {"column": "t_order_records.id", "name": "主键ID", "type": "int"},
                {"column": "t_order_records.order_status", "name": "订单状态", "type": "varchar",
                 "enum": [{"1": "待确认"}]},
                {"column": "t_order_records.laboratory_company_name",
                 "name": "技工所企业名称", "type": "varchar"},
            ],
        }
    ]
}


def _fake_exec(sql, params=None, fetchone=False):
    if "adh_ontology_models" in sql:
        return [{"id": 7, "name": "联耀业务本体", "status": "active", "kind": "business",
                 "datasource_id": 5, "deploy_sites": None,
                 "json_content": json.dumps(DOC, ensure_ascii=False)}]
    if "adh_metrics" in sql:
        return [{"bound_object_key": "order", "name": "订单量", "name_en": "Order Count",
                 "aliases": "[]", "description": "", "unit": ""}]
    if "adh_dimensions" in sql:
        return [
            {"bound_object_key": "order", "name": "订单状态", "name_en": "",
             "aliases": "[]", "description": "", "value_labels": "{}", "category": "枚举"},
            {"bound_object_key": "order", "name": "接单技工所", "name_en": "",
             "aliases": "[]", "description": "", "value_labels": "{}", "category": "枚举"},
        ]
    return []


def _ctx():
    return SimpleNamespace(datasource_id=5, extra={})


def _patch(monkeypatch):
    monkeypatch.setattr(sm, "execute_query", _fake_exec)


def test_object_properties_project_from_name_key(monkeypatch):
    """属性键是 name（不是 display_name）时也要投影，且物理列名不得下发。"""
    _patch(monkeypatch)
    out = sm.execute("get_ontology_model", {"model_id": 7, "object_key": "order"}, _ctx())

    obj = out["objects"][0]
    assert obj["property_count"] == 3
    assert [p["name"] for p in obj["properties"]] == ["主键ID", "订单状态", "技工所企业名称"]
    assert all("column" not in p for p in obj["properties"])
    dumped = json.dumps(out, ensure_ascii=False)
    assert "t_order_records" not in dumped, "physical_table / 物理列名不得进 LLM 视野"
    assert "datasource_id" not in dumped and "primary_table" not in dumped


def test_keyword_expands_whole_object_and_declares_subset(monkeypatch):
    """keyword 只决定命中哪些对象；命中对象的目录必须是全量并显式声明。"""
    _patch(monkeypatch)
    out = sm.execute("get_metrics", {"keyword": "订单"}, _ctx())

    assert out["total"] == 1
    obj = out["objects"][0]
    assert obj["object"] == "order"
    # 命中的只有「订单状态」，但补全后必须能看到同对象的「接单技工所」
    assert {d["name"] for d in obj["dimensions"]} == {"订单状态", "接单技工所"}
    marks = {d["name"]: d["matched_by_keyword"] for d in obj["dimensions"]}
    assert marks == {"订单状态": True, "接单技工所": False}
    assert "keyword_note" in out and "全量" in out["keyword_note"]


def test_keyword_without_hit_returns_empty_not_whole_catalog(monkeypatch):
    """无命中就是空（并保留 keywords 回显），不得把所有对象当命中回给模型。"""
    _patch(monkeypatch)
    out = sm.execute("get_metrics", {"keyword": "时代天使"}, _ctx())
    assert out["total"] == 0 and out["objects"] == []
    assert out["keywords"] == ["时代天使"]


def test_object_aliases_strip_physical_table_names(monkeypatch):
    """对象别名里的 primary_table 是物理标识，必须剥除。"""
    _patch(monkeypatch)
    out = sm.execute("search_ontology", {"keyword": "订单"}, _ctx())
    obj = out["objects"][0]
    assert obj["aliases"] == ["Order", "订单"]
    assert "t_order_records" not in json.dumps(out, ensure_ascii=False)
