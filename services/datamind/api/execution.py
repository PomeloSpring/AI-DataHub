"""Execution Dispatch API — 执行层任务派发.

业务逻辑位于 services.datamind.execution(适配器/管理器),
本路由负责参数校验与派发委托。
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
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
    user_id: int = 0
    username: str = ""
    timeout: int = 300
    attachments: list[str] = []  # 多模态附件 ID 列表


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/layers/{workspace_id}")
def get_workspace_layers(workspace_id: int):
    """获取工作空间可用的执行层(含绑定关系)."""
    from services.datamind.execution import service

    try:
        return service.get_workspace_layers(workspace_id)
    except Exception as e:
        logger.error("Get workspace execution layers failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute")
async def execute_task(req: ExecuteRequest):
    """向执行层派发任务.

    指定 execution_layer_id 时使用对应执行层,
    否则使用工作空间默认执行层(未绑定时回退内置执行层)。
    """
    from services.datamind.execution import service
    from services.datamind.execution.manager import get_execution_layer_manager
    from services.datamind.execution.models import ExecutionContext, ExecutionTask

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")

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
        raise HTTPException(status_code=500, detail=str(e))

    # 加载多模态附件,以文件路径清单透传给适配器
    task_attachments = []
    if req.attachments:
        try:
            from services.datamind.multimodal.loader import load_attachments
            task_attachments = [
                {
                    "id": a["id"],
                    "filename": a["filename"],
                    "category": a["category"],
                    "path": a["storage_path"],
                }
                for a in load_attachments(req.attachments, req.user_id)
            ]
        except Exception as e:
            logger.warning("Load attachments for execution task failed: %s", e)

    task = ExecutionTask(
        task_id=uuid.uuid4().hex[:16],
        question=req.question,
        history=req.history,
        context=ExecutionContext(
            workspace_id=req.workspace_id,
            datasource_id=req.datasource_id or 0,
            user_id=req.user_id,
            username=req.username,
            model_id=req.model_id,
        ),
        timeout=req.timeout,
        attachments=task_attachments,
    )

    try:
        adapter = manager.build_adapter(row)
        result = await adapter.execute(task)
        payload = result.to_dict()
        payload["layer"] = {
            "id": row.get("id"),
            "name": row.get("name"),
            "display_name": row.get("display_name"),
            "layer_type": row.get("layer_type"),
        }
        return payload
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Execute task failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
