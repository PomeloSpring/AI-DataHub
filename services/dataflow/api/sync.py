"""Sync Task API — 数据同步任务（单节点快捷形态）管理与执行。

Tables: adh_sync_tasks, adh_sync_logs

资源口径（UI 资源规范）：源/目标一律 `datasource_name` 引用 adh_datasources
（选择框选择，显示 name），请求与响应均不携带 datasource_id/host/账号/凭据。
执行走 sync_task_runner 真实数据搬运（治理读取 + 目标端批量写）。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from services.shared.common.auth import authorize_workspace, resolve_current_user

from services.dataflow.services.sync_service import sync_service, dispatch_sync_task

logger = logging.getLogger(__name__)

SYNC_MODES = ("full", "incremental")
WRITE_MODES = ("append", "overwrite")


async def _sync_access(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        user = resolve_current_user(header[7:])
        request.state.current_user = user
    params = request.path_params
    resource = None
    if params.get("task_id"):
        resource = sync_service.get_task(int(params["task_id"]))
        if resource is None:
            raise HTTPException(status_code=404, detail="同步任务不存在或无权访问")
    if resource:
        authorize_workspace(user, resource.get("workspace_id") or 0)
        if user.get("role") != "admin" and int(resource.get("owner_id") or 0) != int(user["user_id"]):
            raise HTTPException(status_code=404, detail="同步任务不存在或无权访问")
    else:
        ws = request.query_params.get("workspace_id") or request.headers.get("X-Workspace-Id")
        if ws is None and request.method in ("POST", "PUT"):
            try:
                body = await request.json()
                ws = body.get("workspace_id", 0) if isinstance(body, dict) else 0
            except ValueError:
                ws = 0
        authorize_workspace(user, ws or 0)


router = APIRouter(dependencies=[Depends(_sync_access)])


# ════════════════════════════════════════════════════════════════════
# Request Models（结构化配置；数据源用 name 引用，禁止手填连接信息）
# ════════════════════════════════════════════════════════════════════


class SyncTaskCreate(BaseModel):
    name: str
    description: str = ""
    source_datasource: str          # 源数据源名称（选择框，adh_datasources.name）
    source_table: str
    target_datasource: str          # 目标数据源名称
    target_table: str
    sync_mode: str = "full"         # full / incremental
    incremental_column: Optional[str] = None
    write_mode: str = "append"      # append / overwrite
    transform_sql: Optional[str] = None   # 可选转换 SELECT（可引用 UDF）
    udf_refs: Optional[list] = None       # UDF 引用（name 或 name:version）
    schedule_cron: Optional[str] = None
    timezone: Optional[str] = "Asia/Shanghai"
    workspace_id: int = 0
    timeout_seconds: int = 1800
    max_retries: int = 0


class SyncTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_datasource: Optional[str] = None
    source_table: Optional[str] = None
    target_datasource: Optional[str] = None
    target_table: Optional[str] = None
    sync_mode: Optional[str] = None
    incremental_column: Optional[str] = None
    write_mode: Optional[str] = None
    transform_sql: Optional[str] = None
    udf_refs: Optional[list] = None
    schedule_cron: Optional[str] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None


def _validate(req_dict: dict) -> None:
    """服务端校验（fail-loud）：模式合法 + 数据源可解析 + 表名非空 + 转换 SQL/UDF 合法。"""
    from services.dataflow.dag.dag_validator import validate_graph, DagValidationError
    config = {
        "source_datasource": req_dict.get("source_datasource"),
        "source_table": req_dict.get("source_table"),
        "target_datasource": req_dict.get("target_datasource"),
        "target_table": req_dict.get("target_table"),
        "sync_mode": req_dict.get("sync_mode", "full"),
        "incremental_column": req_dict.get("incremental_column"),
        "write_mode": req_dict.get("write_mode", "append"),
        "transform_sql": req_dict.get("transform_sql") or "",
        "udf_refs": req_dict.get("udf_refs") or [],
    }
    try:
        validate_graph({"nodes": [{"key": "sync", "type": "sync", "config": config}], "edges": []})
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ════════════════════════════════════════════════════════════════════
# CRUD
# ════════════════════════════════════════════════════════════════════


@router.get("/tasks")
def list_sync_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    return sync_service.list_tasks(page=page, size=size, status=status)


@router.post("/tasks")
def create_sync_task(req: SyncTaskCreate, request: Request):
    user = request.state.current_user
    data = req.model_dump()
    _validate(data)
    data["task_config"] = {
        "write_mode": req.write_mode,
        "transform_sql": req.transform_sql or "",
        "udf_refs": req.udf_refs or [],
    }
    task_id = sync_service.create_task(
        data=data,
        dag_id=f"sync_{req.source_datasource}_{req.name}".replace(" ", "_").lower(),
        owner_id=int(user["user_id"]),
        workspace_id=req.workspace_id,
    )
    return {"id": task_id}


@router.get("/tasks/{task_id}")
def get_sync_task(task_id: int):
    task = sync_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    return task


@router.put("/tasks/{task_id}")
def update_sync_task(task_id: int, req: SyncTaskUpdate):
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    data = req.model_dump(exclude_unset=True)
    merged = {**existing, **data}
    if any(key in data for key in ("source_datasource", "source_table", "target_datasource",
                                   "target_table", "sync_mode", "incremental_column", "write_mode")):
        _validate(merged)
    if "write_mode" in data or "transform_sql" in data or "udf_refs" in data:
        data["task_config"] = {
            **(existing.get("task_config") or {}),
            "write_mode": data.pop("write_mode", (existing.get("task_config") or {}).get("write_mode", "append")),
            "transform_sql": data.pop("transform_sql", (existing.get("task_config") or {}).get("transform_sql", "")),
            "udf_refs": data.pop("udf_refs", (existing.get("task_config") or {}).get("udf_refs", [])),
        }
    success = sync_service.update_task(task_id, data)
    return {"success": success}


@router.delete("/tasks/{task_id}")
def delete_sync_task(task_id: int):
    sync_service.delete_task(task_id)
    return {"success": True}


@router.patch("/tasks/{task_id}/toggle")
def toggle_sync_task(task_id: int, is_active: bool = Query(...)):
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    sync_service.update_task(task_id, {"is_active": 1 if is_active else 0})
    return {"task_id": task_id, "is_active": is_active}


@router.post("/tasks/{task_id}/run")
def trigger_sync_execution(task_id: int):
    """手动触发执行（派发进队列，执行记录落 adh_sync_logs）。"""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    try:
        result = dispatch_sync_task(task_id, trigger_type="manual")
    except Exception as exc:
        logger.exception("sync task %s 手动触发失败", task_id)
        raise HTTPException(status_code=503, detail=str(exc))
    return {"success": True, **result}


# ════════════════════════════════════════════════════════════════════
# Execution Logs
# ════════════════════════════════════════════════════════════════════


@router.get("/tasks/{task_id}/logs")
def get_sync_task_logs(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    return sync_service.list_logs(task_id=task_id, page=page, size=size)


@router.get("/logs")
def get_all_sync_logs(
    task_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    return sync_service.list_logs(task_id=task_id, page=page, size=size)
