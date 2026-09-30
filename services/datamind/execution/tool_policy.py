"""Waker 授权事实源：空集合拒绝，注册与执行共享同一策略。"""
import hashlib
import json
from dataclasses import dataclass, field

from services.datamind.execution.tool_catalog import CANONICAL_NAMES, LEGACY_ALIASES


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
            raise ValueError("Waker 工具配置不是合法 JSON") from exc
    if isinstance(raw, list):
        raw = {"groups": raw}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Waker 工具配置必须是对象")
    from services.datamind.execution.sdk_tools import TOOL_SERVER_TOOLS
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
    # 是否启用完全下放到 waker 逐工具授权; AS-BOT 的禁止由 resolve_system_bot_waker 强制保障。
    external = raw.get("external") or {}
    if not isinstance(external, dict):
        raise ValueError("external 必须是 MCP 服务 ID 到工具清单的对象")
    external = {str(k): string_list(v, "外部 MCP 工具") for k, v in external.items()}
    if any(not k.isdigit() or int(k) <= 0 for k in external):
        raise ValueError("外部 MCP 服务 ID 无效")
    if "bash" in standard and "read" not in standard:
        raise ValueError("命令执行可读取会话文件，必须同时显式授权读取")
    return {"groups": list(mcp), "standard": standard, "mcp": mcp, "external": external}


@dataclass(frozen=True)
class ToolPolicy:
    waker: dict
    standard: tuple
    selection: dict
    external: dict
    allowed: frozenset
    digest: str
    descriptions: dict = field(default_factory=dict, compare=False)
    unavailable: dict = field(default_factory=dict)

    def manifest(self):
        from services.datamind.execution.tool_catalog import TOOL_CATALOG
        labels = {t["name"]: t["label"] for t in TOOL_CATALOG}
        from services.datamind.execution.sdk_tools import catalog_tools, semantic_tools, ontology_tools, screen_tools, system_tools, query_tools
        for module in (catalog_tools, semantic_tools, ontology_tools, screen_tools, system_tools, query_tools):
            for spec in getattr(module, "TOOL_SPECS", []):
                labels[spec["name"]] = spec.get("description") or spec["name"]
        return {"waker_key": self.waker["waker_key"], "version": self.digest,
                "tools": [{"name": name, "description": self.descriptions.get(name) or labels.get(name.rsplit("__", 1)[-1], name.rsplit("__", 1)[-1])}
                          for name in sorted(self.allowed)],
                "workspace_scope": "仅当前会话", "empty": not self.allowed,
                "unavailable_tools": [{"name": name, "reason": reason} for name, reason in self.unavailable.items()]}


def compile_policy(waker, ceiling=None):
    from services.datamind.execution.sdk_tools import TOOL_SERVER_TOOLS
    from services.datamind.execution.tool_catalog import parse_allowed_tools
    tools = normalize_tools(waker.get("tools"))
    allowed = set()
    standard = tools["standard"]
    selection = tools["mcp"]
    external = tools["external"]
    ids = {str(x) for x in waker.get("mcp_server_ids", [])}
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
    standard = tuple(n for n in standard if f"mcp__datahub_workspace__{n}" in allowed)
    if "bash" in standard and "read" not in standard:
        raise ValueError("上层权限未授予读取，无法启用命令执行")
    selection = {g: [n for n in ns if f"mcp__{TOOL_SERVER_TOOLS[g][0]}__{n}" in allowed]
                 for g, ns in selection.items()}
    selection = {g: ns for g, ns in selection.items() if ns}
    external = {sid: [n for n in ns if f"mcp__external_{sid}__{n}" in allowed] for sid, ns in external.items()}
    payload = {"waker": waker, "allowed": sorted(allowed)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return ToolPolicy(waker, standard, selection, external, frozenset(allowed), digest, unavailable=unavailable)


def resolve_policy(ctx, ceiling=None):
    from services.shared.common.auth import resolve_execution_owner
    from services.datamind.execution import wakers
    user = resolve_execution_owner(ctx.user_id, ctx.workspace_id)
    ctx.user_role = user["role"]
    ctx.username = user["username"]
    key = (ctx.extra or {}).get("waker_key", "")
    if key == wakers.SYSTEM_BOT_WAKER_KEY:
        if ctx.user_role != "admin" or ctx.workspace_id != 0:
            raise PermissionError("系统助手仅允许管理员在系统域使用")
        waker = wakers.resolve_system_bot_waker()
    else:
        candidates = wakers.resolve_wakers(ctx.workspace_id, ctx.user_role, key, ctx.user_id)
        waker = wakers.default_waker(candidates)
    if not waker:
        raise PermissionError("当前工作空间未授权可用 Waker")
    requested_model = ctx.extra.get("model_ref")
    models = waker.get("models") or []
    if requested_model and requested_model not in models:
        raise PermissionError("所选模型未授权给当前 Waker")
    if not requested_model and models:
        ctx.extra["model_ref"] = models[0]
    return compile_policy(waker, ceiling)


def live_ceiling(ctx, runtime):
    """每次执行读取执行层及工作空间授权，绑定被撤回后禁止继承全局权限。"""
    if not runtime.layer_id:
        return runtime.ceiling
    from services.datamind.execution import service
    from services.datamind.execution.manager import _merge_allowed_tools
    row = service.get_layer(runtime.layer_id)
    if not row or row.get("status") != "active":
        raise PermissionError("执行层已停用或删除")
    config = row.get("config") or {}
    if config.get("mode") != "sdk" or config.get("cli_name") != runtime.backend:
        raise PermissionError("执行层后端配置已变化")
    if config.get("cwd") or config.get("allowed_dirs"):
        raise PermissionError("执行层配置不再满足会话目录隔离要求")
    bindings = service.get_workspace_layers(ctx.workspace_id)
    # 工作空间仅绑定当前层才算授权; 未绑定且存在其它外部层绑定才算“授权被撤回”。
    bound = next((r for r in bindings if r["id"] == runtime.layer_id), None)
    if bound is None and (runtime.layer_bound or bindings):
        raise PermissionError("工作空间的执行层授权已撤回")
    return _merge_allowed_tools(config.get("allowed_tools"), bound.get("allowed_tools") if bound else None)


def check_tool(ctx, name):
    """执行前重新读取权限与 DB 领取版本；旧 SDK 上下文不能保存已撤销授权。"""
    runtime = (ctx.extra or {}).get("secure_runtime")
    if runtime is None:
        raise PermissionError("缺少可信执行会话")
    runtime.verify()
    policy = resolve_policy(ctx, live_ceiling(ctx, runtime))
    if policy.digest != runtime.policy.digest:
        raise PermissionError("Waker 权限已变化，请重新发送消息")
    if name not in policy.allowed:
        raise PermissionError("当前 Waker 未授权该工具")
    return runtime
