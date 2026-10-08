"""AS-BOT 授权事实源：空集合拒绝，注册与执行共享同一策略。"""
import hashlib
import json
import logging
from dataclasses import dataclass, field

from backend.modules.mind.execution.tool_catalog import CANONICAL_NAMES, LEGACY_ALIASES

logger = logging.getLogger(__name__)


def string_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{field} 必须是非空字符串组成的数组")
    return list(dict.fromkeys(x.strip() for x in value))


def normalize_tools(raw):
    if raw is None or raw == "":
        raw = {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("AS-BOT 工具配置不是合法 JSON") from exc
    if isinstance(raw, list):
        raw = {"groups": raw}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("AS-BOT 工具配置必须是对象")
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
    groups = string_list(raw.get("groups"), "groups")
    standard = [LEGACY_ALIASES.get(x, x) for x in string_list(raw.get("standard"), "standard")]
    if set(standard) - set(CANONICAL_NAMES):
        raise ValueError("存在未知标准工具")
    if set(groups) - set(TOOL_SERVER_TOOLS):
        raise ValueError("存在未知业务工具组")
    selection = raw.get("mcp") if "mcp" in raw else {g: TOOL_SERVER_TOOLS[g][1] for g in groups}
    selection = {} if selection is None else selection
    if not isinstance(selection, dict):
        raise ValueError("mcp 必须是逐工具授权对象")
    mcp = {}
    for group, names in selection.items():
        if group not in TOOL_SERVER_TOOLS:
            raise ValueError("存在未知业务工具组")
        names = string_list(names, "mcp 工具")
        if set(names) - set(TOOL_SERVER_TOOLS[group][1]):
            raise ValueError("存在未知业务工具")
        if names:
            mcp[group] = names
    # query 组(execute_sql)是受治理只读旁路(经 execute_query_with_permission),
    # 是否启用完全下放到 AS-BOT 逐工具授权。
    external = raw.get("external") or {}
    if not isinstance(external, dict):
        raise ValueError("external 必须是 MCP 服务 ID 到工具清单的对象")
    external = {str(k): string_list(v, "外部 MCP 工具") for k, v in external.items()}
    if any(not k.isdigit() or int(k) <= 0 for k in external):
        raise ValueError("外部 MCP 服务 ID 无效")
    if "bash" in standard and "read" not in standard:
        raise ValueError("命令执行可读取会话文件，必须同时显式授权读取")
    # 功能能力（菜单/权限码背后的平台功能动作）不是在这里授权的：
    # 它们从**角色权限码自动继承**（见 perm_link），AS-BOT 仅能做减法。
    # 这里只声明 AS-BOT 显式关闭了哪些 action，不声明开启。
    functions_raw = raw.get("functions") or {}
    if not isinstance(functions_raw, dict):
        raise ValueError("functions 必须是对象")
    functions = {"disabled": string_list(functions_raw.get("disabled"), "functions.disabled")}
    return {"groups": list(mcp), "standard": standard, "mcp": mcp, "external": external,
            "functions": functions}


@dataclass(frozen=True)
class ToolPolicy:
    as_bot: dict
    standard: tuple
    selection: dict
    external: dict
    allowed: frozenset
    digest: str
    # 功能能力工具（继承自角色权限码）的精确名，供 build_tool_servers 注册。
    function_names: tuple = ()
    descriptions: dict = field(default_factory=dict, compare=False)
    unavailable: dict = field(default_factory=dict)

    def manifest(self):
        from backend.modules.mind.execution.tool_catalog import TOOL_CATALOG
        labels = {t["name"]: t["label"] for t in TOOL_CATALOG}
        from backend.modules.mind.execution.sdk_tools import catalog_tools, semantic_tools, ontology_tools, screen_tools, system_tools, query_tools, asset_tools
        for module in (catalog_tools, semantic_tools, ontology_tools, screen_tools, system_tools, query_tools, asset_tools):
            for spec in getattr(module, "TOOL_SPECS", []):
                labels[spec["name"]] = spec.get("description") or spec["name"]
        return {"as_bot_key": self.as_bot["as_bot_key"], "version": self.digest,
                "tools": [{"name": name, "description": self.descriptions.get(name) or labels.get(name.rsplit("__", 1)[-1], name.rsplit("__", 1)[-1])}
                          for name in sorted(self.allowed)],
                "workspace_scope": "仅当前会话", "empty": not self.allowed,
                "unavailable_tools": [{"name": name, "reason": reason} for name, reason in self.unavailable.items()]}


def compile_policy(as_bot, ceiling=None, role_perms=None):
    """编译 AS-BOT 生效策略。

    两个正交维度（见 perm_link 模块顶部）：
      * **数据工具**：由 ``as_bot.tools.mcp`` 逐工具勾选，再与执行层 ceiling 取交集；
      * **功能能力**：从**角色权限码自动继承**，级别由 ``adh_perm_registry.ai_access``
        裁定，涉密项由 ``perm_link`` 硬收窄；AS-BOT 只能通过 ``tools.functions.disabled``
        做减法。

    Args:
        role_perms: ``{perm_code: {ai_access, label, ai_note}}`` —— 当前用户角色的
            权限码集合。``None`` 表示本次调用**不参与**功能能力继承（如存量单测），
            此时不注册任何功能工具、也不产生 unavailable 条目；空 dict 表示角色
            无权限码，逐项回传不可用原因。

            生产路径必须经 :func:`resolve_policy`（恒传 role_perms）；
            "resolve_policy 必传" 的契约由 tests/test_function_ai_access.py 锁定，
            避免日后有人绕过它导致功能能力静默消失。
    """
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
    from backend.modules.mind.execution.tool_catalog import parse_allowed_tools
    tools = normalize_tools(as_bot.get("tools"))
    allowed = set()
    standard = tools["standard"]
    selection = tools["mcp"]
    external = tools["external"]
    ids = {str(x) for x in as_bot.get("mcp_server_ids", [])}
    if set(external) - ids:
        raise ValueError("外部工具引用了未绑定的 MCP 服务")
    if ids - set(external):
        raise ValueError("外部 MCP 绑定需确认逐工具授权")
    for name in standard:
        allowed.add(f"mcp__datahub_workspace__{name}")
    for group, names in selection.items():
        allowed.update(f"mcp__{TOOL_SERVER_TOOLS[group][0]}__{n}" for n in names)
    for sid, names in external.items():
        allowed.update(f"mcp__external_{sid}__{n}" for n in names)
    if ceiling is not None:
        cap = set(parse_allowed_tools(ceiling))
        allowed = {n for n in allowed if n in cap or n.rsplit("__", 1)[-1] in cap}
    # query_by_tags 已落地本体资源域隔离(见 resource_guard 注入 + tags_service 过滤)，可注册执行；
    # task(子代理) 隔离契约尚未验证，仍不注册并在会话权限中显示不可用原因。
    unsupported = {"mcp__datahub_workspace__task": "子代理隔离契约尚未验证，当前不可用"}
    unavailable = {name: reason for name, reason in unsupported.items() if name in allowed}
    allowed.difference_update(unavailable)

    # ── 功能能力（A 维度）：角色权限码自动继承，AS-BOT 仅做减法 ───────────────
    from backend.modules.mind.execution import function_tools, perm_link
    function_names: tuple = ()
    if role_perms is None:
        # 本次调用不参与功能能力继承（非生产路径）。不产生 unavailable 条目：
        # 这不是"某个功能被拒"，而是"功能能力本就不在这次策略范围内"。
        # 生产路径经 resolve_policy 恒传 role_perms，契约由 test_function_ai_access.py 锁定。
        logger.debug("[tool_policy] compile_policy 未传 role_perms，跳过功能能力继承")
    else:
        fn_allowed, fn_denied = perm_link.resolve_function_actions(
            function_tools.FUNCTION_ACTION_SPECS, role_perms,
            tools.get("functions", {}).get("disabled") or ())
        for action_key in fn_allowed:
            allowed.add(function_tools.qualified_tool_name(action_key))
        for action_key, reason in fn_denied.items():
            unavailable[function_tools.qualified_tool_name(action_key)] = reason
        function_names = tuple(sorted(
            function_tools.tool_name_for(a) for a in fn_allowed))

    standard = tuple(n for n in standard if f"mcp__datahub_workspace__{n}" in allowed)
    if "bash" in standard and "read" not in standard:
        raise ValueError("上层权限未授予读取，无法启用命令执行")
    selection = {g: [n for n in ns if f"mcp__{TOOL_SERVER_TOOLS[g][0]}__{n}" in allowed]
                 for g, ns in selection.items()}
    selection = {g: ns for g, ns in selection.items() if ns}
    external = {sid: [n for n in ns if f"mcp__external_{sid}__{n}" in allowed] for sid, ns in external.items()}
    payload = {"as_bot": as_bot, "allowed": sorted(allowed)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return ToolPolicy(as_bot, standard, selection, external, frozenset(allowed), digest,
                      function_names=function_names, unavailable=unavailable)


def is_system_scope(policy: ToolPolicy) -> bool:
    """AS-BOT 是否拥有**系统能力面**（system 工具组被授权）。

    能力叠加语义（域规则更新：系统域只是权限的一部分，as-bot-system-waker §1）：
    授权了 system 组（system_usage/system_overview）即叠加系统资源面（系统本体/
    系统工具/系统源），**不屏蔽业务能力**；未授权仅业务域。取代旧
    ``waker_key='__system_bot__'`` 哨兵判定。
    """
    return "system" in (policy.selection or {})


def resolve_policy(ctx, ceiling=None):
    from backend.common.auth import resolve_execution_owner
    from backend.modules.mind.execution import as_bots
    user = resolve_execution_owner(ctx.user_id, ctx.workspace_id)
    ctx.user_role = user["role"]
    ctx.username = user["username"]
    key = (ctx.extra or {}).get("as_bot_key", "")
    candidates = as_bots.resolve_as_bots(ctx.workspace_id, ctx.user_role, key, ctx.user_id)
    as_bot = as_bots.default_as_bot(candidates)
    if not as_bot:
        raise PermissionError("当前工作空间未授权可用 AS-BOT")
    requested_model = ctx.extra.get("model_ref")
    models = as_bot.get("models") or []
    if requested_model and requested_model not in models:
        raise PermissionError("所选模型未授权给当前 AS-BOT")
    if not requested_model and models:
        ctx.extra["model_ref"] = models[0]
    # 功能能力从角色权限码继承。读取失败不静默当成"无权限"：
    # 直接抛错，让调用方看到工具不可用，而不是把功能悄悄少注册一批。
    from backend.core.role_service import role_service
    role_perms = role_service.get_user_role_ai_perms(ctx.user_id, ctx.workspace_id)
    return compile_policy(as_bot, ceiling, role_perms=role_perms)


def live_ceiling(ctx, runtime):
    """每次执行读取执行层有效性与工具上限。

    执行层全局生效(不按工作空间绑定, AS-BOT 可用点按用户角色裁决),
    工具上限取执行层配置; 执行层停用/配置变化立即生效。
    """
    if not runtime.layer_id:
        return runtime.ceiling
    from backend.modules.mind.execution import service
    from backend.modules.mind.execution.manager import _merge_allowed_tools
    row = service.get_layer(runtime.layer_id)
    if not row or row.get("status") != "active":
        raise PermissionError("执行层已停用或删除")
    config = row.get("config") or {}
    if config.get("mode") != "sdk" or config.get("cli_name") != runtime.backend:
        raise PermissionError("执行层后端配置已变化")
    if config.get("cwd") or config.get("allowed_dirs"):
        raise PermissionError("执行层配置不再满足会话目录隔离要求")
    return _merge_allowed_tools(config.get("allowed_tools"), None)


def check_tool(ctx, name):
    """执行前重新读取权限与 DB 领取版本；旧 SDK 上下文不能保存已撤销授权。"""
    runtime = (ctx.extra or {}).get("secure_runtime")
    if runtime is None:
        raise PermissionError("缺少可信执行会话")
    runtime.verify()
    policy = resolve_policy(ctx, live_ceiling(ctx, runtime))
    if policy.digest != runtime.policy.digest:
        raise PermissionError("AS-BOT 权限已变化，请重新发送消息")
    if name not in policy.allowed:
        raise PermissionError("当前 AS-BOT 未授权该工具")
    return runtime
