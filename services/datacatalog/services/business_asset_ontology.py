"""业务本体（数据资产路由图）自动生成：从系统治理资产构建路由索引层。

业务本体的定位（与源本体区分）：**告诉 RAG/LLM 该去哪个数据源找数据**。
它不是任何单个数据源的业务域复制件，而是系统级数据资产全景，节点来自：
- 数据源（adh_datasources）：注册的数据源及其业务内容（数据产品/站点/业务域）
- 数据集（adh_datasets）：发布的分析数据集
- 同步任务（adh_sync_tasks / adh_etl_tasks）：数据流转
- 数据安全（adh_sensitive_fields）：敏感治理概况
- 数据质量（adh_quality_rules / adh_quality_results）：质量规则与结果
- 用户权限（adh_user_roles ⋈ adh_role_datasource_access）：谁在用哪个源
- 血缘（adh_data_lineage）：数据源之间的流转联系（links / 跨源场景）

"日本站点"这类业务语义从**配置推导**（数据产品 site/domain、描述等），落入对象
别名/描述与 filter_hints；RAG 命中节点后按 route.datasource_name 路由到目标数据源。

脱敏纪律：生成内容全链路只含 name 级/业务语义——物理表/列名不进 doc（治理表可
随时重查），to_cloud_md 无需特判即满足护栏 §7。生成完全确定性（无 LLM）；
落库走 save_draft/activate 级联（canonical 单一事实源纪律，不得直写派生表）。
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_DS_KIND_CN = {"mysql": "MySQL", "doris": "Doris", "sls": "SLS 日志", "postgres": "PostgreSQL",
               "oracle": "Oracle", "sqlserver": "SQLServer"}


def _slug(text: str, fallback: str) -> str:
    """资产名 → snake_case key 片段（非 ASCII 丢弃，空则用 fallback 序号）。"""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "")).strip("_").lower()
    return s or fallback


def _q(sql: str, params: tuple = ()) -> list:
    from services.shared.common.db.metadata_db import get_metadata_conn
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _collect() -> dict:
    """一次性收集六类治理资产 + 血缘（每类查询失败显式抛出，不静默跳段）。"""
    data: dict = {}
    data["datasources"] = _q(
        "SELECT id, name, db_type, database_name, is_default FROM adh_datasources ORDER BY name")
    ds_ids = [r["id"] for r in data["datasources"]]
    ds_names = [r["name"] for r in data["datasources"]]

    data["products"] = _q(
        "SELECT product_name, display_name, description, site, domain, classification, "
        "datasource_name FROM adh_data_products WHERE status = 'active'")
    data["datasets"] = _q(
        "SELECT id, name, description, source_type, object_key, datasource_id, chart_type "
        "FROM adh_datasets WHERE status = 'active' ORDER BY name")
    data["sync_tasks"] = _q(
        "SELECT name, description, source_datasource_name, source_table, "
        "target_datasource_name, target_table, sync_mode, schedule_cron, status "
        "FROM adh_sync_tasks WHERE is_active = 1 ORDER BY name")
    data["etl_tasks"] = _q(
        "SELECT name, description, task_type, source_datasource_id, source_tables, "
        "target_datasource_id, target_tables, status FROM adh_etl_tasks WHERE is_active = 1")
    data["quality_rules"] = _q(
        "SELECT id, rule_name, description, rule_type, target_datasource_id, target_table, "
        "severity, is_active FROM adh_quality_rules ORDER BY rule_name")
    data["quality_results"] = _q(
        "SELECT rule_id, passed, pass_rate, check_time FROM adh_quality_results "
        "ORDER BY check_time DESC LIMIT 200")
    data["sensitive"] = _q(
        "SELECT datasource_id, table_name, sensitivity_level FROM adh_sensitive_fields")
    data["grants"] = _q(
        "SELECT rda.datasource_id, ds.name AS datasource_name, u.username, r.name AS role_name "
        "FROM adh_role_datasource_access rda "
        "JOIN adh_user_roles ur ON ur.role_id = rda.role_id "
        "JOIN adh_users u ON u.id = ur.user_id "
        "JOIN adh_roles r ON r.id = rda.role_id "
        "JOIN adh_datasources ds ON ds.id = rda.datasource_id")
    data["lineage"] = _q(
        "SELECT source_type, source_id, source_name, target_type, target_id, target_name, "
        "relation_type, description FROM adh_data_lineage WHERE is_active = 1")
    data["ds_names"] = ds_names
    data["ds_ids"] = ds_ids
    return data


def _source_ontology_by_ds() -> dict:
    """{datasource_name: active 源本体模型名}（route.source_ontology 可选补全）。"""
    from services.datacatalog.services.ontology_service import _active_source_ontology_index
    out: dict = {}
    for name, info in (_active_source_ontology_index() or {}).items():
        ds = str(info.get("datasource_name") or "")
        if ds and ds not in out:
            out[ds] = name
    return out


def _ds_display_name(row: dict) -> str:
    return f"{row['name']}（{_DS_KIND_CN.get(str(row.get('db_type') or ''), row.get('db_type'))}）"


def _build_ds_object(ds: dict, data: dict, src_by_ds: dict) -> dict:
    """数据源节点：业务内容聚合 + 站点/域语义 + 授权用户（全部 name 级）。"""
    name = str(ds["name"])
    products = [p for p in data["products"] if str(p.get("datasource_name") or "") == name]
    datasets = [d for d in data["datasets"] if int(d.get("datasource_id") or 0) == int(ds["id"])]
    syncs = [t for t in data["sync_tasks"]
             if str(t.get("target_datasource_name") or "") == name
             or str(t.get("source_datasource_name") or "") == name]
    etls = [t for t in data["etl_tasks"]
            if int(t.get("source_datasource_id") or 0) == int(ds["id"])
            or int(t.get("target_datasource_id") or 0) == int(ds["id"])]
    rules = [r for r in data["quality_rules"] if int(r.get("target_datasource_id") or 0) == int(ds["id"])]
    sens = [s for s in data["sensitive"] if int(s.get("datasource_id") or 0) == int(ds["id"])]
    grants = [g for g in data["grants"] if g.get("datasource_name") == name]

    sites = sorted({str(p["site"]) for p in products if str(p.get("site") or "").strip()})
    domains = sorted({str(p["domain"]) for p in products if str(p.get("domain") or "").strip()})
    classes = sorted({str(p["classification"]) for p in products if str(p.get("classification") or "").strip()})

    desc: list[str] = []
    desc.append(f"数据源「{name}」"
                f"（{_DS_KIND_CN.get(str(ds.get('db_type') or ''), ds.get('db_type'))} 库 {ds.get('database_name') or ''}"
                f"{'，默认源' if int(ds.get('is_default') or 0) else ''}）。")
    if products:
        seg = f"承载数据产品 {len(products)} 个"
        if classes:
            seg += f"（分级: {'、'.join(classes)}）"
        if sites:
            seg += f"；站点标注: {'、'.join(sites)}"
        if domains:
            seg += f"；业务域: {'、'.join(domains)}"
        desc.append(seg + "。")
    if datasets:
        desc.append("发布数据集: " + "、".join(f"「{d['name']}」" for d in datasets) + "。")
    if syncs:
        desc.append("同步任务: " + "、".join(f"「{t['name']}」" for t in syncs) + "。")
    if etls:
        desc.append("ETL 加工: " + "、".join(f"「{t['name']}」" for t in etls) + "。")
    if rules:
        desc.append(f"质量规则 {len(rules)} 项: " + "、".join(f"「{r['rule_name']}」" for r in rules) + "。")
    if sens:
        tables = {str(s.get("table_name") or "") for s in sens}
        levels = sorted({str(s.get("sensitivity_level") or "") for s in sens})
        desc.append(f"敏感字段治理 {len(sens)} 项（涉及 {len(tables)} 张表，级别 {'、'.join(levels)}），"
                    "查询会按敏感基线自动屏蔽/脱敏。")
    if grants:
        who = sorted({f"{g['username']}（{g['role_name']}）" for g in grants})
        desc.append("授权用户: " + "、".join(who) + "（按角色授权裁决）。")

    aliases = [name, str(ds.get("database_name") or ""), str(ds.get("db_type") or "")]
    aliases += sites + domains
    obj: dict = {
        "key": f"ds_{_slug(name, 'src')}",
        "display_name": _ds_display_name(ds),
        "aliases": sorted({a for a in aliases if a}),
        "description": "".join(desc),
        "route": {"mode": "source", "datasource_name": name},
    }
    if src_by_ds.get(name):
        obj["route"]["source_ontology"] = src_by_ds[name]
    hints = []
    if sites:
        hints.append({"dimension": "站点", "examples": sites})
    if domains:
        hints.append({"dimension": "业务域", "examples": domains})
    if hints:
        obj["route"]["filter_hints"] = hints
    return obj


def _build_asset_objects(data: dict, src_by_ds: dict) -> tuple[list, list]:
    """数据集/同步任务/ETL/质量规则节点（route 指向其数据源）。

    返回 (objects, notes)：归属源解析不到的资产**跳过并告警**（不产空 route、
    不猜归属——宁缺勿错），告警随生成结果显式带回。
    """
    objects: list = []
    notes: list = []
    ds_name_by_id = {int(r["id"]): str(r["name"]) for r in data["datasources"]}
    ds_names = set(ds_name_by_id.values())
    used_keys: set = set()

    def _key(prefix: str, name: str, idx: int) -> str:
        k = f"{prefix}_{_slug(name, str(idx))}"
        n = 2
        while k in used_keys:
            k = f"{prefix}_{_slug(name, str(idx))}_{n}"
            n += 1
        used_keys.add(k)
        return k

    def _route(ds_name: str, object_key: str = "") -> dict:
        r: dict = {"mode": "object_filter" if object_key else "source",
                   "datasource_name": ds_name}
        if object_key:
            r["object_key"] = object_key
        if src_by_ds.get(ds_name):
            r["source_ontology"] = src_by_ds[ds_name]
        return r

    for i, d in enumerate(data["datasets"], 1):
        ds_name = ds_name_by_id.get(int(d.get("datasource_id") or 0), "")
        if not ds_name:
            notes.append(f"数据集「{d.get('name')}」的归属数据源已删除，未生成节点（待人工处理）")
            logger.warning("[business-asset] 数据集「%s」的归属数据源已删除，跳过（不猜归属）", d.get("name"))
            continue
        source_label = {"semantic": "语义对象", "sql": "SQL", "both": "语义对象+SQL"}.get(
            str(d.get("source_type") or ""), str(d.get("source_type") or ""))
        chart_seg = f"，预设图表「{d.get('chart_type')}」" if d.get("chart_type") else ""
        objects.append({
            "key": _key("dataset", d["name"], i),
            "display_name": f"数据集·{d['name']}",
            "aliases": [str(d["name"])],
            "description": (f"发布数据集「{d['name']}」（归属数据源「{ds_name}」，来源 {source_label}{chart_seg}）。"
                            f"{d.get('description') or ''}"),
            "route": _route(ds_name, str(d.get("object_key") or "")),
        })

    for i, t in enumerate(data["sync_tasks"], 1):
        src_n, tgt_n = str(t.get("source_datasource_name") or ""), str(t.get("target_datasource_name") or "")
        if not (tgt_n in ds_names and tgt_n) and not (src_n in ds_names and src_n):
            notes.append(f"同步任务「{t.get('name')}」的源/目标数据源未对齐注册数据源，未生成节点（待人工处理）")
            continue
        objects.append({
            "key": _key("sync", t["name"], i),
            "display_name": f"同步任务·{t['name']}",
            "aliases": [str(t["name"])],
            "description": (f"同步任务「{t['name']}」：从数据源「{src_n or '?'}」同步到「{tgt_n or '?'}」"
                            f"（模式 {t.get('sync_mode') or ''}，调度 {t.get('schedule_cron') or '手动'}，"
                            f"状态 {t.get('status') or ''}）。{t.get('description') or ''}"),
            "route": _route(tgt_n or src_n),
        })

    for i, t in enumerate(data["etl_tasks"], 1):
        src_n = ds_name_by_id.get(int(t.get("source_datasource_id") or 0), "")
        tgt_n = ds_name_by_id.get(int(t.get("target_datasource_id") or 0), "")
        if not (tgt_n or src_n):
            notes.append(f"ETL 加工「{t.get('name')}」的源/目标数据源已删除，未生成节点（待人工处理）")
            logger.warning("[business-asset] ETL「%s」的源/目标数据源已删除，跳过（不猜归属）", t.get("name"))
            continue
        objects.append({
            "key": _key("etl", t["name"], i),
            "display_name": f"ETL 加工·{t['name']}",
            "aliases": [str(t["name"])],
            "description": (f"ETL 加工「{t['name']}」（{t.get('task_type') or ''}）："
                            f"从数据源「{src_n or '?'}」加工到「{tgt_n or '?'}」，状态 {t.get('status') or ''}。"
                            f"{t.get('description') or ''}"),
            "route": _route(tgt_n or src_n),
        })

    results_by_rule: dict = {}
    for r in data["quality_results"]:
        results_by_rule.setdefault(int(r.get("rule_id") or 0), r)   # 已按时间倒序，取最新
    for i, r in enumerate(data["quality_rules"], 1):
        ds_name = ds_name_by_id.get(int(r.get("target_datasource_id") or 0), "")
        if not ds_name:
            notes.append(f"质量规则「{r.get('rule_name')}」的目标数据源已删除，未生成节点（待人工处理）")
            logger.warning("[business-asset] 质量规则「%s」的目标数据源已删除，跳过（不猜归属）", r.get("rule_name"))
            continue
        latest = results_by_rule.get(int(r["id"]))
        quality_seg = ""
        if latest is not None:
            quality_seg = f"最近检查 {'通过' if int(latest.get('passed') or 0) else '未通过'}（通过率 {latest.get('pass_rate')}%）。"
        objects.append({
            "key": _key("quality", r["rule_name"], i),
            "display_name": f"质量规则·{r['rule_name']}",
            "aliases": [str(r["rule_name"])],
            "description": (f"数据质量规则「{r['rule_name']}」（类型 {r.get('rule_type') or ''}，"
                            f"严重级 {r.get('severity') or ''}，目标数据源「{ds_name}」）。{quality_seg}"
                            f"{r.get('description') or ''}"),
            "route": _route(ds_name),
        })
    return objects, notes


def _build_lineage(data: dict) -> tuple[list, list, list]:
    """血缘 → 数据源节点间边 + 跨源场景。返回 (edges, scenarios, unmatched)。

    edges 元素带 from_name/target_name（调用方归到对象 links）；节点按名称尽力
    匹配注册数据源（匹配不上不猜配，宁缺勿错——血缘节点名可能是描述名，
    如「主MySQL」）；table 级流转归并计数，不落表名（脱敏）。
    """
    ds_names = set(data["ds_names"])

    def _match(node_name: str) -> str:
        n = str(node_name or "").strip()
        return n if n in ds_names else ""

    edges: dict = {}          # (src_ds, tgt_ds) -> edge
    unmatched: list = []
    scenarios: dict = {}

    for row in data["lineage"]:
        st, tt = str(row.get("source_type") or ""), str(row.get("target_type") or "")
        src_ds = _match(row.get("source_name"))
        tgt_ds = _match(row.get("target_name"))
        rel = str(row.get("relation_type") or "feeds")
        if src_ds and tgt_ds and src_ds != tgt_ds:
            if st == "datasource" and tt == "datasource":
                edges[(src_ds, tgt_ds)] = {
                    "from_name": src_ds, "target_name": tgt_ds, "type": rel,
                    "description": "数据源间流转（数据流入）"}
            else:
                # table 级流转：归并计数（不落表名），两端都匹配到数据源才连边；
                # 已有 datasource 级边时不被表级计数覆盖
                prev = edges.get((src_ds, tgt_ds))
                if prev and "条表级" not in str(prev.get("description") or ""):
                    continue
                cnt = (int(str(prev.get("description")).split(" 条")[0]) + 1) if prev else 1
                edges[(src_ds, tgt_ds)] = {
                    "from_name": src_ds, "target_name": tgt_ds, "type": "produces",
                    "description": f"{cnt} 条表级加工流转汇聚成数据源间流向"}
        else:
            unmatched.append(str(row.get("source_name") or "") + "→" + str(row.get("target_name") or ""))

    for (src_ds, tgt_ds), e in edges.items():
        scenarios[f"flow_{_slug(src_ds, 'a')}_to_{_slug(tgt_ds, 'b')}"] = {
            "title": f"数据从「{src_ds}」流向「{tgt_ds}」",
            "goal": f"需要上游「{src_ds}」与下游「{tgt_ds}」联合分析时，逐源查询后在结论中合并对比",
            "route": {"sources": [{"datasource_name": src_ds}, {"datasource_name": tgt_ds}],
                      "join_hint": "上下游数据关联（业务口径关联，不跨源连表）"},
        }

    if unmatched:
        # 显式暴露未匹配血缘（不猜配、不静默丢弃），生成报告带回
        logger.info("[business-asset] %d 条血缘节点未匹配注册数据源（保留人工处理）: %s",
                    len(unmatched), unmatched[:10])
    return list(edges.values()), list(scenarios.values()), unmatched


def build_business_asset_doc() -> tuple[dict, list]:
    """构建业务本体 canonical doc。返回 (doc, notes)（notes=生成期显式告警）。"""
    data = _collect()
    src_by_ds = _source_ontology_by_ds()

    ds_objects = [_build_ds_object(ds, data, src_by_ds) for ds in data["datasources"]]
    asset_objects, asset_notes = _build_asset_objects(data, src_by_ds)
    edges, scenarios, unmatched = _build_lineage(data)
    notes = list(asset_notes)
    if unmatched:
        notes.append(f"血缘节点未匹配注册数据源（未建边，待人工对齐）: {'、'.join(unmatched[:10])}")

    # links 归属：跨源边挂到源侧数据源节点；资产节点挂归属数据源
    key_by_name = {str(ds["name"]): f"ds_{_slug(str(ds['name']), 'src')}"
                   for ds in data["datasources"]}
    links_by_from: dict = {}
    for e in edges:
        src_key = key_by_name.get(str(e.get("from_name") or ""))
        tgt_key = key_by_name.get(str(e.get("target_name") or ""))
        if not src_key or not tgt_key:
            continue
        links_by_from.setdefault(src_key, []).append({
            "target": tgt_key, "type": e.get("type") or "feeds",
            "cardinality": "N:1", "description": e.get("description") or ""})
    for obj in ds_objects:
        obj["links"] = links_by_from.get(obj["key"], [])
    # 资产节点 → 归属数据源的挂接关系（图谱有结构）
    for obj in asset_objects:
        ds_name = str((obj.get("route") or {}).get("datasource_name") or "")
        tgt_key = key_by_name.get(ds_name)
        if tgt_key and tgt_key != obj["key"]:
            obj["links"] = [{"target": tgt_key, "type": "belongs_to",
                             "cardinality": "N:1", "description": "资产归属该数据源"}]

    for s in scenarios:
        for entry in s["route"]["sources"]:
            n = entry["datasource_name"]
            if src_by_ds.get(n):
                entry["source_ontology"] = src_by_ds[n]

    doc = {
        "kind": "business",
        "domain": "系统数据资产",
        "sources": sorted(data["ds_names"]),
        "description": ("业务本体（数据资产路由图）：从数据源、数据集、同步任务、数据安全配置、"
                        "数据质量、用户权限策略与血缘自动生成。用途是告诉检索与 LLM "
                        "「该去哪个数据源找数据」——命中业务概念后按对象 route 路由到目标数据源，"
                        "再在该源的源本体/元数据内检索与执行。"),
        "objects": ds_objects + asset_objects,
        "scenarios": scenarios,
    }
    return doc, notes


def generate_business_asset_ontology(created_by: str = "") -> dict:
    """生成/覆盖业务本体（数据资产路由图）并走 save_draft/activate 级联。

    已有非归档 kind='business' 模型 → 覆盖其 json_content（save_draft 级联重建
    派生物）；无 → 新建并激活。返回 {model_id, object_count, notes}。
    """
    from services.datacatalog.services import ontology_service as osvc

    doc, notes = build_business_asset_doc()
    if not doc.get("objects"):
        raise ValueError("治理资产为空，无法生成业务本体（请先注册数据源/资产）")

    rows = _q("SELECT id, status FROM adh_ontology_models "
              "WHERE kind = 'business' AND status != 'archived' ORDER BY id LIMIT 1")
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    if rows:
        model_id = int(rows[0]["id"])
    else:
        from services.datacatalog.services.ontology_service import _gen_id, _now
        model_id = _gen_id()
        with __import__("services.shared.common.db.metadata_db",
                        fromlist=["get_metadata_conn"]).get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_ontology_models "
                    "(id, datasource_id, name, kind, domain, status, json_content, "
                    "yaml_content, md_content, object_count, created_by, created_at, updated_at) "
                    "VALUES (%s, 0, %s, 'business', %s, 'draft', %s, '', '', 0, %s, %s, %s)",
                    (model_id, "业务本体（数据资产路由图）", str(doc.get("domain") or ""),
                     payload, created_by or "business_asset_ontology", _now(), _now()))
            conn.commit()
        osvc.activate(model_id)

    result = osvc.save_draft(model_id, payload)
    if str((rows[0]["status"] if rows else "draft")) != "active":
        result = osvc.activate(model_id)
    return {"model_id": model_id,
            "object_count": len(doc.get("objects") or []),
            "notes": notes + list(result.get("validation_warnings") or [])}
