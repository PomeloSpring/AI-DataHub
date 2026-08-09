"""Workflow API — Workflow + Prompt CRUD.

Migrated from backend/api/admin_workflow.py
Tables: adh_workflow_configs, adh_workflow_steps, adh_workflow_edges,
        adh_prompts, adh_prompt_versions, adh_workflow_logs
"""

import json
import logging
import os
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from services.aiplatform.services.workflow_service import (
    list_prompts, get_prompt, create_prompt, update_prompt,
    get_prompt_versions, rollback_prompt,
    list_workflows, get_workflow, create_workflow, update_workflow, delete_workflow,
    list_workflow_logs, get_workflow_log,
)

logger = logging.getLogger(__name__)
router = APIRouter()

DATAMIND_URL = os.getenv("DATAMIND_SERVICE_URL", "http://127.0.0.1:8001")


# ── Request models ────────────────────────────────────────────────────

class PromptCreate(BaseModel):
    prompt_key: str
    prompt_name: str = ""
    system_prompt: str = ""
    user_prompt_template: str = ""
    description: str = ""
    change_log: str = ""


class PromptUpdate(BaseModel):
    prompt_name: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    description: Optional[str] = None
    change_log: str = ""


class WorkflowConfigCreate(BaseModel):
    name: str
    description: str = ""
    is_active: bool = True
    is_default: bool = False
    workflow_type: str = "linear"
    dag_config: Optional[str] = None
    steps: list = []
    edges: list = []


class WorkflowConfigUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    workflow_type: Optional[str] = None
    dag_config: Optional[str] = None
    steps: Optional[list] = None
    edges: Optional[list] = None


class WorkflowExecuteRequest(BaseModel):
    question: str
    pipeline_mode: str = "deep"


# ── Prompt Management ─────────────────────────────────────────────────

@router.get("/prompts")
def api_list_prompts(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query(""),
):
    """List prompts."""
    try:
        return list_prompts(page=page, size=size, search=search)
    except Exception as e:
        logger.error("List prompts failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/prompts/{prompt_key}")
def api_get_prompt(prompt_key: str):
    """Get prompt by key."""
    try:
        row = get_prompt(prompt_key)
        if not row:
            raise HTTPException(status_code=404, detail=f"Prompt '{prompt_key}' not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/prompts")
def api_create_prompt(req: PromptCreate):
    """Create a new prompt."""
    try:
        result = create_prompt(req.model_dump())
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Create prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/prompts/{prompt_key}")
def api_update_prompt(prompt_key: str, req: PromptUpdate):
    """Update prompt (creates new version)."""
    try:
        result = update_prompt(prompt_key, req.model_dump(exclude_none=True))
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Update prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/prompts/{prompt_key}/versions")
def api_get_prompt_versions(prompt_key: str):
    """Get prompt version history."""
    try:
        items = get_prompt_versions(prompt_key)
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error("Get prompt versions failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/prompts/{prompt_key}/rollback")
def api_rollback_prompt(prompt_key: str, version: int):
    """Rollback prompt to a specific version."""
    try:
        result = rollback_prompt(prompt_key, version)
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Rollback prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Workflow Management ──────────────────────────────────────────────

@router.get("/workflows")
def api_list_workflows(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query(""),
):
    """List workflows."""
    try:
        return list_workflows(page=page, size=size, search=search)
    except Exception as e:
        logger.error("List workflows failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows/{workflow_id}")
def api_get_workflow(workflow_id: int):
    """Get workflow details."""
    try:
        wf = get_workflow(workflow_id)
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return wf
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get workflow failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflows")
def api_create_workflow(req: WorkflowConfigCreate):
    """Create a new workflow."""
    try:
        workflow_id = create_workflow(req.model_dump())
        return {"success": True, "id": workflow_id}
    except Exception as e:
        logger.error("Create workflow failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/workflows/{workflow_id}")
def api_update_workflow(workflow_id: int, req: WorkflowConfigUpdate):
    """Update workflow config, steps and edges."""
    try:
        ok = update_workflow(workflow_id, req.model_dump(exclude_none=True))
        if not ok:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {"success": True, "id": workflow_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update workflow failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workflows/{workflow_id}/execute")
def api_execute_workflow(workflow_id: int, req: WorkflowExecuteRequest, request: Request):
    """Test-execute a workflow by forwarding to the DataMind pipeline.

    Consumes the DataMind SSE stream and aggregates it into a single
    JSON result: {reply, sql, steps}.
    """
    wf = get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth

    payload = {
        "question": req.question,
        "pipeline_mode": req.pipeline_mode,
        "workflow_id": workflow_id,
    }

    try:
        resp = requests.post(
            f"{DATAMIND_URL}/api/pipeline/execute",
            headers=headers,
            data=json.dumps(payload),
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Workflow execute: datamind call failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Pipeline execution failed: {e}")

    reply, sql, steps, error = None, None, [], None
    current_event = None
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if current_event == "done":
                reply = data.get("reply")
                sql = data.get("sql")
                error = data.get("error")
            elif current_event in ("progress", "step", "thinking"):
                steps.append({
                    "step_name": data.get("stage") or data.get("step") or current_event,
                    "result": data.get("message") or data.get("content") or data.get("result"),
                })
            current_event = None

    result = {"reply": reply, "sql": sql, "steps": steps}
    if error and not reply:
        result["error"] = error
    return result


@router.delete("/workflows/{workflow_id}")
def api_delete_workflow(workflow_id: int):
    """Delete a workflow."""
    try:
        ok = delete_workflow(workflow_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete workflow failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Workflow Logs ────────────────────────────────────────────────────

@router.get("/workflow-logs")
def api_list_workflow_logs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    workflow_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
):
    """List workflow execution logs."""
    try:
        return list_workflow_logs(page=page, size=size, workflow_id=workflow_id, status=status)
    except Exception as e:
        logger.error("List workflow logs failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflow-logs/{log_id}")
def api_get_workflow_log(log_id: int):
    """Get workflow log details."""
    try:
        row = get_workflow_log(log_id)
        if not row:
            raise HTTPException(status_code=404, detail="Log not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get workflow log failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
