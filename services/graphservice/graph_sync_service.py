"""Graph Sync Service — automatic synchronization between MySQL and Oxigraph.

Provides event-driven sync when metadata changes. All operations use SPARQL
to write RDF triples into Oxigraph named graphs.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

from services.shared.common.rdf.namespaces import ADH_NS

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
    """图谱自动同步服务 — Oxigraph SPARQL backend."""

    def __init__(self):
        """初始化同步服务."""
        self._store = None
        self._sync_queue: asyncio.Queue = asyncio.Queue()
        self._is_processing = False

    @property
    def store(self):
        """懒加载 OxigraphStore."""
        if self._store is None:
            from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore
            self._store = OxigraphStore()
        return self._store

    async def emit_event(self, event_type: SyncEventType, data: Dict[str, Any]):
        """发送同步事件.

        Args:
            event_type: 事件类型
            data: 事件数据
        """
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        asyncio.create_task(self._process_event(event))

    async def _process_event(self, event: Dict[str, Any]):
        """处理同步事件."""
        event_type = event["type"]
        data = event["data"]

        try:
            logger.info("Processing graph sync event: %s", event_type)

            handlers = {
                SyncEventType.TABLE_CREATED: self._sync_table_created,
                SyncEventType.TABLE_UPDATED: self._sync_table_updated,
                SyncEventType.TABLE_DELETED: self._sync_table_deleted,
                SyncEventType.COLUMN_CREATED: self._sync_column_created,
                SyncEventType.COLUMN_UPDATED: self._sync_column_updated,
                SyncEventType.COLUMN_DELETED: self._sync_column_deleted,
                SyncEventType.RELATION_CREATED: self._sync_relation_created,
                SyncEventType.RELATION_UPDATED: self._sync_relation_updated,
                SyncEventType.RELATION_DELETED: self._sync_relation_deleted,
                SyncEventType.TERM_CREATED: self._sync_term_created,
                SyncEventType.TERM_UPDATED: self._sync_term_updated,
                SyncEventType.TERM_DELETED: self._sync_term_deleted,
                SyncEventType.METRIC_CREATED: self._sync_metric_created,
                SyncEventType.METRIC_UPDATED: self._sync_metric_updated,
                SyncEventType.METRIC_DELETED: self._sync_metric_deleted,
                SyncEventType.DIMENSION_CREATED: self._sync_dimension_created,
                SyncEventType.DIMENSION_UPDATED: self._sync_dimension_updated,
                SyncEventType.DIMENSION_DELETED: self._sync_dimension_deleted,
                SyncEventType.FULL_SYNC: self._full_sync,
            }

            handler = handlers.get(event_type)
            if handler:
                handler(data)

            logger.info("Graph sync completed: %s", event_type)

        except Exception as e:
            logger.error("Graph sync failed for %s: %s", event_type, e, exc_info=True)

    # ── Table Sync ────────────────────────────────────────────────────

    def _sync_table_created(self, data: Dict[str, Any]):
        """同步新建的表."""
        table_name = data.get("table_name")
        if not table_name:
            return
        self.store.create_table_node(
            name=table_name,
            comment=data.get("table_comment", ""),
            business_desc=data.get("table_business_desc", ""),
            datasource_id=data.get("datasource_id", 0),
        )

    def _sync_table_updated(self, data: Dict[str, Any]):
        """同步更新的表."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        table_name = data.get("table_name")
        if not table_name:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}table:{_safe(table_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        # Update comment
        if "table_comment" in data:
            client.update(f"""
                DELETE {{ GRAPH <{graph}> {{ <{iri}> <{ADH_NS}comment> ?old }} }}
                WHERE {{ GRAPH <{graph}> {{ <{iri}> <{ADH_NS}comment> ?old }} }}
            """)
            client.update(f"""
                INSERT DATA {{ GRAPH <{graph}> {{ <{iri}> <{ADH_NS}comment> "{data['table_comment']}" }} }}
            """)

        if "table_business_desc" in data:
            client.update(f"""
                DELETE {{ GRAPH <{graph}> {{ <{iri}> <{ADH_NS}businessDesc> ?old }} }}
                WHERE {{ GRAPH <{graph}> {{ <{iri}> <{ADH_NS}businessDesc> ?old }} }}
            """)
            client.update(f"""
                INSERT DATA {{ GRAPH <{graph}> {{ <{iri}> <{ADH_NS}businessDesc> "{data['table_business_desc']}" }} }}
            """)

    def _sync_table_deleted(self, data: Dict[str, Any]):
        """同步删除的表."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        table_name = data.get("table_name")
        if not table_name:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}table:{_safe(table_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        # Delete all triples involving this node
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
            WHERE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
        """)
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
        """)

    # ── Column Sync ───────────────────────────────────────────────────

    def _sync_column_created(self, data: Dict[str, Any]):
        """同步新建的字段."""
        table_name = data.get("table_name")
        column_name = data.get("column_name")
        if not table_name or not column_name:
            return

        self.store.create_column_node(
            table=table_name,
            column=column_name,
            data_type=data.get("data_type", ""),
            comment=data.get("column_comment", ""),
            datasource_id=data.get("datasource_id", 0),
        )

    def _sync_column_updated(self, data: Dict[str, Any]):
        """同步更新的字段."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        table_name = data.get("table_name")
        column_name = data.get("column_name")
        if not table_name or not column_name:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}col:{_safe(table_name)}.{_safe(column_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        if "column_comment" in data or "data_type" in data:
            prop_map = {
                "column_comment": f"{ADH_NS}comment",
                "data_type": f"{ADH_NS}dataType",
            }
            for key, pred in prop_map.items():
                if key in data:
                    client.update(f"""
                        DELETE {{ GRAPH <{graph}> {{ <{iri}> <{pred}> ?old }} }}
                        WHERE {{ GRAPH <{graph}> {{ <{iri}> <{pred}> ?old }} }}
                    """)
                    client.update(f"""
                        INSERT DATA {{ GRAPH <{graph}> {{ <{iri}> <{pred}> "{data[key]}" }} }}
                    """)

    def _sync_column_deleted(self, data: Dict[str, Any]):
        """同步删除的字段."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        table_name = data.get("table_name")
        column_name = data.get("column_name")
        if not table_name or not column_name:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}col:{_safe(table_name)}.{_safe(column_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
            WHERE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
        """)
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
        """)

    # ── Relation Sync ─────────────────────────────────────────────────

    def _sync_relation_created(self, data: Dict[str, Any]):
        """同步新建的表关系."""
        source_table = data.get("source_table")
        target_table = data.get("target_table")
        if not source_table or not target_table:
            return

        self.store.create_join_relation(
            source_table, target_table,
            join_type=data.get("join_type", ""),
            datasource_id=data.get("datasource_id", 0),
        )

    def _sync_relation_updated(self, data: Dict[str, Any]):
        """同步更新的表关系."""
        self._sync_relation_deleted(data)
        self._sync_relation_created(data)

    def _sync_relation_deleted(self, data: Dict[str, Any]):
        """同步删除的表关系."""
        source_table = data.get("source_table")
        target_table = data.get("target_table")
        if not source_table or not target_table:
            return

        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        from services.shared.common.rdf.namespaces import ADH_NS
        ds_id = data.get("datasource_id", 0)
        graph = self.store.graph_uri(ds_id)
        src_iri = f"{ADH_NS}table:{_safe(source_table)}"
        tgt_iri = f"{ADH_NS}table:{_safe(target_table)}"
        client = self.store._client

        # Delete both directions
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{src_iri}> <{ADH_NS}join> <{tgt_iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ <{src_iri}> <{ADH_NS}join> <{tgt_iri}> }} }}
        """)
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{tgt_iri}> <{ADH_NS}join> <{src_iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ <{tgt_iri}> <{ADH_NS}join> <{src_iri}> }} }}
        """)

    # ── Term Sync ─────────────────────────────────────────────────────

    def _sync_term_created(self, data: Dict[str, Any]):
        """同步新建的术语."""
        term_cn = data.get("term_cn")
        if not term_cn:
            return

        ds_id = data.get("datasource_id", 0)
        self.store.create_term_node(
            name_cn=term_cn,
            name_en=data.get("term_en", ""),
            description=data.get("description", ""),
            calculation=data.get("calculation", ""),
            datasource_id=ds_id,
        )

        # Create MAPS_TO if target specified
        target_table = data.get("target_table", "")
        target_column = data.get("target_column", "")
        if target_table and target_column:
            self.store.create_term_mapping(term_cn, target_table, target_column, ds_id)

    def _sync_term_updated(self, data: Dict[str, Any]):
        """同步更新的术语."""
        term_cn = data.get("term_cn")
        if not term_cn:
            return
        self._sync_term_deleted({"term_cn": term_cn, "datasource_id": data.get("datasource_id", 0)})
        self._sync_term_created(data)

    def _sync_term_deleted(self, data: Dict[str, Any]):
        """同步删除的术语."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        term_cn = data.get("term_cn")
        if not term_cn:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}term:{_safe(term_cn)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
            WHERE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
        """)
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
        """)

    # ── Metric Sync ───────────────────────────────────────────────────

    def _sync_metric_created(self, data: Dict[str, Any]):
        """同步新建的指标."""
        metric_name = data.get("name")
        if not metric_name:
            return

        ds_id = data.get("datasource_id", 0)
        self.store.create_metric_node(
            metric_name,
            description=data.get("description", ""),
            datasource_id=ds_id,
        )

        # Create DEFINES if target specified
        target_table = data.get("target_table", "")
        target_column = data.get("target_column", "")
        if target_table and target_column:
            self.store.create_metric_column_relation(
                metric_name, target_table, target_column, ds_id
            )

    def _sync_metric_updated(self, data: Dict[str, Any]):
        """同步更新的指标."""
        metric_name = data.get("name")
        if not metric_name:
            return
        self._sync_metric_deleted({"name": metric_name, "datasource_id": data.get("datasource_id", 0)})
        self._sync_metric_created(data)

    def _sync_metric_deleted(self, data: Dict[str, Any]):
        """同步删除的指标."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        metric_name = data.get("name")
        if not metric_name:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}metric:{_safe(metric_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
            WHERE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
        """)
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
        """)

    # ── Dimension Sync ────────────────────────────────────────────────

    def _sync_dimension_created(self, data: Dict[str, Any]):
        """同步新建的维度."""
        dim_name = data.get("name")
        if not dim_name:
            return

        ds_id = data.get("datasource_id", 0)
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        from services.shared.common.rdf.namespaces import ADH_NS
        iri = f"{ADH_NS}dimension:{_safe(dim_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Dimension> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{dim_name}" ;
                        <{ADH_NS}comment> "{data.get('description', '')}" ;
                        <{ADH_NS}hierarchy> "{data.get('hierarchy', '')}" .
                }}
            }}
        """)

    def _sync_dimension_updated(self, data: Dict[str, Any]):
        """同步更新的维度."""
        dim_name = data.get("name")
        if not dim_name:
            return
        self._sync_dimension_deleted({"name": dim_name, "datasource_id": data.get("datasource_id", 0)})
        self._sync_dimension_created(data)

    def _sync_dimension_deleted(self, data: Dict[str, Any]):
        """同步删除的维度."""
        from services.datamind.rag.graph_rag.oxigraph_store import _safe
        from services.shared.common.rdf.namespaces import ADH_NS
        dim_name = data.get("name")
        if not dim_name:
            return

        ds_id = data.get("datasource_id", 0)
        iri = f"{ADH_NS}dimension:{_safe(dim_name)}"
        graph = self.store.graph_uri(ds_id)
        client = self.store._client

        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
            WHERE {{ GRAPH <{graph}> {{ <{iri}> ?p ?o }} }}
        """)
        client.update(f"""
            DELETE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
            WHERE {{ GRAPH <{graph}> {{ ?s ?p <{iri}> }} }}
        """)

    # ── Full Sync ─────────────────────────────────────────────────────

    def _full_sync(self, data: Dict[str, Any]):
        """全量同步."""
        datasource_id = data.get("datasource_id", 0)
        result = self.store.clear_graph(datasource_id)
        result = self.builder.build_from_metadata(datasource_id)
        logger.info("Full sync completed: %s", result)

    @property
    def builder(self):
        from services.datamind.rag.graph_rag.graph_builder import GraphBuilder
        return GraphBuilder(store=self.store)


# ── Singleton instance ─────────────────────────────────────────────────

_sync_service: Optional[GraphSyncService] = None


def get_graph_sync_service() -> GraphSyncService:
    """获取图谱同步服务单例."""
    global _sync_service
    if _sync_service is None:
        _sync_service = GraphSyncService()
    return _sync_service
