"""自进化闭环 API：候选看板 / 四出口审核 / 归纳作业手动触发 / 反馈大盘。

权限口径（不另造第二套判断）
--------------------------
* 读：``get_current_user`` + api_permission 中间件按 ``evolution:read`` 门控；
* 写（收编别名/建草稿/转术语/转用例/驳回/立即归纳）：``require_admin`` 且写动作
  统一经 ``perm_link.require_write_perm`` 按功能权限码把关，``decided_by`` **由服务端
  从可信身份注入**，绝不从请求体取（护栏 §2 身份 fail-closed）；
* 评测出口复用 ``eval:manage``：进化候选转成评测用例，用的还是评测那套权限；
  ``eval:*`` 仍为 ``ai_access='none'``——评价 AI 的标尺不能由 AI 自己读写。

Chat 侧审批（双闸，用户 2026-11-21 拍板）
------------------------------------
``function_tools`` 里的进化中心动作（先列待办再逐条处置）**不是一把新尺**：
* 能力面：``evolution:read``（看待办）/ ``evolution:manage``（执行处置）——
  两者的 ``ai_access`` 由 none 抬到 read/write（``docker/mysql/evolution_ai_review_migration.sql``）；
* 动作面：仍按上面那张「出口→权限码」表逐次裁决（handler 内 ``require_write_perm``），
  与 HTTP 端点同一把尺、不扩权；两道闸都过才执行（fail-closed）。
  AI 传的只有业务名（候选词/对象/属性/提议标题/数据源），``decided_by`` 服务端注入。

出口与权限码映射（as-bot-system-waker §3 的动作→权限码表口径）
----------------------------------------------------------
收编别名 / 转术语      → ``ontology:save``
建指标草稿/建维度草稿  → ``metrics:manage``
转评测用例             → ``eval:manage``
立即执行归纳           → ``evolution:manage``
知识更新提议（审/驳/标记）/ 手工提交 → ``knowledge:manage``
更新本体对象标量       → ``ontology:save``（发布仍是独立动作 ``ontology:publish``）

知识更新提议**只是备忘录**：真正落库走各域既有通道（知识库 ``kbdoc:write``、
场景 ``knowledge:manage``、本体 ``ontology:save`` + 独立 publish），
AI 不直写知识库，也不越权发布本体。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.common.auth import get_current_user, require_admin
from backend.modules.catalog.services import evolution_service as evo

logger = logging.getLogger("api.evolution")
router = APIRouter()


# ── Request models ──────────────────────────────────────────────────────────

class AliasIn(BaseModel):
    target_type: str
    target_ref: str


class MetricDraftIn(BaseModel):
    bound_object_key: str
    display_name: str = ""
    agg_type: str = "COUNT"
    formula: str = ""
    unit: str = ""
    description: str = ""


class DimensionDraftIn(BaseModel):
    bound_object_key: str
    property_name: str          # 业务属性名；物理列由服务端映射，人不接触
    display_name: str = ""
    category: str = "属性"
    description: str = ""


class TermIn(BaseModel):
    description: str = ""


class EvalCaseIn(BaseModel):
    suite: str
    capability: str
    question: str
    payload: dict = {}
    expected: dict = {}
    note: str = ""


class EvalFailureIn(BaseModel):
    """从评测失败就地拉候选（人在页面点，不由归纳作业自动扫）。"""
    suite: str
    case_key: str
    question: str
    capability: str = ""
    datasource_name: str = ""
    term: str = ""
    reason: str = ""


class ProposalIn(BaseModel):
    """知识更新提议（人手工提交；Chat 侧走功能工具同落一张表）。

    只收**业务标识**：target_hint 是知识库名/文档标题/场景标题/对象业务名，
    不收任何内部 id（security-guardrails §7）。
    """
    target_type: str
    title: str = ""
    content: str = ""
    rationale: str = ""
    target_hint: str = ""
    datasource_name: str = ""


class ProposalAppliedIn(BaseModel):
    """人已在目标域落库成功后回写落点标识（kb_name/source_id、场景 id、对象 key）。"""
    applied_ref: str = ""


class ProposalRejectIn(BaseModel):
    note: str = ""


class ObjectScalarsIn(BaseModel):
    """本体对象标量更新（只允许三个字段；资源 id 均来自服务端选择框，不手填）。"""
    model_id: int
    object_key: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    aliases: Optional[list[str]] = None


def _require_perm(user: dict, perm_code: str, action: str) -> None:
    """写动作统一过功能权限码；拒绝原因必须可解释（不静默 403 一句话）。"""
    from backend.core.perm_link import require_write_perm

    try:
        require_write_perm(user["user_id"], 0, perm_code, action)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _actor(user: dict) -> str:
    """审核人只取服务端身份，绝不从请求体读（护栏 §2）。"""
    return str(user.get("username") or user.get("user_id") or "")


def _audit(user: dict, action: str, target_type: str, detail: str,
           target_id: int = 0) -> None:
    """知识更新通道的留痕（best-effort，不影响主链路）。

    提议表自己只记 `decided_by/decided_at`，回答不了「谁在什么时候又想改哪一版」；
    真正落库的那一步走的是各域既有端点（KB 文档在 `platform/kb_docs` 已留痕），
    这里补的是**提议侧**的动作流水，与 `adh_audit_logs` 同一张表、同一口径。
    """
    try:
        from backend.common.auth import log_audit
        log_audit(int(user.get("user_id") or 0), _actor(user), action,
                  target_type=target_type, target_id=target_id, detail=detail,
                  module="catalog")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[evolution] 审计写入失败（不影响主链路）: %s", exc)


# ── 读面 ──────────────────────────────────────────────────────

@router.get("/candidates")
def list_candidates(status: str = Query("pending"), kind: str = Query(""),
                    source: str = Query(""), datasource_name: str = Query(""),
                    limit: int = Query(200, ge=1, le=500),
                    user: dict = Depends(get_current_user)):
    if status and status not in evo.STATUSES:
        raise HTTPException(status_code=400, detail=f"status 无效，应为 {list(evo.STATUSES)}")
    if kind and kind not in evo.KINDS:
        raise HTTPException(status_code=400, detail=f"kind 无效，应为 {list(evo.KINDS)}")
    items = evo.list_candidates(status=status, kind=kind, source=source,
                              datasource_name=datasource_name, limit=limit)
    # 建议只对待处置队列生成：已处置行（converted/verified/stale/rejected）再给
    # "建议"会被误读成"还有事要做"；而且每条建议要重跑一次 classify_term（多次查库），
    # 对历史页纯属白烧。
    if status == "pending":
        for it in items:
            it["suggestion"] = evo.suggest_action(it)
    return {"items": items,
            "kinds": evo.KINDS, "sources": evo.SOURCES, "statuses": evo.STATUSES}


@router.get("/overview")
def overview(user: dict = Depends(get_current_user)):
    """候选分布 + 反馈大盘 + 闭环成效。没有这些就看不见缺口长什么样。"""
    return evo.overview()


@router.get("/datasources")
def list_datasources(user: dict = Depends(get_current_user)):
    """数据源清单（显示 name；出口表单的源选择框数据源，不让人填 id）。"""
    rows = evo._q("SELECT id, name, db_type FROM adh_datasources ORDER BY name")
    return {"items": [{"name": r["name"], "db_type": r.get("db_type") or ""} for r in rows]}


@router.get("/objects")
def list_objects(datasource_name: str = Query(...), user: dict = Depends(get_current_user)):
    """可归属对象清单（业务名显示、key 作值）。"""
    return {"items": evo.object_options(datasource_name)}


@router.get("/properties")
def list_properties(datasource_name: str = Query(...), object_key: str = Query(...),
                    user: dict = Depends(get_current_user)):
    """对象主表上的**业务属性名**清单（维度草稿选列用；物理列不面向人）。"""
    return {"items": evo.property_options(bound_object_key=object_key,
                                         datasource_name=datasource_name)}


@router.get("/targets")
def list_targets(target_type: str = Query(...), datasource_name: str = Query(""),
                 user: dict = Depends(get_current_user)):
    """别名收编目标清单（既有字典行/本体对象）：显示业务名，value 用 name/key。

    收编目标属"本系统已有资源"，只能从清单选（ui-resource-display §2）；
    自由手输字典名会造出"填了不存在的目标"这种假动作。
    """
    try:
        return {"items": evo.target_options(target_type=target_type,
                                            datasource_name=datasource_name)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── 知识更新提议（备忘录：不直写资产）─────────────────────────

@router.get("/proposals")
def list_proposals(status: str = Query(""), target_type: str = Query(""),
                   source: str = Query(""), limit: int = Query(100, ge=1, le=500),
                   user: dict = Depends(get_current_user)):
    """知识更新提议清单（默认全状态，前端按页签筛）。"""
    from backend.modules.catalog.services import knowledge_update_service as kus

    try:
        return kus.list_proposals(status=status, target_type=target_type,
                                  source=source, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/proposals")
def create_proposal(body: ProposalIn, user: dict = Depends(require_admin)):
    """手工提交一条知识更新提议（人自己也能把“应该改什么”写进待办）。"""
    from backend.modules.catalog.services import knowledge_update_service as kus

    _require_perm(user, "knowledge:manage", "提交知识更新提议")
    try:
        result = kus.create_proposal(
            source="manual", target_type=body.target_type, title=body.title,
            content=body.content, rationale=body.rationale,
            target_hint=body.target_hint, datasource_name=body.datasource_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(user, "knowledge_proposal.create", "knowledge_update_proposal",
           f"{body.target_type}: {body.title}", int((result or {}).get("id") or 0))
    return result


@router.post("/proposals/{proposal_id}/applied")
def mark_proposal_applied(proposal_id: int, body: ProposalAppliedIn,
                          user: dict = Depends(require_admin)):
    """人已在目标域落库成功 → 标记已处理（只放行 pending，重复提交不覆盖结论）。"""
    from backend.modules.catalog.services import knowledge_update_service as kus

    _require_perm(user, "knowledge:manage", "标记知识更新已处理")
    try:
        result = kus.mark_applied(proposal_id, body.applied_ref, _actor(user))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(user, "knowledge_proposal.applied", "knowledge_update_proposal",
           f"落点：{body.applied_ref or '（未填）'}｜changed={result.get('changed')}",
           proposal_id)
    return result


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, body: ProposalRejectIn,
                    user: dict = Depends(require_admin)):
    """驳回提议（留痕不删行：反复被驳的信号本身是数据）。"""
    from backend.modules.catalog.services import knowledge_update_service as kus

    _require_perm(user, "knowledge:manage", "驳回知识更新提议")
    try:
        result = kus.reject_proposal(proposal_id, _actor(user), body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(user, "knowledge_proposal.reject", "knowledge_update_proposal",
           f"理由：{body.note or '（未填）'}｜changed={result.get('changed')}", proposal_id)
    return result


# ── 本体对象标量（直接维护，也供提议处置复用）────────────────

@router.get("/ontology-objects")
def list_ontology_objects(datasource_name: str = Query(""),
                          user: dict = Depends(get_current_user)):
    """业务本体对象 + 当前标量值（选择框显示业务名，value 用 key）。

    只列业务本体：系统能力本体由声明目录维护，界面改会被刷新覆盖。
    """
    from backend.modules.catalog.services import knowledge_update_service as kus

    return {"items": kus.ontology_object_catalog(datasource_name=datasource_name),
            "note": "系统能力本体不在本清单：它由声明目录维护，请改脚本后重跑刷新"}


@router.post("/ontology-objects/scalars")
def update_object_scalars(body: ObjectScalarsIn, user: dict = Depends(require_admin)):
    """更新对象显示名/说明/别名（落草案；发布是独立动作，需 ``ontology:publish``）。"""
    from backend.modules.catalog.services import ontology_service

    _require_perm(user, "ontology:save", "更新本体对象标量")
    try:
        result = ontology_service.merge_object_scalars(
            int(body.model_id), body.object_key,
            {"display_name": body.display_name, "description": body.description,
             "aliases": body.aliases},
            created_by=_actor(user))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(user, "ontology_object.scalars", "ontology_model",
           f"{body.object_key}: {','.join(result.get('changed_fields') or []) or '无变更'}",
           int(body.model_id))
    return result


# ── 四出口（人工点击，权限码把关，审计服务端注入）────────────────

@router.post("/candidates/{candidate_id}/alias")
def adopt_alias(candidate_id: int, body: AliasIn, user: dict = Depends(require_admin)):
    """收编为**已存在**字典行/对象的别名。

    目标不存在时不报错也不静默：返回 ``next_action`` 引导走"建草稿"出口 ——
    这正是旧 approve 把 8/9 条 pending 卡死的断点。
    """
    _require_perm(user, "ontology:save", "别名回写")
    cand = evo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    try:
        result = evo.adopt_alias(term=cand["term"], target_type=body.target_type,
                                 target_ref=body.target_ref,
                                 datasource_id=evo.datasource_id_by_name(
                                     cand.get("datasource_name") or ""),
                                 decided_by=_actor(user), candidate_id=candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result.get("success"):
        # 目标缺行不是失败，是"该走另一个出口"：200 回指引，前端据此改按钮
        return result
    return result


@router.post("/candidates/{candidate_id}/metric-draft")
def create_metric_draft(candidate_id: int, body: MetricDraftIn,
                        user: dict = Depends(require_admin)):
    """转指标草稿（落 is_active=0，补完口径才启用）。"""
    _require_perm(user, "metrics:manage", "新建指标")
    cand = evo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    try:
        return evo.create_metric_draft(
            term=cand["term"], bound_object_key=body.bound_object_key,
            datasource_name=cand.get("datasource_name") or "",
            display_name=body.display_name, agg_type=body.agg_type,
            formula=body.formula, unit=body.unit, description=body.description,
            decided_by=_actor(user), candidate_id=candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/dimension-draft")
def create_dimension_draft(candidate_id: int, body: DimensionDraftIn,
                           user: dict = Depends(require_admin)):
    """转维度草稿。target_column 由业务属性名经服务端映射，人不接触物理列名。"""
    _require_perm(user, "metrics:manage", "新建维度")
    cand = evo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    try:
        return evo.create_dimension_draft(
            term=cand["term"], bound_object_key=body.bound_object_key,
            datasource_name=cand.get("datasource_name") or "",
            property_name=body.property_name, display_name=body.display_name,
            category=body.category, description=body.description,
            decided_by=_actor(user), candidate_id=candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/to-term")
def to_business_term(candidate_id: int, body: TermIn,
                     user: dict = Depends(require_admin)):
    """转入按数据源分桶的业务术语缓冲区（尚未绑定对象/字典的纯黑话）。"""
    _require_perm(user, "ontology:save", "术语维护")
    cand = evo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    try:
        return evo.to_business_term(term=cand["term"],
                                   datasource_name=cand.get("datasource_name") or "",
                                   decided_by=_actor(user), candidate_id=candidate_id,
                                   description=body.description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/to-eval-case")
def to_eval_case(candidate_id: int, body: EvalCaseIn, user: dict = Depends(require_admin)):
    """把一次缺口固化成评测用例；后续由归纳作业回填 verified/stale。"""
    _require_perm(user, "eval:manage", "新增评测用例")
    cand = evo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    try:
        return evo.to_eval_case(candidate_id=candidate_id, suite=body.suite,
                               capability=body.capability, question=body.question,
                               payload=body.payload, expected=body.expected,
                               note=body.note, decided_by=_actor(user))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: int, user: dict = Depends(require_admin)):
    """驳回候选。驳回不删行：留痕才能看出"这类信号被反复判为无效"。"""
    _require_perm(user, "ontology:save", "别名审核")
    if not evo.get_candidate(candidate_id):
        raise HTTPException(status_code=404, detail="候选不存在")
    return evo.reject_candidate(candidate_id=candidate_id, decided_by=_actor(user))


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: int, user: dict = Depends(get_current_user)):
    cand = evo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    return cand


# ── 归纳作业 ──────────────────────────────────────────────────

@router.post("/candidates/from-eval-failure")
def candidate_from_eval_failure(body: EvalFailureIn, user: dict = Depends(require_admin)):
    """评测失败 → 进化候选。

    只收人明确指定的业务词（缺省用问句），不拿失败文案做分词猜测；用的也是
    ``eval:manage``：评测侧与候选侧共用同一把权限，不另造第二套“谁能做什么”。
    """
    _require_perm(user, "eval:manage", "从评测失败创建进化候选")
    try:
        return evo.record_eval_failure_candidate(
            suite=body.suite, case_key=body.case_key, question=body.question,
            capability=body.capability, datasource_name=body.datasource_name,
            term=body.term, reason=body.reason, decided_by=_actor(user))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/digest")
def run_digest_now(background: Optional[bool] = Query(None),
                   user: dict = Depends(require_admin)):
    """立即执行一轮归纳（不等次日 03:30）。

    归纳失败显式 500 带原因：进化中心"没有新待办"必须能区分"真没发现"与
    "作业挂了"，否则就是把失败掩成空列表（no-silent-degradation §1）。
    """
    from backend.common import system_jobs

    _require_perm(user, "evolution:manage", "执行进化归纳")
    try:
        out = evo.run_digest()
    except Exception as exc:  # noqa: BLE001 — 原因必须回给用户，不吞
        logger.exception("[evolution] 手动归纳失败")
        system_jobs.record_run("evolution_digest", "failed",
                               f"手动归纳失败（{type(exc).__name__}）")
        raise HTTPException(status_code=500,
                            detail=f"归纳未完成：{type(exc).__name__}: {exc}") from exc
    system_jobs.record_run("evolution_digest", "success", out.get("summary", "")[:500])
    return out


@router.post("/backfill-legacy")
def backfill_legacy(user: dict = Depends(require_admin)):
    """把旧别名建议队列一次性搬迁到候选表（幂等；单一事实源收口）。"""
    _require_perm(user, "evolution:manage", "历史候选搬迁")
    try:
        return evo.backfill_alias_suggestions()
    except Exception as exc:  # noqa: BLE001
        logger.exception("[evolution] 历史搬迁失败")
        raise HTTPException(status_code=500,
                            detail=f"搬迁未完成：{type(exc).__name__}: {exc}") from exc
