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


def _resource_ids(raw):
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError("Waker 资源绑定不是合法 JSON") from exc
    if not isinstance(raw, list) or any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in raw):
        raise ValueError("Waker 资源绑定必须为正整数数组")
    return list(dict.fromkeys(raw))


def _normalize(row: dict) -> dict:
    """DB 行 → Waker 配置(persona/tools/mcp_server_ids/datasource_ids/skills 解析)."""
    from services.datamind.execution.tool_policy import normalize_tools
    tools = normalize_tools(row.get("tools"))
    return {
        "id": row.get("id"),
        "waker_key": row.get("waker_key"),
        "name": row.get("name") or row.get("waker_key"),
        "display_name": row.get("display_name") or row.get("name"),
        "description": row.get("description") or "",
        "category": row.get("category") or "custom",
        "system_prompt": row.get("system_prompt") or "",
        "persona": _parse_json(row.get("persona"), {}),
        "tools": tools,
        "mcp_server_ids": _resource_ids(row.get("mcp_server_ids")),
        "datasource_ids": _resource_ids(row.get("datasource_ids")),
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
        logger.exception("[Waker] 权限配置查询失败")
        raise RuntimeError("Waker 权限服务暂不可用") from e


def resolve_wakers(workspace_id: int, user_role: str = "", waker_key: str = "", user_id: int = 0,
                   include_unavailable: bool = False) -> list[dict]:
    """解析 (工作空间 + 角色) 生效的 Waker 配置列表(已归一化).

    waker_key 非空时进一步收窄为聊天端选定的单个 Waker(仅当它属于当前
    生效集合时生效,否则忽略该选择回退到全部生效 Waker,防止越权)。
    """
    # 1. 工作空间候选(含默认标记)
    candidates: list[dict] = []
    if workspace_id:
        candidates = [
            dict(r)
            for r in _query(
                """SELECT w.*, b.is_default FROM adh_workspace_wakers b
                   JOIN adh_wakers w ON w.id = b.waker_id
                   WHERE b.workspace_id = %s AND w.is_active = 1
                   ORDER BY b.sort, w.id""",
                (workspace_id,),
            )
        ]
    # 全局域只在明确选择全局工作空间时使用，不因业务工作空间无绑定而放权。
    if not workspace_id:
        candidates = [
            dict(r)
            for r in _query(
                "SELECT * FROM adh_wakers WHERE is_active = 1 AND workspace_id = 0 ORDER BY id"
            )
        ]
    if not candidates:
        if waker_key:
            raise PermissionError("当前工作空间未授权所选 Waker")
        return []

    # 普通会话不允许经全局候选隐式进入系统助手。
    candidates = [w for w in candidates if w["waker_key"] != SYSTEM_BOT_WAKER_KEY]
    effective = candidates if user_role == "admin" else []
    if user_role != "admin":
        if user_id:
            from services.authservice.services.role_service import role_service
            role_rows = role_service.get_user_roles(user_id, workspace_id)
        else:
            role_rows = _query("SELECT id FROM adh_roles WHERE name = %s", (user_role,)) if user_role else []
        role_ids = [r["id"] for r in role_rows]
        if role_ids:
            placeholders = ", ".join(["%s"] * len(role_ids))
            allowed = {r["waker_id"] for r in _query(
                f"SELECT DISTINCT waker_id FROM adh_role_wakers WHERE role_id IN ({placeholders})",
                tuple(role_ids),
            )}
            effective = [w for w in candidates if w["id"] in allowed]
    if waker_key:
        effective = [w for w in effective if w["waker_key"] == waker_key]
        if not effective:
            raise PermissionError("未授权使用所选 Waker")
    result = []
    for row in effective:
        try:
            waker = _normalize(row)
            if include_unavailable:
                from services.datamind.execution.tool_policy import compile_policy
                compile_policy(waker)
            result.append(waker)
        except ValueError as exc:
            if not include_unavailable:
                raise
            logger.warning("Waker 配置不可用: %s: %s", row.get("waker_key"), exc)
            result.append({**{k: row.get(k) for k in ("id", "waker_key", "name", "display_name", "is_default")},
                           "available": False, "unavailable_reason": str(exc), "models": []})
    return result


def default_waker(wakers: list[dict]) -> Optional[dict]:
    """取默认 Waker(is_default 优先,否则首个)."""
    if not wakers:
        return None
    return next((w for w in wakers if w.get("is_default")), wakers[0])


def visible_resource_ids(user: dict, workspace_id: int, field: str) -> list[int]:
    """目录只读投影；执行权限仍由会话选定的单个 Waker 决定。"""
    from services.shared.common.auth import authorize_workspace
    from services.datamind.execution.tool_policy import compile_policy
    if field not in ("mcp_server_ids", "knowledge_base_ids"):
        raise ValueError("不支持的 Waker 资源类型")
    authorize_workspace(user, workspace_id)
    resolved = resolve_wakers(workspace_id, user.get("role", ""), user_id=user["user_id"])
    ids = set()
    for waker in resolved:
        policy = compile_policy(waker)
        if field == "mcp_server_ids":
            ids.update(int(sid) for sid, names in policy.external.items() if names)
        else:
            ids.update(_resource_ids(waker.get(field)))
    return sorted(ids)


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
    if set(by_id) != set(ids):
        raise ValueError("Waker 绑定的知识库不存在或未启用，请检查绑定")
    return [by_id[kid] for kid in ids]


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
    "task.claim_owner",
    "alias.approve",
    "alias.reject",
    "dashboard.publish",
]

# 每个动作的参数 schema(提议创建时即校验, 不等执行期 payload.get 兑底)。
# required: 必填字段; ints/strs/enum: 类型与取值约束。
AS_BOT_ACTION_SCHEMAS: dict[str, dict] = {
    "ontology.generate": {"required": ["datasource_id"], "ints": ["datasource_id"]},
    "ontology.save": {"required": ["model_id", "json_content"],
                      "ints": ["model_id"], "strs": ["json_content"]},
    "ontology.activate": {"required": ["model_id"], "ints": ["model_id"]},
    "ontology.import_yaml": {"required": ["dir", "datasource_id"],
                             "ints": ["datasource_id"], "strs": ["dir"]},
    "metadata.sync": {"required": ["datasource_id"], "positive_ints": ["datasource_id"]},
    "task.claim_owner": {"required": ["task_id"], "positive_ints": ["task_id"]},
    "alias.approve": {"required": ["target_type", "term"],
                      "ints": ["suggestion_id"],
                      "enum": {"target_type": ["object", "metric", "dimension"]}},
    "alias.reject": {"required": ["suggestion_id"], "ints": ["suggestion_id"]},
    "dashboard.publish": {"required": ["design_id", "version", "digest"],
                          "positive_ints": ["version"], "strs": ["design_id", "digest"]},
}


def validate_action_payload(action_key: str, payload: dict) -> tuple[bool, str]:
    """校验 AS-BOT 动作参数。返回 (是否合法, 错误描述)。未注册 schema 的动作不校验。"""
    schema = AS_BOT_ACTION_SCHEMAS.get(action_key)
    if not schema or action_key not in AS_BOT_WRITE_ACTIONS:
        return False, "动作未注册"
    if not isinstance(payload, dict):
        return False, "参数必须为对象"
    if action_key == "dashboard.publish":
        import re
        if set(payload) != {"design_id", "version", "digest"}:
            return False, "发布仅允许设计 ID、版本和摘要"
        if not re.fullmatch(r"[a-f0-9]{32}", str(payload.get("design_id", ""))) or not re.fullmatch(r"[a-f0-9]{64}", str(payload.get("digest", ""))):
            return False, "设计标识或摘要无效"
    for field in schema.get("positive_ints") or []:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return False, f"参数 {field} 必须为正整数"
    for field in schema.get("required") or []:
        v = payload.get(field)
        if v is None or (isinstance(v, str) and not v.strip()):
            return False, f"缺少必填参数: {field}"
    for field in schema.get("ints") or []:
        if field in payload and payload[field] is not None:
            try:
                int(payload[field])
            except (TypeError, ValueError):
                return False, f"参数 {field} 必须为整数"
    for field in schema.get("strs") or []:
        if field in payload and not isinstance(payload[field], str):
            return False, f"参数 {field} 必须为字符串"
    for field, allowed in (schema.get("enum") or {}).items():
        if field in payload and payload[field] not in allowed:
            return False, f"参数 {field} 取值必须是 {allowed} 之一"
    return True, ""


def resolve_system_bot_waker() -> Optional[dict]:
    """获取系统级 AS-BOT Waker 配置.

    从 adh_wakers 表加载 waker_key='__system_bot__' 的记录.
    该 Waker 绑定项目内置知识库与本体模型能力, 工具集限定为
    catalog + semantic + ontology + screen + system, 不含 query(禁止裸 SQL 直连数据源).
    semantic 组的 run_semantic_query 与 screen 组的取数都走数据护城河, 继承当前用户权限.
    system 组为本系统只读运营/可观测能力(system_usage/system_overview), 仅管理员可用.
    """
    rows = _query(
        "SELECT * FROM adh_wakers WHERE waker_key = %s AND is_active = 1 LIMIT 1",
        (SYSTEM_BOT_WAKER_KEY,),
    )
    if not rows:
        logger.warning("[AS-BOT] System bot waker not found in adh_wakers")
        return None
    row = dict(rows[0])
    row["tools"] = {}
    row["mcp_server_ids"] = []
    waker = _normalize(row)
    # 强制覆盖工具组: catalog + semantic + ontology + screen + system, 禁止 query(裸SQL)
    # system 组提供本系统只读运营/可观测能力(仅管理员), 让 AS-BOT 聚焦本系统而非业务本体。
    waker["tools"]["groups"] = ["catalog", "semantic", "ontology", "screen", "system"]
    from services.datamind.execution.sdk_tools import TOOL_SERVER_TOOLS
    waker["tools"]["mcp"] = {g: list(TOOL_SERVER_TOOLS[g][1]) for g in waker["tools"]["groups"]}
    waker["tools"]["standard"] = []
    waker["mcp_server_ids"] = []
    waker["tools"]["external"] = {}
    return waker


def check_as_bot_permission(user_role: str, action_key: str) -> bool:
    """检查用户角色是否有权执行 AS-BOT 写操作.

    admin 角色始终允许; 其他角色查 adh_as_bot_role_actions 表.
    未配置时默认拒绝( fail-closed ).
    """
    if action_key not in AS_BOT_WRITE_ACTIONS:
        return False
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


def create_approval(user_id: int, action_key: str, payload: dict, conversation_id: int = None, *, cursor=None) -> int:
    """创建审批记录, 返回 approval_id.

    提议时即校验参数 schema(不合法直接拒绝写入), 避免到执行期才暴露参数缺失;
    未注册的 action_key 也在此拦截。
    """
    from services.shared.common.db import execute_insert
    import json as _json

    if action_key not in AS_BOT_WRITE_ACTIONS:
        logger.warning("[AS-BOT] create_approval 拒绝未注册动作: %s", action_key)
        return 0
    ok, err = validate_action_payload(action_key, payload)
    if not ok:
        logger.warning("[AS-BOT] create_approval 参数校验失败 %s: %s", action_key, err)
        return 0

    if action_key == "dashboard.publish" and cursor is None:
        raise ValueError("仪表盘发布提议只能由成功预览事务创建")
    if cursor is not None:
        cursor.execute("INSERT INTO adh_as_bot_approvals (user_id,action_key,payload,conversation_id,created_at) "
                       "VALUES (%s,%s,%s,%s,UTC_TIMESTAMP())",
                       (user_id, action_key, _json.dumps(payload, ensure_ascii=False), conversation_id))
        return cursor.lastrowid
    try:
        return execute_insert(
            "INSERT INTO adh_as_bot_approvals (user_id, action_key, payload, conversation_id) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, action_key, _json.dumps(payload, ensure_ascii=False, default=str), conversation_id),
        )
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


def claim_approval(approval_id: int, decided_by: int) -> bool:
    """唯一执行领取点；并发批准只能有一方获得执行权。"""
    from services.shared.common.db import execute_write
    return execute_write("UPDATE adh_as_bot_approvals SET status='executing', decided_by=%s, "
        "decided_at=NOW() WHERE id=%s AND status='pending'", (decided_by, approval_id)) == 1


def update_approval_status(approval_id: int, status: str, decided_by: int, result: dict = None):
    """拒绝只修改 pending；执行结果只能由领取者完成。写入失败必须可见。"""
    from services.shared.common.db import execute_write
    expected = "pending" if status == "rejected" else "executing"
    if status not in ("rejected", "executed", "failed"):
        raise ValueError("不合法的审批终态")
    return execute_write(
        "UPDATE adh_as_bot_approvals SET status=%s, decided_by=%s, decided_at=NOW(), result=%s "
        "WHERE id=%s AND status=%s AND (%s='pending' OR decided_by=%s)",
        (status, decided_by, json.dumps(result, ensure_ascii=False, default=str) if result else None,
         approval_id, expected, expected, decided_by),
    ) == 1
