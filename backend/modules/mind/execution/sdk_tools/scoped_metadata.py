"""AS-BOT 元数据工具的业务投影：严格按源分域，不返回物理结构。"""
import json

from backend.common.db import execute_query
from backend.modules.mind.execution.sdk_tools.semantic_tools import (
    KEYWORD_REQUIRED_HINT, _prop_view, _public_aliases, _sanitize_dict_desc, metric_keywords,
)

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
    from backend.modules.mind.execution.tool_policy import is_system_scope
    return is_system_scope(policy)


def execute(name, args, ctx, resource_scope=None):
    # resource_scope 只由设计服务校验后传入，不从工具 args 接受。
    datasource_id = resource_scope["datasource_id"] if resource_scope else ctx.datasource_id
    system = resource_scope is None and system_scope(ctx)  # 能力叠加：附加系统本体可见
    # 聚合态（全球）会话：datasource_id 合法为 0（=逐站编排），不得硬拒（同 resource_guard 闸门
    # 修复）。此时源本体按「角色授权集内的具体站点」纳入，让发现层(get_metrics 等)能看到各站
    # 源本体对象（如各站 RUM 事件对象），支撑逐站编排；仍只回业务投影、不回物理结构。
    from backend.core.site_service import is_aggregate_site
    aggregate = resource_scope is None and is_aggregate_site(
        (getattr(ctx, "extra", None) or {}).get("site_code") or "")
    if not system and not datasource_id and not aggregate:
        raise PermissionError("请明确选择当前 AS-BOT 已绑定的数据源")
    # 判别口径用 kind：源本体已退役（合并为 business），只剩 system / business。
    # 可见域（严格按源分域，waker-datasource-domain §1）：
    #   - 生效源集 src_ids = 会话绑定源（单源）或聚合态下授权集内全部具体站点；
    #   - system 本体：仅系统能力(system)时叠加；
    #   - business 本体：模型触达数据源(主源∪deploy_sites)∩src_ids 非空才纳入 ——
    #     不因合并成 business 而把别源业务本体泄漏进本会话投影（源本体退役后仍按源收口）。
    if aggregate and not datasource_id:
        src_ids = sorted({int(i) for i in (ctx.extra.get("available_datasource_ids") or [])
                          if int(i or 0) > 0})
    else:
        src_ids = [int(datasource_id or 0)]
    src_set = set(src_ids)
    from backend.core.site_service import datasource_name_id_map
    from backend.modules.catalog.services.ontology_service import _model_scope_ids_from_row
    name_id_map = datasource_name_id_map()

    def _model_visible(row) -> bool:
        kind = str(row.get("kind") or "")
        if kind == "system":
            return system
        return bool(_model_scope_ids_from_row(row, name_id_map) & src_set)

    def _scope_note(out):
        """未选具体源时业务本体目录可能不完整——显式标注，防把'上下文不完整'误报成'对象不存在'。

        分支顺序必须是 **aggregate 优先于 system+ds=0**：聚合态会话的 src_ids 已经是
        授权集内全部具体站点源（上面同一口径），业务本体**已纳入**；旧写法先命中
        “system 且未选源”那条，给聚合态会话刷上“仅系统本体可见，业务本体需…切聚合态才纳入”
        —— 历史缺陷（会话 1783069459277）：同一回合 keyword=IP 已返回业务对象，而文案叫模型
        去“切聚合态”，模型据此判定业务本体不可用、跳过语义层去手写 SQL。
        """
        if aggregate:
            out = dict(out or {})
            note = ("会话站点为聚合态（全部站点）：已按授权集内各具体站点纳入业务本体，"
                    "取数请对各站分别带 site/datasource 调用 run_semantic_query 后分站呈现；"
                    "某站在本目录无对象=该站未建模（≠无数据），需先生成/部署业务本体")
            out["note"] = f"{out['note']}；{note}" if out.get("note") else note
        elif (system and not datasource_id):
            out = dict(out or {})
            note = ("会话未选定数据源：仅系统本体可见，业务本体需选定数据源或切聚合态（全部站点）才纳入；"
                    "目录为空≠对象不存在")
            out["incomplete_scope"] = True
            out["note"] = f"{out['note']}；{note}" if out.get("note") else note
        return out

    def _fetch_visible_models(require_active: bool = True):
        status_sql = " WHERE status='active'" if require_active else ""
        all_rows = execute_query(
            "SELECT id,name,status,kind,datasource_id,deploy_sites,json_content "
            f"FROM adh_ontology_models{status_sql} ORDER BY id")
        return [r for r in (all_rows or []) if _model_visible(r)]

    if name == "get_glossary":
        from backend.modules.mind.rag import terminology_manager
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
        return _scope_note({"ontology_models": len(_fetch_visible_models(require_active=False))})
    if name == "get_metrics":
        # keyword 必填（2026-11-19 拍板）：空关键词=整层字典一次下发（会话 1783069459335 实测
        # 29 对象 / 180 指标 / 230 维度 = 63,226 字符），被运行时判「输出过大」截断成文件指针，
        # 模型拿到的是半本目录。拦在取本体清单与字典之前：空关键词不发任何 SQL，文案与字典层同源。
        keywords = metric_keywords(args)
        if not keywords:
            raise PermissionError(KEYWORD_REQUIRED_HINT)
    rows = _fetch_visible_models(require_active=(name != "get_ontology_model"))
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
        def _kw_hay(entry: dict) -> set[str]:
            """关键词命中面：名称/英文名/别名 + 枚举业务名。

            不得把整条 JSON 当干草堆（description 里的口径文字会意外命中，
            模型据此以为命中的是同一个概念）。
            """
            hay = {str(entry.get("name") or ""), str(entry.get("name_en") or "")}
            hay |= {str(a) for a in (entry.get("aliases") or [])}
            labels = entry.get("enum") or entry.get("value_labels") or {}
            if isinstance(labels, dict):
                hay |= {str(v) for v in labels.values()}
            return {h.lower() for h in hay if h}

        def _kw_hit(entry: dict) -> bool:
            return any(kw in h for kw in keywords for h in _kw_hay(entry))

        entries_all: dict[str, list] = {}
        for table, fields, category in (("adh_metrics", "name,name_en,aliases,description,unit", "metrics"),
                                        ("adh_dimensions", "name,name_en,aliases,description,value_labels,category", "dimensions")):
            bucket_rows: list = []
            for entry in execute_query(
                    f"SELECT bound_object_key,{fields} FROM {table} WHERE {dict_condition} AND is_active=1",
                    dict_params):
                key = entry.pop("bound_object_key", None)
                if key not in allowed_keys:
                    continue
                for field in ("aliases", "value_labels"):
                    if isinstance(entry.get(field), str):
                        entry[field] = json.loads(entry[field])
                # 字典口径脱敏：description 可能含物理列引用（护栏 §7）
                if entry.get("description"):
                    entry["description"] = _sanitize_dict_desc(entry["description"])
                if category == "dimensions":
                    entry["enum"] = entry.pop("value_labels", None) or {}
                    entry["is_time"] = entry.pop("category", "") == "时间"
                bucket_rows.append((key, entry))
            entries_all[category] = bucket_rows
        # keyword 语义：**命中即整对象补全**（keyword 必填之后仍是唯一形态）。历史缺陷（会话 1783069459337）：
        # keyword="订单" 只回名称含“订单”的 3 个维度，模型把子集当对象全量，断言“订单只有 4 个维度、
        # 没有接单方字段”，反复判定“无法按时代天使查订单”——而该对象实有 13 个维度
        # （含「接单技工所」）与 55 个属性。子集不得冒充全量（no-silent-degradation）。
        hit_keys = {key for bucket_rows in entries_all.values() for key, entry in bucket_rows if _kw_hit(entry)}
        objects: dict[str, dict] = {}
        for category, bucket_rows in entries_all.items():
            for key, entry in bucket_rows:
                matched = _kw_hit(entry)
                if not matched and key not in hit_keys:
                    continue
                row = dict(entry)
                row["matched_by_keyword"] = matched
                objects.setdefault(key, {"object": key, "metrics": [], "dimensions": []})[category].append(row)
        out = {"objects": list(objects.values()), "total": len(objects), "keywords": keywords}
        out["keyword_note"] = (
            "keywords 只决定“哪些对象被命中”：命中对象的指标/维度是**该对象全量**"
            "（matched_by_keyword=true 为词直命中，false 为同对象其余口径，同样可查）。"
            "未出现在结果里的对象=其对象名/别名/枚举业务名不含这些词，不代表平台没有该口径；"
            "要看某对象的完整口径就用它自己的对象名作 keyword，多个概念一次带齐（不空传、也不逐词多轮）。")
        return _scope_note(out)
    models, candidates = [], []
    keyword = str(args.get("query") or args.get("keyword") or args.get("table_name") or "").strip().lower()
    object_key_want = str(args.get("object_key") or args.get("key") or "").strip()
    for row in rows:
        doc = row["json_content"]
        if isinstance(doc, str):
            doc = json.loads(doc)
        models.append({"id": row["id"], "name": row["name"], "status": row["status"]})
        for obj in (doc or {}).get("objects", []):
            display = obj.get("display_name") or obj.get("label")
            if not display:
                continue
            raw_props = obj.get("properties") or []
            view = {"object_key": obj.get("key"), "name": display,
                    "aliases": _public_aliases(obj.get("aliases"), obj.get("primary_table")),
                    "description": obj.get("description") or "",
                    "property_count": len(raw_props)}
            if object_key_want:
                hit = str(view.get("object_key") or "") == object_key_want
            else:
                # 命中面只放业务名/别名/描述/属性业务名（物理列名不得进匹配面，护栏 §7）
                hay = json.dumps({"name": display, "aliases": _public_aliases(obj.get("aliases")),
                                  "description": obj.get("description") or "",
                                  "properties": [p.get("name") or p.get("display_name")
                                                 for p in raw_props]}, ensure_ascii=False).lower()
                hit = (not keyword) or keyword in hay
            if hit:
                candidates.append((view, raw_props))
    # 属性展开口径：
    #   - search_metadata / search_ontology：发现面，命中的对象直接带属性（既有口径，不改窄）；
    #   - get_ontology_model：全模型摘要（十几对象 × 数十属性），默认只回 property_count，
    #     传 object_key 才展开该对象全量属性，避免一次回上千行把上下文撞满。
    expand = bool(object_key_want) or name in ("search_metadata", "search_ontology")
    objects = []
    for view, raw_props in candidates[:50]:
        if expand:
            props = [v for v in (_prop_view(p) for p in raw_props) if v]
            view["properties"] = props
            if raw_props and not props:
                # 登记了属性却投影不出业务名 = 建模数据缺业务名，必须显式暴露，不得伪装成“无字段”
                view["properties_note"] = "该对象已登记属性但缺失业务名，无法投影；不得据此认为对象没有字段"
        else:
            view["properties_note"] = (f"属性未展开（已登记 {len(raw_props)} 个）；"
                                       "需要字段清单时传 object_key=该对象 key 重新调用")
        objects.append(view)
    if name == "list_ontology_models":
        return _scope_note({"models": models})
    return _scope_note({"objects": objects, "models": models if requested else [],
            "view": "business_ontology", "notice": "仅提供已授权的业务本体属性，不提供物理表结构"})
