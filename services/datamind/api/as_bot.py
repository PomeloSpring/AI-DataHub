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
from pydantic import BaseModel
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
            # 元数据同步由 dataflow 服务处理, 此处仅记录审批
            return {"success": True, "message": "元数据同步已触发"}

        else:
            return {"success": False, "error": f"未知操作类型: {action_key}"}

    except Exception as e:
        logger.error("[AS-BOT] Execute action %s failed: %s", action_key, e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.post("/approve")
async def approve_action(
    req: ApproveRequest,
    user: UserInfo = Depends(get_current_user),
):
    """批准并执行待审批操作."""
    from services.datamind.execution.wakers import get_approval, update_approval_status, check_as_bot_permission

    approval = get_approval(req.approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
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

    result = _execute_approved_action(action_key, payload)

    # 更新审批状态
    new_status = "executed" if result.get("success") else "failed"
    update_approval_status(
        req.approval_id, new_status,
        decided_by=user["user_id"],
        result=result,
    )

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
    from services.datamind.execution.wakers import get_approval, update_approval_status

    approval = get_approval(req.approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if approval["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"审批已处理, 状态: {approval['status']}")

    update_approval_status(
        req.approval_id, "rejected",
        decided_by=user["user_id"],
    )

    return {"approval_id": req.approval_id, "status": "rejected"}


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
    conditions = []

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
    }
    return {
        "actions": [
            {"key": key, "label": action_labels.get(key, key)}
            for key in AS_BOT_WRITE_ACTIONS
        ]
    }


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
    """获取所有活跃的本体模型及其对象结构摘要, 供 AS-BOT 配置页只读展示."""
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
