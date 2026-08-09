"""Graph Models — Pydantic schemas for knowledge graph API."""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────

class GraphType(str, Enum):
    """图谱类型"""
    TABLE_RELATION = "table-relation"
    BUSINESS_KNOWLEDGE = "business-knowledge"
    DATA_LINEAGE = "data-lineage"


class NodeType(str, Enum):
    """节点类型"""
    TABLE = "Table"
    COLUMN = "Column"
    TERM = "Term"
    METRIC = "Metric"
    DIMENSION = "Dimension"
    DATASOURCE = "DataSource"
    DOCUMENT = "Document"


class RelationType(str, Enum):
    """关系类型"""
    HAS_COLUMN = "HAS_COLUMN"
    JOIN = "JOIN"
    MAPS_TO = "MAPS_TO"
    DEFINES = "DEFINES"
    USES_DIMENSION = "USES_DIMENSION"
    BELONGS_TO = "BELONGS_TO"
    DESCRIBES = "DESCRIBES"


# ── Request Models ─────────────────────────────────────────────────────

class GraphQueryRequest(BaseModel):
    """图谱查询请求"""
    datasource_id: Optional[int] = Field(None, description="数据源ID")
    node_types: Optional[List[NodeType]] = Field(None, description="节点类型过滤")
    max_depth: int = Field(2, ge=1, le=5, description="最大遍历深度")
    center_node: Optional[str] = Field(None, description="中心节点ID")
    search: Optional[str] = Field(None, description="搜索关键词")
    limit: int = Field(200, ge=1, le=1000, description="返回数量限制")


class NodeCreateRequest(BaseModel):
    """节点创建请求"""
    node_type: NodeType = Field(..., description="节点类型")
    properties: Dict[str, Any] = Field(..., description="节点属性")


class NodeUpdateRequest(BaseModel):
    """节点更新请求"""
    properties: Dict[str, Any] = Field(..., description="更新的属性")


class RelationCreateRequest(BaseModel):
    """关系创建请求"""
    source_id: str = Field(..., description="源节点ID")
    target_id: str = Field(..., description="目标节点ID")
    relation_type: RelationType = Field(..., description="关系类型")
    properties: Optional[Dict[str, Any]] = Field(None, description="关系属性")


class RelationUpdateRequest(BaseModel):
    """关系更新请求"""
    properties: Dict[str, Any] = Field(..., description="更新的属性")


class AskRequest(BaseModel):
    """智能问答请求"""
    question: str = Field(..., min_length=1, description="用户问题")
    datasource_id: Optional[int] = Field(None, description="数据源ID")
    max_results: int = Field(20, ge=1, le=100, description="最大结果数")


# ── Response Models ────────────────────────────────────────────────────

class GraphNode(BaseModel):
    """图节点"""
    id: str = Field(..., description="节点ID")
    label: str = Field(..., description="节点标签/类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点属性")


class GraphEdge(BaseModel):
    """图边"""
    id: str = Field(..., description="关系ID")
    source: str = Field(..., description="源节点ID")
    target: str = Field(..., description="目标节点ID")
    type: str = Field(..., description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="关系属性")


class GraphStats(BaseModel):
    """图谱统计"""
    node_count: int = Field(0, description="节点总数")
    relationship_count: int = Field(0, description="关系总数")
    labels: List[str] = Field(default_factory=list, description="节点类型列表")
    connected: bool = Field(False, description="Neo4j连接状态")


class GraphData(BaseModel):
    """图谱数据"""
    nodes: List[GraphNode] = Field(default_factory=list, description="节点列表")
    edges: List[GraphEdge] = Field(default_factory=list, description="边列表")
    stats: GraphStats = Field(default_factory=GraphStats, description="统计信息")


class AskResponse(BaseModel):
    """智能问答响应"""
    answer: str = Field(..., description="回答")
    cypher: Optional[str] = Field(None, description="生成的Cypher查询")
    nodes: List[GraphNode] = Field(default_factory=list, description="相关节点")
    edges: List[GraphEdge] = Field(default_factory=list, description="相关边")
    confidence: float = Field(0.0, ge=0, le=1, description="置信度")


class SyncResponse(BaseModel):
    """同步响应"""
    success: bool = Field(..., description="是否成功")
    tables: int = Field(0, description="同步的表数")
    columns: int = Field(0, description="同步的字段数")
    terms: int = Field(0, description="同步的术语数")
    relations: int = Field(0, description="同步的关系数")
    message: Optional[str] = Field(None, description="消息")


class NodeDetailResponse(BaseModel):
    """节点详情响应"""
    node: GraphNode = Field(..., description="节点信息")
    related_nodes: List[GraphNode] = Field(default_factory=list, description="关联节点")
    relations: List[GraphEdge] = Field(default_factory=list, description="关联关系")
