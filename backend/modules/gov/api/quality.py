"""Data Quality API — rules, execution, results, reports, and dashboard.

对齐真实 DB schema（adh_quality_rules: rule_name/rule_config/target_datasource_id）
与前端契约（规则列表返回数组含 last_check_*；dashboard 返回 overall_score 等字段）。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends, Request
from backend.common.auth import authorize_resource_scope, get_current_user
from pydantic import BaseModel, Field

from backend.common.db import DBConnection

logger = logging.getLogger(__name__)


async def _quality_access(request: Request, user: dict = Depends(get_current_user)):
    """所有质量入口显式校验工作空间，规则 ID 不能越权选择其他空间。"""
    ws = request.query_params.get("workspace_id")
    rule_id = request.path_params.get("rule_id") or request.query_params.get("rule_id")
    if rule_id:
        rule = _load_rule(int(rule_id))
        actual = rule.get("workspace_id") or 0
        if ws is not None and int(ws) != actual:
            raise HTTPException(status_code=404, detail="规则不存在")
        ws = actual
    elif ws is None and request.method in ("POST", "PUT"):
        try:
            body = await request.json()
            ws = body.get("workspace_id", 0) if isinstance(body, dict) else 0
        except ValueError:
            ws = 0
    authorize_resource_scope(user, ws)


router = APIRouter(dependencies=[Depends(_quality_access)])

RULE_TYPES = [
    "not_null", "unique", "range", "format", "referential",
    "custom_sql", "freshness", "row_count", "distribution",
]


# ── Schemas ──────────────────────────────────────────────────────────

class QualityRuleCreate(BaseModel):
    workspace_id: int = 0
    name: str = Field(..., min_length=1, max_length=200)
    rule_type: str = Field(..., description="|".join(RULE_TYPES))
    target_table: str = Field(..., min_length=1)
    target_column: Optional[str] = None
    target_datasource_id: Optional[int] = None
    rule_config: dict = Field(default_factory=dict, description="规则参数（阈值、表达式等）")
    severity: str = Field(default="medium", description="low|medium|high|critical")
    description: str = ""
    is_active: bool = True


class QualityRuleUpdate(BaseModel):
    name: Optional[str] = None
    rule_type: Optional[str] = None
    target_table: Optional[str] = None
    target_column: Optional[str] = None
    target_datasource_id: Optional[int] = None
    rule_config: Optional[dict] = None
    severity: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _normalize_rule(row: dict) -> dict:
    """DB 行 → 前端契约：rule_config 反序列化，附带最近一次检查状态."""
    if row is None:
        return row
    # 前端契约用 name，DB 列为 rule_name
    if "rule_name" in row:
        row["name"] = row.pop("rule_name")
    cfg = row.get("rule_config")
    if isinstance(cfg, str):
        try:
            row["rule_config"] = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            row["rule_config"] = {}
    # 最近一次检查结果（由列表查询 JOIN 得到，可能为 None）
    if row.get("last_check_time"):
        row["last_check_at"] = row.pop("last_check_time")
        row["last_check_status"] = "passed" if row.pop("last_passed", None) else "failed"
        if row.get("last_detail") and isinstance(row["last_detail"], str):
            try:
                if json.loads(row["last_detail"]).get("error"):
                    row["last_check_status"] = "error"
            except (json.JSONDecodeError, TypeError):
                pass
    else:
        row["last_check_at"] = None
        row["last_check_status"] = None
        row.pop("last_passed", None)
    row.pop("last_detail", None)
    return row


# ── CRUD ─────────────────────────────────────────────────────────────

@router.get("/rules")
def list_rules(
    workspace_id: int = Query(0),
    target_table: Optional[str] = Query(None, description="目标表模糊匹配"),
    rule_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    is_active: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="规则名称/描述模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """List quality rules with latest check status.

    双档契约（.qoder/rules/list-pagination-server-side.md）：
    * 不传 page_size → 返回全量数组（选用面/兼容旧调用）；
    * 传 page_size → 返回 {items,total,page,page_size}，total 来自服务端 COUNT，
      筛选/搜索全部下推 SQL，偏移由页号算出，排序以唯一 id 兜底。

    口径：workspace_id=0 为全局/未分域（与 adh_sensitive_fields 的 IN (ws,0) 一致），
    查询含全局行；最近检查结果不再按执行者过滤（质量结果是治理域共享信息）。
    """
    conditions = ["r.workspace_id IN (%s, 0)"]
    params = [workspace_id]
    if target_table:
        conditions.append("r.target_table LIKE %s")
        params.append(f"%{target_table}%")
    if rule_type:
        conditions.append("r.rule_type = %s")
        params.append(rule_type)
    if severity:
        conditions.append("r.severity = %s")
        params.append(severity)
    if is_active is not None:
        conditions.append("r.is_active = %s")
        params.append(is_active)
    if q and q.strip():
        like = f"%{q.strip()}%"
        conditions.append("(r.rule_name LIKE %s OR r.description LIKE %s)")
        params.extend([like, like])

    where = " AND ".join(conditions)
    select_sql = f"""SELECT r.*, lr.check_time AS last_check_time,
                       lr.passed AS last_passed, lr.detail AS last_detail
                FROM adh_quality_rules r
                LEFT JOIN (
                    SELECT q1.rule_id, q1.check_time, q1.passed, q1.detail
                    FROM adh_quality_results q1
                    JOIN (
                        SELECT rule_id, MAX(check_time) AS max_time
                        FROM adh_quality_results
                        GROUP BY rule_id
                    ) q2 ON q1.rule_id = q2.rule_id AND q1.check_time = q2.max_time
                ) lr ON lr.rule_id = r.id
                WHERE {where}
                ORDER BY r.id DESC"""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            if page_size is None:
                cur.execute(select_sql, params)
                return [_normalize_rule(r) for r in cur.fetchall()]

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_quality_rules r WHERE {where}", params)
            total = int((cur.fetchone() or {}).get("total") or 0)
            offset = (page - 1) * page_size
            cur.execute(f"{select_sql} LIMIT %s OFFSET %s", params + [page_size, offset])
            rows = cur.fetchall()

    return {
        "items": [_normalize_rule(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/rules")
def create_rule(body: QualityRuleCreate):
    """Create a new quality rule."""
    if body.rule_type not in RULE_TYPES:
        raise HTTPException(status_code=400, detail=f"rule_type 必须是: {', '.join(RULE_TYPES)}")
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adh_quality_rules
                   (workspace_id, rule_name, description, rule_type, target_datasource_id,
                    target_table, target_column, rule_config, severity, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (body.workspace_id, body.name, body.description, body.rule_type,
                 body.target_datasource_id, body.target_table, body.target_column,
                 json.dumps(body.rule_config, ensure_ascii=False), body.severity,
                 int(body.is_active)),
            )
            rule_id = cur.lastrowid
    return {"id": rule_id, "message": "Rule created"}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: QualityRuleUpdate):
    """Update an existing quality rule."""
    field_map = {"name": "rule_name"}
    fields = []
    params = []
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "rule_config":
            value = json.dumps(value, ensure_ascii=False)
        if field == "is_active":
            value = int(value)
        if field == "rule_type" and value not in RULE_TYPES:
            raise HTTPException(status_code=400, detail=f"rule_type 必须是: {', '.join(RULE_TYPES)}")
        fields.append(f"{field_map.get(field, field)} = %s")
        params.append(value)

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(rule_id)
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE adh_quality_rules SET {', '.join(fields)} WHERE id = %s", params)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Rule not found")
    return {"message": "Rule updated"}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    """Delete a quality rule and its results."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_quality_results WHERE rule_id = %s", (rule_id,))
            cur.execute("DELETE FROM adh_quality_rules WHERE id = %s", (rule_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Rule not found")
    return {"message": "Rule deleted"}


# ── Execution ────────────────────────────────────────────────────────

def _load_rule(rule_id: int) -> dict:
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_quality_rules WHERE id = %s", (rule_id,))
            rule = cur.fetchone()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.post("/rules/{rule_id}/execute")
def execute_rule(rule_id: int, user: dict = Depends(get_current_user)):
    """Execute a single quality rule check."""
    from backend.modules.gov.services.quality_engine import execute_single_rule

    rule = _load_rule(rule_id)
    result = execute_single_rule(rule, user)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/execute")
def execute_workspace_rules(workspace_id: int = Query(...), user: dict = Depends(get_current_user)):
    """Execute all active rules for a workspace."""
    from backend.modules.gov.services.quality_engine import execute_single_rule

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adh_quality_rules WHERE workspace_id IN (%s, 0) AND is_active = 1",
                (workspace_id,),
            )
            rows = cur.fetchall()
    if not rows:
        return {"message": "No active rules found", "results": []}

    results = []
    for rule in rows:
        results.append(execute_single_rule(rule, user))

    passed = sum(1 for r in results if r.get("passed"))
    return {"executed": len(results), "passed": passed, "failed": len(results) - passed, "results": results}


@router.get("/rules/{rule_id}/samples")
def rule_samples(rule_id: int, user: dict = Depends(get_current_user)):
    from backend.modules.gov.services.quality_engine import execute_single_rule
    result = execute_single_rule(_load_rule(rule_id), user, include_samples=True, persist=False)
    return {"samples": result.get("detail_samples", []), "error": result.get("error")}


# ── Results ──────────────────────────────────────────────────────────

@router.get("/results")
def list_results(
    rule_id: Optional[int] = Query(None),
    workspace_id: Optional[int] = Query(None),
    passed: Optional[int] = Query(None, description="1=仅通过 0=仅失败/异常，缺省不过滤"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """Get quality check results with optional filters.

    口径：含全局(workspace 0)行，不按执行者过滤（质量结果属治理域共享信息）。
    """
    conditions = ["1=1"]
    params: list = []
    if rule_id:
        conditions.append("rule_id = %s")
        params.append(rule_id)
    if passed is not None:
        conditions.append("passed = %s")
        params.append(1 if passed else 0)
    if workspace_id:
        conditions.append("workspace_id IN (%s, 0)")
        params.append(workspace_id)
    if start_date:
        conditions.append("check_time >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("check_time <= %s")
        params.append(end_date + " 23:59:59")

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_quality_results WHERE {where}", params)
            total = cur.fetchone()["total"]

            cur.execute(
                f"SELECT * FROM adh_quality_results WHERE {where} ORDER BY check_time DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            rows = cur.fetchall()

    for row in rows:
        detail = row.get("detail") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except (ValueError, TypeError):
                detail = {}
        # error 必须在明细里透出：否则结果行只有 failed 状态、没有可诊断的失败原因，
        # 前端「问题 TOP10」点进去只能看到一个红点（no-silent-degradation）。
        row["detail"] = {k: detail[k] for k in ("execution", "status", "error") if k in detail}

    return {"total": total, "page": page, "page_size": page_size, "items": rows}


# ── Deletion（人工清理：质量报告 / 问题记录）────────────────────────

@router.delete("/reports/{report_id}")
def delete_report(report_id: int, user: dict = Depends(get_current_user)):
    """删除一份质量报告（报告只是当时快照，删掉不影响规则与检查结果）。"""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, workspace_id FROM adh_quality_reports WHERE id = %s",
                (report_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="报告不存在")
            authorize_resource_scope(user, row.get("workspace_id") or 0)
            cur.execute("DELETE FROM adh_quality_reports WHERE id = %s", (report_id,))
    return {"message": "Report deleted", "id": report_id}


def _delete_result_row(result_id: int, user: dict) -> dict:
    """删单条检查结果（问题记录）；不存在 404、越权 403。

    单条与批量共用同一实现：批量只逐条调用并回报失败项，不另写一套删改逻辑。
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, rule_id, workspace_id FROM adh_quality_results WHERE id = %s",
                (result_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="问题记录不存在")
            authorize_resource_scope(user, row.get("workspace_id") or 0)
            cur.execute("DELETE FROM adh_quality_results WHERE id = %s", (result_id,))
    return row


@router.delete("/results/{result_id}")
def delete_result(result_id: int, user: dict = Depends(get_current_user)):
    """删除单条检查结果（问题记录）。"""
    row = _delete_result_row(result_id, user)
    return {"message": "Result deleted", "id": result_id, "rule_id": row.get("rule_id")}


class ResultBatchDelete(BaseModel):
    # 上限与前端删除弹窗的 page_size 对齐（一次最多删当页 100 条，超出分批）
    ids: list[int] = Field(default_factory=list, min_length=1, max_length=100)


@router.post("/results/batch-delete")
def batch_delete_results(body: ResultBatchDelete,
                         user: dict = Depends(get_current_user)):
    """批量删除问题记录：逐条复用单条实现，逐条回报失败原因（部分成功不静默）。"""
    deleted: list[int] = []
    failed: list[dict] = []
    for result_id in dict.fromkeys(body.ids):
        try:
            _delete_result_row(result_id, user)
            deleted.append(result_id)
        except HTTPException as exc:
            failed.append({"id": result_id, "reason": str(exc.detail)})
    return {
        "deleted_count": len(deleted),
        "deleted": deleted,
        "failed": failed,
        "message": f"已删除 {len(deleted)} 条",
    }


# ── Reports ──────────────────────────────────────────────────────────

@router.get("/reports")
def list_reports(
    workspace_id: int = Query(0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """Get quality reports."""
    offset = (page - 1) * page_size
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM adh_quality_reports WHERE workspace_id IN (%s, 0)",
                (workspace_id,),
            )
            total = cur.fetchone()["total"]

            cur.execute(
                """SELECT * FROM adh_quality_reports WHERE workspace_id IN (%s, 0)
                   ORDER BY report_date DESC, id DESC LIMIT %s OFFSET %s""",
                (workspace_id, page_size, offset),
            )
            rows = cur.fetchall()

    items = []
    for row in rows:
        if row.get("summary") and isinstance(row["summary"], str):
            try:
                row["summary"] = json.loads(row["summary"])
            except (json.JSONDecodeError, TypeError):
                row["summary"] = {}
        items.append(_report_view(row))

    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _report_view(row: dict) -> dict:
    """报告行 → 前端契约（pass_rate/total_checks/passed_checks）."""
    total_rules = row.get("total_rules") or 0
    passed_rules = row.get("passed_rules") or 0
    score = float(row.get("overall_score") or 0)
    return {
        "id": row["id"],
        "workspace_id": row.get("workspace_id"),
        "report_date": str(row.get("report_date")),
        "report_name": (row.get("summary") or {}).get("report_name", ""),
        "overall_score": score,
        "pass_rate": round(passed_rules / total_rules * 100, 2) if total_rules else 0.0,
        "total_checks": total_rules,
        "passed_checks": passed_rules,
        "failed_checks": row.get("failed_rules") or 0,
        "summary": row.get("summary"),
        "created_at": str(row.get("created_at")),
    }


@router.post("/reports/generate")
def generate_report(workspace_id: int = Query(...), report_name: str = Query(""),
                    user: dict = Depends(get_current_user)):
    """Generate a quality report by running all active rules and aggregating."""
    from backend.modules.gov.services.quality_engine import execute_single_rule

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adh_quality_rules WHERE workspace_id = %s AND is_active = 1",
                (workspace_id,),
            )
            rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No active rules found")

    rule_results = []
    passed_count = 0
    for rule in rows:
        result = execute_single_rule(rule, user)
        rule_results.append(result)
        if result.get("passed"):
            passed_count += 1

    total_count = len(rows)
    score = round(passed_count / total_count * 100, 2) if total_count else 0
    name = report_name or f"质量报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    summary = {
        "executed_by": user["user_id"],
        "scope": "authorized",
        "report_name": name,
        "total_rules": total_count,
        "passed": passed_count,
        "failed": total_count - passed_count,
        "score": score,
        "details": rule_results,
    }

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adh_quality_reports
                   (workspace_id, report_date, total_rules, passed_rules, failed_rules,
                    overall_score, summary)
                   VALUES (%s, CURDATE(), %s, %s, %s, %s, %s)""",
                (workspace_id, total_count, passed_count, total_count - passed_count,
                 score, json.dumps(summary, ensure_ascii=False, default=str)),
            )
            report_id = cur.lastrowid

    return {"id": report_id, **_report_view({
        "id": report_id, "workspace_id": workspace_id,
        "report_date": datetime.now().date(), "total_rules": total_count,
        "passed_rules": passed_count, "failed_rules": total_count - passed_count,
        "overall_score": score, "summary": summary,
        "created_at": datetime.now(),
    }), "details": rule_results}


# ── Dashboard ────────────────────────────────────────────────────────

@router.get("/dashboard")
def quality_dashboard(workspace_id: int = Query(0), user: dict = Depends(get_current_user)):
    """Quality overview: overall score, pass rate, rule counts, recent reports, top issues."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            # 规则总数与启用数（含全局 workspace 0）
            cur.execute(
                """SELECT COUNT(*) AS total_rules, SUM(is_active) AS active_rules
                   FROM adh_quality_rules WHERE workspace_id IN (%s, 0)""",
                (workspace_id,),
            )
            rule_stats = cur.fetchone() or {}

            # 近 30 天检查结果：通过率与平均分（质量结果属治理域共享信息，不按执行者过滤）
            cur.execute(
                """SELECT COUNT(*) AS total_checks,
                          SUM(passed) AS passed_checks,
                          ROUND(AVG(pass_rate), 2) AS avg_pass_rate
                   FROM adh_quality_results
                   WHERE workspace_id IN (%s, 0) AND check_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)""",
                (workspace_id,),
            )
            check_stats = cur.fetchone() or {}

            total_checks = check_stats.get("total_checks") or 0
            passed_checks = check_stats.get("passed_checks") or 0
            pass_rate = round(passed_checks / total_checks * 100, 2) if total_checks else 0.0
            overall_score = float(check_stats.get("avg_pass_rate") or 0.0)

            # 最近报告
            cur.execute(
                """SELECT * FROM adh_quality_reports WHERE workspace_id IN (%s, 0)
                   ORDER BY report_date DESC, id DESC LIMIT 5""",
                (workspace_id,),
            )
            report_rows = cur.fetchall()
            recent_reports = []
            for row in report_rows:
                if row.get("summary") and isinstance(row["summary"], str):
                    try:
                        row["summary"] = json.loads(row["summary"])
                    except (json.JSONDecodeError, TypeError):
                        row["summary"] = {}
                recent_reports.append(_report_view(row))

            # TOP 问题规则：近 30 天失败次数最多
            cur.execute(
                """SELECT r.id AS rule_id, r.rule_name, r.rule_type, r.target_table,
                          r.severity, SUM(qr.passed = 0) AS failure_count,
                          MAX(CASE WHEN qr.passed = 0 THEN qr.check_time END) AS last_failure_at
                   FROM adh_quality_results qr
                   JOIN adh_quality_rules r ON qr.rule_id = r.id
                   WHERE qr.workspace_id IN (%s, 0)
                     AND qr.check_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                   GROUP BY r.id, r.rule_name, r.rule_type, r.target_table, r.severity
                   HAVING failure_count > 0
                   ORDER BY failure_count DESC
                   LIMIT 10""",
                (workspace_id,),
            )
            top_issues = []
            for row in cur.fetchall():
                top_issues.append({
                    **row,
                    "failure_count": int(row["failure_count"]),
                    "last_failure_at": str(row["last_failure_at"]) if row["last_failure_at"] else None,
                })

    return {
        "overall_score": overall_score,
        "pass_rate": pass_rate,
        "total_rules": rule_stats.get("total_rules") or 0,
        "active_rules": int(rule_stats.get("active_rules") or 0),
        "recent_reports": recent_reports,
        "top_issues": top_issues,
    }
