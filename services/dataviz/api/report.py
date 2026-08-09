"""Report Generation API — 报表生成和查看."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from services.shared.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def list_reports(
    workspace_id: int = Query(0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取报表列表."""
    try:
        offset = (page - 1) * page_size
        rows = execute_query(
            """SELECT * FROM adh_reports
               WHERE workspace_id = %s
               ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (workspace_id, page_size, offset),
        )
        total = execute_query(
            "SELECT COUNT(*) as cnt FROM adh_reports WHERE workspace_id = %s",
            (workspace_id,),
            fetchone=True,
        )
        return {"items": rows, "total": total["cnt"] if total else 0}
    except Exception as e:
        logger.error("List reports failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
def generate_report(
    workspace_id: int = Query(0),
    template_key: Optional[str] = None,
    title: Optional[str] = None,
):
    """生成报表（LLM驱动）."""
    try:
        # Placeholder: actual report generation requires LLM integration
        report_id = execute_insert(
            """INSERT INTO adh_reports (workspace_id, title, status, content)
               VALUES (%s, %s, 'pending', %s)""",
            (workspace_id, title or "自动生成报表", json.dumps({"template_key": template_key})),
        )
        return {"id": report_id, "status": "pending", "message": "Report generation queued"}
    except Exception as e:
        logger.error("Generate report failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{report_id}")
def get_report(report_id: int, workspace_id: int = Query(0)):
    """获取报表详情."""
    try:
        report = execute_query(
            "SELECT * FROM adh_reports WHERE id = %s AND workspace_id = %s",
            (report_id, workspace_id),
            fetchone=True,
        )
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get report failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{report_id}/public")
def get_public_report(report_id: int):
    """公开报表查看（无需认证）."""
    try:
        report = execute_query(
            "SELECT id, title, content, created_at FROM adh_reports WHERE id = %s",
            (report_id,),
            fetchone=True,
        )
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get public report failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
