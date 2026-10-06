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

from services.shared.common.auth import get_current_user, authorize_workspace, get_file_user
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

    Proxies to the existing backend pipeline orchestrator for NL2SQL.
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
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """Send a message and get a non-streaming response.

    Runs the full NL2SQL pipeline and returns the final result.
    非流式接口不支持附件(附件需执行层处理),携带附件显式拒绝而非静默忽略。
    """
    from services.datamind.api.send_payload import parse_send_request
    from services.datamind.services.chat_service import ChatService

    req = await parse_send_request(request, SendMessageRequest)
    if req.attachments:
        raise HTTPException(status_code=400, detail="附件消息需要执行层处理,请使用 /send/stream 流式接口")
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
                # 合并共用：工作空间列表含全局(ws=0)会话（AS-BOT 面板等全局入口所建），
                # 反向（全局查询不过滤）同样互见，不按入口区分。
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

    workspace_id=0 表示全局/智能助手会话，按 user_id 隔离，不要求工作空间授权。
    """
    # workspace_id=0 为全局助手会话，仅按 user_id 隔离，无需工作空间授权
    if req.workspace_id:
        authorize_workspace(user, req.workspace_id)
    # AS-BOT 与角色强绑定(一对一)：会话归属直接取当前用户角色的 AS-BOT，
    # 不再依赖页面传标识；无授权 AS-BOT 则 fail-closed。
    as_bot_key = ""
    from services.datamind.execution.models import ExecutionContext
    from services.datamind.execution.tool_policy import resolve_policy
    ctx = ExecutionContext(user_id=user["user_id"], user_role=user.get("role", ""),
                           workspace_id=req.workspace_id or 0, extra={"as_bot_key": req.as_bot_key or ""})
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
                (user["user_id"], "新对话", req.datasource_id or 0, req.workspace_id or 0,
                 as_bot_key, "[]"),
            )
            conn.commit()
            conv_id = cur.lastrowid
            return {
                "id": conv_id,
                "title": "新对话",
                "datasource_id": req.datasource_id or 0,
                "workspace_id": req.workspace_id or 0,
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
