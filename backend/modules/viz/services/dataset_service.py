"""Dataset Service — BI 治理建模层（语义对象 × SQL 双定义，互为转换）。

双定义语义(非二选一): 执行 SQL 是查询的实际执行通道; 本体对象绑定供语义层
检索/业务口径(字段随本体自动演进)。两者可互相转换:
- compile_from_semantic: 语义意图 → 编译 SQL(固化为执行定义)
- extract_from_sql:      执行 SQL 输出列 → 语义字段映射建议(未命中回抛待映射)

数据集同时是平台内**唯一的"可信任预制 SQL"载体**：语义层编译不出的形状（漏斗/路径
的两级聚合、窗口函数、自关联）由数据集正文承担，参数与输出列由声明约束；
具体编译在 `semantics/dataset_compiler.py`（与语义层 planner 共用同一实现）。

取数契约(数据护城河):
- 有执行 SQL: 保存即只读校验 → 参数渲染/输出列聚合编译 → 行级 scope 子查询包裹
  → governed_execute(敏感基线+RLS+审计)
- 仅语义对象: intent → resolve_binding → plan → execute_semantic(七闸门链, 与 Chat 同源)
- 身份只信服务端 JWT 解析后传入; 无可信身份 fail-closed(NoIdentityError 向上传播)。
- 数据集级行范围"只收紧不放宽": 与用户查询条件 AND 合并, 叠加在 RLS/敏感屏蔽之上。
- 可见性由角色授权裁决(adh_role_dataset_access): admin 全可见, 其余 fail-closed。
"""
from __future__ import annotations

import json
import logging
import re

from backend.common.db import execute_query, execute_write
from backend.core.governed_query import NoIdentityError

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
    """列出数据集(按角色授权过滤可见性, admin bypass)."""
    from backend.core.role_service import role_service

    user_id = int(identity.get("user_id") or 0)
    user_role = identity.get("role") or ""

    sql = ("SELECT id, name, description, source_type, object_key, datasource_id, "
           "chart_type, chart_preset, owner_id, status, field_config, created_at, updated_at "
           "FROM adh_datasets WHERE status='active'")
    params: list = []

    # admin 全可见, 其余角色 fail-closed: 只展示已授权的数据集
    if user_role != "admin":
        allowed_ids = role_service.get_user_allowed_datasets(user_id)
        if not allowed_ids:
            return []
        ph = ",".join(["%s"] * len(allowed_ids))
        sql += f" AND id IN ({ph})"
        params.extend(allowed_ids)

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


def _validate_and_normalize(req: dict, existing: dict | None = None) -> dict:
    """保存前校验（预制 SQL 正文只读、参数声明可渲染、字段声明合法）。

    不通过直接拒（fail-loud），不存下来等上线静默报错。返回服务端归一化产物
    （touched_tables 由正文静态提取，不信请求体自报）。
    """
    from backend.modules.viz.services.dataset_registry import validate_definition

    merged = dict(existing or {})
    merged.update({k: v for k, v in (req or {}).items() if k in (
        "sql_query", "sql_bodies", "variables", "field_config", "dialect")})
    if not str(merged.get("sql_query") or "").strip() \
            and not merged.get("sql_bodies"):
        return {}                      # 纯语义对象数据集: 无预制 SQL, 不跑正文校验
    try:
        errors, stored = validate_definition(merged)
    except Exception as e:  # noqa: BLE001 — 校验器自身异常也必须拒，不得默认放行
        logger.exception("[dataset] 定义校验异常")
        raise ValueError(f"数据集校验未完成: {str(e)[:200]}") from e
    if errors:
        raise ValueError("数据集定义不符合规范，已拒绝保存: " + "; ".join(errors[:8]))
    return stored or {}


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
    stored = _validate_and_normalize(req)

    if execute_query("SELECT id FROM adh_datasets WHERE name=%s", (name,), fetchone=True):
        raise ValueError(f"数据集名称已存在: {name}")

    field_config = json.dumps(req.get("field_config") or [], ensure_ascii=False)
    new_id = int(execute_query("SELECT COALESCE(MAX(id),0)+1 AS n FROM adh_datasets",
                               fetchone=True)["n"])
    execute_write(
        "INSERT INTO adh_datasets (id, name, description, source_type, object_key, "
        "datasource_id, sql_query, sql_bodies, variables, touched_tables, field_config, "
        "chart_type, chart_preset, owner_id, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
        (new_id, name, req.get("description") or "",
         _derive_source_type(object_key, sql_query), object_key,
         datasource_id, sql_query,
         stored.get("sql_bodies") or json.dumps(req.get("sql_bodies") or {}, ensure_ascii=False),
         _as_json_text(req.get("variables"), "{}"),
         stored.get("touched_tables"),
         field_config,
         (req.get("chart_type") or "").strip(),
         json.dumps(req.get("chart_preset") or {}, ensure_ascii=False),
         int(identity.get("user_id") or 0)))
    return get_dataset(new_id)


def _as_json_text(value, fallback: str) -> str:
    """声明类字段入库存 JSON 文本（已给字符串则原样保留）。"""
    if value in (None, ""):
        return fallback
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


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
    for col in ("sql_bodies", "variables"):
        if req.get(col) is not None:
            updates.append(f"{col} = %s")
            params.append(_as_json_text(req[col], "{}" if col == "variables" else "{}"))
    if req.get("certified") is not None:
        # 认证是口径背书：只能由编辑者显式给出，服务端记录认证人
        updates.append("certified = %s")
        params.append(1 if req["certified"] else 0)
        updates.append("approved_by = %s")
        params.append(str(identity.get("username") or identity.get("user_id") or ""))
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
    # 更新后至少保留一种查询定义(语义对象/执行 SQL), source_type 随之重算
    object_key = str(req["object_key"] if req.get("object_key") is not None
                     else ds.get("object_key") or "").strip()
    sql_query = str(req["sql_query"] if req.get("sql_query") is not None
                    else ds.get("sql_query") or "").strip()
    if not object_key and not sql_query:
        raise ValueError("数据集至少需要一种查询定义: 绑定本体对象或提供执行 SQL(两者可并存互转)")
    # 改 SQL 必须重新过校验（只读正文 + 参数声明 + 字段声明），不通过不保存
    body_touched = any(req.get(k) is not None for k in
                       ("sql_query", "sql_bodies", "variables", "field_config"))
    if sql_query and (req.get("sql_query") or body_touched):
        _validate_select(sql_query)
    stored = _validate_and_normalize(req, existing=ds) if body_touched else {}
    if stored.get("touched_tables") is not None:
        updates.append("touched_tables = %s")
        params.append(stored["touched_tables"])
    if stored.get("sql_bodies") is not None and req.get("sql_bodies") is None:
        updates.append("sql_bodies = %s")
        params.append(stored["sql_bodies"])
    # 正文/参数/字段变更即认证失效（防"改完仍挂已认证"）
    if any(req.get(k) is not None for k in ("sql_query", "sql_bodies", "variables",
                                            "field_config")) and req.get("certified") is None:
        updates.append("certified = %s")
        params.append(0)
        updates.append("approved_by = %s")
        params.append(None)
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
    execute_write("DELETE FROM adh_role_dataset_access WHERE dataset_id = %s", (dataset_id,))
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
    from backend.core.query_executor import validate_sql
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
    from backend.common.api_permission import check_api_permission
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
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

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
    from backend.core.governed_query import governed_execute
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
        from backend.semantics.mdl_compiler import _get_dimensions, _get_metrics
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
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.gates import execute_semantic
    from backend.semantics.intent import parse_intent
    from backend.semantics.planner import plan

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


def _dataset_db_type(ds: dict) -> str:
    """数据集执行数据源的 db_type（决定正文变体与编译方言）；查不到回落 mysql。"""
    ds_id = int(ds.get("datasource_id") or 0)
    if not ds_id:
        return "mysql"
    try:
        from backend.common.db.datasource_db import get_datasource_by_id

        return str((get_datasource_by_id(ds_id) or {}).get("db_type") or "mysql")
    except Exception as e:  # noqa: BLE001 — 查不到不阻断，但保守用 mysql 族
        logger.warning("[dataset] db_type lookup failed for ds=%s: %s", ds_id, e)
        return "mysql"


def has_row_scopes(dataset_id: int) -> bool:
    """该数据集是否配了行级范围（供语义层绑定前判定）。"""
    row = execute_query(
        "SELECT COUNT(*) AS n FROM adh_dataset_scopes WHERE dataset_id = %s",
        (int(dataset_id or 0),), fetchone=True)
    return bool(row and int(row.get("n") or 0) > 0)


def _query_sql(ds: dict, params: dict, scopes: list[dict], identity: dict) -> dict:
    """SQL 数据集取数：编译委托 `semantics.dataset_compiler`（与语义层同一实现）。

    参数渲染/方言正文选择/输出列聚合全在编译器；本函数只负责把
    行级 scope 以子查询包裹叠加（只收紧不放宽）并送治理执行口。
    """
    from backend.core.query_executor import validate_sql
    from backend.core.governed_query import governed_execute
    from backend.semantics.dataset_compiler import DatasetCompileError, compile_dataset

    args = params.get("args") or params.get("variables") or {}
    try:
        compiled = compile_dataset(
            ds,
            args=args if isinstance(args, dict) else {},
            dimensions=params.get("dimensions") or [],
            measures=params.get("measures") or params.get("metrics") or [],
            filters=params.get("filters") or [],
            order=params.get("order") or [],
            limit=params.get("limit"),
            time_grain=params.get("time_grain"),
            db_type=_dataset_db_type(ds),
        )
    except DatasetCompileError as e:
        # 参数非法/无本引擎正文: 直接告诉调用方原因（fail-loud，不换通道不降级）
        raise ValueError(str(e))
    for w in compiled.warnings:
        logger.info("[dataset] %s: %s", ds.get("name"), w)

    sql = compiled.sql
    if scopes:
        where = _build_where(scopes)
        limit = min(int(params.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)
        sql = f"SELECT * FROM ({sql}) AS _ds_scoped{where} LIMIT {limit}"

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
    if compiled.resolved_tables:
        result["resolved_tables"] = compiled.resolved_tables
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
