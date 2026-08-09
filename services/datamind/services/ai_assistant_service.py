"""AI Assistant Service — business logic for AI assistant functionality.

Provides chat, context awareness, and knowledge management capabilities.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncGenerator, Tuple

from services.shared.models.ai_assistant import (
    ChatRequest, ChatResponse, PageContextResponse,
    KnowledgeStatusResponse, DocumentUploadRequest,
    DocumentResponse, DocumentListResponse, SyncResponse,
    SourceInfo, DocumentType, SyncStatus, MessageRole,
    ConversationMessage
)
from services.datamind.config.ai_assistant_config import (
    AI_ASSISTANT_MAX_TOKENS,
    AI_ASSISTANT_TEMPERATURE,
    AI_ASSISTANT_MAX_HISTORY_LENGTH,
    AI_ASSISTANT_CONTEXT_LIMIT,
    SYSTEM_PROMPT_TEMPLATE,
    CONTEXT_INFO_TEMPLATE,
    KNOWLEDGE_CONTEXT_TEMPLATE
)

logger = logging.getLogger(__name__)


class AIAssistantService:
    """AI助手服务类"""

    def __init__(self):
        """初始化服务"""
        self._conversations: Dict[str, List[ConversationMessage]] = {}
        self._knowledge_cache: Dict[str, Any] = {}

    async def chat(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
        module: Optional[str] = None
    ) -> ChatResponse:
        """处理聊天请求

        Args:
            message: 用户消息
            context: 页面上下文
            session_id: 会话ID
            user_id: 用户ID
            module: 指定模块

        Returns:
            ChatResponse: AI回复
        """
        try:
            # 生成或使用现有会话ID
            if not session_id:
                session_id = str(uuid.uuid4())

            # 记录用户消息
            self._add_message(session_id, MessageRole.USER, message, user_id)

            # 判断问题类型
            needs_tools = self._needs_tools(message)
            needs_rag = self._needs_rag(message)

            # 检索相关知识（仅在需要时）
            sources = []
            if needs_rag:
                sources = await self._retrieve_knowledge(message, context, module, limit=3)

            # 构建提示词
            prompt = self._build_prompt(message, sources, context, module, needs_tools)

            response_text = ""
            tool_calls = []
            tool_results = []
            pending_actions = []

            if needs_tools:
                # 获取工具定义
                from services.datamind.services.ai_assistant_tools import get_tool_executor
                tool_executor = get_tool_executor()
                tools = tool_executor.get_tools()

                logger.info(f"Calling LLM with tools for message: {message}")

                # 调用LLM生成回复（带工具支持）
                response_text, tool_calls = await self._call_llm_with_tools(prompt, tools)

                logger.info(f"LLM response: text='{response_text[:100]}...', tool_calls={len(tool_calls)}")

                # 执行工具调用
                if tool_calls:
                    for tool_call in tool_calls:
                        logger.info(f"Executing tool: {tool_call['name']} with input: {tool_call['input']}")
                        result = await tool_executor.execute_tool(
                            tool_call["name"],
                            tool_call["input"]
                        )
                        tool_results.append(result)
                        logger.info(f"Tool result: {result}")

                # 获取待执行的操作
                pending_actions = tool_executor.get_pending_actions()
                logger.info(f"Pending actions: {len(pending_actions)}")

                # 如果有工具调用，生成合适的响应
                if tool_calls:
                    # 检查是否是打开表单的操作
                    has_open_form = any(tc['name'] == 'open_form' for tc in tool_calls)

                    if has_open_form and not response_text:
                        # 打开表单后，询问用户参数
                        form_type = tool_calls[0]['input'].get('form_type', '')
                        response_text = self._generate_form_questions(form_type)
                    elif not response_text:
                        response_text = f"好的，我来帮你{message.replace('帮我', '').replace('请帮我', '')}。"
            else:
                # 简单问题，直接调用LLM，不使用工具
                response_text = await self._call_llm(prompt)

            # 提取建议问题
            suggestions = await self._extract_suggestions(message, response_text, context)

            # 记录AI回复
            self._add_message(session_id, MessageRole.ASSISTANT, response_text, user_id)

            return ChatResponse(
                message=response_text,
                context=context,
                sources=sources,
                suggestions=suggestions,
                session_id=session_id,
                tool_calls=tool_calls if tool_calls else None,
                tool_results=tool_results if tool_results else None,
                pending_actions=pending_actions if pending_actions else None
            )

        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            raise

    def _needs_tools(self, message: str) -> bool:
        """判断是否需要使用工具

        Args:
            message: 用户消息

        Returns:
            bool: 是否需要工具
        """
        message_lower = message.lower()

        # 明确的操作意图关键词（优先级最高）
        action_keywords = [
            "帮我", "请帮我", "帮忙", "协助我",
            "我要", "我想", "我需要"
        ]

        # 需要使用工具的关键词
        tool_keywords = [
            "创建", "新建", "添加", "配置", "设置", "打开", "跳转",
            "填写", "保存", "提交", "删除", "编辑", "修改"
        ]

        # 工具对象关键词
        tool_objects = [
            "数据源", "定时任务", "通知", "工作流", "agent", "报表",
            "渠道", "模板", "用户", "权限"
        ]

        # 纯粹的询问类关键词（不需要工具）
        question_keywords = [
            "是什么", "什么是", "怎么理解", "如何理解", "为什么",
            "哪个页面", "当前页面", "现在在", "这是哪",
            "帮助", "说明", "介绍", "解释", "文档",
            "怎么用", "如何使用", "怎么配置", "如何配置"
        ]

        # 1. 如果是纯粹的询问类问题，不需要工具
        if any(keyword in message_lower for keyword in question_keywords):
            # 但如果同时包含明确的操作意图，仍然需要工具
            if any(keyword in message_lower for keyword in ["帮我", "请帮我", "帮忙"]):
                return True
            return False

        # 2. 如果包含明确的操作意图 + 操作关键词 + 工具对象，需要工具
        has_action = any(keyword in message_lower for keyword in action_keywords)
        has_tool_keyword = any(keyword in message_lower for keyword in tool_keywords)
        has_tool_object = any(keyword in message_lower for keyword in tool_objects)

        if has_action and has_tool_keyword and has_tool_object:
            return True

        # 3. 如果包含操作关键词 + 工具对象，需要工具
        if has_tool_keyword and has_tool_object:
            return True

        # 4. 默认不使用工具
        logger.info(f"Tool not needed for message: {message}")
        return False

    def _generate_form_questions(self, form_type: str) -> str:
        """生成表单参数询问

        Args:
            form_type: 表单类型

        Returns:
            str: 询问文本
        """
        questions_map = {
            "datasource": """已打开数据源配置页面，请告诉我以下信息：

1. **数据源名称**是什么？
   （例如：生产环境 MySQL、测试库等）

2. **数据库类型**是什么？
   （MySQL / PostgreSQL / Doris / Elasticsearch）

3. **主机地址**是什么？
   （例如：192.168.1.100、db.example.com）

4. **端口**是多少？
   （MySQL默认3306，PostgreSQL默认5432）

5. **数据库名称**是什么？

6. **用户名**是什么？

7. **密码**是什么？

请逐个告诉我，我会帮你填写到表单中。""",

            "scheduled_task": """已打开定时任务配置页面，请告诉我以下信息：

1. **任务名称**是什么？
   （例如：每日销售报表、每周汇总等）

2. **执行模式**是什么？
   （SQL模式 / Agent模式）

3. **数据源**是哪个？
   （选择要查询的数据源）

4. **SQL查询**或**查询问题**是什么？
   （SQL模式写SQL，Agent模式写自然语言问题）

5. **执行周期**是什么？
   （例如：每天9点、每小时、每周一等）

6. **通知渠道**需要配置吗？
   （钉钉 / 飞书 / 企业微信 / 邮件）

请告诉我这些信息，我会帮你配置。""",

            "notification_channel": """已打开通知渠道配置页面，请告诉我以下信息：

1. **渠道名称**是什么？
   （例如：研发群钉钉、运维群飞书等）

2. **渠道类型**是什么？
   （钉钉 / 飞书 / 企业微信 / 邮件 / Webhook）

3. **Webhook URL**是什么？
   （从对应的群设置中获取）

请告诉我这些信息，我会帮你配置。"""
        }

        return questions_map.get(form_type, "已打开配置页面，请告诉我需要填写的信息。")

    def _needs_rag(self, message: str) -> bool:
        """判断是否需要RAG检索

        Args:
            message: 用户消息

        Returns:
            bool: 是否需要RAG检索
        """
        # 不需要RAG检索的关键词（上下文相关问题）
        context_keywords = [
            "当前页面", "现在在", "哪个页面", "这是哪", "我在哪",
            "页面名称", "当前模块", "这是什么页面", "什么页面"
        ]

        # 不需要RAG检索的简单问题
        simple_keywords = [
            "你好", "hello", "hi", "谢谢", "感谢"
        ]

        message_lower = message.lower()

        # 如果是上下文相关问题，不需要RAG
        if any(keyword in message_lower for keyword in context_keywords):
            logger.info(f"Skipping RAG for context question: {message}")
            return False

        # 如果是简单问题，不需要RAG
        if any(keyword in message_lower for keyword in simple_keywords):
            logger.info(f"Skipping RAG for simple question: {message}")
            return False

        # 其他问题需要RAG
        logger.info(f"Using RAG for question: {message}")
        return True

    async def chat_stream(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
        module: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """流式聊天

        Args:
            message: 用户消息
            context: 页面上下文
            session_id: 会话ID
            user_id: 用户ID
            module: 指定模块

        Yields:
            str: 流式响应块（JSON格式，包含type和content）
        """
        try:
            # 生成或使用现有会话ID
            if not session_id:
                session_id = str(uuid.uuid4())

            # 记录用户消息
            self._add_message(session_id, MessageRole.USER, message, user_id)

            # 检索相关知识
            sources = await self._retrieve_knowledge(message, context, module)

            # 构建提示词
            prompt = self._build_prompt(message, sources, context, module)

            # 流式调用LLM
            full_response = ""
            full_thinking = ""
            is_thinking = False

            async for event_type, data in self._call_llm_stream(prompt):
                if event_type == "thinking_start":
                    is_thinking = True
                    full_thinking = ""
                    yield json.dumps({"type": "thinking_start"})
                elif event_type == "thinking_end":
                    is_thinking = False
                    yield json.dumps({"type": "thinking_end"})
                elif event_type == "thinking":
                    full_thinking += data
                    yield json.dumps({"type": "thinking", "content": data})
                elif event_type == "token":
                    full_response += data
                    yield json.dumps({"type": "message", "message": data})
                elif event_type == "done":
                    break

            # 记录完整的AI回复
            self._add_message(session_id, MessageRole.ASSISTANT, full_response, user_id)

        except Exception as e:
            logger.error(f"Stream chat error: {e}", exc_info=True)
            yield json.dumps({"type": "error", "message": f"错误: {str(e)}"})

    async def get_page_context(
        self,
        page: str,
        module: str,
        sub_module: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> PageContextResponse:
        """获取页面上下文

        Args:
            page: 页面标识
            module: 模块名称
            sub_module: 子模块
            user_id: 用户ID

        Returns:
            PageContextResponse: 页面上下文
        """
        try:
            # 获取相关文档
            context_query = f"{module} {sub_module or ''} 配置 帮助"
            related_docs = await self._retrieve_knowledge(context_query, None, module)

            # 获取常见问题
            common_questions = self._get_common_questions(page, module)

            # 获取快捷操作
            quick_actions = self._get_quick_actions(page, module)

            return PageContextResponse(
                page=page,
                module=module,
                sub_module=sub_module,
                related_docs=related_docs,
                common_questions=common_questions,
                quick_actions=quick_actions
            )

        except Exception as e:
            logger.error(f"Get context error: {e}", exc_info=True)
            raise

    async def get_knowledge_status(self) -> KnowledgeStatusResponse:
        """获取知识库状态

        Returns:
            KnowledgeStatusResponse: 知识库统计
        """
        try:
            # 使用知识库服务
            from services.datamind.services.knowledge_service import KnowledgeService
            knowledge_service = KnowledgeService()

            stats = await knowledge_service.get_knowledge_stats()

            return KnowledgeStatusResponse(
                document_count=stats.get("document_count", 0),
                chunk_count=stats.get("chunk_count", 0),
                vector_count=stats.get("vector_count", 0),
                last_sync=stats.get("last_sync"),
                sync_status=SyncStatus.IDLE,
                sources=[]
            )

        except Exception as e:
            logger.error(f"Get knowledge status error: {e}", exc_info=True)
            raise

    async def update_knowledge(
        self,
        source: str,
        force: bool = False
    ) -> SyncResponse:
        """更新知识库

        Args:
            source: 知识来源
            force: 是否强制更新

        Returns:
            SyncResponse: 同步结果
        """
        try:
            logger.info(f"Updating knowledge base: source={source}, force={force}")

            # 使用知识库服务
            from services.datamind.services.knowledge_service import KnowledgeService
            knowledge_service = KnowledgeService()

            result = await knowledge_service.sync_knowledge_base(
                source=source,
                force=force
            )

            if result["success"]:
                return SyncResponse(
                    success=True,
                    message="知识库更新成功",
                    document_count=result["document_count"],
                    chunk_count=result["chunk_count"],
                    updated_at=result.get("updated_at")
                )
            else:
                return SyncResponse(
                    success=False,
                    message=f"更新失败: {result.get('error', '未知错误')}",
                    document_count=0,
                    chunk_count=0
                )

        except Exception as e:
            logger.error(f"Update knowledge error: {e}", exc_info=True)
            return SyncResponse(
                success=False,
                message=f"更新失败: {str(e)}",
                document_count=0,
                chunk_count=0
            )

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        doc_type: Optional[str] = None
    ) -> DocumentListResponse:
        """获取文档列表

        Args:
            page: 页码
            page_size: 每页数量
            doc_type: 文档类型过滤

        Returns:
            DocumentListResponse: 文档列表
        """
        try:
            # 使用知识库服务
            from services.datamind.services.knowledge_service import KnowledgeService
            knowledge_service = KnowledgeService()

            result = await knowledge_service.list_documents(
                page=page,
                page_size=page_size,
                doc_type=doc_type
            )

            # 转换为DocumentResponse
            documents = []
            for doc in result["documents"]:
                # 确保tags是列表
                tags = doc.get("tags") or []
                if isinstance(tags, str):
                    try:
                        import json
                        tags = json.loads(tags)
                    except:
                        tags = []

                documents.append(DocumentResponse(
                    id=doc["id"],
                    title=doc["title"],
                    doc_type=DocumentType(doc["doc_type"]),
                    source=doc["source"],
                    size=doc["size"],
                    created_at=datetime.fromisoformat(doc["created_at"]) if doc["created_at"] else datetime.now(),
                    updated_at=datetime.fromisoformat(doc["updated_at"]) if doc["updated_at"] else datetime.now(),
                    tags=tags if isinstance(tags, list) else []
                ))

            return DocumentListResponse(
                documents=documents,
                total=result["total"],
                page=result["page"],
                page_size=result["page_size"]
            )

        except Exception as e:
            logger.error(f"List documents error: {e}", exc_info=True)
            raise

    async def upload_document(
        self,
        title: str,
        content: str,
        doc_type: DocumentType,
        tags: List[str],
        user_id: Optional[int] = None,
        workspace_id: Optional[int] = None
    ) -> DocumentResponse:
        """上传文档

        Args:
            title: 文档标题
            content: 文档内容
            doc_type: 文档类型
            tags: 标签列表
            user_id: 用户ID
            workspace_id: 工作空间ID

        Returns:
            DocumentResponse: 上传的文档信息
        """
        try:
            # 生成文档ID
            doc_id = str(uuid.uuid4())

            # 保存文档
            self._save_document(doc_id, title, content, doc_type, tags, user_id, workspace_id)

            # 异步处理向量化
            await self._process_document_async(doc_id, content)

            return DocumentResponse(
                id=doc_id,
                title=title,
                doc_type=doc_type,
                source="manual",
                size=f"{len(content)} chars",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                tags=tags
            )

        except Exception as e:
            logger.error(f"Upload document error: {e}", exc_info=True)
            raise

    async def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """删除文档

        Args:
            doc_id: 文档ID

        Returns:
            dict: 删除结果
        """
        try:
            # 使用知识库服务
            from services.datamind.services.knowledge_service import KnowledgeService
            knowledge_service = KnowledgeService()

            result = await knowledge_service.delete_document(doc_id)

            return result

        except Exception as e:
            logger.error(f"Delete document error: {e}", exc_info=True)
            raise

    async def vectorize_document(self, doc_id: str) -> Dict[str, Any]:
        """对文档进行向量化

        Args:
            doc_id: 文档ID

        Returns:
            dict: 向量化结果
        """
        try:
            from services.datamind.services.knowledge_service import KnowledgeService
            knowledge_service = KnowledgeService()

            result = await knowledge_service.vectorize_document(doc_id)

            return result

        except Exception as e:
            logger.error(f"Vectorize document error: {e}", exc_info=True)
            raise

    async def get_suggestions(self, context: str) -> List[str]:
        """获取建议问题

        Args:
            context: 上下文信息

        Returns:
            List[str]: 建议问题列表
        """
        try:
            # 基于上下文生成建议
            suggestions = self._generate_suggestions(context)
            return suggestions

        except Exception as e:
            logger.error(f"Get suggestions error: {e}", exc_info=True)
            return []

    # ── Private methods ──────────────────────────────────────────────

    def _save_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        doc_type: str,
        tags: List[str],
        user_id: Optional[int] = None,
        workspace_id: Optional[int] = None
    ):
        """保存文档到数据库

        Args:
            doc_id: 文档ID
            title: 文档标题
            content: 文档内容
            doc_type: 文档类型
            tags: 标签列表
            user_id: 用户ID
            workspace_id: 工作空间ID
        """
        try:
            import json
            from services.shared.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    tags_json = json.dumps(tags) if tags else None
                    cur.execute("""
                        INSERT INTO adh_knowledge_documents
                        (id, title, content, doc_type, source, file_path, file_size,
                         chunk_count, status, workspace_id, tags, has_embedding)
                        VALUES (%s, %s, %s, %s, 'manual', '', %s, 0, 'active', %s, %s, 0)
                        ON DUPLICATE KEY UPDATE
                        title = VALUES(title),
                        content = VALUES(content),
                        tags = VALUES(tags),
                        updated_at = CURRENT_TIMESTAMP
                    """, (doc_id, title, content[:1000], doc_type, len(content), workspace_id, tags_json))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Error saving document: {e}")

    def _add_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
        user_id: Optional[int] = None
    ):
        """添加消息到会话

        Args:
            session_id: 会话ID
            role: 消息角色
            content: 消息内容
            user_id: 用户ID（用于持久化）
        """
        # 添加到内存缓存
        if session_id not in self._conversations:
            self._conversations[session_id] = []

        self._conversations[session_id].append(
            ConversationMessage(
                role=role,
                content=content,
                timestamp=datetime.now()
            )
        )

        # 限制会话历史长度
        if len(self._conversations[session_id]) > AI_ASSISTANT_MAX_HISTORY_LENGTH:
            self._conversations[session_id] = self._conversations[session_id][-AI_ASSISTANT_MAX_HISTORY_LENGTH:]

        # 异步保存到数据库
        if user_id:
            try:
                from services.datamind.services.conversation_service import ConversationService
                conversation_service = ConversationService()

                # 使用asyncio.create_task异步保存，不阻塞主流程
                import asyncio
                asyncio.create_task(
                    conversation_service.save_message(
                        session_id=session_id,
                        user_id=user_id,
                        role=role.value,
                        content=content
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to save message to database: {e}")

    async def _retrieve_knowledge(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        module: Optional[str] = None,
        limit: int = 5
    ) -> List[SourceInfo]:
        """检索相关知识

        Args:
            query: 查询文本
            context: 页面上下文
            module: 指定模块
            limit: 返回结果数量限制

        Returns:
            List[SourceInfo]: 知识来源列表
        """
        try:
            from services.shared.common.vector import get_vector_store
            from services.shared.common.llm.embedding import generate_embedding

            store = get_vector_store()
            query_embedding = generate_embedding(query)

            # 从知识库检索
            results = store.search(
                table="adh_knowledge_chunks",
                query_embedding=query_embedding,
                limit=limit,
                filters={"is_active": 1}
            )

            # 转换为SourceInfo
            sources = []
            for i, result in enumerate(results):
                sources.append(SourceInfo(
                    id=result.get("id", str(i)),
                    title=result.get("title", ""),
                    content=result.get("content", "")[:300],  # 减少内容长度
                    source=result.get("source", "knowledge_base"),
                    relevance=1.0 - (i * 0.1)
                ))

            # 使用图谱上下文增强检索
            try:
                from services.datamind.services.graph_context_service import GraphContextService
                graph_service = GraphContextService()

                # 获取图谱上下文
                datasource_id = context.get("datasource_id") if context else None
                graph_context = await graph_service.get_context_for_query(
                    query=query,
                    datasource_id=datasource_id,
                    max_tables=5
                )

                # 添加表信息
                for table in graph_context.tables[:3]:
                    table_name = table.get('name', '')
                    table_comment = table.get('comment', '') or table.get('business_desc', '')
                    if table_name:
                        sources.append(SourceInfo(
                            id=f"table:{table_name}",
                            title=f"表: {table_name}",
                            content=f"{table_name}: {table_comment}",
                            source="knowledge_graph",
                            relevance=0.9
                        ))

                # 添加术语信息
                for term in graph_context.terms[:2]:
                    term_name = term.get('name_cn', '')
                    term_desc = term.get('description', '')
                    if term_name:
                        sources.append(SourceInfo(
                            id=f"term:{term_name}",
                            title=f"术语: {term_name}",
                            content=f"{term_name}: {term_desc}",
                            source="knowledge_graph",
                            relevance=0.85
                        ))

                # 添加指标信息
                for metric in graph_context.metrics[:2]:
                    metric_name = metric.get('name', '')
                    metric_desc = metric.get('description', '') or metric.get('formula', '')
                    if metric_name:
                        sources.append(SourceInfo(
                            id=f"metric:{metric_name}",
                            title=f"指标: {metric_name}",
                            content=f"{metric_name}: {metric_desc}",
                            source="knowledge_graph",
                            relevance=0.8
                        ))

            except Exception as e:
                logger.debug(f"Graph context enhancement failed (non-critical): {e}")

            return sources

        except Exception as e:
            logger.warning(f"Retrieve knowledge error: {e}")
            return []

    def _build_prompt(
        self,
        message: str,
        sources: List[SourceInfo],
        context: Optional[Dict[str, Any]] = None,
        module: Optional[str] = None,
        needs_tools: bool = False
    ) -> str:
        """构建提示词

        Args:
            message: 用户消息
            sources: 知识来源
            context: 页面上下文
            module: 指定模块
            needs_tools: 是否需要使用工具

        Returns:
            str: 构建的提示词
        """
        # 判断用户问题是否与当前页面相关
        is_page_related = self._is_page_related_question(message)

        # 构建上下文信息（仅在问题与页面相关时添加）
        context_info = ""
        if context and is_page_related:
            title = context.get('title', '')
            page = context.get('page', 'unknown')
            sub_module = context.get('subModule', '')

            context_info = f"当前页面：{title or page}"
            if sub_module:
                context_info += f"（{sub_module}）"

        # 分类知识来源
        knowledge_sources = [s for s in sources if s.source == "knowledge_base"]
        graph_sources = [s for s in sources if s.source == "knowledge_graph"]

        # 构建知识库上下文
        knowledge_context = ""
        if knowledge_sources:
            sources_text = ""
            for i, source in enumerate(knowledge_sources[:3], 1):
                sources_text += f"{i}. {source.title}: {source.content[:200]}\n"
            knowledge_context = f"\n## 知识库\n{sources_text}"

        # 构建图谱上下文
        graph_context = ""
        if graph_sources:
            # 按类型分组
            tables = [s for s in graph_sources if s.id.startswith("table:")]
            terms = [s for s in graph_sources if s.id.startswith("term:")]
            metrics = [s for s in graph_sources if s.id.startswith("metric:")]

            graph_parts = []
            if tables:
                tables_text = "\n".join([f"- {s.title}: {s.content}" for s in tables[:3]])
                graph_parts.append(f"### 相关表\n{tables_text}")
            if terms:
                terms_text = "\n".join([f"- {s.title}: {s.content}" for s in terms[:2]])
                graph_parts.append(f"### 业务术语\n{terms_text}")
            if metrics:
                metrics_text = "\n".join([f"- {s.title}: {s.content}" for s in metrics[:2]])
                graph_parts.append(f"### 业务指标\n{metrics_text}")

            if graph_parts:
                graph_context = f"\n## 知识图谱\n" + "\n\n".join(graph_parts)

        # 根据是否需要工具构建不同的提示词
        if needs_tools:
            prompt = f"""你是一个AI助手，帮助用户配置和使用AI-DataHub系统。

{context_info}
{knowledge_context}
{graph_context}

用户请求：{message}

重要：用户需要你执行操作，请使用提供的工具来帮助用户完成操作。
- 如果用户要创建/新建配置，使用 open_form 工具打开创建表单
- 如果用户要编辑配置，使用 open_form 工具打开编辑表单
- 如果用户要导航到某个页面，使用 navigate_to_page 工具

请直接调用工具执行操作，不要只提供步骤说明。"""
        else:
            prompt = f"""你是一个AI助手，帮助用户配置和使用AI-DataHub系统。

AI-DataHub是一个自然语言商业智能平台，主要功能包括：
- 自然语言查询：用户可以用中文提问，系统自动生成SQL查询数据
- 多Agent架构：支持数据分析、日志分析、流量分析等多种场景
- 可视化报表：自动生成图表和仪表盘
- 定时任务：支持定时执行查询并发送通知
- 多数据源支持：支持MySQL、Doris、Elasticsearch等数据库

{knowledge_context}
{graph_context}

用户问题：{message}

请根据用户的问题提供准确的回答。如果用户问的是项目整体功能，介绍系统的核心能力。如果用户问的是具体功能，提供详细说明。如果涉及表结构、业务术语或指标，请基于知识图谱信息回答。"""

        return prompt

    def _is_page_related_question(self, message: str) -> bool:
        """判断问题是否与当前页面相关

        Args:
            message: 用户消息

        Returns:
            bool: 是否与页面相关
        """
        message_lower = message.lower()

        # 与页面相关的关键词
        page_keywords = [
            "当前页面", "现在在", "这个页面", "这个菜单",
            "这里", "本页面", "当前功能", "这个功能"
        ]

        # 与项目整体相关的关键词
        project_keywords = [
            "项目", "系统", "平台", "整体", "全部",
            "是什么", "做什么", "功能", "能力",
            "介绍", "说明", "概述"
        ]

        # 如果包含页面相关关键词，返回True
        if any(kw in message_lower for kw in page_keywords):
            return True

        # 如果包含项目整体关键词，返回False
        if any(kw in message_lower for kw in project_keywords):
            return False

        # 默认返回False，不添加页面上下文
        return False

    async def _call_llm(self, prompt: str) -> str:
        """调用LLM

        Args:
            prompt: 提示词

        Returns:
            str: LLM回复
        """
        try:
            from services.shared.common.llm.llm_client import call_llm

            response = await call_llm(prompt)
            return response

        except Exception as e:
            logger.error(f"LLM call error: {e}")
            return f"抱歉，处理请求时出现错误: {str(e)}"

    async def _call_llm_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]]
    ) -> tuple[str, List[Dict[str, Any]]]:
        """调用LLM（带工具支持）

        Args:
            prompt: 提示词
            tools: 工具定义列表

        Returns:
            tuple: (回复文本, 工具调用列表)
        """
        try:
            from services.shared.common.llm.llm_client import generate_with_tools

            # 构建消息格式
            messages = [{"role": "user", "content": prompt}]

            logger.info(f"Calling LLM with {len(tools)} tools")

            # 调用LLM
            result = generate_with_tools(
                messages=messages,
                tools=tools,
                max_tokens=AI_ASSISTANT_MAX_TOKENS
            )

            response_text = result.get("text", "")
            tool_calls = result.get("tool_uses", [])

            logger.info(f"LLM returned: text length={len(response_text)}, tool_calls={len(tool_calls)}")

            if tool_calls:
                for tc in tool_calls:
                    logger.info(f"Tool call: {tc.get('name')} with input: {tc.get('input')}")

            return response_text, tool_calls

        except Exception as e:
            logger.error(f"LLM with tools call error: {e}", exc_info=True)
            return f"抱歉，处理请求时出现错误: {str(e)}", []

    async def _call_llm_stream(self, prompt: str) -> AsyncGenerator[tuple, None]:
        """流式调用LLM

        Args:
            prompt: 提示词

        Yields:
            tuple: (event_type, data)
                - ("thinking_start", None): 思考开始
                - ("thinking", str): 思考内容
                - ("thinking_end", None): 思考结束
                - ("token", str): 生成内容
                - ("done", tokens): 完成
        """
        try:
            from services.shared.common.llm.llm_client import generate_sql_stream

            # 构建消息格式
            messages = [{"role": "user", "content": prompt}]

            # 使用流式调用
            thinking_started = False
            for event_type, data in generate_sql_stream(messages):
                if event_type == "token":
                    if thinking_started:
                        yield ("thinking_end", None)
                        thinking_started = False
                    yield ("token", data)
                elif event_type == "thinking":
                    if not thinking_started:
                        yield ("thinking_start", None)
                        thinking_started = True
                    yield ("thinking", data)
                elif event_type == "done":
                    if thinking_started:
                        yield ("thinking_end", None)
                        thinking_started = False
                    yield ("done", data)
                    break

        except Exception as e:
            logger.error(f"LLM stream error: {e}")
            yield ("error", f"错误: {str(e)}")

    async def _extract_suggestions(
        self,
        question: str,
        answer: str,
        context: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """提取建议问题

        Args:
            question: 用户问题
            answer: AI回答
            context: 上下文

        Returns:
            List[str]: 建议问题列表
        """
        # 基于上下文生成建议
        module = context.get("module", "") if context else ""

        suggestions_map = {
            "datasource": [
                "如何测试数据源连接？",
                "数据源配置有哪些注意事项？",
                "如何修改数据源配置？"
            ],
            "agent": [
                "Agent的工作原理是什么？",
                "如何配置Agent的提示词？",
                "Agent支持哪些工具？"
            ],
            "workflow": [
                "如何创建工作流？",
                "工作流节点有哪些类型？",
                "如何调试工作流？"
            ],
            "chat": [
                "如何提高查询准确性？",
                "查询结果为空怎么办？",
                "如何查看SQL语句？"
            ]
        }

        return suggestions_map.get(module, [
            "如何配置数据源？",
            "Agent是什么？",
            "如何创建报表？"
        ])

    def _get_common_questions(self, page: str, module: str) -> List[str]:
        """获取常见问题"""
        questions_map = {
            "datasource": [
                "如何配置MySQL数据源？",
                "数据源连接失败怎么办？",
                "如何测试数据源连接？"
            ],
            "agent": [
                "什么是Agent？",
                "如何配置Agent？",
                "Agent的执行流程是什么？"
            ],
            "workflow": [
                "如何创建工作流？",
                "工作流节点有哪些类型？",
                "如何调试工作流？"
            ]
        }

        return questions_map.get(module, [
            "系统有哪些功能？",
            "如何开始使用？",
            "遇到问题怎么办？"
        ])

    def _get_quick_actions(self, page: str, module: str) -> List[Dict[str, str]]:
        """获取快捷操作"""
        actions_map = {
            "datasource": [
                {"label": "新建数据源", "action": "create_datasource"},
                {"label": "测试连接", "action": "test_connection"},
                {"label": "查看文档", "action": "view_docs"}
            ],
            "agent": [
                {"label": "新建Agent", "action": "create_agent"},
                {"label": "配置提示词", "action": "configure_prompt"},
                {"label": "查看日志", "action": "view_logs"}
            ]
        }

        return actions_map.get(module, [
            {"label": "查看帮助", "action": "view_help"},
            {"label": "联系支持", "action": "contact_support"}
        ])

    def _generate_suggestions(self, context: str) -> List[str]:
        """生成建议"""
        # 基于上下文生成建议
        module = context.get("module", "") if isinstance(context, dict) else ""

        suggestions_map = {
            "datasource": [
                "如何配置MySQL数据源？",
                "数据源连接失败怎么办？",
                "如何测试数据源连接？"
            ],
            "agent": [
                "什么是Agent？",
                "如何配置Agent？",
                "Agent支持哪些工具？"
            ],
            "workflow": [
                "如何创建工作流？",
                "工作流节点有哪些类型？",
                "如何调试工作流？"
            ]
        }

        return suggestions_map.get(module, [
            "如何配置数据源？",
            "Agent是什么？",
            "如何创建报表？"
        ])
