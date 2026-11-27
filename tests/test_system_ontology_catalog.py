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

import asyncio
import json
import os
import re
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


# ══════════════════════════════════════════════════════════════════
# 任务流（scenarios[].flow）：行为 / 规则 / 场景必须真正串起来
#
# 三张平铺清单 ≠ 动态本体。要让模型顺着「场景 → 有序步骤 → 每步动作 + 权限码 +
# 规则门 + 上一步产出」走完一个平台任务，链接点必须能被校验、被图谱遍历、
# 被上云文档与 AS-BOT 工具读到。下列用例把这条链路锁死在代码上。
# ══════════════════════════════════════════════════════════════════

_PERM_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]*(:[a-z][a-z0-9_-]*)+")


def test_every_action_declares_perm_and_channel():
    """每个动作都要有权限码 + 执行通道：缺了 flow 步骤就变成「不知 AI 能不能做」。"""
    akeys = {str(a.get("key") or "")
             for o in catalog.OBJECTS for a in (o.get("actions") or [])}
    assert set(catalog.ACTION_PERM_CODES) == akeys, (
        f"动作绑定表与对象 actions 不一致：多出 {sorted(set(catalog.ACTION_PERM_CODES) - akeys)}"
        f" / 缺少 {sorted(akeys - set(catalog.ACTION_PERM_CODES))}")
    for key, meta in catalog.ACTION_PERM_CODES.items():
        assert _PERM_CODE_RE.match(str(meta.get("code") or "")), f"{key} 的权限码非法: {meta}"
        assert meta.get("channel") in ("ai", "human"), f"{key} 的执行通道非法: {meta}"


def test_ai_channel_actions_have_real_tooling():
    """channel 不是文案：标 ai 必须真有工具，标 human 必须真没工具。

    历史偏差：alias.approve/reject 在 as-bot-system-waker §3 被当成 AI 写动作，
    实际只有 HTTP 端点、没有 AI 工具——这种「配了但 AI 做不了」必须显式标 human，
    否则模型会照计划去调一个根本不存在的工具。
    """
    from backend.modules.mind.execution.function_tools import FUNCTION_ACTION_SPECS
    from backend.modules.mind.execution.sdk_tools import ontology_tools

    onto_specs = {str(spec.get("action_key") or ""): spec for spec in ontology_tools.TOOL_SPECS}
    drift = []
    for key, meta in catalog.ACTION_PERM_CODES.items():
        code, channel = str(meta["code"]), str(meta["channel"])
        spec = FUNCTION_ACTION_SPECS.get(key)
        if spec is None:
            spec = onto_specs.get(key)
            if channel == "ai" and spec is not None:
                if str(spec.get("perm_code") or "") != code:
                    drift.append(f"{key}: 本体标 {code}，本体工具实际 {spec.get('perm_code')}")
                if str(spec.get("name") or "") != str(meta.get("tool_name") or ""):
                    drift.append(f"{key}: 本体工具名与真实注册表不一致")
        if channel == "ai":
            if spec is None:
                drift.append(f"{key}: 标成 AI，但真实注册表没有 executor")
            elif str(spec.get("perm_code") or "") != code:
                drift.append(f"{key}: 本体标 {code}，真实工具实际 {spec.get('perm_code')}")
        elif spec is not None:
            drift.append(f"{key}: 标成 human，但真实注册表 executor 存在（AI 其实能做）")
    assert drift == [], "动作通道与真实工具不一致: " + "; ".join(drift)


def test_flows_are_ordered_and_chained():
    """flow 必须是有序、可串联、引用可解析的步骤清单（平铺清单不算联动）。"""
    doc = catalog.build_system_ontology_doc()
    flows = [s for s in doc["scenarios"] if s.get("flow")]
    assert flows, "至少要有一个带任务步骤的场景"
    for s in flows:
        steps = s["flow"]
        assert [st["order"] for st in steps] == list(range(1, len(steps) + 1)), \
            f"{s['key']} 步序不是 1..N 连续"
        produced: set = set()
        for st in steps:
            assert st["action"] in s["uses_actions"], \
                f"{s['key']} 第 {st['order']} 步动作未列在 uses_actions（两套清单会漂移）"
            assert st["perm_code"], f"{s['key']} 第 {st['order']} 步没有权限码"
            assert st["channel"] in ("ai", "human"), f"{s['key']} 第 {st['order']} 步通道非法"
            assert st["action_label"], f"{s['key']} 第 {st['order']} 步缺业务动作名"
            if st.get("consumes"):
                assert st["consumes"] in produced, (
                    f"{s['key']} 第 {st['order']} 步要消费「{st['consumes']}」，"
                    "但更早步骤没有 produces 它（串联断了）")
            for rk in (st.get("gated_by") or []):
                assert rk in s["uses_rules"], f"{s['key']} 第 {st['order']} 步规则门未列在 uses_rules"
            if st.get("produces"):
                produced.add(st["produces"])


def test_flow_reaches_cloud_projection_with_channel_and_perm():
    """上云文档必须显式写清「哪步 AI 能做 / 哪步只能人工 / 要哪个码 / 依赖上一步什么」。"""
    md = ontology_service.to_cloud_md(catalog.build_system_ontology_doc())
    assert "执行步骤（按序" in md
    assert "AI 可直接执行" in md, "AI 可执行步骤未标注，模型会以为都要人工"
    assert "需人工到界面完成" in md, "人工步骤未标注，模型会声称做完了"
    assert "权限码 article:manage" in md
    assert "待审核草稿" in md, "上一步产出未渲染，串联断在文档里"
    assert "受规则约束：" in md
    # 别名必须上云：模型是拿口语名（「看板发布」）问的
    assert "（又称：看板发布" in md


def test_rdf_projects_task_flow_as_traversable_edges():
    """图谱要能顺着场景→步骤→动作/规则门走，且步骤节点不得被当成 SQL 关系端点。"""
    from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle
    from backend.semantics.graph_rag.link_graph import _iri_key

    turtle = ontology_json_to_turtle(catalog.build_system_ontology_doc(), datasource_id=-1)
    for token in ("a adh:TaskFlow", "a adh:FlowStep", "adh:flowOf", "adh:stepAction",
                  "adh:stepChannel", "adh:stepPerm", "adh:nextStep",
                  "adh:stepGatedBy", "adh:stepProduces"):
        assert token in turtle, f"任务流未投影到图谱: {token}"
    base = "http://ai-datahub.org/ontology/"
    assert _iri_key(base + "fstep:ops_content_publish_1") == "", \
        "步骤节点被当成对象端点会污染 SQL 关系链"


# ── AS-BOT 取计划（只读工具，不含任何新的执行裁决）────────────────

def _plan_harness(monkeypatch, role_perms: dict):
    """把工具接到确定的本体与角色权限上（不依赖库状态）。"""
    from backend.core.role_service import role_service
    from backend.modules.mind.execution.sdk_tools import system_tools

    monkeypatch.setattr(system_tools, "_admin_identity",
                        lambda: {"user_id": 7, "role": "admin", "workspace_id": 0})
    monkeypatch.setattr(system_tools, "_active_system_doc", catalog.build_system_ontology_doc)
    monkeypatch.setattr(role_service, "get_user_role_ai_perms",
                        lambda user_id, workspace_id: role_perms)
    return system_tools


def _call_plan(tools, scenario=None):
    args = {} if scenario is None else {"scenario": scenario}
    out = asyncio.run(tools.system_capability_plan(args))
    return json.loads(out["content"][0]["text"]), bool(out.get("isError"))


def test_plan_resolves_business_alias_and_marks_human_step(monkeypatch):
    """口语别名（「看板发布」）必须命中，人工步骤必须标成不可代做。"""
    tools = _plan_harness(monkeypatch, {"dashboard:read": {"ai_access": "read", "ai_note": ""}})
    data, is_err = _call_plan(tools, "看板发布")
    assert not is_err, data
    assert data["executable"] is True and data["step_count"] == 2
    first, second = data["steps"]
    assert first["channel"] == "ai" and first["you_can_run"] is True
    assert first["perm_code"] == "dashboard:read"
    assert second["channel"] == "human" and second["you_can_run"] is False
    assert "只能由人" in second["why_not"]
    assert second["consumes"] == first["produces"] == "目标看板清单"
    assert any(g["key"] == "constraint_dashboard_preview_before_publish"
               for g in second["rule_gates"])


def test_plan_denies_unheld_code_without_guessing(monkeypatch):
    tools = _plan_harness(monkeypatch, {})
    data, is_err = _call_plan(tools, "内容发布")
    assert not is_err, data
    assert data["steps"][0]["you_can_run"] is False
    assert "未持有权限码 article:manage" in data["steps"][0]["why_not"]


def test_plan_denies_write_step_when_ai_level_is_read(monkeypatch):
    """角色有码但 AI 级别只到 read：写步骤仍不可执行（配置级别不是能力证明）。"""
    tools = _plan_harness(monkeypatch, {"article:manage": {"ai_access": "read", "ai_note": ""}})
    data, _ = _call_plan(tools, "内容发布")
    step = data["steps"][0]
    assert step["read_only"] is False and step["you_can_run"] is False
    assert "需要 write" in step["why_not"]


def test_plan_lists_candidates_instead_of_guessing(monkeypatch):
    tools = _plan_harness(monkeypatch, {})
    data, is_err = _call_plan(tools, "系统巡检")
    assert is_err and "不猜" in data["error"]
    assert data["candidates"], "匹配不到时必须回候选，不能只报错"
    assert all({"key", "title", "executable"} <= set(c) for c in data["candidates"])


def test_plan_returns_full_catalog_when_query_missing(monkeypatch):
    tools = _plan_harness(monkeypatch, {})
    data, is_err = _call_plan(tools, None)
    assert not is_err, data
    assert len(data["candidates"]) == len(catalog.SCENARIOS)
    assert {c["key"] for c in data["candidates"] if c["executable"]} == \
        {s["key"] for s in catalog.SCENARIOS if s.get("flow")}


def test_plan_marks_qa_only_scenario_not_executable(monkeypatch):
    tools = _plan_harness(monkeypatch, {})
    data, is_err = _call_plan(tools, "能力与权限码核查")
    assert not is_err, data
    assert data["executable"] is False
    assert "没有任务步骤" in data["note"], "问答型场景必须显式说明不得编造步骤"
