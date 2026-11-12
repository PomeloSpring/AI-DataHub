"""「应用使用事件（桌面 app 遥测）」域与 RUM 命名分域 的回归门禁（依赖元数据库）。

背景（会话 1783069459151 的 P0 根因）：平台有两个都叫 "RUM" 的采集域，却只有 ARMS 那侧
有语义层资产——`prd-sls-*.observability_rum`（桌面 app 埋点）零本体、零字典、零数据产品，
AI 只能退到裸 SQL 猜列名（把宿主产品 appCode 当"应用"，而 app 名其实在 target 里），
于是同一问题在不同回合给出不同答案。

本门禁锁四件事（任一回退即失败，不做静默跳过——门禁失效却报绿比没有门禁更危险）：
1. 新域**可被语义层取数**：模型 active、对象已展开、部署绑定生效、字典行的 target_table
   必须等于物理表（planner 按物理表加载字典，空值=列名对了却取不到数，历史缺陷）；
2. **五站同构部署**：deploy_sites 是模型级唯一事实源，跨站按 object_key 必须路由到本站源；
   不属于本站的源必须被拒（宁缺勿错，不拿别站数据当本站答案）；
3. **命名分域不漂移**：ARMS 侧业务名收窄到「Web 端 RUM（官网/H5）」，app 域的业务名里
   不出现 "RUM"，两域指标名互不重叠（否则 get_metrics 目录与解析串味）；
4. **渗透率不得登记成伪指标**：分子分母来自不同过滤集，无法编译成单表聚合 → 只登记两个
   计数指标 + 口径文字（`retire_derived_pseudo_metrics` 的教训：目录挂着、编译用不了）。

建模型脚本：`backend/scripts/build_app_usage_ontology.py`（幂等，可重跑收敛）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.common.db import execute_query  # noqa: E402

MODEL_NAME = "应用使用事件本体（桌面 app 遥测）"
OBJECT_KEY = "app_usage_event"
TABLE = "observability_rum"
DS_NAMES = ["prd-sls-cn", "prd-sls-en", "prd-sls-jp", "prd-sls-uk", "prd-sls-eu"]
METRIC_NAMES = ["应用使用事件数", "应用活跃用户数", "应用活跃设备数",
                "应用中心活跃用户数", "人均使用事件数"]
DIM_NAMES = ["事件形态", "宿主应用", "宿主应用版本", "操作目标", "界面路径",
             "应用用户账号", "设备标识", "使用日期", "使用小时"]


def _model_row():
    row = execute_query(
        "SELECT id, name, kind, status, json_content, deploy_sites FROM adh_ontology_models "
        "WHERE name = %s ORDER BY updated_at DESC LIMIT 1", (MODEL_NAME,), fetchone=True)
    if not row:
        pytest.fail(f"生产本体缺模型「{MODEL_NAME}」：重跑 "
                    "venv/bin/python -m backend.scripts.build_app_usage_ontology --apply")
    return row


def test_model_is_active_business_with_contract_binding():
    row = _model_row()
    assert str(row["kind"]) == "business", "app 遥测属业务域（不得建成系统本体，as-bot-system-waker §1）"
    assert str(row["status"]) == "active", "未发布=问不到（activate 是对外可查的唯一开关）"

    doc = json.loads(row["json_content"] or "{}")
    obj = next((o for o in doc.get("objects") or [] if o.get("key") == OBJECT_KEY), None)
    assert obj, f"canonical doc 里没有对象 {OBJECT_KEY}（派生表与事实源不一致）"
    assert obj.get("primary_table") == TABLE
    # 属性必须有业务中文名且列名实存（本体建模规范 §2：照搬表名/列名即泄露物理结构）
    props = {p["name"]: p["column"] for p in obj.get("properties") or []}
    assert {"事件形态", "宿主应用", "操作目标", "应用用户账号", "设备标识"} <= set(props)
    assert set(props.values()) <= {"action", "appCode", "appVersion", "target", "page",
                                   "userCode", "pcId", "__time__"}, props

    binds = execute_query(
        "SELECT datasource_name, physical_table, column_map FROM adh_ontology_bindings "
        "WHERE object_key = %s AND status = 'active'", (OBJECT_KEY,)) or []
    assert len(binds) == 1, f"契约行应唯一（一份契约 × 模型级站点），实得 {len(binds)} 条"
    assert binds[0]["datasource_name"] == "prd-sls-cn" and binds[0]["physical_table"] == TABLE
    cmap = json.loads(binds[0]["column_map"] or "{}")
    assert cmap.get("操作目标") == "target" and cmap.get("宿主应用") == "appCode"


def test_dict_rows_are_compilable_on_the_physical_table():
    """字典行的 target_table 必须是物理表：planner 按 target_table 加载，空值等于不可编译。"""
    rows = execute_query(
        "SELECT name, target_table, bound_object_key, formula, agg_type, description "
        "FROM adh_metrics WHERE name IN ({ph})".format(ph=", ".join(["%s"] * len(METRIC_NAMES))),
        tuple(METRIC_NAMES)) or []
    assert len(rows) == len(METRIC_NAMES), f"指标缺行: {sorted(set(METRIC_NAMES) - {r['name'] for r in rows})}"
    for r in rows:
        assert str(r["target_table"]) == TABLE, f"{r['name']} target_table={r['target_table']!r}（空/错值取不到数）"
        assert r["bound_object_key"] == OBJECT_KEY
        # 伪公式（中文口径直塞 formula）会被原样拼进 SELECT —— 只允许合法单表聚合
        assert "WHERE 有" not in str(r["formula"]) and "口径" not in str(r["formula"])
    by_name = {r["name"]: r for r in rows}
    assert by_name["应用中心活跃用户数"]["formula"] == \
        "COUNT(DISTINCT CASE WHEN target = 'apps' THEN userCode END)"
    assert "NULLIF" in by_name["人均使用事件数"]["formula"], "比率必须防除零"

    dims = execute_query(
        "SELECT name, target_table, target_column FROM adh_dimensions WHERE name IN ({ph})".format(
            ph=", ".join(["%s"] * len(DIM_NAMES))), tuple(DIM_NAMES)) or []
    assert len(dims) == len(DIM_NAMES), f"维度缺行: {sorted(set(DIM_NAMES) - {r['name'] for r in dims})}"
    for d in dims:
        assert str(d["target_table"]) == TABLE, f"{d['name']} target_table 为空 → 语义层不可编译"
    labels = json.loads((execute_query(
        "SELECT value_labels FROM adh_dimensions WHERE name = '操作目标'", fetchone=True
    ) or {}).get("value_labels") or "{}")
    assert "apps" in labels and "MeshCompare" in labels, "app 码必须可枚举（否则 AI 不知道能过滤什么）"
    # 计划要求的合并规则落在 value_labels，不留口头约定
    assert "NewOrthoSim" in (labels.get("OrthoSim") or ""), labels.get("OrthoSim")


def test_penetration_is_two_counts_not_a_pseudo_metric():
    """渗透率=分子分母来自不同过滤集，登记成一条指标会在带目标过滤时静默出错数 → 禁止。"""
    hits = execute_query(
        "SELECT name FROM adh_metrics WHERE bound_object_key = %s AND (name LIKE %s OR name LIKE %s)",
        (OBJECT_KEY, "%渗透%", "%触达%")) or []
    assert not hits, f"app 域不得登记渗透率伪指标: {[h['name'] for h in hits]}"
    desc = " ".join(str(r["description"] or "") for r in execute_query(
        "SELECT description FROM adh_metrics WHERE name IN ({ph})".format(
            ph=", ".join(["%s"] * len(METRIC_NAMES))), tuple(METRIC_NAMES)) or [])
    assert "渗透率" in desc and "应用中心活跃用户数" in desc, "分母口径必须写在字典描述里（随上云文档下发）"


def test_cross_site_routing_and_refusal():
    from backend.common.db.datasource_db import get_datasource_by_name
    from backend.semantics import binding_resolver

    en = get_datasource_by_name("prd-sls-en")
    assert en, "prd-sls-en 数据源缺失，五站部署无从验证"
    binding, warnings = binding_resolver.resolve_binding(OBJECT_KEY, datasource_id=int(en["id"]))
    assert binding is not None, f"跨站路由失败: {warnings}"
    assert int(binding.datasource_id) == int(en["id"]), "生效源必须改写到请求站"
    assert binding.physical_table == TABLE

    # 不属于该模型的站点必须被拒（不拿别站/别库数据充数）
    arms = get_datasource_by_name("arms-prd-cn")
    refused, warn = binding_resolver.resolve_binding(OBJECT_KEY, datasource_id=int(arms["id"]))
    assert refused is None, "app 遥测域不得在 ARMS 源上命中（会拿错库的答案当本站数据）"
    assert any("不是它" in w or "不适用站点" in w for w in warn), warn

    sites = json.loads(_model_row()["deploy_sites"] or "[]")
    assert {s["site_code"] for s in sites} == {"cn", "int", "jp", "uk", "eu"}, sites
    assert {s["datasource"] for s in sites} == set(DS_NAMES), sites


def test_two_rum_domains_are_name_disjoint():
    arms = execute_query(
        "SELECT display_name, aliases FROM adh_ontology_objects "
        "WHERE object_key = 'rum_event' AND is_active = 1", fetchone=True)
    assert arms, "ARMS 对象行丢失（命名分域不得删旧域）"
    assert "Web 端" in str(arms["display_name"]), \
        f"ARMS 业务名未收窄: {arms['display_name']}（双域同名是本次争议的根源）"
    assert "RUM" in str(arms["aliases"] or ""), "裸 RUM 别名要保留，否则历史问法入口凭空消失"

    obj = execute_query("SELECT display_name, aliases FROM adh_ontology_objects "
                        "WHERE object_key = %s AND is_active = 1", (OBJECT_KEY,), fetchone=True)
    assert obj, "app 域对象未展开到 adh_ontology_objects"
    names = [str(obj["display_name"])] + [a.strip() for a in str(obj["aliases"] or "").split(",")]
    assert not [n for n in names if "rum" in n.lower() and "observability" not in n.lower()], \
        f"app 域业务名不得借用 RUM 一词（只有物理表名可含 rum）: {names}"

    arms_metrics = {str(r["name"]) for r in execute_query(
        "SELECT name FROM adh_metrics WHERE bound_object_key = 'rum_event' AND is_active = 1") or []}
    assert not arms_metrics & set(METRIC_NAMES), "两域指标名重叠 → get_metrics 目录会串味"


def test_retired_bindings_are_gone():
    left = execute_query(
        "SELECT object_key, status FROM adh_ontology_bindings WHERE physical_table = 'dwd_observability_rum'") or []
    assert not left, f"已退役对象绑定未清理: {left}（悬空行会被宽松匹配误命中）"


def test_cloud_doc_leaks_no_physical_identifiers():
    """上云文档只说业务名：渲染器剔除的是结构字段（表/列/JOIN/formula），**不洗描述里的专名**。

    所以描述文案必须自己不把表名/列名写进去（护栏 §7）：别名里带表名、口径里写
    `target='apps' 去重 userCode` 这类形态都会随知识库下发给 LLM，黑盒口径就碎了。
    数据源**业务名**不在禁例（list_datasources 本就按业务名下发）。
    """
    import re

    from backend.modules.catalog.services import ontology_kb_sync as kb

    row = dict(execute_query("SELECT * FROM adh_ontology_models WHERE id = %s",
                             (int(_model_row()["id"]),), fetchone=True))
    text = kb._render_cloud_doc(row)
    cols = {str(r["column_name"]) for r in execute_query(
        "SELECT DISTINCT column_name FROM adh_column_metadata "
        "WHERE table_name = %s AND is_active = 1", (TABLE,)) or []}
    # 枚举取值/界面路由值属业务数据，不进判定（单列名与普通英文词同形的只留单形列）
    ambiguous = {"date", "ip", "page", "target", "tags", "region", "timestamp",
                 "properties", "parameters"}
    banned = (cols - ambiguous) | {TABLE, "osInfo", "__time__"}
    hits = [t for t in banned
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])", text, re.I)]
    assert not hits, f"上云文档出现物理标识 {hits}：请改用业务名描述口径"


def test_skill_declares_the_domain_split():
    """技能文案与建模同批对齐：arms 技能必须写出两个域的分界（否则触发词命中仍会串域）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "backend", "modules", "mind", "config", "skills",
                        "arms-observability", "SKILL.md")
    text = open(path, encoding="utf-8").read()
    assert "app_usage_event" in text, "技能未指路到 app 域对象"
    assert "两个采集域" in text, "技能缺少分域段"
