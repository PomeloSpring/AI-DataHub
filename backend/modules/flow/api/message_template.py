"""Message Templates API — 模板中心的消息模板（IM / 邮件 / 短信）。

Mounted: /api/message-templates
权限点: message-template:read（GET）/ message-template:manage（写），菜单 system:report-templates
表: adh_message_templates

这里的 ``/variables`` 是**可用变量的唯一出口**：前端"能插哪些变量"必须读它，
不得再自己列一份 —— 旧 UI 硬写了 9 个（含 task_name/result_summary），而发送侧一个都没消费，
等于在页面上广告了发不出去、且违反"业务内容不进通知"护栏的变量。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from backend.common.auth import get_current_user
from backend.modules.flow.services import message_template_service as mts

logger = logging.getLogger(__name__)
router = APIRouter()


class MessageTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    template_kind: str = "im"
    format: str = "text"
    subject: str = ""
    content: str
    description: str = ""
    workspace_id: int = 0
    is_builtin: bool = False


class MessageTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    template_kind: Optional[str] = None
    format: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[int] = None


class MessageTemplatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_kind: str = "im"
    format: str = "text"
    subject: str = ""
    content: str = ""


@router.get("/variables")
def get_message_template_variables(user: dict = Depends(get_current_user)):
    """可用变量、分类与格式白名单（页面 chips 与表单选项的唯一来源）。"""
    return {
        "variables": mts.MESSAGE_TEMPLATE_VARIABLES,
        "kinds": [{"value": k, "label": label} for k, label in mts.TEMPLATE_KINDS.items()],
        "formats": {k: list(v) for k, v in mts.KIND_FORMATS.items()},
        "sms_segment_chars": mts.SMS_SEGMENT_CHARS,
        # 明确写给页面看：为什么没有任务名/结果摘要（护栏要求，不是漏配）
        "excluded_note": ("任务名、结果摘要、报表正文与数据行不在可用变量里："
                          "通知只报执行状态与需登录的报告链接（安全护栏）。"),
    }


@router.get("")
def list_message_templates(
    kind: str = Query("", description="im / email / sms，留空返回全部"),
    include_inactive: bool = Query(False, description="是否带出已下架模板"),
    user: dict = Depends(get_current_user),
):
    return mts.message_template_service.list_templates(kind=kind, include_inactive=include_inactive)


@router.post("/preview")
def preview_message_template(req: MessageTemplatePreviewRequest,
                             user: dict = Depends(get_current_user)):
    """按示例值预览文案（与真发送同一个 render）：预览什么样，发出去就什么样。"""
    return mts.message_template_service.preview(req.model_dump())


@router.post("")
def create_message_template(req: MessageTemplateCreate,
                            user: dict = Depends(get_current_user)):
    try:
        template_id = mts.message_template_service.create_template(req.model_dump(), user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": template_id}


@router.put("/{template_id}")
def update_message_template(template_id: int, req: MessageTemplateUpdate,
                            user: dict = Depends(get_current_user)):
    try:
        return mts.message_template_service.update_template(
            template_id, req.model_dump(exclude_unset=True), user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{template_id}")
def delete_message_template(template_id: int, user: dict = Depends(get_current_user)):
    """删除前查引用：被渠道引用的模板直接拒，并给出渠道清单（不留"删了通知才静默变默认文案"）。"""
    try:
        return mts.message_template_service.delete_template(template_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
