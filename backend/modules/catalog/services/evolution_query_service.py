"""进化中心统一查询服务：角色域、服务端分页与安全 DTO。

HTTP 与 AI 只构造可信 ``EvolutionContext``，候选、提议、计数和资源选项必须经本服务。
角色数据源授权唯一来自 ``role_service.get_user_allowed_datasources``；空授权 fail-closed，
不得解释为全量。列表固定返回 ``items/total/page/page_size``，筛选与 COUNT 共用同一构造。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from backend.modules.catalog.services import evolution_service as evo
from backend.modules.catalog.services import knowledge_update_service as kus

logger = logging.getLogger("catalog.evolution_query")

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
QUEUES = ("build", "binding")


class EvolutionQueryError(ValueError):
    """可映射为 HTTP 的查询错误；不把权限/参数问题伪装成空清单。"""

    def __init__(self, message: str, *, status_code: int = 422, code: str = "QUERY_INVALID"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _conn():
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def _q(sql: str, params: tuple = ()) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def _iso(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value else ""


def _page_values(page, page_size) -> tuple[int, int]:
    try:
        current = int(1 if page in (None, "") else page)
        size = int(DEFAULT_PAGE_SIZE if page_size in (None, "") else page_size)
    except (TypeError, ValueError) as exc:
        raise EvolutionQueryError("page/page_size 必须为正整数", status_code=400) from exc
    if current < 1:
        raise EvolutionQueryError("page 必须 ≥ 1", status_code=400)
    if size < 1:
        raise EvolutionQueryError("page_size 必须 ≥ 1", status_code=400)
    return current, min(size, MAX_PAGE_SIZE)


def _choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    v = str(value or "").strip()
    if v and v not in allowed:
        raise EvolutionQueryError(f"{field} 无效，应为 {list(allowed)}", status_code=400)
    return v


@dataclass(frozen=True)
class EvolutionContext:
    """由 HTTP 身份或 AI ExecutionContext 构造，不接受工具/请求体身份字段。"""

    user_id: int
    workspace_id: int
    actor: str
    channel: str
    allowed_datasources: tuple[int, ...]
    datasource_names: tuple[str, ...]
    datasource_options: tuple[dict, ...]

    @classmethod
    def create(cls, *, user_id: int, workspace_id: int, actor: str = "", channel: str) -> "EvolutionContext":
        uid = int(user_id or 0)
        ws = int(workspace_id or 0)
        if uid <= 0:
            raise EvolutionQueryError("缺少可信用户身份", status_code=401, code="NO_IDENTITY")
        if ws <= 0:
            raise EvolutionQueryError("进化中心必须绑定已授权工作空间", status_code=403,
                                      code="WORKSPACE_REQUIRED")
        if channel not in ("http", "ai"):
            raise EvolutionQueryError("channel 无效", status_code=400)
        # 授权集只从角色真源读取；测试也不得通过参数注入另一套可见域。
        from backend.core.role_service import role_service
        allowed_datasources = role_service.get_user_allowed_datasources(uid, ws)
        ids = tuple(sorted({int(i) for i in (allowed_datasources or []) if int(i or 0) > 0}))
        options: list[dict] = []
        if ids:
            placeholders = ",".join(["%s"] * len(ids))
            options = [dict(r) for r in _q(
                f"SELECT id, name, db_type FROM adh_datasources "
                f"WHERE id IN ({placeholders}) ORDER BY name, id", ids)]
        clean_options = tuple(
            {"id": int(r.get("id") or 0),
             "name": str(r.get("name") or "").strip(),
             "db_type": str(r.get("db_type") or "")}
            for r in options if str(r.get("name") or "").strip()
        )
        return cls(uid, ws, str(actor or f"user:{uid}"), channel, ids,
                   tuple(sorted({r["name"] for r in clean_options})), clean_options)

    @classmethod
    def from_http(cls, user: dict, workspace_id: int) -> "EvolutionContext":
        return cls.create(user_id=int(user.get("user_id") or 0), workspace_id=workspace_id,
                          actor=str(user.get("username") or user.get("user_id") or ""),
                          channel="http")

    @classmethod
    def from_execution(cls, context) -> "EvolutionContext":
        return cls.create(user_id=int(getattr(context, "user_id", 0) or 0),
                          workspace_id=int(getattr(context, "workspace_id", 0) or 0),
                          actor=str(getattr(context, "username", "") or ""), channel="ai")

    @property
    def datasource_id_map(self) -> dict[str, int]:
        return {r["name"]: int(r["id"]) for r in self.datasource_options}

    def validate_datasource(self, datasource_name: str) -> str:
        name = str(datasource_name or "").strip()
        if name and name not in self.datasource_names:
            raise EvolutionQueryError(
                f"数据源「{name}」不在当前角色授权范围内",
                code="DATASOURCE_FORBIDDEN")
        return name


def scope_condition(ctx: EvolutionContext, column: str = "datasource_name") -> tuple[str, tuple]:
    """候选/提议作用域：授权源 ∪ 全局词汇；空授权时连全局词汇也不可见。"""
    if not ctx.datasource_names:
        return "1 = 0", ()
    placeholders = ",".join(["%s"] * len(ctx.datasource_names))
    return (f"({column} IN ({placeholders}) OR COALESCE({column}, '') = '')",
            tuple(ctx.datasource_names))


_CANDIDATE_COLUMNS = (
    "id, term, kind, source, datasource_name, target_type, target_ref, classify_reason, "
    "evidence, hit_count, status, converted_ref, converted_kind, decided_by, decided_at, "
    "decision_note, decision_version, last_checked_at, created_at, updated_at"
)
_PROPOSAL_COLUMNS = (
    "id, source, target_type, title, rationale, target_hint, datasource_name, status, "
    "applied_ref, decided_by, decided_at, decided_note, decision_version, "
    "origin_conversation_id, origin_message_uuid, created_at, updated_at"
)
_EVIDENCE_TEXT_KEYS = (
    "planner_type", "reason", "feedback_reason", "question", "user_note", "failure_reason",
    "expected_ref", "suite", "case_key", "capability",
)
_EVIDENCE_LIMITS = {
    "planner_type": 32, "reason": 300, "feedback_reason": 64, "question": 300,
    "user_note": 300, "failure_reason": 300, "expected_ref": 128, "suite": 64,
    "case_key": 128, "capability": 128,
}


def _decode_evidence(value) -> tuple[dict, bool]:
    if isinstance(value, dict):
        return value, False
    if value in (None, ""):
        return {}, False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}, True
    if not isinstance(parsed, dict):
        return {}, True
    return parsed, False


def _safe_evidence(value) -> dict:
    """evidence 白名单：只留问句/业务候选/分类原因，不整包透传物理字段和内部 id。"""
    raw, invalid = _decode_evidence(value)
    out: dict = {}
    candidates = raw.get("candidates")
    if isinstance(candidates, list):
        out["candidates"] = [str(x).strip()[:128] for x in candidates
                             if isinstance(x, str) and x.strip()][:6]
    for key in _EVIDENCE_TEXT_KEYS:
        text = str(raw.get(key) or "").strip()
        if text:
            out[key] = text[:_EVIDENCE_LIMITS[key]]
    if invalid:
        out["evidence_error"] = "invalid_json"
        logger.warning("[evolution-query] 候选 evidence 不是合法 JSON 对象，已显式标注")
    return out


def _candidate_dto(row: dict) -> dict:
    allowed = (
        "id", "term", "kind", "source", "datasource_name", "target_type", "target_ref",
        "classify_reason", "hit_count", "status", "converted_ref", "converted_kind",
        "decided_by", "decision_note", "decision_version", "last_checked_at",
        "created_at", "updated_at", "decided_at",
    )
    out = {k: row.get(k) for k in allowed if k in row}
    out["evidence"] = _safe_evidence(row.get("evidence"))
    version = max(int(out.get("decision_version") or 1), 1)
    out["decision_version"] = version
    out["expected_version"] = version
    for key in ("last_checked_at", "created_at", "updated_at", "decided_at"):
        out[key] = _iso(out.get(key))
    out["id"] = int(out.get("id") or 0)
    out["hit_count"] = max(int(out.get("hit_count") or 0), 0)
    return out


def candidate_for_ai(candidate: dict) -> dict:
    """AI 业务投影：移除内部 id/版本/对象 key，物理字段已在 DTO 层剥离。"""
    out = dict(candidate)
    for key in ("id", "decision_version", "expected_version", "object_hint",
                "origin_conversation_id", "origin_message_uuid"):
        out.pop(key, None)
    if out.get("target_type") == "object":
        out.pop("target_ref", None)
    suggestion = out.get("suggestion")
    if isinstance(suggestion, dict):
        suggestion = dict(suggestion)
        prefilled = dict(suggestion.get("prefilled") or {})
        if prefilled.get("target_type") == "object":
            prefilled.pop("target_ref", None)
        if out.get("target_type") == "object":
            suggestion["suggested_refs"] = []
        suggestion["prefilled"] = prefilled
        out["suggestion"] = suggestion
    return out


def _proposal_dto(row: dict, *, detail: bool = False) -> dict:
    allowed = (
        "id", "source", "target_type", "title", "rationale", "target_hint", "datasource_name",
        "status", "applied_ref", "decided_by", "decided_note", "decision_version",
        "origin_conversation_id", "origin_message_uuid", "created_at", "updated_at", "decided_at",
    )
    out = {k: row.get(k) for k in allowed if k in row}
    if detail:
        out["content"] = str(row.get("content") or "")
    else:
        out["content_preview"] = str(row.get("content_preview") or "")[:160]
    version = max(int(out.get("decision_version") or 1), 1)
    out["decision_version"] = version
    out["expected_version"] = version
    out["id"] = int(out.get("id") or 0)
    for key in ("created_at", "updated_at", "decided_at"):
        out[key] = _iso(out.get(key))
    return out


def proposal_for_ai(proposal: dict) -> dict:
    out = dict(proposal)
    for key in ("id", "decision_version", "expected_version", "origin_conversation_id",
                "origin_message_uuid"):
        out.pop(key, None)
    return out


def _candidate_filters(ctx: EvolutionContext, *, status: str, kind: str, queue: str,
                       source: str, datasource_name: str, q: str) -> tuple[str, tuple]:
    conditions, params = [], []
    scope, scope_params = scope_condition(ctx)
    conditions.append(scope)
    params.extend(scope_params)
    status = _choice(status, evo.STATUSES, "status")
    kind = _choice(kind, evo.KINDS, "kind")
    queue = _choice(queue, QUEUES, "queue")
    source = _choice(source, evo.SOURCES, "source")
    datasource_name = ctx.validate_datasource(datasource_name)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if kind:
        conditions.append("kind = %s")
        params.append(kind)
    if queue:
        conditions.append("CASE kind WHEN 'binding_gap' THEN 'binding' ELSE 'build' END = %s")
        params.append(queue)
    if source:
        conditions.append("source = %s")
        params.append(source)
    if datasource_name:
        conditions.append("datasource_name = %s")
        params.append(datasource_name)
    keyword = str(q or "").strip()[:128]
    if keyword:
        conditions.append("(term LIKE %s OR classify_reason LIKE %s OR target_ref LIKE %s)")
        like = f"%{keyword}%"
        params.extend((like, like, like))
    return (" WHERE " + " AND ".join(conditions)), tuple(params)


def list_candidates(ctx: EvolutionContext, *, status: str = "pending", kind: str = "",
                    queue: str = "", source: str = "", datasource_name: str = "",
                    q: str = "", page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    page, page_size = _page_values(page, page_size)
    where, params = _candidate_filters(ctx, status=status, kind=kind, queue=queue,
                                       source=source, datasource_name=datasource_name, q=q)
    total_row = _q(f"SELECT COUNT(*) AS total FROM adh_evolution_candidates{where}", params)
    total = int((total_row[0] or {}).get("total") or 0)
    rows = _q(
        f"SELECT {_CANDIDATE_COLUMNS} FROM adh_evolution_candidates{where} "
        f"{evo._QUEUE_ORDER_BY} LIMIT %s OFFSET %s",
        params + (page_size, (page - 1) * page_size))
    items = []
    for row in rows:
        dto = _candidate_dto(row)
        if dto.get("status") == "pending":
            dto["suggestion"] = evo.suggest_action(dto)
        items.append(dto)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_candidate(ctx: EvolutionContext, candidate_id: int) -> dict | None:
    scope, params = scope_condition(ctx)
    rows = _q(f"SELECT {_CANDIDATE_COLUMNS} FROM adh_evolution_candidates "
              f"WHERE id = %s AND {scope}", (int(candidate_id),) + params)
    if not rows:
        return None
    dto = _candidate_dto(rows[0])
    if dto.get("status") == "pending":
        dto["suggestion"] = evo.suggest_action(dto)
    return dto


def candidate_row_for_decision(conn, ctx: EvolutionContext, candidate_id: int,
                                *, for_update: bool = True) -> dict | None:
    """决策事务内按角色域加锁取候选；作用域与列表真源完全一致。"""
    scope, params = scope_condition(ctx)
    lock = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_CANDIDATE_COLUMNS} FROM adh_evolution_candidates "
                    f"WHERE id = %s AND {scope}{lock}", (int(candidate_id),) + params)
        row = cur.fetchone()
    return dict(row) if row else None


def proposal_row_for_decision(conn, ctx: EvolutionContext, proposal_id: int,
                              *, for_update: bool = True) -> dict | None:
    """决策事务内按角色域加锁取提议；正文只在需要时读取。"""
    scope, params = scope_condition(ctx)
    lock = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute("SELECT " + _PROPOSAL_COLUMNS + ", content "
                    "FROM adh_knowledge_update_proposals "
                    f"WHERE id = %s AND {scope}{lock}", (int(proposal_id),) + params)
        row = cur.fetchone()
    return dict(row) if row else None


def resolve_candidate(ctx: EvolutionContext, *, term: str, datasource_name: str = "",
                      kind: str = "", status: str = "pending") -> dict:
    """按业务名精确解析候选；内部 id 与版本只留在服务端。"""
    name = str(term or "").strip()
    if not name:
        raise EvolutionQueryError("候选业务名不能为空", status_code=400)
    source = ctx.validate_datasource(datasource_name)
    selected_kind = _choice(kind, evo.KINDS, "kind")
    selected_status = _choice(status, evo.STATUSES, "status")
    scope, params = scope_condition(ctx)
    sql = (f"SELECT {_CANDIDATE_COLUMNS} FROM adh_evolution_candidates "
           f"WHERE term = %s AND {scope}")
    values: tuple = (name,) + params
    if selected_status:
        sql += " AND status = %s"
        values += (selected_status,)
    if selected_kind:
        sql += " AND kind = %s"
        values += (selected_kind,)
    if source:
        sql += " AND datasource_name = %s"
        values += (source,)
    rows = _q(sql + " ORDER BY id ASC LIMIT 2", values)
    if not rows:
        raise EvolutionQueryError(f"候选「{name}」不存在或不在当前角色域内",
                                  status_code=404, code="CANDIDATE_NOT_FOUND")
    if len(rows) > 1:
        raise EvolutionQueryError(f"候选「{name}」匹配到多条，请指定数据源业务名或类型",
                                  status_code=409, code="CANDIDATE_AMBIGUOUS")
    dto = _candidate_dto(rows[0])
    if dto.get("status") == "pending":
        dto["suggestion"] = evo.suggest_action(dto)
    return dto


def _proposal_filters(ctx: EvolutionContext, *, status: str, target_type: str, source: str,
                      datasource_name: str, q: str) -> tuple[str, tuple]:
    conditions, params = [], []
    scope, scope_params = scope_condition(ctx)
    conditions.append(scope)
    params.extend(scope_params)
    status = _choice(status, kus.STATUSES, "status")
    target_type = _choice(target_type, kus.TARGET_TYPES, "target_type")
    source = _choice(source, kus.SOURCES, "source")
    datasource_name = ctx.validate_datasource(datasource_name)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if target_type:
        conditions.append("target_type = %s")
        params.append(target_type)
    if source:
        conditions.append("source = %s")
        params.append(source)
    if datasource_name:
        conditions.append("datasource_name = %s")
        params.append(datasource_name)
    keyword = str(q or "").strip()[:128]
    if keyword:
        conditions.append("(title LIKE %s OR rationale LIKE %s OR target_hint LIKE %s)")
        like = f"%{keyword}%"
        params.extend((like, like, like))
    return (" WHERE " + " AND ".join(conditions)), tuple(params)


def list_proposals(ctx: EvolutionContext, *, status: str = "", target_type: str = "",
                   source: str = "", datasource_name: str = "", q: str = "",
                   page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    page, page_size = _page_values(page, page_size)
    where, params = _proposal_filters(ctx, status=status, target_type=target_type,
                                      source=source, datasource_name=datasource_name, q=q)
    total_row = _q(f"SELECT COUNT(*) AS total FROM adh_knowledge_update_proposals{where}", params)
    total = int((total_row[0] or {}).get("total") or 0)
    rows = _q(
        "SELECT " + _PROPOSAL_COLUMNS + ", LEFT(content, 160) AS content_preview "
        "FROM adh_knowledge_update_proposals" + where +
        " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
        params + (page_size, (page - 1) * page_size))
    return {"items": [_proposal_dto(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


def get_proposal(ctx: EvolutionContext, proposal_id: int) -> dict | None:
    scope, params = scope_condition(ctx)
    rows = _q("SELECT " + _PROPOSAL_COLUMNS + ", content "
              "FROM adh_knowledge_update_proposals "
              f"WHERE id = %s AND {scope}", (int(proposal_id),) + params)
    return _proposal_dto(rows[0], detail=True) if rows else None


def resolve_proposal(ctx: EvolutionContext, *, title: str, datasource_name: str = "",
                       target_type: str = "", status: str = "pending") -> dict:
    name = str(title or "").strip()
    if not name:
        raise EvolutionQueryError("提议标题不能为空", status_code=400)
    source = ctx.validate_datasource(datasource_name)
    selected_type = _choice(target_type, kus.TARGET_TYPES, "target_type")
    selected_status = _choice(status, kus.STATUSES, "status")
    scope, params = scope_condition(ctx)
    sql = ("SELECT " + _PROPOSAL_COLUMNS + ", content "
           "FROM adh_knowledge_update_proposals "
           f"WHERE title = %s AND {scope}")
    values: tuple = (name,) + params
    if selected_status:
        sql += " AND status = %s"
        values += (selected_status,)
    if selected_type:
        sql += " AND target_type = %s"
        values += (selected_type,)
    if source:
        sql += " AND datasource_name = %s"
        values += (source,)
    rows = _q(sql + " ORDER BY id DESC LIMIT 2", values)
    if not rows:
        raise EvolutionQueryError(f"提议「{name}」不存在或不在当前角色域内",
                                  status_code=404, code="PROPOSAL_NOT_FOUND")
    if len(rows) > 1:
        raise EvolutionQueryError(f"提议「{name}」匹配到多条，请指定数据源业务名或落点域",
                                  status_code=409, code="PROPOSAL_AMBIGUOUS")
    return _proposal_dto(rows[0], detail=True)


def proposal_counts(ctx: EvolutionContext) -> dict[str, int]:
    scope, params = scope_condition(ctx)
    rows = _q("SELECT status, COUNT(*) AS n FROM adh_knowledge_update_proposals "
              f"WHERE {scope} GROUP BY status", params)
    values = {r.get("status"): int(r.get("n") or 0) for r in rows}
    return {status: values.get(status, 0) for status in kus.STATUSES}


def datasource_options(ctx: EvolutionContext) -> list[dict]:
    """对人/AI 的数据源业务投影；内部 id 只留在上下文中供服务端解析。"""
    return [{"name": r["name"], "db_type": r["db_type"]} for r in ctx.datasource_options]


def _deploy_entries(value) -> list[dict]:
    if value in (None, ""):
        return []
    try:
        rows = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise EvolutionQueryError("本体 deploy_sites 配置不是合法 JSON", status_code=500,
                                  code="ONTOLOGY_CONFIG_INVALID") from exc
    if not isinstance(rows, list):
        raise EvolutionQueryError("本体 deploy_sites 必须是数组", status_code=500,
                                  code="ONTOLOGY_CONFIG_INVALID")
    return [{"site_code": str(r.get("site_code") or "").strip(),
             "datasource": str(r.get("datasource") or "").strip()}
            for r in rows if isinstance(r, dict)]


def _object_site_rules(value) -> dict[str, list[str]]:
    """对象级白名单；非法/空配置不收紧，与 binding_resolver 的存量口径一致。"""
    if value in (None, ""):
        return {}
    try:
        raw = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(x) for x in v if str(x)]
            for k, v in raw.items() if isinstance(v, list)}


def _visible_models(ctx: EvolutionContext, datasource_name: str = "") -> list[dict]:
    if not ctx.datasource_names:
        return []
    selected = ctx.validate_datasource(datasource_name)
    id_map = ctx.datasource_id_map
    rows = _q("SELECT id, name, kind, datasource_id, deploy_sites, object_site_rules "
              "FROM adh_ontology_models WHERE status = 'active' "
              "AND COALESCE(kind, '') <> 'system' ORDER BY name, id")
    out = []
    for row in rows:
        source_ids = {int(row.get("datasource_id") or 0)} - {0}
        deploy = _deploy_entries(row.get("deploy_sites"))
        names = {e["datasource"] for e in deploy if e["datasource"]}
        if selected:
            selected_id = id_map.get(selected)
            if selected_id not in source_ids and selected not in names:
                continue
        elif not (source_ids & set(ctx.allowed_datasources)) and not (names & set(ctx.datasource_names)):
            continue
        row["_deploy_entries"] = deploy
        row["_object_site_rules"] = _object_site_rules(row.get("object_site_rules"))
        out.append(row)
    return out


def object_options(ctx: EvolutionContext, datasource_name: str) -> list[dict]:
    """授权数据源内的业务对象目录；显示名与 key 分离，系统本体不进入选择框。"""
    selected = ctx.validate_datasource(datasource_name)
    if not selected:
        raise EvolutionQueryError("请先选择授权数据源，再读取本体对象", status_code=400,
                                  code="DATASOURCE_REQUIRED")
    models = _visible_models(ctx, selected)
    if not models:
        return []
    ids = tuple(int(m["id"]) for m in models)
    placeholders = ",".join(["%s"] * len(ids))
    rows = _q("SELECT o.object_key AS object_key, o.display_name, o.model_id "
              "FROM adh_ontology_objects o WHERE o.is_active = 1 "
              f"AND o.model_id IN ({placeholders}) "
              "ORDER BY o.display_name, o.object_key", ids)
    model_map = {int(m["id"]): m for m in models}
    model_names = {int(m["id"]): str(m.get("name") or "") for m in models}
    out = []
    for r in rows:
        object_key = str(r.get("object_key") or "")
        model = model_map.get(int(r.get("model_id") or 0)) or {}
        rules = model.get("_object_site_rules") or {}
        if object_key in rules:
            declared = model.get("_deploy_entries") or []
            allowed_codes = set(rules[object_key])
            # 白名单只能收紧；声明站点里没有匹配 code 时不得把对象广告到该源。
            if not any(e.get("datasource") == selected and e.get("site_code") in allowed_codes
                       for e in declared):
                continue
        if object_key:
            out.append({"key": object_key,
                        "display_name": str(r.get("display_name") or object_key),
                        "model_id": int(r.get("model_id") or 0),
                        "model_name": model_names.get(int(r.get("model_id") or 0), "")})
    return out


def property_options(ctx: EvolutionContext, *, bound_object_key: str,
                     datasource_name: str) -> list[dict]:
    selected = ctx.validate_datasource(datasource_name)
    if not selected:
        raise EvolutionQueryError("请先选择授权数据源，再读取对象属性", status_code=400,
                                  code="DATASOURCE_REQUIRED")
    allowed = {str(o.get("key") or "").lower() for o in object_options(ctx, selected)}
    if str(bound_object_key or "").strip().lower() not in allowed:
        raise EvolutionQueryError("对象不在当前角色授权范围内", status_code=403,
                                  code="OBJECT_FORBIDDEN")
    return evo.property_options(bound_object_key=bound_object_key, datasource_name=selected)


def target_options(ctx: EvolutionContext, *, target_type: str,
                   datasource_name: str = "") -> list[dict]:
    t = _choice(target_type, ("metric", "dimension", "object"), "target_type")
    if not t:
        raise EvolutionQueryError("target_type 必填", status_code=400)
    if t == "object":
        return object_options(ctx, datasource_name)
    selected = ctx.validate_datasource(datasource_name)
    table = "adh_metrics" if t == "metric" else "adh_dimensions"
    if selected:
        ds_id = ctx.datasource_id_map[selected]
        rows = _q(f"SELECT name FROM {table} WHERE is_active = 1 "
                  "AND (datasource_id = %s OR datasource_id = 0) ORDER BY name, id", (ds_id,))
    else:
        rows = _q(f"SELECT name FROM {table} WHERE is_active = 1 AND datasource_id = 0 "
                  "ORDER BY name, id")
    return [{"key": str(r.get("name") or ""), "display_name": str(r.get("name") or "")}
            for r in rows if r.get("name")]


def _digest_job_dto(row: dict) -> dict:
    raw_result = row.get("result_json")
    if isinstance(raw_result, str) and raw_result:
        try:
            raw_result = json.loads(raw_result)
        except (TypeError, ValueError) as exc:
            raise EvolutionQueryError("归纳作业结果不是合法 JSON", status_code=500,
                                      code="DIGEST_JOB_INVALID") from exc
    if raw_result is not None and not isinstance(raw_result, dict):
        raise EvolutionQueryError("归纳作业结果必须是对象", status_code=500,
                                  code="DIGEST_JOB_INVALID")
    return {"job_id": int(row.get("id") or 0), "status": str(row.get("status") or ""),
            "requested_by": str(row.get("actor") or ""),
            "created_at": _iso(row.get("created_at")),
            "started_at": _iso(row.get("started_at")),
            "finished_at": _iso(row.get("finished_at")),
            "error_code": str(row.get("error_code") or ""),
            "error_message": str(row.get("error_message") or ""),
            "result": raw_result or {}}


def get_digest_job(ctx: EvolutionContext, job_id: int) -> dict | None:
    rows = _q("SELECT id, status, actor, created_at, started_at, finished_at, "
              "error_code, error_message, result_json FROM adh_evolution_digest_jobs "
              "WHERE id = %s AND workspace_id = %s", (int(job_id), ctx.workspace_id))
    return _digest_job_dto(rows[0]) if rows else None


def recent_digest_job(ctx: EvolutionContext) -> dict | None:
    rows = _q("SELECT id, status, actor, created_at, started_at, finished_at, "
              "error_code, error_message, result_json FROM adh_evolution_digest_jobs "
              "WHERE workspace_id = %s ORDER BY id DESC LIMIT 1", (ctx.workspace_id,))
    return _digest_job_dto(rows[0]) if rows else None


def overview(ctx: EvolutionContext) -> dict:
    scope, params = scope_condition(ctx)
    by_kind = {r["kind"]: int(r["n"]) for r in _q(
        "SELECT kind, COUNT(*) AS n FROM adh_evolution_candidates "
        f"WHERE status = 'pending' AND {scope} GROUP BY kind", params)}
    by_source = {r["source"]: int(r["n"]) for r in _q(
        "SELECT source, COUNT(*) AS n FROM adh_evolution_candidates "
        f"WHERE status = 'pending' AND {scope} GROUP BY source", params)}
    outcome = {r["status"]: int(r["n"]) for r in _q(
        "SELECT status, COUNT(*) AS n FROM adh_evolution_candidates "
        f"WHERE status IN ('converted','verified','stale','rejected','expired') "
        f"AND {scope} GROUP BY status", params)}
    fb = _q("SELECT SUM(CASE WHEN satisfied=1 THEN 1 ELSE 0 END) AS up, "
            "SUM(CASE WHEN satisfied=0 THEN 1 ELSE 0 END) AS down, "
            "SUM(CASE WHEN satisfied=0 AND digest_status='new' THEN 1 ELSE 0 END) "
            "AS pending_negative FROM adh_message_feedback WHERE workspace_id = %s",
            (ctx.workspace_id,))[0]
    return {"pending_by_kind": by_kind, "pending_by_source": by_source,
            "proposal_counts": proposal_counts(ctx),
            "outcome": outcome,
            "feedback": {"up": int(fb.get("up") or 0), "down": int(fb.get("down") or 0),
                         "pending_negative": int(fb.get("pending_negative") or 0)},
            "reasons": list(evo.FEEDBACK_REASONS), "kinds": list(evo.KINDS),
            "sources": list(evo.SOURCES), "recent_digest_job": recent_digest_job(ctx)}
