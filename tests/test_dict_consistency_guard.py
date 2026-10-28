"""字典写入的一致性护栏（fail-loud）.

历史缺陷：
1. 指标口径与归属对象的物理表不符也能落库 → 取数恒为 0（错数比无答案危害大）
2. save_dimension 不传 property_name 落成无物理列的空维度 → 取数永远解析不出
两者都必须显式拒绝，不得静默落库。
"""

import pytest

from backend.modules.catalog.services.metrics_service import (
    _formula_columns,
    _require_formula_matches_object,
)


def test_formula_columns_extracts_refs_and_skips_sql_words():
    cols = _formula_columns("COUNT(DISTINCT t_case_records.id)")
    assert "id" in cols
    assert "count" not in cols and "distinct" not in cols
    # 裸列名也要抓到
    assert "source_type" in _formula_columns("SUM(source_type)")
    # 字符串字面量里的词不算列
    assert "abc" not in _formula_columns("SUM(x) WHERE name = 'abc'")


def test_metric_formula_must_match_bound_object_columns(monkeypatch):
    """口径引用了归属对象没有的列 → 必须拒绝，不得落库。"""
    monkeypatch.setattr(
        "backend.modules.catalog.services.metrics_service._object_property_columns",
        lambda key: (key, ["id", "created_at", "app_code"]),
    )
    # 合法：列都在对象上
    _require_formula_matches_object("case", "COUNT(id)")
    # 非法：source_type 不在案例对象上（这正是“集成记录数挂在 case 上”的实际翻车点）
    with pytest.raises(ValueError) as e:
        _require_formula_matches_object("case", "COUNT(id) WHERE source_type = 0")
    assert "source_type" in str(e.value)
    assert "口径" in str(e.value) or "不符" in str(e.value)


def test_metric_bound_object_without_columns_is_rejected(monkeypatch):
    """归属对象无已登记物理列时不能静默放行（否则校验形同虚设）。"""
    monkeypatch.setattr(
        "backend.modules.catalog.services.metrics_service._object_property_columns",
        lambda key: (key, []),
    )
    with pytest.raises(ValueError) as e:
        _require_formula_matches_object("ghost", "COUNT(id)")
    assert "物理列" in str(e.value)


def test_empty_formula_skips_check():
    """formula 为空由必填校验另管，这里不重复报错。"""
    _require_formula_matches_object("case", "")
    _require_formula_matches_object("case", None)


def test_save_dimension_requires_property_name(monkeypatch):
    """save_dimension 缺 property_name 必须显式拒绝（不得落成空物理列）。"""
    import asyncio
    from backend.modules.mind.execution.sdk_tools import ontology_tools as ot

    class _Ctx:
        username = "tester"
        extra = {}

    # 只测参数校验：授权把关另由 test_as_bot_execution 锁定，这里绕开以免混入权限错因
    monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr(ot, "_reject_datasource_arg", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.modules.mind.execution.sdk_tools.context.get_execution_context",
        lambda: _Ctx())

    result = asyncio.run(ot.save_dimension({
        "display_name": "测试维度", "bound_object_key": "case",
        # 故意不传 property_name
    }))
    text = "".join(b.get("text", "") for b in result.get("content", []))
    assert result.get("isError") is True
    assert "property_name" in text


def test_destructive_overwrite_is_blocked():
    """整份覆盖不得抹掉已有对象的属性/关系（读接口只回摘要，凭摘要重建会毁数据）。"""
    import json
    from backend.modules.catalog.services.ontology_service import _reject_destructive_overwrite

    current = json.dumps({"objects": [
        {"key": "case", "display_name": "案例", "properties": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
         "links": [{"to": "order"}]},
        {"key": "order", "display_name": "订单", "properties": [{"name": "x"}]},
    ]}, ensure_ascii=False)
    model = {"json_content": current}

    # 凭摘要重建：properties 全空 → 必须阻断
    rebuilt = json.dumps({"objects": [
        {"key": "case", "display_name": "案例", "properties": []},
        {"key": "order", "display_name": "订单", "properties": []},
    ]}, ensure_ascii=False)
    with pytest.raises(ValueError) as e:
        _reject_destructive_overwrite(model, rebuilt)
    assert "case" in str(e.value)

    # 整个对象被删掉 → 阻断
    dropped = json.dumps({"objects": [{"key": "order", "properties": [{"name": "x"}]}]}, ensure_ascii=False)
    with pytest.raises(ValueError):
        _reject_destructive_overwrite(model, dropped)

    # 增量追加（保留原属性、多一个对象）→ 放行
    appended = json.dumps({"objects": [
        {"key": "case", "properties": [{"name": "a"}, {"name": "b"}, {"name": "c"}], "links": [{"to": "order"}]},
        {"key": "order", "properties": [{"name": "x"}]},
        {"key": "isv", "properties": []},
    ]}, ensure_ascii=False)
    _reject_destructive_overwrite(model, appended)


# ── 本体写权限化 + 草案/发布两段式（2026-10-26 拍板）──────────────

def _tool_ctx(monkeypatch, username="tester"):
    """绕开权限层只测业务逻辑时用；授权把关另由 test_as_bot_execution 锁定。"""
    class _Ctx:
        extra = {}
    c = _Ctx(); c.username = username
    monkeypatch.setattr(
        "backend.modules.mind.execution.sdk_tools.context.get_execution_context",
        lambda: c)
    return c


def test_add_objects_is_append_only(monkeypatch):
    """add_ontology_objects 只增不删：同 key 已存在时不得覆盖。"""
    import asyncio, json
    from backend.modules.mind.execution.sdk_tools import ontology_tools as ot
    from backend.modules.catalog.services import ontology_service as osvc

    _tool_ctx(monkeypatch)
    monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr(ot, "_reject_datasource_arg", lambda *a, **k: None)
    # kind 域约束单独由 test_ontology_domain_boundary 覆盖；本用例只验“只增不删”，
    # 不桩它会让工具层去查元库（模型 id=1 不存在 → 报“本体模型不存在”而假失败）
    monkeypatch.setattr(ot, "_assert_model_editable", lambda mid: None)

    calls = {}
    monkeypatch.setattr(osvc, "get_model", lambda mid: {
        "id": mid, "status": "active",
        "json_content": json.dumps({"objects": [
            {"key": "case", "display_name": "案例", "properties": [{"name": "a"}, {"name": "b"}]}]},
            ensure_ascii=False)})
    def _fake_draft(mid, jc, name=None, created_by=""):
        calls["doc"] = json.loads(jc)
        return {"id": mid}
    monkeypatch.setattr(osvc, "save_draft_only", _fake_draft)

    result = asyncio.run(ot.add_ontology_objects({"model_id": 1, "objects": [
        {"key": "case", "display_name": "被覆盖者"},   # 已存在 → 必须跳过
        {"key": "isv", "display_name": "集成记录"},    # 新增
    ]}))
    text = "".join(b.get("text", "") for b in result.get("content", []))
    assert result.get("isError") is not True
    # 已存在的 case 属性没被抹掉（只增不删）
    case = next(o for o in calls["doc"]["objects"] if o["key"] == "case")
    assert len(case["properties"]) == 2
    assert case["display_name"] == "案例"
    assert any(o["key"] == "isv" for o in calls["doc"]["objects"])
    assert "isv" in text and "case" in text


def test_publish_requires_user_confirmation(monkeypatch):
    """publish 未带 confirmed 必须不发布，只回 ask_user 确认提示。"""
    import asyncio
    from backend.modules.mind.execution.sdk_tools import ontology_tools as ot

    _tool_ctx(monkeypatch)
    called = []
    monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: called.append("perm"))
    result = asyncio.run(ot.publish_ontology_model({"model_id": 1}))
    text = "".join(b.get("text", "") for b in result.get("content", []))
    assert "needs_user_confirmation" in text
    assert "ask_user" in text
    assert called == [], "未确认时不得进入授权/发布流程"


def test_save_ontology_model_lands_draft_not_active(monkeypatch):
    """save_ontology_model 必须走 save_draft_only（不碰生效内容、不级联）。"""
    import asyncio, json
    from backend.modules.mind.execution.sdk_tools import ontology_tools as ot
    from backend.modules.catalog.services import ontology_service as osvc

    _tool_ctx(monkeypatch)
    monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr(ot, "_reject_datasource_arg", lambda *a, **k: None)
    monkeypatch.setattr(ot, "_assert_model_editable", lambda mid: None)   # 同上：kind 判定另有用例
    used = {}
    def _fake_draft_only(*a, **k):
        used["fn"] = "draft_only"; return {"id": 1}
    def _fake_save_draft(*a, **k):
        used["fn"] = "save_draft"; return {"id": 1}
    monkeypatch.setattr(osvc, "save_draft_only", _fake_draft_only)
    monkeypatch.setattr(osvc, "save_draft", _fake_save_draft)

    asyncio.run(ot.save_ontology_model({"model_id": 1, "json_content": "{}"}))
    assert used["fn"] == "draft_only", "AS-BOT 保存必须落草案，不得直接改生效版"


def test_unpublished_dict_row_is_invisible_to_semantic_filter():
    """未发布（is_active=0）的字典行必须被语义路径过滤掉 —— 这是"不影响现有功能"的硬保证。

    planner / binding_resolver / facets 全部按 `is_active = 1` 取数，
    所以 create_metric/create_dimension 必须尊重入参的 is_active，不得硬编码 1。
    """
    import inspect
    from backend.modules.catalog.services import metrics_service as ms

    for fn in (ms.create_metric, ms.create_dimension):
        src = inspect.getsource(fn)
        # INSERT 的 is_active 位必须是占位符而非字面量 1（曾硬编码导致新建即生效）
        assert "%s, %s, %s, 0, %s, %s, %s, %s, %s, %s" in src or "data.get(\"is_active\"" in src, fn.__name__
        assert 'data.get("is_active"' in src, f"{fn.__name__} 未按入参取 is_active"


def test_save_dict_defaults_to_unpublished(monkeypatch):
    """save_metric / save_dimension 默认 publish=false → 落库必须是 is_active=0。"""
    import asyncio, json
    from backend.modules.mind.execution.sdk_tools import ontology_tools as ot
    from backend.modules.catalog.services import metrics_service as ms

    class _Ctx:
        extra = {}
        username = "tester"
    monkeypatch.setattr(
        "backend.modules.mind.execution.sdk_tools.context.get_execution_context", lambda: _Ctx())
    monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
    monkeypatch.setattr(ot, "_reject_datasource_arg", lambda *a, **k: None)

    captured = {}
    def _fake_create_dim(data):
        captured["data"] = data
        return {"id": 999}
    monkeypatch.setattr(ms, "create_dimension", _fake_create_dim)
    monkeypatch.setattr(ms, "resolve_object_property",
                        lambda k, p: {"matched": True, "table": "t", "column": "c"})

    result = asyncio.run(ot.save_dimension({
        "display_name": "测试维度", "bound_object_key": "case", "property_name": "创建时间"}))
    text = "".join(b.get("text", "") for b in result.get("content", []))
    assert captured["data"]["is_active"] == 0, "未发布必须是 is_active=0"
    assert "未发布" in text and "不影响现有取数" in text

    # 显式发布但没有 ontology:publish 权限 → 必须拒（入口的 ontology:save 放行，只拦发布）
    def _deny_publish_only(code, label):
        if code == "ontology:publish":
            raise PermissionError(f"角色未授予「{code}」权限")
    monkeypatch.setattr(ot, "_require_write_perm", _deny_publish_only)
    denied = asyncio.run(ot.save_dimension({
        "display_name": "测试维度2", "bound_object_key": "case",
        "property_name": "创建时间", "publish": True}))
    dtxt = "".join(b.get("text", "") for b in denied.get("content", []))
    assert denied.get("isError") is True and "ontology:publish" in dtxt
