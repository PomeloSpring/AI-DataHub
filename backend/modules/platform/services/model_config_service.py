"""Model Config Service — LLM Model + System Config CRUD.

Migrated from backend/api/model_config.py.
"""

import json
import logging
import os
import time as _time
from datetime import datetime
from typing import Optional

from backend.common.db import DBConnection, execute_query, execute_insert, execute_write

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
                from backend.common.crypto import decrypt_password, is_encrypted
                if is_encrypted(api_key):
                    try:
                        row["api_key"] = decrypt_password(api_key)
                    except ValueError:
                        logger.warning("Failed to decrypt API key for model %s", row.get("id"))
            _models_cache[cache_key] = row
            return row


def _fallback_config() -> dict:
    """Fallback to .env config when no database config exists (provider 中立)."""
    return {
        "id": 0,
        "name": "Default (from .env)",
        "provider": "openai",
        "base_url": os.getenv("LLM_BASE_URL", ""),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model_name": os.getenv("LLM_MODEL", ""),
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
        "max_tokens, supports_thinking, supports_vision, capability_rank, is_default, "
        "is_active, created_at, updated_at "
        # 排序真值源 = capability_rank（能力序，1=最强），与 mind 侧模型中心同口径；
        # 只按 name 字典序会把「旗舰」与「最省」混成一列，默认项也不置顶（避免打断能力序）。
        "FROM adh_llm_models ORDER BY capability_rank ASC, name"
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
    from backend.common.crypto import encrypt_password

    now = _now()
    row_id = _generate_id()
    encrypted_key = encrypt_password(data["api_key"]) if data.get("api_key") else ""
    execute_insert(
        "INSERT INTO adh_llm_models "
        "(id, name, provider, model_id, base_url, api_key, model_name, max_tokens, "
        "context_window, supports_thinking, supports_vision, capability_rank, is_default, "
        "is_active, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
        (row_id, data["name"], data.get("provider", "openai"), data["model_name"],
         data["base_url"], encrypted_key, data["model_name"],
         data.get("max_tokens", 4096),
         data.get("context_window", 200000),
         1 if data.get("supports_thinking", True) else 0,
         # 多模态不拿建表默认值 1 当“支持”：没实测过就登为不支持（标签宁少勿假）
         1 if data.get("supports_vision") else 0,
         # 新登记不猜能力：缺省 99 = 未定档（排序沉底），由管理员或登记脚本显式定档
         int(data.get("capability_rank") or 99),
         1 if data.get("is_default") else 0, now, now),
    )
    _invalidate_models_cache()
    return row_id


def update_llm_model(model_id: int, data: dict) -> bool:
    """Update an LLM model（只改传入的字段，未传的保持原值）。

    历史写法是 DELETE + INSERT 重建整行，而 INSERT 列清单没带 context_window /
    supports_vision / model_id / capability_rank —— 改个名字就把这些列静默打回默认值。
    改为按字段 UPDATE（宁少改勿误抹）。
    """
    from backend.common.crypto import encrypt_password

    row = execute_query("SELECT id FROM adh_llm_models WHERE id = %s", (model_id,), fetchone=True)
    if not row:
        return False

    fields: dict = {}
    for f in ("name", "provider", "base_url", "model_name", "max_tokens",
              "context_window", "capability_rank"):
        if data.get(f) is not None:
            fields[f] = data[f]
    for flag in ("supports_thinking", "supports_vision"):
        if data.get(flag) is not None:
            fields[flag] = 1 if data[flag] else 0
    if data.get("api_key"):
        fields["api_key"] = encrypt_password(data["api_key"])
    if fields.get("model_name"):
        # 历史列与 model_name 同值（mind 侧写入口径一致），避免同一概念两处漂移
        fields["model_id"] = fields["model_name"]
    if not fields:
        return True

    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = %s" for k in fields)
    execute_write(f"UPDATE adh_llm_models SET {sets} WHERE id = %s",
                  (*fields.values(), model_id))
    _invalidate_models_cache()
    return True


def delete_llm_model(model_id: int) -> bool:
    """Delete an LLM model."""
    execute_write("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
    _invalidate_models_cache()
    return True


def set_default_model(model_id: int) -> bool:
    """Set an LLM model as default（只动 is_default 一位）。

    历史写法把**每一条启用行** DELETE + INSERT 重建，列清单缺 context_window /
    supports_vision / model_id / capability_rank —— 点一次「设为默认」就把全表这些列
    打回默认值（能力序直接消失）。改为两条 UPDATE，其余列不碰。
    """
    exists = execute_query(
        "SELECT id FROM adh_llm_models WHERE id = %s", (model_id,), fetchone=True
    )
    if not exists:
        return False

    now = _now()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE adh_llm_models SET is_default = 0 WHERE is_default = 1")
            cur.execute(
                "UPDATE adh_llm_models SET is_default = 1, updated_at = %s WHERE id = %s",
                (now, model_id))
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
