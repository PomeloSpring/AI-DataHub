"""Neo4j Store — Neo4j connection and operations.

Provides connection management and basic CRUD operations for Neo4j.
"""

import logging
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Neo4j存储层"""

    def __init__(
        self,
        uri: str = None,
        user: str = None,
        password: str = None,
        database: str = "neo4j"
    ):
        """初始化Neo4j连接

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

    @property
    def driver(self):
        """懒加载Neo4j驱动"""
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
                self._driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password)
                )
                self._connected = True
                logger.info(f"Connected to Neo4j: {self.uri}")
            except Exception as e:
                logger.error(f"Failed to connect to Neo4j: {e}")
                self._connected = False
                raise
        return self._driver

    def close(self):
        """关闭连接"""
        if self._driver:
            self._driver.close()
            self._driver = None
            self._connected = False
            logger.info("Neo4j connection closed")

    @contextmanager
    def get_session(self):
        """获取数据库会话"""
        session = self.driver.session(database=self.database)
        try:
            yield session
        finally:
            session.close()

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """执行查询

        Args:
            query: Cypher查询语句
            parameters: 查询参数

        Returns:
            list: 查询结果
        """
        try:
            with self.get_session() as session:
                result = session.run(query, parameters or {})
                return [record.data() for record in result]
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            raise

    def execute_write(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> Any:
        """执行写操作

        Args:
            query: Cypher查询语句
            parameters: 查询参数

        Returns:
            any: 操作结果
        """
        try:
            with self.get_session() as session:
                result = session.execute_write(
                    lambda tx: tx.run(query, parameters or {}).consume()
                )
                return result
        except Exception as e:
            logger.error(f"Write operation failed: {e}")
            raise

    def create_node(
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
        CREATE (n:{label} $properties)
        RETURN n
        """
        result = self.execute_query(query, {"properties": properties})
        return result[0]["n"] if result else None

    def create_relationship(
        self,
        start_node_id: str,
        end_node_id: str,
        relationship_type: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """创建关系

        Args:
            start_node_id: 起始节点ID
            end_node_id: 结束节点ID
            relationship_type: 关系类型
            properties: 关系属性

        Returns:
            bool: 是否成功
        """
        query = f"""
        MATCH (a), (b)
        WHERE a.id = $start_id AND b.id = $end_id
        CREATE (a)-[r:{relationship_type} $properties]->(b)
        RETURN r
        """
        try:
            self.execute_query(query, {
                "start_id": start_node_id,
                "end_id": end_node_id,
                "properties": properties or {}
            })
            return True
        except Exception as e:
            logger.error(f"Failed to create relationship: {e}")
            return False

    def find_nodes(
        self,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """查找节点

        Args:
            label: 节点标签
            properties: 过滤属性
            limit: 返回数量限制

        Returns:
            list: 节点列表
        """
        where_clause = ""
        params = {"limit": limit}

        if properties:
            conditions = []
            for key, value in properties.items():
                conditions.append(f"n.{key} = ${key}")
                params[key] = value
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
        MATCH (n:{label})
        {where_clause}
        RETURN n
        LIMIT $limit
        """

        result = self.execute_query(query, params)
        return [record["n"] for record in result]

    def find_related_nodes(
        self,
        node_id: str,
        relationship_type: Optional[str] = None,
        direction: str = "both",
        max_depth: int = 1
    ) -> List[Dict[str, Any]]:
        """查找关联节点

        Args:
            node_id: 节点ID
            relationship_type: 关系类型
            direction: 方向（in, out, both）
            max_depth: 最大深度

        Returns:
            list: 关联节点列表
        """
        rel_pattern = ""
        if relationship_type:
            rel_pattern = f":{relationship_type}"

        if direction == "out":
            pattern = f"(n)-[{rel_pattern}*1..{max_depth}]->(related)"
        elif direction == "in":
            pattern = f"(related)-[{rel_pattern}*1..{max_depth}]->(n)"
        else:
            pattern = f"(n)-[{rel_pattern}*1..{max_depth}]-(related)"

        query = f"""
        MATCH {pattern}
        WHERE n.id = $node_id
        RETURN DISTINCT related
        LIMIT 100
        """

        result = self.execute_query(query, {"node_id": node_id})
        return [record["related"] for record in result]

    def vector_search(
        self,
        query_embedding: List[float],
        node_types: Optional[List[str]] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """向量搜索

        Args:
            query_embedding: 查询向量
            node_types: 节点类型过滤
            limit: 返回数量

        Returns:
            list: 搜索结果
        """
        # 注意：这需要Neo4j的向量索引支持
        # 这里提供一个基本实现，实际使用时需要根据Neo4j版本调整

        where_clause = ""
        if node_types:
            type_conditions = [f"n:{t}" for t in node_types]
            where_clause = "WHERE " + " OR ".join(type_conditions)

        query = f"""
        MATCH (n)
        {where_clause}
        WHERE n.embedding IS NOT NULL
        WITH n,
             gds.similarity.cosine(n.embedding, $embedding) AS similarity
        ORDER BY similarity DESC
        LIMIT $limit
        RETURN n, similarity
        """

        try:
            result = self.execute_query(query, {
                "embedding": query_embedding,
                "limit": limit
            })
            return result
        except Exception as e:
            logger.warning(f"Vector search failed (may need vector index): {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息

        Returns:
            dict: 统计信息
        """
        try:
            # 获取节点数量
            node_count_query = "MATCH (n) RETURN count(n) as count"
            node_count = self.execute_query(node_count_query)[0]["count"]

            # 获取关系数量
            rel_count_query = "MATCH ()-[r]->() RETURN count(r) as count"
            rel_count = self.execute_query(rel_count_query)[0]["count"]

            # 获取标签统计
            label_query = """
            CALL db.labels() YIELD label
            RETURN collect(label) as labels
            """
            labels = self.execute_query(label_query)[0]["labels"]

            return {
                "node_count": node_count,
                "relationship_count": rel_count,
                "labels": labels,
                "connected": self._connected
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "node_count": 0,
                "relationship_count": 0,
                "labels": [],
                "connected": False,
                "error": str(e)
            }

    def clear_database(self):
        """清空数据库（谨慎使用）"""
        query = "MATCH (n) DETACH DELETE n"
        self.execute_write(query)
        logger.warning("Neo4j database cleared")
