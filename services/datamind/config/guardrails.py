"""AI 护栏 — 权限边界提示词组装。

护栏存储在 adh_prompts（category=permission_boundary），采用
「全局默认(workspace_id=0) + 工作空间可覆盖」模型，经 config.loader 的
DB→文件→TTL 缓存三级回退加载。

注：角色风格(role_style)已从本护栏退出 —— 人格/风格唯一配置点是 Waker
(persona + system_prompt, 经 prompt_composer 注入)，避免同一生效配置两处消费。

用法::

    from services.datamind.config.guardrails import get_guardrail_prompt
    system = get_guardrail_prompt(workspace_id) + "\\n\\n" + base_system_prompt
"""

import logging

logger = logging.getLogger(__name__)

#: adh_prompts 中护栏的逻辑键（沿用 loader 的 "<skill>:<component>" 约定）
PERMISSION_BOUNDARY = ("guardrail", "permission_boundary")


def get_guardrail_prompt(workspace_id: int = 0) -> str:
    """组装「权限边界」护栏文本，供各 system prompt 前置注入。

    Args:
        workspace_id: 工作空间作用域（0 = 全局默认）。非 0 时优先取该工作空间
            的覆盖行，缺失则回退全局默认。

    Returns:
        护栏文本；未配置时返回空串（安全 no-op，不影响原提示词）。
    """
    from services.datamind.config.loader import load_prompt

    try:
        boundary = load_prompt(PERMISSION_BOUNDARY[0], PERMISSION_BOUNDARY[1],
                               workspace_id=workspace_id) or ""
    except Exception as e:  # 护栏加载失败不得阻断主流程
        logger.warning("Guardrail load failed (ws=%s): %s", workspace_id, e)
        return ""

    boundary = boundary.strip()
    if not boundary:
        return ""
    # 权限边界种子自带 "## 权限边界" 标题；若无标题则补一个
    return boundary if boundary.startswith("#") else f"## 权限边界\n{boundary}"
