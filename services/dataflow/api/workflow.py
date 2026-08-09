"""Workflow API — Execute and monitor workflows via Airflow.

Supports ad-hoc workflow execution and status tracking.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.dataflow.services.airflow_client import airflow_client

logger = logging.getLogger(__name__)
router = APIRouter()


class WorkflowExecuteRequest(BaseModel):
    dag_id: str
    conf: dict = {}
    run_id: Optional[str] = None


class WorkflowStatusResponse(BaseModel):
    dag_id: str
    run_id: str
    state: str
    execution_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@router.post("/execute")
def execute_workflow(req: WorkflowExecuteRequest):
    """Trigger execution of an Airflow DAG/workflow."""
    try:
        result = airflow_client.trigger_dag(
            dag_id=req.dag_id,
            conf=req.conf,
            run_id=req.run_id,
        )
        return {
            "success": True,
            "dag_id": req.dag_id,
            "dag_run_id": result.get("dag_run_id", result.get("execution_date")),
            "state": result.get("state", "queued"),
        }
    except Exception as e:
        logger.error("Failed to execute workflow %s: %s", req.dag_id, e)
        raise HTTPException(status_code=502, detail=f"Workflow execution failed: {e}")


@router.get("/{dag_id}/status")
def get_workflow_status(dag_id: str, run_id: Optional[str] = None):
    """Get the status of a workflow (DAG run).

    If run_id is not provided, returns the latest run.
    """
    try:
        if run_id:
            result = airflow_client.get_dag_run_status(dag_id, run_id)
        else:
            # Get latest run
            dag_info = airflow_client.get_dag_info(dag_id)
            if not dag_info:
                raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")

            runs = airflow_client.list_dag_runs(dag_id, limit=1)
            if not runs:
                return {"dag_id": dag_id, "state": "no_runs", "message": "No runs found"}

            latest = runs[0]
            result = {
                "dag_id": dag_id,
                "run_id": latest.get("dag_run_id", latest.get("execution_date")),
                "state": latest.get("state", "unknown"),
                "execution_date": latest.get("execution_date"),
                "start_date": latest.get("start_date"),
                "end_date": latest.get("end_date"),
            }

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get workflow status for %s: %s", dag_id, e)
        raise HTTPException(status_code=502, detail=f"Failed to get workflow status: {e}")
