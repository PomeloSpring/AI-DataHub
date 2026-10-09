"""Report Service — Report generation and retrieval.

Handles LLM-powered report generation from templates and query data.
Tables: adh_reports, adh_report_templates, adh_saved_queries
"""

import json
import logging
import secrets
import time
from typing import Optional

from backend.common.db import DBConnection
from backend.core.governed_query import governed_execute
from backend.common.auth import authorize_resource_scope, authorize_workspace, resolve_execution_owner
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.modules.viz.services import report_access


class ReportSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: int = Field(ge=0)
    title: str = "分析报告"
    template_key: str = ""
    dataset_id: Optional[int] = Field(None, gt=0)
    dataset_query: dict = Field(default_factory=dict)
    intent: Optional[dict] = None
    saved_query_id: Optional[int] = Field(None, gt=0)

    @model_validator(mode="after")
    def validate_source(self):
        if sum(v is not None for v in (self.dataset_id, self.intent, self.saved_query_id)) != 1:
            raise ValueError("必须指定且只能指定一种分析来源")
        if self.intent is not None:
            from backend.semantics.intent import parse_intent
            if any(k in self.intent for k in ("datasource_id", "user_id", "workspace_id")):
                raise ValueError("分析意图不能指定基础设施身份")
            query, error, _ = parse_intent(self.intent)
            if error or query.dry_run:
                raise ValueError("报告需要合法的可执行语义意图")
        return self
from backend.modules.viz.services.report_access import can_access_report, public_report, json_object

logger = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(time.time() * 1000000)


def _normalize_report(row: dict) -> dict:
    """Normalize report row for JSON serialization."""
    for ts in ("created_at",):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    return row


# ── Report CRUD ─────────────────────────────────────────────────────────────


def list_reports(user_id: int, workspace_id: int = 0, page: int = 1, size: int = 20,
                 user: dict = None) -> dict:
    """List generated reports with pagination."""
    if not user or int(user.get("user_id") or 0) != int(user_id):
        raise PermissionError("报告列表缺少可信身份")
    authorize_workspace(user, workspace_id)
    with DBConnection() as conn:
        with conn.cursor() as cur:
            conditions = ["owner_id = %s", "workspace_id = %s"]
            params = [user_id, workspace_id]

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_reports {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT * FROM adh_reports {where} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            return {"items": [public_report(r, content=False) for r in rows], "total": total}


def load_report(report_id: int) -> Optional[dict]:
    """仅供服务内部读取原始记录，不可作为对外响应。"""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_reports WHERE id = %s", (report_id,))
            return cur.fetchone()


def get_report(report_id: int, access_token: str = None, user: dict = None) -> Optional[dict]:
    row = load_report(report_id)
    if not row:
        return None
    if not can_access_report(row, user, access_token):
        if user and int(row.get("owner_id") or 0) == int(user.get("user_id") or 0):
            authorize_workspace(user, row.get("workspace_id") or 0)
            result = public_report(row, content=False)
            result["content"] = ""
            result["access_warning"] = "报告未就绪或权限已变化，请重新生成"
            return result
        return None
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE adh_reports SET view_count = view_count + 1 WHERE id = %s", (report_id,))
    return public_report(row)


def create_report(*, task_id: int = None, log_id: int = None, title: str,
                  content: str, format: str = "markdown", workspace_id: int,
                  owner_id: int, security_context: dict = None, run_key: str = None,
                  generation_status: str = "degraded", evidence_summary: dict = None,
                  analysis_source: dict = None) -> dict:
    """唯一报告持久化入口；未认证的旧结果只能保存为不可分享的降级报告。"""
    if int(owner_id or 0) <= 0:
        raise PermissionError("报告缺少创建者")
    report_id = _generate_id()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_reports (id, task_id, log_id, title, content, format, access_mode, "
                "workspace_id, owner_id, created_at, generation_status, publication_status, "
                "security_context, run_key, evidence_summary, analysis_source, lease_expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'private',%s,%s,%s,%s,'draft',%s,%s,%s,%s,"
                "DATE_ADD(NOW(), INTERVAL 10 MINUTE)) ON DUPLICATE KEY UPDATE id=id",
                (report_id, task_id, log_id, title, content, format, workspace_id, owner_id, _now(),
                 generation_status, json.dumps(security_context or {}, ensure_ascii=False), run_key,
                 json.dumps(evidence_summary or {}, ensure_ascii=False, default=str),
                 json.dumps(analysis_source or {}, ensure_ascii=False, default=str)),
            )
            if run_key:
                cur.execute("SELECT id, owner_id, workspace_id, generation_status FROM adh_reports WHERE run_key=%s", (run_key,))
                existing = cur.fetchone()
                if not existing or existing["owner_id"] != owner_id or existing["workspace_id"] != workspace_id:
                    raise PermissionError("报告运行键冲突")
                report_id, generation_status = existing["id"], existing["generation_status"]
    return {"id": report_id, "run_id": run_key, "access_mode": "private", "generation_status": generation_status}


def submit_report(request: dict, user: dict, background_tasks) -> dict:
    """先保存可追踪的运行，再真实派发；禁止无来源占位报告。"""
    from uuid import uuid4
    from fastapi import HTTPException
    req = ReportSubmission.model_validate(request)
    authorize_workspace(user, req.workspace_id)
    run_id = uuid4().hex
    report = create_report(title=req.title, content="", workspace_id=req.workspace_id,
        owner_id=user["user_id"], run_key=run_id, generation_status="queued", analysis_source=req.model_dump())
    try:
        dispatch_report(report["id"], run_id, background_tasks)
    except Exception:
        logger.exception("报告派发失败")
        finish_report(report["id"], generation_status="failed", stage_error_code="DISPATCH_FAILED")
        raise HTTPException(status_code=503, detail="报告队列暂不可用，派发未完成") from None
    return {"report_id": report["id"], "run_id": run_id, "generation_status": "queued"}


def dispatch_report(report_id, run_id, background_tasks):
    import os
    mode = os.getenv("ADH_TASK_EXECUTION_MODE", "celery")
    if mode == "background":
        background_tasks.add_task(run_report, report_id)
    elif mode == "celery":
        from backend.core.task_runtime import send_task
        send_task("flow.generate_report", args=(report_id,), task_id=run_id, queue="scheduled")
    else:
        raise ValueError("不支持的执行适配器")


def claim_report(report_id):
    from backend.common.db import execute_write
    return execute_write("UPDATE adh_reports SET generation_status='running', "
        "lease_expires_at=DATE_ADD(NOW(), INTERVAL 10 MINUTE) WHERE id=%s "
        "AND generation_status='queued' AND lease_expires_at>NOW()", (report_id,)) == 1


def finish_report(report_id, **values):
    from backend.common.db import execute_write
    allowed = {"generation_status", "stage_error_code", "content", "security_context", "evidence_summary"}
    # cancelled=任务监控人工停止；与其它终态一样受 `generation_status IN ('queued','running')` 保护，迟到结果不可覆盖。
    if set(values) - allowed or values.get("generation_status") not in ("ready", "degraded", "failed", "timeout", "cancelled"):
        raise ValueError("报告更新字段或终态无效")
    updates = ",".join(f"`{key}`=%s" for key in values)
    params = tuple(json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, dict) else v
                   for v in values.values()) + (report_id,)
    return execute_write(f"UPDATE adh_reports SET {updates}, "
        "generation_status=IF(lease_expires_at<=NOW(),'timeout',generation_status), "
        "content=IF(lease_expires_at<=NOW(),'',content), "
        "stage_error_code=IF(lease_expires_at<=NOW(),'TIMEOUT',stage_error_code) WHERE id=%s "
        "AND generation_status IN ('queued','running')", params) == 1


def run_report(report_id):
    from billiard.exceptions import SoftTimeLimitExceeded
    from backend.core.task_runtime import RunInterrupted
    report = load_report(report_id)
    if not report or not claim_report(report_id):
        return {"report_id": report_id, "duplicate": True}
    try:
        identity = resolve_execution_owner(report["owner_id"], report["workspace_id"])
        from backend.core.task_runtime import guarded_call
        result = guarded_call(execute_report_source, json_object(report["analysis_source"]), identity,
                              deadline=time.monotonic() + 600)
        content = render_fact_report(report["title"], [result])
        finish_report(report_id, generation_status="degraded", content=content,
            security_context=result.get("_security_context") or {},
            evidence_summary={"validation_status": "unverified", "quality_status": "unknown",
                              "completeness": "sample_only"}, stage_error_code="FACT_ONLY")
    except (RunInterrupted, TimeoutError, SoftTimeLimitExceeded):
        finish_report(report_id, generation_status="timeout", stage_error_code="TIMEOUT")
    except Exception:
        logger.exception("报告 %s 生成失败", report_id)
        finish_report(report_id, generation_status="failed", stage_error_code="QUERY_FAILED", content="")
    return {"report_id": report_id}


def cleanup_stale_reports():
    from backend.common.db import execute_write
    return execute_write("UPDATE adh_reports SET generation_status='timeout', stage_error_code='TIMEOUT', "
                         "content='' WHERE generation_status IN ('queued','running') AND lease_expires_at<=NOW()")


def _get_template_content(template_key: str) -> Optional[str]:
    """Look up report template content by name or ID."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Try by name first
            cur.execute(
                "SELECT content FROM adh_report_templates WHERE name = %s LIMIT 1",
                (template_key,),
            )
            row = cur.fetchone()
            if row:
                return row.get("content")

            # Try by ID
            try:
                tid = int(template_key)
                cur.execute("SELECT content FROM adh_report_templates WHERE id = %s", (tid,))
                row = cur.fetchone()
                if row:
                    return row.get("content")
            except (ValueError, TypeError):
                pass

    return None


def _execute_data_query(data_query: str, owner_id: int = 0, workspace_id: int = 0,
                        datasource_id: int = 0) -> dict:
    """Execute a SQL query through the data moat and return the results.

    身份以报表创建者(owner_id)构造 user_context, 经统一治理执行器(敏感 block/mask
    + RLS + 审计), 无可信身份 -> fail-closed 拒绝取数(I5)。不再裸连数据源。

    Supports referencing saved queries by ID (prefix with 'query:') or direct SQL.
    """
    if not data_query:
        return {"columns": [], "rows": [], "row_count": 0}

    sql = data_query

    # If it's a saved query reference
    if data_query.startswith("query:"):
        try:
            query_id = int(data_query.split(":", 1)[1])
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT sql_query FROM adh_saved_queries WHERE id = %s", (query_id,))
                    row = cur.fetchone()
                    if row:
                        sql = row["sql_query"]
                    else:
                        return {"columns": [], "rows": [], "row_count": 0, "error": "Saved query not found"}
        except (ValueError, IndexError):
            return {"columns": [], "rows": [], "row_count": 0, "error": "Invalid query reference"}

    # Execute the SQL
    sql = sql.strip().rstrip(";")
    if "limit" not in sql.lower():
        sql += " LIMIT 500"

    try:
        return governed_execute(sql, datasource_id, owner_id, workspace_id)
    except Exception as e:
        logger.warning("Data query execution failed: %s", e)
        return {"columns": [], "rows": [], "row_count": 0, "error": "报告数据查询未完成，请检查权限或联系管理员"}


def render_fact_report(title: str, results: list) -> str:
    """M2 只发布私有降级事实表；M4 证据校验就绪前不生成推断性正文。"""
    import html
    def cell(value):
        text = html.escape(str(value)).replace("\n", " ")
        for marker in (chr(92), "`", "*", "_", "[", "]", "(", ")", "!", "|"):
            text = text.replace(marker, chr(92) + marker)
        return text
    if not any(r.get("status") == "success" for r in results):
        raise ValueError("没有成功的分析结果")
    lines = [f"# {cell(title)}", "", "> 降级事实报告：尚未完成证据校验，不可对外发布。",
             "> 仅展示已授权查询返回的样本，不代表总体，不据此推断趋势或占比。", ""]
    for i, result in enumerate(results, 1):
        lines += [f"## 分析项 {i}", ""]
        if result.get("status") != "success":
            lines += ["该分析项未完成。", ""]
            continue
        columns = result.get("columns") or []
        keys = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
        # 未经业务映射认证的 SQL 列名不能直接向客户端公开。
        labels = [f"字段{n + 1}" for n in range(len(keys))]
        rows = result.get("rows") or []
        if not rows:
            lines += ["本次授权查询未返回数据。", ""]
        elif keys:
            lines += ["| " + " | ".join(labels) + " |", "| " + " | ".join("---" for _ in keys) + " |"]
            for row in rows[:100]:
                values = [row.get(k) for k in keys] if isinstance(row, dict) else row
                lines.append("| " + " | ".join(cell(v) for v in values) + " |")
            lines.append("")
    return "\n".join(lines)


def _authorize_source(identity, datasource_id):
    from backend.core.role_service import role_service
    if int(datasource_id or 0) <= 0:
        raise PermissionError("分析来源未明确绑定")
    # 纯角色裁决: 分析来源可用集=执行身份的角色授权; 空授权 fail-closed。
    allowed = set(role_service.get_user_allowed_datasources(
        identity["user_id"], identity["workspace_id"]))
    if int(datasource_id) not in allowed:
        raise PermissionError("执行身份的角色未授权该分析来源")


def _snapshot(identity, sql, datasource_id, dialect="mysql"):
    from backend.semantics.sql_guard import extract_tables
    sources = [{"datasource_id": datasource_id, "table": t} for t in extract_tables(sql, dialect)]
    return report_access.policy_snapshot(identity["user_id"], identity["workspace_id"], sources)


def _verify_snapshot(identity, security):
    current = report_access.policy_snapshot(identity["user_id"], identity["workspace_id"], security["sources"])
    if current["policy_digest"] != security["policy_digest"]:
        raise PermissionError("执行期间权限发生变化，请重新生成")
    if security.get("dataset_id") and report_access.dataset_snapshot(security["dataset_id"], identity) != security["dataset_digest"]:
        raise PermissionError("执行期间数据集范围发生变化，请重新生成")


def execute_semantic_source(intent, identity, datasource_id=0):
    """复用 intent→binding→plan→七闸门链；内部来源快照不进入 LLM 或客户端。"""
    from backend.semantics.intent import parse_intent
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.planner import plan
    from backend.semantics.gates import execute_semantic
    if not isinstance(intent, dict) or any(k in intent for k in ("user_id", "workspace_id", "datasource_id")):
        raise ValueError("语义意图不能指定执行身份")
    query, error, _ = parse_intent(intent)
    if error or query.dry_run:
        raise ValueError("分析意图无效")
    binding, _ = resolve_binding(query.object, datasource_id=datasource_id)
    if not binding:
        raise ValueError("分析对象尚未绑定或存在歧义")
    _authorize_source(identity, binding.datasource_id)
    query.datasource_id = binding.datasource_id
    query.user_id, query.workspace_id = identity["user_id"], identity["workspace_id"]
    planned = plan(query, binding)
    if not planned.sql or planned.provenance.get("unresolved_terms"):
        raise ValueError("语义分析未通过编译校验")
    security = _snapshot(identity, planned.sql, binding.datasource_id, planned.dialect)
    execution = execute_semantic(query, binding, planned, identity, question="报告分析")
    if not execution.allowed:
        raise PermissionError("分析未通过治理校验或需要人工处理")
    _verify_snapshot(identity, security)
    result = execution.result or {}
    return {"status": "success", **{k: result[k] for k in ("columns", "rows", "row_count", "truncated") if k in result},
            "_security_context": security}


def execute_report_source(source: dict, identity: dict) -> dict:
    from backend.common.db import execute_query
    from backend.semantics.sql_guard import bounded_query
    if source.get("intent") is not None:
        return execute_semantic_source(source["intent"], identity)
    if source.get("dataset_id"):
        from backend.modules.viz.services import dataset_service
        dataset = dataset_service.get_dataset(source["dataset_id"])
        if not dataset or dataset.get("status") != "active":
            raise PermissionError("分析来源不存在或不可用")
        ds_id = int(dataset.get("datasource_id") or 0)
        dataset_digest = report_access.dataset_snapshot(source["dataset_id"], identity, dataset)
        _authorize_source(identity, ds_id)
        # 执行路径按双定义显式选择: 有执行 SQL 走 SQL 快照; 仅语义对象走编译投影快照。
        if not (dataset.get("sql_query") or "").strip() and (dataset.get("object_key") or "").strip():
            # 数据集 scope 必须经数据集服务；编译相同投影只用于捕获全部物理来源。
            from backend.semantics.binding_resolver import resolve_binding
            from backend.semantics.intent import parse_intent
            from backend.semantics.planner import plan
            params = source.get("dataset_query") or {}
            q, err, _ = parse_intent({"object": dataset["object_key"], "datasource_id": ds_id,
                "metrics": params.get("measures") or params.get("metrics") or [],
                "dimensions": params.get("dimensions") or [], "filters": params.get("filters") or [],
                "order": params.get("order") or [], "limit": params.get("limit") or 200})
            binding, _ = resolve_binding(dataset["object_key"], datasource_id=ds_id)
            if err or not binding:
                raise ValueError("数据集语义来源无效")
            planned = plan(q, binding)
            if not planned.sql or planned.provenance.get("unresolved_terms"):
                raise ValueError("数据集投影未完整解析")
            security = _snapshot(identity, planned.sql, ds_id, planned.dialect)
        else:
            for condition in (source.get("dataset_query") or {}).get("filters") or []:
                if (not isinstance(condition, dict)
                        or not dataset_service._FIELD_RE.fullmatch(str(condition.get("field") or ""))
                        or condition.get("op", "eq") not in dataset_service._OPS
                        or (condition.get("op") == "in" and not isinstance(condition.get("value"), list))
                        or condition.get("value") == []):
                    raise ValueError("数据集过滤条件无效")
            security = _snapshot(identity, dataset.get("sql_query") or "", ds_id)
        security.update(dataset_id=source["dataset_id"], dataset_digest=dataset_digest)
        result = dataset_service.query_dataset(source["dataset_id"], source.get("dataset_query") or {}, identity)
    elif source.get("saved_query_id"):
        saved = execute_query("SELECT * FROM adh_saved_queries WHERE id=%s AND owner_id=%s AND workspace_id=%s",
            (source["saved_query_id"], identity["user_id"], identity["workspace_id"]), fetchone=True)
        if not saved:
            raise PermissionError("分析来源不存在或无权访问")
        ds_id = int(saved.get("datasource_id") or 0)
        _authorize_source(identity, ds_id)
        sql = bounded_query(saved["sql_query"], 1000)
        security = _snapshot(identity, sql, ds_id)
        result = governed_execute(sql, ds_id, identity["user_id"], identity["workspace_id"], identity.get("username", ""))
    else:
        raise ValueError("缺少分析来源")
    if result.get("error"):
        raise ValueError("分析未完成")
    _verify_snapshot(identity, security)
    return {"status": "success", **{k: result[k] for k in ("columns", "rows", "row_count", "truncated") if k in result},
            "_security_context": security}
