"""Palantir Ontology YAML importer — 把 ontology/*.yaml 归一化为 canonical 本体模型。

ontology/ 目录遵循 Palantir Ontology 规范（object_types / properties / link_types /
cross_domain_links / computed_properties / metrics），与系统内部的 canonical 本体
JSON（见 ontology_service.ONTOLOGY_SCHEMA_SPEC，亦是前端与 RDF 入图所消费的结构）不同。

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

from backend.common.db.metadata_db import get_metadata_conn
from backend.modules.catalog.services import ontology_service
from backend.modules.catalog.services import physical_ref as _physical_ref

logger = logging.getLogger(__name__)

# 默认本体目录：repo 根 /ontology
_DEFAULT_ONTOLOGY_DIR = Path(__file__).resolve().parents[4] / "ontology"

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

# 别名来源只认 YAML `aliases:` 与对象自身的 name/api_name/primary_table 变体。
# 历史硬编码兜底表 _ALIAS_ZH（暗登记处）已删除，存量别名经
# backend/scripts/migrate_alias_zh.py 收编进 canonical doc 对象 aliases。


def _derive_aliases(name: str, api_name: str, primary_table: str,
                    yaml_aliases: list | None) -> list[str]:
    """合并 YAML 显式声明、英文名/api_name，返回去重后的列表。"""
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


def _enum_list(values) -> dict:
    """Palantir `values: {0: 未知, 1: 男}` → {"0": "未知", "1": "男"}（与 value_labels 同构）。"""
    if not isinstance(values, dict):
        return {}
    return {str(k): str(v) for k, v in values.items()}


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


def object_physical_tables(datasource_id: int = 0, model_id: int = 0) -> dict[str, str]:
    """对象 key → 物理表（部署绑定层 `adh_ontology_bindings` 为唯一事实源）。

    本体 doc 已不再携带物理绑定（v0.5 B6b），但「属性登记的带点列名怎么裁决」
    必须先知道该对象的物理表（`logstore-rum.view.url` 的表前缀是 `logstore-rum`，
    而 `view.url` 本身是列名本体）。多站部署同一对象只对应一张同构表，
    出现多张不同表时该对象不返回（歧义不猜，由调用方按未验证口径处理）。
    查不到返回空 dict（不阻断保存，仅影响带点列的裁决精度）。
    """
    if not (datasource_id or model_id):
        return {}
    sql = ("SELECT object_key, physical_table FROM adh_ontology_bindings "
           "WHERE status = 'active' AND physical_table <> ''")
    params: list = []
    if model_id:
        sql += " AND model_id = %s"
        params.append(model_id)
    if datasource_id:
        sql += " AND (datasource_id = %s OR datasource_id = 0)"
        params.append(datasource_id)
    try:
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall() or []
    except Exception as e:  # noqa: BLE001 — 绑定表不可用时不阻断，回到未验证口径
        logger.warning("[ontology-import] load object physical tables failed (%s)", e)
        return {}
    agg: dict[str, set] = {}
    for r in rows:
        key = str(r.get("object_key") or "").strip()
        tab = str(r.get("physical_table") or "").strip()
        if key and tab:
            agg.setdefault(key, set()).add(tab)
    return {k: next(iter(v)) for k, v in agg.items() if len(v) == 1}


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


def _dataset_ref_of_obj(o: dict) -> str:
    """从 yaml 对象声明中提取查询数据集引用。

    写法: execution_dataset: 用户路径漏斗 或 execution_dataset: {dataset_ref: 用户路径漏斗}。
    值为 `adh_datasets.name`（全局唯一），不用 id 以保证源/数据集重建后仍可关联。
    """
    v = o.get("execution_dataset")
    ref = (v.get("dataset_ref") or v.get("ref")) if isinstance(v, dict) else v
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return ""


def _product_ref_for(datasource_name: str, primary_table: str) -> str:
    """查该表对应的数据产品名（稳定身份）；未登记/查询失败返回空。

    product_ref 随绑定持久化（见 data_product_binding_migration），供 P3 的
    契约变更影响分析从产品反查本体对象。查询口径走
    `data_product_service.find_by_table`（唯一口径，不另造判断）。
    """
    if not primary_table:
        return ""
    try:
        from backend.modules.catalog.services import data_product_service as dps
        row = dps.find_by_table(datasource_name or "", primary_table)
        return str((row or {}).get("product_name") or "")
    except Exception:  # noqa: BLE001 — 查不到不阻断绑定构建
        return ""


def _build_execution_binding(primary_table: str, datasource_id: int,
                             table_info: dict[str, dict],
                             datasource_name: str = "",
                             dataset_ref: str = "") -> dict:
    """面向单个对象产出 execution_binding（canonical 内嵌形式，与
    adh_ontology_bindings 行保持字段同名，为后续解析器提供三级 fallback 中的第一级。
    """
    if not primary_table:
        if dataset_ref:
            # 无物理表但声明了查询数据集: 编译期按 dataset_ref 预制 SQL 渲染（planner._plan_dataset）
            return {
                "datasource_id": datasource_id,
                "datasource_name": datasource_name,
                "physical_table": "",
                "catalog_ref": "",
                "bind_kind": "dataset",
                "dataset_ref": dataset_ref,
                "query_mode": "raw_source",
                "size_class": None,
                "allow_full_scan": 1,
                "permission_tokens": [],
                "masked_columns": [],
                # 本分支的前提是 primary_table 为空：既无物理表也无产品/产品类
                # （产品类=逻辑表名，只在下方有物理表分支登记）。历史形态是同一个
                # dict 字面量里写了两遍 product_class（后者覆盖前者）+ product_ref
                # 走了一次恒空的查表，读起来像"数据集绑定也带产品身份"，实际不带。
                "product_ref": "",
                "product_class": "",
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
            "product_ref": "",
            "product_class": "",
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
        "dataset_ref": dataset_ref or "",
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
        "product_ref": _product_ref_for(datasource_name, primary_table),
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
    by_identity: dict[str, str] = {}   # 对象身份键 → key（按 object_identity_key 判重）
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
            key = ontology_service.normalize_object_key(o.get("api_name") or name) or name.lower()
            if not key:
                continue
            name_to_key[name] = key

    # ── Pass 2: 构建 canonical 对象 ──
    for doc in docs:
        for o in doc.get("object_types") or []:
            name = o.get("name") or ""
            key = ontology_service.normalize_object_key(o.get("api_name") or name) or name.lower()
            if not key:
                continue
            # 按身份键判重(忽略大小写与分隔符)：同身份重复是录入噪音，主表一致才可合并；
            # 主表不同是真冲突，宁可中止导入也不静默二选一（no-silent-degradation）。
            ik = ontology_service.object_identity_key(key)
            target_key = by_identity.get(ik)
            new_table = _source_table(o)
            if target_key is not None:
                prev = objects.get(target_key) or {}
                if str(prev.get("primary_table") or "") != str(new_table or ""):
                    raise ValueError(
                        "导入的本体出现同名不同主表的对象冲突, 已中止(需人工裁决): "
                        f"key={target_key}/{key} "
                        f"primary_table={prev.get('primary_table')}/{new_table}")
            else:
                target_key = key
                by_identity[ik] = key
            primary_table = new_table
            ds_ref = _dataset_ref_of_obj(o)

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

            entry = {
                "key": target_key,
                "display_name": name or target_key,
                "aliases": _derive_aliases(name, target_key, primary_table, o.get("aliases")),
                "description": (o.get("description") or "").strip(),
                "primary_table": primary_table,
                "source_ref": (o.get("source") or {}).get("table") if isinstance(o.get("source"), dict) else o.get("source") or "",
                "execution_binding": _build_execution_binding(
                    primary_table, datasource_id, table_info, datasource_name=ds_name,
                    dataset_ref=ds_ref),
                "properties": properties,
                "links": [],
                "metrics": metrics,
            }
            if target_key in objects:
                objects[target_key], _detail = ontology_service.merge_object_pair(
                    objects[target_key], entry)
                logger.warning("[YamlImport] 对象 %s 重复录入, 已合并(丢弃 %s): %s",
                               target_key, key, _detail)
            else:
                objects[target_key] = entry
            # 域内 link_types
            for lt in o.get("link_types") or []:
                tgt_raw = lt.get("target") or ""
                objects[target_key]["links"].append(_build_link(
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
    datasource_id = doc.get("datasource_id") or 0   # 业务/系统本体无 datasource_id（归属=domain+kind）
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

    # 口径归并（单一登记）：解析产物里旧格式 dict 指标条目（name/formula/description）
    # 归并进指标字典并降为名字引用，必须在落库/激活写闸之前完成
    doc = ontology_service.absorb_object_metric_specs(doc, datasource_id)

    model_id = _upsert_active_model(doc, created_by)
    ontology_service.activate(model_id)  # 复用：归档旧 active、展开对象写 adh_ontology_objects

    binding_stats = _persist_bindings(doc, model_id)

    link_count = sum(len(o["links"]) for o in doc["objects"])

    graph_summary = {"skipped": True}
    if rebuild_graph:
        graph_summary = _rebuild_graph(datasource_id, "business")

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
    col_cache: dict = {}   # 表实存列名缓存(带点列身份裁决), 逐对象复用避免重复查元数据
    table_info = _load_table_info_map(datasource_id) if datasource_id else {}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            # 1. 本模型旧行失效（保留历史，避免下游误读）。范围仅限**模型自有数据源**：
            #    同构多站点的跨站部署行（deploy_contract_bindings.py 按契约复制到各站，
            #    一份契约 × N 部署）不在 doc 级联管辖内，否则每次 save_draft 会把
            #    N 站部署绑定误废弃（历史缺陷：级联后四站取数全挂，仅剩主源可用）。
            cur.execute(
                "UPDATE adh_ontology_bindings SET status = 'deprecated', updated_at = %s "
                "WHERE model_id = %s AND status = 'active' AND datasource_id = %s",
                (now, model_id, datasource_id),
            )
            # 2. 逐对象写入 active 绑定行
            for obj in doc.get("objects") or []:
                binding = obj.get("execution_binding") or {}
                if not binding:
                    # v0.5 B6b 后 doc 不再携带 execution_binding：按 doc 声明的形状
                    # （primary_table / execution_dataset）派生，否则这支路对新建/
                    # 页面编辑的对象恒跳过，绑定行永远补不上。
                    binding = _derive_binding_for_object(
                        obj, datasource_id,
                        doc.get("datasource_name") or _datasource_name_by_id(datasource_id),
                        table_info, col_cache)
                if not binding:
                    continue
                state = binding.get("sync_state") or "unbound"
                if state == "drifted":
                    drifted += 1
                elif state == "unbound":
                    unbound += 1
                cur.execute(
                    "INSERT INTO adh_ontology_bindings "
                    "(id, model_id, object_key, datasource_id, datasource_name, product_ref, catalog_ref, bind_kind, "
                    " dataset_ref, physical_table, join_expr, column_map, query_mode, size_class, "
                    " allow_full_scan, max_rows, timeout_sec, permission_tokens, "
                    " rls_policy_ref, masked_columns, sync_state, last_verified_at, "
                    " status, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,%s,'active',%s,%s)",
                    (
                        ontology_service._gen_id() + written,
                        model_id,
                        obj.get("key", ""),
                        datasource_id,
                        # 存 name 作为稳定关联键：数据源删除重建后仍能按名重关联
                        binding.get("datasource_name") or doc.get("datasource_name") or "",
                        # 数据产品名（稳定身份）：P3 影响分析从产品反查本体对象
                        binding.get("product_ref") or _product_ref_for(
                            binding.get("datasource_name") or doc.get("datasource_name") or "",
                            binding.get("physical_table", "")),
                        binding.get("catalog_ref", ""),
                        binding.get("bind_kind", "primary"),
                        binding.get("dataset_ref") or None,
                        binding.get("physical_table", ""),
                        json.dumps(_column_map_from_props(
                            obj, datasource_id, binding.get("physical_table") or "",
                            col_cache),
                            ensure_ascii=False),
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


def _column_map_from_props(obj: dict, datasource_id: int = 0,
                          table_hint: str = "",
                          col_cache: Optional[dict] = None) -> dict:
    """从 properties[].column 提取 {属性名: 物理列名}（漂移清单不需要的调用方用这支）。

    带点 column 有两种写法，不能一律剥前缀（历史缺陷：SLS/ARMS 嵌套字段 `view.url`
    被剥成 `url`，部署绑定 column_map 指向不存在的列，直查物理源时无法解析）：
    - `t_order.amount` = 表限定写法 → 只留列名 `amount`；
    - `view.url`         = 列名本体含点（嵌套 schema）→ 原样保留。
    以列元数据实存为唯一裁决（裁决口径收口在 `physical_ref.resolve_column_ref`）；
    元数据拿不到时按「前缀等于主表名才剥」的保守口径，否则保留完整名
    （宁留可诊断的限定名，不可丢路径段）。
    """
    table = str(table_hint or obj.get("primary_table") or "").strip()
    known = (_physical_ref.columns_of_table(datasource_id, table, col_cache)
             if table else set())
    cmap: dict[str, str] = {}
    for p in obj.get("properties") or []:
        col = str(p.get("column") or "")
        pname = str(p.get("name") or "")
        if not col or not pname:
            continue
        _t, resolved, verdict = _physical_ref.resolve_column_ref(col, table, known)
        if "." in col and resolved != col and verdict == "ok":
            col = resolved          # 表限定写法（前缀即主表且列名实存）
        elif verdict in ("bad", "unverified") and ("." in col or not known):
            logger.warning(
                "[ontology-import] 对象 %s 属性 %r 的列名 %r 未能对着该表(%s)实存列核验"
                "(%s),保留原写法(建模回炉项)",
                obj.get("key", ""), pname, col, table or "?", verdict)
        cmap[pname] = col
    return cmap


def _column_map_with_drift(obj: dict, datasource_id: int = 0, table_hint: str = "",
                           col_cache: Optional[dict] = None) -> tuple[dict, list]:
    """同 `_column_map_from_props`（裁决口径唯一实现见 physical_ref），额外回收漂移清单。

    漂移只进服务端日志是历史缺陷：换库/改表后页面上属性行照旧显示一个不存在的列名，
    没有任何一处看得见"错"（Doris 换 database 后 client_version/firmware_version
    实为 c_version/f_version 即此形态）。回收成结构化清单，供级联告警与建模页渲染。
    条目形态：{property, column, table, verdict}，verdict ∈ bad（列不存在）/ unverified
    （该表列元数据缺失，无法核验——不是错，但同样不能当已绑定用）。
    """
    table = str(table_hint or obj.get("primary_table") or "").strip()
    known = (_physical_ref.columns_of_table(datasource_id, table, col_cache)
             if table else set())
    cmap: dict[str, str] = {}
    drift: list = []
    for p in obj.get("properties") or []:
        col = str(p.get("column") or "")
        pname = str(p.get("name") or "")
        if not col or not pname:
            continue
        _t, resolved, verdict = _physical_ref.resolve_column_ref(col, table, known)
        if "." in col and resolved != col and verdict == "ok":
            col = resolved
        elif verdict in ("bad", "unverified") and ("." in col or not known):
            drift.append({"property": pname, "column": col, "table": table,
                          "verdict": verdict})
            logger.warning(
                "[ontology-import] 对象 %s 属性 %r 的列名 %r 未能对着该表(%s)实存列核验"
                "(%s),保留原写法(建模回炉项)",
                obj.get("key") or obj.get("object_key") or "", pname, col,
                table or "?", verdict)
        elif verdict in ("bad", "unverified"):
            # 不带点的幽灵列（列名本身在该表实存列之外）：同样必须回收，
            # 否则"表存在但列改名"这一类最常见的换库漂移仍然看不见。
            drift.append({"property": pname, "column": col, "table": table,
                          "verdict": verdict})
            logger.warning(
                "[ontology-import] 对象 %s 属性 %r 的列名 %r 不在该表(%s)实存列里(%s)"
                "(建模回炉项)",
                obj.get("key") or obj.get("object_key") or "", pname, col,
                table or "?", verdict)
        cmap[pname] = col
    return cmap, drift


def property_column_drift(obj: dict, datasource_id: int = 0,
                          table_hint: str = "", col_cache: Optional[dict] = None) -> list:
    """单个对象的属性列漂移清单（只读，供建模页/接口实时呈现，不落库）。

    实时算而不是存快照：列元数据会随同步变，快照会把"已修好"的对象继续标成漂移。
    """
    _cmap, drift = _column_map_with_drift(obj, datasource_id, table_hint, col_cache)
    return drift


def _derive_binding_for_object(obj: dict, datasource_id: int, ds_name: str,
                               table_info: dict, col_cache: Optional[dict] = None) -> dict:
    """按 doc 声明的形状（primary_table / execution_dataset）派生该对象的部署绑定。

    v0.5 B6b 把物理绑定从 doc 移到部署绑定层后，doc 不再有 execution_binding；
    但"对象挂在一张已同步的表上"这件事仍然是 doc 声明的业务形状（建模页「编辑对象 →
    绑定物理表」写的就是它）。派生口径与导入路径完全同源（`_build_execution_binding`），
    不另造一份字段清单。无表也无数据集 → 返回 {}（纯概念对象，不是绑定缺陷）。
    """
    table = str(obj.get("primary_table") or "").strip()
    dataset_ref = _dataset_ref_of_obj(obj)
    if not table and not dataset_ref:
        return {}
    return _build_execution_binding(table, datasource_id, table_info, ds_name, dataset_ref)


def _norm_obj_key(key) -> str:
    """对象 key 的比对归一（不区分大小写与分隔符，与建模判重口径一致）。"""
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _json_obj(raw) -> dict:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


# 换表时必须随新表重取的表级派生列（它们描述的是"这张表长什么样"，不是人工配置）
_TABLE_SHAPE_FIELDS = ("query_mode", "size_class", "allow_full_scan")


def reconcile_deploy_bindings(doc: dict, model_id: int, datasource_id: int) -> dict:
    """把 doc 声明的对象形状**对账**进模型自有源的部署绑定行（幂等，可反复执行）。

    为什么级联必须管这一层（页面能力断链的根因）：B6b 之后登记绑定行只剩
    `import-yaml` 与离线脚本两个入口，而建模页一边写着"发布后在部署绑定层登记物理表"，
    一边没有任何路径真的去登记 —— 换库/新建对象后对象常年显示「未绑定物理表 · 无法取数」，
    用户只能改库或跑脚本。本函数是页面级联（save_draft/publish/activate）的唯一登记口。

    口径（宁缺勿错 + 级联不越界）：
    - 作用域 = `model_id AND datasource_id=<模型自有源>`：跨站部署行、其它源的行一律不碰
      （历史事故：级联按整个 model_id 废弃把四站部署打废）；
    - doc 声明了形状的对象：缺行则登记；表/形态/数据集变了则原地 UPDATE；
      完全没变则**原行不动**（不刷 id，不制造审计噪音）；
    - doc 未声明形状的对象（纯概念对象/route 型）：有行就留着不动，没行也不报错，
      只在结果里计 skip；
    - doc 已删除的对象：其自有源行置 deprecated（真实失效，不留悬空 active 绑定）；
    - 运维护栏列（max_rows/timeout_sec/permission_tokens/rls_policy_ref/masked_columns/
      join_expr）永不覆盖：发布改的是业务形状，不是替人重配权限与限流；
      只有换表时表级派生列（query_mode/size_class/allow_full_scan）才随新表重取；
    - 属性列对不上实存列（drift verdict=bad）时 sync_state 记 `drifted`，不报"已绑定"；
      且当**只有列映射差异**而派生映射本身含幽灵列时，不拿更差的映射覆盖现有行
      —— 由建模人先改 doc（改列名）再发布，漂移在页面上可见。
    """
    datasource_id = int(datasource_id or 0)
    ds_name = str(doc.get("datasource_name") or "") or _datasource_name_by_id(datasource_id)
    table_info = _load_table_info_map(datasource_id) if datasource_id else {}
    col_cache: dict = {}
    now = ontology_service._now()
    skipped_no_shape = 0     # doc 未声明物理形状的对象数（先于使用处初始化）

    desired: dict[str, dict] = {}
    doc_keys: set[str] = set()
    drift_all: list = []
    for obj in doc.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        key = str(obj.get("key") or obj.get("object_key") or "").strip()
        if not key:
            continue
        nk = _norm_obj_key(key)
        doc_keys.add(nk)
        binding = obj.get("execution_binding") or _derive_binding_for_object(
            obj, datasource_id, ds_name, table_info, col_cache)
        if not binding:
            skipped_no_shape += 1      # 纯概念对象/route 型：不报错，但要在结果里数得出来
            continue
        table = str(binding.get("physical_table") or "").strip()
        cmap, drift = _column_map_with_drift(obj, datasource_id, table, col_cache)
        for d in drift:
            d["object_key"] = key
        drift_all.extend(drift)
        state = str(binding.get("sync_state") or "unbound")
        if table and any(d["verdict"] == "bad" for d in drift) and state == "bound":
            state = "drifted"          # 形状对不上不叫已绑定（不静默报成功）
        desired[nk] = {
            "object_key": key,
            "physical_table": table,
            "bind_kind": str(binding.get("bind_kind") or "primary"),
            "dataset_ref": binding.get("dataset_ref") or "",
            "catalog_ref": str(binding.get("catalog_ref") or ""),
            "product_ref": str(binding.get("product_ref") or "")
                           or _product_ref_for(ds_name, table),
            "column_map": cmap,
            "query_mode": str(binding.get("query_mode") or "raw_source"),
            "size_class": binding.get("size_class"),
            "allow_full_scan": int(binding.get("allow_full_scan") or 0),
            "sync_state": state,
            "has_bad_drift": any(d["verdict"] == "bad" for d in drift),
        }

    registered: list[str] = []
    refreshed: list[str] = []
    deprecated: list[str] = []
    unchanged = 0
    kept_drifted: list[str] = []

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, object_key, physical_table, bind_kind, dataset_ref, column_map, "
                "sync_state, product_ref, catalog_ref FROM adh_ontology_bindings "
                "WHERE model_id = %s AND status = 'active' AND datasource_id = %s "
                "ORDER BY id", (model_id, datasource_id))
            existing: dict[str, dict] = {}
            for r in cur.fetchall() or []:
                nk = _norm_obj_key(r.get("object_key"))
                if nk:
                    existing.setdefault(nk, r)   # 同 key 历史重复行只认第一行，不新增副本

            for nk, d in desired.items():
                row = existing.get(nk)
                if row is None:
                    cur.execute(
                        "INSERT INTO adh_ontology_bindings "
                        "(id, model_id, object_key, datasource_id, datasource_name, product_ref, catalog_ref, "
                        "bind_kind, dataset_ref, physical_table, join_expr, column_map, query_mode, size_class, "
                        "allow_full_scan, max_rows, timeout_sec, permission_tokens, rls_policy_ref, masked_columns, "
                        "sync_state, last_verified_at, status, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,NULL,NULL,%s,NULL,%s,%s,%s,'active',%s,%s)",
                        (ontology_service._gen_id() + len(registered), model_id, d["object_key"],
                         datasource_id, ds_name, d["product_ref"], d["catalog_ref"], d["bind_kind"],
                         d["dataset_ref"] or None, d["physical_table"],
                         json.dumps(d["column_map"], ensure_ascii=False),
                         d["query_mode"], d["size_class"], d["allow_full_scan"],
                         json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False),
                         d["sync_state"], now if d["sync_state"] == "bound" else None, now, now))
                    registered.append(d["object_key"])
                    continue
                same_table = str(row.get("physical_table") or "") == d["physical_table"]
                same_shape = (str(row.get("bind_kind") or "primary") == d["bind_kind"]
                              and str(row.get("dataset_ref") or "") == str(d["dataset_ref"] or ""))
                same_map = _json_obj(row.get("column_map")) == d["column_map"]
                same_state = str(row.get("sync_state") or "") == d["sync_state"]
                if same_table and same_shape and same_map and same_state:
                    unchanged += 1
                    continue
                if same_table and same_shape and not same_map and d["has_bad_drift"]:
                    # 只有列映射差异，而新映射本身带幽灵列 → 覆盖它只会更错（宁缺勿错）
                    kept_drifted.append(d["object_key"])
                    logger.warning(
                        "[ontology-binding] 模型 %s 对象 %s 的属性列与实际表结构不一致，"
                        "保留现有列映射不覆盖（请改 doc 里的列名后重新发布）",
                        model_id, d["object_key"])
                    continue
                sets = ["physical_table = %s", "bind_kind = %s", "dataset_ref = %s",
                        "column_map = %s", "sync_state = %s",
                        "last_verified_at = %s", "updated_at = %s"]
                params: list = [d["physical_table"], d["bind_kind"], d["dataset_ref"] or None,
                                json.dumps(d["column_map"], ensure_ascii=False), d["sync_state"],
                                now if d["sync_state"] == "bound" else None, now]
                # 数据产品/目录身份：派生得到就刷新，派生为空则保留原值——绑定行是
                # "已认领的稳定身份"（binding_resolver._enrich_from_data_product 同口径），
                # 拿一次产品表查不到就把人认领的 product_ref 抹平是倒退。
                if d["product_ref"]:
                    sets.append("product_ref = %s")
                    params.append(d["product_ref"])
                if d["catalog_ref"]:
                    sets.append("catalog_ref = %s")
                    params.append(d["catalog_ref"])
                if not same_table:   # 换表 = 表级派生护栏随新表重取；人工列一律不动
                    sets += [f"{c} = %s" for c in _TABLE_SHAPE_FIELDS]
                    params += [d["query_mode"], d["size_class"], d["allow_full_scan"]]
                params.append(row["id"])
                cur.execute(
                    f"UPDATE adh_ontology_bindings SET {', '.join(sets)} WHERE id = %s", params)
                refreshed.append(d["object_key"])

            for nk, row in existing.items():
                if nk in doc_keys:
                    continue
                cur.execute(
                    "UPDATE adh_ontology_bindings SET status = 'deprecated', updated_at = %s "
                    "WHERE id = %s", (now, row["id"]))
                deprecated.append(str(row.get("object_key") or ""))
        conn.commit()

    out = {"model_id": model_id, "datasource_id": datasource_id,
           "datasource_name": ds_name, "registered": registered, "refreshed": refreshed,
           "deprecated": deprecated, "unchanged": unchanged,
           "skipped_no_shape": skipped_no_shape,
           "kept_drifted": kept_drifted, "drift": drift_all}
    logger.info(
        "[ontology-binding] 模型 %s(源 %s) 部署绑定对账：登记 %d / 刷新 %d / 废弃 %d / 未变 %d / 未声明形状 %d",
        model_id, ds_name or datasource_id, len(registered), len(refreshed),
        len(deprecated), unchanged, skipped_no_shape)
    return out


def _rebuild_graph(datasource_id: int, kind: str = "") -> dict:
    """best-effort 触发图谱重建（本体经 graph_builder._merge_ontology_models 入图）。

    按 **kind** 路由目标图（不用 `datasource_id=0` 判系统域——业务本体跨源 ds_id 也可能非 0）：
    - kind=system  → 系统图 ds:-1（AS-BOT 专用，隔离业务本体）；
    - kind=business → 业务图 ds:0（跨源连通聚合图，多本体 append）。
    源本体已退役（WS2）：具体数据源的命名图只留物理血缘，不再单独归并本体。
    """
    try:
        from backend.semantics.graph_rag.oxigraph_store import SYSTEM_DATASOURCE_ID
        from backend.modules.semhub.graph.graph_service import GraphService
        if kind == "system":
            ds = SYSTEM_DATASOURCE_ID
        elif kind == "business":
            ds = 0          # 业务本体独立图（跨源）
        else:
            ds = SYSTEM_DATASOURCE_ID if not datasource_id else datasource_id
        resp = GraphService().sync_from_metadata(ds, kind)
        return {
            "success": bool(getattr(resp, "success", False)),
            "tables": getattr(resp, "tables", 0),
            "relations": getattr(resp, "relations", 0),
            "message": getattr(resp, "message", ""),
        }
    except Exception as e:
        logger.warning("[ontology-import] graph rebuild failed (model saved, "
                       "trigger POST /api/graph/sync?datasource_id=%s&system_scope=true manually): %s",
                       datasource_id, e)
        return {"success": False, "error": str(e)}
