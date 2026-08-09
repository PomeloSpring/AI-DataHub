"""Graph Service — unified knowledge graph service.

Provides graph query, edit, and sync capabilities.
"""

import logging
from typing import Optional, List, Dict, Any

from services.datamind.rag.graph_rag.async_neo4j_store import AsyncNeo4jStore
from services.datamind.rag.graph_rag.neo4j_store import Neo4jStore
from services.datamind.rag.graph_rag.graph_builder import GraphBuilder
from services.datamind.rag.graph_rag.graph_retriever import GraphRetriever
from services.shared.models.graph import (
    GraphType, NodeType, GraphNode, GraphEdge, GraphStats, GraphData,
    NodeDetailResponse, SyncResponse
)

logger = logging.getLogger(__name__)


class GraphService:
    """知识图谱服务"""

    def __init__(self, neo4j_store=None):
        """初始化服务

        Args:
            neo4j_store: Neo4j存储实例（AsyncNeo4jStore或Neo4jStore）
        """
        if neo4j_store is None:
            self.neo4j = AsyncNeo4jStore()
            self.is_async = True
        elif isinstance(neo4j_store, AsyncNeo4jStore):
            self.neo4j = neo4j_store
            self.is_async = True
        else:
            self.neo4j = neo4j_store
            self.is_async = False
        self.builder = GraphBuilder(self.neo4j)
        self.retriever = GraphRetriever(self.neo4j)

    def _to_graph_node(self, neo4j_node: Dict[str, Any]) -> GraphNode:
        """将Neo4j节点转换为GraphNode

        Args:
            neo4j_node: Neo4j节点数据

        Returns:
            GraphNode: 图节点
        """
        props = neo4j_node.get("properties", neo4j_node)
        node_id = props.get("id", str(neo4j_node.get("id", "")))

        # 获取标签
        labels = neo4j_node.get("labels", [])
        if not labels:
            # 尝试从type属性推断
            node_type = props.get("type", "Unknown")
            labels = [node_type.capitalize()]

        return GraphNode(
            id=node_id,
            label=labels[0] if labels else "Unknown",
            properties=props
        )

    def _to_graph_edge(self, neo4j_rel: Dict[str, Any], source_id: str = None, target_id: str = None) -> GraphEdge:
        """将Neo4j关系转换为GraphEdge

        Args:
            neo4j_rel: Neo4j关系数据
            source_id: 源节点ID（可选）
            target_id: 目标节点ID（可选）

        Returns:
            GraphEdge: 图边
        """
        props = neo4j_rel.get("properties", {})
        rel_id = str(neo4j_rel.get("id", ""))
        rel_type = neo4j_rel.get("type", "RELATED")

        return GraphEdge(
            id=rel_id,
            source=source_id or props.get("source", ""),
            target=target_id or props.get("target", ""),
            type=rel_type,
            properties=props
        )

    async def get_graph_data(
        self,
        graph_type: GraphType = GraphType.TABLE_RELATION,
        datasource_id: Optional[int] = None,
        node_types: Optional[List[NodeType]] = None,
        max_depth: int = 2,
        center_node: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200
    ) -> GraphData:
        """获取图谱数据

        Args:
            graph_type: 图谱类型
            datasource_id: 数据源ID
            node_types: 节点类型过滤
            max_depth: 最大遍历深度
            center_node: 中心节点ID
            search: 搜索关键词
            limit: 返回数量限制

        Returns:
            GraphData: 图谱数据
        """
        try:
            nodes = []
            edges = []

            # 构建查询条件
            type_filter = ""
            if node_types:
                type_conditions = [f"n:{t.value}" for t in node_types]
                type_filter = "WHERE " + " OR ".join(type_conditions)

            ds_filter = ""
            if datasource_id is not None:
                ds_filter = f"AND n.datasource_id = {datasource_id}"

            # 根据图谱类型构建不同的查询
            if graph_type == GraphType.TABLE_RELATION:
                nodes, edges = await self._get_table_relation_graph(
                    type_filter, ds_filter, limit
                )
            elif graph_type == GraphType.BUSINESS_KNOWLEDGE:
                nodes, edges = await self._get_business_knowledge_graph(
                    type_filter, ds_filter, limit
                )
            elif graph_type == GraphType.DATA_LINEAGE:
                nodes, edges = await self._get_data_lineage_graph(
                    type_filter, ds_filter, limit
                )

            # 如果指定了中心节点，获取子图
            if center_node and nodes:
                center_found = any(n.id == center_node for n in nodes)
                if not center_found:
                    # 尝试查找中心节点
                    related = await self.retriever.find_related_nodes(
                        center_node, max_depth=max_depth
                    )
                    for node_data in related:
                        nodes.append(self._to_graph_node(node_data))

            # 搜索过滤
            if search and nodes:
                search_lower = search.lower()
                nodes = [
                    n for n in nodes
                    if search_lower in n.id.lower()
                    or search_lower in str(n.properties.get("name", "")).lower()
                    or search_lower in str(n.properties.get("comment", "")).lower()
                    or search_lower in str(n.properties.get("name_cn", "")).lower()
                ]
                # 过滤边，只保留两端都在节点列表中的边
                node_ids = {n.id for n in nodes}
                edges = [e for e in edges if e.source in node_ids and e.target in node_ids]

            # 获取统计
            stats = await self._get_stats()

            return GraphData(
                nodes=nodes[:limit],
                edges=edges[:limit * 2],
                stats=stats
            )

        except Exception as e:
            logger.error(f"Failed to get graph data: {e}", exc_info=True)
            return GraphData(
                stats=GraphStats(connected=False)
            )

    async def _execute_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """执行查询（支持异步和同步）

        Args:
            query: Cypher查询语句
            parameters: 查询参数

        Returns:
            list: 查询结果
        """
        if self.is_async:
            return await self.neo4j.execute_query(query, parameters)
        else:
            return self.neo4j.execute_query(query, parameters)

    async def _get_table_relation_graph(
        self, type_filter: str, ds_filter: str, limit: int
    ) -> tuple[List[GraphNode], List[GraphEdge]]:
        """获取表关系图数据"""
        nodes = []
        edges = []

        # 获取Table和Column节点
        query = f"""
        MATCH (n)
        WHERE (n:Table OR n:Column) {ds_filter.replace('AND', 'AND', 1) if ds_filter else ''}
        RETURN n, labels(n) as labels
        LIMIT {limit}
        """
        try:
            result = await self._execute_query(query)
            for record in result:
                node = self._to_graph_node(record["n"])
                node.label = record["labels"][0] if record["labels"] else "Unknown"
                nodes.append(node)
        except Exception as e:
            logger.warning(f"Query failed, trying simpler query: {e}")
            # 降级查询
            nodes = await self._get_nodes_fallback(["Table", "Column"], limit)

        # 获取关系
        rel_query = """
        MATCH (a)-[r]->(b)
        WHERE type(r) IN ['HAS_COLUMN', 'JOIN']
        RETURN a, b, r, type(r) as rel_type, id(a) as src, id(b) as tgt
        LIMIT $limit
        """
        try:
            result = await self._execute_query(rel_query, {"limit": limit * 2})
            for record in result:
                src_props = record["a"]
                tgt_props = record["b"]
                src_id = src_props.get("id", str(record["src"]))
                tgt_id = tgt_props.get("id", str(record["tgt"]))

                # Extract relationship properties
                rel_props = record["r"]
                if hasattr(rel_props, 'items'):
                    rel_props = dict(rel_props.items())
                elif not isinstance(rel_props, dict):
                    rel_props = {}

                edge = GraphEdge(
                    id=f"{src_id}-{record['rel_type']}-{tgt_id}",
                    source=src_id,
                    target=tgt_id,
                    type=record["rel_type"],
                    properties=rel_props
                )
                edges.append(edge)
        except Exception as e:
            logger.warning(f"Relation query failed: {e}")

        return nodes, edges

    async def _get_business_knowledge_graph(
        self, type_filter: str, ds_filter: str, limit: int
    ) -> tuple[List[GraphNode], List[GraphEdge]]:
        """获取业务知识图数据"""
        nodes = []
        edges = []

        # 获取Term、Metric、Dimension节点
        node_query = f"""
        MATCH (n)
        WHERE (n:Term OR n:Metric OR n:Dimension) {ds_filter.replace('AND', 'AND', 1) if ds_filter else ''}
        RETURN n, labels(n) as labels
        LIMIT {limit}
        """
        try:
            result = await self._execute_query(node_query)
            for record in result:
                node = self._to_graph_node(record["n"])
                node.label = record["labels"][0] if record["labels"] else "Unknown"
                nodes.append(node)
        except Exception as e:
            logger.warning(f"Business knowledge query failed: {e}")
            nodes = await self._get_nodes_fallback(["Term", "Metric", "Dimension"], limit)

        # 获取关联的Table和Column
        related_query = """
        MATCH (n)-[r]-(m)
        WHERE (n:Term OR n:Metric OR n:Dimension)
        AND (m:Table OR m:Column)
        RETURN n, m, r, type(r) as rel_type, id(n) as src, id(m) as tgt
        LIMIT $limit
        """
        try:
            result = await self._execute_query(related_query, {"limit": limit})
            added_node_ids = {n.id for n in nodes}

            for record in result:
                # 添加关联节点
                m_props = record["m"]
                m_id = m_props.get("id", str(record["tgt"]))
                if m_id not in added_node_ids:
                    related_node = self._to_graph_node(record["m"])
                    nodes.append(related_node)
                    added_node_ids.add(m_id)

                # 添加边
                n_props = record["n"]
                n_id = n_props.get("id", str(record["src"]))

                # Extract relationship properties
                rel_props = record["r"]
                if hasattr(rel_props, 'items'):
                    rel_props = dict(rel_props.items())
                elif not isinstance(rel_props, dict):
                    rel_props = {}

                edge = GraphEdge(
                    id=f"{n_id}-{record['rel_type']}-{m_id}",
                    source=n_id,
                    target=m_id,
                    type=record["rel_type"],
                    properties=rel_props
                )
                edges.append(edge)
        except Exception as e:
            logger.warning(f"Business knowledge relations query failed: {e}")

        return nodes, edges

    async def _get_data_lineage_graph(
        self, type_filter: str, ds_filter: str, limit: int
    ) -> tuple[List[GraphNode], List[GraphEdge]]:
        """获取数据血缘图数据"""
        nodes = []
        edges = []

        # 获取DataSource节点
        ds_query = """
        MATCH (n:DataSource)
        RETURN n, labels(n) as labels
        LIMIT $limit
        """
        try:
            result = await self._execute_query(ds_query, {"limit": limit // 3})
            for record in result:
                node = self._to_graph_node(record["n"])
                node.label = "DataSource"
                nodes.append(node)
        except Exception as e:
            logger.warning(f"DataSource query failed: {e}")

        # 获取ETLTask节点
        etl_query = """
        MATCH (n:ETLTask)
        RETURN n, labels(n) as labels
        LIMIT $limit
        """
        try:
            result = await self._execute_query(etl_query, {"limit": limit // 3})
            for record in result:
                node = self._to_graph_node(record["n"])
                node.label = "ETLTask"
                nodes.append(node)
        except Exception as e:
            logger.warning(f"ETLTask query failed: {e}")

        # 获取Table节点
        table_query = """
        MATCH (n:Table)
        RETURN n, labels(n) as labels
        LIMIT $limit
        """
        try:
            result = await self._execute_query(table_query, {"limit": limit // 3})
            for record in result:
                node = self._to_graph_node(record["n"])
                node.label = "Table"
                nodes.append(node)
        except Exception as e:
            logger.warning(f"Table query failed: {e}")

        # 获取血缘关系
        rel_query = """
        MATCH (a)-[r]->(b)
        WHERE type(r) IN ['PRODUCES', 'CONSUMES', 'FEEDS', 'TRANSFORMS', 'DEPENDS_ON']
        RETURN a, b, r, type(r) as rel_type, id(a) as src, id(b) as tgt
        LIMIT $limit
        """
        try:
            result = await self._execute_query(rel_query, {"limit": limit * 2})
            for record in result:
                src_props = record["a"]
                tgt_props = record["b"]
                src_id = src_props.get("id", str(record["src"]))
                tgt_id = tgt_props.get("id", str(record["tgt"]))

                # Extract relationship properties
                rel_props = record["r"]
                if hasattr(rel_props, 'items'):
                    rel_props = dict(rel_props.items())
                elif not isinstance(rel_props, dict):
                    rel_props = {}

                edge = GraphEdge(
                    id=f"{src_id}-{record['rel_type']}-{tgt_id}",
                    source=src_id,
                    target=tgt_id,
                    type=record["rel_type"],
                    properties=rel_props
                )
                edges.append(edge)
        except Exception as e:
            logger.warning(f"Lineage relations query failed: {e}")

        return nodes, edges

    async def _get_nodes_fallback(
        self, labels: List[str], limit: int
    ) -> List[GraphNode]:
        """降级获取节点（处理Neo4j查询失败）"""
        nodes = []
        for label in labels:
            try:
                query = f"MATCH (n:{label}) RETURN n LIMIT {limit // len(labels)}"
                result = await self._execute_query(query)
                for record in result:
                    node = self._to_graph_node(record["n"])
                    node.label = label
                    nodes.append(node)
            except Exception as e:
                logger.warning(f"Fallback query for {label} failed: {e}")
        return nodes

    async def _get_stats(self) -> GraphStats:
        """获取图谱统计"""
        try:
            if self.is_async:
                stats = await self.neo4j.get_stats()
            else:
                stats = self.neo4j.get_stats()
            return GraphStats(
                node_count=stats.get("node_count", 0),
                relationship_count=stats.get("relationship_count", 0),
                labels=stats.get("labels", []),
                connected=stats.get("connected", False)
            )
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return GraphStats(connected=False)

    async def get_node_detail(self, node_id: str) -> Optional[NodeDetailResponse]:
        """获取节点详情

        Args:
            node_id: 节点ID

        Returns:
            NodeDetailResponse: 节点详情
        """
        try:
            # 查找节点
            query = """
            MATCH (n {id: $node_id})
            RETURN n, labels(n) as labels
            """
            result = await self._execute_query(query, {"node_id": node_id})

            if not result:
                return None

            node_data = result[0]["n"]
            labels = result[0]["labels"]
            node = GraphNode(
                id=node_id,
                label=labels[0] if labels else "Unknown",
                properties=node_data
            )

            # 获取关联节点和关系
            related_query = """
            MATCH (n {id: $node_id})-[r]-(m)
            RETURN m, r, type(r) as rel_type, id(n) as src, id(m) as tgt,
                   startNode(r).id as start_id, endNode(r).id as end_id
            LIMIT 50
            """
            related_result = await self._execute_query(related_query, {"node_id": node_id})

            related_nodes = []
            relations = []
            seen_node_ids = set()

            for record in related_result:
                # 添加关联节点
                m_id = record["m"].get("id", str(record["tgt"]))
                if m_id not in seen_node_ids:
                    related_node = self._to_graph_node(record["m"])
                    related_nodes.append(related_node)
                    seen_node_ids.add(m_id)

                # 添加关系
                start_id = record.get("start_id", node_id)
                end_id = record.get("end_id", m_id)

                edge = GraphEdge(
                    id=f"{start_id}-{record['rel_type']}-{end_id}",
                    source=start_id,
                    target=end_id,
                    type=record["rel_type"],
                    properties=record["r"]
                )
                relations.append(edge)

            return NodeDetailResponse(
                node=node,
                related_nodes=related_nodes,
                relations=relations
            )

        except Exception as e:
            logger.error(f"Failed to get node detail: {e}")
            return None

    async def create_node(
        self, node_type: NodeType, properties: Dict[str, Any]
    ) -> Optional[GraphNode]:
        """创建节点

        Args:
            node_type: 节点类型
            properties: 节点属性

        Returns:
            GraphNode: 创建的节点
        """
        try:
            # 确保有id属性
            if "id" not in properties:
                properties["id"] = f"{node_type.value.lower()}:{properties.get('name', 'new')}"

            if self.is_async:
                await self.neo4j.create_node(node_type.value, properties)
            else:
                self.neo4j.create_node(node_type.value, properties)

            return GraphNode(
                id=properties["id"],
                label=node_type.value,
                properties=properties
            )

        except Exception as e:
            logger.error(f"Failed to create node: {e}")
            return None

    async def update_node(
        self, node_id: str, properties: Dict[str, Any]
    ) -> bool:
        """更新节点

        Args:
            node_id: 节点ID
            properties: 更新的属性

        Returns:
            bool: 是否成功
        """
        try:
            set_clauses = []
            params = {"node_id": node_id}

            for key, value in properties.items():
                param_name = f"prop_{key}"
                set_clauses.append(f"n.{key} = ${param_name}")
                params[param_name] = value

            set_str = ", ".join(set_clauses)
            query = f"""
            MATCH (n {{id: $node_id}})
            SET {set_str}
            RETURN n
            """

            await self._execute_query(query, params)
            return True

        except Exception as e:
            logger.error(f"Failed to update node: {e}")
            return False

    async def delete_node(self, node_id: str) -> bool:
        """删除节点

        Args:
            node_id: 节点ID

        Returns:
            bool: 是否成功
        """
        try:
            query = """
            MATCH (n {id: $node_id})
            DETACH DELETE n
            """
            if self.is_async:
                await self.neo4j.execute_write(query, {"node_id": node_id})
            else:
                self.neo4j.execute_write(query, {"node_id": node_id})
            return True

        except Exception as e:
            logger.error(f"Failed to delete node: {e}")
            return False

    async def create_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> Optional[GraphEdge]:
        """创建关系

        Args:
            source_id: 源节点ID
            target_id: 目标节点ID
            relation_type: 关系类型
            properties: 关系属性

        Returns:
            GraphEdge: 创建的关系
        """
        try:
            if self.is_async:
                success = await self.neo4j.create_relationship(
                    source_id, target_id, relation_type, properties
                )
            else:
                success = self.neo4j.create_relationship(
                    source_id, target_id, relation_type, properties
                )

            if success:
                return GraphEdge(
                    id=f"{source_id}-{relation_type}-{target_id}",
                    source=source_id,
                    target=target_id,
                    type=relation_type,
                    properties=properties or {}
                )

            return None

        except Exception as e:
            logger.error(f"Failed to create relation: {e}")
            return None

    async def delete_relation(
        self, source_id: str, target_id: str, relation_type: str
    ) -> bool:
        """删除关系

        Args:
            source_id: 源节点ID
            target_id: 目标节点ID
            relation_type: 关系类型

        Returns:
            bool: 是否成功
        """
        try:
            query = f"""
            MATCH (a {{id: $source_id}})-[r:{relation_type}]->(b {{id: $target_id}})
            DELETE r
            RETURN count(r) as deleted
            """
            result = await self._execute_query(query, {
                "source_id": source_id,
                "target_id": target_id
            })
            return result[0]["deleted"] > 0 if result else False

        except Exception as e:
            logger.error(f"Failed to delete relation: {e}")
            return False

    async def sync_from_metadata(self, datasource_id: int = 0) -> SyncResponse:
        """从元数据同步到Neo4j

        Args:
            datasource_id: 数据源ID

        Returns:
            SyncResponse: 同步结果
        """
        try:
            result = await self.builder.build_from_metadata(datasource_id)

            if result.get("success"):
                return SyncResponse(
                    success=True,
                    tables=result.get("tables", 0),
                    columns=result.get("columns", 0),
                    terms=result.get("terms", 0),
                    relations=result.get("relations", 0),
                    message="同步成功"
                )
            else:
                return SyncResponse(
                    success=False,
                    message=result.get("error", "同步失败")
                )

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return SyncResponse(
                success=False,
                message=str(e)
            )

    async def search_nodes(
        self,
        query: str,
        node_types: Optional[List[NodeType]] = None,
        limit: int = 20
    ) -> List[GraphNode]:
        """搜索节点

        Args:
            query: 搜索关键词
            node_types: 节点类型过滤
            limit: 返回数量限制

        Returns:
            List[GraphNode]: 节点列表
        """
        try:
            types = [t.value for t in node_types] if node_types else None
            results = await self.retriever.search_nodes(query, types, limit)

            nodes = []
            for record in results:
                node = self._to_graph_node(record.get("n", record))
                if "types" in record:
                    node.label = record["types"][0] if record["types"] else node.label
                nodes.append(node)

            return nodes

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
