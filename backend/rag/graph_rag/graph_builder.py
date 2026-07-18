"""Graph Builder — build knowledge graph from metadata.

Constructs Neo4j knowledge graph from database metadata.
"""

import logging
from typing import List, Dict, Any, Optional

from backend.rag.graph_rag.neo4j_store import Neo4jStore

logger = logging.getLogger(__name__)


class GraphBuilder:
    """知识图谱构建器"""

    def __init__(self, neo4j_store: Optional[Neo4jStore] = None):
        """初始化构建器

        Args:
            neo4j_store: Neo4j存储实例
        """
        self.neo4j = neo4j_store or Neo4jStore()

    async def build_from_metadata(self, datasource_id: int = 0) -> Dict[str, Any]:
        """从元数据构建知识图谱

        Args:
            datasource_id: 数据源ID

        Returns:
            dict: 构建结果
        """
        try:
            logger.info(f"Building knowledge graph for datasource: {datasource_id}")

            # 清空现有图谱（可选）
            # self.neo4j.clear_database()

            # 构建表节点
            tables = await self._build_table_nodes(datasource_id)
            logger.info(f"Created {len(tables)} table nodes")

            # 构建字段节点
            columns = await self._build_column_nodes(datasource_id)
            logger.info(f"Created {len(columns)} column nodes")

            # 构建业务术语节点
            terms = await self._build_term_nodes(datasource_id)
            logger.info(f"Created {len(terms)} term nodes")

            # 构建关系
            relations = await self._build_relations(datasource_id)
            logger.info(f"Created {len(relations)} relations")

            return {
                "success": True,
                "tables": len(tables),
                "columns": len(columns),
                "terms": len(terms),
                "relations": len(relations)
            }

        except Exception as e:
            logger.error(f"Failed to build knowledge graph: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _build_table_nodes(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建表节点

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的节点列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 查询表信息
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    cur.execute(f"""
                        SELECT table_name, table_comment, table_business_desc
                        FROM adh_table_info
                        WHERE is_active = 1 {ds_filter}
                    """)

                    tables = []
                    for row in cur.fetchall():
                        # 创建节点
                        properties = {
                            "id": f"table:{row['table_name']}",
                            "name": row["table_name"],
                            "comment": row.get("table_comment", ""),
                            "business_desc": row.get("table_business_desc", ""),
                            "type": "table",
                            "datasource_id": datasource_id
                        }

                        self.neo4j.create_node("Table", properties)
                        tables.append(properties)

                    return tables
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Failed to build table nodes: {e}")
            return []

    async def _build_column_nodes(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建字段节点

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的节点列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 查询字段信息
                    ds_filter = f"AND (c.datasource_id = {datasource_id} OR c.datasource_id = 0)" if datasource_id else ""
                    cur.execute(f"""
                        SELECT c.table_name, c.column_name, c.data_type,
                               c.column_comment, c.business_desc, c.is_key
                        FROM adh_column_metadata c
                        WHERE c.is_active = 1 {ds_filter}
                    """)

                    columns = []
                    for row in cur.fetchall():
                        # 创建节点
                        properties = {
                            "id": f"col:{row['table_name']}.{row['column_name']}",
                            "name": row["column_name"],
                            "table_name": row["table_name"],
                            "data_type": row["data_type"],
                            "comment": row.get("column_comment", ""),
                            "business_desc": row.get("business_desc", ""),
                            "is_key": row.get("is_key", "false"),
                            "type": "column",
                            "datasource_id": datasource_id
                        }

                        self.neo4j.create_node("Column", properties)
                        columns.append(properties)

                    return columns
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Failed to build column nodes: {e}")
            return []

    async def _build_term_nodes(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建业务术语节点

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的节点列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 查询业务术语
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    cur.execute(f"""
                        SELECT term_cn, term_en, term_aliases, target_table,
                               target_column, calculation, description
                        FROM adh_business_terms
                        WHERE is_active = 1 {ds_filter}
                    """)

                    terms = []
                    for row in cur.fetchall():
                        # 创建节点
                        properties = {
                            "id": f"term:{row['term_cn']}",
                            "name_cn": row["term_cn"],
                            "name_en": row.get("term_en", ""),
                            "aliases": row.get("term_aliases", ""),
                            "target_table": row.get("target_table", ""),
                            "target_column": row.get("target_column", ""),
                            "calculation": row.get("calculation", ""),
                            "description": row.get("description", ""),
                            "type": "term",
                            "datasource_id": datasource_id
                        }

                        self.neo4j.create_node("Term", properties)
                        terms.append(properties)

                    return terms
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Failed to build term nodes: {e}")
            return []

    async def _build_relations(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建关系

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的关系列表
        """
        try:
            relations = []

            # 1. 表-字段关系 (HAS_COLUMN)
            column_relations = await self._build_table_column_relations(datasource_id)
            relations.extend(column_relations)

            # 2. 表-表关系 (JOIN)
            join_relations = await self._build_table_join_relations(datasource_id)
            relations.extend(join_relations)

            # 3. 术语-字段关系 (MAPS_TO)
            term_relations = await self._build_term_column_relations(datasource_id)
            relations.extend(term_relations)

            return relations

        except Exception as e:
            logger.error(f"Failed to build relations: {e}")
            return []

    async def _build_table_column_relations(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建表-字段关系

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的关系列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 查询字段信息
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    cur.execute(f"""
                        SELECT table_name, column_name
                        FROM adh_column_metadata
                        WHERE is_active = 1 {ds_filter}
                    """)

                    relations = []
                    for row in cur.fetchall():
                        table_id = f"table:{row['table_name']}"
                        column_id = f"col:{row['table_name']}.{row['column_name']}"

                        # 创建关系
                        self.neo4j.create_relationship(
                            table_id,
                            column_id,
                            "HAS_COLUMN",
                            {"type": "has_column"}
                        )
                        relations.append({
                            "source": table_id,
                            "target": column_id,
                            "type": "HAS_COLUMN"
                        })

                    return relations
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Failed to build table-column relations: {e}")
            return []

    async def _build_table_join_relations(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建表-表关系

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的关系列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 查询表关系
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    cur.execute(f"""
                        SELECT source_table, source_column, target_table,
                               target_column, relation_type, join_type, description
                        FROM adh_table_relations
                        WHERE is_active = 1 {ds_filter}
                    """)

                    relations = []
                    for row in cur.fetchall():
                        source_id = f"table:{row['source_table']}"
                        target_id = f"table:{row['target_table']}"

                        # 创建关系（双向）
                        properties = {
                            "source_column": row["source_column"],
                            "target_column": row["target_column"],
                            "relation_type": row.get("relation_type", "1:N"),
                            "join_type": row.get("join_type", "INNER"),
                            "description": row.get("description", ""),
                            "type": "join"
                        }

                        self.neo4j.create_relationship(source_id, target_id, "JOIN", properties)
                        self.neo4j.create_relationship(target_id, source_id, "JOIN", properties)

                        relations.append({
                            "source": source_id,
                            "target": target_id,
                            "type": "JOIN"
                        })

                    return relations
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Failed to build table join relations: {e}")
            return []

    async def _build_term_column_relations(self, datasource_id: int) -> List[Dict[str, Any]]:
        """构建术语-字段关系

        Args:
            datasource_id: 数据源ID

        Returns:
            list: 创建的关系列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 查询业务术语
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    cur.execute(f"""
                        SELECT term_cn, target_table, target_column
                        FROM adh_business_terms
                        WHERE is_active = 1 AND target_table != '' AND target_column != '' {ds_filter}
                    """)

                    relations = []
                    for row in cur.fetchall():
                        term_id = f"term:{row['term_cn']}"
                        column_id = f"col:{row['target_table']}.{row['target_column']}"

                        # 创建关系
                        self.neo4j.create_relationship(
                            term_id,
                            column_id,
                            "MAPS_TO",
                            {"type": "maps_to"}
                        )
                        relations.append({
                            "source": term_id,
                            "target": column_id,
                            "type": "MAPS_TO"
                        })

                    return relations
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Failed to build term-column relations: {e}")
            return []

    async def add_document_nodes(
        self,
        documents: List[Dict[str, Any]]
    ) -> int:
        """添加文档节点到知识图谱

        Args:
            documents: 文档列表

        Returns:
            int: 添加的节点数量
        """
        count = 0

        for doc in documents:
            try:
                properties = {
                    "id": f"doc:{doc['id']}",
                    "title": doc["title"],
                    "content": doc.get("content", "")[:1000],  # 只保存摘要
                    "source": doc.get("source", ""),
                    "doc_type": doc.get("doc_type", ""),
                    "type": "document"
                }

                self.neo4j.create_node("Document", properties)
                count += 1

                # 关联到相关表
                if "related_tables" in doc:
                    for table_name in doc["related_tables"]:
                        table_id = f"table:{table_name}"
                        self.neo4j.create_relationship(
                            f"doc:{doc['id']}",
                            table_id,
                            "DESCRIBES",
                            {"type": "describes"}
                        )

            except Exception as e:
                logger.error(f"Failed to add document node: {e}")

        return count
