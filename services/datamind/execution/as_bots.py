"""AS-BOT 解析服务 — chat 运行时按 (工作空间 + 用户角色) 解析生效的 AS-BOT.

AS-BOT 是"角色化智能体"的统一配置单元(表 adh_as_bots),内联职责/风格/边界(persona)、
系统提示词、工具集、图表开关、skills;MCP 与数据源作为共享资源被引用(ID 列表)。
AS-BOT 与智能问数是同一载体、不同入口(见 as-bot-system-waker 规则)。

绑定关系(角色-AS-BOT 一对一):
- adh_as_bots.role_id: 每个角色有且仅有一个 AS-BOT(唯一索引)
- adh_workspace_as_bots: 工作空间可用 AS-BOT 授权(已废弃,保留兼容)

解析规则:
- 按用户角色直接查 adh_as_bots.role_id(一对一绑定)
- 角色无 AS-BOT 时返回空列表

写动作(原 AS-BOT 动作矩阵/审批通道)已退役: 动作权限由菜单与功能权限码
(adh_perm_registry + adh_role_perms, 经 perm_link 裁决) + AS-BOT 工具授权直接把关,
不再有提议→审批→执行回路。
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _resource_ids(raw):
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("AS-BOT 资源绑定不是合法 JSON") from exc
    if not isinstance(raw, list) or any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in raw):
        raise ValueError("AS-BOT 资源绑定必须为正整数数组")
    return list(dict.fromkeys(raw))


def _normalize(row: dict) -> dict:
    """DB 行 → AS-BOT 配置(persona/tools/mcp_server_ids/knowledge_base_ids/skills 解析；
    数据源范围不属 AS-BOT 配置，始终由工作空间绑定∩用户角色权限决定)."""
    from services.datamind.execution.tool_policy import normalize_tools
    tools = normalize_tools(row.get("tools"))
    return {
        "id": row.get("id"),
        "as_bot_key": row.get("as_bot_key"),
        "name": row.get("name") or row.get("as_bot_key"),
        "display_name": row.get("display_name") or row.get("name"),
        "description": row.get("description") or "",
        "category": row.get("category") or "custom",
        "system_prompt": row.get("system_prompt") or "",
        "persona": _parse_json(row.get("persona"), {}),
        "tools": tools,
        "mcp_server_ids": _resource_ids(row.get("mcp_server_ids")),
        "knowledge_base_ids": _resource_ids(row.get("knowledge_base_ids")),
        "skills": _parse_json(row.get("skills"), []),
        "models": _parse_json(row.get("models"), []),
        "chart_enabled": bool(row.get("chart_enabled", 1)),
        "permission_mode": row.get("permission_mode") or "inherit",
        "workspace_id": row.get("workspace_id") or 0,
        "is_default": bool(row.get("is_default", 0)),
    }


def _query(sql: str, params: tuple = ()) -> list[dict]:
    from services.shared.common.db import execute_query

    try:
        return list(execute_query(sql, params) or [])
    except Exception as e:
        logger.exception("[AS-BOT] 权限配置查询失败")
        raise RuntimeError("AS-BOT 权限服务暂不可用") from e


def resolve_as_bots(workspace_id: int, user_role: str = "", as_bot_key: str = "", user_id: int = 0,
                    include_unavailable: bool = False) -> list[dict]:
    """解析用户角色对应的 AS-BOT 配置(角色-AS-BOT 一对一绑定).

    按用户角色直接查 adh_as_bots.role_id,返回该角色的 AS-BOT.
    as_bot_key 传入时用于严格校验/收窄(与角色绑定不符即拒绝,fail-closed,定时任务等按存储 key 走此路);
    聊天端不传(空)则不收窄,由 default_as_bot 取当前角色的默认 AS-BOT(前端不再传 asBotId).
    """
    # 解析用户角色 ID
    if user_id:
        from services.authservice.services.role_service import role_service
        role_rows = role_service.get_user_roles(user_id, workspace_id)
    else:
        role_rows = _query("SELECT id FROM adh_roles WHERE name = %s", (user_role,)) if user_role else []
    role_ids = [r["id"] for r in role_rows]
    if not role_ids:
        return []

    # 按 role_id 直接查 AS-BOT(角色-AS-BOT 一对一)
    placeholders = ", ".join(["%s"] * len(role_ids))
    candidates = _query(
        f"SELECT * FROM adh_as_bots WHERE role_id IN ({placeholders}) AND is_active = 1 ORDER BY id",
        tuple(role_ids),
    )
    if not candidates:
        if as_bot_key:
            raise PermissionError("当前角色未配置 AS-BOT")
        return []

    # as_bot_key 传入时严格收窄/校验(定时任务等按存储 key fail-closed，未知即拒不回退默认)；
    # 聊天端不传(空)则不收窄，由 default_as_bot 取当前角色的默认 AS-BOT(角色-AS-BOT 一对一)。
    effective = list(candidates)
    if as_bot_key:
        effective = [w for w in effective if w["as_bot_key"] == as_bot_key]
        if not effective:
            raise PermissionError("未授权使用所选 AS-BOT")
    result = []
    for row in effective:
        try:
            as_bot = _normalize(row)
            if include_unavailable:
                from services.datamind.execution.tool_policy import compile_policy
                compile_policy(as_bot)
            result.append(as_bot)
        except ValueError as exc:
            if not include_unavailable:
                raise
            logger.warning("AS-BOT 配置不可用: %s: %s", row.get("as_bot_key"), exc)
            result.append({**{k: row.get(k) for k in ("id", "as_bot_key", "name", "display_name")},
                           "available": False, "unavailable_reason": str(exc), "models": []})
    return result


def default_as_bot(as_bots: list[dict]) -> Optional[dict]:
    """取默认 AS-BOT(is_default 优先,否则首个)."""
    if not as_bots:
        return None
    return next((w for w in as_bots if w.get("is_default")), as_bots[0])


def visible_resource_ids(user: dict, workspace_id: int, field: str) -> list[int]:
    """目录只读投影；执行权限仍由会话选定的单个 AS-BOT 决定。"""
    from services.shared.common.auth import authorize_workspace
    from services.datamind.execution.tool_policy import compile_policy
    if field not in ("mcp_server_ids", "knowledge_base_ids"):
        raise ValueError("不支持的 AS-BOT 资源类型")
    authorize_workspace(user, workspace_id)
    resolved = resolve_as_bots(workspace_id, user.get("role", ""), user_id=user["user_id"])
    ids = set()
    for as_bot in resolved:
        policy = compile_policy(as_bot)
        if field == "mcp_server_ids":
            ids.update(int(sid) for sid, names in policy.external.items() if names)
        else:
            ids.update(_resource_ids(as_bot.get(field)))
    return sorted(ids)


def collect_mcp_server_ids(as_bots: list[dict]) -> list[int]:
    """合并去重所有生效 AS-BOT 引用的 MCP 服务 ID."""
    seen: list[int] = []
    for w in as_bots:
        for mid in w.get("mcp_server_ids") or []:
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                continue
            if mid not in seen:
                seen.append(mid)
    return seen


def collect_tool_groups(as_bots: list[dict]) -> list[str]:
    """合并去重所有生效 AS-BOT 的工具组名(catalog/query/semantic)."""
    seen: list[str] = []
    for w in as_bots:
        for g in (w.get("tools") or {}).get("groups") or []:
            if g and g not in seen:
                seen.append(g)
    return seen


def collect_standard_tools(as_bots: list[dict]) -> list[str]:
    """合并去重所有生效 AS-BOT 允许的标准工具名(read/grep/...)."""
    seen: list[str] = []
    for w in as_bots:
        for t in (w.get("tools") or {}).get("standard") or []:
            if t and t not in seen:
                seen.append(t)
    return seen


def collect_mcp_tool_selection(as_bots: list[dict]) -> dict:
    """合并生效 AS-BOT 的"逐工具"MCP 选择 → {group: [tool_name, ...]}.

    返回非空 dict 时,适配器据此按细粒度注册工具(只注册被勾选的);
    返回空 dict 表示没有任何 AS-BOT 配置了 tools.mcp,适配器回退到粗粒度 groups。
    """
    merged: dict[str, list[str]] = {}
    for w in as_bots:
        mcp = (w.get("tools") or {}).get("mcp") or {}
        if not isinstance(mcp, dict):
            continue
        for group, tools in mcp.items():
            bucket = merged.setdefault(group, [])
            for t in (tools or []):
                if t and t not in bucket:
                    bucket.append(t)
    return merged


def collect_knowledge_base_ids(as_bots: list[dict]) -> list[int]:
    """合并去重所有生效 AS-BOT 引用的知识库 ID."""
    seen: list[int] = []
    for w in as_bots:
        for kid in w.get("knowledge_base_ids") or []:
            try:
                kid = int(kid)
            except (TypeError, ValueError):
                continue
            if kid not in seen:
                seen.append(kid)
    return seen


def collect_skill_names(as_bots: list[dict]) -> list[str]:
    """合并去重所有生效 AS-BOT 勾选绑定的技能名.

    AS-BOT.skills 现为技能名列表(字符串);兼容早期内联对象 {name/key}。
    """
    seen: list[str] = []
    for w in as_bots:
        for s in w.get("skills") or []:
            if isinstance(s, str):
                name = s
            elif isinstance(s, dict):
                name = s.get("name") or s.get("key") or ""
            else:
                continue
            if name and name not in seen:
                seen.append(name)
    return seen


def load_skills(skill_names: list[str]) -> list[dict]:
    """按名加载已绑定技能(name/display_name/system_prompt),供提示词注入.

    加载失败或技能不存在时跳过该项(行为容错)。
    """
    out: list[dict] = []
    if not skill_names:
        return out
    try:
        from services.datamind.config.skill_loader import load_skill
    except Exception as e:  # noqa: BLE001
        logger.warning("[AS-BOT] skill_loader unavailable: %s", e)
        return out
    for name in skill_names:
        try:
            skill = load_skill(name)
        except Exception:  # noqa: BLE001
            skill = None
        if skill and skill.get("system_prompt"):
            out.append({
                "name": skill.get("name") or name,
                "display_name": skill.get("display_name") or name,
                "system_prompt": skill.get("system_prompt") or "",
            })
    return out


def load_knowledge_bases(kb_ids: list[int]) -> list[dict]:
    """按 ID 加载知识库(id/name/kb_type/status),供系统提示词展示与运行时检索.

    仅返回启用(active)且命中的知识库,保持与 kb_ids 传入顺序一致的排序。
    """
    ids: list[int] = []
    for kid in kb_ids or []:
        try:
            kid = int(kid)
        except (TypeError, ValueError):
            continue
        if kid not in ids:
            ids.append(kid)
    if not ids:
        return []
    placeholders = ", ".join(["%s"] * len(ids))
    rows = _query(
        f"SELECT id, name, kb_type, status FROM adh_knowledge_bases "
        f"WHERE id IN ({placeholders}) AND status = 'active'",
        tuple(ids),
    )
    by_id = {r["id"]: r for r in rows}
    missing, extra = set(ids) - set(by_id), set(by_id) - set(ids)
    # 严格相等：缺失=绑定漂移；越界返回=IN 过滤失效，均 fail-loud 不静默
    if missing or extra:
        raise ValueError(
            f"AS-BOT 绑定的知识库校验失败: 缺失/未启用 {sorted(missing)}，越界返回 {sorted(extra)}，请检查绑定")
    return [by_id[kid] for kid in ids]
