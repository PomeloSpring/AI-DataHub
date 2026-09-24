"""AS-BOT 系统助手 API — 审批管理 + 权限查询 + 配置展示.

挂载在 /api/as-bot 前缀下:
    POST /approve       — 批准并执行待审批操作
    POST /reject        — 拒绝待审批操作
    GET  /approvals     — 查询审批历史
    GET  /permissions   — 获取当前用户的 AS-BOT 动作权限
    PUT  /roles/{id}/permissions — 设置角色的 AS-BOT 动作权限(admin)
    GET  /actions       — 获取所有 AS-BOT 动作定义(前端展示用)
    GET  /config        — 获取系统 Waker 绑定配置(只读展示)
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

from services.shared.common.auth import get_current_user
from services.shared.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


class ApproveRequest(BaseModel):
    approval_id: int


class RejectRequest(BaseModel):
    approval_id: int


class SetRolePermissionsRequest(BaseModel):
    permissions: dict[str, bool]  # {action_key: is_allowed}


# ═══════════════════════════════════════════════════════════════════
# 审批执行
# ═══════════════════════════════════════════════════════════════════

def _execute_approved_action(action_key: str, payload: dict) -> dict:
    """执行已审批的操作, 返回执行结果."""
    try:
        if action_key == "ontology.generate":
            from services.datacatalog.services import ontology_service
            model = ontology_service.generate_draft(
                payload.get("datasource_id", 0),
                created_by=payload.get("created_by", ""),
            )
            return {"success": True, "model_id": model["id"], "object_count": model.get("object_count", 0)}

        elif action_key == "ontology.save":
            from services.datacatalog.services import ontology_service
            model = ontology_service.save_draft(
                payload.get("model_id", 0),
                payload.get("json_content", ""),
                name=payload.get("name"),
            )
            return {"success": True, "model_id": model["id"]}

        elif action_key == "ontology.activate":
            from services.datacatalog.services import ontology_service
            model = ontology_service.activate(payload.get("model_id", 0))
            return {"success": True, "model_id": model["id"], "status": model.get("status")}

        elif action_key == "ontology.import_yaml":
            from services.datacatalog.services import ontology_yaml_import
            result = ontology_yaml_import.import_palantir_yaml(
                dir_path=payload.get("dir"),
                datasource_id=payload.get("datasource_id", 0),
                created_by=payload.get("created_by", ""),
                rebuild_graph=payload.get("rebuild_graph", True),
            )
            return {"success": True, "model_id": result.get("model_id")}

        elif action_key == "metadata.sync":
            from services.datacatalog.services.metadata_service import MetadataService
            outcome = MetadataService.sync_metadata(payload["datasource_id"])
            if not outcome.get("success"):
                logger.error("AS-BOT 元数据同步失败: %s", outcome)
                return {"success": False, "error": "元数据同步未完成，请联系管理员查看日志"}
            return {"success": True, "message": "元数据同步已完成"}

        elif action_key == "task.claim_owner":
            from services.dataflow.services.scheduled_task_service import scheduled_task_service
            return scheduled_task_service.claim_owner(payload["task_id"], payload["proposed_by"])

        elif action_key == "alias.approve":
            # 别名回流审核通过: 写回字典/对象别名(object 经 save_draft 联动重建图谱+知识库)
            from services.datamind.rag.alias_suggestion import approve_suggestion
            result = approve_suggestion(payload)
            return {"success": True, **{k: v for k, v in result.items() if k != "success"}}

        elif action_key == "alias.reject":
            from services.datamind.rag.alias_suggestion import reject_suggestion
            result = reject_suggestion(payload)
            return {"success": True, **{k: v for k, v in result.items() if k != "success"}}

        else:
            return {"success": False, "error": f"未知操作类型: {action_key}"}

    except Exception as e:
        logger.error("[AS-BOT] Execute action %s failed: %s", action_key, e, exc_info=True)
        return {"success": False, "error": "操作未完成，请联系管理员查看执行日志"}


@router.post("/approve")
async def approve_action(
    req: ApproveRequest,
    user: UserInfo = Depends(get_current_user),
):
    """批准并执行待审批操作."""
    from services.datamind.execution.wakers import (
        get_approval, update_approval_status, check_as_bot_permission, claim_approval, validate_action_payload,
    )

    approval = get_approval(req.approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if approval["action_key"] == "dashboard.publish":
        from services.dataviz.services.dashboard_design_service import publish
        return await _design_call(publish, req.approval_id, user)
    if approval["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"审批已处理, 状态: {approval['status']}")

    # 权限检查: 审批者必须有该动作的权限
    user_role = user.get("role") or ""
    action_key = approval["action_key"]
    if not check_as_bot_permission(user_role, action_key):
        raise HTTPException(status_code=403, detail=f"无权操作: {action_key}")

    # 执行操作
    payload = approval.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    ok, error = validate_action_payload(action_key, payload)
    if not ok:
        raise HTTPException(status_code=422, detail=error)
    if not claim_approval(req.approval_id, user["user_id"]):
        raise HTTPException(status_code=409, detail="审批已被其他请求领取")
    # 审计可追溯: 审核人身份由服务端注入(不信任客户端字段)
    payload = {**payload, "decided_by": user["user_id"], "proposed_by": approval.get("user_id")}

    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(_execute_approved_action, action_key, payload)

    # 更新审批状态
    new_status = "executed" if result.get("success") else "failed"
    if not update_approval_status(req.approval_id, new_status, decided_by=user["user_id"], result=result):
        raise HTTPException(status_code=409, detail="审批执行状态发生变化，请核对流水")

    return {
        "approval_id": req.approval_id,
        "status": new_status,
        "result": result,
    }


@router.post("/reject")
async def reject_action(
    req: RejectRequest,
    user: UserInfo = Depends(get_current_user),
):
    """拒绝待审批操作."""
    from services.datamind.execution.wakers import get_approval, update_approval_status, check_as_bot_permission

    approval = get_approval(req.approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if approval["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"审批已处理, 状态: {approval['status']}")

    if approval["action_key"] == "dashboard.publish":
        from services.dataviz.services import dashboard_design_service as designs
        payload = designs.decoded(approval["payload"])
        result = await _design_call(designs.cancel, payload["design_id"], user, payload["version"])
        return {"approval_id": req.approval_id, "status": "superseded", "design": result}
    if not check_as_bot_permission(user.get("role") or "", approval["action_key"]):
        raise HTTPException(status_code=403, detail="无权处理该审批")
    if not update_approval_status(req.approval_id, "rejected", decided_by=user["user_id"]):
        raise HTTPException(status_code=409, detail="审批已被其他请求处理")

    return {"approval_id": req.approval_id, "status": "rejected"}


class CreateApprovalRequest(BaseModel):
    action_key: str
    payload: dict = {}
    conversation_id: Optional[int] = None


@router.post("/approvals/create")
async def create_pending_approval(
    req: CreateApprovalRequest,
    user: UserInfo = Depends(get_current_user),
):
    """创建一个待审批记录(提议阶段)。

    参数 schema 在 create_approval 内校验; 实际权限在 /approve 执行时把门(fail-closed)。
    未注册动作/参数不合法 → 返回 id=0 且 success=false。
    """
    from services.datamind.execution.wakers import create_approval

    if req.action_key == "dashboard.publish":
        raise HTTPException(status_code=422, detail="仪表盘发布审批只能通过设计面板成功预览后创建")
    approval_id = create_approval(
        user_id=user["user_id"],
        action_key=req.action_key,
        payload=req.payload or {},
        conversation_id=req.conversation_id,
    )
    if not approval_id:
        return {"success": False, "id": 0,
                "error": "动作未注册或参数校验失败, 未创建审批"}
    return {"success": True, "id": approval_id}


@router.get("/approvals")
async def list_approvals(
    status: Optional[str] = None,
    limit: int = 50,
    user: UserInfo = Depends(get_current_user),
):
    """查询审批历史."""
    from services.datamind.execution.wakers import _query

    sql = "SELECT * FROM adh_as_bot_approvals"
    params = []
    conditions = ["(action_key <> 'dashboard.publish' OR user_id=%s)"]
    params.append(user["user_id"])

    if status:
        conditions.append("status = %s")
        params.append(status)
    else:
        # 默认只查非 pending 的最近记录 + 所有 pending
        conditions.append("(status = 'pending' OR decided_at IS NOT NULL)")

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(min(limit, 200))

    rows = _query(sql, tuple(params))
    # 解析 JSON 字段
    for row in rows:
        for field in ("payload", "result"):
            val = row.get(field)
            if isinstance(val, str):
                try:
                    row[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
    return {"approvals": rows}


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
    from services.dataviz.services.dashboard_design_service import DesignError
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
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.list_designs, conversation_id, user)


@router.get("/dashboard-designs/{design_id}")
async def get_dashboard_design(design_id: str, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.get_design, design_id, user)


@router.get("/dashboard-designs/{design_id}/options")
async def dashboard_design_options(design_id: str, workspace: Optional[int] = None, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.options, design_id, user, workspace)


@router.post("/dashboard-designs/{design_id}/selection")
async def select_dashboard_design(design_id: str, req: DesignSelection, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.select_scope, design_id, user, req.expected_version, req.selection, req.name, req.operation)


@router.patch("/dashboard-designs/{design_id}")
async def patch_dashboard_design(design_id: str, req: DesignPatch, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.edit_design, design_id, user, req.expected_version, req.patch)


@router.get("/dashboard-designs/{design_id}/sql")
async def get_design_sql(design_id: str, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.sql_view, design_id, user)


@router.put("/dashboard-designs/{design_id}/sql")
async def edit_design_sql(design_id: str, req: DesignSql, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.edit_sql, design_id, user, req.expected_version, req.widget_key, req.sql)


@router.post("/dashboard-designs/{design_id}/preview")
async def preview_dashboard_design(design_id: str, req: DesignVersion, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.preview, design_id, user, req.expected_version)


@router.post("/dashboard-designs/{design_id}/cancel")
async def cancel_dashboard_design(design_id: str, req: DesignVersion, user: dict = Depends(get_current_user)):
    from services.dataviz.services import dashboard_design_service as designs
    return await _design_call(designs.cancel, design_id, user, req.expected_version)


# ═══════════════════════════════════════════════════════════════════
# 权限管理
# ═══════════════════════════════════════════════════════════════════

@router.get("/permissions")
async def get_permissions(user: UserInfo = Depends(get_current_user)):
    """获取当前用户的 AS-BOT 动作权限."""
    from services.datamind.execution.wakers import get_as_bot_role_permissions

    user_role = user.get("role") or ""
    permissions = get_as_bot_role_permissions(user_role)
    return {
        "role": user_role,
        "permissions": permissions,
        "can_access": user_role == "admin" or any(permissions.values()),
    }


@router.put("/roles/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    req: SetRolePermissionsRequest,
    user: UserInfo = Depends(get_current_user),
):
    """设置角色的 AS-BOT 动作权限(仅 admin)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可配置 AS-BOT 权限")

    from services.datamind.execution.wakers import set_as_bot_role_permissions

    set_as_bot_role_permissions(role_id, req.permissions)
    return {"success": True, "role_id": role_id}


@router.get("/actions")
async def list_actions():
    """获取所有 AS-BOT 动作定义."""
    from services.datamind.execution.wakers import AS_BOT_WRITE_ACTIONS

    action_labels = {
        "ontology.generate": "生成本体草案",
        "ontology.save": "保存本体模型",
        "ontology.activate": "激活本体模型",
        "ontology.import_yaml": "导入 Palantir YAML",
        "metadata.sync": "同步元数据",
        "task.claim_owner": "认领无创建者的定时任务",
        "alias.approve": "审核通过别名建议",
        "alias.reject": "驳回别名建议",
        "dashboard.publish": "确认并发布仪表盘设计",
    }
    return {
        "actions": [
            {"key": key, "label": action_labels.get(key, key)}
            for key in AS_BOT_WRITE_ACTIONS
        ]
    }


@router.get("/alias-suggestions")
async def list_alias_suggestions(
    status: str = "pending",
    limit: int = 50,
    user: UserInfo = Depends(get_current_user),
):
    """别名回流建议队列(只读, 供审核展示)。默认列 pending, 按命中次数倒序。"""
    from services.datamind.rag.alias_suggestion import list_pending

    items = list_pending(limit=limit) if status == "pending" else []
    return {"suggestions": items, "total": len(items)}


# ═══════════════════════════════════════════════════════════════════
# 系统 Waker 配置展示(只读)
# ═══════════════════════════════════════════════════════════════════

@router.get("/config")
async def get_system_bot_config(user: UserInfo = Depends(get_current_user)):
    """获取系统 Waker 的完整配置, 用于前端只读展示.

    返回: waker 基本信息、绑定的知识库、skills、工具组.
    """
    from services.datamind.execution.wakers import (
        resolve_system_bot_waker,
        load_knowledge_bases,
        load_skills,
        collect_knowledge_base_ids,
        collect_skill_names,
    )

    waker = resolve_system_bot_waker()
    if not waker:
        return {
            "waker": None,
            "knowledge_bases": [],
            "skills": [],
            "tool_groups": [],
        }

    # 解析绑定的知识库
    kb_ids = collect_knowledge_base_ids([waker])
    knowledge_bases = load_knowledge_bases(kb_ids)

    # 解析绑定的 skills
    skill_names = collect_skill_names([waker])
    skills = load_skills(skill_names)

    # 工具组
    tool_groups = waker.get("tools", {}).get("groups", [])

    return {
        "waker": {
            "id": waker.get("id"),
            "waker_key": waker.get("waker_key"),
            "name": waker.get("name"),
            "display_name": waker.get("display_name"),
            "description": waker.get("description"),
            "system_prompt": waker.get("system_prompt"),
            "persona": waker.get("persona"),
            "category": waker.get("category"),
        },
        "knowledge_bases": [
            {"id": kb["id"], "name": kb["name"], "kb_type": kb.get("kb_type", "")}
            for kb in knowledge_bases
        ],
        "skills": [
            {"name": s.get("name", ""), "display_name": s.get("display_name", "")}
            for s in skills
        ],
        "tool_groups": tool_groups,
    }


# ═══════════════════════════════════════════════════════════════════
# 本体模型结构展示(只读)
# ═══════════════════════════════════════════════════════════════════

@router.get("/ontology")
async def get_ontology_models(user: UserInfo = Depends(get_current_user)):
    """获取 AS-BOT 依赖的**系统能力本体**模型及对象结构摘要(只读展示).

    口径: 仅返数据源为系统级(datasource_id 为 0/空)的模型 —— 业务本体(如 test-alb-全量本体)
    属模型工作区治理范围, 不列在系统助手依赖页; 判定与前端工作区分组逻辑同源。
    """
    try:
        from services.datacatalog.services import ontology_service
    except Exception:
        return {"models": []}

    try:
        models = ontology_service.list_models(include_archived=False)
    except Exception:
        return {"models": []}

    result = []
    for m in models:
        if m.get("status") not in ("active", "draft"):
            continue
        if m.get("datasource_id"):   # 有真实数据源 = 业务本体, 不属 AS-BOT 系统能力依赖
            continue
        # 获取完整内容以提取对象摘要
        try:
            full = ontology_service.get_model(m["id"])
        except Exception:
            full = None

        objects_summary = []
        if full and full.get("json_content"):
            try:
                import json as _json
                doc = _json.loads(full["json_content"]) if isinstance(full["json_content"], str) else full["json_content"]
                for obj in doc.get("objects", []):
                    props = []
                    for p in obj.get("properties", []):
                        props.append({
                            "column": p.get("column", ""),
                            "name": p.get("name", ""),
                            "type": p.get("type", ""),
                            "is_key": bool(p.get("is_key")),
                            "description": p.get("description", ""),
                        })
                    objects_summary.append({
                        "key": obj.get("key", ""),
                        "display_name": obj.get("display_name", ""),
                        "description": obj.get("description", ""),
                        "primary_table": obj.get("primary_table", ""),
                        "aliases": obj.get("aliases", []),
                        "property_count": len(props),
                        "properties": props[:10],  # 最多展示10个属性避免过长
                    })
            except Exception:
                pass

        result.append({
            "id": m["id"],
            "name": m.get("name", ""),
            "status": m.get("status", ""),
            "datasource_id": m.get("datasource_id", 0),
            "object_count": m.get("object_count", 0),
            "created_at": m.get("created_at", ""),
            "updated_at": m.get("updated_at", ""),
            "objects": objects_summary,
        })

    return {"models": result}
