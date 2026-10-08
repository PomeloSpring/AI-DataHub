"""数据产品层（L1）回归：幂等登记 / schema 指纹 / 版本比对 / 破坏性变更显式暴露。

数据产品回答"这张表谁产出、列变了谁负责、能不能被本体引用"四件事，
是「本体只绑数据产品」的前提。锁住的行为：

1. **幂等**：按 product_name upsert，重复登记不产生重复行/重复版本（distributed-first）；
2. **指纹对列序不敏感**：列顺序变化不算 schema 变更，类型变化才算；
3. **破坏性变更显式**：删列/类型变更 → is_breaking + warnings，不得静默覆盖旧契约
   （no-silent-degradation）；
4. **身份稳定**：product_name 必填且是稳定键，内部 id 不进身份；
5. producer_kind / status 越界必须拒绝（fail-closed）。

纯函数 + fake DB，不触碰数据库。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import data_product_service as dps


# ── fake DB ─────────────────────────────────────────────────────

class _FakeDB:
    def __init__(self, select_rows=None):
        self.select_rows = select_rows or []
        self.writes = []
        self.selects = []

    def execute_query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        self.selects.append((s, params))
        if fetchone:
            return self.select_rows[0] if self.select_rows else None
        return list(self.select_rows)

    def execute_write(self, sql, params=None):
        self.writes.append((" ".join(str(sql).split()), params))
        return 1


def _patch(monkeypatch, fake):
    monkeypatch.setattr(dps, "execute_query", fake.execute_query)
    monkeypatch.setattr(dps, "execute_write", fake.execute_write)


# ── schema 指纹 ─────────────────────────────────────────────────

class TestSchemaFingerprint:
    def test_column_order_does_not_change_fingerprint(self):
        a = [{"name": "id", "type": "int"}, {"name": "name", "type": "varchar"}]
        b = [{"name": "name", "type": "varchar"}, {"name": "id", "type": "int"}]
        assert dps.schema_fingerprint(a) == dps.schema_fingerprint(b)

    def test_type_change_changes_fingerprint(self):
        a = [{"name": "id", "type": "int"}]
        b = [{"name": "id", "type": "bigint"}]
        assert dps.schema_fingerprint(a) != dps.schema_fingerprint(b)

    def test_adding_column_changes_fingerprint(self):
        a = [{"name": "id", "type": "int"}]
        b = [{"name": "id", "type": "int"}, {"name": "x", "type": "int"}]
        assert dps.schema_fingerprint(a) != dps.schema_fingerprint(b)

    def test_comment_change_does_not_change_fingerprint(self):
        """注释不是契约，改动不该触发版本升级。"""
        a = [{"name": "id", "type": "int", "comment": "旧注释"}]
        b = [{"name": "id", "type": "int", "comment": "新注释"}]
        assert dps.schema_fingerprint(a) == dps.schema_fingerprint(b)

    def test_empty_and_none_are_stable(self):
        assert dps.schema_fingerprint(None) == dps.schema_fingerprint([])
        assert len(dps.schema_fingerprint([])) == 64


# ── 差异与兼容性判定 ────────────────────────────────────────────

class TestDiffAndCompatibility:
    def test_added_only_is_backward_compatible(self):
        diff = dps._diff_columns([{"name": "a", "type": "int"}],
                                [{"name": "a", "type": "int"}, {"name": "b", "type": "int"}])
        assert diff["added_columns"] == ["b"]
        assert diff["dropped_columns"] == []
        compat, breaking = dps._judge_compatibility(diff)
        assert compat == "backward" and breaking is False

    def test_dropped_column_is_breaking(self):
        diff = dps._diff_columns([{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
                                [{"name": "a", "type": "int"}])
        assert diff["dropped_columns"] == ["b"]
        compat, breaking = dps._judge_compatibility(diff)
        assert breaking is True and compat == "none"

    def test_type_change_is_breaking(self):
        diff = dps._diff_columns([{"name": "a", "type": "int"}], [{"name": "a", "type": "varchar"}])
        assert diff["changed_columns"] == [{"name": "a", "from": "int", "to": "varchar"}]
        compat, breaking = dps._judge_compatibility(diff)
        assert breaking is True

    def test_no_change_is_full(self):
        diff = dps._diff_columns([{"name": "a", "type": "int"}], [{"name": "a", "type": "int"}])
        assert dps._judge_compatibility(diff) == ("full", False)


# ── 登记：幂等与校验 ────────────────────────────────────────────

class TestRegisterProduct:
    def test_product_name_required(self):
        with pytest.raises(ValueError, match="product_name 必填"):
            dps.register_product({"physical_table": "t_x"}, columns=[])

    def test_invalid_producer_kind_rejected(self):
        with pytest.raises(ValueError, match="producer_kind"):
            dps.register_product({"product_name": "a.b", "producer_kind": "hack"}, columns=[])

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="status"):
            dps.register_product({"product_name": "a.b", "status": "unknown"}, columns=[])

    def test_first_register_writes_version(self, monkeypatch):
        """首次登记带列集时，落 v1 版本记录。"""
        # 状态化 fake：写入前不存在，INSERT 后才查得到（否则会误走“已存在”分支）
        store = {"product": None}
        writes = []

        def q(sql, params=None, fetchone=False):
            s = " ".join(str(sql).split())
            if "adh_data_product_versions" in s:
                return None if fetchone else []
            return store["product"] if fetchone else []

        def w(sql, params=None):
            s = " ".join(str(sql).split())
            writes.append((s, params))
            if "INSERT INTO adh_data_products" in s:
                store["product"] = {"id": 1, "product_name": "ds1.t1",
                                    "schema_version": 1,
                                    "schema_hash": dps.schema_fingerprint(cols)}
            return 1

        monkeypatch.setattr(dps, "execute_query", q)
        monkeypatch.setattr(dps, "execute_write", w)

        cols = [{"name": "id", "type": "int"}, {"name": "phone", "type": "varchar"}]
        res = dps.register_product(
            {"product_name": "ds1.t1", "datasource_name": "ds1", "physical_table": "t1"},
            columns=cols, created_by="test")
        assert res["schema_changed"] is False
        assert res["version_record"]["schema_version"] == 1
        assert any("INSERT INTO adh_data_products" in x[0] for x in writes)
        assert any("INSERT INTO adh_data_product_versions" in x[0] for x in writes)

    def test_breaking_schema_change_is_surfaced(self, monkeypatch):
        """删列必须产出 is_breaking 版本 + warnings，不得静默。"""
        old_cols = [{"name": "id", "type": "int"}, {"name": "phone", "type": "varchar"}]
        new_cols = [{"name": "id", "type": "int"}]
        prev = {"id": 7, "product_name": "ds1.t1", "schema_version": 1,
                "schema_hash": dps.schema_fingerprint(old_cols),
                "datasource_name": "ds1", "physical_table": "t1",
                "classification": "internal"}

        def q(sql, params=None, fetchone=False):
            s = " ".join(str(sql).split())
            if "adh_data_product_versions" in s and fetchone:
                if "MAX(schema_version)" in s:
                    return {"m": 1}          # 供 _next_schema_version 避让版本号
                return {"columns_snapshot": old_cols}
            return prev if fetchone else []
        monkeypatch.setattr(dps, "execute_query", q)
        monkeypatch.setattr(dps, "execute_write", lambda sql, params=None: 1)

        res = dps.register_product(
            {"product_name": "ds1.t1", "datasource_name": "ds1", "physical_table": "t1"},
            columns=new_cols, created_by="test")
        assert res["schema_changed"] is True
        assert res["version_record"]["is_breaking"] is True
        assert res["version_record"]["schema_version"] == 2
        assert any("破坏性" in w for w in res["warnings"])

    def test_idempotent_register_no_schema_change(self, monkeypatch):
        """同列集重复登记：不产生新版本、不告警（幂等）。"""
        cols = [{"name": "id", "type": "int"}]
        prev = {"id": 7, "product_name": "ds1.t1", "schema_version": 1,
                "schema_hash": dps.schema_fingerprint(cols),
                "datasource_name": "ds1", "physical_table": "t1",
                "classification": "internal"}
        monkeypatch.setattr(dps, "execute_query",
                            lambda s, p=None, fetchone=False: prev if fetchone else [])
        monkeypatch.setattr(dps, "execute_write", lambda sql, params=None: 1)

        res = dps.register_product({"product_name": "ds1.t1"}, columns=cols)
        assert res["schema_changed"] is False
        assert res["version_record"] is None
        assert res["warnings"] == []


# ── dataflow 自动登记（旁路不阻断、失败显式带回）──────────────────

class TestAutoRegisterFromDataflow:
    def _df(self):
        import pandas as pd
        return pd.DataFrame({"id": [1], "phone": ["138"]})

    def test_registers_with_producer_ref(self, monkeypatch):
        from backend.modules.flow.dag import node_runners as nr
        captured = {}
        monkeypatch.setattr(
            dps, "register_product",
            lambda payload, columns=None, created_by="": (
                captured.update(payload=payload, columns=columns)
                or {"schema_changed": False, "version_record": {}, "warnings": []}))

        out = nr._register_product_after_write(
            self._df(), "dwd", "dws_case", "sync_task", "sync_task:12",
            ["stardb.t_case_records"], {"run_id": 9})
        assert out["registered"] is True
        assert out["product_name"] == "dwd.dws_case"
        assert captured["payload"]["producer_kind"] == "sync_task"
        assert captured["payload"]["producer_ref"] == "sync_task:12"
        assert [c["name"] for c in captured["columns"]] == ["id", "phone"]

    def test_failure_does_not_block_but_is_surfaced(self, monkeypatch):
        """登记失败必须显式带回 error/warnings，不得静默吞掉。"""
        from backend.modules.flow.dag import node_runners as nr

        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(dps, "register_product", _boom)

        out = nr._register_product_after_write(
            self._df(), "dwd", "dws_case", "sync_task", "sync_task:12", [], {"run_id": 9})
        assert out["registered"] is False
        assert "db down" in out["error"]
        assert any("登记失败" in w for w in out["warnings"])


# ── P3 变更治理：影响分析 / 挂起 / 审批 ──────────────────────────

class TestImpactAnalysis:
    def test_impact_follows_chain(self, monkeypatch):
        """影响面链：产品 → 本体对象 → 指标/维度/数据集。"""
        def q(sql, params=None, fetchone=False):
            s = " ".join(str(sql).split())
            if "FROM adh_data_products" in s:
                return {"product_name": "p1", "status": "draft"} if fetchone else []
            if "adh_ontology_bindings" in s:
                return [{"object_key": "case", "model_id": 1},
                        {"object_key": "user", "model_id": 1}]
            if "adh_metrics" in s:
                return [{"name": "案例数量"}]
            if "adh_dimensions" in s:
                return [{"name": "案例状态"}]
            if "adh_datasets" in s:
                return [{"name": "案例看板数据集"}]
            return [] if not fetchone else None
        monkeypatch.setattr(dps, "execute_query", q)

        imp = dps.analyze_impact("p1")
        assert [o["object_key"] for o in imp["ontology_objects"]] == ["case", "user"]
        assert imp["metrics"] == ["案例数量"]
        assert imp["dimensions"] == ["案例状态"]
        assert imp["datasets"] == ["案例看板数据集"]
        # 2 对象 + 1 指标 + 1 维度 + 1 数据集 = 5
        assert imp["total"] == 5

    def test_impact_missing_product_raises(self, monkeypatch):
        monkeypatch.setattr(dps, "execute_query",
                            lambda s, p=None, fetchone=False: None if fetchone else [])
        with pytest.raises(ValueError, match="数据产品不存在"):
            dps.analyze_impact("nope")

    def test_no_downstream_says_low_risk(self, monkeypatch):
        monkeypatch.setattr(dps, "execute_query",
                            lambda s, p=None, fetchone=False:
                            {"product_name": "p1", "status": "draft"} if fetchone else [])
        imp = dps.analyze_impact("p1")
        assert imp["total"] == 0
        assert "风险低" in imp["note"]


class TestBreakingChangeIsDeferred:
    def _prev(self, cols, version=1):
        return {"id": 7, "product_name": "ds1.t1", "schema_version": version,
                "schema_hash": dps.schema_fingerprint(cols),
                "datasource_name": "ds1", "physical_table": "t1",
                "classification": "internal"}

    def _patch(self, monkeypatch, prev, old_cols):
        def q(sql, params=None, fetchone=False):
            s = " ".join(str(sql).split())
            if "adh_data_product_versions" in s and fetchone:
                return {"columns_snapshot": old_cols}
            return prev if fetchone else []
        monkeypatch.setattr(dps, "execute_query", q)
        writes = []
        monkeypatch.setattr(dps, "execute_write",
                            lambda sql, params=None: writes.append((" ".join(str(sql).split()), params)) or 1)
        return writes

    def test_breaking_change_is_deferred_not_applied(self, monkeypatch):
        """删列默认挂起：不改产品契约，只记 pending 候选版本。"""
        old_cols = [{"name": "id", "type": "int"}, {"name": "phone", "type": "varchar"}]
        new_cols = [{"name": "id", "type": "int"}]
        writes = self._patch(monkeypatch, self._prev(old_cols), old_cols)

        res = dps.register_product({"product_name": "ds1.t1"}, columns=new_cols,
                                   created_by="test")   # on_breaking 默认 defer
        assert res["pending_approval"] is True
        assert res["version_record"]["status"] == "pending"
        assert res["version_record"]["is_breaking"] is True
        assert any("挂起" in w for w in res["warnings"])
        # 关键：不得 UPDATE 产品契约
        assert not any("UPDATE adh_data_products SET schema_hash" in w[0] for w in writes)

    def test_breaking_change_applies_only_when_explicit(self, monkeypatch):
        """on_breaking='apply' 才生效（人工明确要求的场景）。"""
        old_cols = [{"name": "id", "type": "int"}, {"name": "phone", "type": "varchar"}]
        new_cols = [{"name": "id", "type": "int"}]
        writes = self._patch(monkeypatch, self._prev(old_cols), old_cols)

        res = dps.register_product({"product_name": "ds1.t1"}, columns=new_cols,
                                   created_by="test", on_breaking="apply")
        assert res["pending_approval"] is False
        assert res["version_record"]["status"] == "applied"
        assert any("UPDATE adh_data_products SET schema_hash" in w[0] for w in writes)

    def test_backward_change_applies_without_approval(self, monkeypatch):
        """新增列向后兼容：自动生效，不挂起。"""
        old_cols = [{"name": "id", "type": "int"}]
        new_cols = [{"name": "id", "type": "int"}, {"name": "addr", "type": "varchar"}]
        writes = self._patch(monkeypatch, self._prev(old_cols), old_cols)

        res = dps.register_product({"product_name": "ds1.t1"}, columns=new_cols, created_by="test")
        assert res["pending_approval"] is False
        assert res["version_record"]["compatibility"] == "backward"
        assert any("UPDATE adh_data_products SET schema_hash" in w[0] for w in writes)


class TestContractChangeDirect:
    """契约变更直执行：propose 只做影响分析（破坏性变更 UI 确认前置），
    apply 由 dataset:manage 把关后直执行（审批回路已退役）。"""

    def _grant_dataset_manage(self, monkeypatch):
        import backend.modules.auth.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms",
                            lambda *a: {"dataset:manage": {"ai_access": "write", "ai_note": ""}})

    def test_propose_requires_valid_compatibility(self, monkeypatch):
        monkeypatch.setattr(dps, "analyze_impact", lambda n: {"total": 0})
        with pytest.raises(ValueError, match="compatibility"):
            dps.propose_contract_change("p1", 2, "hack", "x", "y")

    def test_propose_only_analyzes_no_contract_write(self, monkeypatch):
        """propose 只做影响分析，不改契约、不建审批单（无写入）。"""
        monkeypatch.setattr(dps, "analyze_impact", lambda n: {"total": 3})
        writes = []
        monkeypatch.setattr(dps, "execute_write", lambda *a, **k: writes.append(a) or 1)
        res = dps.propose_contract_change("p1", 2, "none", "删了 phone", "变更说明",
                                          requested_by="user:7")
        assert res["impact"]["total"] == 3
        assert res["payload"]["is_breaking"] == 1        # 破坏性标记供 UI 确认前置
        assert res["payload"]["product_name"] == "p1"
        assert not writes  # 不得直接改契约

    def test_propose_backward_is_not_breaking(self, monkeypatch):
        monkeypatch.setattr(dps, "analyze_impact", lambda n: {"total": 0})
        res = dps.propose_contract_change("p1", 2, "backward", "新增列", "")
        assert res["payload"]["is_breaking"] == 0

    def test_apply_requires_approved_by(self, monkeypatch):
        """审计溯源：无 approved_by 必须拒绝。"""
        monkeypatch.setattr(dps, "get_product", lambda n: {"product_name": n})
        monkeypatch.setattr(dps, "execute_query",
                            lambda s, p=None, fetchone=False: {"schema_hash": "x"})
        with pytest.raises(ValueError, match="approved_by"):
            dps.apply_contract_change("p1", 2, approved_by="")

    def test_apply_without_dataset_manage_fail_closed(self, monkeypatch):
        """直执行把关：approved_by 未持 dataset:manage 写权限即拒（fail-closed）。"""
        import backend.modules.auth.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", lambda *a: {})
        monkeypatch.setattr(dps, "get_product", lambda n: {"product_name": n})
        monkeypatch.setattr(dps, "execute_query",
                            lambda s, p=None, fetchone=False: {"schema_hash": "x"})
        writes = []
        monkeypatch.setattr(dps, "execute_write", lambda *a, **k: writes.append(a) or 1)
        with pytest.raises(PermissionError, match="dataset:manage"):
            dps.apply_contract_change("p1", 2, approved_by="user:7")
        assert not writes  # 无权限不得落库

    def test_apply_marks_pending_as_applied(self, monkeypatch):
        self._grant_dataset_manage(monkeypatch)
        writes = []
        monkeypatch.setattr(dps, "get_product", lambda n: {"product_name": n})
        monkeypatch.setattr(dps, "execute_query",
                            lambda s, p=None, fetchone=False:
                            {"schema_hash": "h", "schema_version": 2,
                             "compatibility": "none",
                             "columns_snapshot": [{"name": "id", "type": "int"}]})
        monkeypatch.setattr(dps, "execute_write",
                            lambda sql, params=None: writes.append(" ".join(str(sql).split())) or 1)
        res = dps.apply_contract_change("p1", 2, approved_by="user:1")
        assert res["approved_by"] == "user:1"
        assert any("SET status = 'applied'" in w for w in writes)
        assert any("UPDATE adh_data_products SET schema_hash" in w for w in writes)


class TestActionDeclaration:
    """动作声明仅存于本体 actions[]（建模描述，无运行时消费方）；
    执行把关已改权限码直执行，参数校验由各服务入口 fail-loud。"""

    def test_contract_change_action_declared_in_canonical(self):
        """product.contract_change 声明在系统本体 actions[]（种子脚本已退役，直接查 canonical）。"""
        from backend.common.db import execute_query
        try:
            rows = execute_query(
                "SELECT json_content FROM adh_ontology_models WHERE kind='system' AND status='active'") or []
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"元数据库不可用: {e}")
        assert any("product.contract_change" in str(r.get("json_content") or "") for r in rows), \
            "系统本体 actions[] 应含 product.contract_change 建模声明"

    def test_contract_change_payload_validated_at_service_entry(self, monkeypatch):
        """无注册表校验后，服务入口自身必须 fail-loud（兼容性枚举越界即拒）。"""
        monkeypatch.setattr(dps, "analyze_impact", lambda n: {"total": 0})
        with pytest.raises(ValueError, match="compatibility"):
            dps.propose_contract_change("p1", 2, "hack", "s", "n")

class TestPendingCandidateNotOverwritten:
    """挂起候选占版本号后，后续变更必须避让，不得覆盖 pending。"""

    def test_pending_candidate_not_overwritten(self, monkeypatch):
        """

        回归：曾因 new_version = prev+1 与挂起候选撞唯一键，
        ON DUPLICATE KEY 把 pending 覆盖成 applied，待审批清单凭空消失。
        """
        old_cols = [{"name": "id", "type": "int"}, {"name": "phone", "type": "varchar"}]
        new_cols = [{"name": "id", "type": "int"}]
        # 历史已有 v3(applied) + v4(pending 候选)
        prev = {"id": 7, "product_name": "ds1.t1", "schema_version": 3,
                "schema_hash": dps.schema_fingerprint(old_cols),
                "datasource_name": "ds1", "physical_table": "t1",
                "classification": "internal"}
        writes = []

        def q(sql, params=None, fetchone=False):
            s2 = " ".join(str(sql).split())
            if "adh_data_product_versions" in s2 and fetchone:
                return {"columns_snapshot": old_cols}
            return prev if fetchone else []
        monkeypatch.setattr(dps, "execute_query", q)
        monkeypatch.setattr(dps, "execute_write",
                            lambda sql, params=None: writes.append((" ".join(str(sql).split()), params)) or 1)
        monkeypatch.setattr(dps, "_next_schema_version", lambda name: 5)   # 避让到 v5

        res = dps.register_product({"product_name": "ds1.t1"}, columns=new_cols,
                                   created_by="test", on_breaking="apply")
        assert res["version_record"]["schema_version"] == 5
        # 不得 UPDATE 到 v4（那会覆盖 pending 候选）
        for w_sql, w_params in writes:
            if "adh_data_product_versions" in w_sql and w_params:
                assert 4 not in w_params, "不得写入已被 pending 占用的版本号"
