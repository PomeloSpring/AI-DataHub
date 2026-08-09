"""Sync Task API — Data sync task management with Airflow DAG integration.

Tables: adh_sync_tasks, adh_sync_logs
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from services.dataflow.services.sync_service import sync_service
from services.dataflow.services.dag_generator import dag_generator

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
    """Create a sync task and generate its Airflow DAG."""
    # Validate sync_mode
    if req.sync_mode not in ("full", "incremental"):
        raise HTTPException(status_code=400, detail="sync_mode must be 'full' or 'incremental'")

    # Generate Airflow DAG
    dag_id = dag_generator.generate_sync_dag({
        "name": req.name,
        "source_type": req.source_type,
        "source_config": req.source_config,
        "target_type": req.target_type,
        "target_config": req.target_config,
        "sync_mode": req.sync_mode,
        "schedule": req.schedule,
        "task_config": req.task_config,
    })

    # Persist to DB
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
    """Update a sync task. Regenerates DAG if config changed."""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sync task not found")

    data = req.model_dump(exclude_unset=True)

    # If source/target config changed, regenerate DAG
    if "source_config" in data or "target_config" in data or "sync_mode" in data:
        dag_config = {
            "name": data.get("name", existing["name"]),
            "source_type": existing["source_type"],
            "source_config": data.get("source_config", existing["source_config"]),
            "target_type": existing["target_type"],
            "target_config": data.get("target_config", existing["target_config"]),
            "sync_mode": data.get("sync_mode", existing["sync_mode"]),
            "schedule": data.get("schedule", existing.get("schedule")),
            "task_config": data.get("task_config", existing.get("task_config", {})),
        }
        dag_id = dag_generator.generate_sync_dag(dag_config)
        data["dag_id"] = dag_id

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
    """Trigger sync task execution via Airflow DAG."""
    existing = sync_service.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sync task not found")

    dag_id = existing.get("dag_id")
    if not dag_id:
        raise HTTPException(status_code=400, detail="No DAG configured for this task")

    from services.dataflow.services.airflow_client import airflow_client

    try:
        result = airflow_client.trigger_dag(
            dag_id=dag_id,
            conf={"task_id": task_id, "triggered_by": "manual"},
        )
        run_id = result.get("dag_run_id", result.get("execution_date", ""))

        # Log execution
        log_id = sync_service.create_log(
            task_id=task_id,
            dag_run_id=run_id,
            status="running",
        )
        return {"success": True, "dag_run_id": run_id, "log_id": log_id}
    except Exception as e:
        logger.error("Failed to trigger DAG %s: %s", dag_id, e)
        raise HTTPException(status_code=502, detail=f"Airflow trigger failed: {e}")


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
