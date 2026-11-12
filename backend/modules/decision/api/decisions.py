"""Decision Advice API — 决策建议的读面（query）与写面（config / publish）。

路径前缀刻意分成三段，与权限码一一对应（`docker/mysql/decision_tree_migration.sql`）：

    /api/decisions/query*    → decision:read     （GET,POST）
    /api/decisions/config*   → decision:manage   （POST,PUT,DELETE）
    /api/decisions/publish*  → decision:publish  （POST）

为什么要分三个前缀而不是统一 `/api/decisions*`：中间件按 `api_pattern × api_method`
做笛卡尔积匹配、且是"任一已授权码命中即放行"，**做不了减法**（fnmatch 的 `*`
连 `/` 一起吃）。放一个前缀就会让 read 码顺带放行 publish。

身份只取服务端（`get_current_user`），工作空间只作选择、必须过归属校验；
`datasource_id`/`site_code` 由角色授权的派生视图给出，不接受请求体指定任意源。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.common.auth import authorize_resource_scope, get_current_user
from backend.modules.decision import engine, params as decision_params, service
from backend.modules.decision.engine import EvalContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["决策建议"])


# ── 请求模型 ─────────────────────────────────────────────────────

class TreeUpsert(BaseModel):
    tree_key: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    workspace_id: int = 0
    tree_doc: dict[str, Any] = Field(default_factory=dict)
    apply_sites: list[dict[str, Any]] = Field(default_factory=list)
    schedule_cron: str = ""
    timezone: str = "Asia/Shanghai"
    notify_channel_id: int = 0


class AdviceUpsert(BaseModel):
    advice_key: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=512)
    rationale: str = ""
    severity: str = "medium"
    kb_refs: list[Any] = Field(default_factory=list)
    workspace_id: int = 0
    is_active: bool = True


class StatusUpdate(BaseModel):
    status: str = Field(..., description="retired | active")


class EvaluateRequest(BaseModel):
    workspace_id: int = 0
    site: str = Field("", description="站点业务名/代码（可选；缺省按会话生效站点派生）")
    # 看板出口（计划 §四出口 4）：控件把看板**逻辑参数原值**与绑定清单递过来，
    # 翻译只在 `decision.params`（内部复用 semantics.param_plan）发生一次；
    # 前端不得自己拼 filters（那就是第二套参数通道）。
    params: dict[str, Any] = Field(default_factory=dict, description="看板逻辑参数值（键=参数名）")
    param_bindings: list[dict[str, Any]] = Field(
        default_factory=list, description="[{key, slot: time_window|dimension|site, dimension?, label?}]")


class ValidateRequest(BaseModel):
    tree_doc: dict[str, Any] = Field(default_factory=dict)


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, service.DecisionError):
        return HTTPException(status_code=exc.status, detail=str(exc))
    logger.exception("[decision] 未预期错误")
    return HTTPException(status_code=500, detail="决策建议请求处理失败，请稍后重试或联系管理员")


# ── 生效源/站点派生（唯一裁决点仍是角色授权，本模块不自建判断）────────

def _resolve_identity(user: dict, workspace_id: int, site: str = "") -> tuple[int, str]:
    """返回 (datasource_id, site_code)，两者都只在角色授权集内解析。

    数据源可用性唯一裁决点是 `role_service.get_user_allowed_datasources`，本层只是它的
    消费方（waker-datasource-domain §1）：不新建判断、不接受请求体里的 datasource_id。
    未授权/多义一律 fail-loud 回可操作文案（含候选站点名），不回退到“随便挑一个源”。
    """
    from backend.core import site_service
    from backend.core.role_service import role_service

    user_id = int(user.get("user_id") or 0)
    allowed_ids = sorted({int(i) for i in (role_service.get_user_allowed_datasources(
        user_id, int(workspace_id or 0)) or []) if str(i).strip() != ""})
    if not allowed_ids:
        # 空授权不是全量：没有可查源就直接说清（前端据此给可操作提示）
        return 0, ""

    wanted = (site or "").strip()
    if wanted:
        ds_id = site_service.resolve_site_to_source(wanted, allowed_ids)
        code = site_service.normalize_site_ref(wanted)
        info = site_service.get_site(code) or {}
        return int(ds_id or 0), str(info.get("site_code") or code or "")

    # 不给站点时：只有一个可查站就直接用它；多站不能替用户猜一个（跨国口径下
    # “猜站点”= 给错数字）。留空时引擎不拼 site，由会话源/树的站点门去说清。
    sites = [s for s in (site_service.authorized_sites(user_id, int(workspace_id or 0)) or [])
             if isinstance(s, dict) and s.get("site_code") and not int(s.get("is_aggregate") or 0)]
    if len(sites) == 1:
        code = str(sites[0].get("site_code"))
        return site_service.resolve_site_to_source(code, allowed_ids), code
    return 0, ""


def _ctx_from(user: dict, workspace_id: int, site: str = "", mode: str = "full",
              params: dict | None = None, param_bindings: list | None = None,
              trigger_type: str = "") -> EvalContext:
    wanted = (site or "").strip()
    # 只有 full 模式会自己取数，所以看板附加条件也只在 full 下套用（turn 不补取数）
    translated = decision_params.translate(params or {},
                                          (param_bindings or []) if mode == "full" else [])
    if not wanted and translated["site"]:
        # 站点不拼进 filters（语义层契约：站点不是 SQL 维度），只当请求没带 site 时的缺省
        wanted = translated["site"]
    ds_id, site_code = _resolve_identity(user, workspace_id, wanted)
    ctx = EvalContext(
        mode=mode,
        user_id=int(user.get("user_id") or 0),
        username=str(user.get("username") or ""),
        role=str(user.get("role") or ""),
        workspace_id=int(workspace_id or 0),
        datasource_id=int(ds_id or 0),
        site_code=site_code or "",
        trigger_type=trigger_type,
        intent_filters=translated["filters"],
        filter_time_start=translated["time_start"],
        filter_time_end=translated["time_end"],
        param_notes=translated["notes"],
    )
    return ctx


# ══════════════════════════════════════════════════════════════════
# 读面：/api/decisions/query*（decision:read）
# ══════════════════════════════════════════════════════════════════

@router.get("/query/trees")
def query_trees(workspace_id: int = Query(0), status: str = Query(""),
                include_retired: bool = Query(False),
                user: dict = Depends(get_current_user)):
    """树清单。配置页需要看到已下架的树（要能恢复/删除），所以 include_retired
    由调用方显式带；消费页不带，下架的树不该出现在业务人员的清单里。"""
    try:
        authorize_resource_scope(user, workspace_id)
        return service.list_trees(workspace_id, status, include_retired)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/query/trees/{tree_key}")
def query_tree(tree_key: str, user: dict = Depends(get_current_user)):
    try:
        row = service.require_tree(tree_key)
        authorize_resource_scope(user, int(row.get("workspace_id") or 0))
        return row
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/query/advice")
def query_advice(workspace_id: int = Query(0), user: dict = Depends(get_current_user)):
    try:
        authorize_resource_scope(user, workspace_id)
        return service.list_advice(workspace_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/query/runs")
def query_runs(workspace_id: int = Query(0), tree_key: str = Query(""),
               verdict: str = Query(""), page: int = Query(1, ge=1),
               size: int = Query(20, ge=1, le=200),
               user: dict = Depends(get_current_user)):
    try:
        authorize_resource_scope(user, workspace_id)
        return service.list_runs(workspace_id, tree_key, verdict, page, size)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/query/runs/{run_id}")
def query_run(run_id: int, user: dict = Depends(get_current_user)):
    try:
        run = service.get_run(run_id)
        if not run:
            raise service.DecisionError("评估记录不存在或已被清理", "RUN_NOT_FOUND", 404)
        authorize_resource_scope(user, int(run.get("workspace_id") or 0))
        # 实例是按"当时那个人的授权"取的数：别人（尤其无该源授权者）不该看到数值
        if int(run.get("user_id") or 0) not in (0, int(user.get("user_id") or 0)) \
                and str(user.get("role") or "") != "admin":
            raise service.DecisionError("这条评估由他人发起，只对其本人与管理员可见",
                                        "RUN_NOT_OWNER", 403)
        return run
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/query/turn-advice")
def query_turn_advice(conversation_id: int = Query(..., ge=1),
                      message_uuids: str = Query("", description="逗号分隔的助手消息 uuid"),
                      user: dict = Depends(get_current_user)):
    """历史回放：按消息 uuid 取 Chat 回合的建议实例，**不重算**。

    为什么不重算（本模块唯一一处例外，原因同时写在 service.find_turn_runs 与
    outlet.advice_for_messages）：重算要逐回合重新取数，打开一个几十轮的
    老会话就会变成几十次打库；而且口径可能已被人改过，重算出来的不是
    “当时那条建议”。消息体的建议字段是 slim 下发的（不往历史正文里复制一份），
    所以回放只读 adh_decision_runs —— 与 done 事件同一份实例来源。
    """
    try:
        from backend.common.db import execute_query

        conv = execute_query(
            "SELECT id, user_id, workspace_id FROM adh_conversations WHERE id = %s",
            (int(conversation_id),), fetchone=True)
        if not conv:
            raise service.DecisionError("会话不存在或已清理", "CONVERSATION_NOT_FOUND", 404)
        # 会话归属：建议实例里带实测值，只能看自己（admin 走 API 层旁路）
        if str(user.get("role") or "") != "admin" \
                and int(conv.get("user_id") or 0) != int(user.get("user_id") or 0):
            raise service.DecisionError("无权读取该会话的决策建议记录", "NOT_CONVERSATION_OWNER", 403)
        authorize_resource_scope(user, int(conv.get("workspace_id") or 0))
        from backend.modules.decision import outlet

        uuids = [u.strip() for u in (message_uuids or "").split(",") if u.strip()][:200]
        return {"advice": outlet.advice_for_messages(int(conversation_id), uuids)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/query/dictionary")
def query_dictionary(kind: str = Query("metric"), search: str = Query(""),
                     limit: int = Query(80, ge=1, le=400),
                     user: dict = Depends(get_current_user)):
    """配置页的业务名选择器数据源：只回 name/name_en/aliases + 归属对象业务名。

    存在意义就是把"该选哪个指标"变成选择框（ui-resource-display §2），
    并且从这一层就保证物理表列名不会进入配置界面。
    """
    if kind not in ("metric", "dimension"):
        raise HTTPException(status_code=400, detail="kind 只支持 metric 或 dimension")
    from backend.modules.decision import dictionary as dic

    index = dic.load_dictionary(0)
    term = (search or "").strip().lower()
    items = []
    for h in index.get(kind) or []:
        if term and term not in h.name.lower() and not any(term in a.lower() for a in h.aliases):
            continue
        items.append({"name": h.name, "name_en": h.name_en, "aliases": h.aliases,
                      "object_key": h.object_key, "has_object": bool(h.object_key)})
    items.sort(key=lambda x: x["name"])
    return {"items": items[:limit], "total": len(items)}


@router.post("/query/evaluate")
def evaluate(req: EvaluateRequest, user: dict = Depends(get_current_user)):
    """批量重算当前空间已发布的决策树（决策中心「重新评估」）。"""
    try:
        authorize_resource_scope(user, req.workspace_id)
        ctx = _ctx_from(user, req.workspace_id, req.site)
        if not ctx.datasource_id and not ctx.site_code:
            return {"items": [], "hit_count": 0, "evaluated": 0,
                    "errors": [{"tree_key": "", "reason":
                                "当前角色没有已授权数据源，无法取数评估；"
                                "请联系管理员配置角色数据源授权"}]}
        return service.evaluate_all(req.workspace_id, ctx)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/query/trees/{tree_key}/evaluate")
def evaluate_one(tree_key: str, req: EvaluateRequest, user: dict = Depends(get_current_user)):
    """单棵树重算：决策中心的「评估」、Chat 卡片的「完整评估」、看板控件都走这里。

    看板控件会带 params/param_bindings，实例归因落 trigger='dashboard'（否则
    命中历史里看板的求值会伪装成人工手动评估）。
    """
    try:
        row = service.require_tree(tree_key)
        authorize_resource_scope(user, int(row.get("workspace_id") or 0))
        has_params = bool(req.params or req.param_bindings)
        ctx = _ctx_from(user, req.workspace_id or int(row.get("workspace_id") or 0), req.site,
                        params=req.params, param_bindings=req.param_bindings,
                        trigger_type="dashboard" if has_params else "")
        return service.evaluate(tree_key, ctx)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/query/validate")
def validate_doc(req: ValidateRequest, user: dict = Depends(get_current_user)):
    """编辑器即时校验：回 errors/warnings + 候选清单（不落库，所以 read 码即可用）。"""
    try:
        doc, err = engine.load_doc(req.tree_doc)
        if err:
            return {"valid": False, "errors": [{"where": "root", "message": err,
                                                "term": "", "type": "doc", "candidates": []}],
                    "warnings": []}
        errors, warnings = service.validate_doc(doc)
        return {"valid": not errors, "errors": errors, "warnings": warnings}
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


# ══════════════════════════════════════════════════════════════════
# 写面：/api/decisions/config*（decision:manage）
# ══════════════════════════════════════════════════════════════════

@router.post("/config/trees")
def save_tree(req: TreeUpsert, user: dict = Depends(get_current_user)):
    try:
        authorize_resource_scope(user, req.workspace_id)
        return service.save_draft(req.model_dump(), user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.put("/config/trees/{tree_key}")
def update_tree(tree_key: str, req: TreeUpsert, user: dict = Depends(get_current_user)):
    try:
        existing = service.require_tree(tree_key)
        authorize_resource_scope(user, int(existing.get("workspace_id") or 0))
        if req.tree_key != tree_key:
            raise service.DecisionError("tree_key 是稳定标识，改名请新建一棵树（历史引用不会跟着走）",
                                        "TREE_KEY_IMMUTABLE")
        return service.save_draft(req.model_dump(), user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.delete("/config/trees/{tree_key}")
def delete_tree(tree_key: str, user: dict = Depends(get_current_user)):
    try:
        existing = service.require_tree(tree_key)
        authorize_resource_scope(user, int(existing.get("workspace_id") or 0))
        return service.delete_tree(tree_key, user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/config/advice")
def save_advice(req: AdviceUpsert, user: dict = Depends(get_current_user)):
    try:
        authorize_resource_scope(user, req.workspace_id)
        return service.save_advice(req.model_dump(), user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.put("/config/advice/{advice_key}")
def update_advice(advice_key: str, req: AdviceUpsert, user: dict = Depends(get_current_user)):
    try:
        if req.advice_key != advice_key:
            raise service.DecisionError("advice_key 是稳定标识，不能改名（决策树按它引用）",
                                        "ADVICE_KEY_IMMUTABLE")
        return service.save_advice(req.model_dump(), user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.delete("/config/advice/{advice_key}")
def delete_advice(advice_key: str, user: dict = Depends(get_current_user)):
    try:
        return service.delete_advice(advice_key)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


# ══════════════════════════════════════════════════════════════════
# 发布面：/api/decisions/publish*（decision:publish，两段式第二段）
# ══════════════════════════════════════════════════════════════════

@router.post("/publish/{tree_key}")
def publish_tree(tree_key: str, user: dict = Depends(get_current_user)):
    try:
        row = service.require_tree(tree_key)
        authorize_resource_scope(user, int(row.get("workspace_id") or 0))
        return service.publish(tree_key, user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/publish/{tree_key}/retire")
def retire_tree(tree_key: str, user: dict = Depends(get_current_user)):
    """下架（不再参与评估，历史实例仍可查）。恢复同样走这里，status 显式给。"""
    try:
        row = service.require_tree(tree_key)
        authorize_resource_scope(user, int(row.get("workspace_id") or 0))
        return service.set_status(tree_key, "retired", user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/publish/{tree_key}/restore")
def restore_tree(tree_key: str, user: dict = Depends(get_current_user)):
    try:
        row = service.require_tree(tree_key)
        authorize_resource_scope(user, int(row.get("workspace_id") or 0))
        return service.set_status(tree_key, "active", user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc
