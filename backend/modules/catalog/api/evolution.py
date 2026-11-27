"""自进化闭环 API：角色域队列 / 统一决策 / 异步归纳作业 / 反馈大盘。

权限口径（不另造第二套判断）
--------------------------
* 读：``get_current_user`` + api_permission 中间件按 ``evolution:read`` 门控；
* 写（候选/提议处置、立即归纳）：``get_current_user`` + 中间件 ``evolution:manage``
  + 服务层 ``require_write_perm`` 双闸（``evolution:manage`` + 动作域码），
  ``decided_by`` **由服务端从可信身份注入**，绝不从请求体取（护栏 §2 身份 fail-closed）；
* 评测失败转候选只保留人工入口（``eval:manage`` 的 ``ai_access=none``）：
  评价 AI 的标尺不能由 AI 自己写，也不经 ``require_write_perm``（那是 AI 写级别闸）。

统一决策（P1-4，旧动作端点已退役）
--------------------------------
候选/提议处置一律走 ``POST .../decisions``（``Idempotency-Key`` + ``expected_version``），
由 ``evolution_decision_service`` 统一做状态机、版本仲裁、幂等账本与领域写入：
HTTP 与 AI 共用同一服务，不再各自实现分类、筛选、状态转换或权限判断。
旧端点 ``/alias`` / ``/metric-draft`` / ``/dimension-draft`` / ``/to-term`` / ``/reject`` /
``/applied`` / ``/to-eval-case`` / 同步 ``/digest`` / ``/backfill-legacy`` /
``/ontology-objects*`` 已于 P1-7 退役：本体标量编辑归
``PUT /api/catalog/ontology/models/{model_id}/objects/{object_key}/scalars``。

出口与权限码映射（as-bot-system-waker §3 的动作→权限码表口径）
----------------------------------------------------------
收编别名 / 转术语 / 驳回候选 → ``ontology:save``
建指标草稿/建维度草稿       → ``metrics:manage``
知识更新提议（标记/驳回/手工提交） → ``knowledge:manage``
立即执行归纳（异步 job）     → ``evolution:manage``
评测失败转候选（人工入口）   → ``eval:manage``（ai_access=none，不进 AI 动作集合）

知识更新提议**只是备忘录**：真正落库走各域既有通道（知识库 ``kbdoc:write``、
场景 ``knowledge:manage``、本体 ``ontology:save`` + 独立 publish）；
标记已处理必须带非空落点（applied_ref），禁止空落点假闭环。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.common.auth import (
    authorize_workspace, get_current_user,
    resolve_user_default_workspace_id,
)
from backend.modules.catalog.services import evolution_decision_service as decide
from backend.modules.catalog.services import evolution_query_service as queries
from backend.modules.catalog.services import evolution_service as evo

logger = logging.getLogger("api.evolution")
router = APIRouter()


# ── Request models ──────────────────────────────────────────────────────────

class EvalFailureIn(BaseModel):
    """从评测失败就地拉候选（人在评测页点击，不由归纳作业自动扫）。"""
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


class DecisionIn(BaseModel):
    """候选/提议统一决策体：action_key 判别 + 版本 + 理由 + 动作输入。

    服务层按 action_key 再校验一次（HTTP/AI 同口径）：
    reject 必须带 note；proposal.applied 必须带非空 applied_ref（input 或顶层均可）。
    """
    action_key: str
    expected_version: int = 1
    note: str = ""
    applied_ref: str = ""
    input: dict = {}


def _require_write(user: dict, perm_code: str, action: str) -> None:
    """写动作统一过功能权限码（AI 写级别闸）；拒绝原因必须可解释。"""
    from backend.core.perm_link import require_write_perm

    try:
        require_write_perm(int(user.get("user_id") or 0), 0, perm_code, action)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_human(user: dict, perm_code: str, action: str) -> None:
    """人工专属动作（如 eval:manage，ai_access=none）：只裁"角色是否持有该码"。

    不走 require_write_perm：那是 AI 写级别闸（要求 ai_access='write'），
    把人工入口也压在它下面等于把 eval:* 永久封死（评测转候选按钮全员 403）。
    HTTP 中间件已按 api_pattern 裁决过一次，这里是服务层第二道（fail-closed）。
    """
    from backend.core.role_service import role_service

    role_perms = role_service.get_user_role_ai_perms(int(user.get("user_id") or 0), 0)
    if not (role_perms or {}).get(perm_code):
        raise HTTPException(
            status_code=403,
            detail=f"角色未授予「{perm_code}」权限，无法执行「{action}」")


async def _evolution_context(request: Request,
                            user: dict = Depends(get_current_user)) -> queries.EvolutionContext:
    """从可信身份解析工作空间与角色数据源；请求体/工具参数不能注入身份。"""
    raw_ws = request.query_params.get("workspace_id") or request.headers.get("X-Workspace-Id")
    if raw_ws:
        try:
            workspace_id = int(raw_ws)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="工作空间无效") from exc
    else:
        workspace_id = await run_in_threadpool(
            resolve_user_default_workspace_id, int(user.get("user_id") or 0))
    workspace_id = authorize_workspace(user, workspace_id)
    if workspace_id <= 0:
        raise HTTPException(status_code=403, detail="进化中心需要已授权工作空间")
    try:
        return await run_in_threadpool(queries.EvolutionContext.from_http, user, workspace_id)
    except queries.EvolutionQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _query_http_error(exc: queries.EvolutionQueryError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _decision_http_error(exc: decide.EvolutionDecisionError) -> HTTPException:
    detail = str(exc)
    if exc.domain_ref:
        detail = f"{detail}（落点：{exc.domain_ref}）"
    return HTTPException(status_code=exc.status_code, detail=detail)


def _actor(user: dict) -> str:
    """审核人只取服务端身份，绝不从请求体读（护栏 §2）。"""
    return str(user.get("username") or user.get("user_id") or "")


def _audit(user: dict, action: str, target_type: str, detail: str,
           target_id: int = 0) -> None:
    """动作流水留痕（best-effort，不影响主链路；决策审计由决策服务账本承担）。"""
    try:
        from backend.common.auth import log_audit
        log_audit(int(user.get("user_id") or 0), _actor(user), action,
                  target_type=target_type, target_id=target_id, detail=detail,
                  module="catalog")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[evolution] 审计写入失败（不影响主链路）: %s", exc)


# ── 读面 ───────────────────────────────────────────────────────────────────

@router.get("/candidates")
def list_candidates(status: str = Query("pending"), kind: str = Query(""),
                    queue: str = Query(""), source: str = Query(""),
                    datasource_name: str = Query(""), q: str = Query(""),
                    page: int = Query(1, ge=1),
                    page_size: int = Query(queries.DEFAULT_PAGE_SIZE, ge=1,
                                           le=queries.MAX_PAGE_SIZE),
                    ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """角色域候选队列：服务端筛选、COUNT、LIMIT/OFFSET 与稳定排序。"""
    try:
        result = queries.list_candidates(
            ctx, status=status, kind=kind, queue=queue, source=source,
            datasource_name=datasource_name, q=q, page=page, page_size=page_size)
    except queries.EvolutionQueryError as exc:
        raise _query_http_error(exc) from exc
    return {**result, "kinds": list(evo.KINDS), "sources": list(evo.SOURCES),
            "statuses": list(evo.STATUSES), "queues": list(queries.QUEUES)}


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: int,
                  ctx: queries.EvolutionContext = Depends(_evolution_context)):
    cand = queries.get_candidate(ctx, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在或不在当前角色域内")
    return cand


@router.get("/overview")
def overview(ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """角色域候选分布、工作空间反馈、闭环成效与最近归纳作业。"""
    try:
        return queries.overview(ctx)
    except queries.EvolutionQueryError as exc:
        raise _query_http_error(exc) from exc


@router.get("/objects")
def list_objects(datasource_name: str = Query(...),
                 ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """授权数据源内的业务对象清单：显示业务名，value 用对象 key。"""
    try:
        return {"items": queries.object_options(ctx, datasource_name)}
    except queries.EvolutionQueryError as exc:
        raise _query_http_error(exc) from exc


@router.get("/properties")
def list_properties(datasource_name: str = Query(...), object_key: str = Query(...),
                    ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """对象主表上的业务属性名清单；物理列名不下发。"""
    try:
        return {"items": queries.property_options(
            ctx, bound_object_key=object_key, datasource_name=datasource_name)}
    except queries.EvolutionQueryError as exc:
        raise _query_http_error(exc) from exc


@router.get("/targets")
def list_targets(target_type: str = Query(...), datasource_name: str = Query(""),
                 ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """别名收编目标清单：既有字典行/本体对象按角色域过滤。"""
    try:
        return {"items": queries.target_options(
            ctx, target_type=target_type, datasource_name=datasource_name)}
    except queries.EvolutionQueryError as exc:
        raise _query_http_error(exc) from exc


# ── 知识更新提议（备忘录：不直写资产）─────────────────────────────────────

@router.get("/proposals")
def list_proposals(status: str = Query(""), target_type: str = Query(""),
                   source: str = Query(""), datasource_name: str = Query(""),
                   q: str = Query(""), page: int = Query(1, ge=1),
                   page_size: int = Query(queries.DEFAULT_PAGE_SIZE, ge=1,
                                          le=queries.MAX_PAGE_SIZE),
                   ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """角色域提议清单：列表只回摘要，正文走详情端点。"""
    try:
        result = queries.list_proposals(
            ctx, status=status, target_type=target_type, source=source,
            datasource_name=datasource_name, q=q, page=page, page_size=page_size)
    except queries.EvolutionQueryError as exc:
        raise _query_http_error(exc) from exc
    return {**result, "counts": queries.proposal_counts(ctx)}


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int,
                 ctx: queries.EvolutionContext = Depends(_evolution_context)):
    proposal = queries.get_proposal(ctx, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="提议不存在或不在当前角色域内")
    return proposal


@router.post("/proposals")
def create_proposal(body: ProposalIn, user: dict = Depends(get_current_user)):
    """手工提交一条知识更新提议（人自己也能把“应该改什么”写进待办）。"""
    from backend.modules.catalog.services import knowledge_update_service as kus

    _require_write(user, "knowledge:manage", "提交知识更新提议")
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


# ── 统一决策（版本 + 幂等 + 决策账本，HTTP/AI 同一条链）───────────────────

@router.post("/candidates/{candidate_id}/decisions")
def decide_candidate(candidate_id: int, body: DecisionIn, request: Request,
                     ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """候选处置：body 为 action_key + expected_version + note + input 判别联合。

    action 仅允许 ``evolution_candidate.adopt/reject/to_term``、
    ``evolution_metric.create``、``evolution_dimension.create``；
    冲突 409（STALE_DECISION / DOMAIN_WRITTEN_DECISION_CONFLICT）不覆盖既有结论。
    """
    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    try:
        return decide.decide_candidate(
            ctx, candidate_id=candidate_id, action_key=str(body.action_key or "").strip(),
            expected_version=int(body.expected_version or 0), note=body.note,
            input_data=dict(body.input or {}), idempotency_key=idem_key)
    except decide.EvolutionDecisionError as exc:
        raise _decision_http_error(exc) from exc


@router.post("/proposals/{proposal_id}/decisions")
def decide_proposal(proposal_id: int, body: DecisionIn, request: Request,
                    ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """提议处置：``evolution_proposal.applied`` 必须带非空 applied_ref（真实落点），
    ``evolution_proposal.reject`` 必须带非空 note；同样要求版本与幂等键。"""
    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    applied_ref = body.applied_ref or str((body.input or {}).get("applied_ref") or "")
    try:
        return decide.decide_proposal(
            ctx, proposal_id=proposal_id, action_key=str(body.action_key or "").strip(),
            expected_version=int(body.expected_version or 0), note=body.note,
            applied_ref=applied_ref, idempotency_key=idem_key)
    except decide.EvolutionDecisionError as exc:
        raise _decision_http_error(exc) from exc


# ── 归纳作业（异步 job：POST 创建、GET 查状态；失败显式可见）──────────────

@router.post("/digests")
def submit_digest_job(ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """提交一轮归纳作业：立即返回 job_id，后台执行；状态/失败原因见 GET /digests/{job_id}。"""
    try:
        return decide.submit_digest_job(ctx)
    except decide.EvolutionDecisionError as exc:
        raise _decision_http_error(exc) from exc


@router.get("/digests/{job_id}")
def get_digest_job(job_id: int,
                   ctx: queries.EvolutionContext = Depends(_evolution_context)):
    """归纳作业状态：queued/running/succeeded/failed + 可重试错误（error_message）。"""
    job = queries.get_digest_job(ctx, int(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="归纳作业不存在或不在当前工作空间")
    return job


# ── 评测失败转候选（人工专属入口，ai_access=none 不进 AI）──────────────────

@router.post("/candidates/from-eval-failure")
def candidate_from_eval_failure(body: EvalFailureIn,
                                user: dict = Depends(get_current_user)):
    """评测失败 → 进化候选。

    只收人明确指定的业务词（缺省用问句），不拿失败文案做分词猜测；用的也是
    ``eval:manage``：评测侧与候选侧共用同一把权限，不另造第二套“谁能做什么”。
    """
    _require_human(user, "eval:manage", "从评测失败创建进化候选")
    try:
        return evo.record_eval_failure_candidate(
            suite=body.suite, case_key=body.case_key, question=body.question,
            capability=body.capability, datasource_name=body.datasource_name,
            term=body.term, reason=body.reason, decided_by=_actor(user))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
