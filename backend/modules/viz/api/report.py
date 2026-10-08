"""Report Generation API — 报表生成和查看."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query, Depends, Request, BackgroundTasks
from backend.common.auth import get_current_user, authorize_workspace
from backend.modules.viz.services import report_service
from pydantic import BaseModel
from typing import Optional
from backend.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def list_reports(
    workspace_id: int = Query(0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """获取报表列表."""
    authorize_workspace(user, workspace_id)
    return report_service.list_reports(user["user_id"], workspace_id, page, page_size, user=user)


ReportGenerateRequest = report_service.ReportSubmission


@router.post("/generate", status_code=202)
def generate_report(req: ReportGenerateRequest, background_tasks: BackgroundTasks,
                    user: dict = Depends(get_current_user)):
    authorize_workspace(user, req.workspace_id)
    if sum(value is not None for value in (req.dataset_id, req.intent, req.saved_query_id)) != 1:
        raise HTTPException(status_code=422, detail="必须指定且只能指定一种分析来源")
    return report_service.submit_report(req.model_dump(), user, background_tasks)


@router.get("/{report_id}")
def get_report(report_id: int, request: Request, token: Optional[str] = Query(None)):
    report = report_service.get_report(report_id, access_token=token,
                                      user=getattr(request.state, "current_user", None))
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在或无权访问")
    return report


@router.get("/{report_id}/public")
def get_public_report(report_id: int):
    """公开报表查看（无需认证）."""
    report = report_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在或无权访问")
    return report
