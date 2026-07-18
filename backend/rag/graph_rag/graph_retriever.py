"""Graph Retriever — graph-based retrieval for enhanced RAG.

Provides graph traversal and retrieval capabilities.
"""

import logging
from typing import List, Dict, Any, Optional

from backend.rag.graph_rag.neo4j_store import Neo4jStore

logger = logging.getLogger(__name__)


class GraphRetriever:
    """图检索器"""

    def __init__(self, neo4j_store: Optional[Neo4jStore] = None):
        """初始化检索器

        Args:
            neo4j_store: Neo4j存储实例
        """
        self.neo4j = neo4j_store or Neo4jStore()

    async def find_related_tables(
        self,
        table_name: str,
        max_depth: int = 2
    ) -> List[Dict[str, Any]]:
        """查找关联表

        Args:
            table_name: 表名
            max_depth: 最大深度

        Returns:
            list: 关联表列表
        """
        try:
            query = """
            MATCH path = (t:Table {name: $table_name})-[:JOIN*1..$max_depth]-(related:Table)
            WITH related, min(length(path)) as distance
            RETURN related.name as name,
                   related.comment as comment,
                   related.business_desc as business_desc,
                   distance
            ORDER BY distance, related.name
            LIMIT 20
            """

            result = self.neo4j.execute_query(query, {
                "table_name": table_name,
                "max_depth": max_depth
            })

            return result

        except Exception as e:
            logger.error(f"Failed to find related tables: {e}")
            return []

    async def find_path_between_tables(
        self,
        start_table: str,
        end_table: str,
        max_length: int = 4
    ) -> List[Dict[str, Any]]:
        """查找两个表之间的路径

        Args:
            start_table: 起始表
            end_table: 结束表
            max_length: 最大路径长度

        Returns:
            list: 路径列表
        """
        try:
            query = """
            MATCH paths = (t1:Table {name: $start_table})-[:JOIN*1..$max_length]-(t2:Table {name: $end_table})
            RETURN [n in nodes(paths) | n.name] as path_nodes,
                   [r in relationships(paths) | r.join_type] as join_types,
                   length(paths) as path_length
            ORDER BY path_length
            LIMIT 5
            """

            result = self.neo4j.execute_query(query, {
                "start_table": start_table,
                "end_table": end_table,
                "max_length": max_length
            })

            return result

        except Exception as e:
            logger.error(f"Failed to find path between tables: {e}")
            return []

    async def get_table_importance(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取表重要性排名

        Args:
            limit: 返回数量

        Returns:
            list: 表重要性列表
        """
        try:
            query = """
            MATCH (t:Table)
            OPTIONAL MATCH (t)-[r:JOIN]-()
            WITH t, count(r) as connections
            RETURN t.name as name,
                   t.comment as comment,
                   connections
            ORDER BY connections DESC
            LIMIT $limit
            """

            result = self.neo4j.execute_query(query, {"limit": limit})
            return result

        except Exception as e:
            logger.error(f"Failed to get table importance: {e}")
            return []

    async def find_tables_by_column(
        self,
        column_name: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """根据字段名查找表

        Args:
            column_name: 字段名
            limit: 返回数量

        Returns:
            list: 表列表
        """
        try:
            query = """
            MATCH (c:Column)
            WHERE c.name CONTAINS $column_name
            MATCH (t:Table)-[:HAS_COLUMN]->(c)
            RETURN DISTINCT t.name as name,
                   t.comment as comment,
                   collect(c.name) as columns
            LIMIT $limit
            """

            result = self.neo4j.execute_query(query, {
                "column_name": column_name,
                "limit": limit
            })

            return result

        except Exception as e:
            logger.error(f"Failed to find tables by column: {e}")
            return []

    async def find_term_mappings(
        self,
        term_name: str
    ) -> List[Dict[str, Any]]:
        """查找术语映射

        Args:
            term_name: 术语名称

        Returns:
            list: 映射列表
        """
        try:
            query = """
            MATCH (t:Term)
            WHERE t.name_cn CONTAINS $term_name
                   OR t.name_en CONTAINS $term_name
                   OR t.aliases CONTAINS $term_name
            MATCH (t)-[:MAPS_TO]->(c:Column)
            MATCH (tab:Table)-[:HAS_COLUMN]->(c)
            RETURN t.name_cn as term_name,
                   t.description as description,
                   t.calculation as calculation,
                   tab.name as table_name,
                   c.name as column_name,
                   c.data_type as data_type
            """

            result = self.neo4j.execute_query(query, {"term_name": term_name})
            return result

        except Exception as e:
            logger.error(f"Failed to find term mappings: {e}")
            return []

    async def search_nodes(
        self,
        query: str,
        node_types: Optional[List[str]] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """搜索节点

        Args:
            query: 搜索关键词
            node_types: 节点类型过滤
            limit: 返回数量

        Returns:
            list: 节点列表
        """
        try:
            type_filter = ""
            if node_types:
                type_conditions = [f"n:{t}" for t in node_types]
                type_filter = "WHERE " + " OR ".join(type_conditions)

            cypher_query = f"""
            MATCH (n)
            {type_filter}
            WHERE n.name CONTAINS $query
                   OR n.comment CONTAINS $query
                   OR n.description CONTAINS $query
            RETURN n,
                   labels(n) as types
            LIMIT $limit
            """

            result = self.neo4j.execute_query(cypher_query, {
                "query": query,
                "limit": limit
            })

            return result

        except Exception as e:
            logger.error(f"Failed to search nodes: {e}")
            return []

    async def get_context_for_query(
        self,
        query: str,
        max_tables: int = 5,
        max_depth: int = 2
    ) -> Dict[str, Any]:
        """获取查询的上下文信息

        Args:
            query: 查询文本
            max_tables: 最大表数量
            max_depth: 最大遍历深度

        Returns:
            dict: 上下文信息
        """
        try:
            # 1. 搜索相关节点
            nodes = await self.search_nodes(query, limit=10)

            # 2. 提取表名
            table_names = set()
            for node in nodes:
                n = node.get("n", {})
                if "Table" in node.get("types", []):
                    table_names.add(n.get("name"))
                elif "Column" in node.get("types", []):
                    table_names.add(n.get("table_name"))

            # 3. 获取关联表
            related_tables = []
            for table_name in list(table_names)[:max_tables]:
                related = await self.find_related_tables(table_name, max_depth)
                related_tables.extend(related)

            # 4. 获取表重要性
            important_tables = await self.get_table_importance(10)

            return {
                "direct_tables": list(table_names),
                "related_tables": related_tables,
                "important_tables": important_tables,
                "nodes": nodes
            }

        except Exception as e:
            logger.error(f"Failed to get context for query: {e}")
            return {
                "direct_tables": [],
                "related_tables": [],
                "important_tables": [],
                "nodes": []
            }

    async def find_business_domain_tables(
        self,
        domain: str
    ) -> List[Dict[str, Any]]:
        """查找业务领域的表

        Args:
            domain: 业务领域

        Returns:
            list: 表列表
        """
        try:
            query = """
            MATCH (t:Table)
            WHERE t.business_desc CONTAINS $domain
                   OR t.comment CONTAINS $domain
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
            RETURN t.name as name,
                   t.comment as comment,
                   t.business_desc as business_desc,
                   count(c) as column_count
            ORDER BY column_count DESC
            LIMIT 20
            """

            result = self.neo4j.execute_query(query, {"domain": domain})
            return result

        except Exception as e:
            logger.error(f"Failed to find business domain tables: {e}")
            return []

    async def get_table_dependencies(
        self,
        table_name: str
    ) -> Dict[str, Any]:
        """获取表依赖关系

        Args:
            table_name: 表名

        Returns:
            dict: 依赖关系
        """
        try:
            # 入度：被哪些表依赖
            in_query = """
            MATCH (t:Table {name: $table_name})<-[:JOIN]-(dependent:Table)
            RETURN collect(dependent.name) as dependents
            """
            in_result = self.neo4j.execute_query(in_query, {"table_name": table_name})
            dependents = in_result[0]["dependents"] if in_result else []

            # 出度：依赖哪些表
            out_query = """
            MATCH (t:Table {name: $table_name})-[:JOIN]->(dependency:Table)
            RETURN collect(dependency.name) as dependencies
            """
            out_result = self.neo4j.execute_query(out_query, {"table_name": table_name})
            dependencies = out_result[0]["dependencies"] if out_result else []

            return {
                "table": table_name,
                "dependents": dependents,
                "dependencies": dependencies,
                "dependent_count": len(dependents),
                "dependency_count": len(dependencies)
            }

        except Exception as e:
            logger.error(f"Failed to get table dependencies: {e}")
            return {
                "table": table_name,
                "dependents": [],
                "dependencies": [],
                "dependent_count": 0,
                "dependency_count": 0
            }
