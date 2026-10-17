"""Report Generation API — 报表生成与报表中心管理接口."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query, Depends, Request, BackgroundTasks
from backend.common.auth import get_current_user, authorize_workspace
from backend.modules.viz.services import report_service
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from backend.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
def list_reports(
    workspace_id: int = Query(0, description="0=全部工作站（报表跟随属主）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """获取报表清单（仅当前用户自己创建的，可按工作站收窄）."""
    return report_service.list_reports(user["user_id"], workspace_id, page, page_size, user=user)


ReportGenerateRequest = report_service.ReportSubmission


class ReportCreateRequest(BaseModel):
    """新建报表：manual=人写正文；conversation=把会话里一轮分析结果存为报表。

    conversation 不接受客户端传正文：内容只从服务端已落库的会话读（身份与内容都不信 body）。
    """
    model_config = ConfigDict(extra="forbid")
    workspace_id: int = Field(0, ge=0)
    title: str = ""
    content: str = ""
    format: str = "markdown"
    source_type: str = "manual"
    conversation_id: int = Field(0, ge=0)
    message_index: Optional[int] = None
    message_uuid: str = ""


class ReportUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    content: Optional[str] = None
    format: Optional[str] = None


@router.post("", status_code=201)
def create_report(req: ReportCreateRequest, user: dict = Depends(get_current_user)):
    """报表中心新建报表（内容型，仅本人可见可编辑）。"""
    if req.source_type == "conversation":
        try:
            return report_service.save_conversation_report(
                user=user, conversation_id=req.conversation_id, title=req.title,
                message_index=req.message_index, message_uuid=req.message_uuid)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.source_type != "manual":
        raise HTTPException(status_code=422, detail="报表来源只能是 manual 或 conversation")
    authorize_workspace(user, req.workspace_id)
    try:
        return report_service.create_content_report(
            title=req.title, content=req.content, format=req.format,
            workspace_id=req.workspace_id, owner_id=user["user_id"], source_type="manual",
            origin={"channel": "report_center"})
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{report_id}")
def update_report(report_id: int, req: ReportUpdateRequest,
                  user: dict = Depends(get_current_user)):
    """重命名 / 编辑报表（仅内容型报表、仅属主）。"""
    try:
        return report_service.update_content_report(
            report_id, user, title=req.title, content=req.content, format=req.format)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{report_id}")
def delete_report(report_id: int, user: dict = Depends(get_current_user)):
    """删除报表（仅内容型报表、仅属主）。"""
    try:
        return report_service.delete_report(report_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{report_id}/detail")
def get_report_detail(report_id: int, user: dict = Depends(get_current_user)):
    """报表中心应用内查看：返回完整正文，走属主/快照校验，免外链 token。"""
    report = report_service.get_report(report_id, user=user)
    if not report:
        raise HTTPException(status_code=404, detail="报表不存在或无权访问")
    return report


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
