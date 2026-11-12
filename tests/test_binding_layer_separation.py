"""B6b + 页面级联登记：execution_binding 出 doc，物理绑定唯一在部署绑定层，
而**发布/保存级联必须真的把绑定行登记上**（页面能力不断链）。

锁住的行为（GLOBAL_SEMANTIC_LAYER_DESIGN §3 源本体退役表 + 建模规范 §1/§2）：
1. save_draft 迁移性剥离 doc.objects[].execution_binding（不静默，log 注明）；
2. 级联按 doc 声明的形状对账登记模型**自有源**的绑定行（缺则登记、幂等不重复、
   对象删除则废弃），跨站部署行与其它源的行一律不碰；
3. 展开行 execution_binding 内嵌列是派生投影：从绑定行回填，不参与写回；
4. 运维护栏列（max_rows/permission_tokens/masked_columns 等）不被发布覆盖；
   派生映射本身含幽灵列时不覆盖现有列映射（宁缺勿错，改 doc 后再发布）；
5. 「列待修」有可执行的修复入口：`GET /models/{id}/objects/{key}/columns` 给出与漂移裁决
   同源的实存列候选（前端在「编辑对象」弹窗里用选择框改列）。

历史断链（本文件第 2 条取代旧"保存不重写绑定行"口径的原因）：B6b 之后登记口只剩
import-yaml 与离线脚本，建模页「编辑对象→绑定物理表→保存草案→发布」什么都不写，
而页面文案承诺"发布后在部署绑定层登记物理表" —— 对象常年显示「未绑定物理表·无法取数」，
用户只能改库或跑脚本。功能承诺与实际能力必须一致，故改为级联登记并在此锁定。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402
from backend.modules.catalog.services import ontology_service  # noqa: E402

TEST_MODEL_ID = 9999999990001
TEST_MODEL_NAME = "__test_binding_layer__"
# 必须用 kind='source' + 专用假 datasource_id：`_expand_objects` 的互斥域对
# business/system 是“同 kind 全域下线其它模型的对象行”（产品语义：业务本体同域只有一套
# active 对象）。本测试跑在真实元库上，若建成 business 会当场把真实业务本体（联耀）
# 的 17 条展开行删掉且 teardown 无法回滚——已踩过两次。source 域按 datasource_id 互斥，
# 用一个不存在的源 id 就能把副作用限制在本测试自己造的行里。
TEST_DS_ID = 990000000001


def _cleanup():
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_ontology_bindings WHERE model_id = %s", (TEST_MODEL_ID,))
            cur.execute("DELETE FROM adh_ontology_objects WHERE model_id = %s OR datasource_id = %s",
                        (TEST_MODEL_ID, TEST_DS_ID))
            cur.execute("DELETE FROM adh_ontology_models WHERE id = %s", (TEST_MODEL_ID,))
            # 列候选用例造的元数据行只属于假源 id，按源清理不会动真实元数据
            cur.execute("DELETE FROM adh_column_metadata WHERE datasource_id = %s", (TEST_DS_ID,))
        conn.commit()


def _doc(primary_table: str = "t_x", column: str = "t_x.id") -> dict:
    return {"name": TEST_MODEL_NAME, "objects": [{
        "key": "t_x", "display_name": "X", "primary_table": primary_table,
        "properties": [{"name": "id", "column": column}],
        # 旧格式内嵌绑定：save_draft 必须剥掉它（doc 不是物理绑定的可编辑源）
        "execution_binding": {"product_ref": "p.t_x", "physical_table": "t_x",
                              "column_map": {"ID": "id"}, "bind_kind": "primary"},
    }]}


def _ensure_model(doc: dict | None = None):
    _cleanup()
    doc = doc if doc is not None else _doc()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_models (id, name, datasource_id, kind, status, json_content, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'source', 'active', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (TEST_MODEL_ID, TEST_MODEL_NAME, TEST_DS_ID, json.dumps(doc, ensure_ascii=False)))
        conn.commit()
    return doc


def _saved_doc() -> dict:
    return json.loads(ontology_service.get_model(TEST_MODEL_ID)["json_content"])


def _binding_rows(datasource_id: int | None = None) -> list:
    sql = ("SELECT object_key, datasource_id, datasource_name, product_ref, physical_table, "
           "column_map, bind_kind, sync_state, status, max_rows, permission_tokens, masked_columns, "
           "query_mode, size_class, allow_full_scan "
           "FROM adh_ontology_bindings WHERE model_id = %s")
    params: list = [TEST_MODEL_ID]
    if datasource_id is not None:
        sql += " AND datasource_id = %s"
        params.append(datasource_id)
    sql += " ORDER BY id"
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in (cur.fetchall() or [])]


def _insert_row(datasource_id: int, object_key: str = "t_x", *, physical_table: str = "t_x",
                column_map: dict | None = None, status: str = "active", **ops) -> None:
    """种一条绑定行（16 个占位符与列清单逐一对应；产品名在 Python 侧拼，
    不写进 SQL 字面量——'%s' 出现在引号里同样会被 pymysql 当占位符吃掉参数）。"""
    ops = {"max_rows": None, "permission_tokens": "[]", "masked_columns": "[]",
           "query_mode": "raw_source", "size_class": None, "allow_full_scan": 1,
           "sync_state": "bound", **ops}
    params = (TEST_MODEL_ID, object_key, datasource_id, "test-ds",
              f"test-ds.{object_key}", physical_table,
              json.dumps(column_map if column_map is not None else {"ID": "id"}, ensure_ascii=False),
              "primary", ops["query_mode"], ops["size_class"], ops["allow_full_scan"],
              ops["max_rows"], ops["permission_tokens"], ops["masked_columns"],
              ops["sync_state"], status)
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_bindings (model_id, object_key, datasource_id, datasource_name, "
                "product_ref, physical_table, column_map, bind_kind, query_mode, size_class, allow_full_scan, "
                "max_rows, permission_tokens, masked_columns, sync_state, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))", params)
            conn.commit()


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

    def test_publish_registers_own_source_binding_row(self):
        """级联登记：doc 声明了 primary_table 的对象，发布后必须有自有源的 active 绑定行。

        这是「页面发布完还是未绑定物理表」那条断链的回归位：徽标与取数都以绑定行为事实源。
        """
        doc = _ensure_model()
        assert _binding_rows(TEST_DS_ID) == []
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        rows = _binding_rows(TEST_DS_ID)
        assert len(rows) == 1, f"发布应登记 1 条自有源绑定行，实际 {rows}"
        r = rows[0]
        assert r["status"] == "active" and r["object_key"] == "t_x"
        assert r["physical_table"] == "t_x" and r["bind_kind"] == "primary"
        # 归属源用 id 断言：本测试的 datasource 是假 id（元库里没有该行），
        # 数据源业务名按"name 为稳定关联键"从 doc/数据源表取，取不到才留空。
        assert r["datasource_id"] == TEST_DS_ID

    def test_repeated_publish_is_idempotent(self):
        """幂等：反复发布不产生副本、不刷掉已登记行（页面重复点发布是常态）。"""
        doc = _ensure_model()
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        first = _binding_rows(TEST_DS_ID)
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        second = _binding_rows(TEST_DS_ID)
        assert len(second) == len(first) == 1
        assert second[0]["physical_table"] == first[0]["physical_table"]

    def test_other_datasource_rows_untouched(self):
        """级联不越界：别的源（含 ds=0 遗留行）与跨站部署行一律不动。"""
        doc = _ensure_model()
        _insert_row(0)                      # 非本模型自有源的行（旧口径遗留）
        _insert_row(990000000002, "t_x")    # 假想的跨站部署行
        before = _binding_rows()
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        after = _binding_rows()
        kept = [r for r in after if r["datasource_id"] != TEST_DS_ID]
        assert kept == [r for r in before if r["datasource_id"] != TEST_DS_ID], (
            "级联只该管模型自有源的绑定行，越界改别的源的行 = 历史四站取数打废事故复发")

    def test_object_removed_from_model_deprecates_row(self):
        """模型里删掉的对象，其自有源绑定行必须废弃（不留悬空 active 绑定）。

        直接打对账入口：save_draft 有"整份覆盖不得丢对象属性"的破坏性拦截
        （_reject_destructive_overwrite），拿它测废弃分支会被前置护栏挡在门外。
        """
        from backend.modules.catalog.services import ontology_yaml_import as yimp

        _ensure_model()
        assert len(_binding_rows(TEST_DS_ID)) == 0
        named = _doc()
        named["datasource_name"] = "test-ds"     # doc 声明的业务名必须随绑定行落库（稳定关联键）
        stats = yimp.reconcile_deploy_bindings(named, TEST_MODEL_ID, TEST_DS_ID)
        assert stats["registered"] == ["t_x"], stats
        assert stats["datasource_name"] == "test-ds", stats
        assert _binding_rows(TEST_DS_ID)[0]["datasource_name"] == "test-ds"
        assert len(_binding_rows(TEST_DS_ID)) == 1
        gone = {"objects": [{"key": "t_other", "display_name": "Y",
                             "primary_table": "t_other", "properties": []}]}
        stats = yimp.reconcile_deploy_bindings(gone, TEST_MODEL_ID, TEST_DS_ID)
        assert stats["deprecated"] == ["t_x"], stats
        rows = _binding_rows(TEST_DS_ID)
        assert [r["object_key"] for r in rows if r["status"] == "active"] == ["t_other"]
        assert any(r["status"] == "deprecated" for r in rows), "被删对象的行应转 deprecated 留痕"

    def test_operational_columns_not_overwritten(self):
        """发布改业务形状，不替人重配护栏：max_rows/权限令牌/脱敏列必须原样保留。"""
        _ensure_model()
        _insert_row(TEST_DS_ID, column_map={"ID": "id"}, max_rows=5000,
                    permission_tokens='["tenant"]', masked_columns='["phone"]')
        before = _binding_rows(TEST_DS_ID)[0]
        assert before["max_rows"] == 5000
        doc = _doc(primary_table="t_x")
        doc["objects"][0].pop("execution_binding", None)
        doc["objects"][0]["properties"] = [{"name": "id", "column": "id"},
                                           {"name": "name", "column": "name"}]
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        after = _binding_rows(TEST_DS_ID)[0]
        assert after["max_rows"] == 5000, "人工配的限流不得被发布抹掉"
        assert after["permission_tokens"] == before["permission_tokens"]
        assert after["masked_columns"] == before["masked_columns"]

    def test_ghost_column_does_not_overwrite_existing_map(self, monkeypatch):
        """派生映射本身含幽灵列（表存在但列改名）→ 保留现有列映射，不拿更错的覆盖。

        宁缺勿错：漂移由页面告警 + validation_warnings 出声，修法是改 doc 的列名再发布，
        不是让级联静默写进一个不存在的列。
        """
        from backend.modules.catalog.services import physical_ref

        _ensure_model()
        _insert_row(TEST_DS_ID, column_map={"ID": "id"})
        monkeypatch.setattr(physical_ref, "columns_of_table",
                            lambda ds, table, cache=None: {"id"} if table == "t_x" else set())
        doc = _doc()
        doc["objects"][0].pop("execution_binding", None)
        doc["objects"][0]["properties"] = [{"name": "ID", "column": "renamed_col"}]
        out = ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        warns = out.get("validation_warnings") or []
        assert any("部署绑定" in str(w) for w in warns), f"漂移必须出声：{warns}"
        after = _binding_rows(TEST_DS_ID)[0]
        assert json.loads(after["column_map"] or "{}") == {"ID": "id"}, "含幽灵列的新映射不得覆盖现有映射"

    def test_drift_warning_splits_unverified_from_missing_column(self, monkeypatch):
        """告警引导必须按 verdict 分流（假引导视同缺陷）。

        换库后有两类完全不同的“列待修”：列不存在（定义写错，要改选/取消绑定）与
        该表在本源下一条列元数据都没同步（改了也没依据，要先同步元数据）。
        统一说“核对实际列名后修正属性”会把人引向一个解决不了问题的操作。
        """
        from backend.modules.catalog.services import physical_ref

        _ensure_model()
        doc = _doc()
        doc["objects"][0].pop("execution_binding", None)
        doc["objects"][0]["properties"] = [{"name": "ID", "column": "t_x.renamed_col"}]

        # ① 表有实存列清单，列不在里面 → 引导改属性列映射（并给“取消绑定”出口）
        _insert_row(TEST_DS_ID, column_map={"ID": "id"})
        monkeypatch.setattr(physical_ref, "columns_of_table",
                            lambda ds, table, cache=None: {"id"} if table == "t_x" else set())
        warns = [str(w) for w in (ontology_service.save_draft(
            TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False)).get("validation_warnings") or [])
            if "部署绑定" in str(w)]
        assert warns, "列漂移必须出声"
        assert any("不在表" in w and "实存列里" in w for w in warns), warns
        assert any("改选实存列" in w and "取消该属性的物理列绑定" in w for w in warns), warns
        assert not any("尚未同步列元数据" in w for w in warns), warns

        # ② 该表整表无列元数据 → 引导同步元数据，不得谎称列不存在/让人改列名
        monkeypatch.setattr(physical_ref, "columns_of_table",
                            lambda ds, table, cache=None: set())
        warns2 = [str(w) for w in (ontology_service.save_draft(
            TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False)).get("validation_warnings") or [])
            if "部署绑定" in str(w)]
        assert any("尚未同步列元数据" in w and "同步这张表的字段" in w for w in warns2), warns2
        assert not any("改选实存列" in w for w in warns2), f"元数据缺失时不得引导改列名：{warns2}"

    def test_corrected_column_refreshes_map_and_state(self, monkeypatch):
        """反向位：在页面上把列名改对后发布，绑定行必须刷新并回到 bound。

        只锁住“不覆盖”不锁“能修好”，用户改完仍看到「列待修/形状漂移」就是新断链
        （「编辑对象 → 属性列映射」的实际使用路径）。
        """
        from backend.modules.catalog.services import ontology_yaml_import as yimp
        from backend.modules.catalog.services import physical_ref

        _ensure_model()
        _insert_row(TEST_DS_ID, column_map={"ID": "renamed_col"}, sync_state="drifted")
        monkeypatch.setattr(physical_ref, "columns_of_table",
                            lambda ds, table, cache=None: {"id"} if table == "t_x" else set())
        # 该表在已同步物理表清单里（真实 Doris 就是这种形态）：否则 drifted 来自
        # “表未登记”而不是“列漂移”，断言不到要验的那一件事
        monkeypatch.setattr(yimp, "_load_table_info_map",
                            lambda ds: {"t_x": {"table_name": "t_x", "query_mode": "raw_source",
                                                "allow_full_scan": 1}})
        doc = _doc()
        doc["objects"][0].pop("execution_binding", None)
        doc["objects"][0]["properties"] = [{"name": "ID", "column": "id"}]
        out = ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        assert not [w for w in (out.get("validation_warnings") or []) if "部署绑定" in str(w)], out
        after = _binding_rows(TEST_DS_ID)[0]
        assert json.loads(after["column_map"] or "{}") == {"ID": "id"}, after
        assert after["sync_state"] == "bound", "列名改对后不得停在 drifted（否则页面永远亮漂移）"


class TestBindingProjection:
    def setup_method(self):
        _ensure_model()

    def teardown_method(self):
        _cleanup()

    def test_backfill_projects_from_binding_rows(self):
        """展开行内嵌列是绑定行的派生投影（回填，不写回）。"""
        doc = _ensure_model()
        _insert_row(TEST_DS_ID)
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        n = ontology_service._backfill_execution_binding(TEST_MODEL_ID)
        assert n >= 1
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT execution_binding FROM adh_ontology_objects "
                    "WHERE model_id = %s AND object_key = 't_x'", (TEST_MODEL_ID,))
                row = cur.fetchone()
        assert row is not None, "save_draft 应已展开对象行"
        eb = json.loads(row["execution_binding"] or "{}")
        assert eb["physical_table"] == "t_x" and eb["product_ref"] == "test-ds.t_x"

    def test_newly_registered_row_projects_immediately(self):
        """级联登记完立刻回填：同一批发布后对象展开行就带上绑定投影（不滞后一次保存）。"""
        doc = _ensure_model()
        doc["objects"][0].pop("execution_binding", None)
        ontology_service.save_draft(TEST_MODEL_ID, json.dumps(doc, ensure_ascii=False))
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT execution_binding FROM adh_ontology_objects "
                    "WHERE model_id = %s AND object_key = 't_x'", (TEST_MODEL_ID,))
                row = cur.fetchone()
        eb = json.loads((row or {}).get("execution_binding") or "{}")
        assert eb.get("physical_table") == "t_x", "新登记的绑定必须同批投影到展开行"


# 列候选接口（「编辑对象 → 属性列映射」选择框）专用表名/对象 key：
# 用带前缀的专用名，避免与真实元数据里的同名表相撞。
OPTS_TABLE = "__test_opts_table__"
OPTS_TABLE_B = "__test_opts_table_b__"
OPTS_OBJECT = "__test_opts_obj__"


def _opts_doc(primary_table: str = OPTS_TABLE) -> dict:
    return {"name": TEST_MODEL_NAME, "objects": [{
        "key": OPTS_OBJECT, "display_name": "选项对象", "primary_table": primary_table,
        "properties": [{"name": "编码", "column": f"{primary_table}.code"},
                       {"name": "旧列", "column": f"{primary_table}.gone"}],
    }]}


def _insert_columns(table: str, cols: list) -> None:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for name, comment in cols:
                cur.execute(
                    "INSERT INTO adh_column_metadata (datasource_id, table_name, column_name, "
                    "data_type, column_comment, is_active, sync_time) "
                    "VALUES (%s, %s, %s, 'varchar', %s, 1, UTC_TIMESTAMP())",
                    (TEST_DS_ID, table, name, comment))
            conn.commit()


def _insert_binding_row(object_key: str, physical_table: str, *, status: str = "active",
                        bind_kind: str = "primary") -> None:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_bindings (model_id, object_key, datasource_id, datasource_name, "
                "physical_table, column_map, bind_kind, sync_state, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'test-ds', %s, '{}', %s, 'bound', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (TEST_MODEL_ID, object_key, TEST_DS_ID, physical_table, bind_kind, status))
            conn.commit()


class TestObjectColumnOptions:
    """`GET /models/{id}/objects/{key}/columns`：修「列待修」的唯一页面能力。

    断链历史：服务端判出列漂移并亮「列待修」徽标，而草案 JSON 视图只读、属性行不可编，
    告警指向一个不存在的操作。候选口 + 弹窗选择框是同一条修复链的两半，必须同批锁住：
    候选 = 漂移裁决同一谓词（已同步实存列）、解析优先级与 get_model_sites 同源、
    拿不到列时出声（note）而不交空清单假装“没列可改”。
    """

    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_columns_come_from_synced_metadata(self, monkeypatch):
        from backend.modules.catalog.api import ontology as ont_api

        monkeypatch.setattr(ont_api, "_allowed_source_ids", lambda user: None)
        _ensure_model(_opts_doc())
        _insert_columns(OPTS_TABLE, [("code", "编码"), ("name", "名称")])
        out = ont_api.get_object_column_options(TEST_MODEL_ID, OPTS_OBJECT, "", {"user_id": 1})
        assert out["table"] == OPTS_TABLE and out["note"] == "", out
        assert [c["name"] for c in out["columns"]] == ["code", "name"], out
        assert out["columns"][0]["comment"] == "编码", "选项主标签必须是可读业务名"
        assert "gone" not in [c["name"] for c in out["columns"]], "漂移列本来就不在实存列里"
        assert "datasource_id" not in out, "内部数据源 id 不得出现在页面响应里"

    def test_deployed_table_wins_over_doc_declaration(self, monkeypatch):
        """候选表名 = 生效部署绑定行优先（与漂移告警同表）。

        否则弹窗里候选来自 doc 写的表、告警判于实际部署的表，用户会陷入
        “选了仍报列不存在”的死循环。
        """
        from backend.modules.catalog.api import ontology as ont_api

        monkeypatch.setattr(ont_api, "_allowed_source_ids", lambda user: None)
        _ensure_model(_opts_doc(OPTS_TABLE))
        _insert_binding_row(OPTS_OBJECT, OPTS_TABLE_B)
        _insert_columns(OPTS_TABLE_B, [("code", "编码")])
        out = ont_api.get_object_column_options(TEST_MODEL_ID, OPTS_OBJECT, "", {"user_id": 1})
        assert out["table"] == OPTS_TABLE_B, out
        assert [c["name"] for c in out["columns"]] == ["code"], out

    def test_table_override_follows_dialog_input(self, monkeypatch):
        """弹窗内改了「绑定物理表」时，候选跟着新表走（table 覆盖值优先）。"""
        from backend.modules.catalog.api import ontology as ont_api

        monkeypatch.setattr(ont_api, "_allowed_source_ids", lambda user: None)
        _ensure_model(_opts_doc(OPTS_TABLE))
        _insert_columns(OPTS_TABLE, [("old_col", "旧列")])
        _insert_columns(OPTS_TABLE_B, [("new_col", "新列")])
        out = ont_api.get_object_column_options(TEST_MODEL_ID, OPTS_OBJECT, OPTS_TABLE_B,
                                                {"user_id": 1})
        assert out["table"] == OPTS_TABLE_B, out
        assert [c["name"] for c in out["columns"]] == ["new_col"], out

    def test_missing_metadata_returns_reason_not_empty_lie(self, monkeypatch):
        """表存在但列元数据未同步：必须给出可诊断原因，不能只回空清单。"""
        from backend.modules.catalog.api import ontology as ont_api

        monkeypatch.setattr(ont_api, "_allowed_source_ids", lambda user: None)
        _ensure_model(_opts_doc())
        out = ont_api.get_object_column_options(TEST_MODEL_ID, OPTS_OBJECT, "", {"user_id": 1})
        assert out["columns"] == [] and "没有已同步的列元数据" in out["note"], out

    def test_object_without_table_says_so(self, monkeypatch):
        """对象未声明物理表：note 指回「绑定物理表」输入框，而不是静默给空候选。"""
        from backend.modules.catalog.api import ontology as ont_api

        monkeypatch.setattr(ont_api, "_allowed_source_ids", lambda user: None)
        doc = _opts_doc()
        doc["objects"][0]["primary_table"] = ""
        _ensure_model(doc)
        out = ont_api.get_object_column_options(TEST_MODEL_ID, OPTS_OBJECT, "", {"user_id": 1})
        assert out["table"] == "" and "尚未声明物理表" in out["note"], out

    def test_unknown_object_is_404(self, monkeypatch):
        from fastapi import HTTPException

        from backend.modules.catalog.api import ontology as ont_api

        monkeypatch.setattr(ont_api, "_allowed_source_ids", lambda user: None)
        _ensure_model(_opts_doc())
        with pytest.raises(HTTPException) as exc:
            ont_api.get_object_column_options(TEST_MODEL_ID, "__no_such_obj__", "", {"user_id": 1})
        assert exc.value.status_code == 404
