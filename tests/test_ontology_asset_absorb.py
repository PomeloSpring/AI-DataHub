"""本体 doc 顶层指标/维度 → 字典收编（`absorb_doc_semantic_assets`）。

锁住的行为（口径归并与字典治理只有一套实现，全部复用 metrics_service 写入口）：
1. 收编成功的条目在字典建行（target_table 由部署绑定层派生、bound_object_key 挂对象），
   doc 顶层清单退役、对象补名字引用（canonical 形态，不留双口径）；
2. 幂等：重复执行不再新建，已登记行只补空字段；
3. 字典优先：同名口径已挂在别的对象上 → 记 conflict，不新建、不改别人的口径；
4. 宁缺勿错：公式列不在归属对象已登记的物理列里 → 记 failed 出声，不落"取数恒为 0"的口径，
   条目留在 doc 里（丢了连口径线索都没有）。

背景（本能力要消除的断链）：字典才是取数权威，而 doc 顶层 metrics/dimensions 两个清单
后端没有任何消费方 —— 建模页看着有 10 条指标，语义层永远回「本对象可用指标: []」。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402
from backend.modules.catalog.services import ontology_service as osvc  # noqa: E402

TEST_MODEL_ID = 9999999990007
TEST_MODEL_NAME = "__test_asset_absorb__"
# 专用假源 id：字典按 bound_object_key/对象名清理，不碰真实模型的口径
TEST_DS_ID = 990000000007
TEST_TABLE = "__test_absorb_table__"
TEST_OBJECT = "__test_absorb_obj__"
# 业务名带唯一后缀：字典名全局唯一，避免与真实口径撞名
M_NAME = "吸收测试指标__t9__"
M_EN = "absorb_test_metric_t9"
D_NAME = "吸收测试维度__t9__"
D_EN = "absorb_test_dim_t9"


def _cleanup():
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_metrics WHERE bound_object_key = %s", (TEST_OBJECT,))
            cur.execute("DELETE FROM adh_dimensions WHERE bound_object_key = %s", (TEST_OBJECT,))
            cur.execute("DELETE FROM adh_ontology_bindings WHERE model_id = %s", (TEST_MODEL_ID,))
            cur.execute("DELETE FROM adh_ontology_objects WHERE model_id = %s OR datasource_id = %s",
                        (TEST_MODEL_ID, TEST_DS_ID))
            cur.execute("DELETE FROM adh_ontology_models WHERE id = %s", (TEST_MODEL_ID,))
            cur.execute("DELETE FROM adh_column_metadata WHERE datasource_id = %s", (TEST_DS_ID,))
            cur.execute("DELETE FROM adh_table_info WHERE datasource_id = %s", (TEST_DS_ID,))
        conn.commit()


def _doc() -> dict:
    return {
        "name": TEST_MODEL_NAME, "kind": "source",
        "objects": [{
            "key": TEST_OBJECT, "display_name": "收编测试对象",
            "primary_table": TEST_TABLE,
            "properties": [
                {"name": "编码", "column": f"{TEST_TABLE}.code"},
                {"name": "金额", "column": f"{TEST_TABLE}.amount"},
            ],
        }],
        "metrics": [{
            "name": M_EN, "display_name": M_NAME, "bound_object_key": TEST_OBJECT,
            "formula": "SUM(amount)", "agg_type": "SUM", "unit": "元",
            "description": "测试口径", "aliases": ["吸收测试别名"],
        }],
        "dimensions": [{
            "name": D_EN, "display_name": D_NAME, "bound_object_key": TEST_OBJECT,
            "target_column": "code", "category": "属性", "description": "测试维度",
        }],
    }


def _setup_model(doc: dict | None = None) -> dict:
    """建 active 模型 + 展开对象 + 登记部署绑定 + 同步表/列元数据（收编的三重依赖）。"""
    _cleanup()
    doc = doc or _doc()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_models (id, name, datasource_id, kind, status, json_content, "
                "object_count, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'source', 'active', %s, 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (TEST_MODEL_ID, TEST_MODEL_NAME, TEST_DS_ID, json.dumps(doc, ensure_ascii=False)))
            cur.execute(
                "INSERT INTO adh_table_info (datasource_id, table_name, query_mode, "
                "allow_full_scan, is_active, sync_time) "
                "VALUES (%s, %s, 'raw_source', 1, 1, UTC_TIMESTAMP())",
                (TEST_DS_ID, TEST_TABLE))
            for col in ("code", "amount"):
                cur.execute(
                    "INSERT INTO adh_column_metadata (datasource_id, table_name, column_name, "
                    "data_type, column_comment, is_active, sync_time) "
                    "VALUES (%s, %s, %s, 'varchar', %s, 1, UTC_TIMESTAMP())",
                    (TEST_DS_ID, TEST_TABLE, col, f"列{col}"))
        conn.commit()
    # 展开对象 + 登记绑定行：字典写入口的 _require_object_keys / _object_binding 都靠这两层
    osvc._expand_objects(doc, TEST_MODEL_ID, TEST_DS_ID, kind="source")
    from backend.modules.catalog.services import ontology_yaml_import as _yimp
    _yimp.reconcile_deploy_bindings(doc, TEST_MODEL_ID, TEST_DS_ID)
    return doc


def _dict_rows(table: str) -> list:
    cols = ("id, name, name_en, formula, agg_type, unit, target_table, bound_object_key "
            "FROM adh_metrics") if table == "adh_metrics" else \
           ("id, name, name_en, target_table, target_column, category, bound_object_key "
            "FROM adh_dimensions")
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {cols} WHERE bound_object_key = %s OR name = %s OR name_en = %s",
                        (TEST_OBJECT, M_NAME, M_EN) if table == "adh_metrics"
                        else (TEST_OBJECT, D_NAME, D_EN))
            return [dict(r) for r in (cur.fetchall() or [])]


class TestAbsorbDocSemanticAssets:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_absorb_creates_rows_and_retires_doc(self):
        doc = _setup_model()
        out = osvc.absorb_doc_semantic_assets(doc)
        assert out["metrics_created"] == [M_NAME], out
        assert out["dimensions_created"] == [D_NAME], out
        m = _dict_rows("adh_metrics")
        assert len(m) == 1, m
        # 业务名进 name、标识进 name_en，target_table 由部署绑定层派生（缺它 planner 加载不到）
        assert m[0]["name"] == M_NAME and m[0]["name_en"] == M_EN, m
        assert m[0]["target_table"] == TEST_TABLE, m
        assert m[0]["formula"] == "SUM(amount)" and m[0]["agg_type"] == "SUM", m
        d = _dict_rows("adh_dimensions")
        assert len(d) == 1 and d[0]["target_table"] == TEST_TABLE, d
        assert d[0]["target_column"] == "code", d
        # doc 顶层清单退役，指标降级为对象级名字引用（canonical 形态，不留双口径）
        assert "metrics" not in doc and "dimensions" not in doc, doc.keys()
        assert doc["objects"][0]["metrics"] == [M_NAME], doc["objects"][0]
        assert out["retired"] == 2, out

    def test_repeat_absorb_is_idempotent(self):
        doc = _setup_model()
        osvc.absorb_doc_semantic_assets(doc)
        # 第二次拿"退役后"的 doc 再跑：无顶层条目 → 不再新建；模拟 AS-BOT 重新提交同一份旧形态 doc
        again = osvc.absorb_doc_semantic_assets(_doc())
        assert again["metrics_created"] == [] and again["dimensions_created"] == [], again
        assert again["metrics_merged"] == [M_NAME], again
        assert len(_dict_rows("adh_metrics")) == 1, "重复收编不得产生第二份同名口径"

    def test_ghost_formula_column_is_not_registered(self):
        """公式列不在对象已登记的物理列里 → 显式失败并留在 doc，不落"取数恒为 0"的口径。"""
        doc = _setup_model()
        bad = _doc()
        bad["metrics"][0]["formula"] = "SUM(not_a_column)"
        bad["metrics"][0]["name"] = "absorb_test_metric_ghost_t9"
        bad["metrics"][0]["display_name"] = M_NAME
        out = osvc.absorb_doc_semantic_assets(bad)
        assert out["metrics_created"] == [], out
        assert out["failed"] and "not_a_column" in out["failed"][0]["reason"], out["failed"]
        assert len(bad["metrics"]) == 1, "未收编的条目必须留在 doc 里（否则连线索都没了）"
        assert not bad["objects"][0].get("metrics"), bad["objects"][0]

    def test_same_name_on_other_object_is_conflict(self):
        """字典名全局唯一：同名口径挂在别的对象 → 上报冲突，不替人改名、不抢别人口径。"""
        _setup_model()
        osvc.absorb_doc_semantic_assets(_doc())
        other = _doc()
        other_key = TEST_OBJECT + "_b"
        other["objects"][0]["key"] = other["objects"][0]["object_key"] = other_key
        other["metrics"][0]["bound_object_key"] = other_key
        other["dimensions"] = []
        out = osvc.absorb_doc_semantic_assets(other)
        assert out["metrics_created"] == [], out
        assert out["conflicts"] and "全局唯一" in out["conflicts"][0]["reason"], out["conflicts"]
        assert len(other["metrics"]) == 1, "冲突条目要留在 doc 里等用户改名"

    def test_existing_row_only_fills_empty_fields(self):
        """字典优先：已登记的非空口径不被收编覆盖。"""
        doc = _setup_model()
        osvc.absorb_doc_semantic_assets(doc)
        rows = _dict_rows("adh_metrics")
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE adh_metrics SET description = %s WHERE id = %s",
                            ("人工口径说明", rows[0]["id"]))
            conn.commit()
        out = osvc.absorb_doc_semantic_assets(_doc())
        assert out["metrics_merged"] == [M_NAME], out
        after = _dict_rows("adh_metrics")
        assert len(after) == 1 and after[0]["formula"] == "SUM(amount)", after

    def test_dimension_without_physical_column_is_skipped(self):
        doc = _setup_model()
        bad = _doc()
        bad["dimensions"][0].pop("target_column")
        bad["metrics"] = []
        out = osvc.absorb_doc_semantic_assets(bad)
        assert out["dimensions_created"] == [], out
        assert out["failed"] and "target_column" in out["failed"][0]["reason"], out["failed"]
        assert len(bad["dimensions"]) == 1, out


@pytest.mark.parametrize("bad_key", ["", "__no_such_object__"])
def test_absorb_reports_object_not_in_model(bad_key):
    """归属对象不在本模型 → 冲突上报（不猜对象、不建悬空口径行）。"""
    _setup_model()
    doc = _doc()
    doc["metrics"][0]["bound_object_key"] = bad_key
    doc["dimensions"] = []
    out = osvc.absorb_doc_semantic_assets(doc)
    assert out["metrics_created"] == [] and out["conflicts"], out
    assert "不在本模型对象清单里" in out["conflicts"][0]["reason"], out["conflicts"]
    _cleanup()
