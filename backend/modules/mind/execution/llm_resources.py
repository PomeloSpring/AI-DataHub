"""系统 LLM 模型资源 — 执行层共享.

将平台模型中心(adh_llm_models)的大模型配置桥接给执行层:
- CLI 本身没有模型清单时,用系统模型配置填充可选项
- 运行时按模型引用解析出 base_url / api_key 等凭据注入 CLI
- 本回合生效模型的四级优先级解析(见 `resolve_effective_model`)

模型引用(ref)统一为 `{provider}/{model_name}`,
同时兼容按配置名 / model_name 直接指定。
"""

import logging

logger = logging.getLogger(__name__)


def _decrypt(row: dict) -> dict:
    """解密行内 api_key(若加密);失败保留原值."""
    api_key = row.get("api_key") or ""
    if api_key:
        try:
            from backend.common.crypto import decrypt_password, is_encrypted
            if is_encrypted(api_key):
                row["api_key"] = decrypt_password(api_key)
        except Exception as e:
            logger.warning("Decrypt LLM api_key failed (id=%s): %s", row.get("id"), e)
    return row


def model_ref(row: dict) -> str:
    """生成模型引用: {provider}/{model_name}."""
    provider = (row.get("provider") or "openai").lower()
    return f"{provider}/{row.get('model_name') or ''}"


def list_system_llm_models() -> list[dict]:
    """加载所有启用的系统 LLM 配置(api_key 已解密,含 ref 字段)."""
    from backend.common.db import execute_query

    try:
        rows = execute_query(
            "SELECT id, name, provider, base_url, api_key, model_name, is_default "
            # 与模型中心同口径按能力序排（1=最强）：执行层/AS-BOT 的模型候选直接拿这个
            # 顺序，名称字典序会把「最省」排在「旗舰」前面；默认项不置顶（不打断能力序）。
            "FROM adh_llm_models WHERE is_active=1 "
            "ORDER BY capability_rank ASC, name"
        )
    except Exception as e:
        logger.warning("List system LLM models failed: %s", e)
        return []
    return [_decrypt(dict(r)) | {"ref": model_ref(r)} for r in rows]


def platform_default_model_ref() -> str:
    """模型中心「设为默认」行的模型引用(`{provider}/{model_name}`);未显式设默认返回空串。

    只认 `is_default=1`:历史缺陷是模型中心页写着「"默认"只是不指定模型时的选型」,
    但执行链上没有任何消费点(唯一带 is_default 查询的分支要求 ref 为空,而调用方在
    ref 为空时已提前返回),导致设与不设都命中不到 —— 配了没用的空承诺。

    这里**不**回退到「取表里第一行」:那会让一个没人勾选的模型架空下面的部署兜底,
    把「管理员还没设默认」这个事实藏起来。未设默认 = 空串 = 继续往下一级走。
    """
    from backend.common.db import execute_query

    try:
        row = execute_query(
            "SELECT provider, model_name FROM adh_llm_models "
            "WHERE is_default=1 AND is_active=1 LIMIT 1",
            fetchone=True,
        )
    except Exception as e:  # noqa: BLE001 — 查不到按「未设默认」回落下一级,但要留可诊断痕迹
        logger.warning("读取模型中心默认模型失败(视为未设默认): %s", e)
        return ""
    if not row or not (row.get("model_name") or "").strip():
        return ""
    return model_ref(dict(row))


def resolve_effective_model(turn_model: str = "", layer_model: str = "") -> str:
    """本回合实际生效的模型引用 —— 四级优先级的唯一实现。

    顺序(越靠前越具体,由近及远:人在现场选 > Harness 配 > 平台设 > 部署兜底):
    1. `turn_model` —— 回合级:Chat 下拉选择,或 AS-BOT「可用模型」的首项
       (`tool_policy.resolve_policy` 已把它写进 ctx.extra.model_ref);
    2. `layer_model` —— Harness 级:执行层 `config.model`;
    3. 平台级:模型中心「设为默认」那一行;
    4. 部署兜底:`.env` 的 `QODER_MODEL`。

    四级全空返回空串,由调用方解释为「不指定,交给 SDK Auto」。

    抽成一个函数的原因:交互问答(secure_sdk)、上下文压缩(context_usage)与无人值守
    定时任务(scheduled_analysis)三处原先各写一份链且各不相同(定时任务漏了 Harness 级,
    三处都没有平台级),改一处就漂移。`QODER_MODEL` 在函数体内读取,保持与既有
    monkeypatch 测试口径一致(模块级 import 会把值定死在导入时刻)。
    """
    from backend.common.config import QODER_MODEL

    return ((turn_model or "").strip() or (layer_model or "").strip()
            or platform_default_model_ref() or (QODER_MODEL or "").strip())


def resolve_system_llm_model(ref: str = "") -> dict:
    """按引用解析系统 LLM 配置.

    Args:
        ref: 模型引用(`{provider}/{model_name}`)、配置名或 model_name;
             为空时返回系统默认模型。

    Returns:
        含 id/name/provider/base_url/api_key/model_name/ref 的 dict;
        未命中且 ref 为空时回退默认模型;均未命中返回 {}。
    """
    from backend.common.db import execute_query

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
