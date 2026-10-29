"""activate() 归档作用域回归（离线，不连库）。

WS0 解除 business kind 级单例后（源本体已退役，合并为业务本体）：
- system：平台元数据层，保持 kind 级单例（全局唯一 active）；
- business（含原 source 迁移而来）：多业务本体并存，激活只取代**同一身份
  (name + datasource_id)** 的旧 active 版本（与 list_versions 归组口径一致），
  绝不跨业务本体归档 —— 否则把 source 迁成 business 后激活其一会顶掉其它业务本体（击穿存量）。
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


def test_system_activate_stays_kind_singleton(monkeypatch):
    """system：平台元数据层，保持 kind 级单例（不带 name/datasource 过滤）。"""
    cap = _Cap()
    _activate_with(monkeypatch, "system", datasource_id=0, cap=cap)
    sql, params = _archive_stmt(cap)
    assert "kind = 'system'" in sql and "name = %s" not in sql, sql
    assert 100 in params


def test_business_activate_scopes_by_identity_not_kind_singleton(monkeypatch):
    """WS0 解除 business kind 级单例：只取代同身份(name+datasource_id)的旧版本。"""
    cap = _Cap()
    _activate_with(monkeypatch, "business", datasource_id=0, cap=cap)
    sql, params = _archive_stmt(cap)
    assert "name = %s" in sql and "datasource_id = %s" in sql, sql
    # 参数含 (now, kind, name, datasource_id, model_id)：只归档同身份版本，不跨业务本体
    assert "business" in params and "M" in params and 100 in params
