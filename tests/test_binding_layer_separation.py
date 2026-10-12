"""B6b：execution_binding 出 doc（物理绑定唯一在部署绑定层）。

锁住的行为（GLOBAL_SEMANTIC_LAYER_DESIGN §3 源本体退役表）：
1. save_draft 迁移性剥离 doc.objects[].execution_binding（不静默，log 注明）；
2. 保存**不重算/不重写**绑定行（adh_ontology_bindings 是唯一事实，deploy_contract_bindings 管理）；
3. 展开行 execution_binding 内嵌列是派生投影：从绑定行回填，不参与写回。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402
from backend.modules.catalog.services import ontology_service  # noqa: E402

TEST_MODEL_ID = 9999999990001
TEST_MODEL_NAME = "__test_binding_layer__"


def _cleanup():
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_ontology_bindings WHERE model_id = %s", (TEST_MODEL_ID,))
            cur.execute("DELETE FROM adh_ontology_objects WHERE model_id = %s", (TEST_MODEL_ID,))
            cur.execute("DELETE FROM adh_ontology_models WHERE id = %s", (TEST_MODEL_ID,))
        conn.commit()


def _ensure_model():
    _cleanup()
    doc = {"name": TEST_MODEL_NAME, "objects": [{
        "key": "t_x", "display_name": "X", "primary_table": "t_x",
        "properties": [{"name": "id", "column": "t_x.id"}],
        "execution_binding": {"product_ref": "p.t_x", "physical_table": "t_x",
                              "column_map": {"ID": "id"}, "bind_kind": "primary"},
    }]}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_models (id, name, datasource_id, kind, status, json_content, created_at, updated_at) "
                "VALUES (%s, %s, 0, 'business', 'active', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (TEST_MODEL_ID, TEST_MODEL_NAME, json.dumps(doc, ensure_ascii=False)))
        conn.commit()
    return doc


def _saved_doc() -> dict:
    return json.loads(ontology_service.get_model(TEST_MODEL_ID)["json_content"])


class TestExecutionBindingOutOfDoc:
    def setup_method(self):
        _ensure_model()

    def teardown_method(self):
        _cleanup()

    def test_save_strips_execution_binding(self):
        doc = _ensure_model()
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        out = _saved_doc()
        assert all("execution_binding" not in o for o in out.get("objects") or [])

    def test_save_does_not_rewrite_binding_rows(self):
        """保存不动部署绑定行（唯一事实=adh_ontology_bindings，级联不越界）。"""
        doc = _ensure_model()
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_ontology_bindings (model_id, object_key, datasource_id, datasource_name, "
                    "product_ref, physical_table, column_map, bind_kind, sync_state, status, created_at, updated_at) "
                    "VALUES (%s, 't_x', 0, 'test-ds', 'test-ds.t_x', 't_x', %s, 'primary', 'bound', 'active', "
                    "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                    (TEST_MODEL_ID, json.dumps({"ID": "id"})))
            conn.commit()
        before = _binding_rows()
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        assert _binding_rows() == before

    def test_backfill_projects_from_binding_rows(self):
        """展开行内嵌列是绑定行的派生投影（回填，不写回）。"""
        doc = _ensure_model()
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_ontology_bindings (model_id, object_key, datasource_id, datasource_name, "
                    "product_ref, physical_table, column_map, bind_kind, sync_state, status, created_at, updated_at) "
                    "VALUES (%s, 't_x', 0, 'test-ds', 'test-ds.t_x', 't_x', %s, 'primary', 'bound', 'active', "
                    "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                    (TEST_MODEL_ID, json.dumps({"ID": "id"})))
            conn.commit()
        # save_draft 级联展开对象行（内嵌列在级联中从绑定行回填），再显式回填一次锁定行为
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        n = ontology_service._backfill_execution_binding(TEST_MODEL_ID)
        assert n == 1
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT execution_binding FROM adh_ontology_objects "
                    "WHERE model_id = %s AND object_key = 't_x'", (TEST_MODEL_ID,))
                row = cur.fetchone()
        assert row is not None, "save_draft 应已展开对象行"
        eb = json.loads(row["execution_binding"] or "{}")
        assert eb["physical_table"] == "t_x" and eb["product_ref"] == "test-ds.t_x"


def _binding_rows() -> list:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key, datasource_name, product_ref, physical_table, status "
                "FROM adh_ontology_bindings WHERE model_id = %s ORDER BY id", (TEST_MODEL_ID,))
            return [dict(r) for r in (cur.fetchall() or [])]
