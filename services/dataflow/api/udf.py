"""UDF 管理 API — SQL 表达式 UDF 的注册、版本与依赖检查。

权限口径：身份只信服务端；写操作受权限码中间件 `udf:manage` 门控。
校验失败显式报错（表达式必须纯表达式：禁子查询/表引用/未注册函数）。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from services.shared.common.auth import resolve_current_user

from services.dataflow.dag.udf_registry import udf_registry, UdfValidationError

logger = logging.getLogger(__name__)


async def _udf_access(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        request.state.current_user = resolve_current_user(header[7:])


router = APIRouter(dependencies=[Depends(_udf_access)])


class UdfCreate(BaseModel):
    name: str
    expression: str
    params: list = []
    return_type: str = "auto"
    description: str = ""


class UdfUpdate(BaseModel):
    expression: Optional[str] = None
    params: Optional[list] = None
    return_type: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class UdfToggle(BaseModel):
    is_active: bool


@router.get("/udfs")
def list_udfs(include_history: bool = False):
    return {"items": udf_registry.list_udfs(include_history=include_history)}


@router.post("/udfs")
def create_udf(req: UdfCreate, request: Request):
    user = request.state.current_user
    try:
        udf_id = udf_registry.create_udf(
            req.model_dump(), owner_id=int(user["user_id"]))
    except UdfValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": udf_id, "name": req.name.lower()}


@router.put("/udfs/{name}")
def update_udf(name: str, req: UdfUpdate):
    data = req.model_dump(exclude_unset=True)
    try:
        if set(data.keys()) <= {"is_active"}:
            # 仅启停走轻量路径
            if "is_active" in data:
                udf_registry.set_active(name, bool(data["is_active"]))
            return {"success": True}
        udf_registry.update_udf(name, data)
    except UdfValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "version": (udf_registry.get_active(name) or {}).get("version")}


@router.post("/udfs/{name}/toggle")
def toggle_udf(name: str, req: UdfToggle):
    if not udf_registry.get_active(name):
        raise HTTPException(status_code=404, detail=f"UDF '{name}' 不存在")
    udf_registry.set_active(name, req.is_active)
    return {"success": True, "is_active": req.is_active}


@router.delete("/udfs/{name}")
def delete_udf(name: str):
    try:
        return udf_registry.delete_udf(name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/udfs/{name}/dependents")
def udf_dependents(name: str):
    """被引用检查（删除保护依据）：引用该 UDF 的工作流/节点清单。"""
    return {"items": udf_registry.dependents(name)}
