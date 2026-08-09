"""Execution Layers API — 执行层配置管理.

Table: adh_execution_layers / adh_workspace_execution_layers
业务逻辑位于 services.datamind.execution(适配器/发现/CRUD),
本路由仅做参数校验与委托。
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.datamind.execution import service as exec_service
from services.datamind.execution.manager import get_execution_layer_manager

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_LAYER_TYPES = ("builtin", "cli", "docker", "remote")
VALID_STATUSES = ("active", "inactive", "error")


# ── Request Models ────────────────────────────────────────────────

class ExecutionLayerCreate(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    layer_type: str = "cli"
    config: dict = {}
    status: str = "active"


class ExecutionLayerUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    layer_type: Optional[str] = None
    config: Optional[dict] = None
    status: Optional[str] = None


class WorkspaceBinding(BaseModel):
    execution_layer_id: int
    is_default: bool = False
    priority: int = 0
    allowed_tools: list[str] = []  # tools 权限白名单,空表示不限制


class WorkspaceLayersUpdate(BaseModel):
    bindings: list[WorkspaceBinding] = []


# ── 静态路径(必须先于 /{layer_id} 定义) ─────────────────────────

@router.get("/discover")
async def discover_clis():
    """自动发现物理机上的已知 CLI 工具(opencode / qoder 等)."""
    try:
        from services.datamind.execution.discovery import CLIDiscovery

        found = await CLIDiscovery().discover()
        return [c.to_dict() for c in found]
    except Exception as e:
        logger.error("Discover CLIs failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tools")
async def list_tool_catalog():
    """工具目录 — 供工作空间配置执行层时选择允许使用的 tools."""
    from services.datamind.execution.tool_catalog import TOOL_CATALOG

    return TOOL_CATALOG


@router.get("/models")
async def list_cli_models(layer_id: Optional[int] = None, cli_name: str = ""):
    """查询执行层可用模型列表.

    优先按 layer_id 使用已配置执行层(含 env/路径),
    否则按 cli_name 构建临时适配器查询。
    """
    try:
        manager = get_execution_layer_manager()
        if layer_id:
            adapter = manager.get_adapter_by_id(layer_id)
        elif cli_name:
            adapter = manager.build_adapter({
                "name": f"tmp-{cli_name}",
                "layer_type": "cli",
                "config": {"cli_name": cli_name},
            })
        else:
            raise HTTPException(status_code=400, detail="需要 layer_id 或 cli_name")
        models = await adapter.list_models()
        return {"models": models}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("List execution layer models failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 执行层 CRUD ────────────────────────────────────────────────────

@router.get("")
def list_execution_layers():
    """列出所有执行层."""
    try:
        return exec_service.list_layers()
    except Exception as e:
        logger.error("List execution layers failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
def create_execution_layer(req: ExecutionLayerCreate):
    """创建执行层."""
    try:
        if req.layer_type not in VALID_LAYER_TYPES:
            raise HTTPException(status_code=400, detail=f"不支持的执行层类型: {req.layer_type}")
        if req.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"不支持的状态: {req.status}")
        if exec_service.get_layer_by_name(req.name):
            raise HTTPException(status_code=400, detail=f"执行层名称已存在: {req.name}")
        # CLI 类型必须指定 cli_name
        if req.layer_type == "cli" and not (req.config or {}).get("cli_name"):
            raise HTTPException(status_code=400, detail="CLI 执行层必须配置 cli_name")
        layer_id = exec_service.create_layer(req.model_dump())
        return {"id": layer_id, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create execution layer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspaces/{workspace_id}")
def get_workspace_execution_layers(workspace_id: int):
    """获取工作空间绑定的执行层."""
    try:
        return exec_service.get_workspace_layers(workspace_id)
    except Exception as e:
        logger.error("Get workspace execution layers failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/workspaces/{workspace_id}")
def update_workspace_execution_layers(workspace_id: int, req: WorkspaceLayersUpdate):
    """配置工作空间的执行层绑定(每个工作空间只允许配置一个执行层)."""
    try:
        if len(req.bindings) > 1:
            raise HTTPException(status_code=400, detail="每个工作空间只允许配置一个执行层")
        for b in req.bindings:
            if not exec_service.get_layer(b.execution_layer_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"执行层不存在: id={b.execution_layer_id}",
                )
            # 单一绑定始终作为默认执行层
            b.is_default = True
        exec_service.set_workspace_layers(
            workspace_id, [b.model_dump() for b in req.bindings]
        )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update workspace execution layers failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspaces/{workspace_id}/execution-layer")
async def get_workspace_execution_layer(workspace_id: int):
    """获取工作空间生效的执行层(未绑定时回退内置层)及模型候选.

    chat 页面据此调整模型框:
    - builtin: 模型候选来自系统模型中心(model_source=system)
    - cli(qoder/opencode 等): 候选来自执行层 list_models(model_source=execution_layer)
    """
    try:
        manager = get_execution_layer_manager()
        row = await manager.resolve_workspace_layer(workspace_id)
        resp = {
            "layer_id": row.get("id"),
            "name": row.get("name"),
            "display_name": row.get("display_name") or row.get("name"),
            "layer_type": row.get("layer_type"),
            "allowed_tools": row.get("allowed_tools") or [],
            "model_source": "system",
            "models": [],
        }
        if row.get("layer_type") == "cli":
            resp["model_source"] = "execution_layer"
            try:
                adapter = manager.build_adapter(row)
                resp["models"] = await adapter.list_models()
            except Exception as e:
                logger.warning("List models for layer %s failed: %s", row.get("name"), e)
        return resp
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Get workspace execution layer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{layer_id}")
def get_execution_layer(layer_id: int):
    """获取单个执行层."""
    try:
        row = exec_service.get_layer(layer_id)
        if not row:
            raise HTTPException(status_code=404, detail="执行层不存在")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get execution layer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{layer_id}")
def update_execution_layer(layer_id: int, req: ExecutionLayerUpdate):
    """更新执行层."""
    try:
        if not exec_service.get_layer(layer_id):
            raise HTTPException(status_code=404, detail="执行层不存在")
        if req.layer_type is not None and req.layer_type not in VALID_LAYER_TYPES:
            raise HTTPException(status_code=400, detail=f"不支持的执行层类型: {req.layer_type}")
        if req.status is not None and req.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"不支持的状态: {req.status}")
        if req.name:
            existing = exec_service.get_layer_by_name(req.name)
            if existing and existing["id"] != layer_id:
                raise HTTPException(status_code=400, detail=f"执行层名称已存在: {req.name}")
        data = {k: v for k, v in req.model_dump().items() if v is not None}
        exec_service.update_layer(layer_id, data)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update execution layer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{layer_id}")
def delete_execution_layer(layer_id: int):
    """删除执行层(同时清理工作空间绑定)."""
    try:
        row = exec_service.get_layer(layer_id)
        if not row:
            raise HTTPException(status_code=404, detail="执行层不存在")
        if row.get("layer_type") == "builtin":
            raise HTTPException(status_code=400, detail="内置执行层不可删除")
        exec_service.delete_layer(layer_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete execution layer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{layer_id}/test")
async def test_execution_layer(layer_id: int):
    """测试执行层连通性(运行健康检查)."""
    try:
        row = exec_service.get_layer(layer_id)
        if not row:
            raise HTTPException(status_code=404, detail="执行层不存在")

        manager = get_execution_layer_manager()
        error_msg = ""
        health = None
        try:
            adapter = manager.build_adapter(row)
            health = await adapter.health_check()
        except ValueError as e:
            error_msg = str(e)

        if health is not None and health.healthy:
            exec_service.record_health(layer_id, "success", health.message)
            return {"success": True, "message": health.message, "details": health.details}

        msg = error_msg if health is None else health.message
        exec_service.record_health(layer_id, "failed", msg)
        return {"success": False, "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Test execution layer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
