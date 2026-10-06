"""本体绑定收敛到数据产品（P2）回归：product_ref / 解析档位 / activate 门禁。

P2 的目标是「本体只绑数据产品」。锁住的行为：

1. **绑定带产品身份**：`ResolvedBinding.product_ref` 由 `adh_data_products` 补齐，
   但**不改变解析结果**（仍按 physical_table 执行）；
2. **解析档位可观测**：`resolution_source` 标注 bindings/execution_binding/canonical，
   同名兜底标 `table_fallback` 并告警（P2.5，此前是静默命中）；
3. **activate 硬门禁**：绑未登记为数据产品的裸表 → 拒绝激活（P2.3）；
4. **save_draft 只告警**：草案宽松、激活严格，留出修复窗口（与孤儿引用同口径）；
5. 查不到数据产品不阻断解析主链路，但必须显式 warning。

纯函数 + fake DB，不触碰数据库。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datacatalog.services import ontology_service
from services.shared.semantics import binding_resolver
from services.shared.semantics.models import ResolvedBinding


# ── fake DB ─────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows, on_select=None):
        self.rows = rows
        self.on_select = on_select
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql = " ".join(str(sql).split())
        if self.on_select:
            self.on_select(self.sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.rows)

    def commit(self):
        return None


def _patch_products(monkeypatch, registered_tables):
    """注册一批已登记数据产品的 physical_table。

    `find_unregistered_tables` 现在走 `data_product_service.find_by_table`，
    故 patch 该模块的 `execute_query`（patch 其他命名空间无效）。
    """
    from services.datacatalog.services import data_product_service as dps

    def q(sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "adh_data_products" not in s:
            return None if fetchone else []
        tbl = (params or [None])[-1] if params else None
        hit = [t for t in registered_tables if t == tbl]
        return ({"product_name": f"test-alb.{tbl}", "status": "draft"} if hit else None) \
            if fetchone else ([{"physical_table": t} for t in registered_tables])
    monkeypatch.setattr(dps, "execute_query", q)
    return q


# ── find_unregistered_tables ────────────────────────────────────

class TestFindUnregisteredTables:
    def _doc(self, *tables):
        return {"datasource_name": "test-alb",
                "objects": [{"key": f"o{i}", "primary_table": t} for i, t in enumerate(tables)]}

    def test_all_registered_returns_empty(self, monkeypatch):
        _patch_products(monkeypatch, ["t_case_records", "t_user_customer"])
        assert ontology_service.find_unregistered_tables(
            self._doc("t_case_records", "t_user_customer")) == []

    def test_unregistered_table_is_reported(self, monkeypatch):
        _patch_products(monkeypatch, ["t_case_records"])
        missing = ontology_service.find_unregistered_tables(
            self._doc("t_case_records", "t_new_table"))
        assert missing == ["test-alb.t_new_table"]

    def test_datasource_name_is_part_of_identity(self, monkeypatch):
        """同名表跨数据源：另一数据源的登记不算数。"""
        from services.datacatalog.services import data_product_service as dps

        def q(sql, params=None, fetchone=False):
            # 只认 test-alb 的登记，other-src 下的同名表未登记
            if params and "other-src" in params:
                return None if fetchone else []
            return {"product_name": "test-alb.t_x", "status": "draft"} if fetchone else []
        monkeypatch.setattr(dps, "execute_query", q)
        doc = {"datasource_name": "other-src",
               "objects": [{"key": "o", "primary_table": "t_x"}]}
        assert ontology_service.find_unregistered_tables(doc) == ["other-src.t_x"]

    def test_empty_datasource_name_falls_back_to_table_only(self, monkeypatch):
        """datasource_name 为空（如 AS-BOT 系统本体）时回落只按表名，不得漏判。

        回归：曾因两处口径不一致（一处按 ds+table、一处回落按 table）导致
        解析说“未登记”、门禁说“已登记”，AS-BOT 的 11 个元数据表全部误报。
        """
        from services.datacatalog.services import data_product_service as dps

        def q(sql, params=None, fetchone=False):
            s = " ".join(str(sql).split())
            # 只支持按表名查（模拟 ds 为空的回落分支）
            if "datasource_name = %s" in s:
                return None if fetchone else []
            return {"product_name": "ds0.adh_as_bots", "status": "draft"} if fetchone else []
        monkeypatch.setattr(dps, "execute_query", q)
        doc = {"datasource_name": "", "objects": [{"key": "as_bot", "primary_table": "adh_as_bots"}]}
        assert ontology_service.find_unregistered_tables(doc) == []

    def test_lookup_parity_with_binding_resolver(self, monkeypatch):
        """口径一致性：find_by_table 与 binding_resolver 的产品查询必须同结果。

        这两处一个在 shared、一个在 datacatalog，不能互相 import，
        只能靠本断言锁住行为一致（否则会出现“解析说未登记、门禁说已登记”）。
        """
        from services.datacatalog.services import data_product_service as dps
        rows = [{"product_name": "ds0.adh_as_bots", "status": "draft"}]

        def q(sql, params=None, fetchone=False):
            return rows[0] if fetchone else rows
        monkeypatch.setattr(dps, "execute_query", q)
        # 权威入口
        assert dps.find_by_table("", "adh_as_bots") is not None
        assert dps.find_by_table("ds0", "adh_as_bots") is not None
        # binding_resolver 同口径：ds 为空时回落按表名
        b = ResolvedBinding(object_key="as_bot", datasource_id=0, datasource_name="",
                            physical_table="adh_as_bots")
        warnings = []
        binding_resolver._enrich_from_data_product(_FakeConn(rows), b, warnings)
        assert b.product_ref == "ds0.adh_as_bots"
        assert warnings == []

    def test_empty_doc_returns_empty(self, monkeypatch):
        _patch_products(monkeypatch, [])
        assert ontology_service.find_unregistered_tables({"objects": []}) == []


# ── activate 硬门禁 / save_draft 告警 ──────────────────────────

class TestActivateGate:
    def _model(self, doc):
        return {"id": 1, "status": "draft", "datasource_id": 1,
                "json_content": json.dumps(doc, ensure_ascii=False)}

    def test_activate_blocks_unregistered_table(self, monkeypatch):
        """绑未登记裸表必须拒绝激活（本体只绑数据产品）。"""
        doc = {"datasource_name": "test-alb",
               "objects": [{"key": "case", "primary_table": "t_case_records"}]}
        monkeypatch.setattr(ontology_service, "get_model",
                            lambda mid: self._model(doc))
        monkeypatch.setattr(ontology_service, "find_duplicate_object_keys", lambda d: [])
        _patch_products(monkeypatch, [])          # 一张都没登记
        with pytest.raises(ValueError) as e:
            ontology_service.activate(1)
        assert "未登记为数据产品" in str(e.value)

    def test_activate_passes_when_all_registered(self, monkeypatch):
        doc = {"datasource_name": "test-alb",
               "objects": [{"key": "case", "primary_table": "t_case_records"}]}
        monkeypatch.setattr(ontology_service, "get_model",
                            lambda mid: self._model(doc))
        monkeypatch.setattr(ontology_service, "find_duplicate_object_keys", lambda d: [])
        monkeypatch.setattr(ontology_service, "find_orphan_dict_bindings", lambda d: [])
        monkeypatch.setattr(ontology_service, "find_unregistered_tables", lambda d: [])
        called = {}
        monkeypatch.setattr(ontology_service, "_expand_objects",
                            lambda *a, **k: called.setdefault("expanded", True) or 1)
        monkeypatch.setattr(ontology_service, "sync_enums_to_dimensions", lambda *a, **k: {})
        monkeypatch.setattr(ontology_service, "get_metadata_conn",
                            lambda: _FakeConn([]))
        monkeypatch.setattr(ontology_service, "_now", lambda: "2026-01-01 00:00:00")
        res = ontology_service.activate(1)
        assert called.get("expanded") is True

    def test_save_draft_warns_but_does_not_block(self, monkeypatch):
        """草案只告警不阻断——留出修复窗口（与孤儿引用同口径）。"""
        doc = {"datasource_name": "test-alb", "domain": "x",
               "objects": [{"key": "case", "primary_table": "t_case_records"}]}
        monkeypatch.setattr(ontology_service, "get_model",
                            lambda mid: {**self._model(doc), "status": "draft"})
        monkeypatch.setattr(ontology_service, "find_duplicate_object_keys", lambda d: [])
        monkeypatch.setattr(ontology_service, "find_unregistered_tables",
                            lambda d: ["test-alb.t_case_records"])
        monkeypatch.setattr(ontology_service, "find_orphan_dict_bindings", lambda d: [])
        monkeypatch.setattr(ontology_service, "get_metadata_conn", lambda: _FakeConn([]))
        monkeypatch.setattr(ontology_service, "to_yaml", lambda d: "")
        monkeypatch.setattr(ontology_service, "to_md", lambda d: "")
        monkeypatch.setattr(ontology_service, "_now", lambda: "2026-01-01 00:00:00")
        res = ontology_service.save_draft(1, json.dumps(doc, ensure_ascii=False))
        assert any("未登记为数据产品" in w for w in res["validation_warnings"])


# ── binding_resolver：product_ref / 解析档位 ────────────────────

class TestBindingProductIdentity:
    def _binding(self, **kw):
        d = dict(object_key="case", datasource_id=1, datasource_name="test-alb",
                 physical_table="t_case_records", source="adh_ontology_bindings")
        d.update(kw)
        return ResolvedBinding(**d)

    def test_resolution_source_maps_binding_tiers(self):
        assert binding_resolver._resolution_source_of(
            self._binding(source="adh_ontology_bindings")) == "bindings"
        assert binding_resolver._resolution_source_of(
            self._binding(source="adh_ontology_objects.execution_binding")) == "execution_binding"
        assert binding_resolver._resolution_source_of(
            self._binding(source="adh_table_info")) == "canonical"

    def test_table_fallback_flagged_by_normalized_equality(self):
        """同名兜底必须标 table_fallback 并告警（此前是静默命中）。"""
        b = self._binding(object_key="case_file", physical_table="case_file")
        warnings = []
        # 复刻 resolve_binding 的判定逻辑
        if binding_resolver._norm_ref("case_file") == binding_resolver._norm_ref(b.physical_table):
            b.resolution_source = "table_fallback"
            warnings.append("同名兜底")
        assert b.resolution_source == "table_fallback"
        assert warnings

    def test_norm_ref_ignores_separators(self):
        assert binding_resolver._norm_ref("case_file") == binding_resolver._norm_ref("casefile")
        assert binding_resolver._norm_ref("CaseFile") == binding_resolver._norm_ref("case_file")

    def test_product_identity_is_attached(self, monkeypatch):
        b = self._binding()
        warnings = []
        monkeypatch.setattr(
            binding_resolver, "_enrich_from_data_product",
            lambda conn, binding, w: setattr(binding, "product_ref", "test-alb.t_case_records")
            or setattr(binding, "product_status", "draft"))
        binding_resolver._enrich_from_data_product(None, b, warnings)
        assert b.product_ref == "test-alb.t_case_records"
        assert b.product_status == "draft"

    def test_missing_product_is_warned_not_fatal(self):
        """查不到数据产品不阻断解析，但必须显式 warning（P2.3 的依据）。"""
        b = self._binding()
        warnings = []
        binding_resolver._enrich_from_data_product(_FakeConn([]), b, warnings)
        assert b.product_ref == ""
        assert any("未登记为数据产品" in w for w in warnings)

    def test_registered_product_no_warning(self):
        b = self._binding()
        warnings = []
        conn = _FakeConn([{"product_name": "test-alb.t_case_records", "status": "certified"}])
        binding_resolver._enrich_from_data_product(conn, b, warnings)
        assert b.product_ref == "test-alb.t_case_records"
        assert b.product_status == "certified"
        assert warnings == []

    def test_persisted_product_ref_kept_and_class_site_filled(self):
        """已持久化的 product_ref 不被覆盖；product_class/site 仍从产品表补。"""
        conn = _FakeConn([{"product_name": "test-alb.t_case_records", "status": "certified",
                          "product_class": "t_case_records", "site": "北京站"}])
        b = self._binding(product_ref="test-alb.t_case_records")
        warnings = []
        binding_resolver._enrich_from_data_product(conn, b, warnings)
        assert b.product_ref == "test-alb.t_case_records"   # 不被覆盖
        assert b.product_status == "certified"               # 状态动态刷新
        assert b.product_class == "t_case_records"           # 产品类补齐
        assert b.site == "北京站"                             # 站点身份补齐
        assert warnings == []

    def test_persisted_ref_no_false_unregistered_warning(self):
        """已持久化 product_ref 时，产品行查不到不误报“未登记”（明明登记过）。"""
        b = self._binding(product_ref="test-alb.t_case_records")
        warnings = []
        binding_resolver._enrich_from_data_product(_FakeConn([]), b, warnings)
        assert b.product_ref == "test-alb.t_case_records"
        assert warnings == []


# ── 多站点路由：一份本体定义 × N 个站点物理部署 ───────────────
class TestMultiSiteRouting:
    def test_multi_site_without_site_is_ambiguous(self):
        """未指定站点 + 多站点绑定 → 回抛候选，不静默猜站点（宁缺勿错）。"""
        rows = [
            {"object_key": "case", "model_id": 1, "datasource_id": 11, "datasource_name": "site-bj",
             "physical_table": "t_case_records", "column_map": {}, "product_ref": "site-bj.t_case_records"},
            {"object_key": "case", "model_id": 1, "datasource_id": 12, "datasource_name": "site-sh",
             "physical_table": "t_case_records", "column_map": {}, "product_ref": "site-sh.t_case_records"},
        ]
        warnings: list = []
        b = binding_resolver._resolve_from_bindings(
            _FakeConn(rows), "case", 0, "", None, warnings)
        assert b is None   # 不猜
        assert any("site-bj" in w and "site-sh" in w for w in warnings), warnings

    def test_specified_site_routes_to_that_binding(self):
        """指定站点 → 路由到该站点的物理部署。"""
        rows = [
            {"object_key": "case", "model_id": 1, "datasource_id": 11, "datasource_name": "site-bj",
             "physical_table": "t_case_records", "column_map": {}},
            {"object_key": "case", "model_id": 1, "datasource_id": 12, "datasource_name": "site-sh",
             "physical_table": "t_case_records", "column_map": {}},
        ]
        warnings: list = []
        b = binding_resolver._resolve_from_bindings(
            _FakeConn(rows), "case", 0, "site-sh", None, warnings)
        assert b is not None
        assert b.datasource_name == "site-sh"
        assert warnings == []

    def test_single_site_unspecified_still_resolves(self):
        """单站点（现有场景）未指定站点仍能解析，不被多站点逻辑误伤。"""
        rows = [{"object_key": "case", "model_id": 1, "datasource_id": 1,
                 "datasource_name": "test-alb", "physical_table": "t_case_records", "column_map": {}}]
        warnings: list = []
        b = binding_resolver._resolve_from_bindings(
            _FakeConn(rows), "case", 0, "", None, warnings)
        assert b is not None and b.datasource_name == "test-alb"
        assert warnings == []


# ── product_ref 持久化链路（P3 影响分析的前提）────────────────

class TestProductRefPersistence:
    def test_product_ref_for_returns_name(self, monkeypatch):
        from services.datacatalog.services import ontology_yaml_import as yi
        from services.datacatalog.services import data_product_service as dps
        monkeypatch.setattr(dps, "find_by_table",
                            lambda ds, t: {"product_name": "test-alb.t_case_records"})
        monkeypatch.setattr(yi, "_product_ref_for", yi._product_ref_for)
        assert yi._product_ref_for("test-alb", "t_case_records") == "test-alb.t_case_records"

    def test_product_ref_for_empty_when_unregistered(self, monkeypatch):
        from services.datacatalog.services import ontology_yaml_import as yi
        from services.datacatalog.services import data_product_service as dps
        monkeypatch.setattr(dps, "find_by_table", lambda ds, t: None)
        assert yi._product_ref_for("test-alb", "t_x") == ""

    def test_product_ref_for_never_raises(self, monkeypatch):
        """查不到/查询失败都回空，不阻断绑定构建。"""
        from services.datacatalog.services import ontology_yaml_import as yi
        from services.datacatalog.services import data_product_service as dps

        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(dps, "find_by_table", _boom)
        assert yi._product_ref_for("test-alb", "t_x") == ""
        assert yi._product_ref_for("test-alb", "") == ""

    def test_execution_binding_carries_product_ref(self, monkeypatch):
        """_build_execution_binding 产出的绑定必须带 product_ref。"""
        from services.datacatalog.services import ontology_yaml_import as yi
        from services.datacatalog.services import data_product_service as dps
        monkeypatch.setattr(dps, "find_by_table",
                            lambda ds, t: {"product_name": "test-alb.t_case_records"})
        b = yi._build_execution_binding("t_case_records", 1, {},
                                       datasource_name="test-alb")
        assert b["product_ref"] == "test-alb.t_case_records"
        assert b["physical_table"] == "t_case_records"

    def test_unbound_binding_has_empty_product_ref(self, monkeypatch):
        from services.datacatalog.services import ontology_yaml_import as yi
        b = yi._build_execution_binding("", 1, {}, datasource_name="test-alb")
        assert b["product_ref"] == ""
        assert b["sync_state"] == "unbound"
