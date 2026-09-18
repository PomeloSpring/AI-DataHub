"""Graph Service — unified knowledge graph service.

Provides graph query, edit, and sync capabilities using Oxigraph/SPARQL.
"""

import logging
from typing import Optional, List, Dict, Any

from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore
from services.datamind.rag.graph_rag.graph_builder import GraphBuilder
from services.datamind.rag.graph_rag.graph_retriever import GraphRetriever
from services.shared.common.rdf.sparql_client import get_sparql_client
from services.shared.common.rdf.namespaces import ADH_NS, SPARQL_PREFIXES
from services.shared.models.graph import (
    GraphType, NodeType, RelationType, GraphNode, GraphEdge, GraphStats, GraphData,
    NodeDetailResponse, SyncResponse
)

logger = logging.getLogger(__name__)


class GraphService:
    """知识图谱服务 — Oxigraph SPARQL backend."""

    def __init__(self, store: OxigraphStore = None):
        self._store = store or OxigraphStore()
        self._client = get_sparql_client()
        self.builder = GraphBuilder(store=self._store)
        self.retriever = GraphRetriever(store=self._store)

    def _iri_to_node_id(self, iri: str) -> str:
        """Convert an RDF IRI to a display-friendly node ID."""
        return iri.replace(ADH_NS, "adh:")

    def _to_graph_node(self, iri: str, node_type: str,
                       label: str, properties: Dict[str, Any]) -> GraphNode:
        """Build a GraphNode from RDF data."""
        return GraphNode(
            id=self._iri_to_node_id(iri),
            label=node_type or label or "Unknown",
            properties=properties,
        )

    def _to_graph_edge(self, src_iri: str, tgt_iri: str,
                       rel_type: str, properties: Dict[str, Any] = None) -> GraphEdge:
        """Build a GraphEdge from RDF data."""
        return GraphEdge(
            id=f"{self._iri_to_node_id(src_iri)}-{rel_type}-{self._iri_to_node_id(tgt_iri)}",
            source=self._iri_to_node_id(src_iri),
            target=self._iri_to_node_id(tgt_iri),
            type=rel_type,
            properties=properties or {},
        )

    def get_graph_data(
        self,
        graph_type: GraphType = GraphType.TABLE_RELATION,
        datasource_id: Optional[int] = None,
        node_types: Optional[List[NodeType]] = None,
        max_depth: int = 2,
        center_node: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200
    ) -> GraphData:
        """获取图谱数据."""
        try:
            nodes = []
            edges = []
            ds_id = datasource_id or 0
            graph = self._store.graph_uri(ds_id)

            if graph_type == GraphType.TABLE_RELATION:
                nodes, edges = self._get_table_relation_graph(graph, ds_id, limit)
            elif graph_type == GraphType.BUSINESS_KNOWLEDGE:
                nodes, edges = self._get_business_knowledge_graph(graph, ds_id, limit)
            elif graph_type == GraphType.DATA_LINEAGE:
                nodes, edges = self._get_data_lineage_graph(graph, ds_id, limit)

            # Search filter
            if search and nodes:
                search_lower = search.lower()
                nodes = [
                    n for n in nodes
                    if search_lower in n.id.lower()
                    or search_lower in str(n.properties.get("label", "")).lower()
                    or search_lower in str(n.properties.get("comment", "")).lower()
                ]
                node_ids = {n.id for n in nodes}
                edges = [e for e in edges if e.source in node_ids and e.target in node_ids]

            stats = self._get_stats(ds_id)

            # 节点/边独立截断后, 剪掉端点已被丢弃的悬空边(各视图通用不变式)。
            final_nodes = nodes[:limit]
            kept_ids = {n.id for n in final_nodes}
            final_edges = [
                e for e in edges[:limit * 2]
                if e.source in kept_ids and e.target in kept_ids
            ]

            return GraphData(
                nodes=final_nodes,
                edges=final_edges,
                stats=stats
            )

        except Exception as e:
            logger.error("Failed to get graph data: %s", e, exc_info=True)
            return GraphData(stats=GraphStats(connected=False))

    def _get_table_relation_graph(
        self, graph: str, ds_id: int, limit: int
    ) -> tuple[List[GraphNode], List[GraphEdge]]:
        """获取表关系图数据."""
        nodes = []
        edges = []

        # Get Table nodes
        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?iri ?label ?comment WHERE {{
                GRAPH <{graph}> {{
                    ?iri a adh:Table ;
                        rdfs:label ?label .
                    OPTIONAL {{ ?iri adh:comment ?comment }}
                }}
            }}
            LIMIT {limit}
        """
        try:
            rows = self._client.query(sparql)
            for r in rows:
                iri = r.get("iri", "")
                nodes.append(self._to_graph_node(iri, "Table", r.get("label", ""), {
                    "label": r.get("label", ""),
                    "comment": r.get("comment", "") if r.get("comment") else "",
                }))
        except Exception as e:
            logger.warning("Table node query failed: %s", e)

        # Get Column nodes
        col_sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?iri ?label ?tableName ?dataType WHERE {{
                GRAPH <{graph}> {{
                    ?iri a adh:Column ;
                        rdfs:label ?label .
                    OPTIONAL {{ ?iri adh:tableName ?tableName }}
                    OPTIONAL {{ ?iri adh:dataType ?dataType }}
                }}
            }}
            LIMIT {limit}
        """
        try:
            rows = self._client.query(col_sparql)
            for r in rows:
                iri = r.get("iri", "")
                nodes.append(self._to_graph_node(iri, "Column", r.get("label", ""), {
                    "label": r.get("label", ""),
                    "table_name": r.get("tableName", "") if r.get("tableName") else "",
                    "data_type": r.get("dataType", "") if r.get("dataType") else "",
                }))
        except Exception as e:
            logger.warning("Column node query failed: %s", e)

        # Get hasColumn + join relations
        rel_sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?src ?tgt ?relType WHERE {{
                GRAPH <{graph}> {{
                    ?src ?relType ?tgt .
                    FILTER(?relType IN (adh:hasColumn, adh:join))
                }}
            }}
            LIMIT {limit * 2}
        """
        try:
            rows = self._client.query(rel_sparql)
            for r in rows:
                src = r.get("src", "")
                tgt = r.get("tgt", "")
                rel = r.get("relType", "").replace(ADH_NS, "")
                edges.append(self._to_graph_edge(src, tgt, rel))
        except Exception as e:
            logger.warning("Relation query failed: %s", e)

        return nodes, edges

    def _get_business_knowledge_graph(
        self, graph: str, ds_id: int, limit: int
    ) -> tuple[List[GraphNode], List[GraphEdge]]:
        """获取业务知识图数据."""
        nodes = []
        edges = []

        # Get Term, Metric nodes (+ binding badges from Phase 2b RDF predicates)
        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?iri ?type ?label ?comment ?qm ?sc ?st ?cr WHERE {{
                GRAPH <{graph}> {{
                    ?iri a ?type ;
                        rdfs:label ?label .
                    FILTER(?type IN (adh:Term, adh:Metric, adh:Dimension))
                    OPTIONAL {{ ?iri adh:comment ?comment }}
                    OPTIONAL {{ ?iri adh:queryMode ?qm }}
                    OPTIONAL {{ ?iri adh:sizeClass ?sc }}
                    OPTIONAL {{ ?iri adh:syncState ?st }}
                    OPTIONAL {{ ?iri adh:catalogRef ?cr }}
                }}
            }}
            LIMIT {limit}
        """
        try:
            rows = self._client.query(sparql)
            for r in rows:
                iri = r.get("iri", "")
                ntype = r.get("type", "").replace(ADH_NS, "")
                props: Dict[str, Any] = {
                    "label": r.get("label", ""),
                    "comment": r.get("comment", "") if r.get("comment") else "",
                }
                # Phase 5: binding 徽标(仅当对象绑过物理表时才有)
                if r.get("qm") or r.get("sc") or r.get("st"):
                    props["binding"] = {
                        "query_mode": r.get("qm", "") or "",
                        "size_class": r.get("sc", "") or "",
                        "sync_state": r.get("st", "") or "",
                        "catalog_ref": r.get("cr", "") or "",
                    }
                nodes.append(self._to_graph_node(iri, ntype, r.get("label", ""), props))
        except Exception as e:
            logger.warning("Business knowledge query failed: %s", e)

        # Get mapsTo / defines relations
        rel_sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?src ?tgt ?relType WHERE {{
                GRAPH <{graph}> {{
                    ?src ?relType ?tgt .
                    FILTER(?relType IN (adh:mapsTo, adh:defines, adh:belongsTo))
                }}
            }}
            LIMIT {limit}
        """
        try:
            rows = self._client.query(rel_sparql)
            for r in rows:
                src = r.get("src", "")
                tgt = r.get("tgt", "")
                rel = r.get("relType", "").replace(ADH_NS, "")
                edges.append(self._to_graph_edge(src, tgt, rel))
        except Exception as e:
            logger.warning("Business relation query failed: %s", e)

        return nodes, edges

    def _get_data_lineage_graph(
        self, graph: str, ds_id: int, limit: int
    ) -> tuple[List[GraphNode], List[GraphEdge]]:
        """获取数据血缘图数据 — 全链路语义->物理->源.

        Phase 5 (详设 E): ``Object -boundTo-> Table -hasColumn-> Column``,并派生
        ``Table -> DataSource(catalog)``。DataSource 边不落 RDF(源节点写在 graph 0),
        而是在视图层依 ``adh_table_info.datasource_id`` 关联,直观呈现
        "语义对象 -> 物理表 -> 数据源(含联邦 catalog)" 的可追溯链路。
        """
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        seen_nodes: set[str] = set()
        seen_edges: set[str] = set()
        bound_tables: Dict[str, str] = {}  # table_iri -> table_name
        # 列先缓存, 最后才入图: 保证 Object->Table->DataSource 骨架不被
        # get_graph_data 的 nodes[:limit] 截断(列为叶子, 可安全省略)。
        pending_columns: List[tuple] = []
        cols_per_table: Dict[str, int] = {}
        MAX_COLS_PER_TABLE = 30

        def _add_node(iri: str, ntype: str, label: str, props: Dict[str, Any]):
            nid = self._iri_to_node_id(iri)
            if nid in seen_nodes:
                return
            seen_nodes.add(nid)
            nodes.append(self._to_graph_node(iri, ntype, label, props))

        def _add_edge(src: str, tgt: str, rel: str, props: Dict[str, Any] = None):
            eid = f"{self._iri_to_node_id(src)}-{rel}-{self._iri_to_node_id(tgt)}"
            if eid in seen_edges:
                return
            seen_edges.add(eid)
            edges.append(self._to_graph_edge(src, tgt, rel, props))

        # 1) Object -boundTo-> Table -hasColumn-> Column (单次图查询)
        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?obj ?objLabel ?qm ?sc ?st ?cr ?table ?tableLabel ?col ?colLabel ?dataType WHERE {{
                GRAPH <{graph}> {{
                    ?obj adh:boundTo ?table .
                    OPTIONAL {{ ?obj rdfs:label ?objLabel }}
                    OPTIONAL {{ ?obj adh:queryMode ?qm }}
                    OPTIONAL {{ ?obj adh:sizeClass ?sc }}
                    OPTIONAL {{ ?obj adh:syncState ?st }}
                    OPTIONAL {{ ?obj adh:catalogRef ?cr }}
                    OPTIONAL {{ ?table rdfs:label ?tableLabel }}
                    OPTIONAL {{
                        ?table adh:hasColumn ?col .
                        ?col rdfs:label ?colLabel
                        OPTIONAL {{ ?col adh:dataType ?dataType }}
                    }}
                }}
            }}
            LIMIT {limit * 3}
        """
        try:
            rows = self._client.query(sparql)
            for r in rows:
                obj_iri = r.get("obj", "")
                table_iri = r.get("table", "")
                if not obj_iri or not table_iri:
                    continue
                obj_label = (r.get("objLabel") or "").strip() or obj_iri.replace(ADH_NS, "")
                obj_props: Dict[str, Any] = {"label": obj_label}
                binding: Dict[str, Any] = {}
                if r.get("qm"):
                    binding["query_mode"] = r["qm"]
                if r.get("sc"):
                    binding["size_class"] = r["sc"]
                if r.get("st"):
                    binding["sync_state"] = r["st"]
                if r.get("cr"):
                    binding["catalog_ref"] = r["cr"]
                if binding:
                    obj_props["binding"] = binding
                _add_node(obj_iri, NodeType.OBJECT.value, obj_label, obj_props)

                # Table 节点(承接 Object)
                tbl_name = table_iri.replace(ADH_NS + "table:", "")
                tlabel = (r.get("tableLabel") or "").strip() or tbl_name
                _add_node(table_iri, NodeType.TABLE.value, tlabel, {"label": tlabel})
                bound_tables[table_iri] = tbl_name
                _add_edge(obj_iri, table_iri, RelationType.BOUND_TO.value, binding)

                # Column 缓存 (Table -> Column), 每张表限量, 最后统一入图
                col_iri = r.get("col", "")
                if col_iri and cols_per_table.get(table_iri, 0) < MAX_COLS_PER_TABLE:
                    cols_per_table[table_iri] = cols_per_table.get(table_iri, 0) + 1
                    pending_columns.append(
                        (table_iri, col_iri,
                         (r.get("colLabel") or "").strip() or col_iri.replace(ADH_NS, ""),
                         r.get("dataType") or "")
                    )
        except Exception as e:
            logger.warning("Lineage object/table/column query failed: %s", e)

        # 2) 派生 Table -> DataSource(catalog):从元数据关联 datasource_id
        #    先于列入图, 确保血缘骨干链路优先保留。
        if bound_tables:
            try:
                ds_map = self._resolve_table_datasources(list(bound_tables.values()))
            except Exception as e:
                logger.warning("Lineage datasource resolution failed: %s", e)
                ds_map = {}
            for table_iri, tbl_name in bound_tables.items():
                info = ds_map.get(tbl_name)
                if not info:
                    continue
                ds_iri = f"{ADH_NS}datasource:{info['datasource_id']}"
                _add_node(ds_iri, NodeType.DATASOURCE.value, info.get("name", ""), {
                    "label": info.get("name", ""),
                    "db_type": info.get("db_type", ""),
                    "catalog_ref": info.get("catalog_ref", ""),
                })
                _add_edge(table_iri, ds_iri, RelationType.BELONGS_TO.value, {
                    "catalog_ref": info.get("catalog_ref", ""),
                })

        # 3) 按剩余预算入图列叶子节点: 保证本视图 nodes <= limit 且边不悬空
        #    (get_graph_data 会 nodes[:limit]/edges[:limit*2] 独立截断)
        budget = max(0, limit - len(nodes))
        col_seen: set[str] = set()
        kept_columns: List[tuple] = []
        for tiri, cir, clabel, dtype in pending_columns:
            if cir in col_seen:
                continue
            if len(kept_columns) >= budget:
                break
            col_seen.add(cir)
            kept_columns.append((tiri, cir, clabel, dtype))
        for table_iri, col_iri, clabel, dtype in kept_columns:
            cprops: Dict[str, Any] = {"label": clabel}
            if dtype:
                cprops["data_type"] = dtype
            _add_node(col_iri, NodeType.COLUMN.value, clabel, cprops)
            _add_edge(table_iri, col_iri, RelationType.HAS_COLUMN.value)

        return nodes, edges

    def _resolve_table_datasources(
        self, table_names: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """按物理表名反查其数据源(含 catalog 引用),供血缘 Table->DataSource 边使用.

        返回 {table_name: {datasource_id, name, db_type, catalog_ref}}。
        best-effort:缺列/异常时降级为仅 datasource_id。
        """
        if not table_names:
            return {}
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        result: Dict[str, Dict[str, Any]] = {}
        try:
            with conn.cursor() as cur:
                fmt = ",".join(["%s"] * len(table_names))
                cur.execute(
                    f"""
                    SELECT ti.table_name, ti.datasource_id, c.catalog_name,
                           ti.size_class, d.name AS ds_name, d.db_type
                    FROM adh_table_info ti
                    LEFT JOIN adh_datasources d ON d.id = ti.datasource_id
                    LEFT JOIN adh_catalogs c ON c.id = ti.catalog_id
                    WHERE ti.table_name IN ({fmt}) AND ti.is_active = 1
                    """,
                    table_names,
                )
                for row in cur.fetchall():
                    tname = row.get("table_name")
                    if not tname:
                        continue
                    result[tname] = {
                        "datasource_id": row.get("datasource_id") or 0,
                        "name": row.get("ds_name") or f"ds:{row.get('datasource_id')}",
                        "db_type": row.get("db_type") or "",
                        "catalog_ref": row.get("catalog_name") or "",
                    }
        except Exception as e:
            logger.warning("Failed to resolve table datasources: %s", e)
        finally:
            conn.close()
        return result

    def _get_stats(self, datasource_id: int = 0) -> GraphStats:
        """获取图谱统计."""
        try:
            graph = self._store.graph_uri(datasource_id)
            triple_count = self._client.count_triples(graph)

            sparql = f"""
                {SPARQL_PREFIXES}
                SELECT ?type (COUNT(?s) as ?cnt) WHERE {{
                    GRAPH <{graph}> {{
                        ?s a ?type .
                    }}
                }}
                GROUP BY ?type
            """
            type_counts = self._client.query(sparql)
            labels = []
            total_nodes = 0
            for r in type_counts:
                t = r.get("type", "").replace(ADH_NS, "")
                labels.append(t)
                total_nodes += int(r.get("cnt", 0))

            return GraphStats(
                node_count=total_nodes,
                relationship_count=triple_count - total_nodes,  # rough estimate
                labels=labels,
                connected=True,
            )
        except Exception as e:
            logger.error("Failed to get stats: %s", e)
            return GraphStats(connected=False)

    def get_node_detail(self, node_iri: str, datasource_id: int = 0) -> Optional[NodeDetailResponse]:
        """获取节点详情."""
        try:
            graph = self._store.graph_uri(datasource_id)

            sparql = f"""
                {SPARQL_PREFIXES}
                SELECT ?p ?o WHERE {{
                    <{node_iri}> ?p ?o .
                }}
                LIMIT 100
            """
            results = self._client.query(sparql)
            if not results:
                return None

            properties = {}
            node_type = "Unknown"
            label = ""
            for r in results:
                pred = r.get("p", "")
                obj = r.get("o", "")
                if pred == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
                    node_type = obj.replace(ADH_NS, "")
                elif pred == "http://www.w3.org/2000/01/rdf-schema#label":
                    label = obj
                else:
                    properties[pred.replace(ADH_NS, "adh:")] = obj

            node = self._to_graph_node(node_iri, node_type, label, properties)

            # Get related nodes
            related_sparql = f"""
                {SPARQL_PREFIXES}
                SELECT ?rel ?other ?otherType ?otherLabel WHERE {{
                    {{ <{node_iri}> ?rel ?other . }}
                    UNION
                    {{ ?other ?rel <{node_iri}> . }}
                    ?other a ?otherType .
                    OPTIONAL {{ ?other rdfs:label ?otherLabel }}
                }}
                LIMIT 50
            """
            related = self._client.query(related_sparql)
            related_nodes = []
            relations = []
            seen = set()

            for r in related:
                other_iri = r.get("other", "")
                if other_iri not in seen:
                    seen.add(other_iri)
                    other_type = r.get("otherType", "").replace(ADH_NS, "")
                    other_label = r.get("otherLabel", "") if r.get("otherLabel") else ""
                    related_nodes.append(self._to_graph_node(other_iri, other_type, other_label, {
                        "label": other_label,
                    }))
                    rel_type = r.get("rel", "").replace(ADH_NS, "")
                    relations.append(self._to_graph_edge(node_iri, other_iri, rel_type))

            return NodeDetailResponse(
                node=node,
                related_nodes=related_nodes,
                relations=relations,
            )

        except Exception as e:
            logger.error("Failed to get node detail: %s", e)
            return None

    def create_node(
        self, node_type: NodeType, properties: Dict[str, Any],
        datasource_id: int = 0
    ) -> Optional[GraphNode]:
        """创建节点."""
        try:
            name = properties.get("name", properties.get("label", "unnamed"))
            comment = properties.get("comment", "")

            if node_type == NodeType.TABLE:
                self._store.create_table_node(name, comment=comment,
                                              business_desc=properties.get("business_desc", ""),
                                              datasource_id=datasource_id)
            elif node_type == NodeType.COLUMN:
                table = properties.get("table_name", "")
                self._store.create_column_node(table, name,
                                               data_type=properties.get("data_type", ""),
                                               comment=comment,
                                               datasource_id=datasource_id)
            elif node_type == NodeType.TERM:
                self._store.create_term_node(name_cn=name,
                                             description=properties.get("description", ""),
                                             datasource_id=datasource_id)
            elif node_type == NodeType.METRIC:
                self._store.create_metric_node(name, description=comment,
                                               datasource_id=datasource_id)
            else:
                logger.warning("Unsupported node type for creation: %s", node_type)
                return None

            return GraphNode(
                id=f"adh:{node_type.value.lower()}:{name}",
                label=node_type.value,
                properties=properties,
            )

        except Exception as e:
            logger.error("Failed to create node: %s", e)
            return None

    def update_node(
        self, node_iri: str, properties: Dict[str, Any],
        datasource_id: int = 0
    ) -> bool:
        """更新节点属性."""
        try:
            graph = self._store.graph_uri(datasource_id)

            # Delete old property values and insert new ones
            for key, value in properties.items():
                pred_iri = f"{ADH_NS}{key}" if not key.startswith("http") else key
                self._client.update(f"""
                    DELETE {{ GRAPH <{graph}> {{ <{node_iri}> <{pred_iri}> ?old }} }}
                    WHERE {{ GRAPH <{graph}> {{ <{node_iri}> <{pred_iri}> ?old }} }}
                """)
                self._client.update(f"""
                    INSERT DATA {{
                        GRAPH <{graph}> {{
                            <{node_iri}> <{pred_iri}> "{value}" .
                        }}
                    }}
                """)
            return True

        except Exception as e:
            logger.error("Failed to update node: %s", e)
            return False

    def delete_node(self, node_iri: str, datasource_id: int = 0) -> bool:
        """删除节点."""
        try:
            graph = self._store.graph_uri(datasource_id)
            self._client.update(f"""
                DELETE {{
                    GRAPH <{graph}> {{ <{node_iri}> ?p ?o }}
                }}
                WHERE {{
                    GRAPH <{graph}> {{ <{node_iri}> ?p ?o }}
                }}
            """)
            # Also delete triples where this node is the object
            self._client.update(f"""
                DELETE {{
                    GRAPH <{graph}> {{ ?s ?p <{node_iri}> }}
                }}
                WHERE {{
                    GRAPH <{graph}> {{ ?s ?p <{node_iri}> }}
                }}
            """)
            return True

        except Exception as e:
            logger.error("Failed to delete node: %s", e)
            return False

    def create_relation(
        self,
        source_iri: str,
        target_iri: str,
        relation_type: str,
        properties: Optional[Dict[str, Any]] = None,
        datasource_id: int = 0,
    ) -> Optional[GraphEdge]:
        """创建关系."""
        try:
            graph = self._store.graph_uri(datasource_id)
            pred_iri = f"{ADH_NS}{relation_type}" if not relation_type.startswith("http") else relation_type

            self._client.update(f"""
                INSERT DATA {{
                    GRAPH <{graph}> {{
                        <{source_iri}> <{pred_iri}> <{target_iri}> .
                    }}
                }}
            """)

            return GraphEdge(
                id=f"{self._iri_to_node_id(source_iri)}-{relation_type}-{self._iri_to_node_id(target_iri)}",
                source=self._iri_to_node_id(source_iri),
                target=self._iri_to_node_id(target_iri),
                type=relation_type,
                properties=properties or {},
            )

        except Exception as e:
            logger.error("Failed to create relation: %s", e)
            return None

    def delete_relation(
        self, source_iri: str, target_iri: str, relation_type: str,
        datasource_id: int = 0
    ) -> bool:
        """删除关系."""
        try:
            graph = self._store.graph_uri(datasource_id)
            pred_iri = f"{ADH_NS}{relation_type}" if not relation_type.startswith("http") else relation_type

            self._client.update(f"""
                DELETE {{
                    GRAPH <{graph}> {{ <{source_iri}> <{pred_iri}> <{target_iri}> }}
                }}
                WHERE {{
                    GRAPH <{graph}> {{ <{source_iri}> <{pred_iri}> <{target_iri}> }}
                }}
            """)
            return True

        except Exception as e:
            logger.error("Failed to delete relation: %s", e)
            return False

    def sync_from_metadata(self, datasource_id: int = 0) -> SyncResponse:
        """从元数据同步到 Oxigraph."""
        try:
            result = self.builder.build_from_metadata(datasource_id)

            if result.get("success"):
                return SyncResponse(
                    success=True,
                    tables=result.get("tables", 0),
                    columns=result.get("columns", 0),
                    terms=result.get("terms", 0),
                    metrics=result.get("metrics", 0),
                    dimensions=result.get("dimensions", 0),
                    sql_templates=result.get("sql_templates", 0),
                    relations=result.get("joins", 0),
                    message=(
                        f"图谱构建完成：{result.get('tables', 0)} 表、"
                        f"{result.get('columns', 0)} 字段、"
                        f"{result.get('terms', 0)} 术语、"
                        f"{result.get('metrics', 0)} 指标、"
                        f"{result.get('sql_templates', 0)} SQL 模板、"
                        f"{result.get('joins', 0)} 关系"
                    )
                )
            else:
                return SyncResponse(
                    success=False,
                    message=result.get("error", "同步失败")
                )

        except Exception as e:
            logger.error("Sync failed: %s", e)
            return SyncResponse(
                success=False,
                message=str(e)
            )

    def search_nodes(
        self,
        query: str,
        node_types: Optional[List[NodeType]] = None,
        limit: int = 20,
        datasource_id: int = 0,
    ) -> List[GraphNode]:
        """搜索节点."""
        try:
            graph = self._store.graph_uri(datasource_id)
            query_escaped = query.replace("\\", "\\\\").replace('"', '\\"')

            type_filter = ""
            if node_types:
                type_iris = " ".join([f"adh:{t.value}" for t in node_types])
                type_filter = f"FILTER(?type IN ({type_iris}))"

            sparql = f"""
                {SPARQL_PREFIXES}
                SELECT ?iri ?type ?label ?comment WHERE {{
                    GRAPH <{graph}> {{
                        ?iri a ?type ;
                            rdfs:label ?label .
                        {type_filter}
                        OPTIONAL {{ ?iri adh:comment ?comment }}
                        FILTER(CONTAINS(LCASE(?label), "{query_escaped.lower()}")
                               || CONTAINS(LCASE(STR(?iri)), "{query_escaped.lower()}"))
                    }}
                }}
                LIMIT {limit}
            """
            results = self._client.query(sparql)
            nodes = []
            for r in results:
                iri = r.get("iri", "")
                ntype = r.get("type", "").replace(ADH_NS, "")
                label = r.get("label", "")
                comment = r.get("comment", "") if r.get("comment") else ""
                nodes.append(self._to_graph_node(iri, ntype, label, {
                    "label": label,
                    "comment": comment,
                }))

            return nodes

        except Exception as e:
            logger.error("Search failed: %s", e)
            return []
