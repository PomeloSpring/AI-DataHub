"""Async Neo4j Store — async Neo4j connection and operations.

Provides async connection management and CRUD operations for Neo4j.
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class AsyncNeo4jStore:
    """异步Neo4j存储层"""

    def __init__(
        self,
        uri: str = None,
        user: str = None,
        password: str = None,
        database: str = "neo4j"
    ):
        """初始化异步Neo4j连接

        Args:
            uri: Neo4j连接URI
            user: 用户名
            password: 密码
            database: 数据库名
        """
        import os

        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.database = database

        self._driver = None
        self._connected = False

    async def _get_driver(self):
        """获取异步Neo4j驱动"""
        if self._driver is None:
            try:
                from neo4j import AsyncGraphDatabase
                self._driver = AsyncGraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password)
                )
                self._connected = True
                logger.info(f"Connected to Neo4j (async): {self.uri}")
            except Exception as e:
                logger.error(f"Failed to connect to Neo4j (async): {e}")
                self._connected = False
                raise
        return self._driver

    async def close(self):
        """关闭连接"""
        if self._driver:
            await self._driver.close()
            self._driver = None
            self._connected = False
            logger.info("Async Neo4j connection closed")

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """执行异步查询

        Args:
            query: Cypher查询语句
            parameters: 查询参数

        Returns:
            list: 查询结果
        """
        try:
            driver = await self._get_driver()
            async with driver.session(database=self.database) as session:
                result = await session.run(query, parameters or {})
                records = await result.data()
                return records
        except Exception as e:
            logger.error(f"Async query execution failed: {e}")
            raise

    async def execute_write(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> Any:
        """执行异步写操作

        Args:
            query: Cypher查询语句
            parameters: 查询参数

        Returns:
            any: 操作结果
        """
        try:
            driver = await self._get_driver()
            async with driver.session(database=self.database) as session:
                result = await session.execute_write(
                    lambda tx: tx.run(query, parameters or {}).consume()
                )
                return result
        except Exception as e:
            logger.error(f"Async write operation failed: {e}")
            raise

    async def create_node(
        self,
        label: str,
        properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        """创建节点

        Args:
            label: 节点标签
            properties: 节点属性

        Returns:
            dict: 创建的节点
        """
        query = f"""
        CREATE (n:{label} $props)
        RETURN n, labels(n) as labels, id(n) as id
        """
        result = await self.execute_query(query, {"props": properties})
        return result[0] if result else None

    async def create_relation(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """创建关系

        Args:
            source_id: 源节点ID
            target_id: 目标节点ID
            rel_type: 关系类型
            properties: 关系属性

        Returns:
            dict: 创建的关系
        """
        query = """
        MATCH (a), (b)
        WHERE id(a) = $source_id AND id(b) = $target_id
        CREATE (a)-[r:$rel_type $props]->(b)
        RETURN r, type(r) as type, id(r) as id
        """
        # Note: Neo4j driver doesn't support dynamic relationship types in parameterized queries
        # We need to use string formatting for the relationship type
        query = f"""
        MATCH (a), (b)
        WHERE id(a) = $source_id AND id(b) = $target_id
        CREATE (a)-[r:{rel_type} $props]->(b)
        RETURN r, type(r) as type, id(r) as id
        """
        result = await self.execute_query(query, {
            "source_id": source_id,
            "target_id": target_id,
            "props": properties or {}
        })
        return result[0] if result else None

    async def search_nodes(
        self,
        search_text: str,
        node_types: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """搜索节点

        Args:
            search_text: 搜索文本
            node_types: 节点类型过滤
            limit: 返回数量限制

        Returns:
            list: 匹配的节点
        """
        type_filter = ""
        if node_types:
            type_conditions = [f"n:{t}" for t in node_types]
            type_filter = "WHERE " + " OR ".join(type_conditions)

        query = f"""
        MATCH (n)
        {type_filter}
        WHERE n.name CONTAINS $search
           OR n.comment CONTAINS $search
           OR n.name_cn CONTAINS $search
           OR n.description CONTAINS $search
        RETURN n, labels(n) as labels, id(n) as id
        LIMIT $limit
        """
        return await self.execute_query(query, {
            "search": search_text,
            "limit": limit
        })

    async def get_node_by_id(self, node_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取节点

        Args:
            node_id: 节点ID

        Returns:
            dict: 节点数据
        """
        query = """
        MATCH (n)
        WHERE id(n) = $node_id
        RETURN n, labels(n) as labels, id(n) as id
        """
        result = await self.execute_query(query, {"node_id": node_id})
        return result[0] if result else None

    async def get_related_nodes(
        self,
        node_id: str,
        max_depth: int = 2,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取相关节点

        Args:
            node_id: 节点ID
            max_depth: 最大遍历深度
            limit: 返回数量限制

        Returns:
            list: 相关节点
        """
        query = """
        MATCH (start)-[*1..$max_depth]-(related)
        WHERE id(start) = $node_id
        RETURN DISTINCT related, labels(related) as labels, id(related) as id
        LIMIT $limit
        """
        return await self.execute_query(query, {
            "node_id": node_id,
            "max_depth": max_depth,
            "limit": limit
        })

    async def get_stats(self) -> Dict[str, Any]:
        """获取图谱统计信息

        Returns:
            dict: 统计信息
        """
        try:
            # 获取节点数量
            node_count_query = "MATCH (n) RETURN count(n) as count"
            node_result = await self.execute_query(node_count_query)
            node_count = node_result[0]["count"] if node_result else 0

            # 获取关系数量
            rel_count_query = "MATCH ()-[r]->() RETURN count(r) as count"
            rel_result = await self.execute_query(rel_count_query)
            rel_count = rel_result[0]["count"] if rel_result else 0

            # 获取标签列表
            labels_query = "CALL db.labels() YIELD label RETURN collect(label) as labels"
            labels_result = await self.execute_query(labels_query)
            labels = labels_result[0]["labels"] if labels_result else []

            return {
                "node_count": node_count,
                "relationship_count": rel_count,
                "labels": labels,
                "connected": True
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "node_count": 0,
                "relationship_count": 0,
                "labels": [],
                "connected": False
            }

    async def create_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """创建关系

        Args:
            source_id: 源节点ID
            target_id: 目标节点ID
            rel_type: 关系类型
            properties: 关系属性

        Returns:
            bool: 是否成功
        """
        try:
            query = f"""
            MATCH (a {{id: $source_id}}), (b {{id: $target_id}})
            CREATE (a)-[r:{rel_type} $props]->(b)
            RETURN r
            """
            result = await self.execute_query(query, {
                "source_id": source_id,
                "target_id": target_id,
                "props": properties or {}
            })
            return len(result) > 0
        except Exception as e:
            logger.error(f"Failed to create relationship: {e}")
            return False
