"""Model Config API — Manage LLM model and system configurations.

Migrated from backend/api/model_config.py
Table: adh_llm_models, adh_system_config
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.common.db import DBConnection, execute_query, execute_insert, execute_write
from backend.common.ttl_cache import model_config_cache

logger = logging.getLogger(__name__)
router = APIRouter()


class LLMModelCreate(BaseModel):
    name: str
    provider: str = "openai"
    base_url: str
    api_key: str
    model_name: str
    context_window: int = 200000
    max_tokens: int = 4096
    supports_thinking: bool = True
    supports_vision: bool = False
    is_default: bool = False
    #: 能力序：1=最强，数字越大越弱/越省；留 99 = 未定档（排序沉底，由管理员显式定档）
    capability_rank: int = 99
    #: 单价（每 1K token）：成本标签的唯一数据源。缺省 None = 未定价（前端如实写“未定价”，
    #: 不拿模型名猜价格）。
    input_price_per_1k: Optional[float] = None
    output_price_per_1k: Optional[float] = None
    currency: Optional[str] = None


class LLMModelUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    supports_thinking: Optional[bool] = None
    supports_vision: Optional[bool] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    capability_rank: Optional[int] = None
    input_price_per_1k: Optional[float] = None
    output_price_per_1k: Optional[float] = None
    currency: Optional[str] = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _seal_api_key(plain: str) -> str:
    """供应商密钥入库前加密（与 platform 侧 model_config_service 同口径）.

    读取侧（llm_resources._decrypt / platform get_llm_config）已兼容密文与历史明文，
    所以此处只需保证**新写入一定是密文**。加密失败就报错，不降级成明文落库。
    """
    if not plain:
        return ""
    from backend.common.crypto import encrypt_password
    return encrypt_password(plain)


@router.get("/llm")
def list_models():
    """List all LLM models (cached 5min)."""
    cached = model_config_cache.get("llm_list")
    if cached is not None:
        return cached
    try:
        rows = execute_query(
            "SELECT id, name, provider, base_url, model_name, context_window, "
            "max_tokens, supports_thinking, supports_vision, capability_rank, "
            "input_price_per_1k, output_price_per_1k, currency, is_default, is_active, "
            "created_at, updated_at "
            # 排序真值源 = capability_rank（能力序，1=最强）：旧写法只按 name 字典序，旗舰与最省
            # 混成一列，用户与管理员看不出该选哪个。默认项不再置顶（是否默认另有 is_default 标记），
            # 否则“按能力排序”会在第一行就被默认项打断；未定档（99）自然沉底。
            "FROM adh_llm_models ORDER BY capability_rank ASC, name"
        )
        for r in rows:
            for k in ("created_at", "updated_at"):
                if hasattr(r.get(k), "isoformat"):
                    r[k] = r[k].isoformat()
        model_config_cache.set("llm_list", rows)
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
            "max_tokens, supports_thinking, supports_vision, capability_rank, "
            "input_price_per_1k, output_price_per_1k, currency, is_default, is_active, "
            "created_at, updated_at "
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
               (name, provider, model_id, base_url, api_key, model_name, context_window,
                max_tokens, supports_thinking, supports_vision, capability_rank,
                input_price_per_1k, output_price_per_1k, currency,
                is_default, is_active, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)""",
            (req.name, req.provider, req.model_name, req.base_url, _seal_api_key(req.api_key),
             req.model_name,
             req.context_window, req.max_tokens, 1 if req.supports_thinking else 0,
             1 if req.supports_vision else 0,
             int(req.capability_rank or 99),
             req.input_price_per_1k, req.output_price_per_1k, req.currency,
             1 if req.is_default else 0, now, now),
        )

        # If set as default, unset others
        if req.is_default:
            execute_write("UPDATE adh_llm_models SET is_default = 0 WHERE id != %s", (model_id,))

        return {"id": model_id, "success": True}
    except Exception as e:
        logger.error("Create model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        model_config_cache.invalidate("llm_list")


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
            params.append(_seal_api_key(req.api_key))
        if req.model_name is not None:
            updates.append("model_name = %s")
            params.append(req.model_name)
            # model_id 是历史列（无消费方），与 model_name 保持同值，避免同一概念两处漂移
            updates.append("model_id = %s")
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
        if req.supports_vision is not None:
            updates.append("supports_vision = %s")
            params.append(1 if req.supports_vision else 0)
        if req.capability_rank is not None:
            updates.append("capability_rank = %s")
            params.append(int(req.capability_rank))
        for price_field in ("input_price_per_1k", "output_price_per_1k", "currency"):
            if getattr(req, price_field) is not None:
                updates.append(f"{price_field} = %s")
                params.append(getattr(req, price_field))
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
    finally:
        model_config_cache.invalidate("llm_list")


@router.delete("/llm/{model_id}")
def delete_model(model_id: int):
    """Delete LLM model config."""
    try:
        execute_write("DELETE FROM adh_llm_models WHERE id = %s", (model_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        model_config_cache.invalidate("llm_list")


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
    finally:
        model_config_cache.invalidate("llm_list")


@router.get("/system")
def get_system_config():
    """Get all system configs (cached 5min)."""
    cached = model_config_cache.get("system_config")
    if cached is not None:
        return cached
    try:
        rows = execute_query("SELECT * FROM adh_system_config ORDER BY config_key")
        config = {}
        for row in rows:
            config[row["config_key"]] = row.get("config_value", "")
        model_config_cache.set("system_config", config)
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
    finally:
        model_config_cache.invalidate("system_config")
