"""系统 LLM 模型资源 — 执行层共享.

将平台模型中心(adh_llm_models)的大模型配置桥接给执行层:
- CLI 本身没有模型清单时(opencode),用系统模型配置填充可选项
- 运行时按模型引用解析出 base_url / api_key 等凭据注入 CLI

模型引用(ref)统一为 `{provider}/{model_name}`(与 opencode 的
model 格式一致),同时兼容按配置名 / model_name 直接指定。
"""

import logging

logger = logging.getLogger(__name__)


def _decrypt(row: dict) -> dict:
    """解密行内 api_key(若加密);失败保留原值."""
    api_key = row.get("api_key") or ""
    if api_key:
        try:
            from services.shared.common.crypto import decrypt_password, is_encrypted
            if is_encrypted(api_key):
                row["api_key"] = decrypt_password(api_key)
        except Exception as e:
            logger.warning("Decrypt LLM api_key failed (id=%s): %s", row.get("id"), e)
    return row


def model_ref(row: dict) -> str:
    """生成模型引用: {provider}/{model_name}."""
    provider = (row.get("provider") or "anthropic").lower()
    return f"{provider}/{row.get('model_name') or ''}"


def list_system_llm_models() -> list[dict]:
    """加载所有启用的系统 LLM 配置(api_key 已解密,含 ref 字段)."""
    from services.shared.common.db import execute_query

    try:
        rows = execute_query(
            "SELECT id, name, provider, base_url, api_key, model_name, is_default "
            "FROM adh_llm_models WHERE is_active=1 ORDER BY is_default DESC, name"
        )
    except Exception as e:
        logger.warning("List system LLM models failed: %s", e)
        return []
    return [_decrypt(dict(r)) | {"ref": model_ref(r)} for r in rows]


def resolve_system_llm_model(ref: str = "") -> dict:
    """按引用解析系统 LLM 配置.

    Args:
        ref: 模型引用(`{provider}/{model_name}`)、配置名或 model_name;
             为空时返回系统默认模型。

    Returns:
        含 id/name/provider/base_url/api_key/model_name/ref 的 dict;
        未命中且 ref 为空时回退默认模型;均未命中返回 {}。
    """
    from services.shared.common.db import execute_query

    try:
        if not ref:
            row = execute_query(
                "SELECT id, name, provider, base_url, api_key, model_name, is_default "
                "FROM adh_llm_models WHERE is_default=1 AND is_active=1 LIMIT 1",
                fetchone=True,
            )
            if not row:
                rows = execute_query(
                    "SELECT id, name, provider, base_url, api_key, model_name, is_default "
                    "FROM adh_llm_models WHERE is_active=1 ORDER BY id LIMIT 1"
                )
                row = rows[0] if rows else None
            return _decrypt(dict(row)) | {"ref": model_ref(row)} if row else {}

        if "/" in ref:
            provider, model_name = ref.split("/", 1)
            row = execute_query(
                "SELECT id, name, provider, base_url, api_key, model_name, is_default "
                "FROM adh_llm_models WHERE is_active=1 AND provider=%s AND model_name=%s LIMIT 1",
                (provider, model_name),
                fetchone=True,
            )
            if row:
                return _decrypt(dict(row)) | {"ref": ref}
        row = execute_query(
            "SELECT id, name, provider, base_url, api_key, model_name, is_default "
            "FROM adh_llm_models WHERE is_active=1 AND (name=%s OR model_name=%s) "
            "ORDER BY is_default DESC LIMIT 1",
            (ref, ref),
            fetchone=True,
        )
        return _decrypt(dict(row)) | {"ref": model_ref(row)} if row else {}
    except Exception as e:
        logger.warning("Resolve system LLM model failed (ref=%s): %s", ref, e)
        return {}
