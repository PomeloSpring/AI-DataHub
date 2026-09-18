"""Palantir Ontology YAML importer — 把 ontology/*.yaml 归一化为 canonical 本体模型。

ontology/ 目录遵循 Palantir Ontology 规范（object_types / properties / link_types /
cross_domain_links / computed_properties / metrics），与系统内部的 canonical 本体
JSON（见 ontology_service._SYSTEM_PROMPT，亦是前端与 RDF 入图所消费的结构）不同。

本模块作为二者的桥：读取 Palantir YAML → 归一化为 canonical doc → 落
adh_ontology_models（active）+ 展开对象写 adh_ontology_objects → 触发图谱重建，
使 datamind 的 `ontology_traversal` 检索策略可直接以该本体为语义标准。

只读导入：actions（07）不参与检索，导入时忽略（预留给 agent 执行工具单独入库）。
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from services.shared.common.db.metadata_db import get_metadata_conn
from services.datacatalog.services import ontology_service

logger = logging.getLogger(__name__)

# 默认本体目录：repo 根 /ontology
_DEFAULT_ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"

# Palantir 类型 → canonical 类型
_TYPE_MAP = {
    "integer": "int", "int": "int", "long": "int",
    "float": "float", "double": "float", "decimal": "float", "number": "float",
    "string": "string", "text": "string",
    "boolean": "boolean", "bool": "boolean",
    "timestamp": "datetime", "datetime": "datetime", "date": "date",
    "enum": "string",
}

# Palantir 基数 → 紧凑记法
_CARD_MAP = {
    "one_to_many": "1:N", "many_to_one": "N:1",
    "many_to_many": "M:N", "one_to_one": "1:1",
}

_TABLE_SUFFIX_NOISE = re.compile(r"[\s(（].*$")

# 中文别名兜底表（skos:altLabel 用；YAML 里的 `aliases:` 优先级更高，会合并去重）。
# 语义层与 ontology_traversal 依靠此表让中英混排问数都能命中同一对象。
_ALIAS_ZH: dict[str, list[str]] = {
    "user":                  ["用户", "客户", "账号"],
    "company":               ["企业", "机构", "公司"],
    "role":                  ["角色", "权限角色"],
    "department":            ["部门", "科室"],
    "patient":               ["患者", "病人"],
    "partnership":           ["合作伙伴", "合作关系"],
    "dentist":               ["牙医", "医师"],
    "case":                  ["案例", "口扫案例", "病例"],
    "case_file":             ["案例文件", "病例附件"],
    "casefile":              ["案例文件", "病例附件"],
    "case_treatment":        ["治疗", "治疗方案"],
    "casetreatment":         ["治疗", "治疗方案"],
    "case_recycle_bin":      ["案例回收站", "回收站"],
    "caserecyclebin":        ["案例回收站", "回收站"],
    "order":                 ["订单"],
    "order_item":            ["订单明细", "订单行"],
    "orderitem":             ["订单明细", "订单行"],
    "equipment":             ["设备", "扫描仪"],
    "product":               ["产品", "商品"],
    "work_order":            ["工单"],
    "workorder":             ["工单"],
    "warranty":              ["保修", "质保"],
    "isv":                   ["独立软件厂商", "服务商"],
    "software_version":      ["软件版本", "版本"],
    "softwareversion":       ["软件版本", "版本"],
    "ai_conversation":       ["AI 会话", "智能助手会话"],
    "aiconversation":        ["AI 会话", "智能助手会话"],
    "disk_file":             ["网盘文件", "素材文件"],
    "diskfile":              ["网盘文件", "素材文件"],
    "dc_case_record":        ["数采案例记录"],
    "dccaserecord":          ["数采案例记录"],
    "observability_event":   ["可观测事件", "前端埋点"],
    "observabilityevent":    ["可观测事件", "前端埋点"],
    "api_request":           ["API 请求", "接口请求"],
    "apirequest":            ["API 请求", "接口请求"],
    "system_log":            ["系统日志"],
    "systemlog":             ["系统日志"],
}


def _derive_aliases(name: str, api_name: str, primary_table: str,
                    yaml_aliases: list | None) -> list[str]:
    """合并 YAML 显式声明、英文名/api_name、常见中文别名，返回去重后的列表。"""
    out: list[str] = []
    seen: set[str] = set()

    def push(v):
        v = str(v).strip() if v is not None else ""
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)

    for a in (yaml_aliases or []):
        push(a)
    push(name)
    push(api_name)
    for a in _ALIAS_ZH.get((api_name or "").strip().lower(), []):
        push(a)
    if primary_table:
        push(primary_table)
    return out


def _clean_table(raw) -> str:
    """从 source.table 提取物理表名：去库前缀、去 `(分表 …)` 等后缀注释。"""
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("sls://") or s.startswith("kafka://"):
        return ""  # 日志/流式源无稳定物理表
    s = _TABLE_SUFFIX_NOISE.sub("", s)          # 去掉空格/括号之后的说明
    return s.rsplit(".", 1)[-1]                  # 去掉 db 前缀，取最后一段


def _map_type(palantir_type: str) -> str:
    return _TYPE_MAP.get(str(palantir_type or "").lower(), "string")


def _enum_list(values) -> list[str]:
    """Palantir `values: {0: 未知, 1: 男}` → ["0=未知", "1=男"]。"""
    if not isinstance(values, dict):
        return []
    return [f"{k}={v}" for k, v in values.items()]


def _iter_yaml_docs(dir_path: Path) -> list[dict]:
    docs = []
    for fp in sorted(dir_path.glob("*.yaml")):
        try:
            data = yaml.safe_load(fp.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Skip unparsable ontology file %s: %s", fp.name, e)
            continue
        if isinstance(data, dict):
            data["_file"] = fp.name
            docs.append(data)
    return docs


def _source_table(obj: dict) -> str:
    src = obj.get("source")
    if isinstance(src, dict):
        return _clean_table(src.get("table"))
    return _clean_table(src)


def _load_table_info_map(datasource_id: int) -> dict[str, dict]:
    """一次性取回该 datasource 下已有 size_class/query_mode 的物理表列表，
    供导入时给每个对象写 execution_binding（避免逐对象 N+1 查库）。

    返回: {table_name -> {physical_table,db_name,catalog_name,size_class,query_mode,
                          allow_full_scan,est_rows,grain,dw_layer,catalog_id,source_datasource_id}}
    """
    sql = (
        "SELECT t.table_name, t.size_class, t.query_mode, t.allow_full_scan, "
        "       t.est_rows, t.grain, t.dw_layer, t.catalog_id, t.source_datasource_id, "
        "       d.database_name AS db_name, c.catalog_name AS catalog_name "
        "FROM adh_table_info t "
        "LEFT JOIN adh_datasources d ON d.id = t.datasource_id "
        "LEFT JOIN adh_catalogs c ON c.id = t.catalog_id "
        "WHERE t.datasource_id = %s"
    )
    try:
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (datasource_id,))
                rows = cur.fetchall()
    except Exception as e:  # 元数据表缺失/无权限时不阻断导入，仅写 unbound
        logger.warning("[ontology-import] load table_info map failed (%s)", e)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out[r["table_name"]] = r
    return out


def _datasource_name_by_id(datasource_id: int) -> str:
    """反查数据源名（name 为全局唯一标识）。查不到返回 ''，不阻断导入。"""
    if not datasource_id:
        return ""
    try:
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM adh_datasources WHERE id = %s LIMIT 1",
                    (datasource_id,),
                )
                row = cur.fetchone()
        return (row or {}).get("name") or ""
    except Exception as e:  # noqa: BLE001 — 查不到名字不阻断，回落空串
        logger.warning("[ontology-import] load datasource name for id=%s failed: %s", datasource_id, e)
        return ""


def _tpl_ref_of_obj(o: dict) -> str:
    """从 yaml 对象声明中提取 SQL 模板引用（execution_template 优先，兼容 sql_template）。

    写法: execution_template: tpl-xxx 或 execution_template: {template_ref: tpl-xxx}。
    """
    for field in ("execution_template", "sql_template"):
        v = o.get(field)
        ref = (v.get("template_ref") or v.get("ref")) if isinstance(v, dict) else v
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    return ""


def _build_execution_binding(primary_table: str, datasource_id: int,
                             table_info: dict[str, dict],
                             datasource_name: str = "",
                             template_ref: str = "") -> dict:
    """面向单个对象产出 execution_binding（canonical 内嵌形式，与
    adh_ontology_bindings 行保持字段同名，为后续解析器提供三级 fallback 中的第一级）。
    """
    if not primary_table:
        if template_ref:
            # 无物理表但声明了 SQL 模板: 编译期按 template_ref 渲染（planner._plan_template）
            return {
                "datasource_id": datasource_id,
                "datasource_name": datasource_name,
                "physical_table": "",
                "catalog_ref": "",
                "bind_kind": "sql_template",
                "template_ref": template_ref,
                "query_mode": "raw_source",
                "size_class": None,
                "allow_full_scan": 1,
                "permission_tokens": [],
                "masked_columns": [],
                "sync_state": "bound",
            }
        return {
            "datasource_id": datasource_id,
            "datasource_name": datasource_name,
            "physical_table": "",
            "catalog_ref": "",
            "bind_kind": "primary",
            "query_mode": "raw_source",
            "size_class": None,
            "allow_full_scan": 1,
            "permission_tokens": [],
            "masked_columns": [],
            "sync_state": "unbound",
        }
    row = table_info.get(primary_table) or {}
    catalog = row.get("catalog_name") or "adh"
    db = row.get("db_name") or ""
    catalog_ref = ".".join([catalog, db, primary_table]) if db else f"{catalog}..{primary_table}"
    return {
        "datasource_id": datasource_id,
        "datasource_name": datasource_name,
        "catalog_id": row.get("catalog_id"),
        "catalog_name": catalog,
        "db_name": db,
        "physical_table": primary_table,
        "catalog_ref": catalog_ref,
        "bind_kind": "primary",
        "template_ref": template_ref or "",
        "query_mode": row.get("query_mode") or "raw_source",
        "size_class": row.get("size_class"),
        "allow_full_scan": int(row.get("allow_full_scan") if row.get("allow_full_scan") is not None else 1),
        "est_rows": row.get("est_rows"),
        "grain": row.get("grain") or "detail",
        "dw_layer": row.get("dw_layer"),
        "source_datasource_id": row.get("source_datasource_id"),
        "permission_tokens": [],
        "masked_columns": [],
        "rls_policy_ref": None,
        # 未命中元数据时标 drifted（不静默），便亍 Phase 4 拦截与图谱徒章
        "sync_state": "bound" if row else "drifted",
    }


def _build_link(name: str, target: str, *, join: str, cardinality: str,
                description: str) -> dict:
    return {
        "target": target,
        "type": name or "references",
        "join": join or "",
        "cardinality": _CARD_MAP.get(cardinality, cardinality or ""),
        "description": description or "",
    }


def palantir_to_canonical(docs: list[dict], datasource_id: int,
                          table_info: dict[str, dict] | None = None) -> dict:
    """把已解析的 Palantir YAML 文档集归一化为 canonical 本体 doc。

    table_info 为导入前一次性拉取的物理表元数据字典（_load_table_info_map）；
    为空时仍产出 unbound 绑定，不阻断预览/测试。
    """
    table_info = table_info or {}
    # name 为数据源全局唯一标识：随 binding 一起持久化，解析时用它映射到当前有效 id
    ds_name = _datasource_name_by_id(datasource_id)
    objects: dict[str, dict] = {}      # key → canonical object（保序）
    name_to_key: dict[str, str] = {}   # Palantir Name → key（解析 link target）
    domain = ""
    platform = ""
    description = ""

    # ── Pass 1: 收集所有 object_types，建 Name→key 映射 ──
    for doc in docs:
        domain = domain or (doc.get("domain") or "")
        # _index.yaml 携带整份本体的 platform 标题；用它作为合并模型的域名，
        # 避免用第一个分域文件的 domain(如 identity) 给全量模型贴上误导性标签。
        platform = platform or (doc.get("platform") or "")
        description = description or (doc.get("description") or "")
        for o in doc.get("object_types") or []:
            name = o.get("name") or ""
            key = (o.get("api_name") or name.lower()).strip()
            if not key or key in objects:
                continue
            name_to_key[name] = key

    # ── Pass 2: 构建 canonical 对象 ──
    for doc in docs:
        for o in doc.get("object_types") or []:
            name = o.get("name") or ""
            key = (o.get("api_name") or name.lower()).strip()
            if not key or key in objects:
                continue
            primary_table = _source_table(o)
            tpl_ref = _tpl_ref_of_obj(o)

            properties = []
            for p in o.get("properties") or []:
                pname = p.get("name") or ""
                column = f"{primary_table}.{pname}" if primary_table and pname else pname
                properties.append({
                    "column": column,
                    "name": pname,
                    "type": _map_type(p.get("type")),
                    "is_key": bool(p.get("is_primary_key")),
                    "description": (p.get("description") or "").strip(),
                    "enum": _enum_list(p.get("values")),
                })

            metrics = []
            for cp in o.get("computed_properties") or []:
                metrics.append({
                    "name": cp.get("name") or "",
                    "formula": (cp.get("expression") or "").strip(),
                    "description": (cp.get("description") or "").strip(),
                })

            objects[key] = {
                "key": key,
                "display_name": name or key,
                "aliases": _derive_aliases(name, key, primary_table, o.get("aliases")),
                "description": (o.get("description") or "").strip(),
                "primary_table": primary_table,
                "source_ref": (o.get("source") or {}).get("table") if isinstance(o.get("source"), dict) else o.get("source") or "",
                "execution_binding": _build_execution_binding(
                    primary_table, datasource_id, table_info, datasource_name=ds_name,
                    template_ref=tpl_ref),
                "properties": properties,
                "links": [],
                "metrics": metrics,
            }
            # 域内 link_types
            for lt in o.get("link_types") or []:
                tgt_raw = lt.get("target") or ""
                objects[key]["links"].append(_build_link(
                    lt.get("name") or "", name_to_key.get(tgt_raw, tgt_raw.lower()),
                    join=lt.get("join") or "", cardinality=lt.get("cardinality") or "",
                    description=lt.get("description") or "",
                ))

    # ── Pass 3: 跨域 link（06-link-types.yaml）+ 域指标（各文件 metrics）──
    for doc in docs:
        for cl in doc.get("cross_domain_links") or []:
            src_raw = cl.get("from") or ""
            tgt_raw = cl.get("to") or ""
            src_key = name_to_key.get(src_raw, src_raw.lower())
            obj = objects.get(src_key)
            if not obj:
                continue
            obj["links"].append(_build_link(
                cl.get("name") or "", name_to_key.get(tgt_raw, tgt_raw.lower()),
                join=cl.get("join") or "", cardinality=cl.get("cardinality") or "",
                description=cl.get("description") or cl.get("note") or "",
            ))

        for m in doc.get("metrics") or []:
            ot_raw = m.get("object_type") or ""
            obj = objects.get(name_to_key.get(ot_raw, ot_raw.lower()))
            if not obj:
                continue
            formula = (m.get("expression") or "").strip()
            filters = m.get("filters") or []
            if filters:
                formula = f"{formula} WHERE {' AND '.join(str(f) for f in filters)}"
            obj["metrics"].append({
                "name": m.get("name") or "",
                "formula": formula,
                "description": (m.get("description") or "").strip(),
            })

    # ── 链接去重 ──
    for obj in objects.values():
        seen = set()
        deduped = []
        for lk in obj["links"]:
            sig = (lk["target"], lk["type"], lk["join"])
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(lk)
        obj["links"] = deduped

    return {
        "datasource_id": datasource_id,
        "datasource_name": ds_name,
        # 合并模型横跨多个分域，用 platform 标题(或默认中文全量标签)作为域名，不沿用单域名
        "domain": platform or "联耀医疗口扫云平台-全量本体",
        "description": (description or "").strip(),
        "objects": list(objects.values()),
    }


def parse_palantir_dir(dir_path: str | Path = None, datasource_id: int = 0) -> dict:
    """解析目录并返回 canonical doc（不落库，供预览/测试）。

    传入 datasource_id 时会预拉一次 table_info 以给每个对象写 execution_binding；
    datasource_id=0 时保持旧行为（canonical 中 binding 为 unbound）。
    """
    d = Path(dir_path) if dir_path else _DEFAULT_ONTOLOGY_DIR
    docs = _iter_yaml_docs(d)
    if not docs:
        raise ValueError(f"目录 {d} 下未找到可解析的 ontology YAML 文件")
    table_info = _load_table_info_map(datasource_id) if datasource_id else {}
    return palantir_to_canonical(docs, datasource_id, table_info)


def _upsert_active_model(doc: dict, created_by: str) -> int:
    """写入新的 draft 本体模型，返回 model_id（旧 active 归档交给 activate 完成）。

    历史行为：这里直接写 status='active' 会让 ontology_service.activate() 因为
    已为 active 而 short-circuit，导致 adh_ontology_objects 不展开。
    修正：先写 draft，再让 activate() 走完整的“归档旧 active + 本行转 active + 展开对象”路径。
    """
    datasource_id = doc["datasource_id"]
    now = ontology_service._now()
    model_id = ontology_service._gen_id()
    json_content = json.dumps(doc, ensure_ascii=False, indent=2)
    yaml_content = ontology_service.to_yaml(doc)
    md_content = ontology_service.to_md(doc)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_ontology_models "
                "(id, datasource_id, name, status, json_content, yaml_content, md_content, "
                "object_count, created_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s)",
                (model_id, datasource_id, f"{doc.get('datasource_name') or doc.get('domain') or '本体模型'}-全量本体",
                 json_content, yaml_content, md_content,
                 len(doc["objects"]), created_by, now, now),
            )
        conn.commit()
    return model_id


def import_palantir_yaml(dir_path: str | Path = None, datasource_id: int = 0,
                         created_by: str = "", rebuild_graph: bool = True) -> dict:
    """导入 Palantir YAML 为 active 本体模型并（可选）重建图谱。

    Args:
        dir_path: ontology YAML 目录，默认为 repo 根 /ontology。
        datasource_id: 绑定的数据源 ID（命名图作用域）。
        created_by: 创建人。
        rebuild_graph: 是否触发 GraphBuilder 重建（把本体编入 Oxigraph）。

    Returns:
        {model_id, datasource_id, object_count, link_count, graph} 摘要。
    """
    if not datasource_id:
        raise ValueError("datasource_id 必填：本体需绑定到某个数据源的命名图")

    doc = parse_palantir_dir(dir_path, datasource_id)
    if not doc["objects"]:
        raise ValueError("未从 YAML 解析出任何 object_types")

    model_id = _upsert_active_model(doc, created_by)
    ontology_service.activate(model_id)  # 复用：归档旧 active、展开对象写 adh_ontology_objects

    binding_stats = _persist_bindings(doc, model_id)

    link_count = sum(len(o["links"]) for o in doc["objects"])

    graph_summary = {"skipped": True}
    if rebuild_graph:
        graph_summary = _rebuild_graph(datasource_id)

    logger.info(
        "[ontology-import] Palantir YAML → model %s (ds=%s): %d objects, %d links, %d bindings (%d drifted)",
        model_id, datasource_id, len(doc["objects"]), link_count,
        binding_stats.get("written", 0), binding_stats.get("drifted", 0),
    )
    return {
        "model_id": model_id,
        "datasource_id": datasource_id,
        "object_count": len(doc["objects"]),
        "link_count": link_count,
        "bindings": binding_stats,
        "graph": graph_summary,
    }


def _persist_bindings(doc: dict, model_id: int) -> dict:
    """将 canonical objects[].execution_binding 展开写入 adh_ontology_bindings，
    同时回写到 adh_ontology_objects.execution_binding（内嵌列）。

    幂等：先按 model_id 下已有行 → 重新写入。返回写入/漂移计数供导入日志。
    """
    datasource_id = doc.get("datasource_id") or 0
    now = ontology_service._now()
    written = drifted = unbound = 0
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            # 1. 本模型旧行失效（保留历史，避免下游误读）
            cur.execute(
                "UPDATE adh_ontology_bindings SET status = 'deprecated', updated_at = %s "
                "WHERE model_id = %s AND status = 'active'",
                (now, model_id),
            )
            # 2. 逐对象写入 active 绑定行
            for obj in doc.get("objects") or []:
                binding = obj.get("execution_binding") or {}
                if not binding:
                    continue
                state = binding.get("sync_state") or "unbound"
                if state == "drifted":
                    drifted += 1
                elif state == "unbound":
                    unbound += 1
                cur.execute(
                    "INSERT INTO adh_ontology_bindings "
                    "(id, model_id, object_key, datasource_id, datasource_name, catalog_ref, bind_kind, "
                    " template_ref, physical_table, join_expr, column_map, query_mode, size_class, "
                    " allow_full_scan, max_rows, timeout_sec, permission_tokens, "
                    " rls_policy_ref, masked_columns, sync_state, last_verified_at, "
                    " status, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,%s,'active',%s,%s)",
                    (
                        ontology_service._gen_id() + written,
                        model_id,
                        obj.get("key", ""),
                        datasource_id,
                        # 存 name 作为稳定关联键：数据源删除重建后仍能按名重关联
                        binding.get("datasource_name") or doc.get("datasource_name") or "",
                        binding.get("catalog_ref", ""),
                        binding.get("bind_kind", "primary"),
                        binding.get("template_ref") or None,
                        binding.get("physical_table", ""),
                        json.dumps(_column_map_from_props(obj), ensure_ascii=False),
                        binding.get("query_mode", "raw_source"),
                        binding.get("size_class"),
                        int(binding.get("allow_full_scan") or 0),
                        json.dumps(binding.get("permission_tokens") or [], ensure_ascii=False),
                        binding.get("rls_policy_ref"),
                        json.dumps(binding.get("masked_columns") or [], ensure_ascii=False),
                        state,
                        now if state == "bound" else None,
                        now, now,
                    ),
                )
                written += 1
            # 3. 同步回写 adh_ontology_objects.execution_binding 内嵌列
            for obj in doc.get("objects") or []:
                binding = obj.get("execution_binding") or {}
                if not binding:
                    continue
                cur.execute(
                    "UPDATE adh_ontology_objects SET execution_binding = %s "
                    "WHERE model_id = %s AND object_key = %s",
                    (json.dumps(binding, ensure_ascii=False), model_id, obj.get("key", "")),
                )
        conn.commit()
    return {"written": written, "drifted": drifted, "unbound": unbound}


def _column_map_from_props(obj: dict) -> dict:
    """从 properties[].column 提取 {属性名: 物理列名}（已剔除表前缀）。"""
    table = obj.get("primary_table") or ""
    cmap: dict[str, str] = {}
    for p in obj.get("properties") or []:
        col = str(p.get("column") or "")
        pname = str(p.get("name") or "")
        if not pname:
            continue
        phys = col.split(".", 1)[-1] if "." in col else col
        if phys:
            cmap[pname] = phys
    _ = table  # 保留参数位，为后续同名列消歧预留
    return cmap


def _rebuild_graph(datasource_id: int) -> dict:
    """best-effort 触发图谱重建（本体经 graph_builder._merge_ontology_models 入图）。"""
    try:
        from services.graphservice.graph_service import GraphService
        resp = GraphService().sync_from_metadata(datasource_id)
        return {
            "success": bool(getattr(resp, "success", False)),
            "tables": getattr(resp, "tables", 0),
            "relations": getattr(resp, "relations", 0),
            "message": getattr(resp, "message", ""),
        }
    except Exception as e:
        logger.warning("[ontology-import] graph rebuild failed (model saved, "
                       "trigger POST /api/graph/sync?datasource_id=%s manually): %s",
                       datasource_id, e)
        return {"success": False, "error": str(e)}
