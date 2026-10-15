"""AS-BOT API — 别名审核 + 仪表盘设计（直执行，无审批回路）.

挂载在 /api/as-bot 前缀下:
    GET  /alias-suggestions              — 别名回流建议队列(只读)
    POST /alias-suggestions/{id}/approve — 别名回写(ontology:save 权限码把关后直执行)
    POST /alias-suggestions/{id}/reject  — 别名驳回(同上)
    GET  /dashboard-designs...            — 仪表盘设计端点组(设计/预览/SQL 编辑)
    POST /dashboard-designs/{id}/publish  — 确认发布(dashboard:manage 权限码把关后直执行)

历史背景: AS-BOT 动作权限矩阵(adh_as_bot_role_actions)与审批通道
(adh_as_bot_approvals)已随 AS-BOT/Waker 统一退役——写动作由菜单与功能权限码
(adh_perm_registry + adh_role_perms, 经 perm_link 裁决) + AS-BOT 工具授权
直接把关执行, 不再走提议→审批→执行回路。
"""

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

from backend.common.auth import get_current_user
from backend.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════
# 别名回流审核（直执行）
# ═══════════════════════════════════════════════════════════════════

class AliasDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: Optional[str] = None
    target_type: Optional[str] = None
    target_ref: Optional[str] = None


@router.get("/alias-suggestions")
async def list_alias_suggestions(
    status: str = "pending",
    limit: int = 50,
    user: UserInfo = Depends(get_current_user),
):
    """进化候选（别名/待建资产/待核绑定）只读清单，供审核展示。

    数据源已收口到 ``adh_evolution_candidates``：旧 ``adh_alias_suggestions``
    退役为冻结表。两处同时当真的迟早飘，所以本端点不再读旧表。
    """
    from backend.modules.catalog.services import evolution_service

    items = evolution_service.list_candidates(status=status, limit=min(int(limit), 200))
    return {"suggestions": items, "total": len(items)}


@router.post("/alias-suggestions/{suggestion_id}/approve")
async def approve_alias_suggestion(
    suggestion_id: int,
    req: AliasDecisionRequest = None,
    user: UserInfo = Depends(get_current_user),
):
    """别名回写直执行：功能权限码 ``ontology:save`` 把关后写回字典/对象别名。

    目标字典行不存在时**不再 raise**：返回 ``next_action`` 引导走“建草稿”出口。
    旧实现在这条路上把 8/9 条 pending 卡死（越需要归纳的词越点不通）。
    """
    from backend.core.perm_link import require_write_perm
    from backend.modules.catalog.services import evolution_service

    try:
        require_write_perm(user["user_id"], 0, "ontology:save", "别名回写")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    cand = evolution_service.get_candidate(suggestion_id)
    if not cand:
        raise HTTPException(status_code=404, detail="候选不存在")
    payload = {"decided_by": str(user["user_id"])}
    for field in ("term", "target_type", "target_ref"):
        value = getattr(req, field, None) if req else None
        if value:
            payload[field] = value
    try:
        result = evolution_service.adopt_alias(
            term=payload.get("term") or cand["term"],
            target_type=payload.get("target_type") or cand.get("target_type") or "dimension",
            target_ref=payload.get("target_ref") or cand.get("target_ref") or "",
            datasource_id=evolution_service.datasource_id_by_name(
                cand.get("datasource_name") or ""),
            decided_by=payload["decided_by"], candidate_id=suggestion_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 写回失败细节仅进日志
        logger.exception("[AS-BOT] 别名回写失败 candidate=%s", suggestion_id)
        raise HTTPException(status_code=503, detail="别名回写未完成，请稍后重试或联系管理员") from exc
    return {"success": True, **{k: v for k, v in result.items() if k != "success"}}


@router.post("/alias-suggestions/{suggestion_id}/reject")
async def reject_alias_suggestion(
    suggestion_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """候选驳回直执行：ontology:save 权限码把关（与回写同一把关口径）。"""
    from backend.core.perm_link import require_write_perm
    from backend.modules.catalog.services import evolution_service

    try:
        require_write_perm(user["user_id"], 0, "ontology:save", "别名审核")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not evolution_service.get_candidate(suggestion_id):
        raise HTTPException(status_code=404, detail="候选不存在")
    try:
        result = evolution_service.reject_candidate(
            candidate_id=suggestion_id, decided_by=str(user["user_id"]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, **{k: v for k, v in result.items() if k != "success"}}


# 仪表盘设计接口：SQL 只在下列鉴权 REST 接口出现，不进 Agent 工具返回。
class DesignVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class DesignSelection(DesignVersion):
    selection: dict
    name: Optional[str] = None
    operation: Optional[str] = None


class DesignPatch(DesignVersion):
    patch: dict


class DesignSql(DesignVersion):
    widget_key: str
    sql: str = Field(min_length=1, max_length=50000)


async def _design_call(fn, *args):
    from backend.modules.viz.services.dashboard_design_service import DesignError
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(fn, *args)
    except DesignError as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), "code": exc.code}) from exc
    except HTTPException:
        raise
    except PermissionError as exc:
        logger.warning("仪表盘设计权限校验拒绝: %s", type(exc).__name__)
        raise HTTPException(status_code=403, detail="查询结构或资源权限不满足要求，操作未完成") from exc
    except Exception as exc:
        logger.exception("仪表盘设计失败")
        raise HTTPException(status_code=503, detail="设计操作未完成，请重试或联系管理员；未发布到仪表盘") from exc


@router.get("/dashboard-designs")
async def list_dashboard_designs(conversation_id: int, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.list_designs, conversation_id, user)


@router.get("/dashboard-designs/{design_id}")
async def get_dashboard_design(design_id: str, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.get_design, design_id, user)


@router.get("/dashboard-designs/{design_id}/options")
async def dashboard_design_options(design_id: str, workspace: Optional[int] = None, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.options, design_id, user, workspace)


@router.post("/dashboard-designs/{design_id}/selection")
async def select_dashboard_design(design_id: str, req: DesignSelection, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.select_scope, design_id, user, req.expected_version, req.selection, req.name, req.operation)


@router.patch("/dashboard-designs/{design_id}")
async def patch_dashboard_design(design_id: str, req: DesignPatch, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.edit_design, design_id, user, req.expected_version, req.patch)


@router.get("/dashboard-designs/{design_id}/sql")
async def get_design_sql(design_id: str, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.sql_view, design_id, user)


@router.put("/dashboard-designs/{design_id}/sql")
async def edit_design_sql(design_id: str, req: DesignSql, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.edit_sql, design_id, user, req.expected_version, req.widget_key, req.sql)


@router.post("/dashboard-designs/{design_id}/preview")
async def preview_dashboard_design(design_id: str, req: DesignVersion, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.preview, design_id, user, req.expected_version)


@router.post("/dashboard-designs/{design_id}/publish")
async def publish_dashboard_design(design_id: str, req: DesignVersion, user: dict = Depends(get_current_user)):
    """确认发布（直执行）：预览有效 + 摘要一致 + 目标未变才落库。

    权限把关在服务内（dashboard:manage，菜单与功能权限码）；修改使旧预览失效。
    """
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.publish, design_id, user, req.expected_version)


@router.post("/dashboard-designs/{design_id}/cancel")
async def cancel_dashboard_design(design_id: str, req: DesignVersion, user: dict = Depends(get_current_user)):
    from backend.modules.viz.services import dashboard_design_service as designs
    return await _design_call(designs.cancel, design_id, user, req.expected_version)
