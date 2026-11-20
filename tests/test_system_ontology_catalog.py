"""系统能力本体（kind='system'）目录与代码事实的一致性回归。

系统本体是 AS-BOT 回答「平台能做什么/边界在哪」的唯一语义来源，历史问题是它
长期未更新（仍在描述 adh_wakers / adh_as_bot_approvals / metadata.sync 等已不存在的
概念），且 function_tools 的 object_key 悬空。本用例锁住：

1. 目录自检通过（key 唯一、关系/规则/场景引用可解析、无涉密列）；
2. 已退役概念不得回流（waker / as_bot_approval / metadata.sync / __system_bot__）；
3. 每个功能动作的 object_key 都能在本体里找到对象（不悬空）；
4. 数据工程域（同步/DAG/UDF）不登记任何可执行动作（运维专属，不对 AI 开放）；
5. M2/M3/M4 建模位校验无 error。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import ontology_service
from backend.modules.catalog.services import system_ontology_catalog as catalog


def test_catalog_self_check_passes():
    problems = catalog.validate_catalog()
    assert problems == [], f"目录自检失败: {problems}"


def test_retired_concepts_do_not_come_back():
    doc = catalog.build_system_ontology_doc()
    keys = {str(o.get("key") or "") for o in doc["objects"]}
    assert "waker" not in keys, "adh_wakers 已不存在，不得再用 waker 对象描述智能体"
    assert "as_bot_approval" not in keys, "审批回路已退役（adh_as_bot_approvals 表不存在）"

    blob = json.dumps(doc, ensure_ascii=False)
    assert "metadata.sync" not in blob, "metadata.sync 动作已废弃"
    assert "__system_bot__" not in blob, "哨兵口径已退役（按 kind + 权限码裁决）"


def test_function_actions_object_keys_resolve():
    """功能动作的 object_key 必须落在系统本体对象上（禁止悬空 key）。"""
    from backend.modules.mind.execution.function_tools import FUNCTION_ACTION_SPECS

    keys = {str(o.get("key") or "") for o in catalog.OBJECTS}
    dangling = {a: s.get("object_key") for a, s in FUNCTION_ACTION_SPECS.items()
                if (s.get("object_key") or "").strip() and s["object_key"] not in keys}
    assert dangling == {}, f"功能动作 object_key 悬空: {dangling}"


def test_data_engineering_domain_is_read_only():
    """数据工程域（同步/DAG/UDF）不得登记可执行动作：运维专属，不对 AI 开放。"""
    by_key = {str(o.get("key") or ""): o for o in catalog.OBJECTS}
    for key in ("sync_task", "dag_workflow", "udf"):
        obj = by_key[key]
        assert obj["actions"] == [], f"{key} 不得登记 AI 可执行动作"
        assert "不对 AI 开放" in obj["description"], f"{key} 描述必须写明运维专属"


def test_doc_has_no_secret_or_body_columns():
    doc = catalog.build_system_ontology_doc()
    banned = ("password", "api_key", "secret", "sql_query", "messages", "filter_expr")
    for obj in doc["objects"]:
        for prop in obj["properties"]:
            col = str(prop.get("column") or "").rsplit(".", 1)[-1].lower()
            assert col not in banned, f"{obj['key']} 不得登记涉密/正文列: {col}"


def test_m234_validation_has_no_errors():
    doc = catalog.build_system_ontology_doc()
    errors = [it for it in ontology_service.find_m234_issues(doc)
              if it.get("severity") == "error"]
    assert errors == [], f"行为/规则/场景建模校验未通过: {errors}"


def test_platform_contract_objects_marked_platform():
    """系统本体的契约清单按 doc 合成并标 platform。

    页面原先拿业务部署绑定口径判它：49 个对象全挂「列待同步」（catalog 永不收录 adh_*），
    新增对象还挂「未绑定物理表」。这类状态对“随版本发布、不参与取数”的系统本体不成立。
    """
    from backend.modules.catalog.api.ontology import _platform_contract_objects

    doc = catalog.build_system_ontology_doc()
    rows = _platform_contract_objects(doc)
    assert {r["object_key"] for r in rows} == {o["key"] for o in doc["objects"]}
    assert all(r["binding_source"] == "platform" for r in rows)
    # 纯概念对象（skill 无物理表）同样列入，避免被误报成「未绑定物理表」
    skill = next(r for r in rows if r["object_key"] == "skill")
    assert skill["physical_table"] == "" and skill["column_count"] == 0


def test_system_property_drift_checks_real_columns(monkeypatch):
    """系统本体的列漂移对着真实库实存列判（不走 catalog），只报真漂移。

    走 catalog 的口径会给每个对象挂一条永远消不掉的「列待同步」（系统模型
    datasource_id=0，没有可同步的源），文案还让人去「表 & 字段」同步——死引导。
    """
    from backend.modules.catalog.api import ontology as api_ontology

    class _Cur:
        def __init__(self, cols):
            self.cols = cols

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self._sql = sql

        def fetchone(self):
            return {"db": "adh2"}

        def fetchall(self):
            return [{"TABLE_NAME": t, "COLUMN_NAME": c}
                    for t, cs in self.cols.items() for c in cs]

    class _Conn:
        def __init__(self, cols):
            self.cur = _Cur(cols)

        def cursor(self):
            return self.cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    doc = {"objects": [
        {"key": "role", "primary_table": "adh_roles",
         "properties": [{"name": "角色ID", "column": "adh_roles.id"},
                        {"name": "角色名", "column": "adh_roles.nope"}]},
        {"key": "skill", "properties": [{"name": "技能名", "column": "x"}]},
    ]}
    monkeypatch.setattr("backend.common.db.get_metadata_conn",
                        lambda: _Conn({"adh_roles": {"id", "name"}}))
    drift = api_ontology._system_property_drift(doc)
    assert list(drift) == ["role"], "纯概念对象/实存列不得产生漂移条目"
    assert drift["role"] == [{"property": "角色名", "column": "adh_roles.nope",
                              "table": "adh_roles", "verdict": "bad"}]


def test_permission_object_exposes_api_binding():
    """权限点必须能回答「这个码管哪些接口」：api_pattern/api_method 是真实表列，不得漏投影。"""
    perm = next(o for o in catalog.OBJECTS if o["key"] == "permission")
    cols = {p["column"].rsplit(".", 1)[-1] for p in perm["properties"]}
    assert {"api_pattern", "api_method"} <= cols, (
        f"permission 对象缺接口绑定列（只剩 {sorted(cols)}）——"
        "AS-BOT 无法回答「这个权限码管哪些接口」")


def test_every_object_declares_capability_binding():
    """每个能力对象都要声明「由哪个权限码把关」；无码的必须显式记账原因（不得静默省略）。"""
    keys = {o["key"] for o in catalog.OBJECTS}
    declared = set(catalog.CAPABILITY_PERM_CODES) | set(catalog.CAPABILITY_NO_OWN_CODE)
    assert keys - declared == set(), f"以下对象未声明能力↔权限码绑定: {sorted(keys - declared)}"
    assert declared - keys == set(), f"绑定表引用了不存在的对象: {sorted(declared - keys)}"
    overlap = set(catalog.CAPABILITY_PERM_CODES) & set(catalog.CAPABILITY_NO_OWN_CODE)
    assert overlap == set(), f"同一对象不能既声明有码又声明无码: {sorted(overlap)}"


def test_capability_bindings_reach_the_doc():
    """绑定必须真的进 canonical doc（links.requires_permission）：只写在常量表里等于没告诉模型。"""
    doc = catalog.build_system_ontology_doc()
    for obj in doc["objects"]:
        links = [l for l in obj.get("links") or []
                 if l.get("target") == "permission"
                 and l.get("type") == "requires_permission"]
        assert len(links) == 1, f"{obj['key']} 缺能力↔权限码绑定关系（requires_permission）"
        assert links[0]["description"].strip(), f"{obj['key']} 的绑定关系缺说明"


def _registry_codes():
    """active 权限码集合（元数据库不可用时返回 None，由用例 skip）。"""
    try:
        import pymysql

        env = {}
        path = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        conn = pymysql.connect(
            host=env["METADATA_DB_HOST"], port=int(env["METADATA_DB_PORT"]),
            user=env["METADATA_DB_USER"], password=env["METADATA_DB_PASSWORD"],
            database=env["METADATA_DB_DATABASE"], charset="utf8mb4", autocommit=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT perm_code FROM adh_perm_registry WHERE is_active=1")
            return {str(r[0]) for r in cur.fetchall()}
    finally:
        conn.close()


def test_bound_codes_exist_in_registry():
    """本体里点名的权限码必须在 adh_perm_registry 真实生效（防写出不存在的码教坏模型）。"""
    codes = _registry_codes()
    if codes is None:
        import pytest

        pytest.skip("元数据库不可用，跳过权限码存在性断言")
    bound = {c for cs in catalog.CAPABILITY_PERM_CODES.values() for c in cs}
    bound |= {str((r.get("enforcement") or {}).get("perm_code") or "")
              for r in catalog.RULES}
    bound.discard("")
    bound.discard("*")
    assert bound - codes == set(), f"本体绑定了库里不存在的权限码: {sorted(bound - codes)}"