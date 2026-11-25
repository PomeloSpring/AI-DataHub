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
from backend.common.auth import authorize_resource_scope, authorize_workspace, resolve_current_user

from backend.modules.flow.dag.dag_service import dag_service
from backend.modules.flow.dag.dag_executor import dispatch_run
from backend.modules.flow.dag.dag_validator import (
    DagValidationError, validate_graph, validate_run_params,
)

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
        authorize_resource_scope(user, resource.get("workspace_id"))
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
        authorize_resource_scope(user, ws)


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


class RunParams(BaseModel):
    """一次运行的参数（物化时间窗口 + 本次参与的粒度）。

    都不给 = 按节点配置的回看格数日常刷新（当前行为不变）；
    只给 end_date = 从「上次成功位置」续跑；
    start_date(+end_date) = 历史初始化/补数区间（平台不静默夹取，超量要求分段）；
    grains = 本次只跑哪些粒度（day/month/year 子集，其余节点本次跳过并回报原因）。
    """

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    grains: Optional[list[str]] = None


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
def run_workflow(workflow_id: int, body: Optional[RunParams] = None):
    workflow = dag_service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="工作流不存在")
    try:
        validate_graph(workflow["graph_json"])
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=f"工作流配置不合法: {exc}")
    # 参数先校验再落库：未识别的键/错日期若被静默忽略，就成了“指定了历史区间
    # 却按回看窗口跑了”的假初始化（no-silent-degradation）。
    asked = body.model_dump(exclude_none=True) if body else {}
    try:
        params = validate_run_params(workflow, asked)
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        result = dispatch_run(workflow_id, trigger_type="manual", params=params or None)
    except Exception as exc:
        logger.exception("workflow %s 手动触发失败", workflow_id)
        raise HTTPException(status_code=503, detail=f"任务派发失败: {exc}")
    return {"status": "queued", "params": params, **result}


@router.get("/workflows/{workflow_id}/watermarks")
def get_workflow_watermarks(workflow_id: int):
    """各节点的「最后一次执行成功的位置」（水位线）。

    编排图/运行页用它展示“这个节点上次跑到哪一期”、判断历史初始化该从哪里续。
    """
    return {"items": dag_service.list_watermarks(workflow_id)}


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
    trigger_type: Optional[str] = Query(None),
    start_from: Optional[str] = Query(None),
    start_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    """运行记录清单（服务端真分页）。

    筛选全部下推 SQL：任务 / 状态 / 触发方式 / 开始日期区间 / 任务名关键字。
    日期格式不对直接 400，不能静默丢掉条件（那会被读成“这段时间没有运行”）。
    """
    try:
        return dag_service.list_runs(
            workflow_id=workflow_id, status=status, trigger_type=trigger_type,
            start_from=start_from, start_to=start_to, q=q, page=page, size=size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


MAX_BATCH_RUNS = 50


class BatchRunIds(BaseModel):
    ids: list[int] = []


def _load_run_for_write(user: dict, run_id: int):
    """按归属取出可写的运行实例（非 admin 只能碰自己 owner 的任务）。

    批量入口没有路径参数，router 级 `_dag_access` 拿不到资源、只能校验会话空间，
    所以逐条归属必须在循环里显式补上。越权与不存在回同一句原因（不泄露他人实例是否存在）。
    """
    run = dag_service.get_run(run_id)
    workflow = dag_service.get_workflow(run["workflow_id"]) if run else None
    if not run or not workflow:
        raise LookupError("运行记录不存在或已被删除，请刷新清单")
    authorize_resource_scope(user, workflow.get("workspace_id"))
    if user.get("role") != "admin" and int(workflow.get("owner_id") or 0) != int(user["user_id"]):
        raise LookupError("运行记录不存在或已被删除，请刷新清单")
    return run, workflow


def _batch_runs(request: Request, payload: BatchRunIds, action: str) -> dict:
    """批量动作 = 逐条复用单条实现 + 逐条回报原因（不整批回滚，不新增权限码）。

    为什么逐条：删除/停止各自带终态保护（运行中不得删、已结束无需停），把已成功的
    回滚掉只会让人以为“什么都没发生”；真实语义是“能做的做了，不能做的逐条说清”。
    """
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        ids = list(dict.fromkeys(int(i) for i in (payload.ids or [])))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="运行记录标识无效")
    if not ids:
        raise HTTPException(status_code=400, detail="请先勾选要处理的运行记录")
    if len(ids) > MAX_BATCH_RUNS:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多处理 {MAX_BATCH_RUNS} 条运行记录（本次 {len(ids)} 条），请分批操作")

    verb = "删除" if action == "delete" else "停止"
    succeeded: list = []
    failed: list = []
    for run_id in ids:
        name = ""
        try:
            run, workflow = _load_run_for_write(user, run_id)
            name = workflow.get("name") or ""
            if action == "delete":
                dag_service.delete_run(run_id)
            elif not dag_service.cancel_run(run_id):
                # 已收尾的实例没有可停的东西：如实说“本次未处理”，不当成功
                failed.append({"id": run_id, "workflow_name": name,
                               "reason": "该运行已结束或已取消，无需停止"})
                continue
            succeeded.append({"id": run_id, "workflow_name": name})
        except (LookupError, ValueError, PermissionError) as exc:
            failed.append({"id": run_id, "workflow_name": name, "reason": str(exc)})

    if not succeeded:
        message = f"{len(failed)} 条未处理（原因逐条列出），本次没有改动生效"
    elif failed:
        message = f"已{verb} {len(succeeded)} 条；{len(failed)} 条未处理（原因逐条列出）"
    else:
        message = f"已{verb} {len(succeeded)} 条"
    logger.info("[dag] 批量%s by=%s ok=%s fail=%s", verb,
                user.get("username") or user.get("user_id"), len(succeeded), len(failed))
    return {"action": action, "succeeded": succeeded, "failed": failed,
            "succeeded_count": len(succeeded), "failed_count": len(failed),
            "message": message}


@router.post("/runs/batch-delete")
def batch_delete_runs(payload: BatchRunIds, request: Request):
    """批量删除运行记录：逐条走 `delete_run`（终态保护相同），失败逐条回因。"""
    return _batch_runs(request, payload, "delete")


@router.post("/runs/batch-stop")
def batch_stop_runs(payload: BatchRunIds, request: Request):
    """批量停止运行实例：逐条走 `cancel_run`，已结束的逐条标“本次未处理”。"""
    return _batch_runs(request, payload, "stop")


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    run = dag_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行实例不存在")
    nodes = dag_service.list_node_runs(run_id)
    # 统计以节点终态为准（stats_json 只在正常收尾时落库）：否则被运维中止/进程被杀/
    # 仍在跑的 run 会显示「0/15 成功、失败 0」，与下方节点明细自相矛盾。
    workflow = dag_service.get_workflow(run["workflow_id"]) or {}
    total_nodes = len(((workflow.get("graph_json") or {}).get("nodes")) or [])
    derived = dag_service.aggregate_run_stats(run_id, total_nodes=total_nodes)
    run["stats_json"] = {**(run.get("stats_json") or {}), **derived}
    return {"run": run, "nodes": [_safe_node_run(n) for n in nodes]}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: int):
    changed = dag_service.cancel_run(run_id)
    return {"status": "cancelled" if changed else "already_finished", "cancelled": changed}


@router.delete("/runs/{run_id}")
def delete_run(run_id: int):
    """删除一条运行记录（含节点实例明细）。

    只删已收尾的实例：排队/执行中的必须先停止，否则执行体会继续写节点行，
    删完又冒出来（半态）。资源归属由 router 级 `_dag_access` 校验（非 admin
    只能删自己的实例），写动作另受 `sync:manage` / `dag:manage` 门控。
    """
    run = dag_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行实例不存在")
    try:
        deleted = dag_service.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="运行实例不存在")
    return {"deleted": True, "run_id": run_id}


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: int):
    """重跑：以同一工作流配置发起新运行（失败节点重跑语义由图依赖自然覆盖）。

    沿用原运行的参数：历史初始化中断后重跑，不能默默退回日常回看窗口
    （那会只补最近几格，却报成功）。
    """
    run = dag_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行实例不存在")
    try:
        result = dispatch_run(run["workflow_id"], trigger_type="retry",
                              params=dict(run.get("params_json") or {}) or None)
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
    # 本次导出的窗口参数（start_date/end_date/grains）；留空 = 按节点 window 的日常回看
    run_params: Optional[dict] = None


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
    from backend.core.query_executor import explain_query_with_permission
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
def translate_dag_to_sql(req: DagToSqlRequest, request: Request):
    """DAG 导出为拓扑序 SQL 脚本（人审阅用，非事实源）。

    物化节点不再只给一句“取数 SQL 由平台编译”：导出时走执行期同一条编译链
    （`materialize_runner.export_materialize_sql`），输出含窗口参数与逐站展开的
    真实 `INSERT INTO ... SELECT`；数据集可见性仍按当前身份裁决（fail-closed）。
    """
    from backend.modules.flow.dag.materialize_runner import (
        MaterializeError, export_materialize_sql,
    )
    from backend.modules.flow.dag.sql_translator import dag_to_sql

    user = getattr(request.state, "current_user", None) or {}
    identity = {
        "user_id": user.get("user_id"),
        "username": user.get("username"),
        "role": user.get("role"),
        "workspace_id": int(user.get("workspace_id") or 0),
    }

    def _materialize_sql(config: dict):
        return export_materialize_sql(config, identity, req.run_params)

    try:
        validate_graph(req.graph_json)
        sql_script = dag_to_sql(req.graph_json, materialize_builder=_materialize_sql)
    except MaterializeError as exc:
        raise HTTPException(status_code=400, detail=f"物化 SQL 导出失败: {exc}")
    except DagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"sql": sql_script}
