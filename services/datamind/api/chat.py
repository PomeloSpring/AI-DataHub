"""Chat/NL2SQL API — Send messages, manage conversations.

Delegates to existing backend chat logic for NL2SQL pipeline.
"""

import json
import logging
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from services.shared.common.auth import get_current_user, authorize_workspace
from services.shared.models.schemas import ChatRequest, UserInfo
from services.datamind.api.attachments import get_file_user

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
    report_theme: Optional[str] = ""  # 报告交付主题 id(前端已将"跟随"解析为当前 App 主题; 空=回落默认)


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

    from services.datamind.execution.session_workspace import preflight_request
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(preflight_request, req, user)
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
            report_theme=req.report_theme or "",
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

    authorize_workspace(user, workspace_id)
    resolved = waker_service.resolve_wakers(workspace_id, user.get("role") or "", user_id=user["user_id"],
                                            include_unavailable=True)
    return [
        {
            "id": w.get("id"),
            "waker_key": w.get("waker_key"),
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


# ── Conversation Management ──────────────────────────────────────

class FollowupsRequest(BaseModel):
    question: str = ""
    answer: str = ""
    model_id: Optional[int] = None


def _parse_followups(raw: str) -> list[str]:
    """从 LLM 文本中抽取 JSON 字符串数组(容忍 ```json 围栏与前后缀文字)。"""
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    return [str(x).strip() for x in arr if isinstance(x, str) and str(x).strip()]


@router.post("/followups")
def chat_followups(req: FollowupsRequest, user: UserInfo = Depends(get_current_user)):
    """基于本轮问答上文, 由 LLM 推断 2-4 条"继续探索"追问。

    失败/无内容返回空数组(不阻断主回答); 追问仅为自然语言问题, 不返回数据行。
    """
    from services.shared.common.llm.llm_client import generate_sql

    q = (req.question or "").strip()
    a = (req.answer or "").strip()
    if not q and not a:
        return {"followups": []}
    system_prompt = (
        "你是数据分析助手的追问推荐器。根据用户本轮的问题与助手回答, 推断用户接下来最可能想继续探索的 2-4 个具体问题。"
        "要求: 每个都是一句可直接发送的自然语言分析请求, 贴合上文的数据/指标/维度/结论, 不泛泛而谈, 不重复原问题, 不解释。"
        '仅返回 JSON 字符串数组, 例如 ["按渠道拆分看各渠道占比变化", "定位环比下降最多的细分并分析原因"]。'
    )
    user_content = f"用户问题：{q}\n\n助手回答：{a[:2000]}"
    try:
        resp = generate_sql(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            max_tokens=512, model_id=req.model_id,
        )
        return {"followups": _parse_followups(resp.get("sql", ""))[:4]}
    except Exception as e:  # noqa: BLE001
        logger.warning("生成追问失败: %s", e)
        return {"followups": []}


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

    rel = (path or "").strip()
    for prefix in ("/workspace/", "/workspace", "workspace/", "./", "/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    rel = rel.lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="缺少文件路径")

    target = (ws_dir / rel).resolve()
    # 防目录穿越: 目标必须仍在会话 workspace 目录内
    if target != ws_dir and ws_dir not in target.parents:
        raise HTTPException(status_code=403, detail="非法文件路径")
    # 逐级拒绝符号链接逃逸(与会话目录守卫一致)
    for p in [target, *target.parents]:
        if p == ws_dir:
            break
        if p.is_symlink():
            raise HTTPException(status_code=403, detail="非法文件路径")
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
    waker_key: str = Query("", description="按归属 Waker 过滤;传 __system_bot__ 只看 AS-BOT 会话"),
    user: UserInfo = Depends(get_current_user),
):
    """List user's conversations, optionally filtered by workspace and waker.

    AS-BOT 面板传 waker_key=__system_bot__ 只看系统助手会话; 未传 waker_key 的业务清单
    默认排除 __system_bot__, 使两套会话历史互不串台。
    """
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cols = "id, title, datasource_id, workspace_id, waker_key, created_at, updated_at"
            where = ["user_id = %s"]
            params: list = [user["user_id"]]
            if workspace_id:
                where.append("workspace_id = %s")
                params.append(workspace_id)
            if waker_key:
                where.append("waker_key = %s")
                params.append(waker_key)
            else:
                where.append("waker_key <> '__system_bot__'")
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
                "SELECT id, title, datasource_id, workspace_id, waker_key, messages, "
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
    waker_key: Optional[str] = ""


@router.post("/conversations")
def create_conversation(
    req: CreateConversationRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new conversation.

    workspace_id=0 表示全局/智能助手会话，按 user_id 隔离，不要求工作空间授权。
    """
    # workspace_id=0 为全局助手会话，仅按 user_id 隔离，无需工作空间授权
    if req.workspace_id:
        authorize_workspace(user, req.workspace_id)
    # workspace_id=0 时不验证 waker_key（全局助手继承角色默认 Waker）
    if req.waker_key and req.workspace_id:
        from services.datamind.execution.tool_policy import resolve_policy
        from services.datamind.execution.models import ExecutionContext
        ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role", ""),
                               workspace_id=req.workspace_id or 0, extra={"waker_key": req.waker_key})
        resolve_policy(ctx)
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_conversations (user_id, title, datasource_id, workspace_id, waker_key, messages, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
                (user["user_id"], "新对话", req.datasource_id or 0, req.workspace_id or 0,
                 req.waker_key or "", "[]"),
            )
            conn.commit()
            conv_id = cur.lastrowid
            return {
                "id": conv_id,
                "title": "新对话",
                "datasource_id": req.datasource_id or 0,
                "workspace_id": req.workspace_id or 0,
                "waker_key": req.waker_key or "",
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
