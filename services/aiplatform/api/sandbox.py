"""Sandbox API — Sandbox environment management (CRUD + connection test + code execution).

Provides endpoints for managing sandbox execution backends (local/ssh/fc)
and executing Python code in isolated containers.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.auth import require_admin
from services.aiplatform.services.sandbox_service import sandbox_service, SANDBOX_TYPES

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request Models ────────────────────────────────────────────────

class SandboxCreate(BaseModel):
    name: str
    sandbox_type: str  # "local" | "ssh" | "fc"
    display_name: str = ""
    description: str = ""
    config: dict = {}
    is_default: bool = False
    is_active: bool = True


class SandboxUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/types")
def list_sandbox_types():
    """获取支持的沙箱类型及其配置 schema。"""
    result = []
    for key, info in SANDBOX_TYPES.items():
        result.append({
            "key": key,
            "label": info["label"],
            "description": info["description"],
            "config_schema": info["config_schema"],
        })
    return result


@router.get("/")
def list_sandboxes(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    sandbox_type: str = Query("", description="按类型筛选"),
    search: str = Query("", description="搜索名称"),
    _user=Depends(require_admin),
):
    """列出沙箱环境（分页）。"""
    return sandbox_service.list_sandboxes(
        page=page, size=size, sandbox_type=sandbox_type, search=search,
    )


# ── Execution Logs (must be before /{sandbox_id} to avoid route conflict) ──

@router.get("/logs")
def list_execution_logs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    sandbox_id: int = Query(0, description="按沙箱筛选"),
    _user=Depends(require_admin),
):
    """查询沙箱执行日志。"""
    return sandbox_service.list_logs(
        sandbox_id=sandbox_id,
        page=page,
        size=size,
    )


@router.get("/logs/{log_id}")
def get_execution_log(log_id: int, _user=Depends(require_admin)):
    """获取单条执行日志详情。"""
    log = sandbox_service.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    return log


@router.get("/{sandbox_id}")
def get_sandbox(sandbox_id: int, _user=Depends(require_admin)):
    """获取单个沙箱详情。"""
    sandbox = sandbox_service.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail="沙箱不存在")
    return sandbox


@router.post("/")
def create_sandbox(body: SandboxCreate, _user=Depends(require_admin)):
    """创建沙箱环境。"""
    # Validate sandbox_type
    if body.sandbox_type not in SANDBOX_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的沙箱类型: {body.sandbox_type}")

    # Validate name uniqueness
    existing = sandbox_service.list_sandboxes(search=body.name, size=1)
    for item in existing.get("items", []):
        if item["name"] == body.name:
            raise HTTPException(status_code=400, detail=f"沙箱名称已存在: {body.name}")

    try:
        new_id = sandbox_service.create_sandbox(body.model_dump())
        return {"id": new_id, "message": "创建成功"}
    except Exception as e:
        logger.error(f"Failed to create sandbox: {e}")
        raise HTTPException(status_code=500, detail=f"创建失败: {e}")


@router.put("/{sandbox_id}")
def update_sandbox(sandbox_id: int, body: SandboxUpdate, _user=Depends(require_admin)):
    """更新沙箱环境。"""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    ok = sandbox_service.update_sandbox(sandbox_id, data)
    if not ok:
        raise HTTPException(status_code=404, detail="沙箱不存在")
    return {"message": "更新成功"}


@router.delete("/{sandbox_id}")
def delete_sandbox(sandbox_id: int, _user=Depends(require_admin)):
    """删除沙箱环境。"""
    ok = sandbox_service.delete_sandbox(sandbox_id)
    if not ok:
        raise HTTPException(status_code=404, detail="沙箱不存在")
    return {"message": "删除成功"}


@router.post("/{sandbox_id}/test")
def test_sandbox(sandbox_id: int, _user=Depends(require_admin)):
    """测试沙箱连接。"""
    sandbox = sandbox_service.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail="沙箱不存在")

    result = sandbox_service.test_connection(sandbox_id)

    # Update status based on test result
    new_status = "ready" if result.get("success") else "error"
    update_data = {"is_active": sandbox["is_active"]}  # keep current active state
    sandbox_service.update_sandbox(sandbox_id, update_data)

    # Update resource_info if test returned it
    if result.get("resource_info"):
        from backend.common.db.metadata_db import get_metadata_conn
        import json
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_sandbox_environments SET resource_info = %s, status = %s WHERE id = %s",
                    (json.dumps(result["resource_info"], ensure_ascii=False), new_status, sandbox_id),
                )
                conn.commit()
        finally:
            conn.close()
    else:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_sandbox_environments SET status = %s WHERE id = %s",
                    (new_status, sandbox_id),
                )
                conn.commit()
        finally:
            conn.close()

    return result


@router.put("/{sandbox_id}/default")
def set_default_sandbox(sandbox_id: int, _user=Depends(require_admin)):
    """设为默认沙箱。"""
    ok = sandbox_service.set_default(sandbox_id)
    if not ok:
        raise HTTPException(status_code=404, detail="沙箱不存在")
    return {"message": "已设为默认"}


# ── Code Execution ─────────────────────────────────────────────────

class CodeExecuteRequest(BaseModel):
    code: str                          # Python source code
    requirements: Optional[list] = None  # pip packages to install
    timeout: Optional[int] = None      # Override sandbox timeout
    sandbox_name: Optional[str] = None # Sandbox name, None = default


@router.post("/execute")
def execute_code(body: CodeExecuteRequest, _user=Depends(require_admin)):
    """在沙箱中执行 Python 代码。

    安全限制:
    - 代码级: AST 分析禁止危险操作（系统调用、网络、文件写入）
    - 容器级: Docker 隔离（内存限制、CPU 限制、只读文件系统、无网络）
    """
    from services.aiplatform.services.sandbox_executor import SandboxExecutor

    # Resolve sandbox
    if body.sandbox_name:
        sandboxes = sandbox_service.list_sandboxes(search=body.sandbox_name, size=1)
        sandbox = None
        for item in sandboxes.get("items", []):
            if item["name"] == body.sandbox_name:
                sandbox = item
                break
        if not sandbox:
            raise HTTPException(status_code=404, detail=f"沙箱不存在: {body.sandbox_name}")
    else:
        sandbox = sandbox_service.get_default_sandbox()
        if not sandbox:
            raise HTTPException(status_code=400, detail="未配置默认沙箱")

    if not sandbox.get("is_active"):
        raise HTTPException(status_code=400, detail=f"沙箱已禁用: {sandbox['name']}")

    # Execute
    executor = SandboxExecutor(sandbox)
    result = executor.execute(
        code=body.code,
        requirements=body.requirements,
        timeout=body.timeout,
    )

    return result


@router.post("/{sandbox_id}/execute")
def execute_code_in_sandbox(sandbox_id: int, body: CodeExecuteRequest, _user=Depends(require_admin)):
    """在指定沙箱中执行 Python 代码。"""
    from services.aiplatform.services.sandbox_executor import SandboxExecutor

    sandbox = sandbox_service.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail="沙箱不存在")

    if not sandbox.get("is_active"):
        raise HTTPException(status_code=400, detail=f"沙箱已禁用: {sandbox['name']}")

    executor = SandboxExecutor(sandbox)
    result = executor.execute(
        code=body.code,
        requirements=body.requirements,
        timeout=body.timeout,
    )

    return result
