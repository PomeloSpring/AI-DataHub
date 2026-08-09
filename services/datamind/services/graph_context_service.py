"""Graph Context Service — enhance RAG with knowledge graph context.

Provides graph-augmented retrieval for better AI responses.
"""

import logging
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field

from services.datamind.rag.graph_rag.neo4j_store import Neo4jStore

logger = logging.getLogger(__name__)


@dataclass
class GraphContext:
    """图谱上下文"""
    tables: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)
    terms: List[Dict[str, Any]] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    dimensions: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """转换为prompt上下文字符串"""
        sections = []

        # 表信息
        if self.tables:
            tables_str = "\n".join([
                f"- {t.get('name')}: {t.get('comment', '')} {t.get('business_desc', '')}"
                for t in self.tables
            ])
            sections.append(f"## 相关表\n{tables_str}")

        # 字段信息
        if self.columns:
            columns_str = "\n".join([
                f"- {c.get('table_name')}.{c.get('name')} ({c.get('data_type', '')}): {c.get('comment', '')}"
                for c in self.columns[:20]  # 限制数量
            ])
            sections.append(f"## 相关字段\n{columns_str}")

        # 术语信息
        if self.terms:
            terms_str = "\n".join([
                f"- {t.get('name_cn')}: {t.get('description', '')} → {t.get('target_table', '')}.{t.get('target_column', '')}"
                for t in self.terms
            ])
            sections.append(f"## 业务术语\n{terms_str}")

        # 指标信息
        if self.metrics:
            metrics_str = "\n".join([
                f"- {m.get('name')}: {m.get('formula', '')} ({m.get('unit', '')}) - {m.get('description', '')}"
                for m in self.metrics
            ])
            sections.append(f"## 业务指标\n{metrics_str}")

        # 维度信息
        if self.dimensions:
            dims_str = "\n".join([
                f"- {d.get('name')}: {d.get('description', '')} 层级={d.get('hierarchy', '')}"
                for d in self.dimensions
            ])
            sections.append(f"## 分析维度\n{dims_str}")

        # 关系信息
        if self.relations:
            rels_str = "\n".join([
                f"- {r.get('source')} --[{r.get('type')}]--> {r.get('target')}"
                for r in self.relations[:30]  # 限制数量
            ])
            sections.append(f"## 关联关系\n{rels_str}")

        return "\n\n".join(sections)

    def get_all_table_names(self) -> List[str]:
        """获取所有表名"""
        table_names = set()
        for t in self.tables:
            table_names.add(t.get('name', ''))
        for c in self.columns:
            if c.get('table_name'):
                table_names.add(c['table_name'])
        return list(table_names)


class GraphContextService:
    """图谱上下文服务"""

    def __init__(self, neo4j_store: Optional[Neo4jStore] = None):
        """初始化服务

        Args:
            neo4j_store: Neo4j存储实例
        """
        self.neo4j = neo4j_store or Neo4jStore()

    async def get_context_for_query(
        self,
        query: str,
        datasource_id: Optional[int] = None,
        max_tables: int = 10,
        max_depth: int = 2
    ) -> GraphContext:
        """获取查询的图谱上下文

        Args:
            query: 用户查询
            datasource_id: 数据源ID
            max_tables: 最大表数量
            max_depth: 最大遍历深度

        Returns:
            GraphContext: 图谱上下文
        """
        context = GraphContext()
        visited_ids: Set[str] = set()

        try:
            # Step 1: 搜索相关节点（多类型）
            search_results = await self._search_all_types(query, limit=20)

            # Step 2: 提取直接匹配的节点
            for result in search_results:
                node_id = result.get('id', '')
                if node_id in visited_ids:
                    continue
                visited_ids.add(node_id)

                node_type = result.get('type', '')
                if node_type == 'table':
                    context.tables.append(result)
                elif node_type == 'column':
                    context.columns.append(result)
                elif node_type == 'term':
                    context.terms.append(result)
                elif node_type == 'metric':
                    context.metrics.append(result)
                elif node_type == 'dimension':
                    context.dimensions.append(result)

            # Step 3: 图谱扩展 - 获取关联节点
            entry_nodes = list(visited_ids)[:5]  # 取前5个作为入口
            for node_id in entry_nodes:
                related = await self._get_related_nodes(node_id, max_depth)
                for rel_node in related:
                    rel_id = rel_node.get('id', '')
                    if rel_id not in visited_ids:
                        visited_ids.add(rel_id)
                        self._add_to_context(context, rel_node)

            # Step 4: 获取表间关系
            table_names = context.get_all_table_names()
            if table_names:
                relations = await self._get_table_relations(table_names[:max_tables])
                context.relations = relations

            # Step 5: 智能裁剪
            context = self._trim_context(context, max_tables)

            return context

        except Exception as e:
            logger.error(f"Failed to get graph context: {e}", exc_info=True)
            return context

    async def get_context_for_tables(
        self,
        table_names: List[str],
        datasource_id: Optional[int] = None
    ) -> GraphContext:
        """获取指定表的图谱上下文

        Args:
            table_names: 表名列表
            datasource_id: 数据源ID

        Returns:
            GraphContext: 图谱上下文
        """
        context = GraphContext()

        try:
            # 获取表信息
            for table_name in table_names[:10]:
                table_info = await self._get_table_info(table_name)
                if table_info:
                    context.tables.append(table_info)

                # 获取表的字段
                columns = await self._get_table_columns(table_name)
                context.columns.extend(columns)

                # 获取关联的术语
                terms = await self._get_terms_for_table(table_name)
                context.terms.extend(terms)

                # 获取关联的指标
                metrics = await self._get_metrics_for_table(table_name)
                context.metrics.extend(metrics)

            # 获取表间关系
            relations = await self._get_table_relations(table_names)
            context.relations = relations

            return context

        except Exception as e:
            logger.error(f"Failed to get context for tables: {e}", exc_info=True)
            return context

    async def _search_all_types(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """搜索所有类型的节点

        Args:
            query: 搜索词
            limit: 返回数量限制

        Returns:
            list: 搜索结果
        """
        try:
            # 搜索所有类型的节点
            cypher_query = """
            MATCH (n)
            WHERE n.name CONTAINS $query
               OR n.name_cn CONTAINS $query
               OR n.comment CONTAINS $query
               OR n.description CONTAINS $query
               OR n.business_desc CONTAINS $query
            RETURN n, labels(n)[0] as label
            LIMIT $limit
            """

            result = self.neo4j.execute_query(cypher_query, {
                "query": query,
                "limit": limit
            })

            nodes = []
            for record in result:
                node = record.get('n', {})
                label = record.get('label', 'Unknown')
                node['type'] = label.lower()
                nodes.append(node)

            return nodes

        except Exception as e:
            logger.error(f"Search all types failed: {e}")
            return []

    async def _get_related_nodes(
        self,
        node_id: str,
        max_depth: int = 2
    ) -> List[Dict[str, Any]]:
        """获取关联节点

        Args:
            node_id: 节点ID
            max_depth: 最大遍历深度

        Returns:
            list: 关联节点列表
        """
        try:
            query = """
            MATCH (n {id: $node_id})-[r*1..$max_depth]-(related)
            RETURN DISTINCT related, labels(related)[0] as label
            LIMIT 30
            """

            result = self.neo4j.execute_query(query, {
                "node_id": node_id,
                "max_depth": max_depth
            })

            nodes = []
            for record in result:
                node = record.get('related', {})
                label = record.get('label', 'Unknown')
                node['type'] = label.lower()
                nodes.append(node)

            return nodes

        except Exception as e:
            logger.error(f"Get related nodes failed: {e}")
            return []

    async def _get_table_info(self, table_name: str) -> Optional[Dict[str, Any]]:
        """获取表信息"""
        try:
            query = """
            MATCH (t:Table {name: $table_name})
            RETURN t
            """

            result = self.neo4j.execute_query(query, {"table_name": table_name})
            if result:
                node = result[0].get('t', {})
                node['type'] = 'table'
                return node
            return None

        except Exception as e:
            logger.error(f"Get table info failed: {e}")
            return None

    async def _get_table_columns(self, table_name: str) -> List[Dict[str, Any]]:
        """获取表的字段"""
        try:
            query = """
            MATCH (t:Table {name: $table_name})-[:HAS_COLUMN]->(c:Column)
            RETURN c
            ORDER BY c.name
            """

            result = self.neo4j.execute_query(query, {"table_name": table_name})

            columns = []
            for record in result:
                col = record.get('c', {})
                col['type'] = 'column'
                columns.append(col)

            return columns

        except Exception as e:
            logger.error(f"Get table columns failed: {e}")
            return []

    async def _get_terms_for_table(self, table_name: str) -> List[Dict[str, Any]]:
        """获取表关联的术语"""
        try:
            query = """
            MATCH (term:Term)-[:MAPS_TO]->(c:Column)<-[:HAS_COLUMN]-(t:Table {name: $table_name})
            RETURN DISTINCT term
            """

            result = self.neo4j.execute_query(query, {"table_name": table_name})

            terms = []
            for record in result:
                term = record.get('term', {})
                term['type'] = 'term'
                terms.append(term)

            return terms

        except Exception as e:
            logger.error(f"Get terms for table failed: {e}")
            return []

    async def _get_metrics_for_table(self, table_name: str) -> List[Dict[str, Any]]:
        """获取表关联的指标"""
        try:
            query = """
            MATCH (m:Metric)
            WHERE m.target_table = $table_name
            RETURN m
            """

            result = self.neo4j.execute_query(query, {"table_name": table_name})

            metrics = []
            for record in result:
                metric = record.get('m', {})
                metric['type'] = 'metric'
                metrics.append(metric)

            return metrics

        except Exception as e:
            logger.error(f"Get metrics for table failed: {e}")
            return []

    async def _get_table_relations(
        self,
        table_names: List[str]
    ) -> List[Dict[str, Any]]:
        """获取表间关系"""
        try:
            query = """
            MATCH (a:Table)-[r:JOIN]-(b:Table)
            WHERE a.name IN $table_names AND b.name IN $table_names
            RETURN a.name as source, b.name as target, type(r) as rel_type,
                   r.source_column as source_col, r.target_column as target_col,
                   r.join_type as join_type
            """

            result = self.neo4j.execute_query(query, {"table_names": table_names})

            relations = []
            seen = set()
            for record in result:
                # 去重（双向关系）
                key = tuple(sorted([record['source'], record['target']]))
                if key not in seen:
                    seen.add(key)
                    relations.append({
                        "source": record['source'],
                        "target": record['target'],
                        "type": record['rel_type'],
                        "source_column": record.get('source_col', ''),
                        "target_column": record.get('target_col', ''),
                        "join_type": record.get('join_type', 'INNER')
                    })

            return relations

        except Exception as e:
            logger.error(f"Get table relations failed: {e}")
            return []

    def _add_to_context(self, context: GraphContext, node: Dict[str, Any]):
        """将节点添加到上下文"""
        node_type = node.get('type', '')
        if node_type == 'table':
            context.tables.append(node)
        elif node_type == 'column':
            context.columns.append(node)
        elif node_type == 'term':
            context.terms.append(node)
        elif node_type == 'metric':
            context.metrics.append(node)
        elif node_type == 'dimension':
            context.dimensions.append(node)

    def _trim_context(self, context: GraphContext, max_tables: int) -> GraphContext:
        """裁剪上下文，保留最相关的内容"""
        # 去重
        context.tables = self._deduplicate(context.tables, 'id')[:max_tables]
        context.columns = self._deduplicate(context.columns, 'id')[:50]
        context.terms = self._deduplicate(context.terms, 'id')[:10]
        context.metrics = self._deduplicate(context.metrics, 'id')[:10]
        context.dimensions = self._deduplicate(context.dimensions, 'id')[:10]
        context.relations = context.relations[:30]

        return context

    def _deduplicate(self, items: List[Dict], key: str) -> List[Dict]:
        """去重"""
        seen = set()
        result = []
        for item in items:
            item_key = item.get(key)
            if item_key and item_key not in seen:
                seen.add(item_key)
                result.append(item)
        return result
