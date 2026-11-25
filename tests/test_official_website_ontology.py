"""「官网业务本体」（t_help_center*，主源 prd-cn）与「官网可观测本体」的回归门禁（依赖元数据库）。

背景（2026-11-25 用户诉求）：`t_help_center*` 的官网咨询/支持反馈/询盘/演示预约/伙伴申请
从未建进任何本体，AI 问「官网提问多少条」只能退到裸 SQL 猜列名。本批按**平台约束**
拆成两个模型（一个模型只能有一个主源，`deploy_sites` 每站一个源）：

* 「官网业务本体」= MySQL `prd-cn` 上的 5 张业务表 + 2 张配置表（仅中国站）；
* 「官网可观测本体」= 原「ARMS RUM 观测本体」（SLS `arms-prd-cn`/`arms-prd-en`）改名，
  与业务侧配对成「业务 + 可观测」两件套。

本门禁锁六件事（任一回退即失败，不做静默跳过——门禁失效却报绿比没有门禁更危险）：
1. 模型 active / kind=business / **仅中国站**（deploy_sites 只有 cn→prd-cn，别站必拒）；
2. 七个对象都已展开（adh_ontology_objects）且部署绑定生效：主源 prd-cn、物理表对得上、
   column_map 覆盖全部属性列（planner 拿不到绑定就取不到数）；
3. 七张来源表已认领为数据产品（否则 activate 的「未登记来源表」门禁会挡、新环境起不来）；
4. 字典行（13 指标 / 18 维度）落在正确物理表 + 归属对象，且 `del_flag = 0` 口径基线不漂；
5. PII 表级敏感规则已登记（全局基线按精确列名，first_name/last_name 等不在其中，必须表级补）；
6. 别名已落地：旧名「ARMS RUM 观测本体」不再作为任何模型名（改写别的脚本时防再造一个旧名模型），
   上云文档不出现 `t_help_center*` 物理表名（护栏 §7 黑盒）。

建模型脚本：`backend/scripts/build_official_website_ontology.py`（幂等，可重跑收敛）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.common.db import execute_query  # noqa: E402
from backend.scripts import build_official_website_ontology as spec  # noqa: E402

MODEL_NAME = spec.MODEL_NAME
DS_NAME = spec.DS_NAME
ARMS_NEW = spec.ARMS_MODEL_NAME_NEW
ARMS_OLD = spec.ARMS_MODEL_NAME_OLD
TABLES = [o["table"] for o in spec.OBJ_SPECS]
OBJECT_KEYS = [o["key"] for o in spec.OBJ_SPECS]


def _model_row():
    row = execute_query(
        "SELECT * FROM adh_ontology_models "
        "WHERE name = %s AND datasource_id = (SELECT id FROM adh_datasources WHERE name = %s) "
        "ORDER BY updated_at DESC LIMIT 1", (MODEL_NAME, DS_NAME), fetchone=True)
    if not row:
        pytest.fail(f"缺模型「{MODEL_NAME}」（主源 {DS_NAME}）：重跑 "
                    "venv/bin/python -m backend.scripts.build_official_website_ontology --apply")
    return dict(row)


def _ds_id() -> int:
    row = execute_query("SELECT id FROM adh_datasources WHERE name = %s", (DS_NAME,), fetchone=True)
    if not row:
        pytest.fail(f"数据源未注册: {DS_NAME}")
    return int(row["id"])


def test_model_is_active_business_on_china_site_only():
    row = _model_row()
    assert str(row["kind"]) == "business", "官网业务表属业务本体（不得建成 system/source）"
    assert str(row["status"]) == "active", "未发布=问不到（activate 是对外可查的唯一开关）"
    sites = json.loads(row["deploy_sites"] or "[]")
    assert sites == [{"site_code": "cn", "datasource": DS_NAME}], (
        f"deploy_sites 必须只有中国站 cn→{DS_NAME}（本批用户拍板仅中国站；多站会让跨站路由拿到别站数据）"
        f"，实得 {sites}")


def test_objects_expanded_and_bound_to_physical_tables():
    row = _model_row()
    doc = json.loads(row["json_content"] or "{}")
    doc_objects = {o["key"]: o for o in doc.get("objects") or []}
    assert set(doc_objects) == set(OBJECT_KEYS), f"doc 对象集不符：{sorted(doc_objects)}"

    for obj in spec.OBJ_SPECS:
        key = obj["key"]
        rows = execute_query(
            "SELECT id FROM adh_ontology_objects WHERE model_id = %s AND object_key = %s",
            (int(row["id"]), key)) or []
        assert len(rows) == 1, f"对象 {key} 展开行应唯一（每次 activate 先删后插），实得 {len(rows)} 行"

        binds = execute_query(
            "SELECT datasource_name, physical_table, column_map, sync_state, status "
            "FROM adh_ontology_bindings WHERE object_key = %s AND status = 'active'", (key,)) or []
        assert len(binds) == 1, f"对象 {key} 的生效绑定行应唯一（一份契约 × 模型级站点），实得 {len(binds)} 条"
        bind = binds[0]
        assert bind["datasource_name"] == DS_NAME, f"{key} 绑定主源应为 {DS_NAME}，实得 {bind['datasource_name']}"
        assert bind["physical_table"] == obj["table"], f"{key} 绑定物理表应为 {obj['table']}"
        assert str(bind["sync_state"]) == "bound", f"{key} 绑定未 bound（取数会被拒）：{bind['sync_state']}"

        # column_map 是「业务名/列 → 物理列(可带表前缀)」：按值取末段判覆盖（与脚本 _bindings 同口径）
        cmap = json.loads(bind["column_map"] or "{}")
        mapped = ({str(v).split(".")[-1].lower() for v in cmap.values()}
                  if isinstance(cmap, dict) else set())
        missing = [c[0] for c in obj["columns"] if c[0].lower() not in mapped]
        assert not missing, f"{key} 的 column_map 缺列（属性解析不到物理列）：{missing[:8]}"


def test_source_tables_are_claimed_as_data_products():
    missing = []
    for table in TABLES:
        row = execute_query(
            "SELECT id FROM adh_data_products WHERE datasource_name = %s AND physical_table = %s "
            "ORDER BY id DESC LIMIT 1", (DS_NAME, table), fetchone=True)
        if not row:
            missing.append(f"{DS_NAME}.{table}")
    assert not missing, (f"来源表未认领为数据产品 {missing}：activate 会被「未登记来源表」拦截，"
                        "跑脚本 products 步骤")


def test_dictionary_rows_bind_physical_columns_and_objects():
    for m in spec.METRICS:
        row = execute_query(
            "SELECT target_table, bound_object_key, formula, is_active FROM adh_metrics WHERE name = %s",
            (m["name"],), fetchone=True)
        assert row, f"指标缺失: {m['name']}"
        assert str(row["target_table"] or "") == m["table"], (
            f"指标 {m['name']} target_table={row['target_table']!r} ≠ {m['table']}"
            "（planner 按物理表加载字典，空/错值等于不可编译）")
        assert str(row["bound_object_key"] or "") == m["object"], f"指标 {m['name']} 归属对象错"
        assert int(row["is_active"] or 0) == 1, f"指标 {m['name']} 未启用"
        assert "del_flag = 0" in str(row["formula"] or ""), (
            f"指标 {m['name']} 口径丢了未删除基线（源表注释写反，del_flag=0 才是活跃集）")

    for d in spec.DIMENSIONS:
        row = execute_query(
            "SELECT target_table, target_column, bound_object_key, is_active FROM adh_dimensions "
            "WHERE name = %s", (d["name"],), fetchone=True)
        assert row, f"维度缺失: {d['name']}"
        assert str(row["target_table"] or "") == d["table"], f"维度 {d['name']} target_table 错"
        assert str(row["target_column"] or "") == d["column"], f"维度 {d['name']} target_column 错"
        assert str(row["bound_object_key"] or "") == d["object"], f"维度 {d['name']} 归属对象错"
        assert int(row["is_active"] or 0) == 1, f"维度 {d['name']} 未启用"


def test_pii_columns_have_table_level_sensitive_rules():
    """全局基线只按精确列名（email/phone/…），first_name/last_name 等不在其中 → 必须表级补。"""
    ds_id = _ds_id()
    missing, wrong_mask = [], []
    for table, column, _level, mask, _desc in spec.SENSITIVE_FIELDS:
        row = execute_query(
            "SELECT mask_type FROM adh_sensitive_fields "
            "WHERE datasource_id = %s AND table_name = %s AND column_name = %s",
            (ds_id, table, column), fetchone=True)
        if not row:
            missing.append(f"{table}.{column}")
        elif str(row["mask_type"]) != mask:
            wrong_mask.append(f"{table}.{column}={row['mask_type']}≠{mask}")
    assert not missing, f"PII 列未登记敏感规则 {missing}（表级规则是唯一产品入口：敏感数据页）"
    assert not wrong_mask, f"敏感规则脱敏方式与规格不符 {wrong_mask}"


def test_arms_model_renamed_and_old_name_retired():
    new_row = execute_query(
        "SELECT id, status FROM adh_ontology_models WHERE name = %s ORDER BY updated_at DESC LIMIT 1",
        (ARMS_NEW,), fetchone=True)
    assert new_row, f"未找到改名后的「{ARMS_NEW}」"
    assert str(new_row["status"]) == "active", "可观测模型应保持 active（改名不得影响可查性）"
    old_row = execute_query(
        "SELECT id FROM adh_ontology_models WHERE name = %s LIMIT 1", (ARMS_OLD,), fetchone=True)
    assert not old_row, (f"仍存在旧名模型「{ARMS_OLD}」(id={old_row['id']})：改名脚本之外的路径又建了一个旧名模型"
                         "（脚本按新名定位，旧名会变成没人维护的孤岛）")


def test_cloud_doc_redacts_physical_table_names():
    """上云文档只说业务名：物理表名不得随知识库下发给 LLM（护栏 §7）。"""
    from backend.modules.catalog.services import ontology_kb_sync as kb

    text = kb._render_cloud_doc(_model_row())
    leaked = [t for t in TABLES if t in text]
    assert not leaked, f"上云文档出现物理表名 {leaked}：描述/别名里不要写物理表"


def test_semantic_binding_is_site_scoped():
    """正例：中国站可解析；负例：其它站点源必须被拒（宁缺勿错，不拿别站数据当本站答案）。"""
    from backend.semantics.binding_resolver import resolve_binding

    ds_id = _ds_id()
    hit, why = resolve_binding("official_website_question", datasource_id=ds_id)
    assert hit, f"中国站源解析不到 official_website_question（对象/绑定/站点声明三者之一缺失）：{why}"

    other = execute_query(
        "SELECT id, name, site_code FROM adh_datasources "
        "WHERE name <> %s AND COALESCE(site_code, '') NOT IN ('', 'cn') "
        "ORDER BY id LIMIT 1", (DS_NAME,), fetchone=True)
    if not other:
        pytest.skip("环境无其它站点数据源，跨站负向用例无从执行（不当作通过）")
    rejected, reasons = resolve_binding("official_website_question", datasource_id=int(other["id"]))
    assert rejected is None, (f"「{other['name']}」(站点 {other['site_code']}) 竟能解析中国站官网业务本体"
                              f"→ 跨站路由边界破了")
    assert any("不适用站点" in str(w) for w in reasons), (
        f"跨站拒绝原因缺少站点声明提示（用户无法判断该去哪个站问）：{reasons}")