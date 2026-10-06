"""AS-BOT 元数据工具的业务投影：严格按源分域，不返回物理结构。"""
import json

from services.shared.common.db import execute_query

NAMES = {"search_metadata", "get_table_schema", "list_datasources", "search_ontology", "get_ontology_model",
         "list_ontology_models", "get_metadata_summary", "get_glossary", "get_metrics"}


def system_scope(ctx) -> bool:
    """系统能力判定（工具授权口径，**能力叠加**而非域互斥）：
    AS-BOT 的生效策略授权了 system 工具组即拥有系统能力面（系统本体/系统工具/系统源），
    业务能力照常叠加（可见域=系统 ∪ 业务，域规则更新：系统域只是权限的一部分）。
    无执行运行时（如设计服务显式传 resource_scope 的调用）一律无系统能力。
    """
    extra = getattr(ctx, "extra", None) or {}
    runtime = extra.get("secure_runtime")
    policy = getattr(runtime, "policy", None)
    if policy is None:
        return False
    from services.datamind.execution.tool_policy import is_system_scope
    return is_system_scope(policy)


def execute(name, args, ctx, resource_scope=None):
    # resource_scope 只由设计服务校验后传入，不从工具 args 接受。
    datasource_id = resource_scope["datasource_id"] if resource_scope else ctx.datasource_id
    system = resource_scope is None and system_scope(ctx)  # 能力叠加：附加系统本体可见
    if not system and not datasource_id:
        raise PermissionError("请明确选择当前 AS-BOT 已绑定的数据源")
    # 判别口径用 kind（x3 归属键改造），不用 datasource_id=0：
    # 本体归属已按业务域而非数据源，业务本体的 datasource_id 也是 0，
    # 旧口径会把业务本体当成系统本体暴露给系统域 AS-BOT（as-bot-system-waker §1 域边界泄漏）。
    # 可见域 = 系统本体(system 能力时叠加) ∪ 业务域（跨源业务本体 + 本绑定源的源本体）。
    business = "kind = 'business' OR (kind = 'source' AND datasource_id = %s)"
    if system:
        condition = f"(kind = 'system' OR {business})"
        params = (int(datasource_id or 0),)
    else:
        condition = f"({business})"
        params = (datasource_id,)

    def _scope_note(out):
        """未选源时源本体(kind='source')目录不可见——显式标注不完整，
        防止把'上下文不完整'误报成'对象不存在'（no-silent-degradation）。"""
        if system and not datasource_id:
            out = dict(out or {})
            note = "会话未选定数据源，kind='source' 的源本体目录未包含在内，请先选定数据源再查源本体对象；目录为空≠对象不存在"
            out["incomplete_scope"] = True
            out["note"] = f"{out['note']}；{note}" if out.get("note") else note
        return out
    if name == "get_glossary":
        from services.datamind.rag import terminology_manager
        rows = terminology_manager.get_all_terms(datasource_id)
        keyword = str(args.get("keywords") or args.get("keyword") or "").strip()
        terms = [{"name": r.get("term_cn"), "aliases": r.get("term_aliases"), "description": r.get("description")}
                 for r in rows if int(r.get("datasource_id") or 0) in (0, datasource_id)
                 and (not keyword or keyword in str(r.get("term_cn", "")))]
        return {"items": terms[:50]}
    if name == "list_datasources":
        out = []
        if system:
            out.append({"name": "系统元数据"})  # 系统能力附加面
        # 返回授权集(用户角色授权)内全部候选源的业务名+方言（不含 id，守 §7），供 LLM 按名选源。
        ids = [int(i) for i in (ctx.extra.get("available_datasource_ids") or [])]
        if ids:
            marks = ",".join(["%s"] * len(ids))
            rows = execute_query(f"SELECT name, db_type FROM adh_datasources WHERE id IN ({marks}) ORDER BY name", tuple(ids))
            out.extend({"name": r.get("name"), "db_type": r.get("db_type")} for r in rows)
        return {"datasources": out}
    if name == "get_metadata_summary":
        return _scope_note(execute_query(f"SELECT COUNT(*) AS ontology_models FROM adh_ontology_models WHERE {condition}",
                                         params, fetchone=True))
    status_filter = "" if name == "get_ontology_model" else " AND status='active'"
    rows = execute_query(f"SELECT id,name,status,json_content FROM adh_ontology_models WHERE {condition}"
                         f"{status_filter} ORDER BY id", params)
    requested = int(args.get("model_id") or 0)
    if requested:
        rows = [r for r in rows if r["id"] == requested]
        if not rows:
            raise PermissionError("模型不存在或不在当前 AS-BOT 资源域")
    if name == "get_metrics":
        # 字典(adh_metrics/adh_dimensions)可能存为全局 datasource_id=0; 与语义层编译/取数
        # (planner/mdl_compiler 的 `datasource_id=%s OR datasource_id=0`)保持同一作用域口径,
        # 否则发现层(get_metrics)会误报空目录而取数层其实能解析。业务归属仍由 allowed_keys
        # (本数据源生效本体模型的对象键) 收口, 不放宽到跨模型对象。
        # 系统对象的字典行同在 ds=0 全局口径内，叠加可见后统一条件即可覆盖。
        dict_condition = "(datasource_id = %s OR datasource_id = 0)"
        dict_params = (int(datasource_id or 0),)
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
        return _scope_note({"objects": list(objects.values()), "total": len(objects)})
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
        return _scope_note({"models": models})
    return _scope_note({"objects": objects[:50], "models": models if requested else [],
            "view": "business_ontology", "notice": "仅提供已授权的业务本体属性，不提供物理表结构"})
