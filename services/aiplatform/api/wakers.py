"""Wakers API — 统一"角色化智能体"(Waker)配置管理.

表: adh_wakers（角色-Waker 一对一绑定，adh_wakers.role_id）
一个 Waker 内联 persona/系统提示词/工具集/skills/图表开关,引用共享 MCP 与数据源。
Waker 只绑定到用户角色，不绑定到工作空间。
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
    knowledge_base_ids: Optional[list] = None
    skills: Optional[list] = None
    models: Optional[list] = None
    chart_enabled: Optional[bool] = None
    permission_mode: Optional[str] = None
    workspace_id: Optional[int] = None
    is_active: Optional[bool] = None



# ── helpers ────────────────────────────────────────────────────────

_JSON_FIELDS = ("persona", "tools", "mcp_server_ids", "knowledge_base_ids", "skills", "models")
_LIST_FIELDS = ("mcp_server_ids", "knowledge_base_ids", "skills", "models")


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
def list_wakers():
    """列出所有 Waker（Waker 只绑定到角色，不绑定到工作空间）。"""
    try:
        rows = execute_query("SELECT * FROM adh_wakers ORDER BY name")
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


# 工具目录展示元数据(供 Waker 编辑器渲染): 以 TOOL_SERVER_TOOLS 为权威工具清单,
# 这里只补中文标签; 新增工具未登记标签时回退原始名(仍会展示, 不丢失)。
_TOOL_GROUP_META = {
    "catalog": ("元数据检索", "数据目录类工具(物理表/列/数据源, 对 LLM 相对敏感)"),
    "semantic": ("业务语义", "统一语义层主路(发现对象/指标/知识 + 声明式查询)"),
    "query": ("SQL 执行", "nl2sql 受治理旁路: check_sql 预检 + execute_sql 只读执行(均经权限/RLS/审计)"),
    "ontology": ("本体模型", "本体建模与元数据管理工具"),
    "screen": ("可视化大屏", "生成/管理数据大屏 + 字模库(取数走治理入口)"),
    "system": ("系统运营", "本系统只读运营/可观测能力(仅管理员)"),
}
_TOOL_LABELS = {
    "search_metadata": "元数据搜索", "get_table_schema": "表结构", "list_datasources": "数据源列表",
    "knowledge_search": "知识检索", "get_metrics": "指标", "get_glossary": "术语",
    "query_by_tags": "标签查询", "run_semantic_query": "语义查询",
    "get_business_semantics": "业务语义", "search_business_knowledge": "业务知识检索",
    "check_sql": "SQL 校验", "execute_sql": "执行 SQL",
    "search_ontology": "搜索本体", "get_ontology_model": "模型详情", "list_ontology_models": "模型列表",
    "get_metadata_summary": "元数据摘要", "generate_ontology_draft": "生成草案*", "save_ontology_model": "保存模型*",
    "activate_ontology_model": "激活模型*", "import_ontology_yaml": "导入YAML*",
    "create_data_screen": "创建大屏*", "get_data_screen": "查看大屏", "update_data_screen_chart": "更新图表*",
    "request_dashboard_design": "申请仪表盘设计", "get_dashboard_design": "读取仪表盘设计", "prepare_dashboard_design": "准备仪表盘设计",
    "list_vis_components": "字模列表", "get_vis_component": "字模详情", "save_vis_component": "存为字模*",
    "system_usage": "用量统计", "system_overview": "系统概览",
}


@router.get("/tools/catalog")
def tool_catalog(user: dict = Depends(get_current_user)):
    from services.datamind.execution.sdk_tools import TOOL_SERVER_TOOLS
    from services.datamind.execution.tool_catalog import TOOL_CATALOG
    return {"standard": TOOL_CATALOG, "groups": [
        {"group": g,
         "label": _TOOL_GROUP_META.get(g, (g, ""))[0],
         "desc": _TOOL_GROUP_META.get(g, (g, ""))[1] or "按实际工具名授权，未勾选即禁止",
         "tools": [{"name": n, "label": _TOOL_LABELS.get(n, n)} for n in names]}
        for g, (_, names) in TOOL_SERVER_TOOLS.items()
    ]}


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
    from services.datamind.execution.tool_policy import compile_policy
    try:
        compile_policy(req.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    key = req.waker_key or req.name
    existing = execute_query("SELECT id FROM adh_wakers WHERE waker_key = %s", (key,), fetchone=True)
    if existing:
        raise HTTPException(status_code=400, detail=f"waker_key '{key}' 已存在")
    now = _now()
    try:
        waker_id = execute_insert(
            """INSERT INTO adh_wakers
               (waker_key, name, display_name, description, category, system_prompt,
                persona, tools, mcp_server_ids, knowledge_base_ids, skills, models, chart_enabled,
                permission_mode, is_active, workspace_id, created_at, updated_at, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'admin')""",
            (
                key, req.name or key, req.display_name, req.description, req.category,
                req.system_prompt,
                json.dumps(req.persona or {}, ensure_ascii=False),
                json.dumps(req.tools or {}, ensure_ascii=False),
                json.dumps(req.mcp_server_ids or [], ensure_ascii=False),
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
    existing = execute_query("SELECT * FROM adh_wakers WHERE id = %s", (waker_id,), fetchone=True)
    if not existing:
        raise HTTPException(status_code=404, detail="Waker not found")
    if existing.get("is_builtin") and (user.get("role") or "") != "admin":
        raise HTTPException(status_code=403, detail="系统内置 Waker 仅管理员可编辑")
    data = req.model_dump(exclude_none=True)
    if "tools" in data or "mcp_server_ids" in data:
        from services.datamind.execution.tool_policy import compile_policy
        try:
            compile_policy({**_normalize(dict(existing)), **data})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if not data:
        return {"success": True, "message": "No changes"}
    updates, params = [], []
    for field in ("waker_key", "name", "display_name", "description", "category",
                  "system_prompt", "permission_mode", "workspace_id"):
        if field in data:
            updates.append(f"{field} = %s")
            params.append(data[field])
    for field in ("persona", "tools", "mcp_server_ids", "knowledge_base_ids", "skills", "models"):
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
    row = execute_query("SELECT is_builtin, name, role_id FROM adh_wakers WHERE id = %s", (waker_id,), fetchone=True)
    if not row:
        raise HTTPException(status_code=404, detail="Waker not found")
    # 角色-Waker 一对一绑定: Waker 不允许直接删除，必须通过删除角色联动删除
    raise HTTPException(
        status_code=400,
        detail=f"Waker「{row.get('name')}」与角色绑定，不允许直接删除。请通过删除对应角色来联动删除 Waker。"
    )


