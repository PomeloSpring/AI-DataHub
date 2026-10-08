"""Dataset Service — BI 治理建模层(语义对象 × SQL 双定义, 互为转换).

双定义语义(非二选一): 执行 SQL 是查询的实际执行通道; 本体对象绑定供语义层
检索/业务口径(字段随本体自动演进)。两者可互相转换:
- compile_from_semantic: 语义意图 → 编译 SQL(固化为执行定义)
- extract_from_sql:      执行 SQL 输出列 → 语义字段映射建议(未命中回抛待映射)

取数契约(数据护城河):
- 有执行 SQL: validate_sql → 行级 scope 子查询包裹 → governed_execute(敏感基线+RLS+审计)
- 仅语义对象: intent → resolve_binding → plan → execute_semantic(七闸门链, 与 Chat 同源)
- 身份只信服务端 JWT 解析后传入; 无可信身份 fail-closed(NoIdentityError 向上传播)。
- 数据集级行范围"只收紧不放宽": 与用户查询条件 AND 合并, 叠加在 RLS/敏感屏蔽之上。
- 可见性功能已退役: 列表不做可见性过滤, 越权防护由取数治理入口承担。
"""
from __future__ import annotations

import json
import logging
import re

from services.shared.common.db import execute_query, execute_write
from services.dataviz.services.governed_query import NoIdentityError

logger = logging.getLogger(__name__)

# 字段名白名单(scope 拼接 SQL 用, 防注入): \w 含中文/字母/数字/下划线(语义层维度名常为中文),
# 不匹配反引号/引号/分号/空白等注入字符
_FIELD_RE = re.compile(r"^\w{1,64}$")
# scope/查询 op 白名单(与语义层 intent filters 对齐)
_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "like"}
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 2000
# SQL 源内层保底 LIMIT(外层 scope 过滤前不移析过多行)
_INNER_LIMIT = 5000
# 执行阶段失败对外统一文案(原始报错仅进服务端日志, 护栏 §7)
_EXEC_FAIL_HINT = ("取数在执行阶段失败(通常是数据源连接/凭据不可用)。"
                   "请在数据集基本信息中选择可用的执行数据源或稍后重试, 勿向用户展示连接细节。")


def _parse_json(value, default):
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _sql_str(v) -> str:
    """SQL 字符串字面量安全转义(单数据源 MySQL 方言; 值来自管理员配置的 scope)."""
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


# ═══════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════

def _derive_source_type(object_key: str, sql_query: str) -> str:
    """展示用来源标注(不驱动分支): 双定义并存时为 'both'."""
    has_obj, has_sql = bool(object_key), bool(sql_query)
    return "both" if has_obj and has_sql else ("semantic" if has_obj else "sql")


def list_datasets(identity: dict, keyword: str = "") -> list[dict]:
    """列出数据集(可见性功能已退役, 取数侧治理不变)."""
    sql = ("SELECT id, name, description, source_type, object_key, datasource_id, "
           "chart_type, chart_preset, owner_id, status, field_config, created_at, updated_at "
           "FROM adh_datasets WHERE status='active'")
    params: list = []
    if keyword:
        sql += " AND (name LIKE %s OR description LIKE %s OR object_key LIKE %s)"
        like = f"%{keyword}%"
        params += [like, like, like]
    sql += " ORDER BY updated_at DESC LIMIT 200"
    rows = execute_query(sql, tuple(params)) or []
    for r in rows:
        r["chart_preset"] = _parse_json(r.get("chart_preset"), {})
        r["field_count"] = len(resolve_fields(r))
        r["references"] = _reference_count(int(r["id"]))
    return rows


def get_dataset(dataset_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM adh_datasets WHERE id = %s", (dataset_id,), fetchone=True)
    return row


def get_dataset_by_name(name: str) -> dict | None:
    return execute_query(
        "SELECT * FROM adh_datasets WHERE name = %s AND status='active'",
        (name,), fetchone=True)


def create_dataset(req: dict, identity: dict) -> dict:
    name = (req.get("name") or "").strip()
    if not name:
        raise ValueError("数据集名称必填")
    object_key = (req.get("object_key") or "").strip()
    sql_query = (req.get("sql_query") or "").strip()
    datasource_id = int(req.get("datasource_id") or 0)

    # 双定义并存: 至少一种查询定义, 两者可互转
    if not object_key and not sql_query:
        raise ValueError("数据集至少需要一种查询定义: 绑定本体对象或提供执行 SQL(两者可并存互转)")
    if sql_query:
        _validate_select(sql_query)

    if execute_query("SELECT id FROM adh_datasets WHERE name=%s", (name,), fetchone=True):
        raise ValueError(f"数据集名称已存在: {name}")

    field_config = json.dumps(req.get("field_config") or [], ensure_ascii=False)
    new_id = int(execute_query("SELECT COALESCE(MAX(id),0)+1 AS n FROM adh_datasets",
                               fetchone=True)["n"])
    execute_write(
        "INSERT INTO adh_datasets (id, name, description, source_type, object_key, "
        "datasource_id, sql_query, field_config, chart_type, chart_preset, owner_id, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
        (new_id, name, req.get("description") or "",
         _derive_source_type(object_key, sql_query), object_key,
         datasource_id, sql_query, field_config,
         (req.get("chart_type") or "").strip(),
         json.dumps(req.get("chart_preset") or {}, ensure_ascii=False),
         int(identity.get("user_id") or 0)))
    return get_dataset(new_id)


def update_dataset(dataset_id: int, req: dict, identity: dict) -> dict:
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("数据集不存在")
    _require_editor(ds, identity)
    updates, params = [], []
    for col in ("description", "object_key", "sql_query"):
        if req.get(col) is not None:
            updates.append(f"{col} = %s")
            params.append(req[col])
    if req.get("chart_type") is not None:
        updates.append("chart_type = %s")
        params.append(str(req["chart_type"]).strip())
    if req.get("chart_preset") is not None:
        if not isinstance(req["chart_preset"], dict):
            raise ValueError("chart_preset 必须是对象 {xCol,yCol,groupCol,limit}")
        updates.append("chart_preset = %s")
        params.append(json.dumps(req["chart_preset"], ensure_ascii=False))
    if req.get("datasource_id") is not None:
        updates.append("datasource_id = %s")
        params.append(int(req["datasource_id"]))
    if req.get("field_config") is not None:
        updates.append("field_config = %s")
        params.append(json.dumps(req["field_config"], ensure_ascii=False))
    if req.get("status") in ("active", "inactive"):
        updates.append("status = %s")
        params.append(req["status"])
    # 改 SQL 必须重新过校验
    if req.get("sql_query"):
        _validate_select(str(req["sql_query"]).strip())
    # 更新后至少保留一种查询定义(语义对象/执行 SQL), source_type 随之重算
    object_key = str(req["object_key"] if req.get("object_key") is not None
                     else ds.get("object_key") or "").strip()
    sql_query = str(req["sql_query"] if req.get("sql_query") is not None
                    else ds.get("sql_query") or "").strip()
    if not object_key and not sql_query:
        raise ValueError("数据集至少需要一种查询定义: 绑定本体对象或提供执行 SQL(两者可并存互转)")
    updates.append("source_type = %s")
    params.append(_derive_source_type(object_key, sql_query))
    if updates:
        execute_write(f"UPDATE adh_datasets SET {', '.join(updates)} WHERE id = %s",
                      tuple(params) + (dataset_id,))
    return get_dataset(dataset_id)


def delete_dataset(dataset_id: int, identity: dict) -> dict:
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("数据集不存在")
    _require_editor(ds, identity)
    refs = _references(dataset_id)
    if refs:
        raise ValueError(
            "数据集被看板引用, 无法删除: " + ", ".join(r["dashboard_name"] for r in refs[:5]))
    execute_write("DELETE FROM adh_dataset_scopes WHERE dataset_id = %s", (dataset_id,))
    execute_write("DELETE FROM adh_datasets WHERE id = %s", (dataset_id,))
    return {"success": True}


def _require_editor(ds: dict, identity: dict):
    if identity.get("role") == "admin":
        return
    if int(ds.get("owner_id") or 0) != int(identity.get("user_id") or 0):
        raise ValueError("仅创建者或管理员可编辑/删除该数据集")


def _validate_select(sql: str):
    """SQL 源校验: 只允许 SELECT/WITH 单语句(复用护城河 validate_sql).

    缺 LIMIT 的扫描先补默认 LIMIT 再校验(不放行无界扫描, 护栏 §5)。
    """
    from services.datamind.nl2sql.sql.query_executor import validate_sql
    probe = sql.strip().rstrip(";")
    if "limit" not in probe.lower():
        probe += f" LIMIT {_INNER_LIMIT}"
    ok, msg = validate_sql(probe)
    if not ok:
        raise ValueError(f"SQL 校验失败: {msg}")


# ═══════════════════════════════════════════════════════════════════
# 语义对象 × SQL 互转(双定义)
# ═══════════════════════════════════════════════════════════════════

def _require_manage(identity: dict, path: str):
    """编译/映射类操作把关: 角色须持有 dataset:manage(fail-closed, 与 API 中间件同口径)."""
    from services.shared.common.api_permission import check_api_permission
    if not check_api_permission(identity.get("role") or "", "POST", path):
        raise PermissionError("角色未授予「dataset:manage」权限, 无法编译 SQL 或对齐语义字段")


def compile_from_semantic(req: dict, identity: dict) -> dict:
    """语义对象 → 执行 SQL: 把声明式意图编译为 SQL, 供固化为数据集执行定义.

    护栏 §7 例外: 编译 SQL 仅回显给持有 dataset:manage 的数据集编辑者(人工编辑面板),
    不进 LLM/聊天/图表响应。未解析词直接拒绝(宁缺勿错, 不静默绑定)。
    """
    _require_manage(identity, "/api/datasets/compile-from-semantic")
    object_key = (req.get("object_key") or "").strip()
    if not object_key:
        raise ValueError("object_key 必填(请先选择本体对象)")
    datasource_id = int(req.get("datasource_id") or 0)
    from services.shared.semantics.binding_resolver import resolve_binding
    from services.shared.semantics.intent import parse_intent
    from services.shared.semantics.planner import plan

    payload = {
        "object": object_key,
        "metrics": req.get("measures") or req.get("metrics") or [],
        "dimensions": req.get("dimensions") or [],
        "filters": req.get("filters") or [],
        "order": req.get("order") or [],
        "limit": min(int(req.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT),
        "datasource_id": datasource_id,
    }
    q, err, _notes = parse_intent(payload)
    if err:
        raise ValueError(f"查询意图非法: {err}")
    binding, _bw = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise ValueError(f"对象 '{q.object}' 未绑定到任何物理表, 请检查本体模型")
    p = plan(q, binding)
    unresolved = list((p.provenance or {}).get("unresolved_terms") or [])
    if unresolved:
        raise ValueError("存在未解析的业务词, 拒绝编译(请先在语义层补齐字典/别名): "
                         + ", ".join(str(t) for t in unresolved))
    if not p.sql:
        raise ValueError("语义层护栏拒绝了本次编译: "
                         + "; ".join(str(w) for w in (p.warnings or [])))
    return {"sql": p.sql, "dialect": p.dialect,
            "dimensions": list(q.dimensions or []), "measures": list(q.metrics or []),
            "warnings": list(p.warnings or [])}


def extract_from_sql(req: dict, identity: dict) -> dict:
    """执行 SQL → 语义字段映射建议: 探测输出列, 按 精确名→name_en→别名 匹配语义字段.

    未命中列显式回抛待映射(maps_to 为空 + 候选业务名), 不静默绑定
    (ontology-modeling §7 宁缺勿错)。探测取数经 governed_execute 治理入口。
    """
    _require_manage(identity, "/api/datasets/extract-from-sql")
    sql = (req.get("sql_query") or "").strip()
    if not sql:
        raise ValueError("SQL 不能为空")
    _validate_select(sql)
    uid = int(identity.get("user_id") or 0)
    if not uid:
        raise NoIdentityError("缺少可信用户身份, 拒绝探测取数")
    datasource_id = int(req.get("datasource_id") or 0)
    inner = sql.rstrip(";")
    if "limit" not in inner.lower():
        inner += f" LIMIT 1"
    probe = f"SELECT * FROM ({inner}) AS _ds_extract LIMIT 1"
    from services.dataviz.services.governed_query import governed_execute
    result = governed_execute(probe, datasource_id or None, uid,
                              int(identity.get("workspace_id") or 0),
                              identity.get("username") or "")
    columns = [str(c) for c in (result.get("columns") or [])]
    first_row = (result.get("rows") or [{}])[0]
    semantic = _semantic_fields(datasource_id, (req.get("object_key") or "").strip())
    by_name = {f["field"]: f for f in semantic}
    by_en = {str(f.get("label") or ""): f for f in semantic if f.get("label")}
    by_alias: dict = {}
    for f in semantic:
        for a in f.get("aliases") or []:
            by_alias.setdefault(str(a), f)

    fields = []
    for col in columns:
        f = by_name.get(col)
        source = "name"
        if f is None:
            f = by_en.get(col)
            source = "name_en"
        if f is None:
            f = by_alias.get(col)
            source = "alias"
        if f is not None:
            fields.append({"field": col, "maps_to": f["field"],
                           "role": f.get("role") or "dimension", "match_source": source})
        else:
            v = (first_row or {}).get(col)
            role = "measure" if isinstance(v, (int, float)) and not isinstance(v, bool) else "dimension"
            fields.append({"field": col, "maps_to": "", "role": role, "match_source": "none",
                           "candidates": [sf["field"] for sf in semantic][:10]})
    return {"fields": fields,
            "semantic_fields": [f["field"] for f in semantic],
            "unmapped": [f["field"] for f in fields if not f["maps_to"]]}


# ═══════════════════════════════════════════════════════════════════
# 字段定义
# ═══════════════════════════════════════════════════════════════════

def resolve_fields(ds: dict) -> list[dict]:
    """数据集字段: 绑定语义对象时以语义字典为主(随本体自动演进),
    field_config 保存「SQL 输出列 → 语义字段」映射(maps_to)与 SQL 独有列;纯 SQL 源读 field_config.

    每项带 from 标记('semantic' 语义字典演进 | 'config' field_config 手工),
    供前端区分可编辑范围(仅 config 项可改名/改角色)。
    """
    config = _parse_json(ds.get("field_config"), [])
    object_key = (ds.get("object_key") or "").strip()
    if not object_key:
        for e in config:
            e["from"] = "config"
        return config
    semantic = _semantic_fields(int(ds.get("datasource_id") or 0), object_key)
    for f in semantic:
        f["from"] = "semantic"
    if not config:
        return semantic
    by_name = {f["field"]: dict(f) for f in semantic}
    fields = list(by_name.values())
    for e in config:
        target = str(e.get("maps_to") or "")
        if target in by_name:
            by_name[target]["sql_column"] = str(e.get("field") or "")
        else:
            extra = dict(e)
            extra["from"] = "config"
            extra["unmapped"] = bool(target)  # 有 maps_to 但语义字段不存在 = 悬空映射
            fields.append(extra)
    return fields


def _semantic_fields(datasource_id: int, object_key: str) -> list[dict]:
    try:
        from services.shared.semantics.mdl_compiler import _get_dimensions, _get_metrics
        metrics = [m for m in _get_metrics(datasource_id)
                   if (m.get("bound_object_key") or "") == object_key]
        dims = [d for d in _get_dimensions(datasource_id)
                if (d.get("bound_object_key") or "") == object_key]
    except Exception as e:  # noqa: BLE001
        logger.warning("[dataset] semantic fields failed ds=%s obj=%s: %s",
                       datasource_id, object_key, e)
        return []
    fields = []
    for d in dims:
        fields.append({
            "field": d["name"], "label": d.get("name_en") or d["name"],
            "role": "dimension", "is_time": (d.get("category") or "") == "时间",
            "aliases": d.get("aliases") or [], "enum": d.get("value_labels") or {},
            "description": d.get("description") or "",
        })
    for m in metrics:
        fields.append({
            "field": m["name"], "label": m.get("name_en") or m["name"],
            "role": "measure", "unit": m.get("unit") or "",
            "aliases": m.get("aliases") or [],
            "calculation": m.get("formula") or "",
            "description": m.get("description") or "",
        })
    return fields


# ═══════════════════════════════════════════════════════════════════
# 行级范围(分享时生效; 只收紧不放宽)
# ═══════════════════════════════════════════════════════════════════

def list_scopes(dataset_id: int) -> list[dict]:
    rows = execute_query(
        "SELECT * FROM adh_dataset_scopes WHERE dataset_id = %s ORDER BY id",
        (dataset_id,)) or []
    for r in rows:
        r["filters"] = _parse_json(r.get("filters"), [])
    return rows


def set_scopes(dataset_id: int, scopes: list[dict], identity: dict) -> dict:
    """全量替换数据集行范围. 每条: {subject_type, subject_id, filters:[{field,op,value}]}"""
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("数据集不存在")
    _require_editor(ds, identity)
    cleaned = []
    for s in scopes or []:
        st = s.get("subject_type")
        if st not in ("user", "role"):
            raise ValueError("subject_type 仅支持 user|role")
        sid = int(s.get("subject_id") or 0)
        if not sid:
            raise ValueError("subject_id 必填")
        filters = []
        for f in (s.get("filters") or []):
            field = str(f.get("field") or "")
            op = str(f.get("op") or "eq")
            if not _FIELD_RE.match(field) or op not in _OPS:
                raise ValueError(f"非法 scope 条件: field={field} op={op}")
            if f.get("value") is None:
                continue
            filters.append({"field": field, "op": op, "value": f["value"]})
        if filters:
            cleaned.append((st, sid, json.dumps(filters, ensure_ascii=False)))
    execute_write("DELETE FROM adh_dataset_scopes WHERE dataset_id = %s", (dataset_id,))
    for st, sid, fj in cleaned:
        execute_write(
            "INSERT INTO adh_dataset_scopes (dataset_id, subject_type, subject_id, filters) "
            "VALUES (%s,%s,%s,%s)", (dataset_id, st, sid, fj))
    return {"success": True, "count": len(cleaned)}


def _scope_filters(dataset_id: int, identity: dict) -> list[dict]:
    """当前用户命中的全部行范围条件(个人 + 角色), AND 合并."""
    uid = int(identity.get("user_id") or 0)
    if not uid:
        return []
    params: list = [dataset_id, uid]
    ph = "(subject_type='user' AND subject_id=%s)"
    role_name = identity.get("role") or ""
    if role_name:
        role = execute_query("SELECT id FROM adh_roles WHERE name=%s",
                             (role_name,), fetchone=True)
        if role:
            ph += " OR (subject_type='role' AND subject_id=%s)"
            params.append(int(role["id"]))
    rows = execute_query(
        f"SELECT filters FROM adh_dataset_scopes WHERE dataset_id=%s AND ({ph})",
        tuple(params)) or []
    merged: list[dict] = []
    for r in rows:
        merged.extend(_parse_json(r.get("filters"), []))
    return merged


# ═══════════════════════════════════════════════════════════════════
# 统一取数入口(看板/预览/Chat 共用)
# ═══════════════════════════════════════════════════════════════════

def query_dataset(dataset_id: int, params: dict, identity: dict) -> dict:
    """按当前身份取数: 权限/RLS/敏感屏蔽/审计/scopes 全部服务端施加.

    params: {dimensions[], measures[], filters[], order[{by,desc}], limit}
    semantic 源走七闸门链; sql 源走 governed_execute(scope 以子查询包裹)。
    """
    ds = get_dataset(dataset_id)
    if not ds or ds.get("status") != "active":
        raise ValueError("数据集不存在或已停用")
    scopes = _scope_filters(dataset_id, identity)
    # 执行路径显式选择(非降级): 执行 SQL 是实际执行通道; 仅绑定语义对象时走七闸门语义链。
    if (ds.get("sql_query") or "").strip():
        return _query_sql(ds, params, scopes, identity)
    if (ds.get("object_key") or "").strip():
        return _query_semantic(ds, params, scopes, identity)
    raise ValueError("数据集缺少执行定义: 请配置执行 SQL 或绑定本体对象")


def _query_semantic(ds: dict, params: dict, scopes: list[dict], identity: dict) -> dict:
    from services.shared.semantics.binding_resolver import resolve_binding
    from services.shared.semantics.gates import execute_semantic
    from services.shared.semantics.intent import parse_intent
    from services.shared.semantics.planner import plan

    filters = list(params.get("filters") or [])
    filters += [{"dim": s["field"], "op": s["op"], "value": s["value"]} for s in scopes]
    payload = {
        "object": ds.get("object_key") or "",
        "metrics": params.get("measures") or params.get("metrics") or [],
        "dimensions": params.get("dimensions") or [],
        "filters": filters,
        "order": params.get("order") or [],
        "limit": min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT),
        "datasource_id": int(ds.get("datasource_id") or 0),
    }
    q, err, notes = parse_intent(payload)
    if err:
        raise ValueError(f"查询意图非法: {err}")

    binding, _bw = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise ValueError(f"对象 '{q.object}' 未绑定到任何物理表, 请检查本体模型")
    p = plan(q, binding)
    if not p.sql:
        raise ValueError("语义层护栏拒绝了本次查询")
    try:
        se = execute_semantic(q, binding, p, {
            "user_id": int(identity.get("user_id") or 0),
            "username": identity.get("username") or "",
            "workspace_id": int(identity.get("workspace_id") or 0),
        }, question=f"dataset:{ds.get('name')}")
    except Exception as e:  # noqa: BLE001  原始报错仅进日志
        logger.error("[dataset] semantic execution failed ds=%s: %s", ds.get("id"), e)
        raise ValueError(_EXEC_FAIL_HINT)
    if not se.allowed:
        # 执行阶段失败回通用文案; 策略拒绝(权限/护栏)回声明式原因
        if getattr(se, "blocked_at", "") == "execute":
            logger.error("[dataset] semantic execute-stage denied: %s", se.reason)
            raise PermissionError(_EXEC_FAIL_HINT)
        raise PermissionError(se.reason or "查询被安全闸门拦截")
    result = dict(se.result or {})
    rows = result.get("rows") or []
    if len(rows) > _MAX_LIMIT:
        result["rows"] = rows[:_MAX_LIMIT]
        result["truncated"] = True
    result["applied_rls"] = se.applied_rls
    result["masked_columns"] = se.masked_columns
    result["dataset"] = ds.get("name")
    result["execution_mode"] = "semantic"
    return result


def _query_sql(ds: dict, params: dict, scopes: list[dict], identity: dict) -> dict:
    from services.datamind.nl2sql.sql.query_executor import validate_sql
    from services.dataviz.services.governed_query import governed_execute

    inner = (ds.get("sql_query") or "").strip().rstrip(";")
    if not inner:
        raise ValueError("SQL 数据集缺少查询语句")
    # 内层保底 LIMIT
    if "limit" not in inner.lower():
        inner += f" LIMIT {_INNER_LIMIT}"

    # scope/用户过滤以子查询包裹叠加(内层表仍被 RLS/敏感基线注入)
    conds = [f for f in (params.get("filters") or []) if _FIELD_RE.match(str(f.get("field") or ""))]
    where = _build_where([{"field": c.get("field"), "op": c.get("op") or "eq",
                           "value": c.get("value")} for c in conds] + scopes)
    limit = min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)
    sql = f"SELECT * FROM ({inner}) AS _ds_base{where} LIMIT {limit}"

    ok, msg = validate_sql(sql)
    if not ok:
        raise ValueError(f"SQL 校验失败: {msg}")

    try:
        result = governed_execute(
            sql, int(ds.get("datasource_id") or 0) or None,  # 0 → 默认引擎(与 Playground 同口径)
            int(identity.get("user_id") or 0),
            int(identity.get("workspace_id") or 0),
            identity.get("username") or "")
    except NoIdentityError:
        raise
    except Exception as e:  # noqa: BLE001  原始报错(含主机/账号)仅进日志, 对外回通用文案
        logger.error("[dataset] sql execution failed ds=%s: %s", ds.get("id"), e)
        raise ValueError(_EXEC_FAIL_HINT)
    result["dataset"] = ds.get("name")
    result["execution_mode"] = "sql"
    return result


def _build_where(filters: list[dict]) -> str:
    parts = []
    for f in filters:
        field, op, value = str(f["field"]), str(f.get("op") or "eq"), f.get("value")
        if not _FIELD_RE.match(field) or op not in _OPS:
            continue
        col = f"`{field}`"
        if op == "in" and isinstance(value, (list, tuple)):
            vals = ", ".join(_sql_str(v) for v in value[:100])
            if vals:
                parts.append(f"{col} IN ({vals})")
        elif op == "like":
            parts.append(f"{col} LIKE {_sql_str('%' + str(value) + '%')}")
        else:
            sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=",
                   "lt": "<", "lte": "<="}[op]
            lit = value if isinstance(value, (int, float)) else _sql_str(value)
            parts.append(f"{col} {sym} {lit}")
    return (" WHERE " + " AND ".join(parts)) if parts else ""


# ═══════════════════════════════════════════════════════════════════
# 引用关系(看板图表)
# ═══════════════════════════════════════════════════════════════════

def _references(dataset_id: int) -> list[dict]:
    return execute_query(
        "SELECT c.id AS chart_id, c.name AS chart_name, d.name AS dashboard_name "
        "FROM adh_charts c JOIN adh_dashboards d ON d.id = c.dashboard_id "
        "WHERE c.source_type = 'dataset' AND c.source_id = %s",
        (dataset_id,)) or []


def _reference_count(dataset_id: int) -> int:
    row = execute_query(
        "SELECT COUNT(*) AS n FROM adh_charts "
        "WHERE source_type='dataset' AND source_id=%s",
        (dataset_id,), fetchone=True)
    return int(row["n"]) if row else 0
