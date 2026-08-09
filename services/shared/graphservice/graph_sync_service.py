"""Graph Sync Service — automatic synchronization between MySQL and Neo4j.

Provides event-driven sync when metadata changes.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class SyncEventType(str, Enum):
    """同步事件类型"""
    TABLE_CREATED = "table_created"
    TABLE_UPDATED = "table_updated"
    TABLE_DELETED = "table_deleted"
    COLUMN_CREATED = "column_created"
    COLUMN_UPDATED = "column_updated"
    COLUMN_DELETED = "column_deleted"
    RELATION_CREATED = "relation_created"
    RELATION_UPDATED = "relation_updated"
    RELATION_DELETED = "relation_deleted"
    TERM_CREATED = "term_created"
    TERM_UPDATED = "term_updated"
    TERM_DELETED = "term_deleted"
    METRIC_CREATED = "metric_created"
    METRIC_UPDATED = "metric_updated"
    METRIC_DELETED = "metric_deleted"
    DIMENSION_CREATED = "dimension_created"
    DIMENSION_UPDATED = "dimension_updated"
    DIMENSION_DELETED = "dimension_deleted"
    FULL_SYNC = "full_sync"


class GraphSyncService:
    """图谱自动同步服务"""

    def __init__(self):
        """初始化同步服务"""
        self._neo4j = None
        self._sync_queue: asyncio.Queue = asyncio.Queue()
        self._is_processing = False

    @property
    def neo4j(self):
        """懒加载Neo4j连接"""
        if self._neo4j is None:
            from services.datamind.rag.graph_rag.neo4j_store import Neo4jStore
            self._neo4j = Neo4jStore()
        return self._neo4j

    async def emit_event(self, event_type: SyncEventType, data: Dict[str, Any]):
        """发送同步事件

        Args:
            event_type: 事件类型
            data: 事件数据
        """
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }

        # 异步处理，不阻塞调用方
        asyncio.create_task(self._process_event(event))

    async def _process_event(self, event: Dict[str, Any]):
        """处理同步事件

        Args:
            event: 事件数据
        """
        event_type = event["type"]
        data = event["data"]

        try:
            logger.info(f"Processing graph sync event: {event_type}")

            if event_type == SyncEventType.TABLE_CREATED:
                await self._sync_table_created(data)
            elif event_type == SyncEventType.TABLE_UPDATED:
                await self._sync_table_updated(data)
            elif event_type == SyncEventType.TABLE_DELETED:
                await self._sync_table_deleted(data)
            elif event_type == SyncEventType.COLUMN_CREATED:
                await self._sync_column_created(data)
            elif event_type == SyncEventType.COLUMN_UPDATED:
                await self._sync_column_updated(data)
            elif event_type == SyncEventType.COLUMN_DELETED:
                await self._sync_column_deleted(data)
            elif event_type == SyncEventType.RELATION_CREATED:
                await self._sync_relation_created(data)
            elif event_type == SyncEventType.RELATION_UPDATED:
                await self._sync_relation_updated(data)
            elif event_type == SyncEventType.RELATION_DELETED:
                await self._sync_relation_deleted(data)
            elif event_type == SyncEventType.TERM_CREATED:
                await self._sync_term_created(data)
            elif event_type == SyncEventType.TERM_UPDATED:
                await self._sync_term_updated(data)
            elif event_type == SyncEventType.TERM_DELETED:
                await self._sync_term_deleted(data)
            elif event_type == SyncEventType.METRIC_CREATED:
                await self._sync_metric_created(data)
            elif event_type == SyncEventType.METRIC_UPDATED:
                await self._sync_metric_updated(data)
            elif event_type == SyncEventType.METRIC_DELETED:
                await self._sync_metric_deleted(data)
            elif event_type == SyncEventType.DIMENSION_CREATED:
                await self._sync_dimension_created(data)
            elif event_type == SyncEventType.DIMENSION_UPDATED:
                await self._sync_dimension_updated(data)
            elif event_type == SyncEventType.DIMENSION_DELETED:
                await self._sync_dimension_deleted(data)
            elif event_type == SyncEventType.FULL_SYNC:
                await self._full_sync(data)

            logger.info(f"Graph sync completed: {event_type}")

        except Exception as e:
            logger.error(f"Graph sync failed for {event_type}: {e}", exc_info=True)

    # ── Table Sync ────────────────────────────────────────────────────

    async def _sync_table_created(self, data: Dict[str, Any]):
        """同步新建的表"""
        table_name = data.get("table_name")
        if not table_name:
            return

        properties = {
            "id": f"table:{table_name}",
            "name": table_name,
            "comment": data.get("table_comment", ""),
            "business_desc": data.get("table_business_desc", ""),
            "type": "table",
            "datasource_id": data.get("datasource_id", 0)
        }

        self.neo4j.create_node("Table", properties)

    async def _sync_table_updated(self, data: Dict[str, Any]):
        """同步更新的表"""
        table_name = data.get("table_name")
        if not table_name:
            return

        # 更新节点属性
        set_clauses = []
        params = {"node_id": f"table:{table_name}"}

        for key in ["table_comment", "table_business_desc"]:
            if key in data:
                prop_name = key.replace("table_", "")
                set_clauses.append(f"n.{prop_name} = ${prop_name}")
                params[prop_name] = data[key]

        if set_clauses:
            query = f"""
            MATCH (n {{id: $node_id}})
            SET {', '.join(set_clauses)}
            """
            self.neo4j.execute_query(query, params)

    async def _sync_table_deleted(self, data: Dict[str, Any]):
        """同步删除的表"""
        table_name = data.get("table_name")
        if not table_name:
            return

        query = """
        MATCH (n {id: $node_id})
        DETACH DELETE n
        """
        self.neo4j.execute_write(query, {"node_id": f"table:{table_name}"})

    # ── Column Sync ───────────────────────────────────────────────────

    async def _sync_column_created(self, data: Dict[str, Any]):
        """同步新建的字段"""
        table_name = data.get("table_name")
        column_name = data.get("column_name")
        if not table_name or not column_name:
            return

        # 创建字段节点
        properties = {
            "id": f"col:{table_name}.{column_name}",
            "name": column_name,
            "table_name": table_name,
            "data_type": data.get("data_type", ""),
            "comment": data.get("column_comment", ""),
            "business_desc": data.get("business_desc", ""),
            "is_key": data.get("is_key", "false"),
            "type": "column",
            "datasource_id": data.get("datasource_id", 0)
        }

        self.neo4j.create_node("Column", properties)

        # 创建表-字段关系
        table_id = f"table:{table_name}"
        column_id = f"col:{table_name}.{column_name}"
        self.neo4j.create_relationship(table_id, column_id, "HAS_COLUMN", {"type": "has_column"})

    async def _sync_column_updated(self, data: Dict[str, Any]):
        """同步更新的字段"""
        table_name = data.get("table_name")
        column_name = data.get("column_name")
        if not table_name or not column_name:
            return

        set_clauses = []
        params = {"node_id": f"col:{table_name}.{column_name}"}

        for key in ["data_type", "column_comment", "business_desc", "is_key"]:
            if key in data:
                prop_name = key.replace("column_", "")
                set_clauses.append(f"n.{prop_name} = ${prop_name}")
                params[prop_name] = data[key]

        if set_clauses:
            query = f"""
            MATCH (n {{id: $node_id}})
            SET {', '.join(set_clauses)}
            """
            self.neo4j.execute_query(query, params)

    async def _sync_column_deleted(self, data: Dict[str, Any]):
        """同步删除的字段"""
        table_name = data.get("table_name")
        column_name = data.get("column_name")
        if not table_name or not column_name:
            return

        query = """
        MATCH (n {id: $node_id})
        DETACH DELETE n
        """
        self.neo4j.execute_write(query, {"node_id": f"col:{table_name}.{column_name}"})

    # ── Relation Sync ─────────────────────────────────────────────────

    async def _sync_relation_created(self, data: Dict[str, Any]):
        """同步新建的表关系"""
        source_table = data.get("source_table")
        target_table = data.get("target_table")
        if not source_table or not target_table:
            return

        source_id = f"table:{source_table}"
        target_id = f"table:{target_table}"

        properties = {
            "source_column": data.get("source_column", ""),
            "target_column": data.get("target_column", ""),
            "relation_type": data.get("relation_type", "1:N"),
            "join_type": data.get("join_type", "INNER"),
            "description": data.get("description", ""),
            "type": "join"
        }

        # 创建双向关系
        self.neo4j.create_relationship(source_id, target_id, "JOIN", properties)
        self.neo4j.create_relationship(target_id, source_id, "JOIN", properties)

    async def _sync_relation_updated(self, data: Dict[str, Any]):
        """同步更新的表关系"""
        # 删除旧关系，创建新关系
        relation_id = data.get("id")
        if relation_id:
            # 先删除旧关系
            await self._sync_relation_deleted({"id": relation_id})
            # 再创建新关系
            await self._sync_relation_created(data)

    async def _sync_relation_deleted(self, data: Dict[str, Any]):
        """同步删除的表关系"""
        relation_id = data.get("id")
        if not relation_id:
            return

        # 从数据库获取关系信息
        try:
            from services.shared.common.db.metadata_db import get_metadata_conn
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT source_table, target_table
                        FROM adh_table_relations
                        WHERE id = %s
                    """, (relation_id,))
                    row = cur.fetchone()
                    if row:
                        source_id = f"table:{row['source_table']}"
                        target_id = f"table:{row['target_table']}"

                        # 删除双向关系
                        query = """
                        MATCH (a {id: $source_id})-[r:JOIN]->(b {id: $target_id})
                        DELETE r
                        """
                        self.neo4j.execute_write(query, {"source_id": source_id, "target_id": target_id})
                        self.neo4j.execute_write(query, {"source_id": target_id, "target_id": source_id})
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to delete relation from graph: {e}")

    # ── Term Sync ─────────────────────────────────────────────────────

    async def _sync_term_created(self, data: Dict[str, Any]):
        """同步新建的术语"""
        term_cn = data.get("term_cn")
        if not term_cn:
            return

        properties = {
            "id": f"term:{term_cn}",
            "name_cn": term_cn,
            "name_en": data.get("term_en", ""),
            "aliases": data.get("term_aliases", ""),
            "target_table": data.get("target_table", ""),
            "target_column": data.get("target_column", ""),
            "calculation": data.get("calculation", ""),
            "description": data.get("description", ""),
            "type": "term",
            "datasource_id": data.get("datasource_id", 0)
        }

        self.neo4j.create_node("Term", properties)

        # 如果有目标表和字段，创建MAPS_TO关系
        target_table = data.get("target_table", "")
        target_column = data.get("target_column", "")
        if target_table and target_column:
            term_id = f"term:{term_cn}"
            column_id = f"col:{target_table}.{target_column}"
            self.neo4j.create_relationship(term_id, column_id, "MAPS_TO", {"type": "maps_to"})

    async def _sync_term_updated(self, data: Dict[str, Any]):
        """同步更新的术语"""
        term_cn = data.get("term_cn")
        if not term_cn:
            return

        # 删除旧关系后重新创建
        await self._sync_term_deleted({"term_cn": term_cn})
        await self._sync_term_created(data)

    async def _sync_term_deleted(self, data: Dict[str, Any]):
        """同步删除的术语"""
        term_cn = data.get("term_cn")
        if not term_cn:
            return

        query = """
        MATCH (n {id: $node_id})
        DETACH DELETE n
        """
        self.neo4j.execute_write(query, {"node_id": f"term:{term_cn}"})

    # ── Metric Sync ───────────────────────────────────────────────────

    async def _sync_metric_created(self, data: Dict[str, Any]):
        """同步新建的指标"""
        metric_name = data.get("name")
        if not metric_name:
            return

        properties = {
            "id": f"metric:{metric_name}",
            "name": metric_name,
            "name_en": data.get("name_en", ""),
            "formula": data.get("formula", ""),
            "unit": data.get("unit", ""),
            "agg_type": data.get("agg_type", ""),
            "target_table": data.get("target_table", ""),
            "target_column": data.get("target_column", ""),
            "description": data.get("description", ""),
            "category": data.get("category", ""),
            "type": "metric",
            "datasource_id": data.get("datasource_id", 0)
        }

        self.neo4j.create_node("Metric", properties)

        # 如果有目标表和字段，创建DEFINES关系
        target_table = data.get("target_table", "")
        target_column = data.get("target_column", "")
        if target_table and target_column:
            metric_id = f"metric:{metric_name}"
            column_id = f"col:{target_table}.{target_column}"
            self.neo4j.create_relationship(metric_id, column_id, "DEFINES", {"type": "defines"})

    async def _sync_metric_updated(self, data: Dict[str, Any]):
        """同步更新的指标"""
        metric_id = data.get("id")
        if not metric_id:
            return

        # 获取指标名称
        try:
            from services.shared.common.db.metadata_db import get_metadata_conn
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM adh_metrics WHERE id = %s", (metric_id,))
                    row = cur.fetchone()
                    if row:
                        await self._sync_metric_deleted({"name": row["name"]})
                        data["name"] = row["name"]
                        await self._sync_metric_created(data)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to sync metric update: {e}")

    async def _sync_metric_deleted(self, data: Dict[str, Any]):
        """同步删除的指标"""
        metric_name = data.get("name")
        if not metric_name:
            return

        query = """
        MATCH (n {id: $node_id})
        DETACH DELETE n
        """
        self.neo4j.execute_write(query, {"node_id": f"metric:{metric_name}"})

    # ── Dimension Sync ────────────────────────────────────────────────

    async def _sync_dimension_created(self, data: Dict[str, Any]):
        """同步新建的维度"""
        dim_name = data.get("name")
        if not dim_name:
            return

        properties = {
            "id": f"dim:{dim_name}",
            "name": dim_name,
            "name_en": data.get("name_en", ""),
            "hierarchy": data.get("hierarchy", ""),
            "level": data.get("level", 0),
            "target_table": data.get("target_table", ""),
            "target_column": data.get("target_column", ""),
            "description": data.get("description", ""),
            "category": data.get("category", ""),
            "type": "dimension",
            "datasource_id": data.get("datasource_id", 0)
        }

        self.neo4j.create_node("Dimension", properties)

        # 如果有目标表和字段，创建BELONGS_TO关系
        target_table = data.get("target_table", "")
        target_column = data.get("target_column", "")
        if target_table and target_column:
            dim_id = f"dim:{dim_name}"
            column_id = f"col:{target_table}.{target_column}"
            self.neo4j.create_relationship(dim_id, column_id, "BELONGS_TO", {"type": "belongs_to"})

    async def _sync_dimension_updated(self, data: Dict[str, Any]):
        """同步更新的维度"""
        dim_id = data.get("id")
        if not dim_id:
            return

        # 获取维度名称
        try:
            from services.shared.common.db.metadata_db import get_metadata_conn
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM adh_dimensions WHERE id = %s", (dim_id,))
                    row = cur.fetchone()
                    if row:
                        await self._sync_dimension_deleted({"name": row["name"]})
                        data["name"] = row["name"]
                        await self._sync_dimension_created(data)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to sync dimension update: {e}")

    async def _sync_dimension_deleted(self, data: Dict[str, Any]):
        """同步删除的维度"""
        dim_name = data.get("name")
        if not dim_name:
            return

        query = """
        MATCH (n {id: $node_id})
        DETACH DELETE n
        """
        self.neo4j.execute_write(query, {"node_id": f"dim:{dim_name}"})

    # ── Full Sync ─────────────────────────────────────────────────────

    async def _full_sync(self, data: Dict[str, Any]):
        """全量同步"""
        datasource_id = data.get("datasource_id", 0)

        from services.datamind.rag.graph_rag.graph_builder import GraphBuilder
        builder = GraphBuilder(self.neo4j)

        result = await builder.build_from_metadata(datasource_id)
        logger.info(f"Full sync completed: {result}")


# ── Singleton instance ─────────────────────────────────────────────────

_sync_service: Optional[GraphSyncService] = None


def get_graph_sync_service() -> GraphSyncService:
    """获取图谱同步服务单例"""
    global _sync_service
    if _sync_service is None:
        _sync_service = GraphSyncService()
    return _sync_service
