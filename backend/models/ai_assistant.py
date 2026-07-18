"""AI Assistant Models — Pydantic schemas for AI assistant API."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ── Enums ─────────────────────────────────────────────────────────────

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class DocumentType(str, Enum):
    GUIDE = "guide"
    API = "api"
    TUTORIAL = "tutorial"
    FAQ = "faq"
    CONFIG = "config"


class SyncStatus(str, Enum):
    IDLE = "idle"
    SYNCING = "syncing"
    SUCCESS = "success"
    ERROR = "error"


# ── Request Models ────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., description="用户消息", min_length=1, max_length=4000)
    context: Optional[Dict[str, Any]] = Field(None, description="当前页面上下文")
    session_id: Optional[str] = Field(None, description="会话ID")
    module: Optional[str] = Field(None, description="指定模块名称")


class KnowledgeUpdateRequest(BaseModel):
    """知识库更新请求"""
    source: str = Field(..., description="知识来源（docs, wiki, database）")
    force: bool = Field(False, description="是否强制更新")


class DocumentUploadRequest(BaseModel):
    """文档上传请求"""
    title: str = Field(..., description="文档标题", min_length=1, max_length=200)
    content: str = Field(..., description="文档内容", min_length=1)
    doc_type: DocumentType = Field(DocumentType.GUIDE, description="文档类型")
    tags: List[str] = Field(default_factory=list, description="标签列表")


class ContextRequest(BaseModel):
    """上下文请求"""
    page: str = Field(..., description="当前页面")
    module: str = Field(..., description="当前模块")
    sub_module: Optional[str] = Field(None, description="子模块")
    params: Optional[Dict[str, Any]] = Field(None, description="页面参数")


# ── Response Models ───────────────────────────────────────────────────

class SourceInfo(BaseModel):
    """知识来源信息"""
    id: str = Field(..., description="来源ID")
    title: str = Field(..., description="来源标题")
    content: str = Field(..., description="来源内容摘要")
    source: str = Field(..., description="来源类型")
    relevance: float = Field(..., description="相关度分数")


class ChatResponse(BaseModel):
    """聊天响应"""
    message: str = Field(..., description="AI回复消息")
    context: Optional[Dict[str, Any]] = Field(None, description="上下文信息")
    sources: List[SourceInfo] = Field(default_factory=list, description="知识来源")
    suggestions: List[str] = Field(default_factory=list, description="建议问题")
    session_id: Optional[str] = Field(None, description="会话ID")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="工具调用列表")
    tool_results: Optional[List[Dict[str, Any]]] = Field(None, description="工具执行结果")
    pending_actions: Optional[List[Dict[str, Any]]] = Field(None, description="待执行的操作")


class PageContextResponse(BaseModel):
    """页面上下文响应"""
    page: str = Field(..., description="当前页面")
    module: str = Field(..., description="当前模块")
    sub_module: Optional[str] = Field(None, description="子模块")
    related_docs: List[SourceInfo] = Field(default_factory=list, description="相关文档")
    common_questions: List[str] = Field(default_factory=list, description="常见问题")
    quick_actions: List[Dict[str, str]] = Field(default_factory=list, description="快捷操作")


class KnowledgeStatusResponse(BaseModel):
    """知识库状态响应"""
    document_count: int = Field(..., description="文档数量")
    chunk_count: int = Field(..., description="分块数量")
    vector_count: int = Field(..., description="向量数量")
    last_sync: Optional[str] = Field(None, description="最后同步时间")
    sync_status: SyncStatus = Field(SyncStatus.IDLE, description="同步状态")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="知识来源统计")


class DocumentResponse(BaseModel):
    """文档响应"""
    id: str = Field(..., description="文档ID")
    title: str = Field(..., description="文档标题")
    doc_type: DocumentType = Field(..., description="文档类型")
    source: str = Field(..., description="来源")
    size: str = Field(..., description="文档大小")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    tags: List[str] = Field(default_factory=list, description="标签")


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    documents: List[DocumentResponse] = Field(..., description="文档列表")
    total: int = Field(..., description="总数")
    page: int = Field(1, description="当前页")
    page_size: int = Field(20, description="每页数量")


class SyncResponse(BaseModel):
    """同步响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    document_count: int = Field(0, description="处理文档数")
    chunk_count: int = Field(0, description="生成分块数")
    updated_at: Optional[str] = Field(None, description="更新时间")


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str = Field(..., description="错误信息")
    detail: Optional[str] = Field(None, description="详细信息")
    code: Optional[str] = Field(None, description="错误代码")


# ── Internal Models ───────────────────────────────────────────────────

class DocumentChunk(BaseModel):
    """文档分块（内部使用）"""
    id: str = Field(..., description="分块ID")
    document_id: str = Field(..., description="文档ID")
    content: str = Field(..., description="分块内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    embedding: Optional[List[float]] = Field(None, description="向量嵌入")


class KnowledgeEntity(BaseModel):
    """知识实体（内部使用）"""
    id: str = Field(..., description="实体ID")
    name: str = Field(..., description="实体名称")
    entity_type: str = Field(..., description="实体类型")
    description: str = Field("", description="描述")
    properties: Dict[str, Any] = Field(default_factory=dict, description="属性")
    embedding: Optional[List[float]] = Field(None, description="向量嵌入")


class KnowledgeRelation(BaseModel):
    """知识关系（内部使用）"""
    source_id: str = Field(..., description="源实体ID")
    target_id: str = Field(..., description="目标实体ID")
    relation_type: str = Field(..., description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="属性")


class ConversationMessage(BaseModel):
    """对话消息（内部使用）"""
    role: MessageRole = Field(..., description="角色")
    content: str = Field(..., description="消息内容")
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
