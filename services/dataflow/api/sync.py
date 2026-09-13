"""Sync Task API — Data sync task management with Airflow DAG integration.

Tables: adh_sync_tasks, adh_sync_logs
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from services.dataflow.services.sync_service import sync_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════════════════
# Request / Response Models
# ════════════════════════════════════════════════════════════════════


class SyncTaskCreate(BaseModel):
    name: str
    description: str = ""
    source_type: str  # mysql, postgres, api, file
    source_config: dict
    target_type: str  # doris, mysql, es
    target_config: dict
    sync_mode: str = "full"  # full, incremental
    schedule: Optional[str] = None  # cron expression
    task_config: dict = {}


class SyncTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_config: Optional[dict] = None
    target_config: Optional[dict] = None
    sync_mode: Optional[str] = None
    schedule: Optional[str] = None
    task_config: Optional[dict] = None
    is_active: Optional[int] = None


class SyncTaskResponse(BaseModel):
    id: int
    name: str
    description: str
    source_type: str
    source_config: dict
    target_type: str
    target_config: dict
    sync_mode: str
    schedule: Optional[str]
    dag_id: Optional[str]
    task_config: dict
    is_active: int
    status: str
    created_at: str
    updated_at: str


# ════════════════════════════════════════════════════════════════════
# Sync Task CRUD
# ════════════════════════════════════════════════════════════════════


@router.get("/tasks")
def list_sync_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    """List sync tasks with pagination."""
    return sync_service.list_tasks(page=page, size=size, status=status)


@router.post("/tasks")
def create_sync_task(req: SyncTaskCreate, background_tasks: BackgroundTasks):
    """Create a sync task."""
    # Validate sync_mode
    if req.sync_mode not in ("full", "incremental"):
        raise HTTPException(status_code=400, detail="sync_mode must be 'full' or 'incremental'")

    # Persist to DB
    dag_id = f"sync_{req.source_type}_{req.name}".replace(" ", "_").lower()
    task_id = sync_service.create_task(
        data=req.model_dump(),
        dag_id=dag_id,
    )
    return {"id": task_id, "dag_id": dag_id}


@router.get("/tasks/{task_id}")
def get_sync_task(task_id: int):
    """Get a single sync task by ID."""
    task = sync_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Sync task not found")
    return task


@router.put("/tasks/{task_id}")
def update_sync_task(task_id: int, req: SyncTaskUpdate):
    """Update a sync task."""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sync task not found")

    data = req.model_dump(exclude_unset=True)
    success = sync_service.update_task(task_id, data)
    return {"success": success}


@router.delete("/tasks/{task_id}")
def delete_sync_task(task_id: int):
    """Delete a sync task and its logs."""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sync task not found")

    sync_service.delete_task(task_id)
    return {"success": True}


@router.post("/tasks/{task_id}/run")
async def trigger_sync_execution(task_id: int, background_tasks: BackgroundTasks):
    """Trigger sync task execution."""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sync task not found")

    # Log execution
    log_id = sync_service.create_log(
        task_id=task_id,
        dag_run_id=f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        status="running",
    )
    return {"success": True, "log_id": log_id}


@router.get("/tasks/{task_id}/logs")
def get_sync_task_logs(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """Get execution logs for a sync task."""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sync task not found")

    return sync_service.list_logs(task_id=task_id, page=page, size=size)


@router.get("/logs")
def get_all_sync_logs(
    task_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """Get all sync execution logs, optionally filtered by task."""
    return sync_service.list_logs(task_id=task_id, page=page, size=size)
