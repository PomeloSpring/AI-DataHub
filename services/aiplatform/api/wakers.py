"""Wakers API — 统一"角色化智能体"(Waker)配置与绑定管理.

表: adh_wakers / adh_workspace_wakers / adh_role_wakers
一个 Waker 内联 persona/系统提示词/工具集/skills/图表开关,引用共享 MCP 与数据源。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from services.shared.common.db import execute_query, execute_insert, execute_write
from services.shared.common.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ─────────────────────────────────────────────────

class WakerPayload(BaseModel):
    waker_key: str = ""
    name: str = ""
    display_name: str = ""
    description: str = ""
    category: str = "custom"
    system_prompt: str = ""
    persona: dict = {}
    tools: dict = {}
    mcp_server_ids: list = []
    datasource_ids: list = []
    knowledge_base_ids: list = []
    skills: list = []
    models: list = []
    chart_enabled: bool = True
    permission_mode: str = "inherit"
    workspace_id: int = 0
    is_active: bool = True


class WakerUpdate(BaseModel):
    waker_key: Optional[str] = None
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    system_prompt: Optional[str] = None
    persona: Optional[dict] = None
    tools: Optional[dict] = None
    mcp_server_ids: Optional[list] = None
    datasource_ids: Optional[list] = None
    knowledge_base_ids: Optional[list] = None
    skills: Optional[list] = None
    models: Optional[list] = None
    chart_enabled: Optional[bool] = None
    permission_mode: Optional[str] = None
    workspace_id: Optional[int] = None
    is_active: Optional[bool] = None


class WorkspaceWakerBinding(BaseModel):
    waker_id: int
    is_default: bool = False
    sort: int = 0


class WorkspaceWakersUpdate(BaseModel):
    bindings: list[WorkspaceWakerBinding] = []


class RoleWakersUpdate(BaseModel):
    waker_ids: list[int] = []


# ── helpers ────────────────────────────────────────────────────────

_JSON_FIELDS = ("persona", "tools", "mcp_server_ids", "datasource_ids", "knowledge_base_ids", "skills", "models")
_LIST_FIELDS = ("mcp_server_ids", "datasource_ids", "knowledge_base_ids", "skills", "models")


def _ensure_columns():
    """幂等确保 adh_wakers 含 knowledge_base_ids / models 列(镜像 knowledge_bases 的建表自愈).

    旧库未跑对应 migration 时自动补列,避免 INSERT/UPDATE 报错。
    """
    for ddl in (
        "ALTER TABLE adh_wakers ADD COLUMN knowledge_base_ids JSON "
        "COMMENT '引用的共享知识库 ID 列表' AFTER datasource_ids",
        "ALTER TABLE adh_wakers ADD COLUMN models JSON "
        "COMMENT 'Chat 端可选模型列表(model_ref 字符串数组)' AFTER skills",
    ):
        try:
            execute_write(ddl)
            logger.info("[Wakers] applied: %s", ddl.split("ADD COLUMN")[1].split(" ")[0])
        except Exception as e:  # noqa: BLE001  (1060 duplicate column 等,视为已存在)
            logger.debug("[Wakers] ensure column skipped: %s", e)


# 服务加载即自愈列结构
_ensure_columns()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(row: dict) -> dict:
    for f in _JSON_FIELDS:
        val = row.get(f)
        default = [] if f in _LIST_FIELDS else {}
        if isinstance(val, str):
            try:
                row[f] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                row[f] = default
        elif val is None:
            row[f] = default
    for k in ("created_at", "updated_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()
    return row


# ── Waker CRUD ─────────────────────────────────────────────────────

@router.get("/")
def list_wakers(workspace_id: int = Query(0)):
    """列出 Waker: workspace_id 指定时返回该工作空间绑定 + 全局 Waker。"""
    try:
        if workspace_id:
            rows = execute_query(
                """SELECT DISTINCT w.*, b.is_default
                   FROM adh_wakers w
                   LEFT JOIN adh_workspace_wakers b
                     ON b.waker_id = w.id AND b.workspace_id = %s
                   WHERE w.is_active = 1 AND (w.workspace_id = 0 OR w.workspace_id = %s OR b.id IS NOT NULL)
                   ORDER BY w.workspace_id, w.name""",
                (workspace_id, workspace_id),
            )
        else:
            rows = execute_query("SELECT * FROM adh_wakers ORDER BY workspace_id, name")
        return [_normalize(dict(r)) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.error("List wakers failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/roles")
def list_roles():
    """角色列表(供 Waker 角色绑定下拉)."""
    try:
        rows = execute_query("SELECT id, name, display_name FROM adh_roles ORDER BY id")
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.error("List roles failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{waker_id}")
def get_waker(waker_id: int):
    row = execute_query("SELECT * FROM adh_wakers WHERE id = %s", (waker_id,), fetchone=True)
    if not row:
        raise HTTPException(status_code=404, detail="Waker not found")
    return _normalize(dict(row))


@router.post("/")
def create_waker(req: WakerPayload):
    if not req.name and not req.waker_key:
        raise HTTPException(status_code=400, detail="name / waker_key 至少填一个")
    key = req.waker_key or req.name
    existing = execute_query("SELECT id FROM adh_wakers WHERE waker_key = %s", (key,), fetchone=True)
    if existing:
        raise HTTPException(status_code=400, detail=f"waker_key '{key}' 已存在")
    now = _now()
    try:
        waker_id = execute_insert(
            """INSERT INTO adh_wakers
               (waker_key, name, display_name, description, category, system_prompt,
                persona, tools, mcp_server_ids, datasource_ids, knowledge_base_ids, skills, models, chart_enabled,
                permission_mode, is_active, workspace_id, created_at, updated_at, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'admin')""",
            (
                key, req.name or key, req.display_name, req.description, req.category,
                req.system_prompt,
                json.dumps(req.persona or {}, ensure_ascii=False),
                json.dumps(req.tools or {}, ensure_ascii=False),
                json.dumps(req.mcp_server_ids or [], ensure_ascii=False),
                json.dumps(req.datasource_ids or [], ensure_ascii=False),
                json.dumps(req.knowledge_base_ids or [], ensure_ascii=False),
                json.dumps(req.skills or [], ensure_ascii=False),
                json.dumps(req.models or [], ensure_ascii=False),
                1 if req.chart_enabled else 0,
                req.permission_mode,
                1 if req.is_active else 0,
                req.workspace_id or 0, now, now,
            ),
        )
        return {"id": waker_id, "waker_key": key, "success": True}
    except Exception as e:  # noqa: BLE001
        logger.error("Create waker failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{waker_id}")
def update_waker(waker_id: int, req: WakerUpdate, user: dict = Depends(get_current_user)):
    # 系统内置 Waker 仅 admin 可编辑
    existing = execute_query("SELECT is_builtin FROM adh_wakers WHERE id = %s", (waker_id,), fetchone=True)
    if not existing:
        raise HTTPException(status_code=404, detail="Waker not found")
    if existing.get("is_builtin") and (user.get("role") or "") != "admin":
        raise HTTPException(status_code=403, detail="系统内置 Waker 仅管理员可编辑")
    data = req.model_dump(exclude_none=True)
    if not data:
        return {"success": True, "message": "No changes"}
    updates, params = [], []
    for field in ("waker_key", "name", "display_name", "description", "category",
                  "system_prompt", "permission_mode", "workspace_id"):
        if field in data:
            updates.append(f"{field} = %s")
            params.append(data[field])
    for field in ("persona", "tools", "mcp_server_ids", "datasource_ids", "knowledge_base_ids", "skills", "models"):
        if field in data:
            updates.append(f"{field} = %s")
            params.append(json.dumps(data[field], ensure_ascii=False))
    if "chart_enabled" in data:
        updates.append("chart_enabled = %s")
        params.append(1 if data["chart_enabled"] else 0)
    if "is_active" in data:
        updates.append("is_active = %s")
        params.append(1 if data["is_active"] else 0)
    updates.append("updated_at = %s")
    params.append(_now())
    params.append(waker_id)
    try:
        execute_write(f"UPDATE adh_wakers SET {', '.join(updates)} WHERE id = %s", params)
        return {"success": True}
    except Exception as e:  # noqa: BLE001
        logger.error("Update waker failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{waker_id}")
def delete_waker(waker_id: int):
    row = execute_query("SELECT is_builtin FROM adh_wakers WHERE id = %s", (waker_id,), fetchone=True)
    if not row:
        raise HTTPException(status_code=404, detail="Waker not found")
    if row.get("is_builtin"):
        raise HTTPException(status_code=400, detail="内置 Waker 不可删除")
    execute_write("DELETE FROM adh_workspace_wakers WHERE waker_id = %s", (waker_id,))
    execute_write("DELETE FROM adh_role_wakers WHERE waker_id = %s", (waker_id,))
    execute_write("DELETE FROM adh_wakers WHERE id = %s", (waker_id,))
    return {"success": True}


# ── 工作空间绑定 ───────────────────────────────────────────────────

@router.get("/workspace/{workspace_id}/wakers")
def get_workspace_wakers(workspace_id: int):
    rows = execute_query(
        """SELECT b.waker_id, b.is_default, b.sort, w.name, w.display_name
           FROM adh_workspace_wakers b JOIN adh_wakers w ON w.id = b.waker_id
           WHERE b.workspace_id = %s ORDER BY b.sort, b.id""",
        (workspace_id,),
    )
    return [dict(r) for r in rows]


@router.put("/workspace/{workspace_id}/wakers")
def set_workspace_wakers(workspace_id: int, req: WorkspaceWakersUpdate):
    execute_write("DELETE FROM adh_workspace_wakers WHERE workspace_id = %s", (workspace_id,))
    default_set = False
    for b in req.bindings:
        is_default = bool(b.is_default) and not default_set
        default_set = default_set or is_default
        execute_write(
            "INSERT INTO adh_workspace_wakers (workspace_id, waker_id, is_default, sort) VALUES (%s,%s,%s,%s)",
            (workspace_id, b.waker_id, 1 if is_default else 0, int(b.sort or 0)),
        )
    return {"success": True}


# ── 角色绑定 ───────────────────────────────────────────────────────

@router.get("/role/{role_id}/wakers")
def get_role_wakers(role_id: int):
    rows = execute_query(
        "SELECT waker_id FROM adh_role_wakers WHERE role_id = %s", (role_id,)
    )
    return {"waker_ids": [r["waker_id"] for r in rows]}


@router.put("/role/{role_id}/wakers")
def set_role_wakers(role_id: int, req: RoleWakersUpdate):
    execute_write("DELETE FROM adh_role_wakers WHERE role_id = %s", (role_id,))
    for wid in req.waker_ids:
        execute_write(
            "INSERT INTO adh_role_wakers (role_id, waker_id) VALUES (%s,%s)", (role_id, wid)
        )
    return {"success": True}
