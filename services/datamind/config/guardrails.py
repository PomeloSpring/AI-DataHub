"""AI 护栏 — 角色风格 + 权限边界 提示词组装。

护栏存储在 adh_prompts（category=role_style / permission_boundary），采用
「全局默认(workspace_id=0) + 工作空间可覆盖」模型，经 config.loader 的
DB→文件→TTL 缓存三级回退加载。

各 system prompt 组装点在拼装前调用 get_guardrail_prompt(workspace_id) 前置注入，
从而在 LLM 层面先行约束模型的语言风格与越权行为（护城河），再由执行层做硬性拦截。

用法::

    from services.datamind.config.guardrails import get_guardrail_prompt
    system = get_guardrail_prompt(workspace_id) + "\\n\\n" + base_system_prompt
"""

import logging

logger = logging.getLogger(__name__)

#: adh_prompts 中护栏的逻辑键（沿用 loader 的 "<skill>:<component>" 约定）
ROLE_STYLE = ("guardrail", "role_style")
PERMISSION_BOUNDARY = ("guardrail", "permission_boundary")


def get_guardrail_prompt(workspace_id: int = 0) -> str:
    """组装「角色风格 + 权限边界」护栏文本，供各 system prompt 前置注入。

    Args:
        workspace_id: 工作空间作用域（0 = 全局默认）。非 0 时优先取该工作空间
            的覆盖行，缺失则回退全局默认。

    Returns:
        护栏文本；当两项护栏均未配置时返回空串（安全 no-op，不影响原提示词）。
    """
    from services.datamind.config.loader import load_prompt

    try:
        role = load_prompt(ROLE_STYLE[0], ROLE_STYLE[1], workspace_id=workspace_id) or ""
        boundary = load_prompt(PERMISSION_BOUNDARY[0], PERMISSION_BOUNDARY[1],
                               workspace_id=workspace_id) or ""
    except Exception as e:  # 护栏加载失败不得阻断主流程
        logger.warning("Guardrail load failed (ws=%s): %s", workspace_id, e)
        return ""

    parts = []
    role = role.strip()
    boundary = boundary.strip()
    if role:
        parts.append(f"## 角色风格\n{role}")
    if boundary:
        # 权限边界种子自带 "## 权限边界" 标题；若无标题则补一个
        parts.append(boundary if boundary.startswith("#") else f"## 权限边界\n{boundary}")
    return "\n\n".join(parts)
