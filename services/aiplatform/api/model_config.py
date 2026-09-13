"""Model Config API — Manage LLM model and system configurations.

Migrated from backend/api/model_config.py
Tables: adh_llm_models, adh_system_config
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.aiplatform.services.model_config_service import (
    list_llm_models, create_llm_model, update_llm_model, delete_llm_model,
    set_default_model, get_llm_model_config, get_system_config,
    get_all_system_config, update_system_config,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ────────────────────────────────────────────────────

class LLMModelCreate(BaseModel):
    name: str
    provider: str = "anthropic"
    base_url: str
    api_key: str
    model_name: str
    max_tokens: int = 4096
    supports_thinking: bool = True


class LLMModelUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    max_tokens: Optional[int] = None
    supports_thinking: Optional[bool] = None


class SystemConfigUpdate(BaseModel):
    key: str
    value: str


# ── LLM Models CRUD ──────────────────────────────────────────────────

@router.get("/llm")
def api_list_llm_models():
    """List all LLM models (api_key masked)."""
    try:
        return list_llm_models()
    except Exception as e:
        logger.error("List LLM models failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/llm")
def api_create_llm_model(req: LLMModelCreate):
    """Add a new LLM model."""
    try:
        model_id = create_llm_model(req.model_dump())
        return {"success": True, "id": model_id}
    except Exception as e:
        logger.error("Create LLM model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/llm/{model_id}")
def api_update_llm_model(model_id: int, req: LLMModelUpdate):
    """Update an LLM model."""
    try:
        ok = update_llm_model(model_id, req.model_dump(exclude_none=True))
        if not ok:
            raise HTTPException(status_code=404, detail="Model not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update LLM model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/llm/{model_id}")
def api_delete_llm_model(model_id: int):
    """Delete an LLM model."""
    try:
        delete_llm_model(model_id)
        return {"success": True}
    except Exception as e:
        logger.error("Delete LLM model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/llm/{model_id}/default")
def api_set_default_model(model_id: int):
    """Set an LLM model as default."""
    try:
        ok = set_default_model(model_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Model not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Set default model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── System Config ──────────────────────────────────────────────────

@router.get("/system")
def api_get_all_system_config():
    """Get all system config values."""
    try:
        return get_all_system_config()
    except Exception as e:
        logger.error("Get system config failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/system")
def api_update_system_config(req: SystemConfigUpdate):
    """Update a system config value."""
    try:
        update_system_config(req.key, req.value)
        return {"success": True}
    except Exception as e:
        logger.error("Update system config failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
