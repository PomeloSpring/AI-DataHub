"""DAG 工作流 API — 编排定义 / 运行实例 / 手动触发与运维。

权限口径：
- 身份只信服务端（Bearer → resolve_current_user），fail-closed；
- workspace 归属校验（authorize_workspace）；非 admin 仅可操作自己 owner 的资源；
- 写操作另受权限码中间件 `dag:manage` 门控（adh_perm_registry 声明）。

响应口径（护栏 §7）：
- workflow 详情含 graph_json（用户自己的设计内容，编辑器必需）；
- 运行实例/节点实例响应隐藏 node_config 中的 SQL 与连接信息，
  只回状态/行数/耗时/脱敏错误。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from backend.common.auth import authorize_workspace, resolve_current_user

from backend.modules.flow.dag.dag_service import dag_service
from backend.modules.flow.dag.dag_executor import dispatch_run
from backend.modules.flow.dag.dag_validator import validate_graph, DagValidationError

logger = logging.getLogger(__name__)


async def _dag_access(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        user = resolve_current_user(header[7:])
        request.state.current_user = user
    params = request.path_params
    resource = None
    if params.get("workflow_id"):
        resource = dag_service.get_workflow(int(params["workflow_id"]))
    elif params.get("run_id"):
        run = dag_service.get_run(int(params["run_id"]))
        resource = dag_service.get_workflow(run["workflow_id"]) if run else None
    if params and (params.get("workflow_id") or params.get("run_id")) and resource is None:
        raise HTTPException(status_code=404, detail="资源不存在或无权访问")
    if resource:
        authorize_workspace(user, resource.get("workspace_id") or 0)
        if user.get("role") != "admin" and int(resource.get("owner_id") or 0) != int(user["user_id"]):
            raise HTTPException(status_code=404, detail="资源不存在或无权访问")
    else:
        ws = request.query_params.get("workspace_id") or request.headers.get("X-Workspace-Id")
        if ws is None and request.method in ("POST", "PUT"):
            try:
                body = await request.json()
                ws = body.get("workspace_id", 0) if isinstance(body, dict) else 0
            except ValueError:
                ws = 0
        authorize_workspace(user, ws or 0)


router = APIRouter(dependencies=[Depends(_dag_access)])


# ════════════════════════════════════════════════════════════════════
# Models
# ════════════════════════════════════════════════════════════════════


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    kind: str = "dag"               # simple=简单同步（单节点） / dag=多节点编排
    graph_json: dict
    cron_expression: Optional[str] = None
    timezone: Optional[str] = "Asia/Shanghai"
    is_active: bool = True
    workspace_id: int = 0
    timeout_seconds: int = 3600
    max_retries: int = 0


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    graph_json: Optional[dict] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None
    version: Optional[int] = None


def _safe_node_config(config: dict) -> dict:
    """节点配置快照脱敏：隐藏 SQL 与任何连接信息（护栏 §7）。"""
    if not isinstance(config, dict):
        return {}
    safe = {}
    for key, value in config.items():
        if key in ("sql", "password", "host", "port", "username"):
            continue
        if key == "target" and isinstance(value, dict):
            safe[key] = {k: v for k, v in value.items() if k not in ("password", "host", "port", "username")}
        else:
            safe[key] = value
    return safe


def _safe_node_run(row: dict) -> dict:
    row = dict(row)
    row["node_config"] = _safe_node_config(row.get("node_config") or {})
    return row


# ════════════════════════════════════════════════════════════════════
# Workflow CRUD
# ════════════════════════════════════════════════════════════════════


@router.get("/workflows")
def list_workflows(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    workspace_id: int = Query(0),
):
    return dag_service.list_workflows(workspace_id=workspace_id, page=page, size=size)


@router.post("/workflows")
def create_workflow(req: WorkflowCreate, request: Request):
    user = request.state.current_user
    try:
        validate_graph(req.graph_json)
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    wf_id = dag_service.create_workflow(
        req.model_dump(), owner_id=int(user["user_id"]), workspace_id=req.workspace_id)
    if not wf_id:
        raise HTTPException(status_code=500, detail="工作流创建失败")
    return {"id": wf_id}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: int):
    workflow = dag_service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return workflow


@router.get("/workflows/{workflow_id}/dataflow")
def get_workflow_dataflow(workflow_id: int, verify: bool = Query(False)):
    """数据流拓扑（定义期静态解析）：源表 → 转换 → 目标表。

    verify=true 时与实际血缘对照（预期 vs 实际），每条流标注 verified。
    """
    from backend.modules.flow.dag.sql_translator import extract_dataflow, verify_dataflow_against_lineage
    workflow = dag_service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="工作流不存在")
    try:
        flow = extract_dataflow(workflow.get("graph_json") or {})
        if verify:
            flow = verify_dataflow_against_lineage(
                flow, int(workflow.get("workspace_id") or 0))
    except Exception as exc:
        logger.exception("workflow %s 数据流解析失败", workflow_id)
        raise HTTPException(status_code=400, detail=f"数据流解析失败: {exc}")
    return flow


@router.put("/workflows/{workflow_id}")
def update_workflow(workflow_id: int, req: WorkflowUpdate):
    existing = dag_service.get_workflow(workflow_id)
    if not existing:
        raise HTTPException(status_code=404, detail="工作流不存在")
    data = req.model_dump(exclude_unset=True)
    expected_version = data.pop("version", None)
    if "graph_json" in data:
        try:
            validate_graph(data["graph_json"])
        except DagValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not dag_service.update_workflow(workflow_id, data, expected_version):
        raise HTTPException(status_code=409, detail="保存冲突：工作流已被他人修改，请刷新后重试")
    return {"success": True, "version": (dag_service.get_workflow(workflow_id) or {}).get("version")}


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: int):
    try:
        return {"success": dag_service.delete_workflow(workflow_id)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ════════════════════════════════════════════════════════════════════
# Runs
# ════════════════════════════════════════════════════════════════════


@router.post("/workflows/{workflow_id}/run")
def run_workflow(workflow_id: int):
    workflow = dag_service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="工作流不存在")
    try:
        validate_graph(workflow["graph_json"])
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=f"工作流配置不合法: {exc}")
    try:
        result = dispatch_run(workflow_id, trigger_type="manual")
    except Exception as exc:
        logger.exception("workflow %s 手动触发失败", workflow_id)
        raise HTTPException(status_code=503, detail=f"任务派发失败: {exc}")
    return {"status": "queued", **result}


@router.get("/workflows/{workflow_id}/runs")
def list_workflow_runs(
    workflow_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    return dag_service.list_runs(workflow_id=workflow_id, status=status, page=page, size=size)


@router.get("/runs")
def list_runs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    workflow_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
):
    return dag_service.list_runs(workflow_id=workflow_id, status=status, page=page, size=size)


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    run = dag_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行实例不存在")
    return {"run": run, "nodes": [_safe_node_run(n) for n in dag_service.list_node_runs(run_id)]}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: int):
    changed = dag_service.cancel_run(run_id)
    return {"status": "cancelled" if changed else "already_finished", "cancelled": changed}


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: int):
    """重跑：以同一工作流配置发起新运行（失败节点重跑语义由图依赖自然覆盖）。"""
    run = dag_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行实例不存在")
    try:
        result = dispatch_run(run["workflow_id"], trigger_type="retry")
    except Exception as exc:
        logger.exception("run %s 重跑失败", run_id)
        raise HTTPException(status_code=503, detail=f"任务派发失败: {exc}")
    return {"status": "queued", **result}


# ════════════════════════════════════════════════════════════════════
# SQL ⇄ DAG 双向转换
# ════════════════════════════════════════════════════════════════════


class SqlToDagRequest(BaseModel):
    sql: str
    datasource: str = ""          # 主数据源名（未限定表的所属源）
    target_datasource: str = ""   # INSERT 目标未限定时的目标数据源名


class DagToSqlRequest(BaseModel):
    graph_json: dict


class DataflowRequest(BaseModel):
    graph_json: dict


@router.post("/translate/dataflow")
def translate_dataflow(req: DataflowRequest):
    """从 DAG 定义解析数据流拓扑（编辑器实时预览用，不落库）。"""
    from backend.modules.flow.dag.sql_translator import extract_dataflow
    try:
        return extract_dataflow(req.graph_json or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"数据流解析失败: {exc}")


class ExplainRequest(BaseModel):
    sql: str
    datasource: str                     # 主数据源名
    federated_names: Optional[list] = None


@router.post("/explain")
def explain_sql(req: ExplainRequest, request: Request):
    """执行诊断：治理后 EXPLAIN，返回计划文本（不含数据行）。

    数据下推情况 / Join 策略 / 扫描代价可由此查看（同步与 SQL 任务通用）。
    """
    from backend.common.db import get_datasource_by_name
    from backend.modules.mind.nl2sql.sql.query_executor import explain_query_with_permission
    source = get_datasource_by_name(req.datasource)
    if not source:
        raise HTTPException(status_code=400, detail=f"数据源 '{req.datasource}' 不存在")
    user = request.state.current_user
    try:
        plan = explain_query_with_permission(
            req.sql, int(source["id"]),
            user_context={"user_id": user.get("user_id"), "username": user.get("username")},
            workspace_id=0,
            federated_names=req.federated_names or None)
        return {"plan": plan}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.exception("explain 失败")
        raise HTTPException(status_code=400, detail=f"执行计划获取失败: {exc}")


@router.post("/translate/sql-to-dag")
def translate_sql_to_dag(req: SqlToDagRequest):
    """SQL 脚本拆解为 DAG（解析预览，不落库；确认后由前端提交 workflow 创建）。"""
    from backend.modules.flow.dag.sql_translator import sql_to_dag
    try:
        graph = sql_to_dag(req.sql, req.datasource, req.target_datasource)
        # 落库前同样过校验（含 UDF/数据源检查），保证预览即所建
        validate_graph(graph)
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"graph_json": graph, "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"])}


@router.post("/translate/dag-to-sql")
def translate_dag_to_sql(req: DagToSqlRequest):
    """DAG 导出为拓扑序 SQL 脚本（人审阅用，非事实源）。"""
    from backend.modules.flow.dag.sql_translator import dag_to_sql
    try:
        validate_graph(req.graph_json)
        sql_script = dag_to_sql(req.graph_json)
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"sql": sql_script}
