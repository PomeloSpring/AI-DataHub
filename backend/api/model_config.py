"""Model Config API — Manage LLM and Embedding model configurations.

LLM models are stored in adh_llm_models, system configs in adh_system_config.
"""

import logging
import time as _time
from datetime import datetime
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.auth import get_current_user, require_admin
from backend.models.schemas import UserInfo
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Models cache ──────────────────────────────────────────────────────

_models_cache = {}
_models_cache_time = 0
_MODELS_CACHE_TTL = 300  # 5 minutes


def _invalidate_models_cache():
    global _models_cache, _models_cache_time
    _models_cache = {}
    _models_cache_time = 0
    # Also clear LLM client cache so new config takes effect
    try:
        from backend.common.llm.llm_client import clear_clients_cache
        clear_clients_cache()
    except Exception:
        pass


def get_llm_model_config(model_id: int = None) -> dict:
    """Get LLM model config from database. Returns default if model_id is None."""
    global _models_cache, _models_cache_time

    now = _time.time()
    if now - _models_cache_time > _MODELS_CACHE_TTL:
        _models_cache = {}
        _models_cache_time = now

    cache_key = f"model:{model_id}" if model_id else "model:default"
    if cache_key in _models_cache:
        return _models_cache[cache_key]

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if model_id:
                cur.execute(
                    "SELECT id, name, provider, base_url, api_key, model_name, context_window, "
                    "max_tokens, supports_thinking, is_default "
                    "FROM adh_llm_models WHERE id = %s AND is_active = 1",
                    (model_id,),
                )
            else:
                cur.execute(
                    "SELECT id, name, provider, base_url, api_key, model_name, context_window, "
                    "max_tokens, supports_thinking, is_default "
                    "FROM adh_llm_models WHERE is_default = 1 AND is_active = 1 LIMIT 1"
                )
            row = cur.fetchone()
            if not row:
                # Fallback to .env config
                return _fallback_config()
            # Decrypt API key
            api_key = row.get("api_key") or ""
            if api_key:
                from backend.common.crypto import decrypt_password, is_encrypted
                if is_encrypted(api_key):
                    try:
                        row["api_key"] = decrypt_password(api_key)
                    except ValueError:
                        logger.warning("Failed to decrypt API key for model %s", row.get("id"))
            _models_cache[cache_key] = row
            return row
    finally:
        conn.close()


def _fallback_config() -> dict:
    """Fallback to .env config when no database config exists."""
    from backend.common.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL
    return {
        "id": 0,
        "name": "默认 (from .env)",
        "provider": "anthropic",
        "base_url": ANTHROPIC_BASE_URL,
        "api_key": ANTHROPIC_API_KEY,
        "model_name": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "supports_thinking": 1,
        "is_default": 1,
    }


def get_system_config(key: str, default: str = "") -> str:
    """Get a system config value from adh_system_config."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT config_value FROM adh_system_config WHERE config_key = %s",
                (key,),
            )
            row = cur.fetchone()
            return row["config_value"] if row else default
    finally:
        conn.close()


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


class EmbeddingConfigUpdate(BaseModel):
    model_path: Optional[str] = None
    dim: Optional[int] = None


# ── LLM Models CRUD ──────────────────────────────────────────────────

@router.get("/llm")
def list_llm_models(user: UserInfo = Depends(get_current_user)):
    """List all LLM models (api_key masked)."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, provider, base_url, api_key, model_name, context_window, "
                "max_tokens, supports_thinking, is_default, is_active, "
                "created_at, updated_at "
                "FROM adh_llm_models ORDER BY is_default DESC, name"
            )
            rows = cur.fetchall()
            for r in rows:
                # Mask api_key
                key = r.get("api_key") or ""
                if len(key) > 8:
                    r["api_key_masked"] = key[:4] + "****" + key[-4:]
                else:
                    r["api_key_masked"] = "****"
                r.pop("api_key", None)
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return rows
    finally:
        conn.close()


@router.post("/llm")
def create_llm_model(req: LLMModelCreate, admin: UserInfo = Depends(require_admin)):
    """Add a new LLM model."""
    from backend.common.crypto import encrypt_password

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            now = _now()
            row_id = int(_time.time() * 1000000)
            encrypted_key = encrypt_password(req.api_key) if req.api_key else ""
            cur.execute(
                "INSERT INTO adh_llm_models "
                "(id, name, provider, base_url, api_key, model_name, max_tokens, supports_thinking, is_default, is_active, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 1, %s, %s)",
                (row_id, req.name, req.provider, req.base_url, encrypted_key,
                 req.model_name, req.max_tokens, 1 if req.supports_thinking else 0,
                 now, now),
            )
        conn.commit()
        _invalidate_models_cache()
        return {"success": True, "id": row_id}
    finally:
        conn.close()


@router.put("/llm/{model_id}")
def update_llm_model(model_id: int, req: LLMModelUpdate, admin: UserInfo = Depends(require_admin)):
    """Update an LLM model."""
    from backend.common.crypto import encrypt_password, decrypt_password, is_encrypted

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_llm_models WHERE id = %s", (model_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="模型不存在")

            fields = {}
            for f in ("name", "provider", "base_url", "model_name", "max_tokens"):
                val = getattr(req, f, None)
                if val is not None:
                    fields[f] = val
                else:
                    fields[f] = row[f] or ""
            # Handle api_key with encryption
            if req.api_key:
                fields["api_key"] = encrypt_password(req.api_key)
            else:
                fields["api_key"] = row["api_key"] or ""  # Keep existing encrypted key
            if req.supports_thinking is not None:
                fields["supports_thinking"] = 1 if req.supports_thinking else 0
            else:
                fields["supports_thinking"] = row.get("supports_thinking", 1)

            now = _now()
            cur.execute("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
            cur.execute(
                "INSERT INTO adh_llm_models "
                "(id, name, provider, base_url, api_key, model_name, max_tokens, supports_thinking, is_default, is_active, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (model_id, fields["name"], fields["provider"], fields["base_url"],
                 fields["api_key"], fields["model_name"], fields["max_tokens"],
                 fields["supports_thinking"],
                 row.get("is_default", 0), row.get("is_active", 1),
                 row.get("created_at", now), now),
            )
        conn.commit()
        _invalidate_models_cache()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/llm/{model_id}")
def delete_llm_model(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete an LLM model."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
        conn.commit()
        _invalidate_models_cache()
        return {"success": True}
    finally:
        conn.close()


@router.put("/llm/{model_id}/default")
def set_default_model(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Set an LLM model as default."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_llm_models WHERE id = %s", (model_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="模型不存在")

            # Clear all defaults, then set this one
            # Doris doesn't support UPDATE, use DELETE + INSERT for all rows
            cur.execute("SELECT * FROM adh_llm_models WHERE is_active = 1")
            rows = cur.fetchall()
            for row in rows:
                new_default = 1 if row["id"] == model_id else 0
                cur.execute("DELETE FROM adh_llm_models WHERE id = %s", (row["id"],))
                cur.execute(
                    "INSERT INTO adh_llm_models "
                    "(id, name, provider, base_url, api_key, model_name, max_tokens, supports_thinking, is_default, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (row["id"], row["name"], row["provider"], row["base_url"],
                     row["api_key"], row["model_name"], row["max_tokens"],
                     row.get("supports_thinking", 1),
                     new_default, row.get("is_active", 1),
                     row.get("created_at"), row.get("updated_at")),
                )
        conn.commit()
        _invalidate_models_cache()
        return {"success": True}
    finally:
        conn.close()


# ── Embedding Config ──────────────────────────────────────────────────

@router.get("/embedding")
def get_embedding_config(user: UserInfo = Depends(get_current_user)):
    """Get embedding model configuration."""
    return {
        "model_path": get_system_config("embedding_model_path", "shibing624/text2vec-base-chinese"),
        "dim": int(get_system_config("embedding_dim", "768")),
    }


@router.put("/embedding")
def update_embedding_config(req: EmbeddingConfigUpdate, admin: UserInfo = Depends(require_admin)):
    """Update embedding model configuration."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            now = _now()
            if req.model_path is not None:
                cur.execute(
                    "DELETE FROM adh_system_config WHERE config_key = 'embedding_model_path'"
                )
                cur.execute(
                    "INSERT INTO adh_system_config (config_key, config_value, description, updated_at) "
                    "VALUES ('embedding_model_path', %s, 'Embedding 模型路径', %s)",
                    (req.model_path, now),
                )
            if req.dim is not None:
                cur.execute(
                    "DELETE FROM adh_system_config WHERE config_key = 'embedding_dim'"
                )
                cur.execute(
                    "INSERT INTO adh_system_config (config_key, config_value, description, updated_at) "
                    "VALUES ('embedding_dim', %s, 'Embedding 向量维度', %s)",
                    (str(req.dim), now),
                )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.post("/embedding/reload")
def reload_embedding(admin: UserInfo = Depends(require_admin)):
    """Reload the embedding model from current config."""
    from backend.common.llm.embedding import reload_model, get_model_info

    model_path = get_system_config("embedding_model_path", "shibing624/text2vec-base-chinese")
    info = reload_model(model_path)
    return {"success": True, "model_info": info}


# ── System Config ──────────────────────────────────────────────────

@router.get("/system")
def get_all_system_config(user: UserInfo = Depends(get_current_user)):
    """Get all system config values."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT config_key, config_value FROM adh_system_config")
            rows = cur.fetchall()
        return {row["config_key"]: row["config_value"] for row in rows}
    finally:
        conn.close()


class SystemConfigUpdate(BaseModel):
    key: str
    value: str


@router.put("/system")
def update_system_config(req: SystemConfigUpdate, admin: UserInfo = Depends(require_admin)):
    """Update a system config value."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            now = _now()
            cur.execute(
                "DELETE FROM adh_system_config WHERE config_key = %s",
                (req.key,),
            )
            cur.execute(
                "INSERT INTO adh_system_config (config_key, config_value, description, updated_at) "
                "VALUES (%s, %s, %s, %s)",
                (req.key, req.value, req.key, now),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()
