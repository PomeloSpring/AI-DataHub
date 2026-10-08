"""Execution Dispatch API — 执行层任务派发.

业务逻辑位于 backend.modules.mind.execution(适配器/管理器),
本路由负责参数校验与派发委托。
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from backend.common.auth import get_current_user, authorize_workspace
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request Models ────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    question: str
    workspace_id: int = 0
    execution_layer_id: Optional[int] = None  # 不传则使用工作空间默认执行层
    datasource_id: Optional[int] = None
    model_id: Optional[int] = None
    history: list[dict] = []
    conversation_id: int = 0
    as_bot_key: str = ""
    timeout: int = 300
    attachments: list[str] = []  # 多模态附件:会话工作区既有文件相对路径清单(如 uploads/x.png)


# ── Endpoints ─────────────────────────────────────────────────────

# (已退役: GET /layers/{workspace_id} 工作空间执行层绑定 —— 执行层全局生效, 不按空间绑定)


@router.post("/execute")
async def execute_task(req: ExecuteRequest, user: dict = Depends(get_current_user)):
    """向执行层派发任务.

    指定 execution_layer_id 时使用对应执行层,
    否则使用工作空间默认执行层(未绑定时回退内置执行层)。
    """
    from backend.modules.mind.execution import service
    from backend.modules.mind.execution.manager import get_execution_layer_manager
    from backend.modules.mind.execution.models import ExecutionContext, ExecutionTask

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")

    workspace_id = authorize_workspace(user, req.workspace_id)
    manager = get_execution_layer_manager()
    try:
        if req.execution_layer_id:
            row = service.get_layer(req.execution_layer_id)
            if not row:
                raise HTTPException(status_code=404, detail=f"执行层不存在: id={req.execution_layer_id}")
            if row.get("status") != "active":
                raise HTTPException(status_code=400, detail=f"执行层不可用: {row.get('name')}")
        else:
            row = await manager.resolve_workspace_layer(req.workspace_id)
    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(status_code=500, detail="执行服务暂不可用") from None

    from backend.modules.mind.execution.session_workspace import preflight_request
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(preflight_request, req, user)
    if row.get("layer_type") != "cli" or (row.get("config") or {}).get("mode") != "sdk":
        raise HTTPException(422, "所选执行层不支持 AS-BOT 安全执行")
    # 执行层全局生效(不按工作空间绑定), 无需绑定校验
    # 附件按工作区相对路径引用既有文件(place_attachments 做包含/存在校验,缺失显式报错)
    task_attachments = [{"path": p} for p in req.attachments]

    task = ExecutionTask(
        task_id=uuid.uuid4().hex[:16],
        question=req.question,
        history=req.history,
        context=ExecutionContext(
            workspace_id=workspace_id,
            datasource_id=req.datasource_id or 0,
            user_id=user["user_id"],
            username=user.get("username", ""),
            user_role=user.get("role", ""),
            model_id=req.model_id,
            extra={"conversation_id": req.conversation_id, "as_bot_key": req.as_bot_key},
        ),
        timeout=req.timeout,
        attachments=task_attachments,
    )

    try:
        adapter = manager.build_adapter(row)
        result = await adapter.execute(task)
        if not result.success and result.meta.get("status_code"):
            raise HTTPException(result.meta["status_code"], result.error)
        payload = result.to_dict()
        payload["layer"] = {
            "id": row.get("id"),
            "name": row.get("name"),
            "display_name": row.get("display_name"),
            "layer_type": row.get("layer_type"),
        }
        return payload
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Execute task failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="执行服务暂不可用") from None
