"""进化中心统一决策服务：候选/提议命令、权限、版本、幂等、领域写入与审计。

HTTP 与 AI 只做薄适配（P1-4）：分类查询走 ``evolution_query_service``，处置编排、
权限判断、版本仲裁、幂等与审计账本都在本服务完成，页面 / API / AI 工具不再各自
实现状态转换。

三条不可妥协的纪律
------------------
* **状态机只前进一步**：仅 ``pending`` 可处置；两条通道统一用
  ``WHERE id=? AND status='pending' AND decision_version=?`` 条件更新仲裁，
  并发单赢，冲突返回 409 ``STALE_DECISION``，不覆盖既有结论。
* **决策必须幂等**：请求带幂等键（UI 用 ``subject:version:action``，AI 用工具调用键），
  首次结果写入 ``adh_evolution_decisions`` 账本，重放返回首次结果、不重复写领域资产；
  同键不同请求 409 ``IDEMPOTENCY_CONFLICT``。校验/预检失败不入账（允许同键修正后重试），
  入账只记"产生了状态或领域影响"的决策（succeeded/conflict）。
* **领域写入与状态转换协调**：字典别名/术语/候选更新同一事务；指标/维度草稿与
  本体对象别名走领域服务（``metrics_service``/``ontology_service``），领域写入成功后
  状态转换冲突时显式返回 ``DOMAIN_WRITTEN_DECISION_CONFLICT`` 与落点（人工核对），
  不回滚成"什么都没发生"（no-silent-degradation）。

领域写入纪律（P1-5）：别名收编调用 ``metrics_service.update_metric/update_dimension``
（不直写 UPDATE 字典表）；转术语调用 ``term_service.create_term``（数据源分桶、
缓存失效同源，禁止裸 INSERT）；指标/维度草稿调用 ``metrics_service.create_*`` 保持
``is_active=0``；本体对象别名调用 ``ontology_service.merge_object_scalars`` 只落草案，
发布仍是独立 ``ontology:publish`` 动作。驳回必须填 ``decision_note``；提议 applied
必须带非空 ``applied_ref``（真实落点，禁止空落点假闭环）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading

from backend.modules.catalog.services import evolution_query_service as queries

logger = logging.getLogger("catalog.evolution_decision")

#: 候选动作 → 领域权限码（与 capability_contracts._EVOLUTION_DOMAIN_PERMS 同源；
#: 双闸第一道永远是 evolution:manage）。
CANDIDATE_ACTIONS: dict[str, dict] = {
    "evolution_candidate.adopt": {"domain_perm": "ontology:save", "label": "收编别名",
                                  "transition": "converted", "converted_kind": "alias"},
    "evolution_candidate.reject": {"domain_perm": "ontology:save", "label": "驳回候选",
                                   "transition": "rejected", "converted_kind": ""},
    "evolution_candidate.to_term": {"domain_perm": "ontology:save", "label": "转术语",
                                    "transition": "converted", "converted_kind": "term"},
    "evolution_metric.create": {"domain_perm": "metrics:manage", "label": "建指标草稿",
                                "transition": "converted", "converted_kind": "metric"},
    "evolution_dimension.create": {"domain_perm": "metrics:manage", "label": "建维度草稿",
                                   "transition": "converted", "converted_kind": "dimension"},
}

PROPOSAL_ACTIONS: dict[str, dict] = {
    "evolution_proposal.applied": {"domain_perm": "knowledge:manage",
                                   "label": "标记知识更新已处理", "transition": "applied"},
    "evolution_proposal.reject": {"domain_perm": "knowledge:manage",
                                  "label": "驳回知识更新提议", "transition": "rejected"},
}

#: 领域写入不与候选状态同事务的动作（跨 ontology/metrics 服务的草案写入），
#: 状态转换冲突时回 DOMAIN_WRITTEN_DECISION_CONFLICT 而不是无声回滚。
#: 注意：别名收编到**字典行**与转术语同 metadata DB，走单事务；
#: 只有对象别名（ontology_service）与指标/维度草稿（metrics 服务）属跨服务。
def _is_cross_service(action_key: str, data: dict) -> bool:
    if action_key in ("evolution_metric.create", "evolution_dimension.create"):
        return True
    return action_key == "evolution_candidate.adopt" and data.get("target_type") == "object"


class EvolutionDecisionError(ValueError):
    """可映射为 HTTP 的决策错误：状态码 + 安全错误码 + （半态时）领域落点。"""

    def __init__(self, message: str, *, status_code: int = 422,
                 code: str = "DECISION_INVALID", domain_ref: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.domain_ref = domain_ref

    def as_dict(self) -> dict:
        out = {"error": str(self), "code": self.code}
        if self.domain_ref:
            out["domain_ref"] = self.domain_ref
        return out


def _conn():
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def _clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _json_default(value):
    return str(value)


# ── 入参校验（服务层再校验一次，HTTP/AI 同口径）─────────────────────────────

def _validate_candidate_input(action_key: str, note: str, data: dict) -> dict:
    out = dict(data or {})
    if action_key == "evolution_candidate.adopt":
        target_type = _clean(out.get("target_type"), 32)
        if target_type not in ("metric", "dimension", "object"):
            raise EvolutionDecisionError(
                "target_type 必须是 metric|dimension|object（收编到指标/维度字典行或本体对象）")
        target = _clean(out.get("target") or out.get("target_ref"), 128)
        if not target:
            raise EvolutionDecisionError("必须指明收编目标（业务名）：metric/dimension 用字典名，"
                                         "object 用对象业务名")
        out["target_type"] = target_type
        out["target"] = target
    elif action_key == "evolution_candidate.reject":
        if not _clean(note, 500):
            raise EvolutionDecisionError("驳回必须说明理由（note）：无理由驳回无法审计")
    elif action_key == "evolution_candidate.to_term":
        out["description"] = _clean(out.get("description"), 512)
    elif action_key == "evolution_metric.create":
        if not _clean(out.get("object"), 128):
            raise EvolutionDecisionError("必须指明归属对象（业务名或对象 key）")
        out["object"] = _clean(out.get("object"), 128)
        out["display_name"] = _clean(out.get("display_name"), 128)
        out["agg_type"] = _clean(out.get("agg_type"), 32) or "COUNT"
        out["formula"] = str(out.get("formula") or "")
        out["unit"] = _clean(out.get("unit"), 32)
        out["description"] = _clean(out.get("description"), 512)
    elif action_key == "evolution_dimension.create":
        if not _clean(out.get("object"), 128):
            raise EvolutionDecisionError("必须指明归属对象（业务名或对象 key）")
        if not _clean(out.get("property"), 128):
            raise EvolutionDecisionError("必须指明对象的业务属性名（property）")
        out["object"] = _clean(out.get("object"), 128)
        out["property"] = _clean(out.get("property"), 128)
        out["display_name"] = _clean(out.get("display_name"), 128)
        out["category"] = _clean(out.get("category"), 32) or "属性"
        out["description"] = _clean(out.get("description"), 512)
    return out


def _validate_proposal_input(action_key: str, note: str, applied_ref: str) -> tuple[str, str]:
    note = _clean(note, 500)
    applied_ref = _clean(applied_ref, 191)
    if action_key == "evolution_proposal.applied":
        if not applied_ref:
            raise EvolutionDecisionError(
                "applied_ref 必填：须先在目标域（知识库文档/分析场景/本体对象）真正落库，"
                "再回写落点业务名；没有落库不得标记已处理", code="APPLIED_REF_REQUIRED")
    elif action_key == "evolution_proposal.reject":
        if not note:
            raise EvolutionDecisionError(
                "驳回必须说明理由（note）：被驳的信号本身是数据，无理由驳回无法审计")
    return note, applied_ref


# ── 权限双闸 ────────────────────────────────────────────────────────────────

def require_action_perm(ctx: queries.EvolutionContext, action_key: str) -> dict:
    """双闸：evolution:manage（能力面）+ 动作所属域权限码（动作面），缺一即拒。

    按字面量逐码调闸（`_require_write_perm(code, label)` 封装），保证
    ``test_function_ai_access`` 的调用点扫描与 ``PERM_GATED_ACTION_PERMS`` 同步。"""
    from backend.core.perm_link import require_write_perm

    spec = CANDIDATE_ACTIONS.get(action_key) or PROPOSAL_ACTIONS.get(action_key)
    if not spec:
        raise EvolutionDecisionError(f"未知决策动作 {action_key!r}", status_code=400,
                                     code="ACTION_NOT_FOUND")
    label = spec["label"]

    def _require_write_perm(code, label=label):
        try:
            require_write_perm(int(ctx.user_id), int(ctx.workspace_id), code, label)
        except PermissionError as exc:
            raise EvolutionDecisionError(str(exc), status_code=403,
                                         code="PERMISSION_DENIED") from exc

    _require_write_perm("evolution:manage")
    domain = spec["domain_perm"]
    if domain == "ontology:save":
        _require_write_perm("ontology:save")
    elif domain == "metrics:manage":
        _require_write_perm("metrics:manage")
    elif domain == "knowledge:manage":
        _require_write_perm("knowledge:manage")
    else:
        _require_write_perm(domain)
    return spec


# ── 幂等账本 ────────────────────────────────────────────────────────────────

def request_hash(action_key: str, expected_version: int, note: str, payload: dict) -> str:
    raw = json.dumps({"action": action_key, "version": int(expected_version or 0),
                      "note": str(note or ""), "input": payload or {}},
                     sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ledger_get(conn, subject_type: str, subject_id: int, idem_key: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, action_key, request_hash, expected_version, result_status, "
            "domain_ref, error_code, result_json FROM adh_evolution_decisions "
            "WHERE subject_type = %s AND subject_id = %s AND idempotency_key = %s",
            (subject_type, int(subject_id), idem_key))
        row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    raw = out.get("result_json")
    if isinstance(raw, str) and raw:
        try:
            out["result_json"] = json.loads(raw)
        except (TypeError, ValueError):
            out["result_json"] = {}
    return out


def _ledger_put(conn, *, subject_type: str, subject_id: int, action_key: str,
                ctx: queries.EvolutionContext, idem_key: str, req_hash: str,
                expected_version: int, result_status: str, domain_ref: str = "",
                error_code: str = "", result: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO adh_evolution_decisions "
            "(subject_type, subject_id, action_key, channel, actor, user_id, workspace_id, "
            " idempotency_key, request_hash, expected_version, result_status, domain_ref, "
            " error_code, result_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (subject_type, int(subject_id), action_key, ctx.channel, _clean(ctx.actor, 64),
             int(ctx.user_id), int(ctx.workspace_id), idem_key, req_hash,
             int(expected_version or 0), result_status, _clean(domain_ref, 191),
             _clean(error_code, 64),
             json.dumps(result or {}, ensure_ascii=False, default=_json_default)))


def _replay(existing: dict, req_hash: str) -> dict:
    """重放：同键同请求回首次结果；同键异请求显式冲突（不静默当重放）。"""
    if str(existing.get("request_hash") or "") != req_hash:
        raise EvolutionDecisionError(
            "同一 Idempotency-Key 已用于不同请求内容，不能重放；请换新幂等键或按首次请求重试",
            status_code=409, code="IDEMPOTENCY_CONFLICT")
    if str(existing.get("result_status") or "") == "succeeded":
        raw = existing.get("result_json") or {}
        out = dict(raw) if isinstance(raw, dict) else {}
        out["replayed"] = True
        return out
    raw = existing.get("result_json") or {}
    message = str((raw or {}).get("message") or "该决策首次执行即冲突，结果未改变") \
        if isinstance(raw, dict) else "该决策首次执行即冲突，结果未改变"
    raise EvolutionDecisionError(message, status_code=409,
                                 code=str(existing.get("error_code") or "STALE_DECISION"),
                                 domain_ref=str(existing.get("domain_ref") or ""))


# ── 状态转换（条件 UPDATE 仲裁）─────────────────────────────────────────────

def _candidate_conflict_message(row: dict) -> str:
    status = str(row.get("status") or "")
    if status != "pending":
        return (f"候选当前状态是 {status!r}，只有待处置（pending）候选可以处置；"
                "既有结论不被新请求覆盖，请刷新后查看")
    return (f"候选已被并发决策修改（当前版本 {int(row.get('decision_version') or 1)}），"
            "本次提交未生效；请刷新获取最新版本后重试")


def _transition_candidate(conn, row: dict, action_key: str, expected_version: int,
                          note: str, domain: dict) -> int:
    spec = CANDIDATE_ACTIONS[action_key]
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE adh_evolution_candidates "
            "SET status = %s, converted_kind = %s, converted_ref = %s, decided_by = %s, "
            "    decided_at = NOW(), decision_note = %s, decision_version = decision_version + 1 "
            "WHERE id = %s AND status = 'pending' AND decision_version = %s",
            (spec["transition"], spec["converted_kind"], _clean(domain.get("domain_ref"), 191),
             _clean(row.get("_actor") or "", 64), _clean(note, 500),
             int(row["id"]), int(expected_version)))
        return int(cur.rowcount or 0)


def _transition_proposal(conn, row: dict, action_key: str, expected_version: int,
                         note: str, applied_ref: str) -> int:
    status = PROPOSAL_ACTIONS[action_key]["transition"]
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE adh_knowledge_update_proposals "
            "SET status = %s, applied_ref = %s, decided_by = %s, decided_at = NOW(), "
            "    decided_note = %s, decision_version = decision_version + 1 "
            "WHERE id = %s AND status = 'pending' AND decision_version = %s",
            (status, applied_ref, _clean(row.get("_actor") or "", 64), _clean(note, 500),
             int(row["id"]), int(expected_version)))
        return int(cur.rowcount or 0)


# ── 领域写入（P1-5：只经领域服务，不直写业务表）────────────────────────────

def _resolve_target_ref(ctx: queries.EvolutionContext, candidate: dict,
                        target_type: str, target: str) -> str:
    """把收编目标业务名（或 key）解析为规范引用；查不到回可选清单，不猜。"""
    options = queries.target_options(
        ctx, target_type=target_type,
        datasource_name=str(candidate.get("datasource_name") or ""))
    hits = [o for o in options
            if str(o.get("key") or "") == target or str(o.get("display_name") or "") == target]
    if not hits:
        names = [str(o.get("display_name") or o.get("key")) for o in options][:20]
        raise EvolutionDecisionError(
            f"收编目标 {target!r} 不在可选清单里（只列在用口径与业务对象）。"
            f"可选：{names or '（空；object 类需候选带数据源归属）'}", code="TARGET_NOT_FOUND")
    keys = {str(o.get("key") or "") for o in hits}
    if len(keys) > 1:
        raise EvolutionDecisionError(
            f"业务名 {target!r} 对应多个目标（{sorted(keys)}），请用更精确的名称",
            status_code=409, code="TARGET_AMBIGUOUS")
    return keys.pop()


def _resolve_object_key(ctx: queries.EvolutionContext, candidate: dict, want: str) -> dict:
    ds_name = str(candidate.get("datasource_name") or "")
    if not ds_name:
        raise EvolutionDecisionError(
            "该候选没有数据源归属，无法选择归属对象；请用 datasource 指定数据源业务名")
    options = queries.object_options(ctx, ds_name)
    names = [str(o.get("display_name") or o.get("key")) for o in options][:20]
    hits = [o for o in options
            if str(o.get("key") or "") == want or str(o.get("display_name") or "") == want]
    if not hits:
        raise EvolutionDecisionError(
            f"归属对象 {want!r} 不在清单里。可选：{names or '（该数据源暂无可用对象）'}",
            code="OBJECT_NOT_FOUND")
    keys = {str(o.get("key") or "") for o in hits}
    if len(keys) > 1:
        raise EvolutionDecisionError(
            f"业务名 {want!r} 匹配到多个对象（{sorted(keys)}），请用对象 key 精确指定",
            status_code=409, code="OBJECT_AMBIGUOUS")
    return hits[0]


def _resolve_property_name(ctx: queries.EvolutionContext, candidate: dict,
                           object_key: str, want: str) -> str:
    options = queries.property_options(
        ctx, bound_object_key=object_key,
        datasource_name=str(candidate.get("datasource_name") or ""))
    labels = [str(p.get("property") or "") for p in options][:20]
    hits = [p for p in options if str(p.get("property") or "") == want]
    if not hits:
        raise EvolutionDecisionError(
            f"对象属性 {want!r} 不在清单里（只认业务属性名）。可选：{labels or '（空）'}",
            code="PROPERTY_NOT_FOUND")
    return str(hits[0]["property"])


def _domain_adopt(ctx: queries.EvolutionContext, conn, row: dict, data: dict,
                  actor: str, atomic: bool) -> dict:
    """别名收编：字典行走 metrics_service.update_*（同事务），对象行走
    ontology_service.merge_object_scalars（只落草案，跨服务）。"""
    from backend.modules.catalog.services import metrics_service

    target_type = data["target_type"]
    term = str(row.get("term") or "")
    ref = _resolve_target_ref(ctx, row, target_type, data["target"])
    connection = conn if atomic else None

    if target_type == "object":
        object_hit = _resolve_object_key(ctx, row, ref)
        from backend.modules.catalog.services import ontology_service
        try:
            merge = ontology_service.merge_object_scalars(
                int(object_hit.get("model_id") or 0), str(object_hit.get("key") or ref),
                {"aliases": [term]}, created_by=actor)
        except ValueError as exc:
            raise EvolutionDecisionError(str(exc)) from exc
        return {"domain_kind": "object_alias", "domain_ref": str(object_hit.get("key") or ref),
                "message": ("已把该词收编为对象别名（只落草案，发布仍需 "
                            "ontology:publish 独立动作）"),
                "changed_fields": list((merge or {}).get("changed_fields") or [])}

    table_row_id = _dict_row_id(ctx, target_type, ref, row, connection)
    current = _dict_aliases(ctx, target_type, table_row_id, row, connection)
    merged = list(current)
    if term and term not in merged:
        merged.append(term)
    updater = (metrics_service.update_metric if target_type == "metric"
               else metrics_service.update_dimension)
    ok = updater(table_row_id, {"aliases": merged}, connection=connection)
    if not ok:
        raise EvolutionDecisionError(f"目标字典行 {ref!r} 更新失败（行可能已被删除）；"
                                     "请改用「建草稿」出口", code="TARGET_MISSING")
    ds_id = ctx.datasource_id_map.get(str(row.get("datasource_name") or ""), 0)
    return {"domain_kind": f"{target_type}_alias", "domain_ref": ref,
            "message": f"已把该词收编为目标「{ref}」的别名",
            "post_commit": [lambda: metrics_service._sync_kb_after_dict_write(ds_id)]}


def _dict_row_id(ctx: queries.EvolutionContext, target_type: str, ref: str,
                 row: dict, connection) -> int:
    table = "adh_metrics" if target_type == "metric" else "adh_dimensions"
    # 作用域：本源 + 全局（与 target_options 同口径，跨源字典行不得被收编）
    ds_name = str(row.get("datasource_name") or "")
    ds_id = ctx.datasource_id_map.get(ds_name, 0) if ds_name else 0
    sql = f"SELECT id FROM {table} WHERE is_active = 1 AND name = %s"
    params: list = [ref]
    if ds_id:
        sql += " AND (datasource_id = %s OR datasource_id = 0)"
        params.append(ds_id)
    sql += " ORDER BY id ASC LIMIT 2"
    rows = _run(connection or _conn(), sql, tuple(params), close=connection is None)
    if not rows:
        nxt = "new_metric" if target_type == "metric" else "new_dimension"
        raise EvolutionDecisionError(
            f"目标字典行 {ref!r} 不存在，无法只加别名；请改用「建{nxt}草稿」出口",
            code="TARGET_MISSING", domain_ref="")
    if len(rows) > 1:
        raise EvolutionDecisionError(
            f"目标字典行 {ref!r} 匹配到多条（全局与本源各一行），请用更精确的名称",
            status_code=409, code="TARGET_AMBIGUOUS")
    return int(rows[0]["id"])


def _dict_aliases(ctx: queries.EvolutionContext, target_type: str, row_id: int,
                  row: dict, connection) -> list:
    table = "adh_metrics" if target_type == "metric" else "adh_dimensions"
    rows = _run(connection or _conn(), f"SELECT aliases FROM {table} WHERE id = %s",
                (int(row_id),), close=connection is None)
    raw = (rows[0].get("aliases") if rows else "") or ""
    if isinstance(raw, list):
        return [str(a).strip() for a in raw if str(a).strip()]
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        parsed = []
    return [str(a).strip() for a in parsed if str(a).strip()]


def _run(conn, sql: str, params: tuple, *, close: bool) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        if close:
            conn.close()


def _domain_to_term(ctx: queries.EvolutionContext, conn, row: dict, data: dict,
                    actor: str, atomic: bool) -> dict:
    """转术语：term_service.create_term（数据源分桶、缓存失效同源，禁止裸 INSERT）。"""
    from backend.modules.catalog.services import term_service as term_svc

    term = str(row.get("term") or "")
    ds_name = str(row.get("datasource_name") or "")
    ds_id = ctx.datasource_id_map.get(ds_name, 0) if ds_name else 0
    if ds_name and not ds_id:
        raise EvolutionDecisionError(f"数据源 {ds_name!r} 不在授权清单内，无法按源分桶落术语",
                                     code="DATASOURCE_FORBIDDEN")
    try:
        result = term_svc.TermService.create_term(
            {"term_cn": term, "datasource_id": ds_id,
             "description": data.get("description")
             or f"自进化候选「{term}」转入的术语缓冲区条目"},
            connection=conn if atomic else None, return_existing=True)
    except ValueError as exc:
        raise EvolutionDecisionError(str(exc)) from exc
    return {"domain_kind": "term",
            "domain_ref": term,
            "created": bool((result or {}).get("created")),
            "message": "已按数据源分桶转入业务术语缓冲区；映射到对象/字典后由归纳作业复核",
            "post_commit": [lambda: term_svc._clear_term_cache(ds_id)]}


def _domain_metric_draft(ctx: queries.EvolutionContext, conn, row: dict, data: dict,
                         actor: str, atomic: bool) -> dict:
    """指标草稿：metrics_service.create_metric，is_active=0（未启用不进语义层广告）。"""
    import re

    from backend.modules.catalog.services import metrics_service

    term = str(row.get("term") or "")
    ds_name = str(row.get("datasource_name") or "")
    obj = _resolve_object_key(ctx, row, data["object"])
    biz = data.get("display_name") or term
    payload = {"name": _clean(re.sub(r"[^0-9A-Za-z_]", "_", biz), 64) or biz,
               "display_name": biz,
               "bound_object_key": str(obj.get("key") or ""),
               "datasource_id": ctx.datasource_id_map.get(ds_name, 0) if ds_name else 0,
               "calculation_type": data.get("agg_type") or "COUNT",
               "formula": data.get("formula") or "",
               "unit": data.get("unit") or "",
               "description": data.get("description")
               or "自进化候选归纳生成的指标草稿（待补口径后启用）",
               "aliases": [term] if term != biz else [],
               "is_active": 0, "workspace_id": 0}
    try:
        metrics_service.create_metric(payload)
    except ValueError as exc:
        raise EvolutionDecisionError(str(exc)) from exc
    needs = [] if payload["formula"] else ["formula"]
    message = ("指标草稿已创建（未启用）。补齐口径后在指标中心启用，"
               "否则语义层不会广告该指标。") if payload["formula"] else (
        "指标草稿已创建（未启用）但**缺计算公式**，planner 无法编译它；"
        "请在指标中心补 formula 后再启用。")
    return {"domain_kind": "metric_draft", "domain_ref": biz,
            "is_active": 0, "needs_completion": needs, "message": message}


def _domain_dimension_draft(ctx: queries.EvolutionContext, conn, row: dict, data: dict,
                            actor: str, atomic: bool) -> dict:
    """维度草稿：业务属性名经服务端映射物理列（人/AI 都不接触物理列名）。"""
    from backend.modules.catalog.services import evolution_service as evo
    from backend.modules.catalog.services import metrics_service

    term = str(row.get("term") or "")
    ds_name = str(row.get("datasource_name") or "")
    obj = _resolve_object_key(ctx, row, data["object"])
    property_name = _resolve_property_name(ctx, row, str(obj.get("key") or ""), data["property"])
    biz = data.get("display_name") or term
    col = evo.resolve_property_column(bound_object_key=str(obj.get("key") or ""),
                                      datasource_name=ds_name, property_name=property_name)
    payload = {"name": biz, "display_name": biz,
               "name_en": _clean(term, 64),
               "bound_object_key": str(obj.get("key") or ""),
               "target_table": col["target_table"], "target_column": col["target_column"],
               "datasource_id": col["datasource_id"],
               "category": data.get("category") or "属性",
               "description": data.get("description") or "自进化候选归纳生成的维度草稿",
               "aliases": [term] if term != biz else [],
               "is_active": 0, "workspace_id": 0}
    try:
        metrics_service.create_dimension(payload)
    except ValueError as exc:
        raise EvolutionDecisionError(str(exc)) from exc
    # target_column 是物理列名：不外露（护栏 §7），只回业务属性名
    return {"domain_kind": "dimension_draft", "domain_ref": biz,
            "property": property_name, "is_active": 0,
            "needs_completion": ["业务属性复核"],
            "message": "维度草稿已创建（未启用）并绑定对象业务属性；"
                       "请在维度中心复核口径后启用，未启用前语义层不会广告该维度。"}


def _domain_reject(ctx: queries.EvolutionContext, conn, row: dict, data: dict,
                   actor: str, atomic: bool) -> dict:
    """驳回：无领域写入，只做状态转换与留痕。"""
    return {"domain_kind": "", "domain_ref": "",
            "message": "已驳回并留痕（不删行）；若认为驳回错了，需在下一条新证据里重新提出"}


_DOMAIN_WRITERS = {
    "evolution_candidate.adopt": _domain_adopt,
    "evolution_candidate.reject": _domain_reject,
    "evolution_candidate.to_term": _domain_to_term,
    "evolution_metric.create": _domain_metric_draft,
    "evolution_dimension.create": _domain_dimension_draft,
}


# ── 决策入口 ────────────────────────────────────────────────────────────────

def _candidate_result(row: dict, action_key: str, domain: dict,
                      expected_version: int, *, replayed: bool = False) -> dict:
    spec = CANDIDATE_ACTIONS[action_key]
    out = {"changed": True, "action_key": action_key,
           "subject_type": "candidate",
           "term": str(row.get("term") or ""),
           "kind": str(row.get("kind") or ""),
           "datasource": str(row.get("datasource_name") or ""),
           "status": spec["transition"],
           "decision_version": int(expected_version) + 1,
           "domain_kind": domain.get("domain_kind") or spec["converted_kind"],
           "domain_ref": domain.get("domain_ref") or "",
           "message": domain.get("message") or ""}
    for key in ("needs_completion", "created", "is_active", "property", "changed_fields"):
        if key in domain:
            out[key] = domain[key]
    if replayed:
        out["replayed"] = True
    return out


def decide_candidate(ctx: queries.EvolutionContext, *, candidate_id: int, action_key: str,
                     expected_version: int, note: str = "", input_data: dict | None = None,
                     idempotency_key: str = "") -> dict:
    """候选处置（HTTP/AI 共用）：幂等 → 双闸 → 版本仲裁 → 领域写入 → 条件更新 → 账本。"""
    if action_key not in CANDIDATE_ACTIONS:
        raise EvolutionDecisionError(
            f"候选不支持动作 {action_key!r}，应为 {sorted(CANDIDATE_ACTIONS)}",
            status_code=400, code="ACTION_NOT_FOUND")
    data = _validate_candidate_input(action_key, note, dict(input_data or {}))
    key = _require_idem_key(idempotency_key)
    require_action_perm(ctx, action_key)
    try:
        if _is_cross_service(action_key, data):
            result = _decide_candidate_cross(ctx, candidate_id, action_key, expected_version,
                                             note, data, key)
        else:
            result = _decide_candidate_atomic(ctx, candidate_id, action_key, expected_version,
                                              note, data, key)
    except EvolutionDecisionError as exc:
        # 收编目标缺行不是失败，是「该走另一个出口」：回指引而不做状态转换
        # （旧 approve_suggestion 在这条路上把 8/9 条 pending 卡死）。
        if action_key == "evolution_candidate.adopt" and exc.code == "TARGET_MISSING":
            nxt = "new_metric" if data.get("target_type") == "metric" else "new_dimension"
            return {"changed": False, "reason": "target_missing", "next_action": nxt,
                    "message": str(exc)}
        raise
    _audit(ctx, action_key, "candidate", candidate_id, result)
    return result


def decide_candidate_by_name(ctx: queries.EvolutionContext, *, term: str,
                             datasource_name: str = "", kind: str = "",
                             action_key: str, expected_version: int = 0, note: str = "",
                             input_data: dict | None = None,
                             idempotency_key: str = "") -> dict:
    """AI 通道：不收内部 id，按业务名解析当前版本后走同一决策链。

    顺序与 HTTP 同口径：先本地校验与双闸（失败不触达库），再解析业务名。"""
    if action_key not in CANDIDATE_ACTIONS:
        raise EvolutionDecisionError(
            f"候选不支持动作 {action_key!r}，应为 {sorted(CANDIDATE_ACTIONS)}",
            status_code=400, code="ACTION_NOT_FOUND")
    _validate_candidate_input(action_key, note, dict(input_data or {}))
    require_action_perm(ctx, action_key)
    try:
        cand = queries.resolve_candidate(ctx, term=term, datasource_name=datasource_name,
                                         kind=kind, status="pending")
    except queries.EvolutionQueryError as exc:
        raise EvolutionDecisionError(str(exc), status_code=exc.status_code,
                                     code=exc.code) from exc
    version = int(expected_version or cand.get("expected_version") or 1)
    key = idempotency_key or f"{term}:{version}:{action_key}"
    return decide_candidate(ctx, candidate_id=int(cand["id"]), action_key=action_key,
                            expected_version=version, note=note, input_data=input_data,
                            idempotency_key=key)


def _require_idem_key(idempotency_key: str) -> str:
    key = _clean(idempotency_key, 191)
    if not key:
        raise EvolutionDecisionError(
            "决策请求必须带 Idempotency-Key（UI 用 subject:version:action，AI 用工具调用键）",
            status_code=400, code="IDEMPOTENCY_KEY_REQUIRED")
    return key


def _decide_candidate_atomic(ctx: queries.EvolutionContext, candidate_id: int,
                             action_key: str, expected_version: int, note: str,
                             data: dict, key: str) -> dict:
    """同事务路径（字典别名/术语/驳回等同库写入）：锁内校验、领域写入、条件更新、账本。"""
    req_hash = request_hash(action_key, expected_version, note, data)
    conn = _conn()
    try:
        conn.begin()
        row = queries.candidate_row_for_decision(conn, ctx, candidate_id, for_update=True)
        if not row:
            conn.rollback()
            raise EvolutionDecisionError("候选不存在或不在当前角色域内",
                                         status_code=404, code="CANDIDATE_NOT_FOUND")
        existing = _ledger_get(conn, "candidate", candidate_id, key)
        if existing:
            conn.rollback()
            return _replay(existing, req_hash)
        if str(row.get("status") or "") != "pending" or \
                int(row.get("decision_version") or 1) != int(expected_version):
            _ledger_put(conn, subject_type="candidate", subject_id=candidate_id,
                        action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                        expected_version=expected_version, result_status="conflict",
                        error_code="STALE_DECISION",
                        result={"message": _candidate_conflict_message(row)})
            conn.commit()
            raise EvolutionDecisionError(_candidate_conflict_message(row),
                                         status_code=409, code="STALE_DECISION")
        row["_actor"] = ctx.actor
        try:
            domain = _DOMAIN_WRITERS[action_key](ctx, conn, row, data, ctx.actor, True)
        except queries.EvolutionQueryError as exc:
            conn.rollback()
            raise EvolutionDecisionError(str(exc), status_code=exc.status_code,
                                         code=exc.code) from exc
        except EvolutionDecisionError:
            conn.rollback()
            raise
        except ValueError as exc:
            conn.rollback()
            raise EvolutionDecisionError(str(exc)) from exc
        affected = _transition_candidate(conn, row, action_key, expected_version, note, domain)
        if not affected:
            _ledger_put(conn, subject_type="candidate", subject_id=candidate_id,
                        action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                        expected_version=expected_version, result_status="conflict",
                        error_code="DOMAIN_WRITTEN_DECISION_CONFLICT",
                        domain_ref=domain.get("domain_ref") or "",
                        result={"message": "领域写入已生效但候选状态被并发修改，请人工核对",
                                "domain_ref": domain.get("domain_ref") or ""})
            conn.commit()
            raise EvolutionDecisionError(
                f"领域写入已成功（落点：{domain.get('domain_ref') or '-'}）但候选状态被并发修改，"
                "本次候选未转换；请人工核对落点后重试", status_code=409,
                code="DOMAIN_WRITTEN_DECISION_CONFLICT",
                domain_ref=str(domain.get("domain_ref") or ""))
        result = _candidate_result(row, action_key, domain, expected_version)
        _ledger_put(conn, subject_type="candidate", subject_id=candidate_id,
                    action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                    expected_version=expected_version, result_status="succeeded",
                    domain_ref=domain.get("domain_ref") or "", result=result)
        conn.commit()
        _post_commit(domain)
        return result
    except EvolutionDecisionError:
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        logger.exception("[evolution-decision] 候选决策失败 candidate=%s", candidate_id)
        raise EvolutionDecisionError(f"决策未完成：{type(exc).__name__}",
                                     status_code=500, code="EXECUTION_FAILED") from exc
    finally:
        conn.close()


def _decide_candidate_cross(ctx: queries.EvolutionContext, candidate_id: int,
                            action_key: str, expected_version: int, note: str,
                            data: dict, key: str) -> dict:
    """跨服务路径（指标/维度草稿、对象别名）：领域写入先行，状态转换用条件更新协调；
    领域写成功但状态冲突 → 显式 DOMAIN_WRITTEN_DECISION_CONFLICT + 落点。"""
    req_hash = request_hash(action_key, expected_version, note, data)
    # 阶段一：无锁预检（幂等 + 版本），把明显冲突挡在领域写入之前
    conn = _conn()
    try:
        row = queries.candidate_row_for_decision(conn, ctx, candidate_id, for_update=False)
    finally:
        conn.close()
    if not row:
        raise EvolutionDecisionError("候选不存在或不在当前角色域内",
                                     status_code=404, code="CANDIDATE_NOT_FOUND")
    conn = _conn()
    try:
        existing = _ledger_get(conn, "candidate", candidate_id, key)
    finally:
        conn.close()
    if existing:
        return _replay(existing, req_hash)
    if str(row.get("status") or "") != "pending" or \
            int(row.get("decision_version") or 1) != int(expected_version):
        _record_conflict(ctx, "candidate", candidate_id, action_key, key, req_hash,
                         expected_version, "STALE_DECISION", "",
                         _candidate_conflict_message(row))
        raise EvolutionDecisionError(_candidate_conflict_message(row),
                                     status_code=409, code="STALE_DECISION")

    # 阶段二：领域写入（各自服务事务；只落草案/草稿，不发布）
    row["_actor"] = ctx.actor
    try:
        domain = _DOMAIN_WRITERS[action_key](ctx, None, row, data, ctx.actor, False)
    except queries.EvolutionQueryError as exc:
        raise EvolutionDecisionError(str(exc), status_code=exc.status_code,
                                     code=exc.code) from exc
    except EvolutionDecisionError:
        raise
    except ValueError as exc:
        raise EvolutionDecisionError(str(exc)) from exc

    # 阶段三：锁内条件更新 + 账本
    conn = _conn()
    try:
        conn.begin()
        locked = queries.candidate_row_for_decision(conn, ctx, candidate_id, for_update=True)
        existing = _ledger_get(conn, "candidate", candidate_id, key)
        if existing:
            conn.rollback()
            return _replay(existing, req_hash)
        if not locked or str(locked.get("status") or "") != "pending" or \
                int(locked.get("decision_version") or 1) != int(expected_version):
            message = (f"领域写入已成功（落点：{domain.get('domain_ref') or '-'}）"
                       "但候选状态被并发修改，本次候选未转换；请人工核对落点后重试")
            _ledger_put(conn, subject_type="candidate", subject_id=candidate_id,
                        action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                        expected_version=expected_version, result_status="conflict",
                        error_code="DOMAIN_WRITTEN_DECISION_CONFLICT",
                        domain_ref=domain.get("domain_ref") or "",
                        result={"message": message,
                                "domain_ref": domain.get("domain_ref") or ""})
            conn.commit()
            raise EvolutionDecisionError(message, status_code=409,
                                         code="DOMAIN_WRITTEN_DECISION_CONFLICT",
                                         domain_ref=str(domain.get("domain_ref") or ""))
        locked["_actor"] = ctx.actor
        affected = _transition_candidate(conn, locked, action_key, expected_version,
                                         note, domain)
        if not affected:
            message = (f"领域写入已成功（落点：{domain.get('domain_ref') or '-'}）"
                       "但候选状态被并发修改，本次候选未转换；请人工核对落点后重试")
            _ledger_put(conn, subject_type="candidate", subject_id=candidate_id,
                        action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                        expected_version=expected_version, result_status="conflict",
                        error_code="DOMAIN_WRITTEN_DECISION_CONFLICT",
                        domain_ref=domain.get("domain_ref") or "",
                        result={"message": message,
                                "domain_ref": domain.get("domain_ref") or ""})
            conn.commit()
            raise EvolutionDecisionError(message, status_code=409,
                                         code="DOMAIN_WRITTEN_DECISION_CONFLICT",
                                         domain_ref=str(domain.get("domain_ref") or ""))
        result = _candidate_result(locked, action_key, domain, expected_version)
        _ledger_put(conn, subject_type="candidate", subject_id=candidate_id,
                    action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                    expected_version=expected_version, result_status="succeeded",
                    domain_ref=domain.get("domain_ref") or "", result=result)
        conn.commit()
        _post_commit(domain)
        return result
    except EvolutionDecisionError:
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        logger.exception("[evolution-decision] 候选决策（跨服务）失败 candidate=%s", candidate_id)
        raise EvolutionDecisionError(f"决策未完成：{type(exc).__name__}",
                                     status_code=500, code="EXECUTION_FAILED") from exc
    finally:
        conn.close()


def _record_conflict(ctx: queries.EvolutionContext, subject_type: str, subject_id: int,
                     action_key: str, key: str, req_hash: str, expected_version: int,
                     error_code: str, domain_ref: str, message: str) -> None:
    conn = _conn()
    try:
        conn.begin()
        _ledger_put(conn, subject_type=subject_type, subject_id=subject_id,
                    action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                    expected_version=expected_version, result_status="conflict",
                    error_code=error_code, domain_ref=domain_ref,
                    result={"message": message, "domain_ref": domain_ref})
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — 账本旁路失败不掩盖主错误
        _rollback(conn)
        logger.warning("[evolution-decision] 冲突账本写入失败: %s", exc)
    finally:
        conn.close()


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass


def _post_commit(domain: dict) -> None:
    """提交后的派生刷新（KB 同步/缓存失效）：旁路失败只告警，不把已提交的决策打成错误。"""
    for hook in (domain.get("post_commit") or []):
        try:
            hook()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[evolution-decision] 提交后派生刷新失败（决策已生效）: %s", exc)


def _audit(ctx: queries.EvolutionContext, action_key: str, subject_type: str,
           subject_id: int, result: dict) -> None:
    """决策审计（best-effort）：账本是权威，这里补 adh_audit_logs 流水；重放不重复记。"""
    if result.get("replayed"):
        return
    try:
        from backend.common.auth import log_audit
        detail = (f"{result.get('term') or result.get('title') or ''}"
                  f"｜{action_key}｜落点：{result.get('domain_ref') or '-'}"
                  f"｜{result.get('status') or ''}")
        log_audit(int(ctx.user_id), str(ctx.actor), action_key,
                  target_type=f"evolution_{subject_type}", target_id=int(subject_id or 0),
                  detail=detail[:500], module="catalog")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[evolution-decision] 审计写入失败（不影响主链路）: %s", exc)


# ── 提议决策 ────────────────────────────────────────────────────────────────

def decide_proposal(ctx: queries.EvolutionContext, *, proposal_id: int, action_key: str,
                    expected_version: int, note: str = "", applied_ref: str = "",
                    idempotency_key: str = "") -> dict:
    """提议处置：applied 必须带非空落点、reject 必须带理由（HTTP/AI 同口径再校验）。"""
    if action_key not in PROPOSAL_ACTIONS:
        raise EvolutionDecisionError(
            f"提议不支持动作 {action_key!r}，应为 {sorted(PROPOSAL_ACTIONS)}",
            status_code=400, code="ACTION_NOT_FOUND")
    note, applied_ref = _validate_proposal_input(action_key, note, applied_ref)
    key = _require_idem_key(idempotency_key)
    require_action_perm(ctx, action_key)
    result = _decide_proposal_tx(ctx, proposal_id, action_key, expected_version,
                                 note, applied_ref, key)
    _audit(ctx, action_key, "proposal", proposal_id, result)
    return result


def _decide_proposal_tx(ctx: queries.EvolutionContext, proposal_id: int, action_key: str,
                        expected_version: int, note: str, applied_ref: str,
                        key: str) -> dict:
    req_hash = request_hash(action_key, expected_version, note,
                            {"applied_ref": applied_ref})
    conn = _conn()
    try:
        conn.begin()
        row = queries.proposal_row_for_decision(conn, ctx, proposal_id, for_update=True)
        if not row:
            conn.rollback()
            raise EvolutionDecisionError("提议不存在或不在当前角色域内",
                                         status_code=404, code="PROPOSAL_NOT_FOUND")
        existing = _ledger_get(conn, "proposal", proposal_id, key)
        if existing:
            conn.rollback()
            return _replay(existing, req_hash)
        if str(row.get("status") or "") != "pending" or \
                int(row.get("decision_version") or 1) != int(expected_version):
            message = _proposal_conflict_message(row)
            _ledger_put(conn, subject_type="proposal", subject_id=proposal_id,
                        action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                        expected_version=expected_version, result_status="conflict",
                        error_code="STALE_DECISION", result={"message": message})
            conn.commit()
            raise EvolutionDecisionError(message, status_code=409, code="STALE_DECISION")
        row["_actor"] = ctx.actor
        affected = _transition_proposal(conn, row, action_key, expected_version,
                                        note, applied_ref)
        if not affected:
            message = "提议状态已被并发决策改变，本次未生效；请刷新核对"
            _ledger_put(conn, subject_type="proposal", subject_id=proposal_id,
                        action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                        expected_version=expected_version, result_status="conflict",
                        error_code="STALE_DECISION", result={"message": message})
            conn.commit()
            raise EvolutionDecisionError(message, status_code=409, code="STALE_DECISION")
        result = {"changed": True, "action_key": action_key, "subject_type": "proposal",
                  "title": str(row.get("title") or ""),
                  "target_type": str(row.get("target_type") or ""),
                  "status": PROPOSAL_ACTIONS[action_key]["transition"],
                  "decision_version": int(expected_version) + 1,
                  "domain_ref": applied_ref,
                  "message": ("已标记为已处理（落点已留痕，不删行）" if applied_ref
                              else "已驳回并留痕（不删行）")}
        _ledger_put(conn, subject_type="proposal", subject_id=proposal_id,
                    action_key=action_key, ctx=ctx, idem_key=key, req_hash=req_hash,
                    expected_version=expected_version, result_status="succeeded",
                    domain_ref=applied_ref, result=result)
        conn.commit()
        return result
    except EvolutionDecisionError:
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        logger.exception("[evolution-decision] 提议决策失败 proposal=%s", proposal_id)
        raise EvolutionDecisionError(f"决策未完成：{type(exc).__name__}",
                                     status_code=500, code="EXECUTION_FAILED") from exc
    finally:
        conn.close()


def _proposal_conflict_message(row: dict) -> str:
    status = str(row.get("status") or "")
    if status != "pending":
        return (f"提议当前状态是 {status!r}，只有待处理（pending）提议可以处置；"
                "既有结论不被新请求覆盖，请刷新后查看")
    return (f"提议已被并发决策修改（当前版本 {int(row.get('decision_version') or 1)}），"
            "本次提交未生效；请刷新获取最新版本后重试")


def decide_proposal_by_name(ctx: queries.EvolutionContext, *, title: str,
                            target_type: str = "", action_key: str,
                            note: str = "", applied_ref: str = "",
                            idempotency_key: str = "") -> dict:
    """AI 通道：按提议标题解析当前版本后走同一决策链（不收内部 id）。

    顺序与 HTTP 同口径：先本地校验与双闸（失败不触达库），再解析标题。"""
    if action_key not in PROPOSAL_ACTIONS:
        raise EvolutionDecisionError(
            f"提议不支持动作 {action_key!r}，应为 {sorted(PROPOSAL_ACTIONS)}",
            status_code=400, code="ACTION_NOT_FOUND")
    _validate_proposal_input(action_key, note, applied_ref)
    require_action_perm(ctx, action_key)
    try:
        proposal = queries.resolve_proposal(ctx, title=title, target_type=target_type,
                                            status="pending")
    except queries.EvolutionQueryError as exc:
        raise EvolutionDecisionError(str(exc), status_code=exc.status_code,
                                     code=exc.code) from exc
    version = int(proposal.get("expected_version") or 1)
    key = idempotency_key or f"{title}:{version}:{action_key}"
    return decide_proposal(ctx, proposal_id=int(proposal["id"]), action_key=action_key,
                           expected_version=version, note=note,
                           applied_ref=applied_ref, idempotency_key=key)


# ── 异步归纳作业（POST 创建 job，状态可查询；失败显式可见）──────────────────

def submit_digest_job(ctx: queries.EvolutionContext, *, run_inline: bool = False) -> dict:
    """提交归纳作业：写 job 行后异步执行；``run_inline`` 供 AI 工具同步等结果。

    归纳失败必须显式落 ``failed`` + 可读原因："没有新待办"与"作业挂了"是两件事
    （no-silent-degradation §1）。
    """
    _require_digest_perm(ctx)
    conn = _conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_evolution_digest_jobs (workspace_id, requested_by, actor, "
                "status) VALUES (%s, %s, %s, 'queued')",
                (int(ctx.workspace_id), int(ctx.user_id), _clean(ctx.actor, 64)))
            job_id = int(cur.lastrowid or 0)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        logger.exception("[evolution-decision] 归纳作业创建失败")
        raise EvolutionDecisionError(f"归纳作业创建失败：{type(exc).__name__}",
                                     status_code=500, code="EXECUTION_FAILED") from exc
    finally:
        conn.close()
    if run_inline:
        return run_digest_job(job_id)
    worker = threading.Thread(target=_run_digest_job_quiet, args=(job_id,), daemon=True)
    worker.start()
    return {"job_id": job_id, "status": "queued",
            "message": "归纳作业已提交；请轮询作业状态，失败原因见 error_message"}


def _require_digest_perm(ctx: queries.EvolutionContext) -> None:
    from backend.core.perm_link import require_write_perm

    try:
        require_write_perm(int(ctx.user_id), int(ctx.workspace_id),
                           "evolution:manage", "执行进化归纳")
    except PermissionError as exc:
        raise EvolutionDecisionError(str(exc), status_code=403,
                                     code="PERMISSION_DENIED") from exc


def _run_digest_job_quiet(job_id: int) -> None:
    try:
        run_digest_job(job_id)
    except Exception as exc:  # noqa: BLE001 — 后台线程必须吞掉异常但已落库失败态
        logger.warning("[evolution-decision] 归纳作业线程异常 job=%s: %s", job_id, exc)


def run_digest_job(job_id: int) -> dict:
    """执行归纳作业并落状态（queued → running → succeeded/failed）。"""
    from backend.modules.catalog.services import evolution_service as evo

    _set_job_status(job_id, "running", started=True)
    try:
        out = evo.run_digest()
    except Exception as exc:  # noqa: BLE001 — 失败显式落库，不吞
        logger.exception("[evolution-decision] 归纳作业失败 job=%s", job_id)
        message = f"{type(exc).__name__}: {exc}"[:500]
        _set_job_status(job_id, "failed", error_code="DIGEST_FAILED", error_message=message)
        return {"job_id": int(job_id), "status": "failed",
                "error_code": "DIGEST_FAILED", "error_message": message}
    result = {"summary": str(out.get("summary") or "")[:500],
              "pending_by_kind": out.get("pending_by_kind") or {},
              "feedback": out.get("feedback") or {},
              "recheck": out.get("recheck") or {},
              "verification": out.get("verification") or {}}
    _set_job_status(job_id, "succeeded", result=result)
    return {"job_id": int(job_id), "status": "succeeded", "result": result}


def _set_job_status(job_id: int, status: str, *, started: bool = False,
                    error_code: str = "", error_message: str = "",
                    result: dict | None = None) -> None:
    conn = _conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            if started:
                cur.execute(
                    "UPDATE adh_evolution_digest_jobs SET status = %s, started_at = NOW() "
                    "WHERE id = %s AND status = 'queued'", (status, int(job_id)))
            elif status == "failed":
                cur.execute(
                    "UPDATE adh_evolution_digest_jobs "
                    "SET status = 'failed', finished_at = NOW(), error_code = %s, "
                    "    error_message = %s WHERE id = %s AND status = 'running'",
                    (_clean(error_code, 64), _clean(error_message, 500), int(job_id)))
            else:
                cur.execute(
                    "UPDATE adh_evolution_digest_jobs "
                    "SET status = %s, finished_at = NOW(), result_json = %s "
                    "WHERE id = %s AND status = 'running'",
                    (status, json.dumps(result or {}, ensure_ascii=False,
                                        default=_json_default), int(job_id)))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        logger.warning("[evolution-decision] 归纳作业状态写入失败 job=%s: %s", job_id, exc)
    finally:
        conn.close()
