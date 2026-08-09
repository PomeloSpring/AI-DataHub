"""Model Config Service — LLM Model + System Config CRUD.

Migrated from backend/api/model_config.py.
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)

# Models cache
_models_cache = {}
_models_cache_time = 0
_MODELS_CACHE_TTL = 300  # 5 minutes


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


def _invalidate_models_cache():
    global _models_cache, _models_cache_time
    _models_cache = {}
    _models_cache_time = 0
    try:
        from services.shared.common.llm.llm_client import clear_clients_cache
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

    with DBConnection() as conn:
        with conn.cursor() as cur:
            if model_id:
                cur.execute(
                    "SELECT id, name, provider, base_url, api_key, model_name, context_window, "
                    "max_tokens, supports_thinking, supports_vision, is_default "
                    "FROM adh_llm_models WHERE id = %s AND is_active = 1",
                    (model_id,),
                )
            else:
                cur.execute(
                    "SELECT id, name, provider, base_url, api_key, model_name, context_window, "
                    "max_tokens, supports_thinking, supports_vision, is_default "
                    "FROM adh_llm_models WHERE is_default = 1 AND is_active = 1 LIMIT 1"
                )
            row = cur.fetchone()
            if not row:
                return _fallback_config()
            # Decrypt API key
            api_key = row.get("api_key") or ""
            if api_key:
                from services.shared.common.crypto import decrypt_password, is_encrypted
                if is_encrypted(api_key):
                    try:
                        row["api_key"] = decrypt_password(api_key)
                    except ValueError:
                        logger.warning("Failed to decrypt API key for model %s", row.get("id"))
            _models_cache[cache_key] = row
            return row


def _fallback_config() -> dict:
    """Fallback to .env config when no database config exists."""
    from services.shared.common.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL
    return {
        "id": 0,
        "name": "Default (from .env)",
        "provider": "anthropic",
        "base_url": ANTHROPIC_BASE_URL,
        "api_key": ANTHROPIC_API_KEY,
        "model_name": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "supports_thinking": 1,
        "supports_vision": 1,
        "is_default": 1,
    }


def get_system_config(key: str, default: str = "") -> str:
    """Get a system config value from adh_system_config."""
    row = execute_query(
        "SELECT config_value FROM adh_system_config WHERE config_key = %s",
        (key,),
        fetchone=True,
    )
    return row["config_value"] if row else default


# ── LLM Models CRUD ──────────────────────────────────────────────────

def list_llm_models() -> list:
    """List all LLM models (api_key masked)."""
    rows = execute_query(
        "SELECT id, name, provider, base_url, api_key, model_name, context_window, "
        "max_tokens, supports_thinking, supports_vision, is_default, is_active, "
        "created_at, updated_at "
        "FROM adh_llm_models ORDER BY is_default DESC, name"
    )
    for r in rows:
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


def create_llm_model(data: dict) -> int:
    """Add a new LLM model."""
    from services.shared.common.crypto import encrypt_password

    now = _now()
    row_id = _generate_id()
    encrypted_key = encrypt_password(data["api_key"]) if data.get("api_key") else ""
    execute_insert(
        "INSERT INTO adh_llm_models "
        "(id, name, provider, base_url, api_key, model_name, max_tokens, supports_thinking, "
        "is_default, is_active, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 1, %s, %s)",
        (row_id, data["name"], data.get("provider", "anthropic"),
         data["base_url"], encrypted_key, data["model_name"],
         data.get("max_tokens", 4096),
         1 if data.get("supports_thinking", True) else 0, now, now),
    )
    _invalidate_models_cache()
    return row_id


def update_llm_model(model_id: int, data: dict) -> bool:
    """Update an LLM model."""
    from services.shared.common.crypto import encrypt_password

    row = execute_query("SELECT * FROM adh_llm_models WHERE id = %s", (model_id,), fetchone=True)
    if not row:
        return False

    fields = {}
    for f in ("name", "provider", "base_url", "model_name", "max_tokens"):
        val = data.get(f)
        if val is not None:
            fields[f] = val
        else:
            fields[f] = row[f] or ""

    if data.get("api_key"):
        fields["api_key"] = encrypt_password(data["api_key"])
    else:
        fields["api_key"] = row["api_key"] or ""

    if data.get("supports_thinking") is not None:
        fields["supports_thinking"] = 1 if data["supports_thinking"] else 0
    else:
        fields["supports_thinking"] = row.get("supports_thinking", 1)

    now = _now()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
            cur.execute(
                "INSERT INTO adh_llm_models "
                "(id, name, provider, base_url, api_key, model_name, max_tokens, "
                "supports_thinking, is_default, is_active, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (model_id, fields["name"], fields["provider"], fields["base_url"],
                 fields["api_key"], fields["model_name"], fields["max_tokens"],
                 fields["supports_thinking"],
                 row.get("is_default", 0), row.get("is_active", 1),
                 row.get("created_at", now), now),
            )
    _invalidate_models_cache()
    return True


def delete_llm_model(model_id: int) -> bool:
    """Delete an LLM model."""
    execute_write("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
    _invalidate_models_cache()
    return True


def set_default_model(model_id: int) -> bool:
    """Set an LLM model as default."""
    exists = execute_query(
        "SELECT id FROM adh_llm_models WHERE id = %s", (model_id,), fetchone=True
    )
    if not exists:
        return False

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_llm_models WHERE is_active = 1")
            rows = cur.fetchall()
            for row in rows:
                new_default = 1 if row["id"] == model_id else 0
                cur.execute("DELETE FROM adh_llm_models WHERE id = %s", (row["id"],))
                cur.execute(
                    "INSERT INTO adh_llm_models "
                    "(id, name, provider, base_url, api_key, model_name, max_tokens, "
                    "supports_thinking, is_default, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (row["id"], row["name"], row["provider"], row["base_url"],
                     row["api_key"], row["model_name"], row["max_tokens"],
                     row.get("supports_thinking", 1),
                     new_default, row.get("is_active", 1),
                     row.get("created_at"), row.get("updated_at")),
                )
    _invalidate_models_cache()
    return True


# ── System Config ──────────────────────────────────────────────────

def get_all_system_config() -> dict:
    """Get all system config values."""
    rows = execute_query("SELECT config_key, config_value FROM adh_system_config")
    return {row["config_key"]: row["config_value"] for row in rows}


def update_system_config(key: str, value: str) -> bool:
    """Update a system config value."""
    now = _now()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_system_config WHERE config_key = %s", (key,))
            cur.execute(
                "INSERT INTO adh_system_config (config_key, config_value, description, updated_at) "
                "VALUES (%s, %s, %s, %s)",
                (key, value, key, now),
            )
    return True


def get_embedding_config() -> dict:
    """Get embedding model configuration."""
    return {
        "model_path": get_system_config("embedding_model_path", "shibing624/text2vec-base-chinese"),
        "dim": int(get_system_config("embedding_dim", "768")),
    }


def update_embedding_config(model_path: str = None, dim: int = None) -> bool:
    """Update embedding model configuration."""
    now = _now()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            if model_path is not None:
                cur.execute("DELETE FROM adh_system_config WHERE config_key = 'embedding_model_path'")
                cur.execute(
                    "INSERT INTO adh_system_config (config_key, config_value, description, updated_at) "
                    "VALUES ('embedding_model_path', %s, 'Embedding model path', %s)",
                    (model_path, now),
                )
            if dim is not None:
                cur.execute("DELETE FROM adh_system_config WHERE config_key = 'embedding_dim'")
                cur.execute(
                    "INSERT INTO adh_system_config (config_key, config_value, description, updated_at) "
                    "VALUES ('embedding_dim', %s, 'Embedding vector dimension', %s)",
                    (str(dim), now),
                )
    return True
