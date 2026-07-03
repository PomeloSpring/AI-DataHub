"""Admin API — Loop Engineering Workflow and Prompt Management."""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)

from backend.api.auth import require_admin, get_current_user
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn
from backend.models.schemas import (
    UserInfo,
    PromptCreate, PromptUpdate, PromptResponse, PromptVersionResponse, PromptListResponse,
    WorkflowConfigCreate, WorkflowConfigUpdate, WorkflowConfigResponse, WorkflowListResponse,
    WorkflowStepUpdate, WorkflowStepResponse,
    WorkflowLogResponse, WorkflowLogListResponse,
    WorkflowEdgeCreate, WorkflowDAGConfig,
)

router = APIRouter()


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id():
    return int(_time.time() * 1000000)


# ════════════════════════════════════════════════════════════════════
# Prompt Management
# ════════════════════════════════════════════════════════════════════

@router.get("/prompts", response_model=PromptListResponse)
def list_prompts(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query("", description="搜索Prompt名称或Key"),
    admin: UserInfo = Depends(require_admin),
):
    """获取Prompt列表"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if search:
                conditions.append("(prompt_key LIKE %s OR prompt_name LIKE %s)")
                params.extend([f"%{search}%", f"%{search}%"])
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_prompts {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                f"description, version, is_active, created_at, updated_at, created_by, change_log "
                f"FROM adh_prompts {where} "
                f"ORDER BY prompt_key, version DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
                r["is_active"] = bool(r.get("is_active"))
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/prompts/{prompt_key}")
def get_prompt(prompt_key: str, admin: UserInfo = Depends(require_admin)):
    """获取指定Prompt的当前生效版本"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log "
                "FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Prompt '{prompt_key}' 不存在或未激活")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            row["is_active"] = bool(row.get("is_active"))
            return row
    finally:
        conn.close()


@router.post("/prompts")
def create_prompt(req: PromptCreate, admin: UserInfo = Depends(require_admin)):
    """创建新Prompt（初始版本）"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 检查是否已存在该prompt_key的活跃版本
            cur.execute(
                "SELECT id FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (req.prompt_key,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Prompt '{req.prompt_key}' 已存在，请使用更新接口",
                )

            now = _now()
            prompt_id = _generate_id()

            # 创建Prompt记录
            cur.execute(
                "INSERT INTO adh_prompts "
                "(id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1, 1, %s, %s, %s, %s)",
                (prompt_id, req.prompt_key, req.prompt_name, req.system_prompt or "",
                 req.user_prompt_template or "", req.description or "", now, now,
                 admin.username, req.change_log or "初始版本"),
            )

            # 创建版本历史
            version_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompt_versions "
                "(id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current) "
                "VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, 1)",
                (version_id, prompt_id, req.prompt_key, req.system_prompt or "",
                 req.user_prompt_template or "", req.change_log or "初始版本", now, admin.username),
            )

        conn.commit()
        return {"success": True, "id": prompt_id, "version": 1}
    finally:
        conn.close()


@router.put("/prompts/{prompt_key}")
def update_prompt(prompt_key: str, req: PromptUpdate, admin: UserInfo = Depends(require_admin)):
    """更新Prompt（创建新版本）"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 获取当前活跃版本
            cur.execute(
                "SELECT id, prompt_name, system_prompt, user_prompt_template, version "
                "FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            current = cur.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail=f"Prompt '{prompt_key}' 不存在")

            now = _now()
            new_version = current["version"] + 1
            prompt_id = current["id"]

            # 合并更新
            prompt_name = req.prompt_name if req.prompt_name is not None else current["prompt_name"]
            system_prompt = req.system_prompt if req.system_prompt is not None else current["system_prompt"]
            user_prompt_template = req.user_prompt_template if req.user_prompt_template is not None else current["user_prompt_template"]
            description = req.description if req.description is not None else ""
            change_log = req.change_log or f"版本 {new_version}"

            # 更新Prompt记录（创建新版本）
            new_prompt_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompts "
                "(id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)",
                (new_prompt_id, prompt_key, prompt_name, system_prompt, user_prompt_template,
                 description, new_version, now, now, admin.username, change_log),
            )

            # 将旧版本设为非活跃
            cur.execute(
                "UPDATE adh_prompts SET is_active = 0 WHERE prompt_key = %s AND id != %s",
                (prompt_key, new_prompt_id),
            )

            # 更新版本历史：将旧版本的is_current设为0
            cur.execute(
                "UPDATE adh_prompt_versions SET is_current = 0 WHERE prompt_key = %s",
                (prompt_key,),
            )

            # 创建新版本历史
            version_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompt_versions "
                "(id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                (version_id, new_prompt_id, prompt_key, new_version, system_prompt,
                 user_prompt_template, change_log, now, admin.username),
            )

        conn.commit()
        return {"success": True, "id": new_prompt_id, "version": new_version}
    finally:
        conn.close()


@router.get("/prompts/{prompt_key}/versions")
def get_prompt_versions(prompt_key: str, admin: UserInfo = Depends(require_admin)):
    """获取Prompt的版本历史"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current "
                "FROM adh_prompt_versions WHERE prompt_key = %s ORDER BY version DESC",
                (prompt_key,),
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("created_at"), "isoformat"):
                    r["created_at"] = r["created_at"].isoformat()
                r["is_current"] = bool(r.get("is_current"))
            return {"items": rows, "total": len(rows)}
    finally:
        conn.close()


@router.post("/prompts/{prompt_key}/rollback")
def rollback_prompt(prompt_key: str, version: int, admin: UserInfo = Depends(require_admin)):
    """回退到指定版本"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 获取指定版本的Prompt
            cur.execute(
                "SELECT id, system_prompt, user_prompt_template, change_log "
                "FROM adh_prompt_versions WHERE prompt_key = %s AND version = %s",
                (prompt_key, version),
            )
            target_version = cur.fetchone()
            if not target_version:
                raise HTTPException(status_code=404, detail=f"版本 {version} 不存在")

            now = _now()

            # 获取当前最大版本号
            cur.execute(
                "SELECT MAX(version) as max_version FROM adh_prompts WHERE prompt_key = %s",
                (prompt_key,),
            )
            max_version = cur.fetchone()["max_version"] or 0
            new_version = max_version + 1

            # 获取当前prompt_name和description
            cur.execute(
                "SELECT prompt_name, description FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            current = cur.fetchone() or {"prompt_name": prompt_key, "description": ""}

            # 创建新版本（基于回退内容）
            new_prompt_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompts "
                "(id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)",
                (new_prompt_id, prompt_key, current["prompt_name"],
                 target_version["system_prompt"], target_version["user_prompt_template"],
                 current["description"], new_version, now, now, admin.username,
                 f"回退到版本 {version}"),
            )

            # 将旧版本设为非活跃
            cur.execute(
                "UPDATE adh_prompts SET is_active = 0 WHERE prompt_key = %s AND id != %s",
                (prompt_key, new_prompt_id),
            )

            # 更新版本历史
            cur.execute(
                "UPDATE adh_prompt_versions SET is_current = 0 WHERE prompt_key = %s",
                (prompt_key,),
            )

            # 创建回退版本记录
            version_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompt_versions "
                "(id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                (version_id, new_prompt_id, prompt_key, new_version,
                 target_version["system_prompt"], target_version["user_prompt_template"],
                 f"回退到版本 {version}", now, admin.username),
            )

        conn.commit()
        return {"success": True, "id": new_prompt_id, "version": new_version, "rollback_from": version}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# Workflow Configuration Management
# ════════════════════════════════════════════════════════════════════

@router.get("/workflows", response_model=WorkflowListResponse)
def list_workflows(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query("", description="搜索工作流名称"),
    admin: UserInfo = Depends(require_admin),
):
    """获取工作流列表"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if search:
                conditions.append("name LIKE %s")
                params.append(f"%{search}%")
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_workflow_configs {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, name, description, is_active, is_default, workflow_type, dag_config, "
                f"created_at, updated_at, created_by "
                f"FROM adh_workflow_configs {where} "
                f"ORDER BY is_default DESC, name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            workflows = cur.fetchall()

            result = []
            for wf in workflows:
                for k in ("created_at", "updated_at"):
                    if hasattr(wf.get(k), "isoformat"):
                        wf[k] = wf[k].isoformat()
                wf["is_active"] = bool(wf.get("is_active"))
                wf["is_default"] = bool(wf.get("is_default"))

                # 获取工作流步骤
                cur.execute(
                    "SELECT id, workflow_id, step_type, step_name, step_order, "
                    "max_rounds, is_enabled, prompt_key, config, created_at, updated_at "
                    "FROM adh_workflow_steps WHERE workflow_id = %s ORDER BY step_order",
                    (wf["id"],),
                )
                steps = cur.fetchall()
                for step in steps:
                    for k in ("created_at", "updated_at"):
                        if hasattr(step.get(k), "isoformat"):
                            step[k] = step[k].isoformat()
                    step["is_enabled"] = bool(step.get("is_enabled"))
                    if step.get("config"):
                        try:
                            step["config"] = json.loads(step["config"])
                        except:
                            pass
                wf["steps"] = steps

                # 获取工作流边
                try:
                    cur.execute(
                        "SELECT id, workflow_id, source_step_id, target_step_id, edge_type, "
                        "condition_expr, label, created_at "
                        "FROM adh_workflow_edges WHERE workflow_id = %s",
                        (wf["id"],),
                    )
                    edges = cur.fetchall()
                    for e in edges:
                        if hasattr(e.get("created_at"), "isoformat"):
                            e["created_at"] = e["created_at"].isoformat()
                    wf["edges"] = edges
                except Exception:
                    wf["edges"] = []

                result.append(wf)

            return {"total": total, "items": result}
    finally:
        conn.close()


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: int, admin: UserInfo = Depends(require_admin)):
    """获取工作流详情"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, is_active, is_default, workflow_type, dag_config, "
                "created_at, updated_at, created_by "
                "FROM adh_workflow_configs WHERE id = %s",
                (workflow_id,),
            )
            wf = cur.fetchone()
            if not wf:
                raise HTTPException(status_code=404, detail="工作流不存在")

            for k in ("created_at", "updated_at"):
                if hasattr(wf.get(k), "isoformat"):
                    wf[k] = wf[k].isoformat()
            wf["is_active"] = bool(wf.get("is_active"))
            wf["is_default"] = bool(wf.get("is_default"))

            # 获取工作流步骤
            cur.execute(
                "SELECT id, workflow_id, step_type, step_name, step_order, "
                "max_rounds, is_enabled, prompt_key, config, created_at, updated_at "
                "FROM adh_workflow_steps WHERE workflow_id = %s ORDER BY step_order",
                (workflow_id,),
            )
            steps = cur.fetchall()
            for step in steps:
                for k in ("created_at", "updated_at"):
                    if hasattr(step.get(k), "isoformat"):
                        step[k] = step[k].isoformat()
                step["is_enabled"] = bool(step.get("is_enabled"))
                if step.get("config"):
                    try:
                        step["config"] = json.loads(step["config"])
                    except:
                        pass
            wf["steps"] = steps

            # 获取工作流边
            try:
                cur.execute(
                    "SELECT id, workflow_id, source_step_id, target_step_id, edge_type, "
                    "condition_expr, label, created_at "
                    "FROM adh_workflow_edges WHERE workflow_id = %s",
                    (workflow_id,),
                )
                edges = cur.fetchall()
                for e in edges:
                    if hasattr(e.get("created_at"), "isoformat"):
                        e["created_at"] = e["created_at"].isoformat()
                wf["edges"] = edges
            except Exception:
                wf["edges"] = []

            return wf
    finally:
        conn.close()


@router.post("/workflows")
def create_workflow(req: WorkflowConfigCreate, admin: UserInfo = Depends(require_admin)):
    """创建工作流"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            now = _now()
            workflow_id = _generate_id()

            # 如果设置为默认，先取消其他默认
            if req.is_default:
                cur.execute(
                    "UPDATE adh_workflow_configs SET is_default = 0 WHERE is_default = 1"
                )

            # 创建工作流配置
            cur.execute(
                "INSERT INTO adh_workflow_configs "
                "(id, name, description, is_active, is_default, workflow_type, dag_config, "
                "created_at, updated_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (workflow_id, req.name, req.description or "",
                 1 if req.is_active else 0, 1 if req.is_default else 0,
                 req.workflow_type, req.dag_config, now, now, admin.username),
            )

            # 创建工作流步骤
            if req.steps:
                for step in req.steps:
                    step_id = _generate_id()
                    config_json = json.dumps(step.config, ensure_ascii=False) if step.config else None
                    cur.execute(
                        "INSERT INTO adh_workflow_steps "
                        "(id, workflow_id, step_type, step_name, step_order, "
                        "max_rounds, is_enabled, prompt_key, config, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (step_id, workflow_id, step.step_type, step.step_name, step.step_order,
                         step.max_rounds, 1 if step.is_enabled else 0, step.prompt_key,
                         config_json, now, now),
                    )

            # 创建工作流边
            if req.edges:
                for edge in req.edges:
                    edge_id = _generate_id()
                    cur.execute(
                        "INSERT INTO adh_workflow_edges "
                        "(id, workflow_id, source_step_id, target_step_id, edge_type, "
                        "condition_expr, label, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (edge_id, workflow_id, edge.source_step_id, edge.target_step_id,
                         edge.edge_type, edge.condition_expr, edge.label, now),
                    )

        conn.commit()
        return {"success": True, "id": workflow_id}
    finally:
        conn.close()


@router.put("/workflows/{workflow_id}")
def update_workflow(workflow_id: int, req: WorkflowConfigUpdate, admin: UserInfo = Depends(require_admin)):
    """更新工作流配置"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 获取当前配置
            cur.execute(
                "SELECT id, name, description, is_active, is_default, workflow_type, dag_config "
                "FROM adh_workflow_configs WHERE id = %s",
                (workflow_id,),
            )
            current = cur.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="工作流不存在")

            now = _now()

            # 合并更新
            name = req.name if req.name is not None else current["name"]
            description = req.description if req.description is not None else current["description"]
            is_active = req.is_active if req.is_active is not None else bool(current["is_active"])
            is_default = req.is_default if req.is_default is not None else bool(current["is_default"])
            workflow_type = req.workflow_type if req.workflow_type is not None else current.get("workflow_type", "linear")
            dag_config = req.dag_config if req.dag_config is not None else current.get("dag_config")

            # 如果设置为默认，先取消其他默认
            if is_default and not current["is_default"]:
                cur.execute(
                    "UPDATE adh_workflow_configs SET is_default = 0 WHERE is_default = 1"
                )

            # 更新（Doris不支持UPDATE，使用DELETE+INSERT）
            cur.execute("DELETE FROM adh_workflow_configs WHERE id = %s", (workflow_id,))
            cur.execute(
                "INSERT INTO adh_workflow_configs "
                "(id, name, description, is_active, is_default, workflow_type, dag_config, "
                "created_at, updated_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (workflow_id, name, description, 1 if is_active else 0,
                 1 if is_default else 0, workflow_type, dag_config, now, now, admin.username),
            )

            # 更新步骤（如果提供了新的步骤列表）
            if req.steps is not None:
                cur.execute("DELETE FROM adh_workflow_steps WHERE workflow_id = %s", (workflow_id,))
                for step in req.steps:
                    step_id = _generate_id()
                    config_json = json.dumps(step.config, ensure_ascii=False) if step.config else None
                    cur.execute(
                        "INSERT INTO adh_workflow_steps "
                        "(id, workflow_id, step_type, step_name, step_order, "
                        "max_rounds, is_enabled, prompt_key, config, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (step_id, workflow_id, step.step_type, step.step_name, step.step_order,
                         step.max_rounds, 1 if step.is_enabled else 0, step.prompt_key,
                         config_json, now, now),
                    )

            # 更新边（如果提供了新的边列表）
            if req.edges is not None:
                cur.execute("DELETE FROM adh_workflow_edges WHERE workflow_id = %s", (workflow_id,))
                for edge in req.edges:
                    edge_id = _generate_id()
                    cur.execute(
                        "INSERT INTO adh_workflow_edges "
                        "(id, workflow_id, source_step_id, target_step_id, edge_type, "
                        "condition_expr, label, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (edge_id, workflow_id, edge.source_step_id, edge.target_step_id,
                         edge.edge_type, edge.condition_expr, edge.label, now),
                    )

        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: int, admin: UserInfo = Depends(require_admin)):
    """删除工作流"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 检查是否为默认工作流
            cur.execute(
                "SELECT is_default FROM adh_workflow_configs WHERE id = %s",
                (workflow_id,),
            )
            wf = cur.fetchone()
            if not wf:
                raise HTTPException(status_code=404, detail="工作流不存在")
            if wf["is_default"]:
                raise HTTPException(status_code=400, detail="不能删除默认工作流")

            # 删除步骤
            cur.execute("DELETE FROM adh_workflow_steps WHERE workflow_id = %s", (workflow_id,))
            # 删除工作流
            cur.execute("DELETE FROM adh_workflow_configs WHERE id = %s", (workflow_id,))

        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ── DAG Workflow Endpoints ──────────────────────────────────────────


@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow_dag(workflow_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Execute a DAG workflow."""
    from backend.nl2sql.orchestrator.workflow.dag_engine import DAGExecutor

    question = req.get("question", "")
    datasource_id = req.get("datasource_id", 0)

    if not question:
        return {"success": False, "error": "请输入测试问题"}

    executor = DAGExecutor(
        workflow_id=workflow_id,
        context={"question": question, "datasource_id": datasource_id},
    )

    result = await executor.execute()
    return result


@router.post("/workflows/{workflow_id}/validate")
async def validate_workflow_dag(workflow_id: int, admin: UserInfo = Depends(require_admin)):
    """Validate a DAG workflow (check for cycles, orphans, etc.)."""
    from backend.nl2sql.orchestrator.workflow.dag_engine import DAGExecutor

    executor = DAGExecutor(workflow_id=workflow_id, context={})
    try:
        await executor._load_workflow_config()
        errors = executor.validate_dag()
        return {"valid": len(errors) == 0, "errors": errors}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}


@router.get("/workflows/{workflow_id}/dag")
async def get_workflow_dag(workflow_id: int, admin: UserInfo = Depends(require_admin)):
    """Get DAG configuration for a workflow."""
    from backend.nl2sql.orchestrator.workflow.dag_engine import load_dag_config

    try:
        dag_config = load_dag_config(workflow_id)
        return {"success": True, **dag_config}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/workflows/{workflow_id}/edges")
async def create_workflow_edge(workflow_id: int, req: WorkflowEdgeCreate, admin: UserInfo = Depends(require_admin)):
    """Create a new edge in the workflow DAG."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            now = _now()
            edge_id = _generate_id()

            cur.execute(
                "INSERT INTO adh_workflow_edges "
                "(id, workflow_id, source_step_id, target_step_id, edge_type, condition_expr, label, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (edge_id, workflow_id, req.source_step_id, req.target_step_id,
                 req.edge_type, req.condition_expr, req.label, now),
            )

        conn.commit()
        return {"success": True, "id": edge_id}
    finally:
        conn.close()


@router.delete("/workflows/{workflow_id}/edges/{edge_id}")
async def delete_workflow_edge(workflow_id: int, edge_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete an edge from the workflow DAG."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_workflow_edges WHERE id = %s AND workflow_id = %s",
                (edge_id, workflow_id),
            )

        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.get("/workflows/{workflow_id}/edges")
async def list_workflow_edges(workflow_id: int, admin: UserInfo = Depends(require_admin)):
    """List all edges in a workflow DAG."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, workflow_id, source_step_id, target_step_id, edge_type, condition_expr, label, created_at "
                "FROM adh_workflow_edges WHERE workflow_id = %s",
                (workflow_id,),
            )
            edges = cur.fetchall()
            for e in edges:
                if hasattr(e.get("created_at"), "isoformat"):
                    e["created_at"] = e["created_at"].isoformat()
            return {"items": edges, "total": len(edges)}
    finally:
        conn.close()


@router.put("/workflows/{workflow_id}/steps/{step_id}")
def update_workflow_step(
    workflow_id: int,
    step_id: int,
    req: WorkflowStepUpdate,
    admin: UserInfo = Depends(require_admin),
):
    """更新工作流步骤"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # 获取当前步骤
            cur.execute(
                "SELECT id, workflow_id, step_type, step_name, step_order, "
                "max_rounds, is_enabled, prompt_key, config "
                "FROM adh_workflow_steps WHERE id = %s AND workflow_id = %s",
                (step_id, workflow_id),
            )
            current = cur.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="步骤不存在")

            now = _now()

            # 合并更新
            step_name = req.step_name if req.step_name is not None else current["step_name"]
            max_rounds = req.max_rounds if req.max_rounds is not None else current["max_rounds"]
            is_enabled = req.is_enabled if req.is_enabled is not None else bool(current["is_enabled"])
            prompt_key = req.prompt_key if req.prompt_key is not None else current["prompt_key"]
            config = req.config if req.config is not None else current["config"]

            config_json = json.dumps(config, ensure_ascii=False) if config else None

            # 更新（Doris不支持UPDATE，使用DELETE+INSERT）
            cur.execute("DELETE FROM adh_workflow_steps WHERE id = %s", (step_id,))
            cur.execute(
                "INSERT INTO adh_workflow_steps "
                "(id, workflow_id, step_type, step_name, step_order, "
                "max_rounds, is_enabled, prompt_key, config, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (step_id, workflow_id, current["step_type"], step_name, current["step_order"],
                 max_rounds, 1 if is_enabled else 0, prompt_key, config_json, now, now),
            )

        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# Workflow Execution Logs
# ════════════════════════════════════════════════════════════════════

@router.get("/workflow-logs", response_model=WorkflowLogListResponse)
def list_workflow_logs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    workflow_id: Optional[int] = Query(None, description="按工作流筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    admin: UserInfo = Depends(require_admin),
):
    """获取工作流执行日志"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if workflow_id:
                conditions.append("workflow_id = %s")
                params.append(workflow_id)
            if status:
                conditions.append("status = %s")
                params.append(status)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_workflow_logs {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, workflow_id, workflow_name, session_id, user_id, username, "
                f"question, current_step, current_round, "
                f"generated_sql, chart_type, status, error_message, "
                f"started_at, completed_at, elapsed_ms "
                f"FROM adh_workflow_logs {where} "
                f"ORDER BY started_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("started_at", "completed_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/workflow-logs/{log_id}")
def get_workflow_log(log_id: int, admin: UserInfo = Depends(require_admin)):
    """获取工作流执行日志详情"""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adh_workflow_logs WHERE id = %s",
                (log_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="日志不存在")

            for k in ("started_at", "completed_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()

            # 解析JSON字段
            for field in ("metadata_context", "metadata_requested", "metadata_supplemented",
                          "execution_result"):
                if row.get(field):
                    try:
                        row[field] = json.loads(row[field])
                    except:
                        pass

            return row
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
# Workflow Testing
# ════════════════════════════════════════════════════════════════════

@router.post("/workflows/test")
async def test_workflow(req: dict, admin: UserInfo = Depends(require_admin)):
    """Test a workflow with a sample question.

    Returns detailed execution results for each step.
    """
    from backend.nl2sql.orchestrator.workflow.loop_engine import load_workflow_config, get_step_max_rounds
    from backend.rag.rag_retriever import retrieve_all
    from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
    from backend.common.llm.llm_client import generate_sql
    from backend.nl2sql.sql.sql_validator import validate_and_fix
    from backend.nl2sql.sql.query_executor import execute_query
    import time

    workflow_id = req.get("workflow_id")
    question = req.get("question", "")
    datasource_id = req.get("datasource_id", 0)

    if not question:
        return {"success": False, "error": "请输入测试问题"}

    # Load workflow config
    workflow = load_workflow_config(workflow_id)
    steps = workflow.get("steps", [])

    results = []

    try:
        # Step 1: Metadata retrieval
        step_config = next((s for s in steps if s["step_type"] == "metadata_retrieval"), None)
        if step_config and step_config.get("is_enabled", True):
            t_start = time.time()
            results.append({
                "step_type": "metadata_retrieval",
                "step_name": step_config.get("step_name", "元数据检索"),
                "status": "running",
                "input": {"question": question},
            })

            from backend.rag.table_selector import select_tables
            selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)
            rag_context = retrieve_all(question=question, selected_tables=selected_tables, datasource_id=datasource_id)

            current_metadata = {
                "table_info": rag_context.get("table_info", []),
                "column_metadata": rag_context.get("column_metadata", []),
                "business_terms": rag_context.get("business_terms", []),
                "table_relations": rag_context.get("table_relations", []),
                "sql_templates": rag_context.get("sql_templates", []),
            }

            results[-1].update({
                "status": "success",
                "output": {
                    "table_info_count": len(current_metadata["table_info"]),
                    "column_metadata_count": len(current_metadata["column_metadata"]),
                    "business_terms_count": len(current_metadata["business_terms"]),
                    "table_relations_count": len(current_metadata["table_relations"]),
                },
                "duration_ms": int((time.time() - t_start) * 1000),
            })

        # Step 2: LLM Analysis (with loop)
        step_config = next((s for s in steps if s["step_type"] == "llm_analysis"), None)
        if step_config and step_config.get("is_enabled", True):
            max_rounds = step_config.get("max_rounds", 1)
            prompt_key = step_config.get("prompt_key", "metadata_supplement")

            for round_num in range(max_rounds):
                t_start = time.time()
                step_name = f"{step_config.get('step_name', 'LLM意图分析')} (轮次 {round_num + 1}/{max_rounds})"
                results.append({
                    "step_type": "llm_analysis",
                    "step_name": step_name,
                    "status": "running",
                    "input": {"question": question, "metadata_summary": {
                        "tables": len(current_metadata["table_info"]),
                        "columns": len(current_metadata["column_metadata"]),
                    }},
                })

                # Load prompt from database
                from backend.nl2sql.orchestrator.workflow.loop_engine import load_prompt, analyze_metadata_need
                analysis = analyze_metadata_need(question, current_metadata, prompt_key)

                results[-1].update({
                    "status": "success" if not analysis.get("error") else "error",
                    "output": analysis,
                    "error": analysis.get("error") if isinstance(analysis.get("error"), str) else None,
                    "duration_ms": int((time.time() - t_start) * 1000),
                })

                # Check if more metadata needed
                if not analysis.get("need_more", False):
                    break

                # Step 3: Metadata supplement (if needed)
                step_config_supplement = next((s for s in steps if s["step_type"] == "metadata_supplement"), None)
                if step_config_supplement and step_config_supplement.get("is_enabled", True):
                    t_start = time.time()
                    results.append({
                        "step_type": "metadata_supplement",
                        "step_name": f"{step_config_supplement.get('step_name', '元数据补充')} (轮次 {round_num + 1})",
                        "status": "running",
                        "input": analysis.get("required_tables", []),
                    })

                    # Retrieve supplementary metadata
                    required_tables = analysis.get("required_tables", [])
                    if required_tables:
                        supplement_context = retrieve_all(
                            question=question,
                            selected_tables=required_tables,
                            datasource_id=datasource_id,
                        )
                        # Merge metadata
                        for key in ["table_info", "column_metadata", "business_terms", "table_relations"]:
                            existing_ids = {item.get("id") for item in current_metadata[key]}
                            for item in supplement_context.get(key, []):
                                if item.get("id") not in existing_ids:
                                    current_metadata[key].append(item)

                    results[-1].update({
                        "status": "success",
                        "output": {
                            "table_info_count": len(current_metadata["table_info"]),
                            "column_metadata_count": len(current_metadata["column_metadata"]),
                        },
                        "duration_ms": int((time.time() - t_start) * 1000),
                    })

        # Step 4: SQL Generation
        step_config = next((s for s in steps if s["step_type"] == "sql_generation"), None)
        if step_config and step_config.get("is_enabled", True):
            t_start = time.time()
            results.append({
                "step_type": "sql_generation",
                "step_name": step_config.get("step_name", "SQL生成"),
                "status": "running",
                "input": {"question": question, "metadata_used": {
                    "tables": len(current_metadata["table_info"]),
                    "columns": len(current_metadata["column_metadata"]),
                }},
            })

            messages = build_nl2sql_prompt(
                question=question,
                table_info=current_metadata["table_info"],
                column_metadata=current_metadata["column_metadata"],
                sql_templates=current_metadata.get("sql_templates", []),
                business_terms=current_metadata["business_terms"],
                table_relations=current_metadata.get("table_relations", []),
                engine="Doris",
            )

            llm_result = generate_sql(messages=messages, max_tokens=2000)
            llm_response = llm_result.get("sql", "")

            # Parse response
            import json
            content = llm_response.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            try:
                sql_result = json.loads(content)
            except:
                sql_result = {"success": False, "message": "JSON解析失败"}

            results[-1].update({
                "status": "success" if sql_result.get("success", True) else "error",
                "output": sql_result,
                "error": sql_result.get("message") if not sql_result.get("success", True) else None,
                "duration_ms": int((time.time() - t_start) * 1000),
            })

            generated_sql = sql_result.get("sql", "")

        # Step 5: SQL Execution
        exec_result = None
        step_config = next((s for s in steps if s["step_type"] == "sql_execution"), None)
        if step_config and step_config.get("is_enabled", True) and generated_sql:
            t_start = time.time()
            results.append({
                "step_type": "sql_execution",
                "step_name": step_config.get("step_name", "SQL执行"),
                "status": "running",
                "input": {"sql": generated_sql},
            })

            # Validate and fix SQL
            generated_sql, warnings = validate_and_fix(generated_sql)
            if warnings:
                logger.info("SQL warnings: %s", warnings)

            # Execute query — returns (DataFrame, elapsed_ms, row_count)
            try:
                df, query_elapsed_ms, row_count = execute_query(generated_sql, datasource_id=datasource_id)
                columns = list(df.columns) if not df.empty else []
                rows = df.to_dict(orient="records") if not df.empty else []
                # Sanitize non-serializable types
                from decimal import Decimal
                for row in rows:
                    for k, v in row.items():
                        if hasattr(v, 'isoformat'):
                            row[k] = v.isoformat()
                        elif isinstance(v, Decimal):
                            row[k] = float(v)
                        elif isinstance(v, bytes):
                            row[k] = v.decode('utf-8', errors='replace')
                exec_result = {
                    "success": True,
                    "columns": columns,
                    "row_count": row_count,
                    "rows": rows,
                }
            except (ValueError, RuntimeError) as e:
                exec_result = {"success": False, "error": str(e)}

            results[-1].update({
                "status": "success" if exec_result and exec_result.get("success") else "error",
                "output": {
                    "columns": exec_result.get("columns", []) if exec_result else [],
                    "row_count": exec_result.get("row_count", 0) if exec_result else 0,
                    "sample_rows": (exec_result.get("rows", [])[:5] if exec_result else []),
                } if exec_result and exec_result.get("success") else None,
                "error": exec_result.get("error") if exec_result and not exec_result.get("success") else None,
                "duration_ms": int((time.time() - t_start) * 1000),
            })

        # Step 6: Result Analysis (optional)
        step_config = next((s for s in steps if s["step_type"] == "result_analysis"), None)
        if step_config and step_config.get("is_enabled", True) and exec_result and exec_result.get("success"):
            t_start = time.time()
            results.append({
                "step_type": "result_analysis",
                "step_name": step_config.get("step_name", "结果分析"),
                "status": "running",
                "input": {"question": question, "row_count": exec_result.get("row_count", 0)},
            })

            from backend.nl2sql.orchestrator.workflow.loop_engine import analyze_result
            analysis_result = analyze_result(question, exec_result, step_config.get("prompt_key", "result_analysis"))

            results[-1].update({
                "status": "success" if not analysis_result.get("error") else "error",
                "output": analysis_result,
                "error": analysis_result.get("error") if isinstance(analysis_result.get("error"), str) else None,
                "duration_ms": int((time.time() - t_start) * 1000),
            })

        return {"success": True, "steps": results}

    except Exception as e:
        logger.error("Workflow test failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e), "steps": results}


@router.post("/workflows/test-step")
async def test_workflow_step(req: dict, admin: UserInfo = Depends(get_current_user)):
    """Execute a single workflow step for testing.

    Request body:
        workflow_id: int
        step_type: str  # e.g. "metadata_retrieval", "llm_analysis", etc.
        question: str
        datasource_id: int
        metadata_context: dict (optional)  # Previous step output to use as input
    """
    from backend.nl2sql.orchestrator.workflow.loop_engine import load_workflow_config, analyze_metadata_need, analyze_result
    from backend.rag.rag_retriever import retrieve_all, retrieve_tables_metadata
    from backend.rag.table_selector import select_tables
    from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
    from backend.common.llm.llm_client import generate_sql
    from backend.nl2sql.sql.sql_validator import validate_and_fix
    from backend.nl2sql.sql.query_executor import execute_query
    import time

    workflow_id = req.get("workflow_id")
    step_type = req.get("step_type", "")
    question = req.get("question", "")
    datasource_id = req.get("datasource_id", 0)
    metadata_context = req.get("metadata_context", {})

    if not step_type or not question:
        return {"success": False, "error": "step_type and question are required"}

    conn = None
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
    except Exception:
        pass

    t0 = time.time()

    try:
        if step_type == "metadata_retrieval":
            selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)
            rag_results = retrieve_all(question, selected_tables=selected_tables, datasource_id=datasource_id)
            elapsed = round(time.time() - t0, 2)
            return {
                "success": True,
                "step_type": step_type,
                "elapsed": elapsed,
                "output": {
                    "selected_tables": selected_tables,
                    "table_info_count": len(rag_results.get("table_info", [])),
                    "column_metadata_count": len(rag_results.get("column_metadata", [])),
                    "rag_source": rag_results.get("rag_source", ""),
                },
                "metadata_context": {
                    "table_info": rag_results.get("table_info", []),
                    "column_metadata": rag_results.get("column_metadata", []),
                    "business_terms": rag_results.get("business_terms", []),
                    "table_relations": rag_results.get("table_relations", []),
                    "sql_templates": rag_results.get("sql_templates", []),
                },
            }

        elif step_type == "llm_analysis":
            analysis = analyze_metadata_need(
                question=question,
                current_metadata=metadata_context,
                conn=conn,
            )
            analysis.pop("_llm_result", None)
            elapsed = round(time.time() - t0, 2)
            return {"success": True, "step_type": step_type, "elapsed": elapsed, "output": analysis}

        elif step_type == "sql_generation":
            messages = build_nl2sql_prompt(
                question=question,
                table_info=metadata_context.get("table_info", []),
                column_metadata=metadata_context.get("column_metadata", []),
                sql_templates=metadata_context.get("sql_templates", []),
                business_terms=metadata_context.get("business_terms", []),
                table_relations=metadata_context.get("table_relations", []),
                engine="Doris",
            )
            llm_result = generate_sql(messages=messages)
            elapsed = round(time.time() - t0, 2)
            return {"success": True, "step_type": step_type, "elapsed": elapsed, "output": {"sql": llm_result.get("sql", ""), "tokens": llm_result.get("tokens", {})}}

        elif step_type == "result_analysis":
            query_result = metadata_context.get("query_result", {})
            col_meta = metadata_context.get("column_metadata", [])
            analysis = analyze_result(
                question=question,
                query_result=query_result,
                column_metadata=col_meta,
                conn=conn,
            )
            analysis.pop("_llm_result", None)
            elapsed = round(time.time() - t0, 2)
            return {"success": True, "step_type": step_type, "elapsed": elapsed, "output": analysis}

        else:
            return {"success": False, "error": f"Unknown step_type: {step_type}"}

    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        return {"success": False, "step_type": step_type, "elapsed": elapsed, "error": str(e)}
    finally:
        if conn:
            conn.close()
