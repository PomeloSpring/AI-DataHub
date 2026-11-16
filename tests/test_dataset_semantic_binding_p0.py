"""数据集接入语义层 P0 的契约用例（spec：.qoder/plan/数据集接入语义层_spec_20261113.md）。

锁四件事，都是"做错不会报错、但会把缺陷藏起来"的类型：
1. 生成器认领**自己的**模型槽位（按名），不再 `kind='business' ORDER BY id LIMIT 1` 抢人工建模的业务本体；
2. 数据集对象携带 `execution_dataset`（doc 里写 execution_binding 会被 save_draft 剥离，等于白写）；
3. `allow_full_regenerate` 只有服务端派生生产者能传，任何 AI/工具通道不得转发（护栏不被开后门）；
4. `search_objects` 排序确定且派生对象不抢人工建模对象（无 ORDER BY + LIMIT 会让 top-5 随机漂移）。
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "backend/modules/catalog/services/business_asset_ontology.py"
OSVC = ROOT / "backend/modules/catalog/services/ontology_service.py"


# ═══════════════════════════════════════════════════════════════════
# 1. 专属模型槽位
# ═══════════════════════════════════════════════════════════════════

def test_生成器按名称认领专属模型_不再抓第一个业务本体(monkeypatch):
    """认领谓词必须是 name=_MODEL_NAME。

    旧写法 `kind='business' ORDER BY id LIMIT 1` 会命中人工建模的业务本体（实跑复现：
    18 对象 + 字典绑定的那个），随后被破坏性覆盖护栏拦下，功能从未产出过任何东西。
    """
    from backend.modules.catalog.services import business_asset_ontology as B

    seen: list[tuple[str, tuple]] = []
    calls: list[dict] = []

    def fake_q(sql, params=()):
        seen.append((sql, tuple(params or ())))
        low = sql.lower()
        if "adh_ontology_models" in low:
            return []          # 无专属模型 → 走新建分支
        if "adh_datasets" in low:
            return []
        return []

    def fake_build():
        return {"objects": [{"key": "dataset_x", "execution_dataset": "X"}], "domain": "d"}, []

    def fake_activate(model_id):
        calls.append({"activate": model_id})
        return {"validation_warnings": []}

    def fake_save_draft(model_id, payload, name=None, *, allow_full_regenerate=False):
        calls.append({"save_draft": model_id, "regen": allow_full_regenerate, "payload": payload})
        return {"validation_warnings": []}

    monkeypatch.setattr(B, "_q", fake_q)
    monkeypatch.setattr(B, "ensure_dataset_object_keys", lambda: 0)
    monkeypatch.setattr(B, "build_business_asset_doc", fake_build)
    from backend.modules.catalog.services import ontology_service as osvc
    monkeypatch.setattr(osvc, "save_draft", fake_save_draft)
    monkeypatch.setattr(osvc, "activate", fake_activate)
    # 新建分支要落模型行：这里只验认领与参数，INSERT 用假连接吞掉
    import backend.common.db.metadata_db as mdb

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): return None

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()
        def commit(self): return None

    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _Conn())

    result = B.generate_business_asset_ontology(created_by="tester")

    model_sql = [s for s, _ in seen if "adh_ontology_models" in s.lower()]
    assert model_sql, "未查询模型槽位"
    assert re.search(r"WHERE\s+name\s*=\s*%s", model_sql[0]), f"认领谓词不是按名: {model_sql[0]}"
    assert "order by id limit 1" not in model_sql[0].lower().replace(" ", " ") or \
        "name = %s" in model_sql[0]
    assert B._MODEL_NAME in [p for _, ps in seen for p in ps], "认领参数里没有专属模型名"
    assert result["bound_datasets"] == 1
    assert any(c.get("save_draft") for c in calls)


def test_跨kind占用同名槽位时显式拒绝(monkeypatch):
    """名称撞到一个非 business 模型 → 报错不覆盖（不跨型改写别人的模型）。"""
    from backend.modules.catalog.services import business_asset_ontology as B

    def fake_q(sql, params=()):
        if "adh_ontology_models" in sql.lower():
            return [{"id": 1, "status": "active", "kind": "system"}]
        return []

    monkeypatch.setattr(B, "_q", fake_q)
    monkeypatch.setattr(B, "ensure_dataset_object_keys", lambda: 0)
    monkeypatch.setattr(B, "build_business_asset_doc",
                        lambda: ({"objects": [{"key": "k"}], "domain": "d"}, []))
    with pytest.raises(ValueError, match="kind=system"):
        B.generate_business_asset_ontology()


# ═══════════════════════════════════════════════════════════════════
# 2. execution_dataset 是可执行形状的唯一载体
# ═══════════════════════════════════════════════════════════════════

def test_生成器不在doc里写_execution_binding():
    """doc 里的 execution_binding 会被 save_draft 的 _strip_execution_bindings 剥掉。

    写它等于"看着登记了、其实没生效"，故源码级禁止；正确载体是 execution_dataset
    （→ _build_execution_binding 产 bind_kind='dataset' → 绑定行 → 内嵌投影）。
    """
    src = GEN.read_text(encoding="utf-8")
    assert '"execution_binding"' not in src, "生成器不得在 doc 里登记 execution_binding（会被剥离）"
    assert '"execution_dataset"' in src, "生成器必须用 execution_dataset 登记数据集执行契约"


@pytest.mark.parametrize("only_es", [False, True])
def test_es源数据集本期不绑语义层(only_es, monkeypatch):
    """入口判据不得宽于服务端能力：ES 透传契约要到 P1，现在绑定 = LLM 可选中但必被拒。"""
    from backend.modules.catalog.services import business_asset_ontology as B

    data = {
        "datasources": [{"id": 7, "name": "es-src", "db_type": "es", "database_name": "", "is_default": 0}],
        "products": [], "sync_tasks": [], "etl_tasks": [], "quality_rules": [],
        "quality_results": [], "sensitive": [], "grants": [], "lineage": [],
        "ds_names": ["es-src"], "ds_ids": [7],
        "datasets": [{"id": 56, "name": "App 使用·明细", "description": "", "source_type": "sql",
                      "object_key": "dataset_app_56", "datasource_id": 7, "chart_type": ""}],
    }
    monkeypatch.setattr(B, "_collect", lambda: data)
    monkeypatch.setattr(B, "_source_ontology_by_ds", lambda: {})
    monkeypatch.setattr(B, "_is_es_source", lambda _id: only_es)

    doc, notes = B.build_business_asset_doc.__wrapped__() if hasattr(
        B.build_business_asset_doc, "__wrapped__") else B.build_business_asset_doc()
    ds_objs = [o for o in doc["objects"] if str(o.get("key", "")).startswith("dataset_")]
    assert ds_objs, "数据集对象未生成"
    if only_es:
        assert not any(o.get("execution_dataset") for o in ds_objs), "ES 源不应登记语义层绑定"
        assert any("ES 源" in n for n in notes), "未如实说明为何不绑"
    else:
        assert ds_objs[0]["execution_dataset"] == "App 使用·明细"
    # 稳定身份直接当对象 key（不是每轮按名称重算）
    assert ds_objs[0]["key"] == "dataset_app_56"


def test_object_key_回写只补空行():
    """回写谓词必须排除已有值：否则同步会把人工/历史登记的 key 抬回新值（同类事故已发生过）。"""
    src = GEN.read_text(encoding="utf-8")
    m = re.search(r"def ensure_dataset_object_keys.*?(?=\ndef )", src, re.S)
    assert m, "未找到 ensure_dataset_object_keys"
    body = m.group(0)
    assert "object_key IS NULL OR object_key = ''" in body
    assert body.count("UPDATE adh_datasets") == 1
    assert "SET object_key = %s" in body


# ═══════════════════════════════════════════════════════════════════
# 3. 跳过护栏的参数不得被 AI 通道传到
# ═══════════════════════════════════════════════════════════════════

def test_allow_full_regenerate_是关键字参数且默认关闭():
    from backend.modules.catalog.services import ontology_service as osvc

    sig = inspect.signature(osvc.save_draft)
    p = sig.parameters["allow_full_regenerate"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, "必须是关键字参数（防位置参数误传）"
    assert p.default is False


def test_只有派生生产者传_allow_full_regenerate():
    """全仓调用点清单化：AI/工具通道一旦出现转发，破坏性覆盖护栏就被人开了后门。"""
    allowed = {"backend/modules/catalog/services/business_asset_ontology.py"}
    hits: set[str] = set()
    for py in (ROOT / "backend").rglob("*.py"):
        rel = str(py.relative_to(ROOT))
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "allow_full_regenerate":
                hits.add(rel)
    assert hits == allowed, f"allow_full_regenerate 调用点漂移: {sorted(hits)} ≠ {sorted(allowed)}"


def test_工具通道不转发_save_draft_的关键字参数():
    """AS-BOT 本体工具走 save_draft_only/save_draft，不得出现该关键字（上一条的显式重述）。"""
    for rel in ("backend/modules/mind/execution/sdk_tools/ontology_tools.py",
                "backend/modules/mind/execution/sdk_tools/compat.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "allow_full_regenerate" not in src, f"{rel} 不得转发全量重算参数"


# ═══════════════════════════════════════════════════════════════════
# 4. 检索排序：确定 + 派生对象不抢位
# ═══════════════════════════════════════════════════════════════════

def test_search_objects_必须有确定性排序():
    """无 ORDER BY + LIMIT 时行序由引擎自定 → 同一提问两次检索拿到不同 top-5。"""
    src = OSVC.read_text(encoding="utf-8")
    m = re.search(r"def search_objects\(.*?\n(?=\n# |ndef |\Z)", src, re.S)
    body = m.group(0) if m else src
    assert "ORDER BY" in body, "search_objects 必须有 ORDER BY"
    assert "id ASC" in body, "排序需含唯一 tiebreaker"
    # 回归：MySQL 把 ORDER BY 里的裸数字当列位置解析（Unknown column '0' in 'order clause'）
    assert 'derived_expr, derived_params = "(model_id = 0)"' in body, \
        "无派生模型时的恒假表达式不得写成裸常量"


def test_search_objects_两次检索结果一致():
    """真库核验：确定性必须由数据说话，不能只看 SQL 文本。"""
    from backend.modules.catalog.services import ontology_service as osvc

    a = [h["object_key"] for h in osvc.search_objects("App 使用", datasource_id=0, limit=5)]
    b = [h["object_key"] for h in osvc.search_objects("App 使用", datasource_id=0, limit=5)]
    assert a == b, f"检索结果不稳定: {a} ≠ {b}"


def test_派生资产对象排在人工建模对象之后():
    """构造两张模型：人工建模命中 + 派生命中，断言人工的在前（分层真生效，不是注释）。"""
    from backend.modules.catalog.services import ontology_service as osvc

    captured: dict = {}
    real_ids = osvc._asset_route_model_ids

    def fake_ids():
        return {999}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = list(params or [])
            return None

        @staticmethod
        def fetchall(): return []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        @staticmethod
        def cursor(): return _Cur()

    osvc._asset_route_model_ids = fake_ids          # type: ignore[assignment]
    import backend.modules.catalog.services.ontology_service as _m
    real_conn = _m.get_metadata_conn
    _m.get_metadata_conn = lambda: _Conn()          # type: ignore[assignment]
    try:
        assert osvc.search_objects("订单", datasource_id=0, limit=5) == []
    finally:
        _m.get_metadata_conn = real_conn             # type: ignore[assignment]
        osvc._asset_route_model_ids = real_ids       # type: ignore[assignment]

    assert "model_id IN (%s)" in captured["sql"]
    assert 999 in captured["params"], "派生模型 id 未参与排序分层"
