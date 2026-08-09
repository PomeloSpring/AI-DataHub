"""Model Config API — Manage LLM and Embedding model configurations.

Migrated from backend/api/model_config.py
Table: adh_llm_models, adh_system_config
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


class LLMModelCreate(BaseModel):
    name: str
    provider: str = "anthropic"
    base_url: str
    api_key: str
    model_name: str
    context_window: int = 200000
    max_tokens: int = 4096
    supports_thinking: bool = True
    is_default: bool = False


class LLMModelUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    supports_thinking: Optional[bool] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.get("/llm")
def list_models():
    """List all LLM models."""
    try:
        rows = execute_query(
            "SELECT id, name, provider, base_url, model_name, context_window, "
            "max_tokens, supports_thinking, is_default, is_active, created_at, updated_at "
            "FROM adh_llm_models ORDER BY is_default DESC, name"
        )
        for r in rows:
            for k in ("created_at", "updated_at"):
                if hasattr(r.get(k), "isoformat"):
                    r[k] = r[k].isoformat()
        return rows
    except Exception as e:
        logger.error("List models failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm/{model_id}")
def get_model(model_id: int):
    """Get model by ID."""
    try:
        row = execute_query(
            "SELECT id, name, provider, base_url, model_name, context_window, "
            "max_tokens, supports_thinking, is_default, is_active, created_at, updated_at "
            "FROM adh_llm_models WHERE id = %s",
            (model_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/llm")
def create_model(req: LLMModelCreate):
    """Create a new LLM model config."""
    try:
        now = _now()
        model_id = execute_insert(
            """INSERT INTO adh_llm_models
               (name, provider, base_url, api_key, model_name, context_window,
                max_tokens, supports_thinking, is_default, is_active, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)""",
            (req.name, req.provider, req.base_url, req.api_key, req.model_name,
             req.context_window, req.max_tokens, 1 if req.supports_thinking else 0,
             1 if req.is_default else 0, now, now),
        )

        # If set as default, unset others
        if req.is_default:
            execute_write("UPDATE adh_llm_models SET is_default = 0 WHERE id != %s", (model_id,))

        return {"id": model_id, "success": True}
    except Exception as e:
        logger.error("Create model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/llm/{model_id}")
def update_model(model_id: int, req: LLMModelUpdate):
    """Update LLM model config."""
    try:
        updates = []
        params = []

        if req.name is not None:
            updates.append("name = %s")
            params.append(req.name)
        if req.provider is not None:
            updates.append("provider = %s")
            params.append(req.provider)
        if req.base_url is not None:
            updates.append("base_url = %s")
            params.append(req.base_url)
        if req.api_key is not None:
            updates.append("api_key = %s")
            params.append(req.api_key)
        if req.model_name is not None:
            updates.append("model_name = %s")
            params.append(req.model_name)
        if req.context_window is not None:
            updates.append("context_window = %s")
            params.append(req.context_window)
        if req.max_tokens is not None:
            updates.append("max_tokens = %s")
            params.append(req.max_tokens)
        if req.supports_thinking is not None:
            updates.append("supports_thinking = %s")
            params.append(1 if req.supports_thinking else 0)
        if req.is_default is not None:
            updates.append("is_default = %s")
            params.append(1 if req.is_default else 0)
        if req.is_active is not None:
            updates.append("is_active = %s")
            params.append(1 if req.is_active else 0)

        if not updates:
            return {"success": True, "message": "No changes"}

        updates.append("updated_at = %s")
        params.append(_now())
        params.append(model_id)

        execute_write(f"UPDATE adh_llm_models SET {', '.join(updates)} WHERE id = %s", params)

        # If set as default, unset others
        if req.is_default:
            execute_write("UPDATE adh_llm_models SET is_default = 0 WHERE id != %s", (model_id,))

        return {"success": True}
    except Exception as e:
        logger.error("Update model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/llm/{model_id}")
def delete_model(model_id: int):
    """Delete LLM model config."""
    try:
        execute_write("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/llm/{model_id}/default")
def set_default_model(model_id: int):
    """Set a model as default."""
    try:
        execute_write("UPDATE adh_llm_models SET is_default = 0")
        execute_write("UPDATE adh_llm_models SET is_default = 1 WHERE id = %s", (model_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Set default model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system")
def get_system_config():
    """Get all system configs."""
    try:
        rows = execute_query("SELECT * FROM adh_system_config ORDER BY config_key")
        # Convert to dict format
        config = {}
        for row in rows:
            config[row["config_key"]] = row.get("config_value", "")
        return config
    except Exception as e:
        logger.error("Get system config failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/system")
def update_system_config(data: dict):
    """Update system config values."""
    try:
        for key, value in data.items():
            execute_write(
                """INSERT INTO adh_system_config (config_key, config_value)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE config_value = %s""",
                (key, str(value), str(value)),
            )
        return {"success": True}
    except Exception as e:
        logger.error("Update system config failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
