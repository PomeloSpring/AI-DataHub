"""Waker 元数据工具的业务投影：严格按源分域，不返回物理结构。"""
import json

from services.shared.common.db import execute_query

NAMES = {"search_metadata", "get_table_schema", "list_datasources", "search_ontology", "get_ontology_model",
         "list_ontology_models", "get_metadata_summary", "get_glossary", "get_metrics"}


def execute(name, args, ctx, resource_scope=None):
    # resource_scope 只由设计服务校验后传入，不从工具 args 接受。
    datasource_id = resource_scope["datasource_id"] if resource_scope else ctx.datasource_id
    system = ctx.extra.get("waker_key") == "__system_bot__" and resource_scope is None
    if not system and not datasource_id:
        raise PermissionError("请明确选择当前 Waker 已绑定的数据源")
    condition = "(datasource_id IS NULL OR datasource_id=0)" if system else "datasource_id=%s"
    params = () if system else (datasource_id,)
    if name == "get_glossary":
        from services.datamind.rag import terminology_manager
        rows = terminology_manager.get_all_terms(datasource_id)
        keyword = str(args.get("keywords") or args.get("keyword") or "").strip()
        terms = [{"name": r.get("term_cn"), "aliases": r.get("term_aliases"), "description": r.get("description")}
                 for r in rows if int(r.get("datasource_id") or 0) in (0, datasource_id)
                 and (not keyword or keyword in str(r.get("term_cn", "")))]
        return {"items": terms[:50]}
    if name == "list_datasources":
        if system:
            return {"datasources": [{"name": "系统元数据"}]}
        # 返回授权集(工作空间∩Waker∩角色)内全部候选源的业务名+方言（不含 id，守 §7），供 LLM 按名选源。
        ids = [int(i) for i in (ctx.extra.get("available_datasource_ids") or [])]
        if not ids:
            return {"datasources": []}
        marks = ",".join(["%s"] * len(ids))
        rows = execute_query(f"SELECT name, db_type FROM adh_datasources WHERE id IN ({marks}) ORDER BY name", tuple(ids))
        return {"datasources": [{"name": r.get("name"), "db_type": r.get("db_type")} for r in rows]}
    if name == "get_metadata_summary":
        return execute_query(f"SELECT COUNT(*) AS ontology_models FROM adh_ontology_models WHERE {condition}",
                             params, fetchone=True)
    status_filter = "" if name == "get_ontology_model" else " AND status='active'"
    rows = execute_query(f"SELECT id,name,status,json_content FROM adh_ontology_models WHERE {condition}"
                         f"{status_filter} ORDER BY id", params)
    requested = int(args.get("model_id") or 0)
    if requested:
        rows = [r for r in rows if r["id"] == requested]
        if not rows:
            raise PermissionError("模型不存在或不在当前 Waker 资源域")
    if name == "get_metrics":
        # 字典(adh_metrics/adh_dimensions)可能存为全局 datasource_id=0; 与语义层编译/取数
        # (planner/mdl_compiler 的 `datasource_id=%s OR datasource_id=0`)保持同一作用域口径,
        # 否则发现层(get_metrics)会误报空目录而取数层其实能解析。业务归属仍由 allowed_keys
        # (本数据源生效本体模型的对象键) 收口, 不放宽到跨模型对象。
        dict_condition = "(datasource_id IS NULL OR datasource_id=0)" if system \
            else "(datasource_id = %s OR datasource_id = 0)"
        dict_params = () if system else (datasource_id,)
        allowed_keys = set()
        for row in rows:
            doc = json.loads(row["json_content"]) if isinstance(row["json_content"], str) else row["json_content"]
            allowed_keys.update(o.get("key") for o in (doc or {}).get("objects", []))
        objects = {}
        for table, fields, category in (("adh_metrics", "name,name_en,aliases,description,unit", "metrics"),
                                        ("adh_dimensions", "name,name_en,aliases,description,value_labels,category", "dimensions")):
            entries = execute_query(f"SELECT bound_object_key,{fields} FROM {table} WHERE {dict_condition} AND is_active=1", dict_params)
            for entry in entries:
                key = entry.pop("bound_object_key", None)
                if key not in allowed_keys:
                    continue
                keyword = str(args.get("keyword") or "").lower()
                if keyword and keyword not in json.dumps(entry, ensure_ascii=False).lower():
                    continue
                for field in ("aliases", "value_labels"):
                    if isinstance(entry.get(field), str):
                        entry[field] = json.loads(entry[field])
                if category == "dimensions":
                    entry["enum"] = entry.pop("value_labels", None) or {}
                    entry["is_time"] = entry.pop("category", "") == "时间"
                objects.setdefault(key, {"object": key, "metrics": [], "dimensions": []})[category].append(entry)
        return {"objects": list(objects.values()), "total": len(objects)}
    models, objects = [], []
    keyword = str(args.get("query") or args.get("keyword") or args.get("table_name") or "").strip().lower()
    for row in rows:
        doc = row["json_content"]
        if isinstance(doc, str):
            doc = json.loads(doc)
        models.append({"id": row["id"], "name": row["name"], "status": row["status"]})
        for obj in (doc or {}).get("objects", []):
            display = obj.get("display_name") or obj.get("label")
            if not display:
                continue
            view = {"object_key": obj.get("key"), "name": display,
                    "aliases": obj.get("aliases") or [], "description": obj.get("description") or "",
                    "properties": [{"name": p["display_name"], "description": p.get("description") or ""}
                                   for p in obj.get("properties", []) if p.get("display_name")]}
            if not keyword or keyword in json.dumps(view, ensure_ascii=False).lower():
                objects.append(view)
    if name == "list_ontology_models":
        return {"models": models}
    return {"objects": objects[:50], "models": models if requested else [],
            "view": "business_ontology", "notice": "仅提供已授权的业务本体属性，不提供物理表结构"}
