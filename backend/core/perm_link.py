"""功能能力 ↔ 角色权限码 的映射与涉密边界（AI 可调用级别的唯一裁决点）。

两个正交维度，**不得混用**：

  A. 功能能力（本模块管辖）
     对象：菜单/权限码背后的平台功能动作（报表、看板、调度任务、质量检核、数据集…）
     授权来源：``adh_role_perms``（角色权限码，AS-BOT 自动继承，AS-BOT 仅做减法）
     级别来源：``adh_perm_registry.ai_access``（none / read / write）
     动作登记：``function_tools.FUNCTION_ACTION_SPECS``（登记了才生成 LLM 工具）

  B. 系统数据工具（**不**归本模块管辖）
     对象：nl2sql / check_sql / execute_sql / 知识库检索 / run_semantic_query /
           search_metadata 等取数与检索通道
     授权来源：``adh_as_bots.tools.mcp`` 逐工具勾选（tool_policy.normalize_tools）
     管控：统一治理入口 + 数据护城河（security-guardrails §1/§6/§7）

为什么必须分开：若用 A 的菜单权限码去门控 B 的取数工具，权限语义会与
``permission_enforcer`` / ``gates`` 打架，也会让「看板页能读指标」这类授权
被误当成「可以执行 SQL」。UI 上同样分区展示，避免管理员误配。

写动作直执行把关（as-bot 统一后）
------------------------------
AS-BOT 写动作（本体保存/激活、别名回写、仪表盘发布、契约变更…）不再走
提议→审批→执行回路；执行前用 :func:`check_write_perm` 按**菜单与功能权限码**
fail-closed 把关（perm_code + ai_access='write'），拒绝原因可解释给用户。

涉密硬约束（配置不可覆盖）
--------------------------
``SECRET_BOUND_*`` 是**代码层**的上界，与 DB 里的 ``ai_access`` 取更严者：

  * ``SECRET_BOUND_NONE_PERMS``     —— 恒为 none，根本不进 LLM 工具面；
  * ``SECRET_BOUND_READ_ONLY_PERMS`` —— 上限 read，禁止一切写。

即使管理员把某个涉密项的 ``ai_access`` 配成 ``write``，有效级别仍被 cap 下来。
这不是"静默降级"：``cap_level`` 会返回收窄原因，由上层放进
``ToolPolicy.unavailable`` 与 UI 的 ``ai_note`` 显示出来（no-silent-degradation §1）。
"""

from __future__ import annotations

# ── AI 可调用级别 ────────────────────────────────────────────────────────────
AI_LEVEL_NONE = "none"
AI_LEVEL_READ = "read"
AI_LEVEL_WRITE = "write"
AI_LEVELS = (AI_LEVEL_NONE, AI_LEVEL_READ, AI_LEVEL_WRITE)

_LEVEL_RANK = {AI_LEVEL_NONE: 0, AI_LEVEL_READ: 1, AI_LEVEL_WRITE: 2}

_LEVEL_LABEL = {
    AI_LEVEL_NONE: "不开放给 AI",
    AI_LEVEL_READ: "只读可见",
    AI_LEVEL_WRITE: "可读可写",
}

# ── 涉密硬 deny-list（代码层上界，配置不可覆盖）────────────────────────────────
# 判定口径（与 docker/mysql/perm_ai_access_migration.sql 的 ai_note 一致）：
#   * 含连接凭据 / API Key / Token / 账号个人身份；
#   * 本身是安全边界（RLS / 角色权限 / 沙箱），可被 prompt injection 诱导放宽；
#   * 含系统提示词（注入面）或物理 SQL / 表列名（守 security-guardrails §7 黑盒）。
SECRET_BOUND_NONE_PERMS = frozenset({
    # 连接凭据 / 密钥 / 通知凭据
    "datasource:manage", "mcp:manage", "mcp:read",
    "model:manage", "notification:manage", "notification:read",
    # 身份与权限配置（可被诱导提权）
    "user:manage", "user:read", "role:manage", "role:read",
    "system:users", "system:roles",
    # 安全边界（可被诱导放宽）
    "rls:manage", "rls:read", "system:rls",
    "sandbox:manage", "sandbox:read", "system:sandbox",
    "asbot:manage", "asbot:read", "system:as-bots",
    "sensitive:manage",
    # 全局配置含各类密钥
    "settings:manage", "settings:read", "system:settings",
    # 注入面：系统提示词 / 技能提示词
    "prompt:manage", "prompt:read", "skill:manage", "skill:read",
    # 物理 SQL 与表列名（守 §7 黑盒）
    "sql-pairs:manage", "sql-pairs:read",
})

# 上限 read：可见但禁止一切写（涉密域的"只读可见"边界）。
SECRET_BOUND_READ_ONLY_PERMS = frozenset({
    "datasource:read", "model:read", "sensitive:read",
    "audit:read", "observability:read",
})


def normalize_level(value, field: str = "ai_access") -> str:
    """校验并归一化 AI 级别取值。未知取值直接抛错，不静默回落。"""
    if value is None or value == "":
        return AI_LEVEL_NONE
    level = str(value).strip().lower()
    if level not in _LEVEL_RANK:
        raise ValueError(f"{field} 取值必须是 {list(AI_LEVELS)} 之一")
    return level


def cap_level(perm_code: str, configured) -> str:
    """把配置级别收窄到涉密硬上界内（只能更严，不能更松）。

    Returns the **effective** level. 调用方若需要解释为什么被收窄，
    用 :func:`effective_level` 拿收窄原因。
    """
    level = normalize_level(configured)
    if perm_code in SECRET_BOUND_NONE_PERMS:
        return AI_LEVEL_NONE
    if perm_code in SECRET_BOUND_READ_ONLY_PERMS:
        return min(level, AI_LEVEL_READ, key=lambda x: _LEVEL_RANK[x])
    return level


def effective_level(perm_code: str, configured, note: str = "") -> tuple[str, str]:
    """返回 (有效级别, 收窄原因)。收窄原因非空表示配置被涉密边界压下来了。"""
    level = normalize_level(configured)
    capped = cap_level(perm_code, level)
    if capped == level:
        return level, ""
    label = note.strip() if note else "该功能涉及敏感信息"
    return capped, f"{label}；配置级别「{_LEVEL_LABEL[level]}」已被涉密边界收窄为「{_LEVEL_LABEL[capped]}」"


def resolve_function_actions(action_specs: dict, role_perms: dict,
                             disabled_actions=()) -> tuple[dict, dict]:
    """按角色权限码 + AI 级别 + AS-BOT 减法开关，判定每个功能动作是否可被 LLM 调用。

    Args:
        action_specs: ``function_tools.FUNCTION_ACTION_SPECS``
            ``{action_key: {"perm_code":…, "read_only": bool, "label":…, …}}``
        role_perms: 当前用户角色拥有的权限码
            ``{perm_code: {"ai_access": "none|read|write", "label": str, "ai_note": str}}``
        disabled_actions: AS-BOT 侧显式关闭的 action_key（仅做减法）

    Returns:
        (allowed, denied)
        allowed: ``{action_key: 有效级别}``
        denied:  ``{action_key: 可读原因}`` —— 原因必须能解释给用户看，
                 并由上层透传到 ``ToolPolicy.unavailable``，**不静默少注册**。
    """
    allowed: dict[str, str] = {}
    denied: dict[str, str] = {}
    disabled = {str(a) for a in (disabled_actions or ())}

    for action_key, spec in action_specs.items():
        perm_code = spec.get("perm_code") or ""
        label = spec.get("label") or action_key

        if action_key in disabled:
            denied[action_key] = "AS-BOT 配置中已关闭该功能"
            continue

        grant = (role_perms or {}).get(perm_code)
        if not grant:
            denied[action_key] = f"角色未授予「{perm_code}」权限，功能「{label}」不可用"
            continue

        level, capped_reason = effective_level(
            perm_code, grant.get("ai_access"), grant.get("ai_note") or "")
        if level == AI_LEVEL_NONE:
            denied[action_key] = capped_reason or (
                grant.get("ai_note") or "该功能未开放给 AI")
            continue

        if not spec.get("read_only", True) and level != AI_LEVEL_WRITE:
            # 两层原因都要说清：它是个写动作，且当前级别不够（若被涉密收窄也要点明）。
            base = capped_reason or f"当前级别仅「{_LEVEL_LABEL[level]}」"
            denied[action_key] = f"「{label}」是写操作，{base}"
            continue

        allowed[action_key] = level

    return allowed, denied


def function_action_perms(action_specs: dict) -> dict:
    """``{action_key: perm_code}`'，供 UI/测试核对 DB 的 ai_action_key 双侧一致。"""
    return {k: (v.get("perm_code") or "") for k, v in action_specs.items()}


def check_write_perm(role_perms: dict, perm_code: str, label: str = "") -> tuple[bool, str]:
    """写动作直执行把关：角色须持有 perm_code 且有效级别为 write（fail-closed）。

    Returns:
        (可否执行, 原因) —— 原因必须能解释给用户；无授权/级别不足/涉密收窄均拒绝。
    """
    name = label or perm_code
    grant = (role_perms or {}).get(perm_code)
    if not grant:
        return False, f"角色未授予「{perm_code}」权限，无法执行「{name}」"
    level, capped_reason = effective_level(
        perm_code, grant.get("ai_access"), grant.get("ai_note") or "")
    if level != AI_LEVEL_WRITE:
        base = capped_reason or f"当前级别仅「{_LEVEL_LABEL[level]}」"
        return False, f"「{name}」是写操作，{base}"
    return True, ""


def require_write_perm(user_id: int, workspace_id: int, perm_code: str, label: str = "") -> None:
    """执行期把关：按用户经角色持有的权限码裁决写动作，不满足即 raise PermissionError。

    口径与 tool_policy 的功能能力继承一致（adh_user_roles ⋈ adh_role_perms ⋈
    adh_perm_registry.ai_access + 涉密硬上界），不新建第二套判断。
    """
    from backend.core.role_service import role_service
    role_perms = role_service.get_user_role_ai_perms(int(user_id or 0), int(workspace_id or 0))
    ok, reason = check_write_perm(role_perms, perm_code, label)
    if not ok:
        raise PermissionError(reason)
