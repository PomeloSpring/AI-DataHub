"""activate() 归档作用域回归（离线，不连库）。

锁定修复：源本体(kind='source')按**数据源**互斥——激活某源的源本体只归档同源旧 active，
不得顶掉其它数据源的源本体（历史缺陷：为日志源建源本体把医疗 Doris 源本体挤成 archived，
且 business 激活还会删掉既有业务本体的对象）。business/system 维持 kind 级单例。
"""
import json

from backend.modules.catalog.services import ontology_service


class _Cap:
    """记录被执行的 UPDATE（归档/激活），供断言作用域。"""

    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def commit(self):
        pass

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def _activate_with(monkeypatch, kind, datasource_id, cap):
    doc = {"kind": kind, "datasource_id": datasource_id,
           "objects": [{"key": "o1", "display_name": "对象1", "properties": []}]}
    model = {"id": 100, "status": "draft", "kind": kind,
             "datasource_id": datasource_id, "domain": "", "name": "M",
             "json_content": json.dumps(doc, ensure_ascii=False)}
    monkeypatch.setattr(ontology_service, "get_model", lambda mid: dict(model))
    monkeypatch.setattr(ontology_service, "get_metadata_conn", lambda: cap)
    # 放行所有门禁（本用例只验归档作用域）
    for fn in ("normalize_object_field_aliases", "validate_doc_formats",
               "find_duplicate_object_keys", "find_unregistered_tables",
               "find_orphan_dict_bindings", "find_business_route_issues"):
        monkeypatch.setattr(ontology_service, fn, lambda *a, **k: [])
    monkeypatch.setattr(ontology_service, "find_m234_issues", lambda *a, **k: [])
    monkeypatch.setattr(ontology_service, "_expand_objects", lambda *a, **k: 1)
    monkeypatch.setattr(ontology_service, "sync_enums_to_dimensions", lambda *a, **k: None)
    ontology_service.activate(100)


def _archive_stmt(cap):
    """取归档那条 UPDATE（SET archived）。"""
    return next((sql, params) for sql, params in cap.executed if "archived" in sql)


def test_source_activate_scopes_by_datasource(monkeypatch):
    cap = _Cap()
    _activate_with(monkeypatch, "source", datasource_id=777, cap=cap)
    sql, params = _archive_stmt(cap)
    assert "kind = 'source'" in sql and "datasource_id = %s" in sql, sql
    # 参数含 (now, datasource_id, model_id)：只归档同源，不误伤其它源的源本体
    assert 777 in params and 100 in params


def test_business_activate_stays_kind_singleton(monkeypatch):
    cap = _Cap()
    _activate_with(monkeypatch, "business", datasource_id=0, cap=cap)
    sql, params = _archive_stmt(cap)
    # business 仍按 kind 级单例归档（不带 datasource_id 过滤）
    assert "kind = %s" in sql and "datasource_id = %s" not in sql, sql
    assert "business" in params
