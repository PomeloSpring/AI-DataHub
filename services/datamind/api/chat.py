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
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Chat Send (Non-Streaming) ────────────────────────────────────────

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
                "SELECT id, title, datasource_id, workspace_id, messages, created_at, updated_at "
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
