"""Chat/NL2SQL API — Send messages, manage conversations.

Delegates to existing backend chat logic for NL2SQL pipeline.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.shared.common.auth import get_current_user
from services.shared.models.schemas import ChatRequest, UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request/Response Models ──────────────────────────────────────────

class SendMessageRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    pipeline_mode: Optional[str] = "quick"
    retrieval_strategy: Optional[str] = None
    workspace_id: Optional[int] = 0
    attachments: Optional[list[str]] = []  # 多模态附件 ID 列表
    model_ref: Optional[str] = ""  # 执行层运行时模型(如 provider/model_name)
    session_id: Optional[str] = ""  # 执行层会话 ID(SDK 多轮对话 resume)
    conversation_id: Optional[int] = 0  # chat 会话 ID(qoder 长对话池 key)
    waker_key: Optional[str] = ""  # 聊天端选定的 Waker


# ── Chat Send (Streaming) ────────────────────────────────────────────

@router.post("/send/stream")
async def chat_send_stream(
    req: SendMessageRequest,
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Send a message with SSE streaming response.

    Proxies to the existing backend pipeline orchestrator for NL2SQL.
    """
    from services.datamind.services.chat_service import ChatService

    service = ChatService()
    return StreamingResponse(
        service.stream_query(
            question=req.question,
            history=req.history or [],
            datasource_id=req.datasource_id or 0,
            model_id=req.model_id,
            pipeline_mode=req.pipeline_mode or "quick",
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
            waker_key=req.waker_key or "",
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Chat 可用 Waker 清单 ──────────────────────────────────

@router.get("/wakers")
def list_chat_wakers(
    workspace_id: int = Query(0, description="Workspace ID"),
    user: UserInfo = Depends(get_current_user),
):
    """返回当前 (工作空间 + 用户角色) 可选的 Waker 清单(含各自的可用模型).

    与运行时 resolve_wakers 解析口径一致(工作空间绑定回退全局 + 角色白名单),
    确保聊天端选择器展示的候选与后端实际允许的集合对齐。
    """
    from services.datamind.execution import wakers as waker_service

    resolved = waker_service.resolve_wakers(workspace_id, user.get("role") or "")
    return [
        {
            "id": w.get("id"),
            "waker_key": w.get("waker_key"),
            "name": w.get("name"),
            "display_name": w.get("display_name") or w.get("name"),
            "description": w.get("description") or "",
            "models": w.get("models") or [],
            "is_default": bool(w.get("is_default")),
        }
        for w in resolved
    ]


# ── Chat Send (Non-Streaming) ────────────────────────────────────────

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


@router.post("/send")
async def chat_send(
    req: SendMessageRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Send a message and get a non-streaming response.

    Runs the full NL2SQL pipeline and returns the final result.
    """
    from services.datamind.services.chat_service import ChatService

    service = ChatService()
    result = await service.query(
        question=req.question,
        history=req.history or [],
        datasource_id=req.datasource_id or 0,
        model_id=req.model_id,
        pipeline_mode=req.pipeline_mode or "quick",
        retrieval_strategy=req.retrieval_strategy,
        workspace_id=req.workspace_id or 0,
        user_id=user["user_id"],
        username=user["username"],
        attachments=req.attachments or [],
    )
    return result


# ── Conversation Management ──────────────────────────────────────────

@router.get("/conversations")
def list_conversations(
    workspace_id: int = Query(0, description="Filter by workspace"),
    user: UserInfo = Depends(get_current_user),
):
    """List user's conversations, optionally filtered by workspace."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id:
                cur.execute(
                    "SELECT id, title, datasource_id, workspace_id, created_at, updated_at "
                    "FROM adh_conversations "
                    "WHERE user_id = %s AND workspace_id = %s ORDER BY updated_at DESC LIMIT 50",
                    (user["user_id"], workspace_id),
                )
            else:
                cur.execute(
                    "SELECT id, title, datasource_id, workspace_id, created_at, updated_at "
                    "FROM adh_conversations "
                    "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
                    (user["user_id"],),
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
                "SELECT id, title, datasource_id, workspace_id, messages, executor_session_id, "
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


@router.post("/conversations")
def create_conversation(
    req: CreateConversationRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new conversation."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations (user_id, title, datasource_id, workspace_id, messages, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, NOW(), NOW())",
                (user["user_id"], "新对话", req.datasource_id or 0, req.workspace_id or 0, "[]"),
            )
            conn.commit()
            conv_id = cur.lastrowid
            return {
                "id": conv_id,
                "title": "新对话",
                "datasource_id": req.datasource_id or 0,
                "workspace_id": req.workspace_id or 0,
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

            # Build dynamic update
            updates = ["updated_at = NOW()"]
            params = []
            if req.title is not None:
                updates.append("title = %s")
                params.append(req.title)
            if req.messages is not None:
                updates.append("messages = %s")
                params.append(json.dumps(req.messages, ensure_ascii=False))
            if req.executor_session_id is not None:
                updates.append("executor_session_id = %s")
                params.append(req.executor_session_id)

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
    """Delete a conversation."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM adh_conversations WHERE id = %s AND user_id = %s",
                (conv_id, user["user_id"]),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()
