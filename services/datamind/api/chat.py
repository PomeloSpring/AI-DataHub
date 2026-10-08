"""Chat API — Send messages, manage conversations.

消息派发 qoder 执行层(agent 模式);quick 单发管道已退役。
"""

import json
import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from services.shared.common.auth import (
    get_current_user, authorize_workspace, get_file_user, resolve_user_default_workspace_id,
)
from services.shared.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request/Response Models ──────────────────────────────────────────

class SendMessageRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    pipeline_mode: Optional[str] = "agent"  # agent(quick 管道已退役)
    retrieval_strategy: Optional[str] = None
    workspace_id: Optional[int] = 0
    attachments: list[dict] = []  # 服务端解析回填的上传附件(send_payload);JSON 请求不得携带
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)
    conversation_id: Optional[int] = 0  # chat 会话 ID(qoder 长对话池 key)
    as_bot_key: Optional[str] = ""  # 聊天端选定的 AS-BOT
    report_theme: Optional[str] = ""  # 报告交付主题 id(前端已将"跟随"解析为当前 App 主题; 空=回落默认)


# ── Chat Send (Streaming) ────────────────────────────────────────────

@router.post("/send/stream")
async def chat_send_stream(
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Send a message with SSE streaming response.

    消息派发 qoder 执行层(agent 模式)。
    请求体双模: JSON(无附件)或 multipart(payload + files,附件随消息上传落盘会话工作区)。
    """
    from services.datamind.api.send_payload import parse_send_request
    from services.datamind.services.chat_service import ChatService

    from services.datamind.execution.session_workspace import preflight_request
    from starlette.concurrency import run_in_threadpool
    req = await parse_send_request(request, SendMessageRequest)
    await run_in_threadpool(preflight_request, req, user)
    service = ChatService()
    return StreamingResponse(
        service.stream_query(
            question=req.question,
            history=req.history or [],
            datasource_id=req.datasource_id or 0,
            model_id=req.model_id,
            pipeline_mode=req.pipeline_mode or "agent",
            retrieval_strategy=req.retrieval_strategy,
            workspace_id=req.workspace_id or 0,
            user_id=user["user_id"],
            username=user["username"],
            request=request,
            attachments=req.attachments or [],
            model_ref=req.model_ref or "",
            session_id=req.session_id or "",
            conversation_id=req.conversation_id or 0,
            user_role=user.get("role") or "",
            as_bot_key=req.as_bot_key or "",
            report_theme=req.report_theme or "",
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Chat 可用 AS-BOT 清单 ──────────────────────────────────

@router.get("/as-bots")
def list_chat_as_bots(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """返回当前 (工作空间 + 用户角色) 可选的 AS-BOT 清单(含各自的可用模型).

    与运行时 resolve_as_bots 解析口径一致(工作空间绑定回退全局 + 角色白名单),
    确保聊天端选择器展示的候选与后端实际允许的集合对齐。
    """
    from services.datamind.execution import as_bots as as_bot_service

    authorize_workspace(user, workspace_id)
    resolved = as_bot_service.resolve_as_bots(workspace_id, user.get("role") or "", user_id=user["user_id"],
                                              include_unavailable=True)
    return [
        {
            "id": w.get("id"),
            "as_bot_key": w.get("as_bot_key"),
            "name": w.get("name"),
            "display_name": w.get("display_name") or w.get("name"),
            "description": w.get("description") or "",
            "models": w.get("models") or [],
            "is_default": bool(w.get("is_default")),
            "available": w.get("available", True),
            "unavailable_reason": w.get("unavailable_reason", ""),
        }
        for w in resolved
    ]


# ── Chat Feedback ────────────────────────────────────────

class FeedbackRequest(BaseModel):
    question: str = ""
    tables_used: str = ""
    datasource_id: int = 0
    satisfied: bool
    expected_table: str = ""
    reason: str = ""
    trace_id: str = ""
    message_uuid: str = ""
    conversation_id: int = 0
    workspace_id: int = 0


@router.post("/feedback")
def save_message_feedback(req: FeedbackRequest, user: UserInfo = Depends(get_current_user)):
    """消息赞踩反馈 → adh_message_feedback。

    按 message_uuid 幂等 upsert(唯一键 uk_msg);携带 trace 关联键时
    可观测页(Trace 列表/满意率 KPI)能直接联表命中该回合。
    无 message_uuid 的旧消息前端不传,此处兼容生成,仅计入满意度不关联 trace。
    """
    import uuid as _uuid

    from services.shared.common.db import execute_write

    message_uuid = req.message_uuid or _uuid.uuid4().hex
    tags = {"tables_used": req.tables_used, "datasource_id": req.datasource_id} if req.tables_used else None
    execute_write(
        """INSERT INTO adh_message_feedback
           (trace_id, conversation_id, message_uuid, user_id, workspace_id,
            satisfied, expected_table, reason, tags)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE
             satisfied=VALUES(satisfied), expected_table=VALUES(expected_table),
             reason=VALUES(reason), tags=VALUES(tags),
             trace_id=IF(VALUES(trace_id)='', trace_id, VALUES(trace_id))""",
        (
            req.trace_id or "", req.conversation_id or 0, message_uuid,
            user["user_id"], req.workspace_id or 0,
            1 if req.satisfied else 0, req.expected_table or "", req.reason or "",
            json.dumps(tags, ensure_ascii=False) if tags else None,
        ),
    )
    return {"ok": True, "message_uuid": message_uuid}


# ── Conversation Management ──────────────────────────────────────




@router.get("/session-file")
def get_session_file(
    conversation_id: int = Query(..., description="会话 ID"),
    path: str = Query(..., description="工作区相对路径(可带 /workspace/ 前缀)"),
    download: int = Query(0, description="1=作为附件下载, 0=内联预览"),
    user: UserInfo = Depends(get_file_user),
):
    """下载/预览 Agent 写入本会话工作区的产物文件(单机, 仅属主, 防目录穿越).

    多实例下文件可能在其它 storage_node, 本期不做跨节点路由(后续用对象存储导出解决)。
    """
    from services.shared.common.db.metadata_db import get_metadata_conn
    from services.datamind.execution import session_workspace as sw

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_key, workspace_id FROM adh_agent_sessions "
                "WHERE conversation_id=%s AND user_id=%s",
                (conversation_id, user["user_id"]),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row.get("session_key"):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    base = sw.workspace_base(create=False)
    root = sw.session_paths(base, row["session_key"], int(row["workspace_id"] or 0), create=False)
    ws_dir = (root / "workspace").resolve()

    from services.datamind.multimodal.loader import normalize_rel_path, resolve_workspace_file

    if not normalize_rel_path(path):
        raise HTTPException(status_code=400, detail="缺少文件路径")
    try:
        # 防目录穿越/符号链接逃逸(与附件引用共用同一守卫)
        target = resolve_workspace_file(ws_dir, path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e) or "非法文件路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    quoted = quote(target.name)
    headers = {"Content-Disposition": f"{disposition}; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"}
    return FileResponse(str(target), media_type=media_type, headers=headers)


@router.get("/conversations")
def list_conversations(
    workspace_id: int = Query(0, description="Filter by workspace"),
    as_bot_key: str = Query("", description="按归属 AS-BOT 过滤"),
    user: UserInfo = Depends(get_current_user),
):
    """List user's conversations, optionally filtered by workspace and AS-BOT.

    会话历史合并共用（AS-BOT 面板与智能问数同一载体、不同入口），不再按入口区分。
    """
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cols = "id, title, datasource_id, workspace_id, as_bot_key, created_at, updated_at"
            where = ["user_id = %s"]
            params: list = [user["user_id"]]
            if workspace_id:
                # 合并共用：工作空间列表含遗留 ws=0 会话（统一改造前的全局归属，仅兼容保留），
                # 反向（不带空间过滤）同样互见，不按入口区分。
                where.append("workspace_id IN (%s, 0)")
                params.append(workspace_id)
            if as_bot_key:
                where.append("as_bot_key = %s")
                params.append(as_bot_key)
            cur.execute(
                f"SELECT {cols} FROM adh_conversations "
                f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT 50",
                tuple(params),
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return rows
    finally:
        conn.close()


@router.get("/conversations/{conv_id}")
def get_conversation(
    conv_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """Get conversation with messages."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, datasource_id, workspace_id, as_bot_key, messages, "
                "created_at, updated_at "
                "FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user["user_id"]),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Conversation not found")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            messages = json.loads(row["messages"]) if row["messages"] else []
            row["messages"] = messages
            return row
    finally:
        conn.close()


class CreateConversationRequest(BaseModel):
    datasource_id: Optional[int] = 0
    workspace_id: Optional[int] = 0
    as_bot_key: Optional[str] = ""


@router.post("/conversations")
def create_conversation(
    req: CreateConversationRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new conversation.

    workspace_id 未指定(0/缺省)时归属用户默认工作空间(个人工作站, 随用户自动创建)。
    AS-BOT 与 Waker 统一后不再有全局/系统域会话(历史 ws=0 会话仅兼容保留)。
    """
    # 未指定工作空间 → 解析用户默认工作空间(唯一口径, 见 resolve_user_default_workspace_id)
    workspace_id = req.workspace_id or 0
    if not workspace_id:
        workspace_id = resolve_user_default_workspace_id(user["user_id"])
        if not workspace_id:
            raise HTTPException(status_code=422, detail="未找到您的默认工作空间，请先创建工作空间后再试")
    authorize_workspace(user, workspace_id)
    # AS-BOT 与角色强绑定(一对一)：会话归属直接取当前用户角色的 AS-BOT，
    # 不再依赖页面传标识；无授权 AS-BOT 则 fail-closed。
    as_bot_key = ""
    from services.datamind.execution.models import ExecutionContext
    from services.datamind.execution.tool_policy import resolve_policy
    ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role", ""),
                           workspace_id=workspace_id, extra={"as_bot_key": req.as_bot_key or ""})
    try:
        as_bot_key = resolve_policy(ctx).as_bot["as_bot_key"]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations (user_id, title, datasource_id, workspace_id, as_bot_key, messages, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
                (user["user_id"], "新对话", req.datasource_id or 0, workspace_id,
                 as_bot_key, "[]"),
            )
            conn.commit()
            conv_id = cur.lastrowid
            return {
                "id": conv_id,
                "title": "新对话",
                "datasource_id": req.datasource_id or 0,
                "workspace_id": workspace_id,
                "as_bot_key": as_bot_key,
                "created_at": datetime.now().isoformat(),
            }
    finally:
        conn.close()


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    messages: Optional[list] = None
    executor_session_id: Optional[str] = None


@router.put("/conversations/{conv_id}")
def update_conversation(
    conv_id: int,
    req: UpdateConversationRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Update conversation title and/or messages."""
    if req.executor_session_id is not None:
        raise HTTPException(422, "SDK 会话标识只能由服务端维护")
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Verify ownership
            cur.execute(
                "SELECT id FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user["user_id"]),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Conversation not found")

            if req.messages == []:
                # 清空 = 清理历史消息并重置模型上下文，不关闭会话：置空 sdk_session_id 后
                # 同一会话下一轮以零上下文继续；旧数据里被误判关闭的 closed 行一并重新开放。
                cur.execute("UPDATE adh_agent_sessions SET status='idle', sdk_session_id=NULL, execution_token=NULL, "
                            "updated_at=UTC_TIMESTAMP(6) "
                            "WHERE conversation_id=%s AND user_id=%s AND status IN ('idle','interrupted','closed')",
                            (conv_id, user["user_id"]))
                cur.execute("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (conv_id,))
                session = cur.fetchone()
                if session and session["status"] == "running":
                    raise HTTPException(409, "会话尚未停止，暂不能清空记录")
                if session and session["status"] == "deleting":
                    raise HTTPException(409, "会话正在删除中，暂不能清空")
            # Build dynamic update
            updates = ["updated_at = NOW()"]
            params = []
            if req.title is not None:
                updates.append("title = %s")
                params.append(req.title)
            if req.messages is not None:
                updates.append("messages = %s")
                params.append(json.dumps(req.messages, ensure_ascii=False))

            params.append(conv_id)
            cur.execute(
                f"UPDATE adh_conversations SET {', '.join(updates)} WHERE id = %s",
                params,
            )
            conn.commit()
            return {"success": True}
    finally:
        conn.close()


# ── MCP Tools ─────────────────────────────────────────────────────

@router.get("/mcp-tools")
def list_mcp_tools(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """List available MCP servers and their tools for the given workspace."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Get workspace MCP servers
            cur.execute(
                "SELECT id, name, url, discovered_tools, tools_config, is_active "
                "FROM adh_mcp_servers WHERE is_active = 1 AND workspace_id = %s",
                (workspace_id,),
            )
            servers = []
            for row in cur.fetchall():
                # Parse discovered tools
                all_tools = []
                discovered = row.get("discovered_tools", "")
                if isinstance(discovered, str) and discovered:
                    try:
                        parsed = json.loads(discovered)
                        if isinstance(parsed, list):
                            all_tools = parsed
                    except json.JSONDecodeError:
                        pass

                # Apply whitelist filter
                tools_config = row.get("tools_config", "")
                whitelist = None
                if isinstance(tools_config, str) and tools_config:
                    try:
                        parsed = json.loads(tools_config)
                        if isinstance(parsed, list) and parsed:
                            whitelist = {t.get("name") for t in parsed if isinstance(t, dict)}
                    except json.JSONDecodeError:
                        pass

                if whitelist:
                    filtered = [t for t in all_tools if t.get("name") in whitelist]
                else:
                    filtered = all_tools

                servers.append({
                    "id": row["id"],
                    "server_name": row["name"],
                    "server_url": row["url"],
                    "tools": filtered,
                })

            return {"servers": servers}
    finally:
        conn.close()


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: int,
    user: UserInfo = Depends(get_current_user),
):
    """删除会话及其独立工作区；只有目录清理成功才移除数据库记录。"""
    from services.datamind.execution.session_workspace import delete_conversation_workspace

    try:
        return delete_conversation_workspace(conv_id, user["user_id"])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("会话及工作区删除未完成: conversation_id=%s", conv_id)
        raise HTTPException(
            status_code=503,
            detail="会话工作区清理未完成，记录仍保留；可能已清理部分文件，请重试删除或联系管理员",
        ) from exc
