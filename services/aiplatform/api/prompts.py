"""Prompts API — CRUD + Version History + Rollback.

Table: adh_prompts (main) + adh_prompt_versions (snapshots)
Uses PromptVersionStore from shared/common/versioned_config.py.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.shared.common.versioned_config import get_prompt_store
from services.shared.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request Models ─────────────────────────────────────────────────────

class PromptCreate(BaseModel):
    prompt_key: str = Field(..., min_length=1, max_length=100)
    prompt_name: str = Field(..., min_length=1, max_length=200)
    category: str = Field("skill", max_length=40)
    system_prompt: str = ""
    user_prompt_template: str = ""
    description: str = ""
    workspace_id: int = 0
    change_log: str = "Initial creation"
    created_by: str = "system"


class PromptUpdate(BaseModel):
    prompt_name: Optional[str] = None
    category: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    description: Optional[str] = None
    workspace_id: Optional[int] = None
    change_log: str = "Updated"
    updated_by: str = "system"


class RollbackRequest(BaseModel):
    version: int = Field(..., ge=1, description="Target version to rollback to")
    rolled_by: str = "system"


# ── Helpers ────────────────────────────────────────────────────────────

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_row(row: dict) -> dict:
    for k in ("created_at", "updated_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()
    return row


def _clear_prompt_cache():
    """Invalidate the datamind prompt TTL cache after a write.

    Note: services run in separate processes, so cross-process propagation
    relies on the loader TTL (PROMPT_CACHE_TTL, default 300s). This clears the
    local process cache so same-process readers see the change immediately.
    """
    try:
        from services.datamind.config.loader import clear_prompt_cache
        clear_prompt_cache()
    except Exception as e:  # never fail a write because of cache invalidation
        logger.debug("clear_prompt_cache skipped: %s", e)


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/")
def list_prompts(
    active_only: bool = Query(True),
    category: Optional[str] = Query(None, description="skill | role_style | permission_boundary | dialect"),
    workspace_id: Optional[int] = Query(None, description="0=global default; N=workspace override"),
):
    """List prompts with optional category / workspace_id filters."""
    try:
        store = get_prompt_store()
        rows = store.list_filtered(active_only=active_only, category=category,
                                   workspace_id=workspace_id)
        return [_fmt_row(r) for r in rows]
    except Exception as e:
        logger.error("List prompts failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{prompt_key}")
def get_prompt(prompt_key: str):
    """Get a prompt by key."""
    try:
        store = get_prompt_store()
        row = store.get(prompt_key)
        if not row:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return _fmt_row(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/id/{prompt_id}")
def get_prompt_by_id(prompt_id: int):
    """Get a prompt by ID."""
    try:
        store = get_prompt_store()
        row = store.get_by_id(prompt_id)
        if not row:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return _fmt_row(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get prompt by id failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
def create_prompt(req: PromptCreate):
    """Create a new prompt (scoped by prompt_key + workspace_id)."""
    try:
        # Check duplicate key within the same workspace scope
        existing = execute_query(
            "SELECT id FROM adh_prompts WHERE prompt_key = %s AND workspace_id = %s",
            (req.prompt_key, req.workspace_id), fetchone=True,
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"prompt_key '{req.prompt_key}' already exists for workspace_id={req.workspace_id}")

        store = get_prompt_store()
        new_id = store.create({
            "prompt_key": req.prompt_key,
            "prompt_name": req.prompt_name,
            "category": req.category,
            "system_prompt": req.system_prompt,
            "user_prompt_template": req.user_prompt_template,
            "description": req.description,
            "workspace_id": req.workspace_id,
            "change_log": req.change_log,
        }, created_by=req.created_by)

        _clear_prompt_cache()
        return {"id": new_id, "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{prompt_id}")
def update_prompt(prompt_id: int, req: PromptUpdate):
    """Update a prompt. Automatically creates a version snapshot."""
    try:
        store = get_prompt_store()
        data = {}
        for field in ("prompt_name", "category", "system_prompt", "user_prompt_template",
                      "description", "workspace_id", "change_log"):
            val = getattr(req, field, None)
            if val is not None:
                data[field] = val

        if not data:
            return {"success": True, "message": "No changes"}

        ok = store.update(prompt_id, data, updated_by=req.updated_by)
        if not ok:
            raise HTTPException(status_code=404, detail="Prompt not found")
        _clear_prompt_cache()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int):
    """Soft-delete (deactivate) a prompt."""
    try:
        store = get_prompt_store()
        store.delete(prompt_id)
        _clear_prompt_cache()
        return {"success": True}
    except Exception as e:
        logger.error("Delete prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Version History ────────────────────────────────────────────────────

@router.get("/{prompt_id}/versions")
def get_prompt_versions(prompt_id: int):
    """Get version history for a prompt."""
    try:
        store = get_prompt_store()
        versions = store.get_versions(prompt_id)
        return [_fmt_row(v) for v in versions]
    except Exception as e:
        logger.error("Get prompt versions failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{prompt_id}/rollback")
def rollback_prompt(prompt_id: int, req: RollbackRequest):
    """Rollback a prompt to a specific version."""
    try:
        store = get_prompt_store()
        ok = store.rollback(prompt_id, req.version, rolled_by=req.rolled_by)
        if not ok:
            raise HTTPException(status_code=404, detail="Prompt or target version not found")
        _clear_prompt_cache()
        return {"success": True, "message": f"Rolled back to version {req.version}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rollback prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{prompt_id}/activate")
def activate_prompt(prompt_id: int):
    """Activate a prompt (set as the active version for its key)."""
    try:
        store = get_prompt_store()
        ok = store.activate(prompt_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Prompt not found")
        _clear_prompt_cache()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Activate prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
