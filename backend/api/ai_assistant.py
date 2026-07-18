"""AI Assistant API — endpoints for AI assistant functionality.

Provides chat, context, and knowledge management capabilities.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from backend.models.ai_assistant import (
    ChatRequest, ChatResponse, ContextRequest, PageContextResponse,
    KnowledgeUpdateRequest, KnowledgeStatusResponse,
    DocumentUploadRequest, DocumentResponse, DocumentListResponse,
    SyncResponse, ErrorResponse, SourceInfo, MessageRole, DocumentType
)
from backend.api.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-assistant", tags=["AI Assistant"])

# ── Permission check ──────────────────────────────────────────────────

AI_ASSISTANT_ROLES = {"admin", "configurator", "viewer"}


def get_user_attr(user, attr: str, default=None):
    """Get attribute from user object (supports both Pydantic model and dict).

    Args:
        user: UserInfo object or dict
        attr: Attribute name
        default: Default value if attribute not found

    Returns:
        Attribute value
    """
    if hasattr(user, attr):
        return getattr(user, attr, default)
    elif isinstance(user, dict):
        return user.get(attr, default)
    return default


def check_ai_assistant_permission(user):
    """Check if user has permission to use AI assistant.

    Args:
        user: UserInfo object (Pydantic model) or dict
    """
    role = get_user_attr(user, "role", "user")
    if role not in AI_ASSISTANT_ROLES:
        raise HTTPException(
            status_code=403,
            detail="没有访问AI助手的权限"
        )


# ── Chat endpoints ────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: dict = Depends(get_current_user)
):
    """发送消息给AI助手

    Args:
        request: 聊天请求，包含消息和可选的上下文
        user: 当前用户（从JWT token获取）

    Returns:
        ChatResponse: AI回复，包含消息、来源和建议
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        response = await service.chat(
            message=request.message,
            context=request.context,
            session_id=request.session_id,
            user_id=get_user_attr(user, "id"),
            module=request.module
        )

        return response

    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="处理请求时出错")


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: dict = Depends(get_current_user)
):
    """流式聊天接口

    Args:
        request: 聊天请求
        user: 当前用户

    Returns:
        StreamingResponse: SSE流式响应
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_service import AIAssistantService
        import uuid

        service = AIAssistantService()

        # 生成或使用现有session_id
        session_id = request.session_id or str(uuid.uuid4())

        async def generate():
            # 首先发送session_id
            yield f'data: {{"session_id": "{session_id}"}}\n\n'

            async for chunk in service.chat_stream(
                message=request.message,
                context=request.context,
                session_id=session_id,
                user_id=get_user_attr(user, "id"),
                module=request.module
            ):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Session-Id": session_id
            }
        )

    except Exception as e:
        logger.error(f"Stream chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="处理请求时出错")


# ── Context endpoints ─────────────────────────────────────────────────

@router.get("/context", response_model=PageContextResponse)
async def get_context(
    page: str = Query(..., description="当前页面"),
    module: str = Query(..., description="当前模块"),
    sub_module: Optional[str] = Query(None, description="子模块"),
    user: dict = Depends(get_current_user)
):
    """获取当前页面的上下文信息

    Args:
        page: 当前页面标识
        module: 当前模块名称
        sub_module: 子模块名称（可选）
        user: 当前用户

    Returns:
        PageContextResponse: 页面上下文，包含相关文档和常见问题
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        context = await service.get_page_context(
            page=page,
            module=module,
            sub_module=sub_module,
            user_id=get_user_attr(user, "id")
        )

        return context

    except Exception as e:
        logger.error(f"Get context error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取上下文时出错")


# ── Knowledge base endpoints ──────────────────────────────────────────

@router.get("/knowledge/status", response_model=KnowledgeStatusResponse)
async def get_knowledge_status(
    user: dict = Depends(get_current_user)
):
    """获取知识库状态

    Returns:
        KnowledgeStatusResponse: 知识库统计信息
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        status = await service.get_knowledge_status()

        return status

    except Exception as e:
        logger.error(f"Get knowledge status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取知识库状态时出错")


@router.post("/knowledge/update", response_model=SyncResponse)
async def update_knowledge(
    request: KnowledgeUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """更新知识库

    Args:
        request: 更新请求，指定来源和是否强制更新
        user: 当前用户

    Returns:
        SyncResponse: 同步结果
    """
    # 只有admin和configurator可以更新知识库
    role = get_user_attr(user, "role", "user")
    if role not in {"admin", "configurator"}:
        raise HTTPException(
            status_code=403,
            detail="没有更新知识库的权限"
        )

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        result = await service.update_knowledge(
            source=request.source,
            force=request.force
        )

        return result

    except Exception as e:
        logger.error(f"Update knowledge error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="更新知识库时出错")


# ── Document endpoints ────────────────────────────────────────────────

@router.get("/knowledge/documents", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    doc_type: Optional[str] = Query(None, description="文档类型过滤"),
    user: dict = Depends(get_current_user)
):
    """获取文档列表

    Args:
        page: 页码
        page_size: 每页数量
        doc_type: 文档类型过滤（可选）
        user: 当前用户

    Returns:
        DocumentListResponse: 文档列表
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        result = await service.list_documents(
            page=page,
            page_size=page_size,
            doc_type=doc_type
        )

        return result

    except Exception as e:
        logger.error(f"List documents error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取文档列表时出错")


@router.post("/knowledge/documents", response_model=DocumentResponse)
async def upload_document(
    request: DocumentUploadRequest,
    user: dict = Depends(get_current_user)
):
    """上传文档到知识库（JSON格式）

    Args:
        request: 文档上传请求
        user: 当前用户

    Returns:
        DocumentResponse: 上传的文档信息
    """
    # 只有admin和configurator可以上传文档
    role = get_user_attr(user, "role", "user")
    if role not in {"admin", "configurator"}:
        raise HTTPException(
            status_code=403,
            detail="没有上传文档的权限"
        )

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        result = await service.upload_document(
            title=request.title,
            content=request.content,
            doc_type=request.doc_type,
            tags=request.tags,
            user_id=get_user_attr(user, "id")
        )

        return result

    except Exception as e:
        logger.error(f"Upload document error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="上传文档时出错")


@router.post("/knowledge/upload", response_model=DocumentResponse)
async def upload_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    doc_type: str = Form("guide"),
    tags: Optional[str] = Form(None),
    user: dict = Depends(get_current_user)
):
    """上传文件到知识库

    Args:
        file: 上传的文件
        title: 文档标题（可选，默认使用文件名）
        doc_type: 文档类型
        tags: 标签（逗号分隔）
        user: 当前用户

    Returns:
        DocumentResponse: 上传的文档信息
    """
    # 只有admin和configurator可以上传文档
    role = get_user_attr(user, "role", "user")
    if role not in {"admin", "configurator"}:
        raise HTTPException(
            status_code=403,
            detail="没有上传文档的权限"
        )

    # 检查文件类型
    allowed_extensions = {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_ext}。支持的类型: {', '.join(allowed_extensions)}"
        )

    try:
        # 读取文件内容
        content = await file.read()
        content_str = content.decode("utf-8")

        # 解析标签
        tag_list = [t.strip() for t in tags.split(",")] if tags else []

        # 使用文件名作为标题（如果没有指定）
        doc_title = title or file.filename

        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        result = await service.upload_document(
            title=doc_title,
            content=content_str,
            doc_type=DocumentType(doc_type),
            tags=tag_list,
            user_id=get_user_attr(user, "id")
        )

        return result

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="文件编码错误，请使用UTF-8编码的文件"
        )
    except Exception as e:
        logger.error(f"Upload file error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="上传文件时出错")


@router.post("/knowledge/upload-multiple")
async def upload_multiple_files(
    files: List[UploadFile] = File(...),
    doc_type: str = Form("guide"),
    tags: Optional[str] = Form(None),
    user: dict = Depends(get_current_user)
):
    """批量上传文件到知识库

    Args:
        files: 上传的文件列表
        doc_type: 文档类型
        tags: 标签（逗号分隔）
        user: 当前用户

    Returns:
        dict: 上传结果
    """
    # 只有admin和configurator可以上传文档
    role = get_user_attr(user, "role", "user")
    if role not in {"admin", "configurator"}:
        raise HTTPException(
            status_code=403,
            detail="没有上传文档的权限"
        )

    # 检查文件数量限制
    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail="最多同时上传10个文件"
        )

    results = []
    errors = []

    for file in files:
        try:
            # 检查文件类型
            allowed_extensions = {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}
            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext not in allowed_extensions:
                errors.append({
                    "filename": file.filename,
                    "error": f"不支持的文件类型: {file_ext}"
                })
                continue

            # 读取文件内容
            content = await file.read()
            content_str = content.decode("utf-8")

            # 解析标签
            tag_list = [t.strip() for t in tags.split(",")] if tags else []

            from backend.services.ai_assistant_service import AIAssistantService

            service = AIAssistantService()
            result = await service.upload_document(
                title=file.filename,
                content=content_str,
                doc_type=DocumentType(doc_type),
                tags=tag_list,
                user_id=get_user_attr(user, "id")
            )

            results.append({
                "filename": file.filename,
                "success": True,
                "doc_id": result.id
            })

        except Exception as e:
            logger.error(f"Error uploading file {file.filename}: {e}")
            errors.append({
                "filename": file.filename,
                "error": str(e)
            })

    return {
        "success": len(errors) == 0,
        "uploaded": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors
    }


@router.delete("/knowledge/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    user: dict = Depends(get_current_user)
):
    """删除文档

    Args:
        doc_id: 文档ID
        user: 当前用户

    Returns:
        dict: 删除结果
    """
    # 只有admin可以删除文档
    role = get_user_attr(user, "role", "user")
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail="没有删除文档的权限"
        )

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        result = await service.delete_document(doc_id)

        return result

    except Exception as e:
        logger.error(f"Delete document error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="删除文档时出错")


@router.get("/knowledge/documents/{doc_id}/content")
async def get_document_content(
    doc_id: str,
    user: dict = Depends(get_current_user)
):
    """获取文档内容（用于在线查看）

    Args:
        doc_id: 文档ID
        user: 当前用户

    Returns:
        dict: 文档内容
    """
    check_ai_assistant_permission(user)

    try:
        from backend.common.db.metadata_db import get_metadata_conn

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, content, doc_type, source, file_path, created_at, updated_at
                    FROM adh_knowledge_documents
                    WHERE id = %s AND status = 'active'
                """, (doc_id,))

                doc = cur.fetchone()
                if not doc:
                    raise HTTPException(status_code=404, detail="文档不存在")

                return {
                    "id": doc["id"],
                    "title": doc["title"],
                    "content": doc["content"],
                    "doc_type": doc["doc_type"],
                    "source": doc["source"],
                    "file_path": doc["file_path"],
                    "created_at": doc["created_at"].isoformat() if doc["created_at"] else None,
                    "updated_at": doc["updated_at"].isoformat() if doc["updated_at"] else None
                }
        finally:
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get document content error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取文档内容时出错")


@router.get("/knowledge/graph")
async def get_knowledge_graph(
    user: dict = Depends(get_current_user)
):
    """获取知识图谱数据（用于可视化展示）

    Args:
        user: 当前用户

    Returns:
        dict: 知识图谱数据
    """
    check_ai_assistant_permission(user)

    try:
        from backend.rag.graph_rag import Neo4jStore

        neo4j = Neo4jStore()
        stats = neo4j.get_stats()

        # 获取节点和关系
        nodes_query = """
        MATCH (n)
        WITH labels(n) as labels, n
        LIMIT 100
        RETURN {
            id: id(n),
            labels: labels(n),
            properties: properties(n)
        } as node
        """
        nodes_result = neo4j.execute_query(nodes_query)

        relationships_query = """
        MATCH (a)-[r]->(b)
        LIMIT 200
        RETURN {
            id: id(r),
            type: type(r),
            source: id(a),
            target: id(b),
            properties: properties(r)
        } as relationship
        """
        relationships_result = neo4j.execute_query(relationships_query)

        return {
            "stats": stats,
            "nodes": [r["node"] for r in nodes_result],
            "relationships": [r["relationship"] for r in relationships_result]
        }

    except Exception as e:
        logger.error(f"Get knowledge graph error: {e}", exc_info=True)
        return {
            "stats": {"node_count": 0, "relationship_count": 0, "labels": [], "connected": False},
            "nodes": [],
            "relationships": [],
            "error": str(e)
        }


@router.get("/knowledge/debug")
async def debug_knowledge_base(
    user: dict = Depends(get_current_user)
):
    """调试知识库状态（用于诊断问题）

    Args:
        user: 当前用户

    Returns:
        dict: 调试信息
    """
    check_ai_assistant_permission(user)

    try:
        from backend.common.db.metadata_db import get_metadata_conn

        result = {
            "tables": {},
            "counts": {},
            "errors": []
        }

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查表是否存在
                tables_to_check = [
                    "adh_knowledge_documents",
                    "adh_knowledge_chunks",
                    "adh_table_info",
                    "adh_column_metadata"
                ]

                for table_name in tables_to_check:
                    try:
                        cur.execute(f"SELECT COUNT(*) as count FROM {table_name}")
                        count = cur.fetchone()["count"]
                        result["tables"][table_name] = {"exists": True, "count": count}
                    except Exception as e:
                        result["tables"][table_name] = {"exists": False, "error": str(e)}

                # 检查adh_table_info是否有数据
                try:
                    cur.execute("SELECT COUNT(*) as count FROM adh_table_info WHERE is_active = 1")
                    result["counts"]["active_tables"] = cur.fetchone()["count"]
                except Exception as e:
                    result["errors"].append(f"Error counting tables: {e}")

                # 检查adh_column_metadata是否有数据
                try:
                    cur.execute("SELECT COUNT(*) as count FROM adh_column_metadata WHERE is_active = 1")
                    result["counts"]["active_columns"] = cur.fetchone()["count"]
                except Exception as e:
                    result["errors"].append(f"Error counting columns: {e}")

                # 检查知识库文档
                try:
                    cur.execute("SELECT COUNT(*) as count FROM adh_knowledge_documents WHERE status = 'active'")
                    result["counts"]["knowledge_docs"] = cur.fetchone()["count"]
                except Exception as e:
                    result["errors"].append(f"Error counting knowledge docs: {e}")

                # 检查知识库分块
                try:
                    cur.execute("SELECT COUNT(*) as count FROM adh_knowledge_chunks WHERE is_active = 1")
                    result["counts"]["knowledge_chunks"] = cur.fetchone()["count"]
                except Exception as e:
                    result["errors"].append(f"Error counting knowledge chunks: {e}")

        finally:
            conn.close()

        return result

    except Exception as e:
        logger.error(f"Debug knowledge base error: {e}", exc_info=True)
        return {
            "tables": {},
            "counts": {},
            "errors": [str(e)]
        }


# ── Suggestions endpoints ─────────────────────────────────────────────

@router.get("/suggestions")
async def get_suggestions(
    context: str = Query(..., description="上下文信息"),
    user: dict = Depends(get_current_user)
):
    """获取基于上下文的建议问题

    Args:
        context: 上下文信息
        user: 当前用户

    Returns:
        list: 建议问题列表
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_service import AIAssistantService

        service = AIAssistantService()
        suggestions = await service.get_suggestions(context)

        return {"suggestions": suggestions}

    except Exception as e:
        logger.error(f"Get suggestions error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取建议时出错")


# ── Conversation history endpoints ─────────────────────────────────────

@router.get("/conversations")
async def get_conversations(
    limit: int = Query(20, ge=1, le=100, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    user: dict = Depends(get_current_user)
):
    """获取用户的会话列表

    Args:
        limit: 返回数量
        offset: 偏移量
        user: 当前用户

    Returns:
        dict: 会话列表
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.conversation_service import ConversationService

        service = ConversationService()
        sessions = await service.get_user_sessions(
            user_id=get_user_attr(user, "id"),
            limit=limit,
            offset=offset
        )

        total = await service.get_session_count(get_user_attr(user, "id"))

        return {
            "sessions": sessions,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Get conversations error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取会话列表时出错")


@router.get("/conversations/{session_id}")
async def get_conversation_messages(
    session_id: str,
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
    user: dict = Depends(get_current_user)
):
    """获取会话消息历史

    Args:
        session_id: 会话ID
        limit: 返回数量
        user: 当前用户

    Returns:
        dict: 消息列表
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.conversation_service import ConversationService

        service = ConversationService()
        messages = await service.get_session_messages(
            session_id=session_id,
            limit=limit
        )

        return {
            "session_id": session_id,
            "messages": messages
        }

    except Exception as e:
        logger.error(f"Get conversation messages error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取会话消息时出错")


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    user: dict = Depends(get_current_user)
):
    """删除会话

    Args:
        session_id: 会话ID
        user: 当前用户

    Returns:
        dict: 删除结果
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.conversation_service import ConversationService

        service = ConversationService()
        success = await service.delete_session(
            session_id=session_id,
            user_id=get_user_attr(user, "id")
        )

        if success:
            return {"success": True, "message": "会话删除成功"}
        else:
            raise HTTPException(status_code=404, detail="会话不存在")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete conversation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="删除会话时出错")


@router.get("/conversations/search")
async def search_conversations(
    query: str = Query(..., description="搜索关键词"),
    limit: int = Query(20, ge=1, le=100, description="返回数量"),
    user: dict = Depends(get_current_user)
):
    """搜索会话消息

    Args:
        query: 搜索关键词
        limit: 返回数量
        user: 当前用户

    Returns:
        dict: 搜索结果
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.conversation_service import ConversationService

        service = ConversationService()
        messages = await service.search_messages(
            user_id=get_user_attr(user, "id"),
            query=query,
            limit=limit
        )

        return {
            "query": query,
            "messages": messages,
            "total": len(messages)
        }

    except Exception as e:
        logger.error(f"Search conversations error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="搜索会话时出错")


# ── Tool execution endpoints ──────────────────────────────────────────

@router.get("/tools/actions")
async def get_pending_actions(
    user: dict = Depends(get_current_user)
):
    """获取待执行的操作

    Args:
        user: 当前用户

    Returns:
        dict: 待执行的操作列表
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_tools import get_tool_executor

        tool_executor = get_tool_executor()
        actions = tool_executor.get_pending_actions()

        return {
            "actions": actions,
            "count": len(actions)
        }

    except Exception as e:
        logger.error(f"Get pending actions error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取待执行操作时出错")


@router.post("/tools/execute")
async def execute_action(
    action: Dict[str, Any],
    user: dict = Depends(get_current_user)
):
    """执行操作

    Args:
        action: 操作定义
        user: 当前用户

    Returns:
        dict: 执行结果
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_tools import get_tool_executor

        tool_executor = get_tool_executor()

        # 执行操作
        result = await tool_executor.execute_tool(
            action.get("type"),
            action.get("params", {})
        )

        return result

    except Exception as e:
        logger.error(f"Execute action error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="执行操作时出错")


@router.post("/tools/clear")
async def clear_pending_actions(
    user: dict = Depends(get_current_user)
):
    """清空待执行的操作

    Args:
        user: 当前用户

    Returns:
        dict: 操作结果
    """
    check_ai_assistant_permission(user)

    try:
        from backend.services.ai_assistant_tools import get_tool_executor

        tool_executor = get_tool_executor()
        tool_executor.clear_pending_actions()

        return {
            "success": True,
            "message": "已清空待执行的操作"
        }

    except Exception as e:
        logger.error(f"Clear pending actions error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="清空操作时出错")


# ── Health check ──────────────────────────────────────────────────────

@router.get("/health")
async def health_check():
    """AI助手健康检查

    Returns:
        dict: 健康状态
    """
    return {
        "status": "healthy",
        "service": "ai-assistant",
        "timestamp": datetime.now().isoformat()
    }
