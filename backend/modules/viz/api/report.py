"""Report Generation API — 报表生成与报表中心管理接口.

正文不在响应体里：``adh_reports`` 只存对象 key，详情接口回「域名 + 签名」的短期直链，
由浏览器直连对象存储取正文。本模块的 ``/{report_id}/content`` 只服务**本地回退模式**
（未配置 OBJECT_STORAGE_* 时无签名可拼），对象存储模式下显式拒绝而不是静默代理。
"""

import json
import logging
from fastapi import APIRouter, HTTPException, Query, Depends, Request, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from backend.common.auth import get_current_user, authorize_workspace
from backend.modules.viz.services import report_service
from backend.modules.viz.services import report_access
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from backend.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
def list_reports(
    workspace_id: int = Query(0, description="0=全部（报表跟随属主）；>0 收窄到该工作空间"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """获取报表清单（仅当前用户自己创建的，可按工作空间收窄）.

    报表中心不再按工作空间筛选（创建工作空间暂未开放，一个账号一个默认空间，
    而报表本来就跟随属主跨空间）；``workspace_id`` 保留给任务调度侧的
    ``GET /api/scheduled/reports`` 收窄使用。
    """
    return report_service.list_reports(user["user_id"], workspace_id, page, page_size, user=user)


ReportGenerateRequest = report_service.ReportSubmission


class ReportCreateRequest(BaseModel):
    """把会话里一轮分析结果存为报表（``source_type`` 只接受 conversation）。

    不接受客户端传正文：内容只从服务端已落库的会话读（身份与内容都不信 body）。

    手写空白报表的入口已下线（用户拍板）：报表中心的定位是"分析产物的稳定版本"，
    手建一张没有取数来源、没有证据链的空白报表，既不能「按最新数据重新生成」也不能建定时任务，
    在清单里与真产物混在一起只会造成歧义。存量手写报表不删不改，仍可编辑/导出/删除。
    ``template_key`` 随入口一起退役（模板的消费方 = 定时任务表单与报表重跑沿用版式）。
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
    template_key: str = ""


class ReportSaveAsTemplateRequest(BaseModel):
    """把本报表的版式沉淀成报告模板（名称留空时服务端按报表标题命名）。"""
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    description: str = ""


class ReportTemplatePayload(BaseModel):
    """报告模板的版式契约载荷：``layout`` 是权威结构，``content`` 缺省时由版式生成骨架。"""
    model_config = ConfigDict(extra="forbid")
    name: str
    format: str = "markdown"
    theme: str = ""
    layout: Optional[dict] = None
    content: str = ""
    description: str = ""
    is_builtin: bool = False


class ReportTemplateAnalyzeRequest(BaseModel):
    """配置页「从正文抽版式」：抽取口径全平台一处（报表重跑/任务契约/预览共用）。

    手工编过版式时传 ``layout``（以它为准），只给了正文则从正文抽；前端不自己解析。
    """
    model_config = ConfigDict(extra="forbid")
    content: str = ""
    format: str = "markdown"
    layout: Optional[dict] = None
    # 预览主题的令牌口径：HTML 骨架在隔离 iframe 里渲染，主文档 CSS 变量进不去，
    # 不传就只会看到浏览器默认样式（用户实拍的"预览没样式"）。传查看者当前主题即可。
    theme: str = ""


class ReportUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    content: Optional[str] = None
    format: Optional[str] = None


class ReportTaskFromReportRequest(BaseModel):
    """从报表快捷创建同款 Agent 定时任务。

    问题正文预填报表登记的可复跑问题，**允许属主就地改一句**（口语化/归位不准时）：
    它是用户自己写的分析诉求，不是身份与资源标识——护栏 §2 禁的是请求体传
    user_id/datasource_id 那类内部事实，任务调度页新建任务本来就让人写问题正文。
    不传则沿用登记值；传了且不同则写回报表 origin（重新生成与建任务同一句）。
    任务**不选 AS-BOT 也不选数据源**，能力与可查范围每次执行按任务属主（=报表属主）
    的角色实时继承，授权统一由 scheduled_task_service.create_task 裁决。
    ``report_template_key`` 选填：每次执行按该模板版式出报告（建任务时就校模板存在，不留到运行时才失败）。
    """
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    cron_expression: str = ""
    question: str = ""
    notify_on_success: bool = True
    notify_on_failure: bool = True
    report_template_key: str = ""


@router.post("", status_code=201)
def create_report(req: ReportCreateRequest, user: dict = Depends(get_current_user)):
    """把会话里一轮分析存为报表（内容型，仅本人可见可编辑）。"""
    if req.source_type == "conversation":
        try:
            return report_service.save_conversation_report(
                user=user, conversation_id=req.conversation_id, title=req.title,
                message_index=req.message_index, message_uuid=req.message_uuid)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=(
        "报告中心不再手建空白报告：报告是分析产物，请在智能问数里把分析结果「存为报告」，"
        "或由定时任务产出。存量手写报告不受影响，仍可重命名、编辑正文、导出与删除"))


@router.post("/{report_id}/save-as-template", status_code=201)
def save_report_as_template(report_id: int, req: ReportSaveAsTemplateRequest,
                            user: dict = Depends(get_current_user)):
    """把本报表的版式沉淀成报告模板（报表中心=生产者，配置页/任务表单=消费者）。

    只抽结构（章节 + 图表规格 + 主题），不带正文里的数字；无可沉淀结构时 422 回可解释原因。
    """
    try:
        return report_service.save_report_as_template(
            report_id, user, name=req.name, description=req.description)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/templates/analyze")
def analyze_report_template(req: ReportTemplateAnalyzeRequest,
                           user: dict = Depends(get_current_user)):
    """从模板正文抽版式骨架（配置页用）：前端不自己解析，抽取口径与生成/校验同一函数。"""
    return report_service.analyze_report_layout(req.content, req.format, req.layout,
                                                theme=req.theme)


@router.get("/templates/{template_key}/skeleton")
def get_report_template_skeleton(template_key: str, theme: str = "",
                                 user: dict = Depends(get_current_user)):
    """按模板取报表骨架（报表中心「套用模板」起稿）；模板不可用回 422 + 原因，不静默给空白。

    ``theme``：查看者当前主题（模板锁了主题时以模板为准）——新建的报表第一版就该带样式。
    """
    out = report_service.template_skeleton_by_key(template_key, theme=theme)
    if not out.get("found"):
        raise HTTPException(status_code=422, detail=out.get("reason") or "报告模板不可用")
    return out


# ── 报告模板（模板中心）：读写收口在报表域，权限点 report-template:* 同源 ──────────
# 存储仍是字模库 ``report_template``（版式契约的唯一事实源），这里只是把 HTTP 面从
# /api/vis-library 挪到本域：菜单勾「报告模板」才打得开这个页面，取消才关得掉。


@router.get("/templates")
def list_report_templates(include_inactive: bool = Query(False),
                          user: dict = Depends(get_current_user)):
    """报告模板清单（含版式摘要与被任务引用数）——配置页表格与任务/报表下拉同一来源。"""
    return report_service.list_report_templates(include_inactive=include_inactive)


@router.get("/templates/theme-options")
def list_report_theme_options(user: dict = Depends(get_current_user)):
    """主题包清单（配置页主题下拉用），免得页面为拿主题再去读字模库。"""
    return report_service.report_theme_options()


@router.post("/templates", status_code=201)
def create_report_template(req: ReportTemplatePayload,
                           user: dict = Depends(get_current_user)):
    """新建报告模板：只存版式契约（章节 + 图表规格 + 主题 + 骨架），一个数值都不存。"""
    try:
        return report_service.save_report_template(req.model_dump(), user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/templates/{template_id}")
def update_report_template(template_id: int, req: ReportTemplatePayload,
                           user: dict = Depends(get_current_user)):
    try:
        return report_service.save_report_template(req.model_dump(), user,
                                                   template_id=template_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        code = 403 if "管理员" in detail else 422
        raise HTTPException(status_code=code, detail=detail) from exc


@router.delete("/templates/{template_id}")
def delete_report_template(template_id: int, user: dict = Depends(get_current_user)):
    """被定时任务引用的模板拒绝删除，并回引用数量（不留"删掉后任务跑到运行时才失败"）。"""
    try:
        return report_service.delete_report_template(template_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(status_code=403 if "管理员" in detail else 422,
                            detail=detail) from exc


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
        raise HTTPException(status_code=404, detail="报告不存在或无权访问")
    return report


@router.get("/{report_id}/content")
def get_report_content(report_id: int, user: dict = Depends(get_current_user)):
    """本地回退模式下的正文同源代理（对象存储模式请直连详情返回的 content_url）。

    三条约束：
    1. **访问判据与详情完全同一**（``report_access.can_access_report``），不开第二条通道；
    2. 已配对象存储时显式 400 —— 让「直链」与「代理」并存会漂出两套寻址口径，
       而本地模式根本没有直链可给，所以这里只在那种模式下工作；
    3. 一律按 ``text/plain`` + ``attachment`` 下发：报表正文可以是 HTML，
       以本站源 inline 渲染等于把存储型 XSS 挂在自己域名下（与内容附件同口径）。
       前端 ``fetch().text()`` 不受 disposition 影响，拿到文本后自行净化再渲染。
    """
    row = report_service.load_report(report_id)
    if not row or not report_access.can_access_report(row, user):
        raise HTTPException(status_code=404, detail="报告不存在或无权访问")
    # 存储访问器只有报表域这一个入口（与写侧同源），端点不自己 new 客户端
    storage = report_service.content_storage()
    if storage.is_object_storage:
        raise HTTPException(status_code=400,
                            detail="正文存于对象存储，请使用报告详情返回的 content_url 直连读取")
    try:
        data = report_service.read_report_content_bytes(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # 行在、对象没了：说清是"正文不可用"，不要伪装成 404 报表不存在
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    return Response(content=data, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="report-{report_id}.txt"'})


@router.post("/{report_id}/refresh-stream")
async def refresh_report_stream(report_id: int, request: Request,
                                user: dict = Depends(get_current_user)):
    """按报表登记的原始问题重新取数：全过程在同一条 SSE 连接里跑完，**离开页面即终止**。

    旧写法是 ``POST /refresh`` 派发 Celery 后台任务，页面只剩一句“生成中”、进度看不到、
    关掉页面它照跑（用户实拍）。现在改成人眼看得到的一步步：取了几条数、走到哪个工具、
    正文在不在写、最后这一版是 ready 还是证据不全 / 没沿用原版式。

    建档与校验（归属、是否有可复跑问题、是不是任务产出的报表、租约能不能抢到）
    先在**线程池**里做完 —— 能用 4xx 说清的不留到流里；之后才升级成流。
    任务产出的报表不在此重跑（前端改走触发所属任务），避免同一报表两个重跑口径。
    """
    try:
        plan = await run_in_threadpool(report_service.prepare_report_refresh, report_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StreamingResponse(report_service.stream_report_refresh(plan, request.is_disconnected),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{report_id}/create-task", status_code=201)
def create_task_from_report(report_id: int, req: ReportTaskFromReportRequest,
                            user: dict = Depends(get_current_user)):
    """从已有报表快捷创建同款 Agent 定时任务（需 scheduled:manage）。"""
    try:
        return report_service.create_task_from_report(report_id, user, **req.model_dump())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
