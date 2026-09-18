"""Waker 解析服务 — chat 运行时按 (工作空间 + 用户角色) 解析生效的 Waker 集合.

Waker 是"角色化智能体"的统一配置单元(表 adh_wakers),内联职责/风格/边界(persona)、
系统提示词、工具集、图表开关、skills;MCP 与数据源作为共享资源被引用(ID 列表)。

绑定关系:
- adh_workspace_wakers: 工作空间可用 Waker(is_default 指定默认)
- adh_role_wakers: 角色可用 Waker(按 adh_roles.name = user_role 解析 role_id)

解析规则:
- 候选 = 工作空间绑定 Waker;工作空间无绑定时回退全局 Waker(workspace_id=0)
- 角色白名单 = 该角色绑定的 Waker;角色无绑定时不额外限制
- 生效 = 候选 ∩ 角色白名单(白名单为空则取候选);取不到时回退工作空间默认 Waker
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


def _normalize(row: dict) -> dict:
    """DB 行 → Waker 配置(persona/tools/mcp_server_ids/datasource_ids/skills 解析)."""
    tools = _parse_json(row.get("tools"), {})
    if isinstance(tools, list):  # 兼容早期: tools 直接是组名列表
        tools = {"groups": tools, "standard": []}
    return {
        "id": row.get("id"),
        "waker_key": row.get("waker_key"),
        "name": row.get("name") or row.get("waker_key"),
        "display_name": row.get("display_name") or row.get("name"),
        "description": row.get("description") or "",
        "category": row.get("category") or "custom",
        "system_prompt": row.get("system_prompt") or "",
        "persona": _parse_json(row.get("persona"), {}),
        "tools": {
            "groups": tools.get("groups") or [],
            "standard": tools.get("standard") or [],
            "mcp": tools.get("mcp") or {},
        },
        "mcp_server_ids": _parse_json(row.get("mcp_server_ids"), []),
        "datasource_ids": _parse_json(row.get("datasource_ids"), []),
        "knowledge_base_ids": _parse_json(row.get("knowledge_base_ids"), []),
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
        logger.warning("[Waker] query failed: %s", e)
        return []


def resolve_wakers(workspace_id: int, user_role: str = "", waker_key: str = "") -> list[dict]:
    """解析 (工作空间 + 角色) 生效的 Waker 配置列表(已归一化).

    waker_key 非空时进一步收窄为聊天端选定的单个 Waker(仅当它属于当前
    生效集合时生效,否则忽略该选择回退到全部生效 Waker,防止越权)。
    """
    # 1. 工作空间候选(含默认标记)
    candidates: list[dict] = []
    if workspace_id:
        candidates = [
            _normalize(r)
            for r in _query(
                """SELECT w.*, b.is_default FROM adh_workspace_wakers b
                   JOIN adh_wakers w ON w.id = b.waker_id
                   WHERE b.workspace_id = %s AND w.is_active = 1
                   ORDER BY b.sort, w.id""",
                (workspace_id,),
            )
        ]
    # 2. 工作空间无绑定 → 回退全局 Waker
    if not candidates:
        candidates = [
            _normalize(r)
            for r in _query(
                "SELECT * FROM adh_wakers WHERE is_active = 1 AND workspace_id = 0 ORDER BY id"
            )
        ]
    if not candidates:
        return []

    # 3. 角色白名单
    allowed_ids: Optional[set] = None
    if user_role:
        role_rows = _query("SELECT id FROM adh_roles WHERE name = %s", (user_role,))
        role_ids = [r["id"] for r in role_rows]
        if role_ids:
            placeholders = ", ".join(["%s"] * len(role_ids))
            bind_rows = _query(
                f"SELECT DISTINCT waker_id FROM adh_role_wakers WHERE role_id IN ({placeholders})",
                tuple(role_ids),
            )
            bound = {r["waker_id"] for r in bind_rows}
            # 仅当该角色确有 Waker 授权时才施加限制
            if bound:
                allowed_ids = bound

    effective = candidates
    if allowed_ids is not None:
        filtered = [w for w in candidates if w["id"] in allowed_ids]
        if filtered:
            effective = filtered

    # 4. 聊天端选定的单个 Waker(仅当在生效集合内时收窄;否则保持全部生效)
    if waker_key:
        picked = [
            w for w in effective
            if str(w.get("waker_key")) == str(waker_key) or str(w.get("id")) == str(waker_key)
        ]
        if picked:
            effective = picked

    return effective


def default_waker(wakers: list[dict]) -> Optional[dict]:
    """取默认 Waker(is_default 优先,否则首个)."""
    if not wakers:
        return None
    return next((w for w in wakers if w.get("is_default")), wakers[0])


def collect_mcp_server_ids(wakers: list[dict]) -> list[int]:
    """合并去重所有生效 Waker 引用的 MCP 服务 ID."""
    seen: list[int] = []
    for w in wakers:
        for mid in w.get("mcp_server_ids") or []:
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                continue
            if mid not in seen:
                seen.append(mid)
    return seen


def collect_tool_groups(wakers: list[dict]) -> list[str]:
    """合并去重所有生效 Waker 的工具组名(catalog/query/semantic)."""
    seen: list[str] = []
    for w in wakers:
        for g in (w.get("tools") or {}).get("groups") or []:
            if g and g not in seen:
                seen.append(g)
    return seen


def collect_standard_tools(wakers: list[dict]) -> list[str]:
    """合并去重所有生效 Waker 允许的标准工具名(read/grep/...)."""
    seen: list[str] = []
    for w in wakers:
        for t in (w.get("tools") or {}).get("standard") or []:
            if t and t not in seen:
                seen.append(t)
    return seen


def collect_mcp_tool_selection(wakers: list[dict]) -> dict:
    """合并生效 Waker 的"逐工具"MCP 选择 → {group: [tool_name, ...]}.

    返回非空 dict 时,适配器据此按细粒度注册工具(只注册被勾选的);
    返回空 dict 表示没有任何 waker 配置了 tools.mcp,适配器回退到粗粒度 groups。
    """
    merged: dict[str, list[str]] = {}
    for w in wakers:
        mcp = (w.get("tools") or {}).get("mcp") or {}
        if not isinstance(mcp, dict):
            continue
        for group, tools in mcp.items():
            bucket = merged.setdefault(group, [])
            for t in (tools or []):
                if t and t not in bucket:
                    bucket.append(t)
    return merged


def collect_knowledge_base_ids(wakers: list[dict]) -> list[int]:
    """合并去重所有生效 Waker 引用的知识库 ID."""
    seen: list[int] = []
    for w in wakers:
        for kid in w.get("knowledge_base_ids") or []:
            try:
                kid = int(kid)
            except (TypeError, ValueError):
                continue
            if kid not in seen:
                seen.append(kid)
    return seen


def collect_skill_names(wakers: list[dict]) -> list[str]:
    """合并去重所有生效 Waker 勾选绑定的技能名.

    Waker.skills 现为技能名列表(字符串);兼容早期内联对象 {name/key}。
    """
    seen: list[str] = []
    for w in wakers:
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
        logger.warning("[Waker] skill_loader unavailable: %s", e)
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
    return [by_id[kid] for kid in ids if kid in by_id]


# ═══════════════════════════════════════════════════════════════════
# AS-BOT 系统助手
# ═══════════════════════════════════════════════════════════════════

SYSTEM_BOT_WAKER_KEY = "__system_bot__"

# AS-BOT 写操作动作清单
AS_BOT_WRITE_ACTIONS = [
    "ontology.generate",
    "ontology.save",
    "ontology.activate",
    "ontology.import_yaml",
    "metadata.sync",
]


def resolve_system_bot_waker() -> Optional[dict]:
    """获取系统级 AS-BOT Waker 配置.

    从 adh_wakers 表加载 waker_key='__system_bot__' 的记录.
    该 Waker 绑定项目内置知识库与本体模型能力, 工具集限定为
    catalog + semantic + ontology + screen, 不含 query(禁止裸 SQL 直连数据源).
    semantic 组的 run_semantic_query 与 screen 组的取数都走数据护城河, 继承当前用户权限.
    """
    rows = _query(
        "SELECT * FROM adh_wakers WHERE waker_key = %s AND is_active = 1 LIMIT 1",
        (SYSTEM_BOT_WAKER_KEY,),
    )
    if not rows:
        logger.warning("[AS-BOT] System bot waker not found in adh_wakers")
        return None
    waker = _normalize(rows[0])
    # 强制覆盖工具组: catalog + semantic + ontology + screen, 禁止 query(裸SQL)
    waker["tools"]["groups"] = ["catalog", "semantic", "ontology", "screen"]
    return waker


def check_as_bot_permission(user_role: str, action_key: str) -> bool:
    """检查用户角色是否有权执行 AS-BOT 写操作.

    admin 角色始终允许; 其他角色查 adh_as_bot_role_actions 表.
    未配置时默认拒绝( fail-closed ).
    """
    if user_role == "admin":
        return True
    if not user_role or not action_key:
        return False
    role_rows = _query("SELECT id FROM adh_roles WHERE name = %s", (user_role,))
    if not role_rows:
        return False
    role_id = role_rows[0]["id"]
    rows = _query(
        "SELECT is_allowed FROM adh_as_bot_role_actions "
        "WHERE role_id = %s AND action_key = %s LIMIT 1",
        (role_id, action_key),
    )
    if not rows:
        return False  # fail-closed: 未配置即拒绝
    return bool(rows[0].get("is_allowed"))


def get_as_bot_role_permissions(user_role: str) -> dict[str, bool]:
    """获取用户角色在 AS-BOT 中的所有动作权限."""
    if user_role == "admin":
        return {action: True for action in AS_BOT_WRITE_ACTIONS}
    role_rows = _query("SELECT id FROM adh_roles WHERE name = %s", (user_role,))
    if not role_rows:
        return {action: False for action in AS_BOT_WRITE_ACTIONS}
    role_id = role_rows[0]["id"]
    rows = _query(
        "SELECT action_key, is_allowed FROM adh_as_bot_role_actions WHERE role_id = %s",
        (role_id,),
    )
    perm_map = {r["action_key"]: bool(r["is_allowed"]) for r in rows}
    return {action: perm_map.get(action, False) for action in AS_BOT_WRITE_ACTIONS}


def set_as_bot_role_permissions(role_id: int, permissions: dict[str, bool]):
    """设置角色的 AS-BOT 动作权限(upsert)."""
    from services.shared.common.db import execute_query

    for action_key, is_allowed in permissions.items():
        if action_key not in AS_BOT_WRITE_ACTIONS:
            continue
        try:
            execute_query(
                "INSERT INTO adh_as_bot_role_actions (role_id, action_key, is_allowed) "
                "VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE is_allowed = VALUES(is_allowed)",
                (role_id, action_key, int(is_allowed)),
            )
        except Exception as e:
            logger.warning("[AS-BOT] Failed to set permission %s for role %s: %s", action_key, role_id, e)


def create_approval(user_id: int, action_key: str, payload: dict, conversation_id: int = None) -> int:
    """创建审批记录, 返回 approval_id."""
    from services.shared.common.db import execute_query
    import json as _json

    try:
        execute_query(
            "INSERT INTO adh_as_bot_approvals (user_id, action_key, payload, conversation_id) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, action_key, _json.dumps(payload, ensure_ascii=False, default=str), conversation_id),
        )
        # 获取最新插入 ID
        rows = _query("SELECT LAST_INSERT_ID() as id")
        return rows[0]["id"] if rows else 0
    except Exception as e:
        logger.error("[AS-BOT] Failed to create approval: %s", e)
        return 0


def get_approval(approval_id: int) -> Optional[dict]:
    """获取审批记录."""
    rows = _query(
        "SELECT * FROM adh_as_bot_approvals WHERE id = %s LIMIT 1",
        (approval_id,),
    )
    return rows[0] if rows else None


def update_approval_status(approval_id: int, status: str, decided_by: int, result: dict = None):
    """更新审批状态."""
    from services.shared.common.db import execute_query
    import json as _json
    from datetime import datetime

    try:
        result_json = _json.dumps(result, ensure_ascii=False, default=str) if result else None
        execute_query(
            "UPDATE adh_as_bot_approvals SET status = %s, decided_by = %s, "
            "decided_at = %s, result = %s WHERE id = %s",
            (status, decided_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             result_json, approval_id),
        )
    except Exception as e:
        logger.error("[AS-BOT] Failed to update approval %s: %s", approval_id, e)
