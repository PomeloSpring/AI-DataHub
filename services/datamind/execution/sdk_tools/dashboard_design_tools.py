"""任务限定的仪表盘设计工具；不发布、不接受或返回物理 SQL。"""
import asyncio
from typing import Annotated, Optional

from services.datamind.execution.sdk_tools.catalog_tools import _text
from services.datamind.execution.sdk_tools.context import get_execution_context
from services.dataviz.services import dashboard_design_service as designs


def design_actor():
    ctx = get_execution_context()
    if not ctx or ctx.extra.get("waker_key") != designs.SYSTEM_BOT or not ctx.extra.get("secure_runtime"):
        raise designs.DesignError("仪表盘设计仅可通过可信 AS-BOT 会话使用", "forbidden", 403)
    cid = int(ctx.extra.get("conversation_id") or 0)
    if not cid:
        raise designs.DesignError("请先建立系统助手会话")
    return {"user_id": ctx.user_id}, cid


async def request_dashboard_design(args):
    user, cid = design_actor()
    designs.reject_private(args)
    result = await asyncio.to_thread(designs.request_design, user, cid,
                                     args.get("request", ""), args.get("name", ""), args.get("operation", "append"))
    return _text(result)


async def get_dashboard_design(args):
    user, cid = design_actor()
    return _text(await asyncio.to_thread(designs.get_design, args["design_id"], user, cid))


async def get_business_semantics(args):
    user, cid = design_actor()
    result = await asyncio.to_thread(designs.semantics, args["design_id"], user, args.get("keyword") or "", cid)
    result["design"] = await asyncio.to_thread(designs.get_design, args["design_id"], user, cid)
    return _text(result)


async def search_business_knowledge(args):
    user, cid = design_actor()
    return _text(await asyncio.to_thread(designs.business_knowledge, args["design_id"], user, args["questions"], cid))


async def prepare_dashboard_design(args):
    user, cid = design_actor()
    designs.reject_private(args)
    result = await asyncio.to_thread(designs.prepare, args["design_id"], user, args["expected_version"],
                                     args.get("widgets") or [], args.get("steps") or [], args.get("questions") or [], cid)
    return _text(result)


DESIGN_ID = Annotated[str, "当前设计 ID，必须来自本会话的设计卡片"]
SCREEN_SPECS = [
    {"name": "request_dashboard_design", "handler": request_dashboard_design,
     "description": "仅在用户要求新建/追加/修改仪表盘时提出设计草稿。先让用户在面板确认目标和业务范围，再查业务知识；不直接创建正式图表。系统用量/健康问题不要调用。",
     "schema": {"request": Annotated[str, "用户的业务设计诉求"],
                "operation": Annotated[str, "create=新建，append=追加，update=修改单图"],
                "name": Annotated[Optional[str], "新仪表盘名称或用户提及的目标名称"]}},
    {"name": "get_dashboard_design", "handler": get_dashboard_design,
     "description": "读取本会话设计状态、用户已确认的范围和口径、当前版本；不返回 SQL。用户完成选择后先读这里，不重建设计。",
     "schema": {"design_id": DESIGN_ID}},
    {"name": "prepare_dashboard_design", "handler": prepare_dashboard_design,
     "description": "按用户确认的选择准备图表语义意图和设计步骤。存疑时用 questions 提出选项并等待回答，可传空 widgets。方案准备完成后提示用户打开面板查看 SQL、真实预览并确认发布。禁止 SQL 字段。",
     "schema": {"design_id": DESIGN_ID, "expected_version": Annotated[int, "get_dashboard_design 返回的当前版本"],
                "widgets": Annotated[list, "图表数组：{title,chart_type,query:{object,metrics,dimensions,time_window,time_column,time_grain},config,position:{x,y,w,h}}"],
                "steps": Annotated[list, "可见业务设计步骤：目标、来源、口径、时间、坐标、布局及影响"],
                "questions": Annotated[Optional[list], "存疑选项 [{key,label,options:[业务选项1,业务选项2]}]，不能擅自挑选"]}},
]
SEMANTIC_SPECS = [
    {"name": "get_business_semantics", "handler": get_business_semantics,
     "description": "仅用于用户已确认业务范围的仪表盘设计，查询生效业务本体和实时指标/维度目录。不可用于回答系统运营问题；不改变系统工具的默认域。",
     "schema": {"design_id": DESIGN_ID, "keyword": Annotated[Optional[str], "业务对象/指标/维度关键词"]}},
    {"name": "search_business_knowledge", "handler": search_business_knowledge,
     "description": "按需理解仪表盘场景的业务背景、指标口径和模板参数。只检索用户在此设计中确认的业务知识库；无范围或来源不明时明确拒绝。不回答系统用量/健康问题，无自动回退。",
     "schema": {"design_id": DESIGN_ID, "questions": Annotated[list, "1–8 个业务问题"]}},
]
