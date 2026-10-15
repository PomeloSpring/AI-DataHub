"""自进化闭环 API：候选看板 / 四出口审核 / 归纳作业手动触发 / 反馈大盘。

权限口径（不另造第二套判断）
--------------------------
* 读：``get_current_user`` + api_permission 中间件按 ``evolution:read`` 门控；
* 写（收编别名/建草稿/转术语/转用例/驳回/立即归纳）：``require_admin`` 且写动作
  统一经 ``perm_link.require_write_perm`` 按功能权限码把关，``decided_by`` **由服务端
  从可信身份注入**，绝不从请求体取（护栏 §2 身份 fail-closed）；
* 评测出口复用 ``eval:manage``：进化候选转成评测用例，用的还是评测那套权限，
  且 ``evolution:*`` / ``eval:*`` 均为 ``ai_access='none'`` —— 评价 AI 的标尺
  不能由 AI 自己读写。

出口与权限码映射（as-bot-system-waker §3 的动作→权限码表口径）
----------------------------------------------------------
收编别名 / 转术语      → ``ontology:save``
建指标草稿/建维度草稿  → ``metrics:manage``
转评测用例             → ``eval:manage``
立即执行归纳           → ``evolution:manage``
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
    return {"items": evo.list_candidates(status=status, kind=kind, source=source,
                                         datasource_name=datasource_name, limit=limit),
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
